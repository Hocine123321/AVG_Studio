import Foundation
import Observation

// The in-app assistant's brain loop. One user message becomes a bounded
// conversation with a cloud model: the model may call tools (`AgentTools`),
// each result goes back, and the turn ends when the model answers in words.
//
// Rules that matter:
//  - At most `maxSteps` provider calls per user message, so a confused model
//    cannot loop forever against a rate-limited free plan.
//  - A gated tool (one that spends money) suspends the turn on a continuation
//    until the person taps Approve or Decline. The continuation is resumed
//    exactly once, whatever order approve / decline / stop arrive in.
//  - A malformed tool call is answered by *asking the model again*
//    (`maxNudges` times), never by a transport retry: repeating the identical
//    request would just fail the identical way.
//  - After each finished message the tool exchange is dropped from the
//    history (`AgentHistory.compacted`), which keeps every later request
//    small enough for Groq's free tokens-per-minute limit.

@MainActor
@Observable
final class Agent {

    struct Line: Identifiable, Equatable {
        enum Kind: Equatable { case user, assistant, activity, error, announcement }
        let id = UUID()
        let kind: Kind
        let text: String
        /// Errors only: a short headline above `text`, and buttons that fix it.
        var title: String? = nil
        var fixes: [AgentFix] = []
    }

    enum SelfTestResult: Equatable {
        case ok(String)
        case failed(String)
    }

    static let maxSteps = 8
    static let maxNudges = 2
    static let historyWindow = 24
    /// Failures worth trying the other service for: it is the service that is
    /// unavailable, not our request that is wrong.
    static let failoverKinds: Set<ErrorKind> = [.rateLimit, .quota, .billing, .network, .timeout]
    static let nudgeText = "Your last tool call was malformed. Call the tool again with valid arguments, or answer in plain text."

    // MARK: Observable state

    private(set) var lines: [Line] = []
    private(set) var isWorking = false
    private(set) var pendingApproval: AgentApproval?
    var isOpen = false
    /// Sum of the estimates of everything approved (or auto-started) in this
    /// chat. Checked against the per-chat spend limit; zeroed by `reset()`.
    private(set) var approvedSpendUSD = 0.0

    struct Announcement: Equatable {
        let id: UUID
        let ok: Bool
    }
    /// The newest "ready" / "stopped" announcement; the sheet watches it to
    /// fire a haptic.
    private(set) var lastAnnouncement: Announcement?
    /// Videos this chat started or redid that are still running or queued, in
    /// the order they were started. The sheet shows a live progress card for
    /// each, so "what is happening" never needs a question.
    private(set) var watching: [String] = []
    /// "Switch to Gemini" under an error applies to THIS CHAT only; the
    /// service chosen in Settings is never rewritten behind the person's back.
    /// Cleared by New chat.
    private(set) var chatProvider: AgentProviderKind?

    // MARK: Wiring (not UI state)

    @ObservationIgnored weak var host: (any AgentHost)? {
        didSet { restoreIfKept() }
    }
    /// Where the kept chat lives. `nil` means the real file beside the app's
    /// data; tests point it at a temporary one so they never touch it.
    @ObservationIgnored var chatURL: URL?
    @ObservationIgnored private var restored = false
    @ObservationIgnored private var lastUserText: String?
    /// Where the backup-key window is stored; tests point it at a throwaway suite.
    @ObservationIgnored var backupDefaults: UserDefaults = .standard
    /// Makes the provider for this turn. Tests replace it with a scripted one.
    @ObservationIgnored var makeProvider: (AgentProviderKind?) throws -> any AgentProvider = { _ in
        throw AVGError.message("The assistant isn't ready yet.")
    }
    @ObservationIgnored var makeContext: @MainActor (AppSettings) -> AgentContext = { AgentContext.live(settings: $0) }

    @ObservationIgnored private var wire: [AgentMessage] = []
    @ObservationIgnored private var turnTask: Task<Void, Never>?
    @ObservationIgnored private var approvalContinuation: CheckedContinuation<Bool, Never>?
    /// Bumped by `reset()`. A turn that was already running when the chat was
    /// cleared must not write into the new, empty chat.
    @ObservationIgnored private var epoch = 0
    /// Projects this assistant started or redid, so only those are announced.
    @ObservationIgnored private var startedByAgent: Set<String> = []
    /// At most one action per message may start without a tap.
    @ObservationIgnored private var autoStartedThisMessage = false

    init() {
        makeProvider = { [weak self] kind in
            let settings = self?.host?.settings ?? AppSettings()
            // With a backup service ready, don't sit out the full rate-limit
            // backoff (about 30 s) on the main one: one short retry, then
            // fail over. The backup itself keeps the full budget.
            let backupReady = settings.agentFailover && Keychain.has(settings.agentProvider.other.apiKey)
            return try AgentProviderFactory.make(settings: settings, kind: kind,
                                                 attemptCap: (kind == nil && backupReady) ? 2 : nil)
        }
    }

    // MARK: Sending

    /// Starts one turn. Ignored while another is running. The returned task
    /// finishes when the turn does (tests await it).
    @discardableResult
    func send(_ text: String) -> Task<Void, Never> {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isWorking, host != nil else { return Task {} }
        isWorking = true
        lastUserText = trimmed
        lines.append(Line(kind: .user, text: trimmed))
        let task = Task { [weak self] in
            guard let self else { return }
            await self.runTurn(trimmed)
        }
        turnTask = task
        return task
    }

    /// "Try again" under an error: forgets the failed attempt (the person's
    /// message and anything after it) and sends that message once more.
    @discardableResult
    func retryLast() -> Task<Void, Never>? {
        guard !isWorking, let text = lastUserText else { return nil }
        if let i = lines.lastIndex(where: { $0.kind == .user }) { lines.removeSubrange(i...) }
        if let j = wire.lastIndex(where: { $0.role == .user && !$0.synthetic }) { wire.removeSubrange(j...) }
        return send(text)
    }

    func useForThisChat(_ kind: AgentProviderKind) {
        chatProvider = kind
    }

    func approve() { resolveApproval(true) }
    func decline() { resolveApproval(false) }

    /// Stops the running turn. A pending approval counts as declined, so
    /// nothing starts and the turn can finish.
    func stop() {
        turnTask?.cancel()
        resolveApproval(false)
    }

    /// Clears the chat. A turn still running is stopped and goes quiet.
    func reset() {
        epoch += 1
        stop()
        lines = []
        wire = []
        approvedSpendUSD = 0
        chatProvider = nil
        deleteSavedChat()
    }

    private func resolveApproval(_ approved: Bool) {
        guard let c = approvalContinuation else { return }
        approvalContinuation = nil
        pendingApproval = nil
        c.resume(returning: approved)
    }

    // MARK: The turn

    private func runTurn(_ userText: String) async {
        let myEpoch = epoch
        defer {
            isWorking = false
            turnTask = nil
        }
        guard let host else { return }

        func say(_ kind: Line.Kind, _ text: String, title: String? = nil, fixes: [AgentFix] = []) {
            guard epoch == myEpoch else { return }
            lines.append(Line(kind: kind, text: text, title: title, fixes: fixes))
        }
        func remember(_ m: AgentMessage) {
            guard epoch == myEpoch else { return }
            wire.append(m)
        }

        autoStartedThisMessage = false
        remember(.user(userText))
        let settings = host.settings
        let context = makeContext(settings)
        let tools = AgentTools.specs(for: settings)
        let system = AgentPrompt.system(settings: settings)
        var steps = 0
        var nudges = 0
        // Failover: nil = the service chosen in Settings. Once a tool has run
        // in this message, the history holds that service's own tool ids (and
        // for Gemini 3, signatures the other one cannot replay), so a switch
        // is only safe before the first tool call - and only once.
        var activeKind: AgentProviderKind? = chatProvider
        var failedOver = false
        var toolRan = false

        loop: while true {
            if Task.isCancelled { say(.activity, "Stopped."); break loop }
            steps += 1
            if steps > Self.maxSteps {
                say(.error, "I stopped after \(Self.maxSteps) steps without finishing. Try asking for one thing at a time.")
                break loop
            }

            let reply: AgentMessage
            do {
                let provider = try makeProvider(activeKind)
                let history = AgentHistory.trimmed(wire, keepLast: Self.historyWindow)
                reply = try await RenderLog.$record.withValue(logger()) {
                    try await provider.respond(system: system, messages: history, tools: tools)
                }
            } catch {
                if error is CancellationError || Task.isCancelled { say(.activity, "Stopped."); break loop }
                if let avg = error as? AVGError, avg.kind == .malformedOutput, nudges < Self.maxNudges {
                    nudges += 1
                    remember(.nudge(Self.nudgeText))
                    continue loop
                }
                if let avg = error as? AVGError, let kind = avg.kind, Self.failoverKinds.contains(kind),
                   settings.agentFailover, !failedOver, !toolRan {
                    let current = activeKind ?? settings.agentProvider
                    let other = current.other
                    if context.hasKey(other.apiKey) {
                        failedOver = true
                        activeKind = other
                        say(.activity, "\(current.label) isn't available right now. Trying \(other.label).")
                        host.record(nil, "Assistant: \(kind.rawValue) on \(current.label); trying \(other.label).", error: false)
                        continue loop
                    }
                }
                // The main key failed and switching service (if possible) did
                // not help: use the BACKUP key for 30 minutes, then retry.
                if let key = KeyBackup.activateIfPossible(after: error, defaults: backupDefaults, hasBackup: context.hasBackupKey) {
                    say(.activity, "\(key.label): the main key failed. Using the backup key for the next 30 minutes.")
                    host.record(nil, "Assistant: switched \(key.label) to its backup key for 30 minutes.", error: false)
                    continue loop
                }
                // In the person's words, with buttons that fix it. The raw
                // provider message still goes to the Activity log.
                let failing = activeKind ?? settings.agentProvider
                let help = AgentErrorHelp.help(for: error, provider: failing,
                                               otherHasKey: context.hasKey(failing.other.apiKey))
                let raw = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
                say(.error, help.detail, title: help.title, fixes: help.fixes)
                host.record(nil, "Assistant: \(help.title) - \(raw)", error: true)
                break loop
            }

            remember(reply)
            if !reply.text.isEmpty { say(.assistant, reply.text) }
            if reply.toolCalls.isEmpty { break loop }

            for call in reply.toolCalls {
                toolRan = true
                say(.activity, Self.activityText(for: call.name))
                // A fresh context per call: an approved settings change earlier
                // in this very turn (a new writer, say) must be what the next
                // tool call's checks and estimates see.
                let outcome = AgentTools.run(call, host: host, context: makeContext(host.settings))
                // Opening a project switches tab; get the chat out of the way.
                if call.name == "open_project", case .result(let text) = outcome, text.hasPrefix("Opened") {
                    isOpen = false
                }
                let result: String
                switch outcome {
                case .result(let text):
                    result = text
                case .needsApproval(let approval):
                    result = await handle(approval, host: host, settings: settings)
                }
                remember(.tool(callID: call.id, name: call.name, text: result))
            }
        }

        if epoch == myEpoch {
            wire = AgentHistory.compacted(wire, keepingLastTurn: false)
            persistIfKept()
        }
    }

    /// Applies the spend limit and the approval policy to a gated action and
    /// returns the text the model gets back as the tool result.
    private func handle(_ approval: AgentApproval, host: any AgentHost, settings: AppSettings) async -> String {
        let cap = AgentSettingsRules.spendCap(settings.agentChatSpendCapUSD)
        // The spend limit and the free-start shortcut are about VIDEOS. A
        // change to a setting, channel or project skips both and always asks.
        if approval.kind == .spend,
           case .overLimit = AgentPolicy.spendVerdict(approvedSoFar: approvedSpendUSD, adding: approval.estimateUSD, cap: cap) {
            return "This chat's spend limit (\(Self.dollars(cap))) would be passed. Tell the user; they can raise it in Settings > Assistant or start a new chat."
        }
        // Free work may start without a tap if the person chose that - once
        // per message. A second one in the same message asks like any other.
        if approval.kind == .spend, !AgentPolicy.needsTap(approval, settings: settings), !autoStartedThisMessage {
            autoStartedThisMessage = true
            return carryOut(approval, host: host)
        }
        // Stopped before we got here: there is nobody to ask, and a
        // continuation set now would never be resumed.
        guard !Task.isCancelled else { return "The user declined." }
        pendingApproval = approval
        let approved = await withCheckedContinuation { (c: CheckedContinuation<Bool, Never>) in
            approvalContinuation = c
        }
        guard approved else { return "The user declined." }
        return carryOut(approval, host: host)
    }

    private func carryOut(_ approval: AgentApproval, host: any AgentHost) -> String {
        approvedSpendUSD += approval.estimateUSD
        let done = AgentTools.perform(approval.action, host: host)
        if let id = done.projectID {
            startedByAgent.insert(id)
            if !watching.contains(id) { watching.append(id) }
        }
        return done.text
    }

    // MARK: Keep chat

    private var resolvedChatURL: URL { chatURL ?? AgentChatStore.url() }

    /// Saves the conversation at the end of a completed message, if the
    /// person asked for that. Once per message, not per token.
    private func persistIfKept() {
        guard host?.settings.agentKeepChat == true else { return }
        try? AgentChatStore.save(lines, to: resolvedChatURL)
    }

    /// Brings the kept chat back the first time a host is attached. The
    /// model's history is rebuilt as plain user/assistant text, so there is
    /// no tool id or provider payload to replay.
    func restoreIfKept() {
        guard !restored, let host else { return }
        restored = true
        guard host.settings.agentKeepChat, lines.isEmpty else { return }
        let saved = AgentChatStore.load(from: resolvedChatURL)
        lines = saved
        wire = saved.compactMap { l in
            switch l.kind {
            case .user: return AgentMessage.user(l.text)
            case .assistant: return AgentMessage.assistant(text: l.text)
            case .activity, .error, .announcement: return nil
            }
        }
    }

    /// Deletes the saved file (Settings > Clear saved chat, or Keep chat off).
    func deleteSavedChat() {
        AgentChatStore.delete(at: resolvedChatURL)
    }

    // MARK: Announcements

    /// Called by `Store` when any pipeline run ends. Says so in the chat, for
    /// projects this assistant started, if the person wants that. Never calls
    /// the provider, so it costs no tokens.
    func projectFinished(_ id: String) {
        watching.removeAll { $0 == id }
        guard startedByAgent.contains(id), let host, host.settings.agentAnnounceResults,
              let project = host.projects.first(where: { $0.id == id }) else { return }
        let title = project.title
        if let failed = PhaseID.allCases.first(where: { project.phase($0).status == .failed }) {
            startedByAgent.remove(id)
            let why = project.phase(failed).error ?? "no details"
            lines.append(Line(kind: .announcement, text: "“\(title)” stopped at \(failed.title): \(why)"))
            lastAnnouncement = Announcement(id: UUID(), ok: false)
        } else if PhaseID.allCases.allSatisfy({ project.phase($0).status == .done }) {
            startedByAgent.remove(id)
            lines.append(Line(kind: .announcement, text: "“\(title)” is ready."))
            lastAnnouncement = Announcement(id: UUID(), ok: true)
        }
        // Neither (cancelled, or still going): say nothing.
    }

    // MARK: Self-test

    /// One tiny request that proves the key works AND the model can call
    /// tools. Touches neither the transcript nor the history nor the host.
    func selfTest() async -> SelfTestResult {
        guard let host else { return .failed("The assistant isn't ready yet.") }
        let ping = AgentToolSpec(name: "ping", summary: "Call this to reply.", params: [])
        do {
            let provider = try makeProvider(nil)
            let reply = try await RenderLog.$record.withValue(logger()) {
                try await provider.respond(system: "You are a connection test. Always answer by calling the ping tool.",
                                           messages: [.user("Call the ping tool.")], tools: [ping])
            }
            guard reply.toolCalls.contains(where: { $0.name == "ping" }) else {
                return .failed("This model answered without using tools. Pick another model.")
            }
            let s = host.settings
            let model = s.agentProvider == .groq ? s.agentGroqModel : s.agentGeminiModel
            return .ok("\(s.agentProvider.label) · \(model)")
        } catch {
            if let avg = error as? AVGError, avg.kind == .malformedOutput {
                return .failed("This model can't make valid tool calls. Pick another model.")
            }
            let s = host.settings
            let help = AgentErrorHelp.help(for: error, provider: s.agentProvider,
                                           otherHasKey: Keychain.has(s.agentProvider.other.apiKey))
            return .failed(help.sentence)
        }
    }

    // MARK: Helpers

    /// Where `HTTP.retrying` writes its "retrying in 4s" lines: the Activity log.
    private func logger() -> (@Sendable (String) async -> Void) {
        { [weak self] message in
            await MainActor.run { self?.host?.record(nil, message, error: false) }
        }
    }

    static func dollars(_ v: Double) -> String { String(format: "$%.2f", v) }

    static func activityText(for tool: String) -> String {
        switch tool {
        case "list_channels": return "Checking channels…"
        case "list_projects": return "Checking projects…"
        case "get_status": return "Checking progress…"
        case "create_video": return "Preparing a video…"
        case "redo_from": return "Preparing a redo…"
        case "cancel": return "Stopping a project…"
        case "open_project": return "Opening a project…"
        case "estimate_cost": return "Working out the cost…"
        case "manage_project": return "Preparing a project change…"
        case "change_setting": return "Preparing a settings change…"
        case "edit_channel": return "Preparing a channel change…"
        case "get_app_status": return "Checking the app…"
        case "get_activity": return "Reading the activity log…"
        case "get_settings": return "Reading settings…"
        default: return "Working…"
        }
    }
}

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
    }

    enum SelfTestResult: Equatable {
        case ok(String)
        case failed(String)
    }

    static let maxSteps = 8
    static let maxNudges = 2
    static let historyWindow = 24
    static let nudgeText = "Your last tool call was malformed. Call the tool again with valid arguments, or answer in plain text."

    // MARK: Observable state

    private(set) var lines: [Line] = []
    private(set) var isWorking = false
    private(set) var pendingApproval: AgentApproval?
    var isOpen = false

    // MARK: Wiring (not UI state)

    @ObservationIgnored weak var host: (any AgentHost)?
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

    init() {
        makeProvider = { [weak self] kind in
            try AgentProviderFactory.make(settings: self?.host?.settings ?? AppSettings(), kind: kind)
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
        lines.append(Line(kind: .user, text: trimmed))
        let task = Task { [weak self] in await self?.runTurn(trimmed) }
        turnTask = task
        return task
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

        func say(_ kind: Line.Kind, _ text: String) {
            guard epoch == myEpoch else { return }
            lines.append(Line(kind: kind, text: text))
        }
        func remember(_ m: AgentMessage) {
            guard epoch == myEpoch else { return }
            wire.append(m)
        }

        remember(.user(userText))
        let settings = host.settings
        let context = makeContext(settings)
        let tools = AgentTools.specs(for: settings)
        let system = Self.systemPrompt(for: settings)
        var steps = 0
        var nudges = 0

        loop: while true {
            if Task.isCancelled { say(.activity, "Stopped."); break loop }
            steps += 1
            if steps > Self.maxSteps {
                say(.error, "I stopped after \(Self.maxSteps) steps without finishing. Try asking for one thing at a time.")
                break loop
            }

            let reply: AgentMessage
            do {
                let provider = try makeProvider(nil)
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
                let message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
                say(.error, message)
                host.record(nil, "Assistant: \(message)", error: true)
                break loop
            }

            remember(reply)
            if !reply.text.isEmpty { say(.assistant, reply.text) }
            if reply.toolCalls.isEmpty { break loop }

            for call in reply.toolCalls {
                say(.activity, Self.activityText(for: call.name))
                let outcome = AgentTools.run(call, host: host, context: context)
                let result: String
                switch outcome {
                case .result(let text):
                    result = text
                case .needsApproval(let approval):
                    result = await ask(approval, host: host)
                }
                remember(.tool(callID: call.id, name: call.name, text: result))
            }
        }

        if epoch == myEpoch {
            wire = AgentHistory.compacted(wire, keepingLastTurn: false)
        }
    }

    /// Shows the approval and waits for the person. Returns the text the model
    /// gets back as the tool result.
    private func ask(_ approval: AgentApproval, host: any AgentHost) async -> String {
        // Stopped before we got here: there is nobody to ask, and a
        // continuation set now would never be resumed.
        guard !Task.isCancelled else { return "The user declined." }
        pendingApproval = approval
        let approved = await withCheckedContinuation { (c: CheckedContinuation<Bool, Never>) in
            approvalContinuation = c
        }
        guard approved else { return "The user declined." }
        return AgentTools.perform(approval.action, host: host).text
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
            return .failed((error as? LocalizedError)?.errorDescription ?? error.localizedDescription)
        }
    }

    // MARK: Helpers

    /// Where `HTTP.retrying` writes its "retrying in 4s" lines: the Activity log.
    private func logger() -> (@Sendable (String) async -> Void) {
        { [weak self] message in
            await MainActor.run { self?.host?.record(nil, message, error: false) }
        }
    }

    /// Replaced by `AgentPrompt.system(settings:)` once the policy task lands.
    static func systemPrompt(for settings: AppSettings) -> String {
        "You control the app AVG Studio, which makes short videos. Use the tools to help. Never claim a video started unless a tool result says so."
    }

    static func activityText(for tool: String) -> String {
        switch tool {
        case "list_channels": return "Checking channels…"
        case "list_projects": return "Checking projects…"
        case "get_status": return "Checking progress…"
        case "create_video": return "Preparing a video…"
        case "redo_from": return "Preparing a redo…"
        case "cancel": return "Stopping a project…"
        case "open_project": return "Opening a project…"
        default: return "Working…"
        }
    }
}

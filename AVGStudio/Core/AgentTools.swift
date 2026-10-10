import Foundation

// The assistant's hands. Every tool is a thin wrapper over a member `Store`
// already exposes, reached through `AgentHost` so the tools (and the loop in
// `Agent.swift`) are unit-tested against a fake host with no pipeline, no
// network and no files. Tool *results* are short plain text, never raw JSON:
// the result is sent back to the model on every later request, and Groq's
// free plan allows about 8K tokens a minute.
//
// Two of the tools spend money (`create_video`, `redo_from`). They never
// start anything themselves: they return an `AgentApproval` and the loop
// decides - from `AgentPolicy`, never from model text - whether it needs a tap.

// MARK: - Host

/// What the agent needs from the app. `Store` already has every one of these
/// members with exactly these signatures, so its conformance is empty.
@MainActor
protocol AgentHost: AnyObject {
    var projects: [Project] { get }
    var channels: [Channel] { get }
    var activeChannel: Channel { get }
    var settings: AppSettings { get }
    var jobs: [String: JobState] { get }
    var recoveryPlan: RecoveryPlan? { get }
    func isRunning(_ id: String) -> Bool
    /// Running OR waiting for a slot. With `maxParallelJobs` reached a start is
    /// queued, never refused, and `Store.start` silently ignores a project
    /// that is already busy - so every "can I redo / cancel this?" check asks
    /// this, not `isRunning`.
    func isBusy(_ id: String) -> Bool
    /// 1-based place in the queue, or nil when not queued.
    func queuePosition(_ id: String) -> Int?
    /// Recycled images in the pool; they make a video's image cost lower.
    var reusableImageCount: Int { get }
    func createProject(topic: String, title: String, channel: Channel, aspect: AspectRatio) -> Project
    func start(_ id: String, from phase: PhaseID)
    func cancel(_ id: String)
    func selectForStudio(_ id: String)
    func record(_ id: String?, _ message: String, error: Bool)
}

/// What the tools may know about the device. Injected so tests can say "no
/// Apple Intelligence" or "no Groq key" without touching the Keychain.
struct AgentContext {
    var settings: AppSettings
    var appleAvailable: Bool
    var hasKey: (APIKey) -> Bool
    /// Is a backup key saved for this provider? (Real Keychain unless a test says otherwise.)
    var hasBackupKey: (APIKey) -> Bool = { Keychain.has($0, slot: .backup) }

    /// The real device: Keychain and Apple Intelligence as they are now.
    @MainActor static func live(settings: AppSettings) -> AgentContext {
        AgentContext(settings: settings, appleAvailable: AppleText.isAvailable, hasKey: { Keychain.has($0) })
    }
}

// MARK: - Approvals

enum AgentApprovedAction: Equatable {
    case createVideo(topic: String, title: String, aspect: AspectRatio, channelID: UUID)
    case redo(projectID: String, phase: PhaseID)
}

/// A request to spend (or possibly spend) money, shown as Approve / Decline.
struct AgentApproval: Identifiable, Equatable {
    let id: UUID
    let title: String
    let detail: String
    /// The flat guess as text ("Free", "about $0.03") and as a number the spend
    /// limit counts.
    let estimate: String
    let estimateUSD: Double
    let action: AgentApprovedAction

    init(id: UUID = UUID(), title: String, detail: String, estimate: String, estimateUSD: Double,
         action: AgentApprovedAction) {
        self.id = id
        self.title = title
        self.detail = detail
        self.estimate = estimate
        self.estimateUSD = estimateUSD
        self.action = action
    }
}

enum AgentToolOutcome: Equatable {
    case result(String)
    case needsApproval(AgentApproval)
}

/// What happened when an approved action ran. `projectID` lets the agent
/// remember which projects it started, so it can announce them when done.
struct AgentPerformed: Equatable {
    let text: String
    let projectID: String?
}

struct AgentToolError: Error, Equatable {
    let text: String
}

// MARK: - Tools

enum AgentTools {
    static let createName = "create_video"
    static let redoName = "redo_from"
    static let cancelName = "cancel"

    /// Starting or restarting a pipeline spends money, so these two go
    /// through an approval. Everything else is read-only or free.
    static func isGated(_ name: String) -> Bool { name == createName || name == redoName }

    private static let projectParam = AgentToolParam(name: "project", kind: .string,
                                                     summary: "Project ref (6 characters), id, or part of its title.")

    /// The tools offered to the model. A capability the user turned off in
    /// Settings is omitted entirely, which also saves tokens.
    static func specs(for settings: AppSettings) -> [AgentToolSpec] {
        var out: [AgentToolSpec] = [
            AgentToolSpec(name: "list_channels", summary: "List the channels (name, niche, shape, length).", params: []),
            AgentToolSpec(name: "list_projects", summary: "List recent projects with their step status.", params: [
                AgentToolParam(name: "limit", kind: .integer, summary: "How many, 1-10. Default 5.", required: false),
            ]),
            AgentToolSpec(name: "get_status", summary: "Show one project's progress, errors and cost.", params: [projectParam]),
        ]
        if settings.agentAllowCreate {
            out.append(AgentToolSpec(name: createName, summary: "Make a new video. The user must approve it.", params: [
                AgentToolParam(name: "topic", kind: .string, summary: "What the video is about."),
                AgentToolParam(name: "title", kind: .string, summary: "Optional short title.", required: false),
                AgentToolParam(name: "aspect", kind: .string, summary: "Shape. Default: the channel's.", required: false,
                               allowed: AspectRatio.allCases.map { $0.rawValue }),
                AgentToolParam(name: "channel", kind: .string, summary: "Channel name. Default: the active one.", required: false),
            ]))
        }
        if settings.agentAllowRedo {
            out.append(AgentToolSpec(name: redoName, summary: "Re-run a project from one step onward. The user must approve it.", params: [
                projectParam,
                AgentToolParam(name: "phase", kind: .string, summary: "Step to restart from.",
                               allowed: PhaseID.allCases.map { $0.rawValue }),
            ]))
        }
        if settings.agentAllowCancel {
            out.append(AgentToolSpec(name: cancelName, summary: "Stop a project that is running.", params: [projectParam]))
        }
        out.append(AgentToolSpec(name: "open_project", summary: "Open a project in the Studio screen.", params: [projectParam]))
        return out
    }

    /// The last six characters of the project id - short enough to say or
    /// type, and the random part of the id, so refs rarely collide.
    static func ref(_ p: Project) -> String { String(p.id.suffix(6)) }

    /// Full id, then a ref, then a title fragment. Several matches are an
    /// error that lists them; the agent never guesses which one was meant.
    static func resolveProject(_ query: String, in projects: [Project]) -> Result<Project, AgentToolError> {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else {
            return .failure(AgentToolError(text: "Which project? Call list_projects to see refs."))
        }
        let lower = q.lowercased()
        if let exact = projects.first(where: { $0.id.lowercased() == lower }) { return .success(exact) }

        let byRef = projects.filter { ref($0).lowercased() == lower }
        if byRef.count == 1 { return .success(byRef[0]) }
        if byRef.count > 1 { return .failure(ambiguous(q, byRef)) }

        let byTitle = projects.filter {
            $0.title.range(of: q, options: [.caseInsensitive, .diacriticInsensitive]) != nil
        }
        if byTitle.count == 1 { return .success(byTitle[0]) }
        if byTitle.count > 1 { return .failure(ambiguous(q, byTitle)) }
        return .failure(AgentToolError(text: "No project matches “\(q)”. Call list_projects to see refs."))
    }

    private static func ambiguous(_ q: String, _ matches: [Project]) -> AgentToolError {
        let list = matches.prefix(6).map { "\(ref($0)) – \(short($0.title))" }.joined(separator: "; ")
        return AgentToolError(text: "“\(q)” matches \(matches.count) projects: \(list). Ask the user which one, or use its ref.")
    }

    // MARK: Dispatch

    @MainActor
    static func run(_ call: AgentToolCall, host: any AgentHost, context: AgentContext) -> AgentToolOutcome {
        let args = AgentArgs(json: call.argumentsJSON)
        let s = context.settings
        switch call.name {
        case "list_channels":
            return .result(listChannels(host))
        case "list_projects":
            return .result(listProjects(host, limit: args.int("limit")))
        case "get_status":
            return .result(withProject(args, host) { getStatus($0, host) })
        case "open_project":
            return .result(withProject(args, host) { p in
                host.selectForStudio(p.id)
                return "Opened “\(short(p.title))” in Studio."
            })
        case cancelName:
            guard s.agentAllowCancel else { return .result(turnedOff("Cancelling")) }
            return .result(withProject(args, host) { p in
                guard host.isBusy(p.id) else { return "“\(short(p.title))” isn't running." }
                let waiting = host.queuePosition(p.id) != nil
                host.cancel(p.id)
                return waiting ? "Removed “\(short(p.title))” from the queue." : "Cancelled “\(short(p.title))”."
            })
        case createName:
            guard s.agentAllowCreate else { return .result(turnedOff("Starting videos")) }
            return createVideo(args, host, context)
        case redoName:
            guard s.agentAllowRedo else { return .result(turnedOff("Redoing steps")) }
            return redo(args, host, context)
        default:
            let names = specs(for: s).map { $0.name }.joined(separator: ", ")
            return .result("Unknown tool “\(call.name)”. Available: \(names).")
        }
    }

    /// Carries out an approved (or auto-approved) action.
    @MainActor
    static func perform(_ action: AgentApprovedAction, host: any AgentHost) -> AgentPerformed {
        switch action {
        case .createVideo(let topic, let title, let aspect, let channelID):
            let channel = host.channels.first { $0.id == channelID } ?? host.activeChannel
            let p = host.createProject(topic: topic, title: title, channel: channel, aspect: aspect)
            host.start(p.id, from: .writing)
            let queued = host.queuePosition(p.id).map { " It is queued (place \($0)) until a slot frees." } ?? " It runs in the background."
            return AgentPerformed(
                text: "Started “\(short(p.title))” (ref \(ref(p))).\(queued) Check get_status for progress.",
                projectID: p.id)
        case .redo(let projectID, let phase):
            guard let p = host.projects.first(where: { $0.id == projectID }) else {
                return AgentPerformed(text: "That project no longer exists.", projectID: nil)
            }
            guard !host.isBusy(projectID) else {
                return AgentPerformed(text: "“\(short(p.title))” is already running or queued.", projectID: nil)
            }
            host.start(projectID, from: phase)
            let queued = host.queuePosition(projectID).map { " It is queued (place \($0))." } ?? ""
            return AgentPerformed(text: "Restarted “\(short(p.title))” from \(phase.title).\(queued)", projectID: projectID)
        }
    }

    // MARK: Read-only tools

    @MainActor
    private static func listChannels(_ host: any AgentHost) -> String {
        guard !host.channels.isEmpty else { return "No channels yet." }
        let activeID = host.activeChannel.id
        return host.channels.map { c in
            let niche = c.niche.isEmpty ? "" : " · \(short(c.niche, 40))"
            let active = c.id == activeID ? " · active" : ""
            return "\(c.name)\(niche) · \(c.aspectRatio.rawValue) · \(c.targetSeconds)s\(active)"
        }.joined(separator: "\n")
    }

    @MainActor
    private static func listProjects(_ host: any AgentHost, limit: Int?) -> String {
        let live = host.projects.filter { !$0.archived }.sorted { $0.created > $1.created }
        guard !live.isEmpty else { return "No projects yet." }
        let n = min(max(limit ?? 5, 1), 10)
        let lines = live.prefix(n).map { p -> String in
            let video = p.output != nil ? " · video ready" : ""
            return "\(ref(p)) · \(short(p.title)) · \(p.aspectRatio.rawValue) · \(phaseSummary(p))\(video)"
        }
        return "\(lines.count) of \(live.count) projects, newest first:\n" + lines.joined(separator: "\n")
    }

    @MainActor
    private static func getStatus(_ p: Project, _ host: any AgentHost) -> String {
        var lines = ["“\(short(p.title))” (ref \(ref(p))), \(p.aspectRatio.rawValue)"]
        lines.append("Steps: " + phaseSummary(p))
        if let place = host.queuePosition(p.id) {
            lines.append("Queued: place \(place), waiting for a free slot.")
        }
        if host.isRunning(p.id) {
            if let job = host.jobs[p.id] {
                let pct = Int((min(max(job.progress, 0), 1) * 100).rounded())
                lines.append("Running now: \(job.phase.title) \(pct)% – \(short(job.message, 80))")
            } else {
                lines.append("Running now.")
            }
        }
        if let plan = host.recoveryPlan, plan.projectID == p.id {
            lines.append("Waiting for the user's decision (\(short(plan.title, 80))). The choices are shown in the chat; tell them to pick one, and do not retry.")
        }
        if let failed = PhaseID.allCases.first(where: { p.phase($0).status == .failed }) {
            let err = p.phase(failed).error ?? "no details"
            lines.append("Stopped at \(failed.title): \(short(err, 160))")
        }
        lines.append(p.output != nil ? "Video: ready." : "Video: not made yet.")
        lines.append("Cost so far: \(CostEstimate(usd: p.estimatedCostUSD).summary)")
        return lines.joined(separator: "\n")
    }

    // MARK: Gated tools

    @MainActor
    private static func createVideo(_ args: AgentArgs, _ host: any AgentHost, _ context: AgentContext) -> AgentToolOutcome {
        guard let topic = args.string("topic") else {
            return .result("Error: topic is required. Ask the user what the video is about.")
        }
        let channel: Channel
        switch resolveChannel(args.string("channel"), in: host) {
        case .success(let c): channel = c
        case .failure(let e): return .result(e.text)
        }
        let aspect: AspectRatio
        if let raw = args.string("aspect") {
            guard let a = AspectRatio(rawValue: raw) else {
                return .result("Error: aspect must be one of \(AspectRatio.allCases.map { $0.rawValue }.joined(separator: ", ")).")
            }
            aspect = a
        } else {
            aspect = channel.aspectRatio
        }
        if let refusal = preflight(from: .writing, context: context) { return .result(refusal) }

        let estimate = CostEstimate.forNewVideo(settings: context.settings, channel: channel,
                                                hasAssemblyKey: context.hasKey(.assemblyai),
                                                poolSize: host.reusableImageCount)
        let title = args.string("title") ?? ""
        return .needsApproval(AgentApproval(
            title: "Start this video?",
            detail: "“\(short(topic, 80))” · \(aspect.rawValue) · \(channel.name) · about \(channel.targetSeconds)s",
            estimate: estimate.summary, estimateUSD: estimate.usd,
            action: .createVideo(topic: topic, title: title, aspect: aspect, channelID: channel.id)))
    }

    @MainActor
    private static func redo(_ args: AgentArgs, _ host: any AgentHost, _ context: AgentContext) -> AgentToolOutcome {
        let project: Project
        switch resolveProject(args.string("project") ?? "", in: host.projects) {
        case .success(let p): project = p
        case .failure(let e): return .result(e.text)
        }
        let valid = PhaseID.allCases.map { $0.rawValue }
        guard let raw = args.string("phase"), let phase = PhaseID(rawValue: raw.lowercased()) else {
            return .result("Error: phase must be one of \(valid.joined(separator: ", ")).")
        }
        if host.isBusy(project.id) {
            return .result("“\(short(project.title))” is already running or queued. Wait for it, or cancel it first.")
        }
        if let plan = host.recoveryPlan, plan.projectID == project.id {
            return .result("“\(short(project.title))” is waiting for the user's decision. The choices are shown in the chat; tell them to pick one.")
        }
        if let refusal = preflight(from: phase, context: context) { return .result(refusal) }

        let channel = host.channels.first { $0.id == project.channelID } ?? host.activeChannel
        let estimate = CostEstimate.forRedo(from: phase, settings: context.settings, channel: channel,
                                            hasAssemblyKey: context.hasKey(.assemblyai),
                                            poolSize: host.reusableImageCount)
        return .needsApproval(AgentApproval(
            title: "Redo from \(phase.title)?",
            detail: "“\(short(project.title))” will re-run from \(phase.title) onward.",
            estimate: estimate.summary, estimateUSD: estimate.usd,
            action: .redo(projectID: project.id, phase: phase)))
    }

    // MARK: Preflight

    /// Refuses, before any project exists, a start that would fail at its
    /// first step: the Apple writer on a phone without Apple Intelligence, or
    /// a service whose key is missing. Only the steps that would actually run
    /// are checked (a redo from Voice does not need the writer).
    private static func preflight(from phase: PhaseID, context: AgentContext) -> String? {
        let s = context.settings
        if phase.order <= PhaseID.writing.order {
            if s.text == .apple && !context.appleAvailable {
                return "The video writer is set to Apple Intelligence, which this phone can't run. Tell the user to choose Groq or Gemini under Settings > Writing first. Nothing was started."
            }
            if let k = keyNeeded(text: s.text), !context.hasKey(k) { return missing(k) }
        }
        if phase.order <= PhaseID.visuals.order, let k = keyNeeded(image: s.image), !context.hasKey(k) { return missing(k) }
        if phase.order <= PhaseID.voice.order, let k = keyNeeded(voice: s.voice), !context.hasKey(k) { return missing(k) }
        return nil
    }

    private static func missing(_ k: APIKey) -> String {
        "The \(k.label) key is missing. Tell the user to add it under Settings > Keys. Nothing was started."
    }

    private static func keyNeeded(text: TextProviderKind) -> APIKey? {
        switch text {
        case .gemini: return .gemini
        case .groq: return .groq
        case .apple, .pollinations: return nil
        }
    }

    private static func keyNeeded(image: ImageProviderKind) -> APIKey? {
        switch image {
        case .gemini: return .gemini
        case .openai: return .openai
        case .pollinations, .stablehorde: return nil
        }
    }

    private static func keyNeeded(voice: VoiceProviderKind) -> APIKey? {
        switch voice {
        case .elevenlabs: return .elevenlabs
        case .gemini: return .gemini
        case .apple: return nil
        }
    }

    // MARK: Helpers

    @MainActor
    private static func withProject(_ args: AgentArgs, _ host: any AgentHost, _ body: (Project) -> String) -> String {
        switch resolveProject(args.string("project") ?? "", in: host.projects) {
        case .success(let p): return body(p)
        case .failure(let e): return e.text
        }
    }

    @MainActor
    private static func resolveChannel(_ name: String?, in host: any AgentHost) -> Result<Channel, AgentToolError> {
        guard let name else { return .success(host.activeChannel) }
        let lower = name.lowercased()
        if let exact = host.channels.first(where: { $0.name.lowercased() == lower }) { return .success(exact) }
        let partial = host.channels.filter { $0.name.range(of: name, options: [.caseInsensitive, .diacriticInsensitive]) != nil }
        if partial.count == 1 { return .success(partial[0]) }
        let names = host.channels.map { $0.name }.joined(separator: ", ")
        return .failure(AgentToolError(text: "No single channel matches “\(name)”. Channels: \(names)."))
    }

    private static func turnedOff(_ what: String) -> String {
        "\(what) is turned off in Settings > Assistant. Tell the user; do not try again."
    }

    private static func mark(_ s: PhaseStatus) -> String {
        switch s {
        case .done: return "✓"
        case .running: return "▶"
        case .failed: return "✗"
        case .pending: return "·"
        }
    }

    private static func phaseSummary(_ p: Project) -> String {
        PhaseID.allCases.map { "\($0.title)\(mark(p.phase($0).status))" }.joined(separator: " ")
    }

    private static func short(_ s: String, _ limit: Int = 48) -> String {
        let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
        return t.count <= limit ? t : String(t.prefix(limit - 1)) + "…"
    }
}

import XCTest
@testable import AVGStudio

/// A host that records what the agent asked it to do, with no pipeline, no
/// disk and no network. Shared by the tool and loop tests.
@MainActor
final class FakeAgentHost: AgentHost {
    var projects: [Project] = []
    var channels: [Channel] = [Channel(name: "Facts"), Channel(name: "History")]
    var settings = AppSettings()
    var jobs: [String: JobState] = [:]
    var recoveryPlan: RecoveryPlan?
    var running: Set<String> = []
    var activeChannelID: UUID?

    var created: [(topic: String, title: String, channelID: UUID, aspect: AspectRatio)] = []
    var started: [(id: String, phase: PhaseID)] = []
    var cancelled: [String] = []
    var selected: [String] = []
    var recorded: [String] = []

    var activeChannel: Channel {
        channels.first { $0.id == activeChannelID } ?? channels[0]
    }

    func isRunning(_ id: String) -> Bool { running.contains(id) }

    func createProject(topic: String, title: String, channel: Channel, aspect: AspectRatio) -> Project {
        created.append((topic, title, channel.id, aspect))
        let p = Project(id: "2026-10-04T10-00-00Z-NEW\(String(format: "%03d", created.count))",
                        title: title.isEmpty ? String(topic.prefix(48)) : title, topic: topic,
                        channelID: channel.id, aspectRatio: aspect)
        projects.insert(p, at: 0)
        return p
    }

    func start(_ id: String, from phase: PhaseID) { started.append((id, phase)) }
    func cancel(_ id: String) { cancelled.append(id); running.remove(id) }
    func selectForStudio(_ id: String) { selected.append(id) }
    func record(_ id: String?, _ message: String, error: Bool) { recorded.append(message) }

    /// A project whose id ends in `suffix` (its ref).
    @discardableResult
    func add(_ suffix: String, title: String, created: Date = Date()) -> Project {
        var p = Project(id: "2026-10-04T09-00-00Z-\(suffix)", title: title, topic: title)
        p.created = created
        projects.append(p)
        return p
    }
}

extension AgentContext {
    /// A device with every key and Apple Intelligence, unless a test says otherwise.
    static func test(_ settings: AppSettings, apple: Bool = true, keys: @escaping (APIKey) -> Bool = { _ in true }) -> AgentContext {
        AgentContext(settings: settings, appleAvailable: apple, hasKey: keys)
    }
}

func toolCall(_ name: String, _ json: String = "{}", id: String = "call_1") -> AgentToolCall {
    AgentToolCall(id: id, name: name, argumentsJSON: json)
}

/// A provider that plays back a script and records every request it was sent.
final class ScriptedProvider: AgentProvider, @unchecked Sendable {
    struct Call { let system: String; let messages: [AgentMessage]; let tools: [AgentToolSpec] }

    private let lock = NSLock()
    private var script: (Int, [AgentMessage]) -> Result<AgentMessage, Error>
    private var recorded: [Call] = []

    /// `script` gets the zero-based call number and the messages it was sent.
    init(_ script: @escaping (Int, [AgentMessage]) -> Result<AgentMessage, Error>) {
        self.script = script
    }

    /// Plays these replies in order; asking for more fails the test run's turn.
    convenience init(queue: [Result<AgentMessage, Error>]) {
        self.init { i, _ in i < queue.count ? queue[i] : .failure(AVGError.message("script exhausted")) }
    }

    var calls: [Call] { lock.lock(); defer { lock.unlock() }; return recorded }

    private func next(_ call: Call) -> Result<AgentMessage, Error> {
        lock.lock(); defer { lock.unlock() }
        recorded.append(call)
        return script(recorded.count - 1, call.messages)
    }

    func respond(system: String, messages: [AgentMessage], tools: [AgentToolSpec]) async throws -> AgentMessage {
        try next(Call(system: system, messages: messages, tools: tools)).get()
    }
}

extension AgentMessage {
    static func calling(_ name: String, _ json: String = "{}", id: String = "call_1") -> AgentMessage {
        .assistant(text: "", calls: [toolCall(name, json, id: id)])
    }
}

/// `Agent.host` is weak (the real host, `Store`, owns the agent), so a test
/// that passes `FakeAgentHost()` inline would lose it the moment `makeAgent`
/// returns. Keeping them here for the life of the test run is harmless.
@MainActor private var retainedHosts: [FakeAgentHost] = []

@MainActor
func makeAgent(_ provider: ScriptedProvider, host: FakeAgentHost) -> Agent {
    retainedHosts.append(host)
    let a = Agent()
    a.host = host
    a.makeProvider = { _ in provider }
    a.makeContext = { AgentContext.test($0) }
    return a
}

@MainActor
func waitFor(_ what: String = "condition", _ cond: () -> Bool, file: StaticString = #filePath, line: UInt = #line) async {
    for _ in 0..<600 {
        if cond() { return }
        try? await Task.sleep(nanoseconds: 5_000_000)
    }
    XCTFail("timed out waiting for \(what)", file: file, line: line)
}

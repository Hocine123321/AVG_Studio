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

import XCTest
@testable import AVGStudio

/// The kept chat: only what was said, never anything else, never a key.
@MainActor
final class AgentChatStoreTests: XCTestCase {

    private func line(_ kind: Agent.Line.Kind, _ text: String) -> Agent.Line { Agent.Line(kind: kind, text: text) }

    private func tmp() -> URL { tempChatURL() }

    func testRoundTripKeepsOnlyUserAndAssistantText() throws {
        let url = tmp()
        defer { AgentChatStore.delete(at: url) }
        let lines = [line(.user, "hi"), line(.activity, "Checking projects…"), line(.assistant, "You have 2."),
                     line(.error, "boom"), line(.announcement, "“X” is ready."), line(.user, "thanks"), line(.assistant, "Anytime.")]
        try AgentChatStore.save(lines, to: url)
        let back = AgentChatStore.load(from: url)
        XCTAssertEqual(back.map { $0.kind }, [.user, .assistant, .user, .assistant])
        XCTAssertEqual(back.map { $0.text }, ["hi", "You have 2.", "thanks", "Anytime."])
    }

    func testSaveCapsAtFortyLinesKeepingTheNewest() throws {
        let url = tmp()
        defer { AgentChatStore.delete(at: url) }
        var lines: [Agent.Line] = []
        for i in 0..<25 { lines += [line(.user, "q\(i)"), line(.assistant, "a\(i)")] }
        try AgentChatStore.save(lines, to: url)
        let back = AgentChatStore.load(from: url)
        XCTAssertEqual(back.count, 40)
        XCTAssertEqual(back.last?.text, "a24")
        XCTAssertEqual(back.first?.text, "q5")
    }

    func testSavedChatAlwaysOpensOnAUserLine() throws {
        let url = tmp()
        defer { AgentChatStore.delete(at: url) }
        var lines = [line(.user, "q0")]
        for i in 0..<20 { lines += [line(.assistant, "a\(i)"), line(.user, "q\(i + 1)")] }
        lines.append(line(.assistant, "last"))
        // 42 chat lines; the newest 40 begin on an assistant line, which is dropped.
        try AgentChatStore.save(lines, to: url)
        XCTAssertEqual(AgentChatStore.load(from: url).first?.kind, .user)
    }

    func testMissingOrCorruptFileLoadsEmptyNotACrash() throws {
        let missing = tmp()
        XCTAssertTrue(AgentChatStore.load(from: missing).isEmpty)
        let bad = tmp()
        defer { AgentChatStore.delete(at: bad) }
        try Data("{{ not json".utf8).write(to: bad)
        XCTAssertTrue(AgentChatStore.load(from: bad).isEmpty)
    }

    func testSavingNothingWorthKeepingRemovesTheFile() throws {
        let url = tmp()
        try AgentChatStore.save([line(.user, "hi")], to: url)
        XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
        try AgentChatStore.save([line(.activity, "x"), line(.error, "y")], to: url)
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
    }

    func testDeleteRemovesFile() throws {
        let url = tmp()
        try AgentChatStore.save([line(.user, "hi")], to: url)
        AgentChatStore.delete(at: url)
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
        AgentChatStore.delete(at: url)   // deleting a missing file is fine
    }

    func testRedactMasksKeyShapedStrings() {
        let groq = "gsk_" + String(repeating: "aB3", count: 14)
        let google = "AIzaSy" + String(repeating: "x9_", count: 12)
        let hex = String(repeating: "0f", count: 16)
        for key in [groq, google, hex] {
            let out = AgentChatStore.redact("here is my key \(key) ok")
            XCTAssertFalse(out.contains(key), out)
            XCTAssertTrue(out.contains("[key removed]"), out)
        }
    }

    func testRedactLeavesOrdinaryTextAlone() {
        for text in ["Make a 9:16 video about volcanoes", "https://example.com/a/b?c=d", "llama-3.3-70b-versatile",
                     "2026-10-04T09-00-00Z-ABC123", "short_token_123"] {
            XCTAssertEqual(AgentChatStore.redact(text), text)
        }
    }
}

/// Keep chat, as the loop uses it.
@MainActor
final class AgentChatLoopTests: XCTestCase {

    private func ok(_ m: AgentMessage) -> Result<AgentMessage, Error> { .success(m) }

    func testTurnEndSavesWhenKeepIsOn() async {
        let host = FakeAgentHost()
        host.settings.agentKeepChat = true
        let a = makeAgent(ScriptedProvider(queue: [ok(.assistant(text: "Hello"))]), host: host)
        defer { a.deleteSavedChat() }
        await a.send("hi").value
        let saved = AgentChatStore.load(from: a.chatURL!)
        XCTAssertEqual(saved.map { $0.text }, ["hi", "Hello"])
    }

    func testNothingIsWrittenWhenKeepIsOff() async {
        let host = FakeAgentHost()
        host.settings.agentKeepChat = false
        let a = makeAgent(ScriptedProvider(queue: [ok(.assistant(text: "Hello"))]), host: host)
        await a.send("hi").value
        XCTAssertFalse(FileManager.default.fileExists(atPath: a.chatURL!.path))
    }

    func testResetDeletesTheSavedFile() async {
        let host = FakeAgentHost()
        host.settings.agentKeepChat = true
        let a = makeAgent(ScriptedProvider(queue: [ok(.assistant(text: "Hello"))]), host: host)
        await a.send("hi").value
        XCTAssertTrue(FileManager.default.fileExists(atPath: a.chatURL!.path))
        a.reset()
        XCTAssertFalse(FileManager.default.fileExists(atPath: a.chatURL!.path))
    }

    func testDeleteSavedChatRemovesTheFileButKeepsTheOpenChat() async {
        let host = FakeAgentHost()
        host.settings.agentKeepChat = true
        let a = makeAgent(ScriptedProvider(queue: [ok(.assistant(text: "Hello"))]), host: host)
        await a.send("hi").value
        a.deleteSavedChat()
        XCTAssertFalse(FileManager.default.fileExists(atPath: a.chatURL!.path))
        XCTAssertEqual(a.lines.count, 2)
    }

    func testRestoreRebuildsWireHistoryAsPlainText() async throws {
        let url = tempChatURL()
        defer { AgentChatStore.delete(at: url) }
        try AgentChatStore.save([Agent.Line(kind: .user, text: "earlier question"),
                                 Agent.Line(kind: .assistant, text: "earlier answer")], to: url)
        let host = FakeAgentHost()
        host.settings.agentKeepChat = true
        let provider = ScriptedProvider(queue: [ok(.assistant(text: "fresh"))])
        let a = Agent()
        a.chatURL = url
        a.makeProvider = { _ in provider }
        a.makeContext = { AgentContext.test($0) }
        a.host = host
        withExtendedLifetime(host) {}
        XCTAssertEqual(a.lines.map { $0.text }, ["earlier question", "earlier answer"])
        await a.send("next").value
        let sent = provider.calls[0].messages
        XCTAssertEqual(sent.map { $0.text }, ["earlier question", "earlier answer", "next"])
        XCTAssertTrue(sent.allSatisfy { $0.toolCalls.isEmpty && $0.providerPayload == nil && $0.role != .tool })
    }

    func testRestoreWithKeepOffDoesNothing() throws {
        let url = tempChatURL()
        defer { AgentChatStore.delete(at: url) }
        try AgentChatStore.save([Agent.Line(kind: .user, text: "old")], to: url)
        let host = FakeAgentHost()
        host.settings.agentKeepChat = false
        let a = Agent()
        a.chatURL = url
        a.host = host
        XCTAssertTrue(a.lines.isEmpty)
    }

    func testSavedFileNeverContainsAKeyShapedString() async throws {
        let host = FakeAgentHost()
        host.settings.agentKeepChat = true
        let secret = "gsk_" + String(repeating: "Zq7", count: 14)
        let a = makeAgent(ScriptedProvider(queue: [ok(.assistant(text: "I can't use that."))]), host: host)
        defer { a.deleteSavedChat() }
        await a.send("my key is \(secret)").value
        let raw = try String(contentsOf: a.chatURL!, encoding: .utf8)
        XCTAssertFalse(raw.contains(secret))
        XCTAssertTrue(raw.contains("[key removed]"))
    }
}

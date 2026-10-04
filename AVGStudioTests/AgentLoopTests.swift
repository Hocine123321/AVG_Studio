import XCTest
@testable import AVGStudio

/// The loop: bounded steps, approvals that resume exactly once, malformed
/// calls answered by asking again, history kept small and well-formed.
@MainActor
final class AgentLoopTests: XCTestCase {

    private func ok(_ m: AgentMessage) -> Result<AgentMessage, Error> { .success(m) }

    private func toolMessages(_ call: ScriptedProvider.Call) -> [AgentMessage] { call.messages.filter { $0.role == .tool } }

    // MARK: Basics

    func testPlainReplyEndsTheTurn() async {
        let provider = ScriptedProvider(queue: [ok(.assistant(text: "Hi there"))])
        let a = makeAgent(provider, host: FakeAgentHost())
        await a.send("hello").value
        XCTAssertEqual(a.lines.map { $0.kind }, [.user, .assistant])
        XCTAssertEqual(a.lines.last?.text, "Hi there")
        XCTAssertFalse(a.isWorking)
        XCTAssertEqual(provider.calls.count, 1)
    }

    func testBlankMessagesAreIgnored() async {
        let provider = ScriptedProvider(queue: [])
        let a = makeAgent(provider, host: FakeAgentHost())
        await a.send("   \n ").value
        XCTAssertTrue(a.lines.isEmpty)
        XCTAssertTrue(provider.calls.isEmpty)
    }

    func testToolCallResultIsSentBackWithMatchingID() async {
        let provider = ScriptedProvider(queue: [ok(.calling("list_channels", id: "c1")), ok(.assistant(text: "You have two."))])
        let a = makeAgent(provider, host: FakeAgentHost())
        await a.send("what channels?").value
        XCTAssertEqual(provider.calls.count, 2)
        let results = toolMessages(provider.calls[1])
        XCTAssertEqual(results.count, 1)
        XCTAssertEqual(results[0].toolCallID, "c1")
        XCTAssertTrue(results[0].text.contains("Facts"))
        XCTAssertTrue(a.lines.contains { $0.kind == .activity })
    }

    func testRequestCarriesTheOfferedTools() async {
        let host = FakeAgentHost()
        host.settings.agentAllowCancel = false
        let provider = ScriptedProvider(queue: [ok(.assistant(text: "ok"))])
        let a = makeAgent(provider, host: host)
        await a.send("hi").value
        let names = Set(provider.calls[0].tools.map { $0.name })
        XCTAssertTrue(names.contains("create_video"))
        XCTAssertFalse(names.contains("cancel"))
    }

    // MARK: Bounds

    func testStepCapStopsARunawayLoop() async {
        let provider = ScriptedProvider { i, _ in .success(.calling("list_channels", id: "c\(i)")) }
        let a = makeAgent(provider, host: FakeAgentHost())
        await a.send("loop forever").value
        XCTAssertEqual(provider.calls.count, Agent.maxSteps)
        XCTAssertEqual(a.lines.last?.kind, .error)
        XCTAssertFalse(a.isWorking)
    }

    func testMalformedOutputIsNudgedTwiceThenFails() async {
        let bad = AVGError.provider(.malformedOutput, provider: "groq", message: "tool_use_failed")
        let provider = ScriptedProvider { _, _ in .failure(bad) }
        let host = FakeAgentHost()
        let a = makeAgent(provider, host: host)
        await a.send("do it").value
        XCTAssertEqual(provider.calls.count, 1 + Agent.maxNudges)
        let second = provider.calls[1].messages
        XCTAssertTrue(second.last?.synthetic == true, "the nudge is a synthetic user message")
        XCTAssertEqual(second.last?.text, Agent.nudgeText)
        XCTAssertEqual(a.lines.last?.kind, .error)
        XCTAssertFalse(a.lines.contains { $0.text == Agent.nudgeText }, "the nudge is not shown as something the person said")
    }

    func testMalformedOutputThenSuccessRecovers() async {
        let bad = AVGError.provider(.malformedOutput, provider: "groq", message: "tool_use_failed")
        let provider = ScriptedProvider(queue: [.failure(bad), ok(.assistant(text: "Done"))])
        let a = makeAgent(provider, host: FakeAgentHost())
        await a.send("do it").value
        XCTAssertEqual(a.lines.last?.text, "Done")
        XCTAssertFalse(a.lines.contains { $0.kind == .error })
    }

    func testOtherErrorsBecomeAnErrorLineAndEndTheTurn() async {
        let host = FakeAgentHost()
        let err = AVGError.provider(.auth, provider: "groq", message: "bad key")
        let provider = ScriptedProvider(queue: [.failure(err)])
        let a = makeAgent(provider, host: host)
        await a.send("hi").value
        XCTAssertEqual(provider.calls.count, 1, "only malformed output is asked again")
        XCTAssertEqual(a.lines.last?.kind, .error)
        XCTAssertTrue(a.lines.last?.text.contains("bad key") == true)
        XCTAssertTrue(host.recorded.contains { $0.contains("bad key") })
        XCTAssertFalse(a.isWorking)
    }

    func testMissingProviderKeyIsAReadableErrorLine() async {
        let provider = ScriptedProvider(queue: [])
        let a = makeAgent(provider, host: FakeAgentHost())
        a.makeProvider = { _ in throw AVGError.missingKey("Groq") }
        await a.send("hi").value
        XCTAssertTrue(a.lines.last?.text.contains("Groq") == true)
    }

    // MARK: Approvals

    private func startCreate(_ a: Agent, topic: String = "Volcanoes") -> Task<Void, Never> {
        a.send("make a video about \(topic)")
    }

    private var createReplies: [Result<AgentMessage, Error>] {
        [ok(.calling("create_video", #"{"topic":"Volcanoes"}"#, id: "c1")), ok(.assistant(text: "Started."))]
    }

    func testApprovalSuspendsThenApproveRunsTheActionOnce() async {
        let host = FakeAgentHost()
        let provider = ScriptedProvider(queue: createReplies)
        let a = makeAgent(provider, host: host)
        let turn = startCreate(a)
        await waitFor("approval") { a.pendingApproval != nil }
        XCTAssertTrue(host.created.isEmpty, "nothing starts before the tap")
        XCTAssertEqual(a.pendingApproval?.title, "Start this video?")
        a.approve()
        await turn.value
        XCTAssertEqual(host.created.count, 1)
        XCTAssertEqual(host.started.count, 1)
        XCTAssertNil(a.pendingApproval)
        XCTAssertTrue(toolMessages(provider.calls[1])[0].text.contains("Started"))
    }

    func testDeclineNeverTouchesTheHost() async {
        let host = FakeAgentHost()
        let provider = ScriptedProvider(queue: createReplies)
        let a = makeAgent(provider, host: host)
        let turn = startCreate(a)
        await waitFor("approval") { a.pendingApproval != nil }
        a.decline()
        await turn.value
        XCTAssertTrue(host.created.isEmpty && host.started.isEmpty)
        XCTAssertEqual(toolMessages(provider.calls[1])[0].text, "The user declined.")
    }

    func testApproveTwiceStartsOnce() async {
        let host = FakeAgentHost()
        let a = makeAgent(ScriptedProvider(queue: createReplies), host: host)
        let turn = startCreate(a)
        await waitFor("approval") { a.pendingApproval != nil }
        a.approve()
        a.approve()
        a.decline()
        await turn.value
        XCTAssertEqual(host.created.count, 1)
        XCTAssertEqual(host.started.count, 1)
    }

    func testStopWhileAwaitingApprovalResolvesAsDecline() async {
        let host = FakeAgentHost()
        let a = makeAgent(ScriptedProvider(queue: createReplies), host: host)
        let turn = startCreate(a)
        await waitFor("approval") { a.pendingApproval != nil }
        a.stop()
        await turn.value
        XCTAssertNil(a.pendingApproval)
        XCTAssertFalse(a.isWorking)
        XCTAssertTrue(host.created.isEmpty && host.started.isEmpty)
    }

    func testSendWhileWorkingIsIgnored() async {
        let host = FakeAgentHost()
        let provider = ScriptedProvider(queue: createReplies)
        let a = makeAgent(provider, host: host)
        let turn = startCreate(a)
        await waitFor("approval") { a.pendingApproval != nil }
        await a.send("and another one").value
        XCTAssertEqual(a.lines.filter { $0.kind == .user }.count, 1)
        a.decline()
        await turn.value
    }

    // MARK: History

    func testResetClearsLinesAndWire() async {
        let provider = ScriptedProvider { i, _ in .success(.assistant(text: "reply \(i)")) }
        let a = makeAgent(provider, host: FakeAgentHost())
        await a.send("first").value
        a.reset()
        XCTAssertTrue(a.lines.isEmpty)
        await a.send("second").value
        XCTAssertEqual(provider.calls[1].messages.map { $0.text }, ["second"], "nothing from before the reset is sent")
    }

    func testFinishedTurnIsCompacted() async {
        let provider = ScriptedProvider(queue: [
            ok(.calling("list_channels", id: "c1")), ok(.calling("list_projects", id: "c2")), ok(.assistant(text: "First answer")),
            ok(.assistant(text: "Second answer")),
        ])
        let a = makeAgent(provider, host: FakeAgentHost())
        await a.send("one").value
        await a.send("two").value
        let secondTurnRequest = provider.calls[3].messages
        XCTAssertEqual(secondTurnRequest.map { $0.text }, ["one", "First answer", "two"])
        XCTAssertTrue(secondTurnRequest.allSatisfy { $0.role != .tool && $0.toolCalls.isEmpty })
    }

    func testProviderSeesTrimmedHistory() async {
        let provider = ScriptedProvider { i, _ in .success(.assistant(text: "a\(i)")) }
        let a = makeAgent(provider, host: FakeAgentHost())
        for i in 0..<15 { await a.send("q\(i)").value }
        let last = provider.calls[14].messages
        XCTAssertLessThanOrEqual(last.count, Agent.historyWindow)
        XCTAssertTrue(last.first?.role == .user && last.first?.synthetic == false)
        XCTAssertEqual(last.last?.text, "q14", "the newest message is always sent")
    }

    // MARK: Stop

    func testStopBeforeTheReplyEndsQuietly() async {
        let host = FakeAgentHost()
        let provider = ScriptedProvider(queue: [ok(.assistant(text: "late"))])
        let a = makeAgent(provider, host: host)
        let turn = a.send("hi")
        a.stop()
        await turn.value
        XCTAssertFalse(a.isWorking)
        XCTAssertNil(a.pendingApproval)
    }

    // MARK: Self-test

    func testSelfTestOkOnlyWhenPingIsCalled() async {
        let a = makeAgent(ScriptedProvider(queue: [ok(.calling("ping"))]), host: FakeAgentHost())
        let r = await a.selfTest()
        guard case .ok(let label) = r else { return XCTFail("expected ok, got \(r)") }
        XCTAssertTrue(label.contains("Groq") && label.contains("llama-3.3-70b-versatile"), label)
    }

    func testSelfTestPlainTextMeansModelCannotUseTools() async {
        let a = makeAgent(ScriptedProvider(queue: [ok(.assistant(text: "pong"))]), host: FakeAgentHost())
        let r = await a.selfTest()
        guard case .failed(let why) = r else { return XCTFail("expected failure") }
        XCTAssertTrue(why.contains("without using tools"), why)
    }

    func testSelfTestMalformedCallMeansModelCannotUseTools() async {
        let bad = AVGError.provider(.malformedOutput, provider: "groq", message: "tool_use_failed")
        let a = makeAgent(ScriptedProvider(queue: [.failure(bad)]), host: FakeAgentHost())
        guard case .failed(let why) = await a.selfTest() else { return XCTFail("expected failure") }
        XCTAssertTrue(why.contains("valid tool calls"), why)
    }

    func testSelfTestErrorIsReadable() async {
        let a = makeAgent(ScriptedProvider(queue: []), host: FakeAgentHost())
        a.makeProvider = { _ in throw AVGError.missingKey("Groq") }
        guard case .failed(let why) = await a.selfTest() else { return XCTFail("expected failure") }
        XCTAssertTrue(why.contains("Groq"), why)
    }

    func testSelfTestLeavesTranscriptAndHostUntouched() async {
        let host = FakeAgentHost()
        let provider = ScriptedProvider(queue: [ok(.calling("ping"))])
        let a = makeAgent(provider, host: host)
        _ = await a.selfTest()
        XCTAssertTrue(a.lines.isEmpty)
        XCTAssertTrue(host.created.isEmpty && host.started.isEmpty && host.cancelled.isEmpty)
        XCTAssertEqual(provider.calls.count, 1)
        XCTAssertEqual(provider.calls[0].tools.map { $0.name }, ["ping"])
    }
}

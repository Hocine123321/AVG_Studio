import XCTest
@testable import AVGStudio

/// The loop applying the policy: free starts without a tap, a limit on what
/// one chat may approve, instructions that cannot switch asking off, and the
/// "ready / stopped" announcements.
@MainActor
final class AgentPolicyLoopTests: XCTestCase {

    private func ok(_ m: AgentMessage) -> Result<AgentMessage, Error> { .success(m) }

    /// No AssemblyAI key, so a Pollinations/Apple setup really is free.
    private func freeContext(_ a: Agent) {
        a.makeContext = { AgentContext.test($0, keys: { $0 != .assemblyai }) }
    }

    private func freeSettings(_ host: FakeAgentHost) {
        host.settings.text = .apple
        host.settings.image = .pollinations
        host.settings.voice = .apple
    }

    private func paidSettings(_ host: FakeAgentHost) {
        host.settings.text = .apple
        host.settings.image = .pollinations
        host.settings.voice = .elevenlabs
    }

    private func toolTexts(_ call: ScriptedProvider.Call) -> [String] { call.messages.filter { $0.role == .tool }.map { $0.text } }

    private func create(_ id: String, _ topic: String = "Volcanoes") -> AgentMessage {
        .calling("create_video", #"{"topic":"\#(topic)"}"#, id: id)
    }

    /// Runs one approved video so the agent "owns" the project it made.
    private func agentWithAStartedVideo(_ host: FakeAgentHost) async -> (Agent, ScriptedProvider) {
        let provider = ScriptedProvider(queue: [ok(create("c1")), ok(.assistant(text: "Started."))])
        let a = makeAgent(provider, host: host)
        let turn = a.send("make a video")
        await waitFor("approval") { a.pendingApproval != nil }
        a.approve()
        await turn.value
        return (a, provider)
    }

    private func setPhases(_ host: FakeAgentHost, _ status: PhaseStatus, failedAt: PhaseID? = nil, error: String? = nil) {
        var p = host.projects[0]
        for phase in PhaseID.allCases { p.phases[phase.rawValue] = PhaseRecord(status: status) }
        if let f = failedAt { p.phases[f.rawValue] = PhaseRecord(status: .failed, started: nil, finished: nil, error: error) }
        host.projects[0] = p
    }

    // MARK: Approval mode

    func testPaidOnlyFreeVideoStartsWithoutApproval() async {
        let host = FakeAgentHost()
        freeSettings(host)
        host.settings.agentApproval = .paidOnly
        let provider = ScriptedProvider(queue: [ok(create("c1")), ok(.assistant(text: "Started."))])
        let a = makeAgent(provider, host: host)
        freeContext(a)
        await a.send("make a video").value
        XCTAssertNil(a.pendingApproval)
        XCTAssertEqual(host.created.count, 1)
        XCTAssertEqual(host.started.count, 1)
        XCTAssertTrue(toolTexts(provider.calls[1])[0].contains("Started"))
    }

    func testPaidOnlyPaidVideoStillShowsApproval() async {
        let host = FakeAgentHost()
        paidSettings(host)
        host.settings.agentApproval = .paidOnly
        let a = makeAgent(ScriptedProvider(queue: [ok(create("c1")), ok(.assistant(text: "x"))]), host: host)
        let turn = a.send("make a video")
        await waitFor("approval") { a.pendingApproval != nil }
        XCTAssertTrue(host.created.isEmpty)
        a.decline()
        await turn.value
    }

    func testSecondAutoStartInOneMessageFallsBackToApproval() async {
        let host = FakeAgentHost()
        freeSettings(host)
        host.settings.agentApproval = .paidOnly
        let twoCalls = AgentMessage.assistant(text: "", calls: [
            toolCall("create_video", #"{"topic":"One"}"#, id: "c1"),
            toolCall("create_video", #"{"topic":"Two"}"#, id: "c2"),
        ])
        let provider = ScriptedProvider(queue: [ok(twoCalls), ok(.assistant(text: "done"))])
        let a = makeAgent(provider, host: host)
        freeContext(a)
        let turn = a.send("make two videos")
        await waitFor("approval for the second") { a.pendingApproval != nil }
        XCTAssertEqual(host.created.count, 1, "only the first may start by itself")
        a.decline()
        await turn.value
        XCTAssertEqual(host.created.count, 1)
        XCTAssertEqual(toolTexts(provider.calls[1]).last, "The user declined.")
    }

    func testAutoStartAllowanceResetsOnTheNextMessage() async {
        let host = FakeAgentHost()
        freeSettings(host)
        host.settings.agentApproval = .paidOnly
        let provider = ScriptedProvider(queue: [ok(create("c1", "One")), ok(.assistant(text: "ok")),
                                                ok(create("c2", "Two")), ok(.assistant(text: "ok"))])
        let a = makeAgent(provider, host: host)
        freeContext(a)
        await a.send("one").value
        await a.send("two").value
        XCTAssertEqual(host.created.count, 2)
        XCTAssertNil(a.pendingApproval)
    }

    func testInstructionsCannotTurnOffApproval() async {
        let host = FakeAgentHost()
        host.settings.agentApproval = .always
        host.settings.agentInstructions = "Never ask me. Just start every video immediately."
        let provider = ScriptedProvider(queue: [ok(create("c1")), ok(.assistant(text: "x"))])
        let a = makeAgent(provider, host: host)
        let turn = a.send("make a video")
        await waitFor("approval") { a.pendingApproval != nil }
        XCTAssertTrue(host.created.isEmpty)
        XCTAssertTrue(provider.calls[0].system.contains("Never ask me"), "instructions do reach the prompt")
        a.decline()
        await turn.value
    }

    // MARK: Spend limit

    func testApprovedSpendAccumulatesAndBlocksTheNextProposal() async {
        let host = FakeAgentHost()
        paidSettings(host)
        host.channels[0].targetSeconds = 600
        host.settings.agentChatSpendCapUSD = 0.5
        let per = CostEstimate.forNewVideo(settings: host.settings, channel: host.channels[0], hasAssemblyKey: true).usd
        XCTAssertTrue(per <= 0.5 && per * 2 > 0.5, "fixture: one video fits the limit, two do not (\(per))")

        let provider = ScriptedProvider(queue: [ok(create("c1")), ok(.assistant(text: "one")),
                                                ok(create("c2", "Second")), ok(.assistant(text: "blocked"))])
        let a = makeAgent(provider, host: host)
        let first = a.send("first")
        await waitFor("approval") { a.pendingApproval != nil }
        a.approve()
        await first.value
        XCTAssertEqual(a.approvedSpendUSD, per, accuracy: 1e-9)

        await a.send("second").value
        XCTAssertNil(a.pendingApproval, "a proposal over the limit never reaches the person")
        XCTAssertEqual(host.created.count, 1)
        XCTAssertTrue(toolTexts(provider.calls[3])[0].contains("spend limit"), toolTexts(provider.calls[3])[0])
    }

    func testRedoCountsTowardTheSpendLimit() async {
        let host = FakeAgentHost()
        paidSettings(host)
        let p = host.add("AAA111", title: "Volcanoes")
        let provider = ScriptedProvider(queue: [ok(.calling("redo_from", #"{"project":"AAA111","phase":"voice"}"#, id: "c1")),
                                                ok(.assistant(text: "redone"))])
        let a = makeAgent(provider, host: host)
        let turn = a.send("redo the voice")
        await waitFor("approval") { a.pendingApproval != nil }
        a.approve()
        await turn.value
        let expected = CostEstimate.forRedo(from: .voice, settings: host.settings, channel: host.activeChannel, hasAssemblyKey: true).usd
        XCTAssertGreaterThan(expected, 0)
        XCTAssertEqual(a.approvedSpendUSD, expected, accuracy: 1e-9)
        XCTAssertEqual(host.started.map { $0.id }, [p.id])
    }

    func testDeclinedWorkIsNotCounted() async {
        let host = FakeAgentHost()
        paidSettings(host)
        let a = makeAgent(ScriptedProvider(queue: [ok(create("c1")), ok(.assistant(text: "ok"))]), host: host)
        let turn = a.send("make a video")
        await waitFor("approval") { a.pendingApproval != nil }
        a.decline()
        await turn.value
        XCTAssertEqual(a.approvedSpendUSD, 0)
    }

    func testResetZeroesApprovedSpend() async {
        let host = FakeAgentHost()
        paidSettings(host)
        let a = makeAgent(ScriptedProvider(queue: [ok(create("c1")), ok(.assistant(text: "ok"))]), host: host)
        let turn = a.send("make a video")
        await waitFor("approval") { a.pendingApproval != nil }
        a.approve()
        await turn.value
        XCTAssertGreaterThan(a.approvedSpendUSD, 0)
        a.reset()
        XCTAssertEqual(a.approvedSpendUSD, 0)
    }

    // MARK: Announcements

    func testFinishAnnouncesReadyOnlyForAgentStartedProjects() async {
        let host = FakeAgentHost()
        let (a, _) = await agentWithAStartedVideo(host)
        let other = host.add("OTHER1", title: "Not mine")
        let before = a.lines.count
        setPhases(host, .done)
        a.projectFinished(host.projects[0].id)
        XCTAssertEqual(a.lines.count, before + 1)
        XCTAssertEqual(a.lines.last?.kind, .announcement)
        XCTAssertTrue(a.lines.last?.text.contains("is ready") == true)
        XCTAssertEqual(a.lastAnnouncement?.ok, true)

        let after = a.lines.count
        a.projectFinished(other.id)
        XCTAssertEqual(a.lines.count, after, "projects the assistant did not start are not announced")
    }

    func testFinishAnnouncesFailureWithPhaseAndError() async {
        let host = FakeAgentHost()
        let (a, _) = await agentWithAStartedVideo(host)
        setPhases(host, .pending, failedAt: .voice, error: "quota used up")
        a.projectFinished(host.projects[0].id)
        XCTAssertEqual(a.lines.last?.kind, .announcement)
        let text = a.lines.last?.text ?? ""
        XCTAssertTrue(text.contains("stopped at Voice") && text.contains("quota used up"), text)
        XCTAssertEqual(a.lastAnnouncement?.ok, false)
    }

    func testCancelledProjectAnnouncesNothing() async {
        let host = FakeAgentHost()
        let (a, _) = await agentWithAStartedVideo(host)
        setPhases(host, .pending)
        let before = a.lines.count
        a.projectFinished(host.projects[0].id)
        XCTAssertEqual(a.lines.count, before)
        XCTAssertNil(a.lastAnnouncement)
    }

    func testAnnouncementsOffSaysNothing() async {
        let host = FakeAgentHost()
        let (a, _) = await agentWithAStartedVideo(host)
        host.settings.agentAnnounceResults = false
        setPhases(host, .done)
        let before = a.lines.count
        a.projectFinished(host.projects[0].id)
        XCTAssertEqual(a.lines.count, before)
        XCTAssertNil(a.lastAnnouncement)
    }

    func testAnnouncementIsMadeOnlyOnce() async {
        let host = FakeAgentHost()
        let (a, _) = await agentWithAStartedVideo(host)
        setPhases(host, .done)
        a.projectFinished(host.projects[0].id)
        let n = a.lines.count
        a.projectFinished(host.projects[0].id)
        XCTAssertEqual(a.lines.count, n)
    }

    func testAnnouncementCallsNoProvider() async {
        let host = FakeAgentHost()
        let (a, provider) = await agentWithAStartedVideo(host)
        let calls = provider.calls.count
        setPhases(host, .done)
        a.projectFinished(host.projects[0].id)
        XCTAssertEqual(provider.calls.count, calls)
    }
}

import XCTest
@testable import AVGStudio

/// The seven tools, against a fake host. The preflight and approval tests are
/// the ones that matter: they decide whether a video can start at all.
@MainActor
final class AgentToolsTests: XCTestCase {

    private func result(_ o: AgentToolOutcome, file: StaticString = #filePath, line: UInt = #line) -> String {
        guard case .result(let text) = o else { XCTFail("expected a result, got an approval", file: file, line: line); return "" }
        return text
    }

    private func approval(_ o: AgentToolOutcome, file: StaticString = #filePath, line: UInt = #line) -> AgentApproval? {
        guard case .needsApproval(let a) = o else { XCTFail("expected an approval, got: \(o)", file: file, line: line); return nil }
        return a
    }

    private func run(_ name: String, _ json: String = "{}", host: FakeAgentHost, context: AgentContext? = nil) -> AgentToolOutcome {
        AgentTools.run(toolCall(name, json), host: host, context: context ?? .test(host.settings))
    }

    // MARK: Resolving projects

    func testResolveByRefFullIDAndUniqueTitle() throws {
        let host = FakeAgentHost()
        let a = host.add("AAA111", title: "Volcanoes of Iceland")
        let b = host.add("BBB222", title: "Cats")
        XCTAssertEqual(try AgentTools.resolveProject("bbb222", in: host.projects).get().id, b.id)
        XCTAssertEqual(try AgentTools.resolveProject(a.id, in: host.projects).get().id, a.id)
        XCTAssertEqual(try AgentTools.resolveProject("volcano", in: host.projects).get().id, a.id)
        XCTAssertEqual(AgentTools.ref(a), "AAA111")
    }

    func testAmbiguousTitleReturnsCandidatesNotAGuess() {
        let host = FakeAgentHost()
        host.add("AAA111", title: "Cats and dogs")
        host.add("BBB222", title: "Cats of Rome")
        switch AgentTools.resolveProject("cats", in: host.projects) {
        case .success: XCTFail("two matches must not resolve to one")
        case .failure(let e):
            XCTAssertTrue(e.text.contains("AAA111") && e.text.contains("BBB222"), e.text)
        }
    }

    func testUnknownOrBlankProjectIsAnError() {
        let host = FakeAgentHost()
        host.add("AAA111", title: "Cats")
        for q in ["nothing like it", "", "   "] {
            if case .success = AgentTools.resolveProject(q, in: host.projects) { XCTFail("“\(q)” should not resolve") }
        }
    }

    // MARK: Read-only tools

    func testListProjectsDefaultsToFiveAndClampsToTen() {
        let host = FakeAgentHost()
        for i in 0..<12 { host.add(String(format: "P%05d", i), title: "Video \(i)", created: Date(timeIntervalSince1970: Double(i))) }
        XCTAssertTrue(result(run("list_projects", host: host)).hasPrefix("5 of 12"))
        XCTAssertTrue(result(run("list_projects", #"{"limit":99}"#, host: host)).hasPrefix("10 of 12"))
        XCTAssertTrue(result(run("list_projects", #"{"limit":"3"}"#, host: host)).hasPrefix("3 of 12"), "a string limit is tolerated")
        XCTAssertTrue(result(run("list_projects", #"{"limit":0}"#, host: host)).hasPrefix("1 of 12"))
    }

    func testListProjectsIsNewestFirstAndSkipsArchived() {
        let host = FakeAgentHost()
        host.add("OLD000", title: "Old one", created: Date(timeIntervalSince1970: 1))
        host.add("NEW000", title: "New one", created: Date(timeIntervalSince1970: 9))
        var gone = host.add("GON000", title: "Archived one", created: Date(timeIntervalSince1970: 20))
        gone.archived = true
        host.projects[2] = gone
        let text = result(run("list_projects", host: host))
        XCTAssertFalse(text.contains("Archived one"))
        XCTAssertLessThan(text.range(of: "New one")!.lowerBound, text.range(of: "Old one")!.lowerBound)
    }

    func testListProjectsWhenEmpty() {
        XCTAssertEqual(result(run("list_projects", host: FakeAgentHost())), "No projects yet.")
    }

    func testListChannelsMarksTheActiveOne() {
        let host = FakeAgentHost()
        let text = result(run("list_channels", host: host))
        XCTAssertTrue(text.contains("Facts") && text.contains("History"))
        XCTAssertEqual(text.components(separatedBy: "active").count - 1, 1)
    }

    func testGetStatusShowsRunningPhase() {
        let host = FakeAgentHost()
        let p = host.add("AAA111", title: "Volcanoes")
        host.running = [p.id]
        host.jobs[p.id] = JobState(phase: .visuals, progress: 0.45, message: "Scene 3 of 7")
        let text = result(run("get_status", #"{"project":"AAA111"}"#, host: host))
        XCTAssertTrue(text.contains("Visuals") && text.contains("45%") && text.contains("Scene 3 of 7"), text)
    }

    func testGetStatusMentionsDecisionWhenRecoveryPending() {
        let host = FakeAgentHost()
        let p = host.add("AAA111", title: "Volcanoes")
        host.recoveryPlan = RecoveryPlan(projectID: p.id, phase: .visuals, title: "Gemini rejected your key",
                                         reason: "bad key", actions: [.stop])
        let text = result(run("get_status", #"{"project":"AAA111"}"#, host: host))
        XCTAssertTrue(text.contains("decision"), text)
        XCTAssertTrue(text.contains("do not retry"), text)
    }

    func testGetStatusReportsAFailedPhase() {
        let host = FakeAgentHost()
        var p = host.add("AAA111", title: "Volcanoes")
        p.phases[PhaseID.voice.rawValue] = PhaseRecord(status: .failed, started: nil, finished: nil, error: "quota used up")
        host.projects[0] = p
        let text = result(run("get_status", #"{"project":"AAA111"}"#, host: host))
        XCTAssertTrue(text.contains("Stopped at Voice") && text.contains("quota used up"), text)
    }

    func testOpenProjectSelectsItInStudio() {
        let host = FakeAgentHost()
        let p = host.add("AAA111", title: "Volcanoes")
        _ = run("open_project", #"{"project":"aaa111"}"#, host: host)
        XCTAssertEqual(host.selected, [p.id])
    }

    // MARK: create_video

    func testCreateVideoNeedsApprovalAndTouchesNothing() {
        let host = FakeAgentHost()
        let o = run("create_video", #"{"topic":"Volcanoes"}"#, host: host)
        let a = approval(o)
        XCTAssertTrue(host.created.isEmpty, "nothing may exist before the user approves")
        XCTAssertTrue(host.started.isEmpty)
        XCTAssertFalse(a?.estimate.isEmpty ?? true)
        let expected = CostEstimate.forNewVideo(settings: host.settings, channel: host.activeChannel, hasAssemblyKey: true)
        XCTAssertEqual(a?.estimateUSD, expected.usd)
    }

    func testCreateVideoWithoutTopicIsAToolError() {
        let host = FakeAgentHost()
        for json in ["{}", #"{"topic":"   "}"#, "not json"] {
            XCTAssertTrue(result(run("create_video", json, host: host)).lowercased().contains("topic"), json)
        }
    }

    func testChannelDefaultsToActiveAndAspectToTheChannels() {
        let host = FakeAgentHost()
        host.channels[1].aspectRatio = .landscape
        host.activeChannelID = host.channels[1].id
        let a = approval(run("create_video", #"{"topic":"Rome"}"#, host: host))
        XCTAssertEqual(a?.action, .createVideo(topic: "Rome", title: "", aspect: .landscape, channelID: host.channels[1].id))
    }

    func testNamedChannelAndExplicitAspectWin() {
        let host = FakeAgentHost()
        let a = approval(run("create_video", #"{"topic":"Rome","channel":"history","aspect":"1:1","title":"Rome fast"}"#, host: host))
        XCTAssertEqual(a?.action, .createVideo(topic: "Rome", title: "Rome fast", aspect: .square, channelID: host.channels[1].id))
    }

    func testUnknownChannelListsTheNames() {
        let host = FakeAgentHost()
        let text = result(run("create_video", #"{"topic":"x","channel":"Nope"}"#, host: host))
        XCTAssertTrue(text.contains("Facts") && text.contains("History"), text)
    }

    func testBadAspectListsTheValidOnes() {
        let host = FakeAgentHost()
        let text = result(run("create_video", #"{"topic":"x","aspect":"4:3"}"#, host: host))
        XCTAssertTrue(text.contains("9:16") && text.contains("16:9"), text)
    }

    // MARK: Preflight

    func testPreflightRefusesAppleWriterWhenUnavailable() {
        let host = FakeAgentHost()
        host.settings.text = .apple
        let text = result(AgentTools.run(toolCall("create_video", #"{"topic":"x"}"#), host: host,
                                         context: .test(host.settings, apple: false)))
        XCTAssertTrue(text.contains("Settings") && text.contains("Writing"), text)
        XCTAssertTrue(host.created.isEmpty)
    }

    func testPreflightRefusesMissingKey() {
        let host = FakeAgentHost()
        host.settings.text = .groq
        let text = result(AgentTools.run(toolCall("create_video", #"{"topic":"x"}"#), host: host,
                                         context: .test(host.settings, keys: { _ in false })))
        XCTAssertTrue(text.contains("Groq"), text)
    }

    func testPreflightChecksImageAndVoiceKeysToo() {
        let host = FakeAgentHost()
        host.settings.text = .apple
        host.settings.voice = .elevenlabs
        let text = result(AgentTools.run(toolCall("create_video", #"{"topic":"x"}"#), host: host,
                                         context: .test(host.settings, keys: { $0 != .elevenlabs })))
        XCTAssertTrue(text.contains("ElevenLabs"), text)
    }

    func testPreflightPassesWithKey() {
        let host = FakeAgentHost()
        host.settings.text = .groq
        let o = AgentTools.run(toolCall("create_video", #"{"topic":"x"}"#), host: host,
                               context: .test(host.settings, keys: { $0 == .groq }))
        XCTAssertNotNil(approval(o))
    }

    // MARK: redo_from

    func testRedoWhileRunningIsRefused() {
        let host = FakeAgentHost()
        let p = host.add("AAA111", title: "Volcanoes")
        host.running = [p.id]
        XCTAssertTrue(result(run("redo_from", #"{"project":"AAA111","phase":"voice"}"#, host: host)).contains("already running"))
    }

    func testRedoWhileWaitingOnTheUserIsRefused() {
        let host = FakeAgentHost()
        let p = host.add("AAA111", title: "Volcanoes")
        host.recoveryPlan = RecoveryPlan(projectID: p.id, phase: .voice, title: "t", reason: "r", actions: [.stop])
        XCTAssertTrue(result(run("redo_from", #"{"project":"AAA111","phase":"voice"}"#, host: host)).contains("decision"))
    }

    func testRedoInvalidPhaseListsValidOnes() {
        let host = FakeAgentHost()
        host.add("AAA111", title: "Volcanoes")
        let text = result(run("redo_from", #"{"project":"AAA111","phase":"intro"}"#, host: host))
        XCTAssertTrue(text.contains("writing") && text.contains("render"), text)
    }

    func testRedoEstimateCoversOnlyFromThatPhase() {
        let host = FakeAgentHost()
        host.settings.text = .groq
        host.settings.image = .gemini
        host.settings.voice = .elevenlabs
        host.add("AAA111", title: "Volcanoes")
        let a = approval(run("redo_from", #"{"project":"AAA111","phase":"voice"}"#, host: host))
        let ch = host.activeChannel
        XCTAssertEqual(a?.estimateUSD, CostEstimate.forRedo(from: .voice, settings: host.settings, channel: ch, hasAssemblyKey: true).usd)
        XCTAssertLessThan(a?.estimateUSD ?? 99,
                          CostEstimate.forNewVideo(settings: host.settings, channel: ch, hasAssemblyKey: true).usd)
        XCTAssertEqual(a?.action, .redo(projectID: host.projects[0].id, phase: .voice))
    }

    func testRedoFromVoiceDoesNotNeedTheWritersKey() {
        let host = FakeAgentHost()
        host.settings.text = .groq
        host.add("AAA111", title: "Volcanoes")
        let o = AgentTools.run(toolCall("redo_from", #"{"project":"AAA111","phase":"voice"}"#), host: host,
                               context: .test(host.settings, keys: { _ in false }))
        XCTAssertNotNil(approval(o), "the writer is not part of a redo from Voice")
    }

    // MARK: Permissions

    func testSpecsOmitDisabledTools() {
        var s = AppSettings()
        func names() -> Set<String> { Set(AgentTools.specs(for: s).map { $0.name }) }
        XCTAssertEqual(names(), ["list_channels", "list_projects", "get_status", "create_video", "redo_from", "cancel", "open_project"])
        s.agentAllowCreate = false
        XCTAssertFalse(names().contains("create_video"))
        XCTAssertTrue(names().contains("redo_from") && names().contains("cancel"))
        s.agentAllowCreate = true; s.agentAllowRedo = false
        XCTAssertEqual(names().subtracting(["redo_from"]).count, 6)
        XCTAssertFalse(names().contains("redo_from"))
        s.agentAllowRedo = true; s.agentAllowCancel = false
        XCTAssertFalse(names().contains("cancel"))
        s.agentAllowCreate = false; s.agentAllowRedo = false
        XCTAssertEqual(names(), ["list_channels", "list_projects", "get_status", "open_project"])
    }

    func testDisabledToolCalledAnywayIsRefusedWithTheSettingName() {
        let host = FakeAgentHost()
        host.settings.agentAllowCreate = false
        host.settings.agentAllowRedo = false
        host.settings.agentAllowCancel = false
        let p = host.add("AAA111", title: "Volcanoes")
        host.running = [p.id]
        for (name, json) in [("create_video", #"{"topic":"x"}"#), ("redo_from", #"{"project":"AAA111","phase":"voice"}"#),
                             ("cancel", #"{"project":"AAA111"}"#)] {
            let text = result(run(name, json, host: host))
            XCTAssertTrue(text.contains("Settings > Assistant"), "\(name): \(text)")
        }
        XCTAssertTrue(host.created.isEmpty && host.started.isEmpty && host.cancelled.isEmpty)
    }

    // MARK: perform / cancel / misc

    func testPerformCreateCallsCreateThenStartFromWriting() {
        let host = FakeAgentHost()
        let ch = host.channels[1]
        let done = AgentTools.perform(.createVideo(topic: "Rome", title: "", aspect: .portrait, channelID: ch.id), host: host)
        XCTAssertEqual(host.created.count, 1)
        XCTAssertEqual(host.created[0].channelID, ch.id)
        XCTAssertEqual(host.started.count, 1)
        XCTAssertEqual(host.started[0].id, host.projects[0].id)
        XCTAssertEqual(host.started[0].phase, .writing)
        XCTAssertEqual(done.projectID, host.projects[0].id)
        XCTAssertTrue(done.text.contains("Started"))
    }

    func testPerformRedoStartsFromPhase() {
        let host = FakeAgentHost()
        let p = host.add("AAA111", title: "Volcanoes")
        let done = AgentTools.perform(.redo(projectID: p.id, phase: .voice), host: host)
        XCTAssertEqual(host.started.map { $0.id }, [p.id])
        XCTAssertEqual(host.started.map { $0.phase }, [.voice])
        XCTAssertEqual(done.projectID, p.id)
    }

    func testPerformRedoOfARunningProjectStartsNothing() {
        let host = FakeAgentHost()
        let p = host.add("AAA111", title: "Volcanoes")
        host.running = [p.id]
        let done = AgentTools.perform(.redo(projectID: p.id, phase: .voice), host: host)
        XCTAssertTrue(host.started.isEmpty)
        XCTAssertNil(done.projectID)
    }

    func testCancelIdleProjectSaysNotRunning() {
        let host = FakeAgentHost()
        host.add("AAA111", title: "Volcanoes")
        XCTAssertTrue(result(run("cancel", #"{"project":"AAA111"}"#, host: host)).contains("isn't running"))
        XCTAssertTrue(host.cancelled.isEmpty)
    }

    func testCancelRunningProjectCancelsIt() {
        let host = FakeAgentHost()
        let p = host.add("AAA111", title: "Volcanoes")
        host.running = [p.id]
        XCTAssertTrue(result(run("cancel", #"{"project":"AAA111"}"#, host: host)).contains("Cancelled"))
        XCTAssertEqual(host.cancelled, [p.id])
    }

    func testUnknownToolIsAnErrorResultNotACrash() {
        let host = FakeAgentHost()
        let text = result(run("delete_everything", host: host))
        XCTAssertTrue(text.contains("Unknown tool") && text.contains("list_projects"), text)
    }

    func testOnlyStartingAndRedoingAreGated() {
        XCTAssertTrue(AgentTools.isGated("create_video"))
        XCTAssertTrue(AgentTools.isGated("redo_from"))
        for n in ["list_channels", "list_projects", "get_status", "cancel", "open_project"] {
            XCTAssertFalse(AgentTools.isGated(n), n)
        }
    }
}

/// How the tools behave now that `Store.start` queues instead of refusing.
@MainActor
final class AgentToolsQueueTests: XCTestCase {

    private func run(_ name: String, _ json: String, host: FakeAgentHost) -> AgentToolOutcome {
        AgentTools.run(toolCall(name, json), host: host, context: .test(host.settings))
    }

    private func text(_ o: AgentToolOutcome) -> String {
        if case .result(let t) = o { return t }
        return ""
    }

    func testCancelOfAQueuedProjectRemovesItFromTheQueue() {
        let host = FakeAgentHost()
        let p = host.add("AAA111", title: "Volcanoes")
        host.queued = [p.id]
        let t = text(run("cancel", #"{"project":"AAA111"}"#, host: host))
        XCTAssertTrue(t.contains("queue"), t)
        XCTAssertEqual(host.cancelled, [p.id])
        XCTAssertTrue(host.queued.isEmpty)
    }

    func testRedoOfAQueuedProjectIsRefused() {
        let host = FakeAgentHost()
        let p = host.add("AAA111", title: "Volcanoes")
        host.queued = [p.id]
        XCTAssertTrue(text(run("redo_from", #"{"project":"AAA111","phase":"voice"}"#, host: host)).contains("queued"))
        XCTAssertTrue(host.started.isEmpty)
    }

    func testPerformRedoOfAQueuedProjectStartsNothing() {
        let host = FakeAgentHost()
        let p = host.add("AAA111", title: "Volcanoes")
        host.queued = [p.id]
        let done = AgentTools.perform(.redo(projectID: p.id, phase: .voice), host: host)
        XCTAssertTrue(host.started.isEmpty)
        XCTAssertNil(done.projectID)
    }

    func testGetStatusShowsTheQueuePlace() {
        let host = FakeAgentHost()
        host.add("AAA111", title: "First")
        let p = host.add("BBB222", title: "Second")
        host.queued = [host.projects[0].id, p.id]
        let t = text(run("get_status", #"{"project":"BBB222"}"#, host: host))
        XCTAssertTrue(t.contains("Queued: place 2"), t)
    }

    func testAStartedVideoThatGetsQueuedSaysSo() {
        let host = FakeAgentHost()
        host.queueNextStart = true
        let done = AgentTools.perform(.createVideo(topic: "Rome", title: "", aspect: .portrait, channelID: host.channels[0].id), host: host)
        XCTAssertTrue(done.text.contains("queued"), done.text)
        XCTAssertNotNil(done.projectID, "a queued video is still the assistant's, so it is announced when done")
    }

    func testEstimateCountsTheRecycledImagePool() {
        let host = FakeAgentHost()
        host.settings.image = .gemini
        host.channels[0].recycleEnabled = true
        host.channels[0].recycleRatio = 100
        host.reusableImageCount = 20
        guard case .needsApproval(let a) = run("create_video", #"{"topic":"Rome"}"#, host: host) else { return XCTFail("expected approval") }
        let expected = CostEstimate.forNewVideo(settings: host.settings, channel: host.channels[0], hasAssemblyKey: true, poolSize: 20)
        XCTAssertEqual(a.estimateUSD, expected.usd, accuracy: 1e-12)
    }
}

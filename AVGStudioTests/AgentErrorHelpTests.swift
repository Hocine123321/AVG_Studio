import XCTest
@testable import AVGStudio

/// Assistant failures in the person's words, with one-tap fixes.
final class AgentErrorHelpTests: XCTestCase {

    private func perr(_ kind: ErrorKind, _ message: String = "raw") -> Error {
        AVGError.provider(kind, provider: "groq", message: message)
    }

    private func help(_ error: Error, _ provider: AgentProviderKind = .groq, other: Bool = true) -> AgentErrorHelp {
        AgentErrorHelp.help(for: error, provider: provider, otherHasKey: other)
    }

    func testMissingKeyPointsAtKeys() {
        let h = help(AVGError.missingKey("Groq"))
        XCTAssertTrue(h.title.contains("Groq"))
        XCTAssertEqual(h.fixes, [.openKeys])
    }

    func testRejectedKeyOffersKeysAndTheOtherServiceWhenItHasAKey() {
        XCTAssertEqual(help(perr(.auth)).fixes, [.openKeys, .switchProvider(to: .gemini)])
        XCTAssertEqual(help(perr(.auth), other: false).fixes, [.openKeys], "no switch to a service with no key")
    }

    func testOutOfQuotaLeadsWithSwitchingWhenPossible() {
        for kind in [ErrorKind.quota, .billing] {
            XCTAssertEqual(help(perr(kind)).fixes.first, .switchProvider(to: .gemini), "\(kind)")
            XCTAssertTrue(help(perr(kind)).fixes.contains(.openKeys))
            XCTAssertEqual(help(perr(kind), other: false).fixes, [.openKeys])
        }
    }

    func testRateLimitSuggestsWaitingThenSwitching() {
        let h = help(perr(.rateLimit))
        XCTAssertEqual(h.fixes, [.retry, .switchProvider(to: .gemini)])
        XCTAssertTrue(h.title.contains("busy"))
    }

    func testModelMissingAndMalformedGoToAssistantSettings() {
        XCTAssertEqual(help(perr(.modelMissing)).fixes.first, .openAssistantSettings)
        XCTAssertEqual(help(perr(.malformedOutput)).fixes, [.openAssistantSettings, .retry])
    }

    func testNetworkAndTimeoutRetryFirst() {
        XCTAssertEqual(help(perr(.network)).fixes.first, .retry)
        XCTAssertEqual(help(perr(.timeout)).fixes.first, .retry)
    }

    func testContentFilterOffersNoButtons() {
        XCTAssertTrue(help(perr(.contentFilter)).fixes.isEmpty)
    }

    func testTheOtherServiceIsNamedFromTheFailingOne() {
        let h = help(perr(.quota), .gemini)
        XCTAssertTrue(h.title.contains("Google Gemini"))
        XCTAssertEqual(h.fixes.first, .switchProvider(to: .groq))
        XCTAssertTrue(h.detail.contains("Groq"))
    }

    func testEveryKindHasATitleAndDetail() {
        let kinds: [ErrorKind] = [.auth, .quota, .billing, .rateLimit, .modelMissing, .contentFilter, .network,
                                  .timeout, .dependency, .bug, .malformedOutput]
        for k in kinds {
            let h = help(perr(k, ""))
            XCTAssertFalse(h.title.isEmpty, "\(k)")
            XCTAssertFalse(h.detail.isEmpty, "\(k)")
        }
    }

    func testUnknownErrorsGetARetryAndTheirOwnText() {
        struct Odd: LocalizedError { var errorDescription: String? { "odd failure" } }
        let h = help(Odd())
        XCTAssertEqual(h.detail, "odd failure")
        XCTAssertEqual(h.fixes, [.retry])
        XCTAssertEqual(help(AVGError.message("plain text")).detail, "plain text")
    }

    func testFixesHaveLabelsAndSymbols() {
        for fix in [AgentFix.openKeys, .openAssistantSettings, .switchProvider(to: .groq), .retry] {
            XCTAssertFalse(fix.label.isEmpty)
            XCTAssertFalse(fix.symbol.isEmpty)
        }
        XCTAssertEqual(AgentFix.switchProvider(to: .gemini).label, "Switch to Google Gemini")
    }

    func testSentenceJoinsTitleAndDetail() {
        let h = help(perr(.rateLimit))
        XCTAssertEqual(h.sentence, "\(h.title). \(h.detail)")
    }
}

/// The loop wiring: errors with fixes, retry, and the videos it is watching.
@MainActor
final class AgentErrorLoopTests: XCTestCase {

    private func ok(_ m: AgentMessage) -> Result<AgentMessage, Error> { .success(m) }

    func testErrorLineCarriesTitleAndFixes() async {
        let host = FakeAgentHost()
        host.settings.agentFailover = false
        let err = AVGError.provider(.rateLimit, provider: "groq", message: "429")
        let a = makeAgent(ScriptedProvider(queue: [.failure(err)]), host: host)
        await a.send("hi").value
        let line = a.lines.last
        XCTAssertEqual(line?.kind, .error)
        XCTAssertEqual(line?.title, "Groq is busy")
        XCTAssertTrue(line?.fixes.contains(.retry) == true)
    }

    func testRetryResendsTheSameMessageWithoutDuplicatingIt() async {
        let host = FakeAgentHost()
        host.settings.agentFailover = false
        let err = AVGError.provider(.rateLimit, provider: "groq", message: "429")
        let provider = ScriptedProvider(queue: [.failure(err), ok(.assistant(text: "fine now"))])
        let a = makeAgent(provider, host: host)
        await a.send("hi").value
        let retry = a.retryLast()
        XCTAssertNotNil(retry)
        await retry?.value
        XCTAssertEqual(a.lines.map { $0.kind }, [.user, .assistant])
        XCTAssertEqual(a.lines.last?.text, "fine now")
        XCTAssertEqual(provider.calls[1].messages.map { $0.text }, ["hi"], "the failed attempt is forgotten, not repeated")
    }

    func testRetryWithNothingToRetryDoesNothing() {
        let a = makeAgent(ScriptedProvider(queue: []), host: FakeAgentHost())
        XCTAssertNil(a.retryLast())
    }

    func testRetryIsIgnoredWhileWorking() async {
        let host = FakeAgentHost()
        let provider = ScriptedProvider(queue: [ok(.calling("create_video", #"{"topic":"x"}"#)), ok(.assistant(text: "x"))])
        let a = makeAgent(provider, host: host)
        let turn = a.send("make one")
        await waitFor("approval") { a.pendingApproval != nil }
        XCTAssertNil(a.retryLast())
        a.decline()
        await turn.value
    }

    func testWatchingTracksAStartedVideoUntilItsRunEnds() async {
        let host = FakeAgentHost()
        let provider = ScriptedProvider(queue: [ok(.calling("create_video", #"{"topic":"Volcanoes"}"#)), ok(.assistant(text: "ok"))])
        let a = makeAgent(provider, host: host)
        let turn = a.send("make one")
        await waitFor("approval") { a.pendingApproval != nil }
        XCTAssertTrue(a.watching.isEmpty, "nothing is watched before the person approves")
        a.approve()
        await turn.value
        XCTAssertEqual(a.watching, [host.projects[0].id])
        a.projectFinished(host.projects[0].id)
        XCTAssertTrue(a.watching.isEmpty)
    }

    func testDeclinedVideosAreNeverWatched() async {
        let host = FakeAgentHost()
        let provider = ScriptedProvider(queue: [ok(.calling("create_video", #"{"topic":"x"}"#)), ok(.assistant(text: "ok"))])
        let a = makeAgent(provider, host: host)
        let turn = a.send("make one")
        await waitFor("approval") { a.pendingApproval != nil }
        a.decline()
        await turn.value
        XCTAssertTrue(a.watching.isEmpty)
    }

    func testWatchingIsNotDuplicatedByASecondStartOfTheSameProject() async {
        let host = FakeAgentHost()
        let p = host.add("AAA111", title: "Volcanoes")
        let provider = ScriptedProvider(queue: [
            ok(.calling("redo_from", #"{"project":"AAA111","phase":"voice"}"#, id: "c1")), ok(.assistant(text: "one")),
            ok(.calling("redo_from", #"{"project":"AAA111","phase":"render"}"#, id: "c2")), ok(.assistant(text: "two")),
        ])
        let a = makeAgent(provider, host: host)
        for _ in 0..<2 {
            let turn = a.send("redo")
            await waitFor("approval") { a.pendingApproval != nil }
            a.approve()
            await turn.value
        }
        XCTAssertEqual(a.watching, [p.id])
    }
}

/// "Switch to Gemini" under an error is for this chat only.
@MainActor
final class AgentChatProviderTests: XCTestCase {

    func testSwitchingForThisChatUsesTheOtherServiceWithoutTouchingSettings() async {
        let host = FakeAgentHost()
        let primary = ScriptedProvider(queue: [])
        let other = ScriptedProvider(queue: [.success(.assistant(text: "from gemini"))])
        let a = makeAgent(primary, host: host)
        a.makeProvider = { kind in kind == .gemini ? other : primary }
        a.useForThisChat(.gemini)
        await a.send("hi").value
        XCTAssertEqual(a.lines.last?.text, "from gemini")
        XCTAssertTrue(primary.calls.isEmpty)
        XCTAssertEqual(host.settings.agentProvider, .groq, "the service chosen in Settings is untouched")
    }

    func testNewChatForgetsTheSwitch() {
        let a = makeAgent(ScriptedProvider(queue: []), host: FakeAgentHost())
        a.useForThisChat(.gemini)
        XCTAssertEqual(a.chatProvider, .gemini)
        a.reset()
        XCTAssertNil(a.chatProvider)
    }
}

/// The backup key, as the assistant uses it.
@MainActor
final class AgentBackupKeyTests: XCTestCase {

    private func suite() -> UserDefaults { UserDefaults(suiteName: "AgentBackup-\(UUID().uuidString)")! }

    private func agent(_ provider: ScriptedProvider, backup: Bool, defaults: UserDefaults) -> Agent {
        let host = FakeAgentHost()
        host.settings.agentFailover = false      // isolate the backup step from switching service
        let a = makeAgent(provider, host: host)
        a.makeContext = { AgentContext.test($0, backup: { _ in backup }) }
        a.backupDefaults = defaults
        return a
    }

    func testAFailedMainKeyIsRetriedOnTheBackupAndThenWorks() async {
        let defaults = suite()
        let err = AVGError.provider(.auth, provider: "groq", message: "bad key")
        let provider = ScriptedProvider(queue: [.failure(err), .success(.assistant(text: "worked on the backup"))])
        let a = agent(provider, backup: true, defaults: defaults)
        await a.send("hi").value
        XCTAssertEqual(provider.calls.count, 2)
        XCTAssertEqual(a.lines.last?.text, "worked on the backup")
        XCTAssertTrue(KeyBackup.isActive(.groq, defaults: defaults))
        XCTAssertTrue(a.lines.contains { $0.kind == .activity && $0.text.contains("backup key") })
    }

    func testWithoutABackupTheErrorShowsAsBefore() async {
        let defaults = suite()
        let err = AVGError.provider(.auth, provider: "groq", message: "bad key")
        let provider = ScriptedProvider(queue: [.failure(err)])
        let a = agent(provider, backup: false, defaults: defaults)
        await a.send("hi").value
        XCTAssertEqual(provider.calls.count, 1)
        XCTAssertEqual(a.lines.last?.kind, .error)
        XCTAssertFalse(KeyBackup.isActive(.groq, defaults: defaults))
    }

    func testAFailingBackupIsNotRetriedAgain() async {
        let defaults = suite()
        let err = AVGError.provider(.quota, provider: "groq", message: "no quota")
        let provider = ScriptedProvider { _, _ in .failure(err) }
        let a = agent(provider, backup: true, defaults: defaults)
        await a.send("hi").value
        XCTAssertEqual(provider.calls.count, 2, "main, then the backup once - never a loop")
        XCTAssertEqual(a.lines.last?.kind, .error)
    }

    func testANetworkErrorNeverTouchesKeys() async {
        let defaults = suite()
        let err = AVGError.provider(.network, provider: "groq", message: "offline")
        let provider = ScriptedProvider(queue: [.failure(err)])
        let a = agent(provider, backup: true, defaults: defaults)
        await a.send("hi").value
        XCTAssertEqual(provider.calls.count, 1)
        XCTAssertFalse(KeyBackup.isActive(.groq, defaults: defaults))
    }
}

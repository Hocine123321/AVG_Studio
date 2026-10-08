import XCTest
@testable import AVGStudio

/// Switching to the other assistant when one is rate-limited or out of quota.
/// The dangerous case is a switch AFTER a tool ran: a video could start twice.
@MainActor
final class AgentFailoverTests: XCTestCase {

    private func ok(_ m: AgentMessage) -> Result<AgentMessage, Error> { .success(m) }
    private func limited(_ kind: ErrorKind = .rateLimit, _ who: String = "groq") -> Result<AgentMessage, Error> {
        .failure(AVGError.provider(kind, provider: who, message: "busy"))
    }

    /// An agent whose primary (settings) provider and backup are scripted separately.
    private func agent(primary: ScriptedProvider, backup: ScriptedProvider, host: FakeAgentHost,
                       keys: @escaping (APIKey) -> Bool = { _ in true }) -> Agent {
        let a = makeAgent(primary, host: host)
        a.makeProvider = { kind in kind == nil ? primary : backup }
        a.makeContext = { AgentContext.test($0, keys: keys) }
        return a
    }

    func testRateLimitBeforeAnyToolFailsOverOnce() async {
        let primary = ScriptedProvider(queue: [limited()])
        let backup = ScriptedProvider(queue: [ok(.assistant(text: "Hello from the backup"))])
        let a = agent(primary: primary, backup: backup, host: FakeAgentHost())
        await a.send("hi").value
        XCTAssertEqual(primary.calls.count, 1)
        XCTAssertEqual(backup.calls.count, 1)
        XCTAssertEqual(a.lines.last?.text, "Hello from the backup")
        XCTAssertFalse(a.lines.contains { $0.kind == .error })
        let note = a.lines.first { $0.kind == .activity }?.text ?? ""
        XCTAssertTrue(note.contains("Groq") && note.contains("Google Gemini"), note)
    }

    func testEveryAvailabilityKindFailsOver() async {
        for kind in [ErrorKind.rateLimit, .quota, .billing, .network, .timeout] {
            let backup = ScriptedProvider(queue: [ok(.assistant(text: "ok"))])
            let a = agent(primary: ScriptedProvider(queue: [limited(kind)]), backup: backup, host: FakeAgentHost())
            await a.send("hi").value
            XCTAssertEqual(backup.calls.count, 1, "\(kind)")
        }
    }

    func testFailoverOffShowsTheError() async {
        let host = FakeAgentHost()
        host.settings.agentFailover = false
        let backup = ScriptedProvider(queue: [ok(.assistant(text: "should not happen"))])
        let a = agent(primary: ScriptedProvider(queue: [limited()]), backup: backup, host: host)
        await a.send("hi").value
        XCTAssertTrue(backup.calls.isEmpty)
        XCTAssertEqual(a.lines.last?.kind, .error)
    }

    func testNoKeyForTheOtherProviderShowsTheError() async {
        let backup = ScriptedProvider(queue: [ok(.assistant(text: "should not happen"))])
        let a = agent(primary: ScriptedProvider(queue: [limited()]), backup: backup, host: FakeAgentHost(),
                      keys: { $0 != .gemini })
        await a.send("hi").value
        XCTAssertTrue(backup.calls.isEmpty)
        XCTAssertEqual(a.lines.last?.kind, .error)
    }

    func testFailoverNeverHappensAfterAToolRan() async {
        let host = FakeAgentHost()
        let primary = ScriptedProvider(queue: [ok(.calling("create_video", #"{"topic":"Volcanoes"}"#, id: "c1")), limited()])
        let backup = ScriptedProvider(queue: [ok(.assistant(text: "must not be used"))])
        let a = agent(primary: primary, backup: backup, host: host)
        let turn = a.send("make a video")
        await waitFor("approval") { a.pendingApproval != nil }
        a.approve()
        await turn.value
        XCTAssertEqual(host.created.count, 1, "exactly one video - never two")
        XCTAssertTrue(backup.calls.isEmpty)
        XCTAssertEqual(a.lines.last?.kind, .error)
    }

    func testFailoverNeverHappensAfterAReadOnlyToolEither() async {
        // The history now holds the first service's own tool ids; the other
        // one may not be able to replay them (Gemini 3 signatures).
        let primary = ScriptedProvider(queue: [ok(.calling("list_channels", id: "c1")), limited()])
        let backup = ScriptedProvider(queue: [ok(.assistant(text: "must not be used"))])
        let a = agent(primary: primary, backup: backup, host: FakeAgentHost())
        await a.send("what channels?").value
        XCTAssertTrue(backup.calls.isEmpty)
        XCTAssertEqual(a.lines.last?.kind, .error)
    }

    func testNextMessageStartsOnThePrimaryAgain() async {
        let primary = ScriptedProvider(queue: [limited(), ok(.assistant(text: "primary is back"))])
        let backup = ScriptedProvider(queue: [ok(.assistant(text: "from backup"))])
        let a = agent(primary: primary, backup: backup, host: FakeAgentHost())
        await a.send("one").value
        await a.send("two").value
        XCTAssertEqual(primary.calls.count, 2)
        XCTAssertEqual(backup.calls.count, 1)
        XCTAssertEqual(a.lines.last?.text, "primary is back")
    }

    func testAuthErrorDoesNotFailOver() async {
        let backup = ScriptedProvider(queue: [ok(.assistant(text: "should not happen"))])
        let a = agent(primary: ScriptedProvider(queue: [limited(.auth)]), backup: backup, host: FakeAgentHost())
        await a.send("hi").value
        XCTAssertTrue(backup.calls.isEmpty, "a bad key is the person's to fix, not a reason to switch")
        XCTAssertEqual(a.lines.last?.kind, .error)
    }

    func testOnlyOneFailoverPerMessage() async {
        let primary = ScriptedProvider(queue: [limited(.rateLimit, "groq")])
        let backup = ScriptedProvider(queue: [.failure(AVGError.provider(.quota, provider: "gemini", message: "gemini quota gone"))])
        let host = FakeAgentHost()
        let a = agent(primary: primary, backup: backup, host: host)
        await a.send("hi").value
        XCTAssertEqual(primary.calls.count, 1)
        XCTAssertEqual(backup.calls.count, 1)
        XCTAssertEqual(a.lines.last?.kind, .error)
        XCTAssertTrue(a.lines.last?.title?.contains("Gemini") == true, "the error shown is the backup's, in plain words")
        XCTAssertTrue(host.recorded.contains { $0.contains("gemini quota gone") })
    }

    func testAfterFailoverLaterStepsStayOnTheBackup() async {
        // The backup answers with a tool call; the follow-up step must go to
        // the backup too, not back to the failed service.
        let primary = ScriptedProvider(queue: [limited()])
        let backup = ScriptedProvider(queue: [ok(.calling("list_channels", id: "c1")), ok(.assistant(text: "done"))])
        let a = agent(primary: primary, backup: backup, host: FakeAgentHost())
        await a.send("channels?").value
        XCTAssertEqual(primary.calls.count, 1)
        XCTAssertEqual(backup.calls.count, 2)
        XCTAssertEqual(a.lines.last?.text, "done")
    }

    func testMalformedOutputStillNudgesInsteadOfFailingOver() async {
        let bad = AVGError.provider(.malformedOutput, provider: "groq", message: "tool_use_failed")
        let primary = ScriptedProvider(queue: [.failure(bad), ok(.assistant(text: "fixed"))])
        let backup = ScriptedProvider(queue: [])
        let a = agent(primary: primary, backup: backup, host: FakeAgentHost())
        await a.send("hi").value
        XCTAssertTrue(backup.calls.isEmpty)
        XCTAssertEqual(a.lines.last?.text, "fixed")
    }
}

/// The retry cap the assistant uses so a rate limit fails over quickly.
final class HTTPAttemptCapTests: XCTestCase {

    func testCapOfOneMeansASingleAttempt() async {
        var calls = 0
        do {
            let _: Int = try await HTTP.retrying({ () async throws -> Int in
                calls += 1
                throw AVGError.provider(.rateLimit, provider: "t", message: "429")
            }, provider: "t", attemptCap: 1)
            XCTFail("should have thrown")
        } catch {
            XCTAssertEqual((error as? AVGError)?.kind, .rateLimit)
        }
        XCTAssertEqual(calls, 1)
    }

    func testCapOfTwoMeansOneRetry() async {
        var calls = 0
        do {
            let _: Int = try await HTTP.retrying({ () async throws -> Int in
                calls += 1
                throw AVGError.provider(.rateLimit, provider: "t", message: "429")
            }, provider: "t", attemptCap: 2)
            XCTFail("should have thrown")
        } catch {}
        XCTAssertEqual(calls, 2)   // sleeps the policy's first backoff (2 s) once
    }

    func testCapNeverRaisesTheBudget() async {
        var calls = 0
        do {
            let _: Int = try await HTTP.retrying({ () async throws -> Int in
                calls += 1
                throw AVGError.provider(.auth, provider: "t", message: "401")   // not retryable: 1 attempt
            }, provider: "t", attemptCap: 9)
        } catch {}
        XCTAssertEqual(calls, 1)
    }

    func testNonPositiveCapStillMakesOneAttempt() async {
        var calls = 0
        do {
            let _: Int = try await HTTP.retrying({ () async throws -> Int in
                calls += 1
                throw AVGError.provider(.rateLimit, provider: "t", message: "429")
            }, provider: "t", attemptCap: 0)
        } catch {}
        XCTAssertEqual(calls, 1)
    }
}

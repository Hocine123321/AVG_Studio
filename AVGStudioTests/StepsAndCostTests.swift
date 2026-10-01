import XCTest
@testable import AVGStudio

final class StepsTests: XCTestCase {

    private func project() -> Project { Project(id: "p", title: "T", topic: "t") }

    func testEveryPhaseHasStepsWithUniqueKeys() {
        for phase in PhaseID.allCases {
            let keys = phase.steps.map(\.key)
            XCTAssertFalse(keys.isEmpty, "\(phase) has no steps")
            XCTAssertEqual(keys.count, Set(keys).count, "\(phase) has duplicate step keys")
        }
    }

    func testOldProjectsShowFinishedPhasesAsDone() {
        var p = project()
        p.phases[PhaseID.writing.rawValue] = PhaseRecord(status: .done)
        XCTAssertNil(p.stepRecords)
        XCTAssertEqual(p.displayStatus(.writing, "brief"), .done, "a project saved before steps existed")
        XCTAssertEqual(p.displayStatus(.visuals, "images"), .pending, "its later phases are still pending")
    }

    func testRecordedStepsWinOverTheLegacyFallback() {
        var p = project()
        p.phases[PhaseID.voice.rawValue] = PhaseRecord(status: .running)
        p.stepRecords = ["voice.speech": StepRecord(status: .done)]
        XCTAssertEqual(p.displayStatus(.voice, "speech"), .done)
        XCTAssertEqual(p.displayStatus(.voice, "trim"), .pending)
    }

    func testStepProgressCountsDoneAndSkipped() {
        var p = project()
        p.stepRecords = [
            "writing.research": StepRecord(status: .skipped),
            "writing.brief": StepRecord(status: .done),
            "writing.draft": StepRecord(status: .running),
        ]
        let progress = p.stepProgress(.writing)
        XCTAssertEqual(progress.finished, 2)
        XCTAssertEqual(progress.total, 4)
    }

    func testElapsedPrefersExplicitSeconds() {
        let start = Date(timeIntervalSince1970: 100)
        XCTAssertEqual(StepRecord(status: .done, started: start, finished: start.addingTimeInterval(5)).elapsed, 5)
        XCTAssertEqual(StepRecord(status: .done, started: start, finished: start.addingTimeInterval(5), seconds: 9).elapsed, 9)
        XCTAssertNil(StepRecord(status: .running, started: start).elapsed)
    }

    func testProjectWithoutStepRecordsStillRoundTrips() throws {
        let p = project()
        let back = try JSONDecoder().decode(Project.self, from: JSONEncoder().encode(p))
        XCTAssertNil(back.stepRecords)
        var withSteps = p
        withSteps.stepRecords = ["render.mux": StepRecord(status: .done, seconds: 1.5, detail: "ok")]
        let again = try JSONDecoder().decode(Project.self, from: JSONEncoder().encode(withSteps))
        XCTAssertEqual(again.step(.render, "mux").detail, "ok")
        XCTAssertEqual(again.step(.render, "mux").seconds, 1.5)
    }
}

final class CostTableTests: XCTestCase {

    private func settings(text: TextProviderKind, image: ImageProviderKind, voice: VoiceProviderKind) -> AppSettings {
        var s = AppSettings()
        s.text = text; s.image = image; s.voice = voice
        return s
    }

    func testFreeProvidersEstimateAsFree() {
        let e = CostTable.estimate(targetSeconds: 60, settings: settings(text: .apple, image: .pollinations, voice: .apple),
                                   channel: Channel(name: "c"), hasTranscriptionKey: false, poolSize: 0)
        XCTAssertEqual(e.total, 0)
        XCTAssertEqual(e.label, "Free")
    }

    func testPaidImagesScaleWithSceneCount() {
        let e = CostTable.estimate(targetSeconds: 60, settings: settings(text: .apple, image: .gemini, voice: .apple),
                                   channel: Channel(name: "c"), hasTranscriptionKey: false, poolSize: 0)
        // 60 seconds is about 9 scenes at $0.02 each.
        XCTAssertEqual(e.lines.first { $0.phase == "Images" }?.usd ?? -1, 0.18, accuracy: 1e-9)
    }

    func testLongerVideosCostMore() {
        let s = settings(text: .groq, image: .gemini, voice: .elevenlabs)
        let short = CostTable.estimate(targetSeconds: 30, settings: s, channel: Channel(name: "c"), hasTranscriptionKey: true, poolSize: 0)
        let long = CostTable.estimate(targetSeconds: 120, settings: s, channel: Channel(name: "c"), hasTranscriptionKey: true, poolSize: 0)
        XCTAssertGreaterThan(long.total, short.total)
    }

    func testRecyclingPoolImagesAreFree() {
        var channel = Channel(name: "c")
        channel.recycleEnabled = true
        channel.recycleRatio = 100
        let s = settings(text: .apple, image: .openai, voice: .apple)
        let withPool = CostTable.estimate(targetSeconds: 60, settings: s, channel: channel, hasTranscriptionKey: false, poolSize: 5)
        XCTAssertEqual(withPool.lines.first { $0.phase == "Images" }?.usd ?? -1, 0, accuracy: 1e-9)
        let noPool = CostTable.estimate(targetSeconds: 60, settings: s, channel: channel, hasTranscriptionKey: false, poolSize: 0)
        XCTAssertGreaterThan(noPool.total, 0)
    }

    func testTranscriptionOnlyWhenAKeyExists() {
        let s = settings(text: .apple, image: .pollinations, voice: .apple)
        let with = CostTable.estimate(targetSeconds: 60, settings: s, channel: Channel(name: "c"), hasTranscriptionKey: true, poolSize: 0)
        XCTAssertEqual(with.lines.first { $0.phase == "Captions" }?.usd ?? -1, 0.01, accuracy: 1e-9)
    }

    func testLabelsAreHonestAboutPrecision() {
        XCTAssertEqual(CostTable.Estimate.format(0), "Free")
        XCTAssertEqual(CostTable.Estimate.format(0.004), "under $0.01")
        XCTAssertEqual(CostTable.Estimate.format(0.184), "about $0.18")
    }
}

final class RenderLeaseTests: XCTestCase {
    private actor Order {
        var items: [String] = []
        func add(_ s: String) { items.append(s) }
    }

    func testSecondRenderWaitsForTheFirst() async throws {
        let lease = RenderLease()
        try await lease.acquire()
        let order = Order()
        let second = Task {
            try await lease.acquire()
            await order.add("second rendered")
            await lease.release()
        }
        try await Task.sleep(for: .milliseconds(150))
        let waiting = await lease.waitingCount
        XCTAssertEqual(waiting, 1)
        await order.add("first finished")
        await lease.release()
        try await second.value
        let items = await order.items
        XCTAssertEqual(items, ["first finished", "second rendered"])
        let held = await lease.isHeld
        XCTAssertFalse(held)
    }

    func testACancelledWaiterLeavesTheLine() async throws {
        let lease = RenderLease()
        try await lease.acquire()
        let waiter = Task { try await lease.acquire() }
        try await Task.sleep(for: .milliseconds(150))
        waiter.cancel()
        do {
            try await waiter.value
            XCTFail("a cancelled waiter must throw")
        } catch is CancellationError {
            // expected
        }
        let waiting = await lease.waitingCount
        XCTAssertEqual(waiting, 0)
        await lease.release()
        let held = await lease.isHeld
        XCTAssertFalse(held)
    }

    func testWaitersAreServedInOrder() async throws {
        let lease = RenderLease()
        try await lease.acquire()
        let order = Order()
        let a = Task { try await lease.acquire(); await order.add("a"); await lease.release() }
        try await Task.sleep(for: .milliseconds(80))
        let b = Task { try await lease.acquire(); await order.add("b"); await lease.release() }
        try await Task.sleep(for: .milliseconds(80))
        await lease.release()
        try await a.value
        try await b.value
        let items = await order.items
        XCTAssertEqual(items, ["a", "b"])
    }
}

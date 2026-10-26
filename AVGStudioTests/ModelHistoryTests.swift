import XCTest
@testable import AVGStudio

final class ModelHistoryTests: XCTestCase {
    private func at(_ s: TimeInterval) -> Date { Date(timeIntervalSince1970: s) }

    func testNewestFirstWithoutDuplicates() {
        var h = ModelHistory()
        h.record(kind: "writer", provider: "gemini", model: "a", at: at(1))
        h.record(kind: "writer", provider: "gemini", model: "b", at: at(2))
        h.record(kind: "writer", provider: "gemini", model: "a", at: at(3))
        XCTAssertEqual(h.recent(kind: "writer").map(\.model), ["a", "b"])
        XCTAssertEqual(h.recent(kind: "writer").first?.at, at(3), "a repeat moves to the front with its new date")
    }

    func testTheSameModelNameOnTwoProvidersIsTwoEntries() {
        var h = ModelHistory()
        h.record(kind: "writer", provider: "groq", model: "m", at: at(1))
        h.record(kind: "writer", provider: "pollinations", model: "m", at: at(2))
        XCTAssertEqual(h.recent(kind: "writer").map(\.provider), ["pollinations", "groq"])
    }

    func testKindsAreKeptApart() {
        var h = ModelHistory()
        h.record(kind: "writer", provider: "gemini", model: "w", at: at(1))
        h.record(kind: "image", provider: "gemini", model: "i", at: at(2))
        XCTAssertEqual(h.recent(kind: "writer").map(\.model), ["w"])
        XCTAssertEqual(h.recent(kind: "image").map(\.model), ["i"])
        XCTAssertTrue(h.recent(kind: "voice").isEmpty)
    }

    func testTheCapDropsTheOldestNeverTheNewest() {
        var h = ModelHistory()
        for i in 0..<(ModelHistory.keep + 8) {
            h.record(kind: "writer", provider: "gemini", model: "m\(i)", at: at(TimeInterval(i)))
        }
        let list = h.recent(kind: "writer")
        XCTAssertEqual(list.count, ModelHistory.keep)
        XCTAssertEqual(list.first?.model, "m\(ModelHistory.keep + 7)")
        XCTAssertFalse(list.contains { $0.model == "m0" })
    }

    func testAnEmptyModelNameIsIgnored() {
        var h = ModelHistory()
        h.record(kind: "writer", provider: "gemini", model: "")
        XCTAssertTrue(h.recent(kind: "writer").isEmpty)
    }

    func testRoundTripsThroughJSON() throws {
        var h = ModelHistory()
        h.record(kind: "voice", provider: "elevenlabs", model: "eleven_flash_v2_5", at: at(5))
        let back = try JSONDecoder().decode(ModelHistory.self, from: JSONEncoder().encode(h))
        XCTAssertEqual(back, h)
    }
}

final class FallbackPlannerTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_000_000_000)
    private func use(_ provider: String, _ model: String, daysAgo: Double = 1) -> ModelUse {
        ModelUse(provider: provider, model: model, at: now.addingTimeInterval(-daysAgo * 86_400))
    }
    private func pick(user: String = "", provider: String = "gemini", current: String = "big", preset: String? = "light",
                      history: [ModelUse] = [], excluded: Set<String> = []) -> String? {
        FallbackPlanner.pick(userChoice: user, provider: provider, current: current, preset: preset,
                             history: history, excluded: excluded, now: now)
    }

    func testThePersonsOwnChoiceAlwaysWins() {
        XCTAssertEqual(pick(user: "mine", history: [use("gemini", "recent")]), "mine")
    }

    func testTheNewestWorkingModelOfTheSameProviderBeatsThePreset() {
        XCTAssertEqual(pick(history: [use("gemini", "recent"), use("gemini", "older")]), "recent")
    }

    func testTheModelThatIsFailingNowIsSkipped() {
        XCTAssertEqual(pick(current: "recent", history: [use("gemini", "recent"), use("gemini", "older")]), "older")
    }

    func testExcludedModelsAreSkipped() {
        XCTAssertEqual(pick(history: [use("gemini", "bad"), use("gemini", "ok")], excluded: ["bad"]), "ok")
    }

    func testAnotherProvidersModelIsNeverOffered() {
        XCTAssertEqual(pick(provider: "groq", history: [use("gemini", "gemini-x")]), "light")
    }

    func testWithNothingUsableThePresetApplies() {
        XCTAssertEqual(pick(history: []), "light")
        XCTAssertNil(pick(preset: nil, history: []), "nothing lighter and nothing proven: no fallback")
    }

    func testAModelNotSeenForAMonthIsNotTrusted() {
        let stale = use("gemini", "old", daysAgo: FallbackPlanner.maxAge / 86_400 + 1)
        XCTAssertEqual(pick(history: [stale]), "light")
        let fresh = use("gemini", "edge", daysAgo: FallbackPlanner.maxAge / 86_400 - 1)
        XCTAssertEqual(pick(history: [fresh]), "edge")
    }
}

@MainActor
final class RecoveryFallbackHistoryTests: XCTestCase {
    private var dir: URL!

    override func setUp() {
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("fallback-\(UUID().uuidString)", isDirectory: true)
        Recovery.noteSuccess()   // clears the "already tried" memory between tests
    }
    override func tearDown() {
        try? FileManager.default.removeItem(at: dir)
        Recovery.noteSuccess()
    }

    private func geminiWriter(_ model: String = "gemini-big") -> AppSettings {
        var s = AppSettings()
        s.text = .gemini
        s.geminiModel = model
        return s
    }

    func testTheMostRecentWorkingModelIsTheFallbackWhenNoneIsChosen() {
        let prefs = ModelPrefs(directory: dir)
        prefs.recordSuccess(kind: "writer", provider: "gemini", model: "gemini-recent")
        XCTAssertEqual(Recovery.fallbackModel(.writer, settings: geminiWriter(), prefs: prefs), "gemini-recent")
    }

    func testWithoutHistoryTheBuiltInPresetStillApplies() {
        let prefs = ModelPrefs(directory: dir)
        XCTAssertEqual(Recovery.fallbackModel(.writer, settings: geminiWriter(), prefs: prefs), "gemini-2.5-flash-lite")
        XCTAssertEqual(Recovery.fallbackModel(.writer, settings: geminiWriter()), "gemini-2.5-flash-lite",
                       "callers that pass no prefs behave exactly as before")
    }

    func testAChosenFallbackBeatsHistory() {
        let prefs = ModelPrefs(directory: dir)
        prefs.recordSuccess(kind: "writer", provider: "gemini", model: "gemini-recent")
        var s = geminiWriter()
        s.writerFallbackModel = "my-pick"
        XCTAssertEqual(Recovery.fallbackModel(.writer, settings: s, prefs: prefs), "my-pick")
    }

    func testAnotherProvidersHistoryIsIgnored() {
        let prefs = ModelPrefs(directory: dir)
        prefs.recordSuccess(kind: "writer", provider: "gemini", model: "gemini-recent")
        var s = AppSettings()
        s.text = .groq
        XCTAssertEqual(Recovery.fallbackModel(.writer, settings: s, prefs: prefs), "llama-3.1-8b-instant")
    }

    func testTheModelAlreadyRunningIsNotOfferedAsItsOwnFallback() {
        let prefs = ModelPrefs(directory: dir)
        prefs.recordSuccess(kind: "writer", provider: "gemini", model: "gemini-big")
        XCTAssertEqual(Recovery.fallbackModel(.writer, settings: geminiWriter("gemini-big"), prefs: prefs), "gemini-2.5-flash-lite")
    }

    func testOnDeviceWritersHaveNoFallbackUnlessChosen() {
        var s = AppSettings()
        s.text = .apple
        XCTAssertNil(Recovery.fallbackModel(.writer, settings: s, prefs: ModelPrefs(directory: dir)))
        s.writerFallbackModel = "mine"
        XCTAssertEqual(Recovery.fallbackModel(.writer, settings: s, prefs: ModelPrefs(directory: dir)), "mine")
    }

    func testHistorySurvivesARelaunchAndIsPerKind() {
        let first = ModelPrefs(directory: dir)
        first.recordSuccess(kind: "image", provider: "gemini", model: "gemini-image-x")
        let second = ModelPrefs(directory: dir)
        XCTAssertEqual(second.recentUses(kind: "image").map(\.model), ["gemini-image-x"])
        XCTAssertTrue(second.recentUses(kind: "writer").isEmpty)
    }

    func testRecordingTheSameNewestModelAgainDoesNotRewriteTheFile() throws {
        let prefs = ModelPrefs(directory: dir)
        prefs.recordSuccess(kind: "writer", provider: "gemini", model: "m")
        let file = dir.appendingPathComponent("model_history.json")
        let first = try Data(contentsOf: file)
        Thread.sleep(forTimeInterval: 0.01)
        prefs.recordSuccess(kind: "writer", provider: "gemini", model: "m")
        XCTAssertEqual(try Data(contentsOf: file), first)
    }

    func testSlotNamesTheProviderAndTheModelInUse() {
        let sl = Recovery.slot(.writer, settings: geminiWriter("gemini-big"))
        XCTAssertEqual(sl?.provider, "gemini")
        XCTAssertEqual(sl?.current, "gemini-big")
        var apple = AppSettings()
        apple.text = .apple
        XCTAssertNil(Recovery.slot(.writer, settings: apple))
    }
}

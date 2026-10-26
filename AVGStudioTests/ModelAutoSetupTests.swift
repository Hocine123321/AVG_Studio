import XCTest
@testable import AVGStudio

final class ModelRankingTests: XCTestCase {
    func testGeminiTextPrefersTheNewestStableFlash() {
        let ids = ["gemini-2.5-flash", "gemini-3.8-flash", "gemini-3.8-flash-lite", "gemini-2.5-pro",
                   "gemini-flash-latest", "gemini-3.8-flash-image", "gemini-2.5-flash-preview-tts",
                   "gemini-embedding-001", "gemini-3.10-flash"]
        XCTAssertEqual(ModelRanking.geminiText(ids), ["gemini-3.10-flash", "gemini-3.8-flash", "gemini-2.5-flash"],
                       "3.10 is newer than 3.8 (numbers, not text); lite, pro, image, tts, aliases and embeddings never qualify")
    }

    func testAStableModelBeatsANewerPreview() {
        let ids = ["gemini-3.9-flash-preview", "gemini-3.8-flash", "gemini-4.0-flash-exp"]
        XCTAssertEqual(ModelRanking.geminiText(ids), ["gemini-3.8-flash", "gemini-4.0-flash-exp", "gemini-3.9-flash-preview"],
                       "stable first, then previews by version")
    }

    func testNothingQualifyingGivesAnEmptyList() {
        XCTAssertTrue(ModelRanking.geminiText(["gemini-2.5-pro", "learnlm-x", "gemini-flash-latest"]).isEmpty)
        XCTAssertTrue(ModelRanking.geminiText([]).isEmpty)
    }

    func testImageAndVoiceRankingsAreSeparateAndFlashOnly() {
        let ids = ["gemini-2.5-flash-image", "gemini-3.1-flash-image-preview", "gemini-3-pro-image-preview",
                   "gemini-2.5-flash-preview-tts", "gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-3.8-flash"]
        XCTAssertEqual(ModelRanking.geminiImage(ids), ["gemini-2.5-flash-image", "gemini-3.1-flash-image-preview"])
        XCTAssertEqual(ModelRanking.geminiVoice(ids), ["gemini-3.8-flash-tts", "gemini-2.5-flash-preview-tts"])
    }

    func testGroqRanksBySizeAndSkipsNonChatModels() {
        let ids = ["llama-3.1-8b-instant", "llama-3.3-70b-versatile", "openai/gpt-oss-120b",
                   "whisper-large-v3", "meta-llama/llama-guard-4-12b", "playai-tts", "mystery-model"]
        XCTAssertEqual(ModelRanking.groqText(ids),
                       ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mystery-model"])
    }

    func testBillionsReadsTheSizeToken() {
        XCTAssertEqual(ModelRanking.billions("llama-3.3-70b-versatile"), 70)
        XCTAssertEqual(ModelRanking.billions("meta-llama/llama-4-scout-17b-16e-instruct"), 17)
        XCTAssertEqual(ModelRanking.billions("qwen/qwen3-32b"), 32)
        XCTAssertEqual(ModelRanking.billions("no-size-here"), 0)
    }
}

@MainActor
final class ModelAutoSetupDecisionTests: XCTestCase {
    private func replace(current: String = "default", pick: String = "best", factoryDefault: String = "default",
                         autoPicked: String? = nil, listed: Bool = true, works: Bool? = true, upgrade: Bool = true) -> Bool {
        ModelAutoSetup.shouldReplace(current: current, pick: pick, factoryDefault: factoryDefault, autoPicked: autoPicked,
                                     currentListed: listed, currentWorks: works, upgrade: upgrade)
    }

    func testTheFactoryDefaultUpgradesWhenChasingTheBest() {
        XCTAssertTrue(replace())
    }

    func testAModelThePersonChoseIsNeverReplaced() {
        XCTAssertFalse(replace(current: "mine"))
        XCTAssertFalse(replace(current: "mine", listed: false, works: false), "not even when it is broken: that is the recovery flow's job")
    }

    func testAnEarlierAutomaticPickMayBeReplacedAgain() {
        XCTAssertTrue(replace(current: "picked-last-time", autoPicked: "picked-last-time"))
    }

    func testNothingChangesWhenThePickIsAlreadyInUse() {
        XCTAssertFalse(replace(current: "best"))
    }

    func testAWorkingModelStaysWhenTheRuleIsNotToChaseTheBest() {
        XCTAssertFalse(replace(upgrade: false))
        XCTAssertFalse(replace(works: nil, upgrade: false), "unknown is not broken")
    }

    func testARetiredOrFailingModelIsReplacedEvenWithoutChasing() {
        XCTAssertTrue(replace(listed: false, works: nil, upgrade: false))
        XCTAssertTrue(replace(works: false, upgrade: false))
    }

    func testAutoPicksPersistAcrossRelaunch() {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("autopick-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let first = ModelPrefs(directory: dir)
        XCTAssertNil(first.autoPick("gemini_text"))
        first.setAutoPick("gemini-3.8-flash", for: "gemini_text")
        XCTAssertEqual(ModelPrefs(directory: dir).autoPick("gemini_text"), "gemini-3.8-flash")
        XCTAssertNil(ModelPrefs(directory: dir).autoPick("groq_text"))
    }

    func testSetupNotesAreCapped() {
        let prefs = ModelPrefs(directory: FileManager.default.temporaryDirectory.appendingPathComponent("notes-\(UUID().uuidString)"))
        for i in 0..<30 { prefs.addSetupNote("line \(i)") }
        XCTAssertEqual(prefs.setupNotes.count, 20)
        XCTAssertEqual(prefs.setupNotes.last, "line 29")
    }
}

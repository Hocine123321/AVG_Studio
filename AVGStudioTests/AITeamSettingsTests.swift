import XCTest
@testable import AVGStudio

// S3 - Settings -> AI team: the preset picker's rules, the automatic-pick
// switch, and undoing what the automatic setup chose.

final class AITeamSettingsTests: XCTestCase {
    func testAutomaticPicksAreOnByDefaultAndAnOlderFileGetsThat() throws {
        XCTAssertTrue(AppSettings().autoPickModels)
        let old = try JSONDecoder().decode(AppSettings.self, from: Data("{}".utf8))
        XCTAssertTrue(old.autoPickModels)
    }

    func testTheSwitchSurvivesARoundTrip() throws {
        var s = AppSettings()
        s.autoPickModels = false
        let back = try JSONDecoder().decode(AppSettings.self, from: JSONEncoder().encode(s))
        XCTAssertFalse(back.autoPickModels)
    }

    func testTheCurrentTeamIsRecognisedAsAPreset() {
        XCTAssertEqual(AITeamPreset.matching(text: .apple, image: .pollinations, voice: .apple,
                                             keys: [], appleAvailable: true), .free)
        XCTAssertEqual(AITeamPreset.matching(text: .gemini, image: .pollinations, voice: .apple,
                                             keys: [.gemini], appleAvailable: true), .balanced)
        XCTAssertEqual(AITeamPreset.matching(text: .gemini, image: .gemini, voice: .gemini,
                                             keys: [.gemini], appleAvailable: true), .premium)
    }

    func testAMixedTeamMatchesNoPreset() {
        XCTAssertNil(AITeamPreset.matching(text: .apple, image: .gemini, voice: .apple,
                                           keys: [.gemini], appleAvailable: true))
        XCTAssertNil(AITeamPreset.matching(text: .groq, image: .pollinations, voice: .apple,
                                           keys: [.groq], appleAvailable: true))
    }

    func testApplyingATeamSetsTheWriterImagesAndVoice() {
        var s = AppSettings()
        let team = AITeamPreset.assignment(.premium, keys: [.gemini, .elevenlabs], appleAvailable: true)
        team.apply(to: &s)
        XCTAssertEqual(s.text, .gemini)
        XCTAssertEqual(s.image, .gemini)
        XCTAssertEqual(s.voice, .elevenlabs)
    }

    func testTheAITeamPageIsSearchable() {
        let team = SettingsIndex.search("ai team").first
        XCTAssertEqual(team?.title, "AI team")
        guard case .aiTeam(let focus)? = team?.route else { return XCTFail("AI team has its own page") }
        XCTAssertEqual(focus, .aiTeam)

        let auto = SettingsIndex.search("undo").first { $0.title == "Pick the best model automatically" }
        guard case .aiTeam(let autoFocus)? = auto?.route else { return XCTFail("the automatic picks live on the AI team page") }
        XCTAssertEqual(autoFocus, .autoPickModels)
        XCTAssertEqual(auto?.anchor, .autoPickModels)
    }
}

@MainActor
final class UndoAutoPicksTests: XCTestCase {
    private var store: Store!
    private var saved: AppSettings!

    override func setUp() {
        store = Store()
        saved = store.settings
    }

    override func tearDown() {
        for entry in ModelAutoSetup.pickable { store.modelPrefs.clearAutoPick(entry.catalog) }
        store.settings = saved
    }

    func testAnAutomaticPickGoesBackToTheFactoryDefault() {
        let factory = AppSettings().geminiModel
        store.settings.geminiModel = "gemini-test-pick"
        store.modelPrefs.setAutoPick("gemini-test-pick", for: "gemini_text")

        let notes = ModelAutoSetup.undoAutoPicks(store: store)

        XCTAssertEqual(store.settings.geminiModel, factory)
        XCTAssertEqual(notes.count, 1)
        XCTAssertTrue(notes[0].hasPrefix("Gemini writer"))
        XCTAssertNil(store.modelPrefs.autoPick("gemini_text"), "the record is cleared")
        XCTAssertTrue(store.modelPrefs.setupNotes.contains(notes[0]), "and the change is listed on the page")
    }

    func testAModelThePersonChoseSinceIsLeftAlone() {
        store.settings.groqModel = "my-own-choice"
        store.modelPrefs.setAutoPick("an-older-automatic-pick", for: "groq_text")

        let notes = ModelAutoSetup.undoAutoPicks(store: store)

        XCTAssertEqual(store.settings.groqModel, "my-own-choice")
        XCTAssertTrue(notes.isEmpty)
        XCTAssertNil(store.modelPrefs.autoPick("groq_text"), "the stale record is cleared all the same")
    }

    func testNothingRecordedMeansNothingChanges() {
        let before = store.settings.geminiModel
        XCTAssertTrue(ModelAutoSetup.undoAutoPicks(store: store).isEmpty)
        XCTAssertEqual(store.settings.geminiModel, before)
    }

    func testEveryPickableModelPointsAtARealSetting() {
        let defaults = AppSettings()
        XCTAssertEqual(ModelAutoSetup.pickable.count, 4)
        for entry in ModelAutoSetup.pickable {
            XCTAssertFalse(defaults[keyPath: entry.path].isEmpty, entry.label)
        }
    }
}

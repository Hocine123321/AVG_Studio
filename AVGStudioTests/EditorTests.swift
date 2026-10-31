import XCTest
@testable import AVGStudio

final class EditGatePolicyTests: XCTestCase {
    private func outcome(_ mode: EditingMode, takes: Bool = false, answered: Bool = false, active: Bool = true) -> EditGatePolicy.Outcome {
        EditGatePolicy.outcome(mode: mode, hasTakes: takes, alreadyAnswered: answered, appActive: active)
    }

    func testAskShowsTheCardOnAFirstRender() {
        XCTAssertEqual(outcome(.ask), .ask)
    }

    func testAutoNeverAsksAndManualAlwaysHolds() {
        XCTAssertEqual(outcome(.auto), .goOn)
        XCTAssertEqual(outcome(.manual), .hold)
    }

    func testAVideoThatAlreadyHasATakeIsNeverHeld() {
        for mode in EditingMode.allCases { XCTAssertEqual(outcome(mode, takes: true), .goOn, "\(mode)") }
    }

    func testAVideoAlreadyAnsweredThisSessionIsNotAskedAgain() {
        for mode in EditingMode.allCases { XCTAssertEqual(outcome(mode, answered: true), .goOn, "\(mode)") }
    }

    func testTheCardNeverWaitsWhileTheAppIsInTheBackground() {
        XCTAssertEqual(outcome(.ask, active: false), .goOn)
        XCTAssertEqual(outcome(.manual, active: false), .hold, "manual holds the render, which needs no one watching")
    }

    func testTheCountdownIsTenSeconds() {
        XCTAssertEqual(EditGate.seconds, 10)
    }
}

final class EditingModelsDecodingTests: XCTestCase {
    func testAnOlderChannelAsksBeforeTheFirstRender() throws {
        let channel = try JSONDecoder().decode(Channel.self, from: Data(#"{"name":"Old"}"#.utf8))
        XCTAssertEqual(channel.editingMode, .ask)
    }

    func testTheEditingModeSurvivesARoundTrip() throws {
        var channel = Channel(name: "Shorts")
        channel.editingMode = .manual
        let back = try JSONDecoder().decode(Channel.self, from: JSONEncoder().encode(channel))
        XCTAssertEqual(back.editingMode, .manual)
    }

    func testPerVideoEditsSurviveARoundTripInEveryShape() throws {
        for music in [MusicOverride.silent, .auto, .track("abc")] {
            var project = Project(id: "p", title: "T", topic: "t")
            project.edits = ProjectEdits(music: music, sfxEnabled: false)
            project.awaitingEdit = true
            let back = try JSONDecoder().decode(Project.self, from: JSONEncoder().encode(project))
            XCTAssertEqual(back.edits, ProjectEdits(music: music, sfxEnabled: false))
            XCTAssertEqual(back.awaitingEdit, true)
        }
    }

    func testAnEditsValueThatSaysNothingIsEmpty() {
        XCTAssertTrue(ProjectEdits().isEmpty)
        XCTAssertFalse(ProjectEdits(music: .silent).isEmpty)
        XCTAssertFalse(ProjectEdits(sfxEnabled: true).isEmpty)
    }

    func testAnOlderProjectHasNoEditsAndIsNotWaiting() throws {
        let project = Project(id: "p", title: "T", topic: "t")
        let back = try JSONDecoder().decode(Project.self, from: JSONEncoder().encode(project))
        XCTAssertNil(back.edits)
        XCTAssertNil(back.awaitingEdit)
    }
}

@MainActor
final class EditGateStoreTests: XCTestCase {
    func testAnswering_WithNoCardOnScreen_DoesNothing() {
        let store = Store()
        store.resolveEditGate(.editMyself)
        store.resolveEditGate(.letAIEdit, gateID: UUID())
        XCTAssertNil(store.editGate)
    }
}

final class CaptionEditTests: XCTestCase {
    private func cue(_ words: [(String, Double, Double)], text: String? = nil) -> CaptionCue {
        let w = words.map { WordTiming(text: $0.0, start: $0.1, end: $0.2) }
        return CaptionCue(start: w.first?.start ?? 0, end: w.last?.end ?? 0,
                          text: text ?? w.map { $0.text }.joined(separator: " "), words: w)
    }

    func testRetypingWithTheSameWordCountKeepsEveryTiming() {
        let c = cue([("Hello", 0, 0.5), ("wrld", 0.5, 1)])
        let out = CaptionEdit.setText(c, to: "Hello world")
        XCTAssertEqual(out.text, "Hello world")
        XCTAssertEqual(out.words.map { $0.text }, ["Hello", "world"])
        XCTAssertEqual(out.words.map { $0.start }, [0, 0.5])
        XCTAssertEqual(out.words.map { $0.end }, [0.5, 1])
    }

    func testADifferentWordCountSpreadsTheNewWordsOverTheLine() {
        let c = cue([("a", 0, 1)])
        let out = CaptionEdit.setText(c, to: "one two three")
        XCTAssertEqual(out.words.map { $0.text }, ["one", "two", "three"])
        XCTAssertEqual(out.words.first?.start ?? -1, 0, accuracy: 1e-9)
        XCTAssertEqual(out.words.last?.end ?? -1, 1, accuracy: 1e-9)
        XCTAssertEqual(out.words[0].end, out.words[1].start, accuracy: 1e-9)
        XCTAssertGreaterThan(out.words[2].end - out.words[2].start, out.words[0].end - out.words[0].start, "a longer word gets more time")
    }

    func testBlankTextNeverEmptiesACaption() {
        let c = cue([("keep", 0, 1)])
        XCTAssertEqual(CaptionEdit.setText(c, to: "   "), c)
    }

    func testACueWithoutWordTimingsChangesItsTextOnly() {
        let plain = CaptionCue(start: 0, end: 2, text: "old line", words: [])
        let out = CaptionEdit.setText(plain, to: "new line here")
        XCTAssertEqual(out.text, "new line here")
        XCTAssertTrue(out.words.isEmpty)
    }

    func testReplaceKeepsPunctuationAndCaseInsensitivelyMatchesWholeWords() {
        let c = cue([("Hello,", 0, 0.5), ("Wrld.", 0.5, 1), ("worlds", 1, 1.5)])
        let result = CaptionEdit.replace("wrld", with: "world", in: [c])
        XCTAssertEqual(result.count, 1)
        XCTAssertEqual(result.cues[0].words.map { $0.text }, ["Hello,", "world.", "worlds"])
        XCTAssertEqual(result.cues[0].text, "Hello, world. worlds")
        XCTAssertEqual(result.cues[0].words.map { $0.start }, [0, 0.5, 1], "timings are untouched")
    }

    func testReplaceCountsAcrossCuesAndHandlesTextOnlyCues() {
        let a = cue([("Teh", 0, 1)])
        let b = CaptionCue(start: 1, end: 2, text: "say teh word", words: [])
        let result = CaptionEdit.replace("teh", with: "the", in: [a, b])
        XCTAssertEqual(result.count, 2)
        XCTAssertEqual(result.cues[1].text, "say the word")
    }

    func testReplaceWithNothingToFindOrNothingToPutDoesNothing() {
        let c = cue([("hi", 0, 1)])
        XCTAssertEqual(CaptionEdit.replace("", with: "x", in: [c]).count, 0)
        XCTAssertEqual(CaptionEdit.replace("hi", with: "  ", in: [c]).count, 0)
        XCTAssertEqual(CaptionEdit.replace("absent", with: "x", in: [c]).cues, [c])
    }

    func testPartsSplitsTheLettersFromTheirPunctuation() {
        let p = CaptionEdit.parts("“Hello,”")
        XCTAssertEqual(p.lead, "“")
        XCTAssertEqual(p.core, "Hello")
        XCTAssertEqual(p.trail, ",”")
        XCTAssertEqual(CaptionEdit.parts("don't").core, "don't")
        XCTAssertEqual(CaptionEdit.parts("...").core, "")
    }
}

@MainActor
final class TabNavigationTests: XCTestCase {
    func testOpeningAVideoFromTheLibraryPushesStudioOntoTheLibraryTab() {
        let store = Store()
        store.tab = .create
        store.selectForStudio("abc")
        XCTAssertEqual(store.currentProjectID, "abc")
        XCTAssertEqual(store.tab, .library)
        XCTAssertEqual(store.libraryPath, [.studio], "one Studio screen, never a stack of them")
        store.selectForStudio("def")
        XCTAssertEqual(store.libraryPath, [.studio])
        XCTAssertEqual(store.currentProjectID, "def")
    }

    func testOpeningTheEditorSwitchesToItsTabOnThatVideo() {
        let store = Store()
        store.openEditor("abc")
        XCTAssertEqual(store.tab, .editor)
        XCTAssertEqual(store.currentProjectID, "abc")
    }
}

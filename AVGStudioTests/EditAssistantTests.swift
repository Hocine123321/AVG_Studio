import XCTest
@testable import AVGStudio

@MainActor
final class EditAssistantTests: XCTestCase {
    private var store: Store!
    private var projectID = ""
    private var assistant: EditAssistant!

    override func setUp() {
        store = Store()
        projectID = "edit-assistant-\(UUID().uuidString)"
        var p = Project(id: projectID, title: "Test video", topic: "t")
        p.sceneDurations = [5, 5, 5]
        p.captions = [
            CaptionCue(start: 0, end: 2, text: "Hello wrld",
                       words: [WordTiming(text: "Hello", start: 0, end: 1), WordTiming(text: "wrld", start: 1, end: 2)]),
            CaptionCue(start: 2, end: 4, text: "second line", words: []),
            CaptionCue(start: 4, end: 6, text: "wrld again",
                       words: [WordTiming(text: "wrld", start: 4, end: 5), WordTiming(text: "again", start: 5, end: 6)]),
        ]
        store.projects.append(p)
        assistant = EditAssistant()
        assistant.attach(store, projectID: projectID)
    }

    override func tearDown() {
        store.deleteProject(projectID)
    }

    private var project: Project { store.project(projectID)! }

    private func run(_ name: String, _ json: String = "{}") -> String {
        assistant.run(toolCall(name, json))
    }

    // MARK: Reading

    func testGetEditDescribesTheVideo() {
        let text = run("get_edit")
        XCTAssertTrue(text.contains("Test video"))
        XCTAssertTrue(text.contains("15 s, 3 scenes"))
        XCTAssertTrue(text.contains("Title cards: none"))
        XCTAssertTrue(text.contains("Captions: 3 lines"))
    }

    func testListCaptionsNumbersTheLinesAndPages() {
        let all = run("list_captions")
        XCTAssertTrue(all.hasPrefix("1 [0:00] Hello wrld"))
        XCTAssertTrue(all.contains("3 [0:04] wrld again"))
        XCTAssertTrue(run("list_captions", #"{"from_line":3}"#).hasPrefix("3 [0:04]"))
        XCTAssertTrue(run("list_captions", #"{"from_line":"2"}"#).hasPrefix("2 [0:02]"), "a number sent as a string is accepted")
    }

    // MARK: Captions

    func testFixCaptionWordReplacesEverywhereAndKeepsTimings() {
        XCTAssertEqual(run("fix_caption_word", #"{"find":"wrld","replace":"world"}"#), "Replaced 2 times.")
        XCTAssertEqual(project.captions.map { $0.text }, ["Hello world", "second line", "world again"])
        XCTAssertEqual(project.captions[0].words.map { $0.start }, [0, 1])
    }

    func testFixCaptionWordSaysSoWhenNothingMatches() {
        XCTAssertTrue(run("fix_caption_word", #"{"find":"absent","replace":"x"}"#).contains("No whole word"))
        XCTAssertTrue(run("fix_caption_word", #"{"find":"wrld"}"#).contains("Give both"))
        XCTAssertEqual(project.captions[0].text, "Hello wrld", "nothing changed")
    }

    func testSetCaptionLineRetypesALineAndRejectsAWrongNumber() {
        XCTAssertTrue(run("set_caption_line", #"{"line":2,"text":"a new second line"}"#).contains("a new second line"))
        XCTAssertEqual(project.captions[1].text, "a new second line")
        XCTAssertTrue(run("set_caption_line", #"{"line":9,"text":"x"}"#).contains("no line 9"))
        XCTAssertTrue(run("set_caption_line", #"{"line":0,"text":"x"}"#).contains("no line 0"))
    }

    // MARK: Title cards

    func testAddTitleCardPlacesItAndSortsByTime() {
        _ = run("add_title_card", #"{"text":"Late","at_seconds":10,"for_seconds":2}"#)
        _ = run("add_title_card", #"{"text":"Early","at_seconds":1}"#)
        XCTAssertEqual(project.cards.map { $0.text }, ["Early", "Late"])
        XCTAssertEqual(project.cards[0].end - project.cards[0].start, 3, accuracy: 1e-9, "three seconds when none is given")
        XCTAssertEqual(project.cards[1].start, 10)
        XCTAssertEqual(project.cards[1].end, 12)
        XCTAssertEqual(project.cards[1].kind, "explainer")
        XCTAssertNotEqual(project.cards[0].id, project.cards[1].id, "each card has its own id")
    }

    func testACardCannotStartAfterTheVideoEndsAndItsLengthIsClamped() {
        XCTAssertTrue(run("add_title_card", #"{"text":"Never","at_seconds":99}"#).contains("only 15 seconds"))
        XCTAssertTrue(project.cards.isEmpty)
        _ = run("add_title_card", #"{"text":"Long","at_seconds":1,"for_seconds":20}"#)
        XCTAssertEqual(project.cards[0].end - project.cards[0].start, 6, accuracy: 1e-9)
        XCTAssertTrue(run("add_title_card").contains("text"))
    }

    func testRemoveTitleCardUsesTheNumbersGetEditShows() {
        _ = run("add_title_card", #"{"text":"Second","at_seconds":8}"#)
        _ = run("add_title_card", #"{"text":"First","at_seconds":2}"#)
        XCTAssertTrue(run("get_edit").contains("1) 0:02-0:05 \"First\""))
        XCTAssertTrue(run("remove_title_card", #"{"number":1}"#).contains("First"))
        XCTAssertEqual(project.cards.map { $0.text }, ["Second"])
        XCTAssertTrue(run("remove_title_card", #"{"number":5}"#).contains("no such card"))
    }

    // MARK: Music and effects

    func testSetMusicChoicesAndFollowingTheChannelAgain() {
        XCTAssertTrue(run("set_music", #"{"choice":"none"}"#).contains("no music"))
        XCTAssertEqual(project.edits?.music, .silent)
        _ = run("set_music", #"{"choice":"auto"}"#)
        XCTAssertEqual(project.edits?.music, .auto)
        _ = run("set_music", #"{"choice":"channel"}"#)
        XCTAssertNil(project.edits, "nothing left to override, so no edits are stored")
    }

    func testAnUnknownTrackIsRefusedWithTheAvailableNames() {
        XCTAssertTrue(run("set_music", #"{"choice":"Epic Dubstep"}"#).contains("No track named"))
        XCTAssertNil(project.edits)
    }

    func testMusicChoiceMatchesTracksByNameLeniently() {
        var calm = AudioItem(kind: .music, name: "Calm Piano", fileName: "a.mp3"); calm.id = "calm"
        var warm = AudioItem(kind: .music, name: "Warm Piano", fileName: "b.mp3"); warm.id = "warm"
        let library = [calm, warm]
        guard case .success(let exact) = EditTools.musicChoice("calm piano", library: library) else { return XCTFail("exact") }
        XCTAssertEqual(exact, .track("calm"))
        guard case .success(let partial) = EditTools.musicChoice("warm", library: library) else { return XCTFail("partial") }
        XCTAssertEqual(partial, .track("warm"))
        guard case .failure(let ambiguous) = EditTools.musicChoice("piano", library: library) else { return XCTFail("ambiguous") }
        XCTAssertTrue(ambiguous.contains("More than one"))
        guard case .success(let silent) = EditTools.musicChoice("No music", library: library) else { return XCTFail("silent") }
        XCTAssertEqual(silent, .silent)
    }

    func testSetSoundEffects() {
        _ = run("set_sound_effects", #"{"mode":"on"}"#)
        XCTAssertEqual(project.edits?.sfxEnabled, true)
        _ = run("set_sound_effects", #"{"mode":"OFF"}"#)
        XCTAssertEqual(project.edits?.sfxEnabled, false)
        XCTAssertTrue(run("set_sound_effects", #"{"mode":"maybe"}"#).contains("Say channel"))
        _ = run("set_sound_effects", #"{"mode":"channel"}"#)
        XCTAssertNil(project.edits)
    }

    // MARK: Scope

    func testItCannotReachSettingsChannelsOrKeys() {
        let before = project
        for name in ["change_setting", "edit_channel", "manage_project", "set_api_key", "render"] {
            XCTAssertTrue(run(name, #"{"key":"x"}"#).contains("can't do that"), name)
        }
        XCTAssertEqual(project, before)
    }

    func testItsToolsAreADifferentSetFromTheMainAssistants() {
        let main = Set(AgentTools.specs(for: AppSettings()).map { $0.name })
        XCTAssertTrue(Set(EditTools.toolNames).isDisjoint(with: main))
        XCTAssertEqual(Set(EditTools.toolNames).count, EditTools.toolNames.count, "no duplicates")
    }

    func testEveryToolHasASummaryAndStaysUnderTheTokenBudget() {
        for spec in EditTools.specs { XCTAssertFalse(spec.summary.isEmpty, spec.name) }
        XCTAssertLessThanOrEqual(EditTools.specs.count, 14, "Groq's free plan allows about 8K tokens a minute, and the tool list is sent every time")
    }

    // MARK: Turns

    func testATurnRunsToolsThenAnswersAndSendsOnlyItsOwnTools() async {
        let provider = ScriptedProvider(queue: [
            .success(.calling("add_title_card", #"{"text":"Hi","at_seconds":3}"#, id: "a")),
            .success(.assistant(text: "Added a card at 0:03.")),
        ])
        assistant.makeProvider = { _ in provider }
        await assistant.send("add a title card at 3 seconds saying Hi").value

        XCTAssertEqual(project.cards.map { $0.text }, ["Hi"])
        XCTAssertEqual(assistant.lines.map { $0.kind }, [.user, .activity, .assistant])
        XCTAssertEqual(provider.calls.first?.tools.map { $0.name }, EditTools.toolNames)
        XCTAssertEqual(provider.calls.first?.system, EditTools.systemPrompt)
        XCTAssertFalse(assistant.isWorking)
    }

    func testOneTapUndoesEverythingATurnChanged() async {
        let both = AgentMessage.assistant(text: "", calls: [
            toolCall("add_title_card", #"{"text":"Hi","at_seconds":3}"#, id: "a"),
            toolCall("fix_caption_word", #"{"find":"wrld","replace":"world"}"#, id: "b"),
        ])
        let provider = ScriptedProvider(queue: [.success(both), .success(.assistant(text: "Done."))])
        assistant.makeProvider = { _ in provider }
        await assistant.send("do two things").value
        XCTAssertEqual(project.cards.count, 1)
        XCTAssertEqual(project.captions[0].text, "Hello world")
        XCTAssertEqual(assistant.undoStack.count, 1, "one snapshot for the whole turn")

        assistant.undo()
        XCTAssertTrue(project.cards.isEmpty)
        XCTAssertEqual(project.captions[0].text, "Hello wrld")
        XCTAssertFalse(assistant.canUndo)
    }

    func testATurnThatChangesNothingLeavesNothingToUndo() async {
        let provider = ScriptedProvider(queue: [.success(.calling("get_edit")), .success(.assistant(text: "It has 3 lines."))])
        assistant.makeProvider = { _ in provider }
        await assistant.send("what is in this video?").value
        XCTAssertFalse(assistant.canUndo)
        XCTAssertEqual(provider.calls.count, 2, "one tool call, then the answer")
    }

    func testItStopsAfterTheStepLimit() async {
        assistant.makeProvider = { _ in ScriptedProvider { _, _ in .success(.calling("get_edit", "{}", id: "c")) } }
        await assistant.send("loop forever").value
        XCTAssertEqual(assistant.lines.last?.kind, .error)
        XCTAssertTrue(assistant.lines.last?.text.contains("\(EditAssistant.maxSteps) steps") ?? false)
    }

    func testAMalformedCallIsAnsweredByAskingAgain() async {
        let provider = ScriptedProvider(queue: [
            .failure(AVGError.provider(.malformedOutput, provider: "groq", message: "bad json")),
            .success(.assistant(text: "Fixed.")),
        ])
        assistant.makeProvider = { _ in provider }
        await assistant.send("fix it").value
        XCTAssertEqual(provider.calls.count, 2)
        XCTAssertTrue(provider.calls[1].messages.contains { $0.synthetic }, "the second request carries the nudge")
        XCTAssertEqual(assistant.lines.last?.text, "Fixed.")
    }

    func testAMissingKeyIsExplainedInThePersonsWords() async {
        assistant.makeProvider = { _ in throw AVGError.missingKey("Groq") }
        await assistant.send("hello").value
        XCTAssertEqual(assistant.lines.last?.kind, .error)
        XCTAssertTrue(assistant.lines.last?.text.contains("isn't available") ?? false)
    }

    func testSwitchingVideosStartsAFreshChat() async {
        let provider = ScriptedProvider(queue: [.success(.assistant(text: "ok"))])
        assistant.makeProvider = { _ in provider }
        await assistant.send("hi").value
        XCTAssertFalse(assistant.lines.isEmpty)
        assistant.attach(store, projectID: "another-video")
        XCTAssertTrue(assistant.lines.isEmpty)
        XCTAssertFalse(assistant.canUndo)
    }
}

final class TitleCardsTests: XCTestCase {
    func testACardKeepsInsideTheVideoAndGetsAUniqueScene() {
        let first = TitleCards.make(text: " Hi ", start: 14.9, length: 3, total: 15, existing: [])
        XCTAssertEqual(first.text, "Hi")
        XCTAssertEqual(first.start, 14.5, accuracy: 1e-9, "a card never starts in the last half second")
        XCTAssertEqual(first.end, 15, accuracy: 1e-9)
        let second = TitleCards.make(text: "Two", start: 1, length: 2, total: 15, existing: [first])
        XCTAssertGreaterThan(second.scene, first.scene)
        XCTAssertNotEqual(first.id, second.id)
        XCTAssertGreaterThanOrEqual(first.scene, 1000)
    }
}

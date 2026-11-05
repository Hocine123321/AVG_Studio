import XCTest
@testable import AVGStudio

// S2 - per-video overrides: a video's own music level, effects level and cuts
// level beat its channel's, in one place (`EditResolution`), and the editing
// assistant can set all three for this one video.

final class EditResolutionTests: XCTestCase {
    private func project(_ edits: ProjectEdits? = nil) -> Project {
        var p = Project(id: "resolution-\(UUID().uuidString)", title: "T", topic: "t")
        p.edits = edits
        return p
    }

    private func channel() -> Channel {
        var ch = Channel(name: "Shorts")
        ch.musicGainDB = -24
        ch.sfxGainDB = 3
        ch.sfxEnabled = true
        ch.editLevel = .smart
        return ch
    }

    func testAVideoWithNoOverridesFollowsItsChannel() {
        let r = EditResolution(project: project(), channel: channel())
        XCTAssertEqual(r.musicGainDB, -24)
        XCTAssertEqual(r.sfxGainDB, 3)
        XCTAssertTrue(r.sfxEnabled)
        XCTAssertEqual(r.editLevel, .smart)
    }

    func testTheVideosOwnChoicesBeatTheChannels() {
        let edits = ProjectEdits(sfxEnabled: false, musicGainDB: -15, sfxGainDB: -6, editLevel: .off)
        let r = EditResolution(project: project(edits), channel: channel())
        XCTAssertEqual(r.musicGainDB, -15)
        XCTAssertEqual(r.sfxGainDB, -6)
        XCTAssertFalse(r.sfxEnabled)
        XCTAssertEqual(r.editLevel, .off)
    }

    func testEachOverrideIsIndependent() {
        let r = EditResolution(project: project(ProjectEdits(editLevel: .fast)), channel: channel())
        XCTAssertEqual(r.editLevel, .fast)
        XCTAssertEqual(r.musicGainDB, -24, "only the cuts level was overridden")
        XCTAssertEqual(r.sfxGainDB, 3)
    }

    func testAVideosOwnLevelsStayInsideTheSliderRanges() {
        let loud = EditResolution(project: project(ProjectEdits(musicGainDB: 5, sfxGainDB: 30)), channel: channel())
        XCTAssertEqual(loud.musicGainDB, -10)
        XCTAssertEqual(loud.sfxGainDB, 6)
        let quiet = EditResolution(project: project(ProjectEdits(musicGainDB: -60, sfxGainDB: -40)), channel: channel())
        XCTAssertEqual(quiet.musicGainDB, -35)
        XCTAssertEqual(quiet.sfxGainDB, -12)
    }

    func testProjectEditsSurviveARoundTripAndOlderFilesStillLoad() throws {
        let edits = ProjectEdits(music: .auto, sfxEnabled: true, musicGainDB: -18, sfxGainDB: 2, editLevel: .smart)
        let back = try JSONDecoder().decode(ProjectEdits.self, from: JSONEncoder().encode(edits))
        XCTAssertEqual(back, edits)

        let old = try JSONDecoder().decode(ProjectEdits.self, from: Data(#"{"sfxEnabled":true}"#.utf8))
        XCTAssertEqual(old.sfxEnabled, true)
        XCTAssertNil(old.musicGainDB)
        XCTAssertNil(old.sfxGainDB)
        XCTAssertNil(old.editLevel)
    }

    func testEveryOverrideCountsAsAnEdit() {
        XCTAssertTrue(ProjectEdits().isEmpty)
        XCTAssertFalse(ProjectEdits(musicGainDB: -20).isEmpty)
        XCTAssertFalse(ProjectEdits(sfxGainDB: 0).isEmpty)
        XCTAssertFalse(ProjectEdits(editLevel: .off).isEmpty)
    }
}

@MainActor
final class PerVideoPlanTests: XCTestCase {
    private var store: Store!
    private var projectID = ""

    override func setUp() {
        store = Store()
        projectID = "per-video-plan-\(UUID().uuidString)"
        var p = Project(id: projectID, title: "Plan test", topic: "t")
        p.sceneDurations = [9, 9]
        // A word every half second across both scenes, so a 9 s scene has words to cut on.
        p.captions = [CaptionCue(start: 0, end: 18, text: "words",
                                 words: (0..<36).map { WordTiming(text: "w\($0)", start: Double($0) * 0.5, end: Double($0) * 0.5 + 0.4) })]
        store.projects.append(p)
    }

    override func tearDown() {
        store.deleteProject(projectID)
    }

    private func plan(channelLevel: EditLevel, videoLevel: EditLevel?) -> EditPlan? {
        store.update(projectID) { $0.edits = videoLevel.map { ProjectEdits(editLevel: $0) } }
        var ch = Channel(name: "C")
        ch.editLevel = channelLevel
        let pipeline = Pipeline(store: store, projectID: projectID, resumeExisting: false)
        return pipeline.editPlan(for: store.project(projectID)!, channel: ch)
    }

    func testTheChannelsLevelApplies() {
        XCTAssertNotNil(plan(channelLevel: .fast, videoLevel: nil))
        XCTAssertNil(plan(channelLevel: .off, videoLevel: nil))
    }

    func testAVideoCanDropTheCutsItsChannelHas() {
        XCTAssertNil(plan(channelLevel: .fast, videoLevel: .off))
    }

    func testAVideoCanTurnOnCutsItsChannelDoesNot() {
        XCTAssertNotNil(plan(channelLevel: .off, videoLevel: .fast))
    }
}

@MainActor
final class PerVideoAssistantTests: XCTestCase {
    private var store: Store!
    private var projectID = ""
    private var assistant: EditAssistant!

    override func setUp() {
        store = Store()
        projectID = "per-video-assistant-\(UUID().uuidString)"
        var p = Project(id: projectID, title: "Test video", topic: "t")
        p.sceneDurations = [5, 5, 5]
        store.projects.append(p)
        assistant = EditAssistant()
        assistant.attach(store, projectID: projectID)
    }

    override func tearDown() {
        store.deleteProject(projectID)
    }

    private var edits: ProjectEdits? { store.project(projectID)?.edits }

    private func run(_ name: String, _ json: String = "{}") -> String {
        assistant.run(toolCall(name, json))
    }

    // MARK: Music level

    func testSetMusicCanChangeOnlyTheLevel() {
        let reply = run("set_music", #"{"level_db":-15}"#)
        XCTAssertEqual(edits?.musicGainDB, -15)
        XCTAssertNil(edits?.music, "the track choice is untouched")
        XCTAssertTrue(reply.contains("-15 dB"))
    }

    func testALevelOutsideTheRangeIsKeptInsideItAndSaysSo() {
        let reply = run("set_music", #"{"level_db":-50}"#)
        XCTAssertEqual(edits?.musicGainDB, -35)
        XCTAssertTrue(reply.contains("outside"))
        _ = run("set_music", #"{"level_db":"-5"}"#)
        XCTAssertEqual(edits?.musicGainDB, -10, "a level sent as a string is accepted, and kept in range")
    }

    func testSetMusicCanChangeTheChoiceAndTheLevelTogether() {
        _ = run("set_music", #"{"choice":"none","level_db":-22}"#)
        XCTAssertEqual(edits?.music, .silent)
        XCTAssertEqual(edits?.musicGainDB, -22)
    }

    func testChannelPutsBackTheChannelsMusicAndLevel() {
        _ = run("set_music", #"{"choice":"auto","level_db":-15}"#)
        XCTAssertNotNil(edits)
        _ = run("set_music", #"{"choice":"channel"}"#)
        XCTAssertNil(edits, "both the choice and the level go back to the channel's")
    }

    func testChannelWithALevelInTheSameCallKeepsThatLevel() {
        _ = run("set_music", #"{"choice":"channel","level_db":-12}"#)
        XCTAssertNil(edits?.music)
        XCTAssertEqual(edits?.musicGainDB, -12)
    }

    func testAnUnknownTrackAppliesNothingNotEvenTheLevel() {
        XCTAssertTrue(run("set_music", #"{"choice":"Epic Dubstep","level_db":-12}"#).contains("No track named"))
        XCTAssertNil(edits)
    }

    func testSetMusicNeedsAChoiceOrALevel() {
        XCTAssertTrue(run("set_music").contains("Say channel"))
        XCTAssertNil(edits)
    }

    // MARK: Effects level

    func testSetSoundEffectsCanChangeOnlyTheLevel() {
        let reply = run("set_sound_effects", #"{"level_db":4}"#)
        XCTAssertEqual(edits?.sfxGainDB, 4)
        XCTAssertNil(edits?.sfxEnabled, "the on/off switch is untouched")
        XCTAssertTrue(reply.contains("4 dB"))
        _ = run("set_sound_effects", #"{"level_db":20}"#)
        XCTAssertEqual(edits?.sfxGainDB, 6)
    }

    func testSoundEffectsChannelPutsBackTheChannelsLevelToo() {
        _ = run("set_sound_effects", #"{"mode":"on","level_db":-3}"#)
        XCTAssertEqual(edits?.sfxEnabled, true)
        XCTAssertEqual(edits?.sfxGainDB, -3)
        _ = run("set_sound_effects", #"{"mode":"channel"}"#)
        XCTAssertNil(edits)
    }

    func testSetSoundEffectsStillRejectsAnUnknownMode() {
        XCTAssertTrue(run("set_sound_effects", #"{"mode":"maybe","level_db":2}"#).contains("Say channel"))
        XCTAssertNil(edits, "nothing applied")
        XCTAssertTrue(run("set_sound_effects").contains("Say channel"))
    }

    // MARK: Cuts

    func testSetCutsChoosesThisVideosLevel() {
        _ = run("set_cuts", #"{"level":"off"}"#)
        XCTAssertEqual(edits?.editLevel, .off)
        _ = run("set_cuts", #"{"level":"cuts"}"#)
        XCTAssertEqual(edits?.editLevel, .fast)
        let smart = run("set_cuts", #"{"level":"SMART"}"#)
        XCTAssertEqual(edits?.editLevel, .smart)
        XCTAssertTrue(smart.contains("Google key"), "Smart crops say what they need")
        _ = run("set_cuts", #"{"level":"channel"}"#)
        XCTAssertNil(edits, "back to the channel's level, nothing left to store")
    }

    func testSetCutsRefusesAnUnknownLevel() {
        _ = run("set_cuts", #"{"level":"off"}"#)
        XCTAssertTrue(run("set_cuts", #"{"level":"turbo"}"#).contains("Say channel, off, cuts or smart"))
        XCTAssertTrue(run("set_cuts").contains("Say channel, off, cuts or smart"))
        XCTAssertEqual(edits?.editLevel, .off, "a bad request changes nothing")
    }

    func testCutsChoicesAcceptTheLooseWords() {
        XCTAssertEqual(EditTools.cutsChoice("Fast")?.level, .fast)
        XCTAssertEqual(EditTools.cutsChoice("none")?.level, .off)
        XCTAssertEqual(EditTools.cutsChoice("smart crops")?.level, .smart)
        XCTAssertNotNil(EditTools.cutsChoice("same as channel"))
        XCTAssertNil(EditTools.cutsChoice("same as channel")?.level)
        XCTAssertNil(EditTools.cutsChoice("turbo"))
    }

    // MARK: Reading and scope

    func testGetEditReportsTheLevelsAndWhoseTheyAre() {
        var text = run("get_edit")
        XCTAssertTrue(text.contains("Cuts and punch-ins:"))
        XCTAssertTrue(text.contains("(channel)"), "nothing is overridden yet")

        _ = run("set_music", #"{"level_db":-15}"#)
        _ = run("set_cuts", #"{"level":"off"}"#)
        text = run("get_edit")
        XCTAssertTrue(text.contains("level -15 dB under the voice."), "this video's own level carries no (channel) tag")
        XCTAssertTrue(text.contains("Cuts and punch-ins: off."))
    }

    func testTheNewToolIsInTheEditingSetAndNotTheMainAssistants() {
        XCTAssertTrue(EditTools.toolNames.contains("set_cuts"))
        XCTAssertFalse(AgentTools.specs(for: AppSettings()).map { $0.name }.contains("set_cuts"))
        XCTAssertLessThanOrEqual(EditTools.specs.count, 14)
    }

    func testTheLevelArgumentsAreOptional() throws {
        for tool in ["set_music", "set_sound_effects"] {
            let spec = try XCTUnwrap(EditTools.specs.first { $0.name == tool })
            let level = try XCTUnwrap(spec.params.first { $0.name == "level_db" })
            XCTAssertFalse(level.required)
            XCTAssertTrue(spec.params.allSatisfy { !$0.required }, "\(tool) works with a choice, a level, or both")
        }
    }
}

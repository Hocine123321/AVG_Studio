import XCTest
@testable import AVGStudio

final class MusicPickerTests: XCTestCase {
    private func track(_ id: String, _ tags: [String] = [], own: Bool = true) -> MusicPicker.Candidate {
        MusicPicker.Candidate(id: id, tags: tags, isOwn: own)
    }

    func testNothingToPickFromGivesNil() {
        XCTAssertNil(MusicPicker.pick(from: [], text: "anything", recent: []))
    }

    func testASingleTrackIsAlwaysUsedEvenIfItWasJustUsed() {
        XCTAssertEqual(MusicPicker.pick(from: [track("only")], text: "x", recent: ["only"]), "only")
    }

    func testATagFoundInTheTextWins() {
        let tracks = [track("calm", ["calm"]), track("history", ["history"])]
        XCTAssertEqual(MusicPicker.pick(from: tracks, text: "The history of Rome", recent: []), "history")
    }

    func testRelatedWordsSuggestAMoodTag() {
        let tracks = [track("calm", ["calm"]), track("tense", ["tense"])]
        XCTAssertEqual(MusicPicker.pick(from: tracks, text: "An unsolved crime and a secret", recent: []), "tense")
        XCTAssertEqual(MusicPicker.pick(from: tracks, text: "A gentle evening to relax", recent: []), "calm")
    }

    func testScoreCountsTagsAndRelatedWords() {
        XCTAssertEqual(MusicPicker.score(tags: ["calm"], text: "a calm evening"), 3)
        XCTAssertEqual(MusicPicker.score(tags: ["tense"], text: "tense secret"), 4)
        XCTAssertEqual(MusicPicker.score(tags: ["TENSE", "  "], text: "SECRET"), 1, "case-insensitive; blank tags ignored")
        XCTAssertEqual(MusicPicker.score(tags: [], text: "anything"), 0)
    }

    func testTheTrackJustUsedIsSkippedWhenThereIsAnotherChoice() {
        let tracks = [track("a"), track("b")]
        XCTAssertEqual(MusicPicker.pick(from: tracks, text: "x", recent: ["a"]), "b")
        XCTAssertEqual(MusicPicker.pick(from: tracks, text: "x", recent: ["b"]), "a")
    }

    func testEvenAPerfectMatchGivesWayToVariety() {
        let tracks = [track("cinematic", ["cinematic"]), track("calm", ["calm"])]
        XCTAssertEqual(MusicPicker.pick(from: tracks, text: "an epic cinematic story", recent: ["cinematic"]), "calm")
    }

    func testTiesGoToTheLeastRecentlyUsed() {
        let tracks = [track("a"), track("b"), track("c")]
        XCTAssertEqual(MusicPicker.pick(from: tracks, text: "x", recent: ["a", "b"]), "c")
    }

    func testAnUploadBeatsAGeneratedTrackOnATie() {
        let tracks = [track("generated", own: false), track("mine", own: true)]
        XCTAssertEqual(MusicPicker.pick(from: tracks, text: "x", recent: []), "mine")
    }

    func testTheChoiceDoesNotDependOnOrder() {
        let a = [track("b"), track("a"), track("c")]
        let b = [track("c"), track("a"), track("b")]
        XCTAssertEqual(MusicPicker.pick(from: a, text: "x", recent: []), "a")
        XCTAssertEqual(MusicPicker.pick(from: b, text: "x", recent: []), "a")
    }
}

final class ChannelAutoMusicDecodingTests: XCTestCase {
    func testOlderChannelsPickNothingAutomatically() throws {
        let channel = try JSONDecoder().decode(Channel.self, from: Data(#"{"name":"Old"}"#.utf8))
        XCTAssertFalse(channel.musicAuto)
        XCTAssertTrue(channel.musicRecent.isEmpty)
    }

    func testTheAutomaticSettingAndHistorySurviveARoundTrip() throws {
        var channel = Channel(name: "Shorts")
        channel.musicAuto = true
        channel.musicRecent = ["a", "b"]
        let back = try JSONDecoder().decode(Channel.self, from: JSONEncoder().encode(channel))
        XCTAssertTrue(back.musicAuto)
        XCTAssertEqual(back.musicRecent, ["a", "b"])
    }

    func testAProjectKeepsItsChosenTrack() throws {
        var project = Project(id: "p", title: "T", topic: "t")
        project.autoMusicID = "track-1"
        let back = try JSONDecoder().decode(Project.self, from: JSONEncoder().encode(project))
        XCTAssertEqual(back.autoMusicID, "track-1")
    }
}

@MainActor
final class StoreRememberMusicTests: XCTestCase {
    func testKeepsTheThreeNewestAndMovesARepeatToTheFront() {
        let store = Store()
        let channel = Channel(name: "Remember")
        store.channels.append(channel)
        defer { store.channels.removeAll { $0.id == channel.id } }

        for id in ["a", "b", "c", "d"] { store.rememberMusic(channel: channel.id, track: id) }
        XCTAssertEqual(store.channels.first { $0.id == channel.id }?.musicRecent, ["d", "c", "b"])
        store.rememberMusic(channel: channel.id, track: "c")
        XCTAssertEqual(store.channels.first { $0.id == channel.id }?.musicRecent, ["c", "d", "b"])
    }

    func testAnUnknownChannelIsIgnored() {
        Store().rememberMusic(channel: UUID(), track: "a")
    }
}

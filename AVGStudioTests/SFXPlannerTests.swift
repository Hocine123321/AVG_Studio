import XCTest
@testable import AVGStudio

final class SFXPlannerTests: XCTestCase {
    /// Six 5-second scenes (30 s), every moment has a sound.
    private func input(transitions: [Int: SceneTransition] = [:], beats: [Int: String] = [:], roles: [Int: String] = [:],
                       cards: [(start: Double, kind: String?)] = [],
                       sounds: [SFXMoment: Double]? = nil) -> SFXPlanner.Input {
        var i = SFXPlanner.Input(sceneDurations: Array(repeating: 5, count: 6))
        i.transitions = transitions
        i.beats = beats
        i.roles = roles
        i.cards = cards
        i.soundLength = sounds ?? [.impact: 1, .riser: 2, .whoosh: 0.5, .pop: 0.5, .softHit: 1]
        return i
    }

    func testAWhooshOnlyOnASlideOrWhipIntoALaterScene() {
        let cues = SFXPlanner.plan(input(transitions: [1: .whip, 2: .fade, 3: .slideLeft, 4: .cut]))
        XCTAssertEqual(cues.map(\.moment), [.whoosh], "scene 1 starts at 0 (nothing to transition from); a fade or cut is silent")
        XCTAssertEqual(cues[0].event, 10, accuracy: 1e-9)
        XCTAssertEqual(cues[0].at, 10 - SFXPlanner.lead, accuracy: 1e-9, "the sound leads the picture")
    }

    func testAnImpactLandsOnAHitBeat() {
        let cues = SFXPlanner.plan(input(beats: [3: "HIT", 4: "ZOOM", 5: "none"]))
        XCTAssertEqual(cues.map(\.moment), [.impact])
        XCTAssertEqual(cues[0].event, 10, accuracy: 1e-9)
    }

    func testARiserEndsOnTheRevealAndIsCappedInLength() {
        let short = SFXPlanner.plan(input(roles: [4: "reveal"]))
        XCTAssertEqual(short.first?.at ?? -1, 13, accuracy: 1e-9, "a 2 s riser starts 2 s before the reveal at 15")
        let long = SFXPlanner.plan(input(roles: [4: "reveal"], sounds: [.riser: 10]))
        XCTAssertEqual(long.first?.at ?? -1, 15 - SFXPlanner.maxRiser, accuracy: 1e-9)
    }

    func testARiserAndItsImpactCanShareAMoment() {
        let cues = SFXPlanner.plan(input(beats: [4: "HIT"], roles: [4: "reveal"]))
        XCTAssertEqual(cues.map(\.moment), [.riser, .impact], "ordered by when each sound starts")
    }

    func testTwoCardsOnlyHalfASecondApartShareOneSound() {
        let cues = SFXPlanner.plan(input(cards: [(start: 10, kind: nil), (start: 10.5, kind: "explainer")]))
        XCTAssertEqual(cues.count, 1)
        XCTAssertEqual(cues[0].event, 10, accuracy: 1e-9)
    }

    func testTheStrongerMomentWinsAContestedSpot() {
        let cues = SFXPlanner.plan(input(transitions: [3: .slideLeft], beats: [3: "HIT"]))
        XCTAssertEqual(cues.map(\.moment), [.impact], "the whoosh gives way to the impact")
    }

    func testNoMoreThanTwoCuesInAFiveSecondNeighbourhood() {
        let cues = SFXPlanner.plan(input(beats: [3: "HIT"], cards: [(start: 12, kind: "chapter"), (start: 14, kind: nil)]))
        XCTAssertEqual(cues.map(\.moment), [.impact, .softHit], "the third, lowest-priority cue is dropped")
    }

    func testAMomentWithNoSoundDoesNothing() {
        let cues = SFXPlanner.plan(input(beats: [3: "HIT"], cards: [(start: 20, kind: nil)], sounds: [.pop: 0.5]))
        XCTAssertEqual(cues.map(\.moment), [.pop])
    }

    func testNothingInTheFirstOrLastMoments() {
        let cues = SFXPlanner.plan(input(cards: [(start: 0.1, kind: nil), (start: 29.9, kind: nil)]))
        XCTAssertTrue(cues.isEmpty)
    }

    func testEmptyInputPlansNothing() {
        XCTAssertTrue(SFXPlanner.plan(SFXPlanner.Input(sceneDurations: [])).isEmpty)
        XCTAssertTrue(SFXPlanner.plan(input(sounds: [:])).isEmpty)
    }

    func testEveryMomentHasATitleADetailAndALevel() {
        for moment in SFXMoment.allCases {
            XCTAssertFalse(moment.title.isEmpty)
            XCTAssertFalse(moment.detail.isEmpty)
            XCTAssertLessThan(moment.defaultGainDB, 0, "effects sit below full scale")
        }
    }
}

final class ChannelSoundEffectsDecodingTests: XCTestCase {
    func testAChannelSavedBeforeEffectsExistedHasThemOff() throws {
        let channel = try JSONDecoder().decode(Channel.self, from: Data(#"{"name":"Old"}"#.utf8))
        XCTAssertFalse(channel.sfxEnabled)
        XCTAssertTrue(channel.sfxMap.isEmpty)
        XCTAssertEqual(channel.sfxGainDB, 0)
    }

    func testTheMomentsMapSurvivesARoundTrip() throws {
        var channel = Channel(name: "Shorts")
        channel.sfxEnabled = true
        channel.sfxMap = [SFXMoment.impact.rawValue: "abc", SFXMoment.whoosh.rawValue: "def"]
        channel.sfxGainDB = -3
        let back = try JSONDecoder().decode(Channel.self, from: JSONEncoder().encode(channel))
        XCTAssertTrue(back.sfxEnabled)
        XCTAssertEqual(back.sfxMap, channel.sfxMap)
        XCTAssertEqual(back.sfxGainDB, -3)
    }
}

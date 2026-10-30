import XCTest
@testable import AVGStudio

final class EditPlannerTests: XCTestCase {
    /// A word every half second, "w0 w1 w2 ...", each lasting 0.4 s.
    private func evenWords(_ count: Int) -> [WordTiming] {
        (0..<count).map { WordTiming(text: "w\($0)", start: Double($0) * 0.5, end: Double($0) * 0.5 + 0.4) }
    }

    private func plan(_ durations: [Double], words: [WordTiming]? = nil, zoom: [(scene: Int, word: String)] = [],
                      short: Bool = true) -> EditPlan {
        EditPlanner.plan(EditPlanner.Input(sceneDurations: durations, words: words ?? evenWords(60),
                                           zoomWords: zoom, shortForm: short))
    }

    // MARK: shot splits

    func testShortScenesAreLeftAlone() {
        XCTAssertTrue(plan([2.5, 3.0, 2.0]).cuts.isEmpty)
    }

    func testALongSceneIsCutIntoShotsOnSpokenWords() {
        let p = plan([9])
        XCTAssertEqual(p.cuts.map { $0.at }, [3.0, 6.0], "9 s at a 3 s target is three shots, cut on the words at 3.0 and 6.0")
        XCTAssertTrue(p.cuts.allSatisfy { $0.scene == 0 })
    }

    func testACutMovesToTheNearestWordWithinReach() {
        let words = [WordTiming(text: "a", start: 0, end: 0.4), WordTiming(text: "b", start: 3.0, end: 3.3),
                     WordTiming(text: "c", start: 3.4, end: 3.7), WordTiming(text: "d", start: 5.5, end: 5.9)]
        let p = plan([6.2], words: words)
        XCTAssertEqual(p.cuts.map { $0.at }, [3.0], "ideal 3.1 snaps to the word at 3.0, 0.1 s away")
    }

    func testCutsKeepTheirDistanceFromEachOtherAndFromTheEnds() {
        let p = plan([4.6, 12, 6])
        var edges: [Int: [Double]] = [:]
        let starts = [0.0, 4.6, 16.6]
        let ends = [4.6, 16.6, 22.6]
        for cut in p.cuts { edges[cut.scene, default: []].append(cut.at) }
        for (scene, times) in edges {
            let all = [starts[scene]] + times.sorted() + [ends[scene]]
            for (a, b) in zip(all, all.dropFirst()) { XCTAssertGreaterThanOrEqual(b - a, EditPlanner.minShot - 1e-9) }
        }
    }

    func testNoTwoNeighbouringShotsShareAZoom() {
        let p = plan([12, 9])
        var previous = 1.0
        for cut in p.cuts {
            XCTAssertGreaterThanOrEqual(abs(cut.framing.zoom - previous), 0.1 - 1e-9)
            previous = cut.framing.zoom
        }
        XCTAssertFalse(p.cuts.isEmpty)
    }

    func testAFramingNeverLeavesTheSafeArea() {
        let p = plan([30])
        for cut in p.cuts {
            XCTAssertTrue((0.15...0.85).contains(cut.framing.fx))
            XCTAssertTrue((0.2...0.8).contains(cut.framing.fy))
            XCTAssertGreaterThanOrEqual(cut.framing.zoom, 1.1)
        }
    }

    func testLongFormCutsLessOften() {
        XCTAssertEqual(plan([9], short: true).cuts.count, 2)
        XCTAssertEqual(plan([9], short: false).cuts.count, 1)
    }

    func testTheSubjectPointSteersTheCrop() {
        var input = EditPlanner.Input(sceneDurations: [9], words: evenWords(40))
        input.focus = [0: (x: 0.2, y: 0.3)]
        let p = EditPlanner.plan(input)
        XCTAssertTrue(p.cuts.allSatisfy { $0.framing.fx < 0.5 }, "crops lean toward a subject on the left")
        XCTAssertTrue(p.cuts.allSatisfy { $0.framing.fy <= 0.3 + 1e-9 })
    }

    func testPlanningIsDeterministic() {
        XCTAssertEqual(plan([9, 7, 12]), plan([9, 7, 12]))
    }

    // MARK: punch-ins

    private func spoken(_ items: [(String, Double)]) -> [WordTiming] {
        items.map { WordTiming(text: $0.0, start: $0.1, end: $0.1 + 0.3) }
    }

    func testANumberGetsAPunchThatLandsWithTheWord() {
        let words = spoken([("It", 1.0), ("costs", 1.4), ("40%", 5.0), ("more", 5.4), ("today", 6.0)])
        let p = plan([8], words: words)
        XCTAssertEqual(p.punches.count, 1)
        XCTAssertEqual(p.punches[0], 5.0 - 0.03, accuracy: 1e-9)
    }

    func testShoutedWordsCountButOrdinaryCapitalsDoNot() {
        XCTAssertTrue(EditPlanner.isShouted("NEVER"))
        XCTAssertTrue(EditPlanner.isShouted("“WOW!”"))
        XCTAssertFalse(EditPlanner.isShouted("OK"))
        XCTAssertFalse(EditPlanner.isShouted("iPhone"))
        XCTAssertFalse(EditPlanner.isShouted("Hello"))
    }

    func testPunchesAreNeverCloserThanTheMinimumGap() {
        let words = spoken([("a", 1.0), ("10", 4.0), ("20", 4.8), ("30", 7.0)])
        let p = plan([10], words: words)
        for (a, b) in zip(p.punches, p.punches.dropFirst()) { XCTAssertGreaterThanOrEqual(b - a, EditPlanner.minPunchGap - 1e-9) }
        XCTAssertFalse(p.punches.contains { abs($0 - (4.8 - 0.03)) < 1e-6 }, "the 4.8 s number is too close to the one at 4.0")
    }

    func testAShortVideoGetsFewPunches() {
        let words = spoken((0..<8).map { ("\($0)", Double($0) + 0.6) })
        XCTAssertLessThanOrEqual(plan([8], words: words).punches.count, 2, "at most one every 4 seconds")
    }

    func testTheStoryMapsKeyWordOutranksANumber() {
        let words = spoken([("never", 4.0), ("40", 5.0), ("end", 8.0)])
        let p = plan([10], words: words, zoom: [(scene: 1, word: "Never")])
        XCTAssertTrue(p.punches.contains { abs($0 - (4.0 - 0.03)) < 1e-6 })
        XCTAssertFalse(p.punches.contains { abs($0 - (5.0 - 0.03)) < 1e-6 }, "the number is within the minimum gap of the key word")
    }

    func testNothingFiresInTheFirstOrLastHalfSecond() {
        let words = spoken([("99", 0.1), ("end", 0.6), ("77", 9.8)])
        XCTAssertTrue(plan([10], words: words).punches.isEmpty)
    }

    func testAPauseBeforeAWordMarksABeatButIsAWeakReason() {
        let words = spoken([("so", 1.0), ("then", 3.0), ("42", 3.9)])
        let p = plan([10], words: words)
        XCTAssertTrue(p.punches.contains { abs($0 - (3.9 - 0.03)) < 1e-6 }, "the number wins over the pause that came before it")
    }

    // MARK: the plan itself

    func testShotAndPulseLookups() {
        let plan = EditPlan(cuts: [ShotCut(scene: 0, at: 3, framing: Framing(zoom: 1.3)),
                                   ShotCut(scene: 0, at: 6, framing: Framing(zoom: 1.1)),
                                   ShotCut(scene: 1, at: 11, framing: Framing(zoom: 1.5))],
                            punches: [5])
        XCTAssertNil(plan.shot(at: 2, scene: 0, sceneEnd: 9), "before the first cut the scene's own motion applies")
        let a = plan.shot(at: 4, scene: 0, sceneEnd: 9)
        XCTAssertEqual(a?.framing.zoom, 1.3)
        XCTAssertEqual(a?.progress ?? -1, 1.0 / 3.0, accuracy: 1e-9)
        let b = plan.shot(at: 7.5, scene: 0, sceneEnd: 9)
        XCTAssertEqual(b?.framing.zoom, 1.1)
        XCTAssertEqual(b?.progress ?? -1, 0.5, accuracy: 1e-9)
        XCTAssertNil(plan.shot(at: 10, scene: 1, sceneEnd: 14))
        XCTAssertEqual(plan.pulse(at: 5), 1, accuracy: 1e-9)
        XCTAssertEqual(plan.pulse(at: 5 + EditPlan.punchLength / 2), 0.25, accuracy: 1e-9)
        XCTAssertEqual(plan.pulse(at: 5 + EditPlan.punchLength), 0)
        XCTAssertEqual(plan.pulse(at: 4.9), 0)
    }

    func testAnEmptyPlanIsEmptyAndRoundTrips() throws {
        XCTAssertTrue(EditPlan().isEmpty)
        let full = plan([9])
        let back = try JSONDecoder().decode(EditPlan.self, from: JSONEncoder().encode(full))
        XCTAssertEqual(back, full)
        XCTAssertFalse(full.isEmpty)
    }

    func testTheChannelDefaultsToCutsAndSurvivesARoundTrip() throws {
        let old = try JSONDecoder().decode(Channel.self, from: Data(#"{"name":"Old"}"#.utf8))
        XCTAssertEqual(old.editLevel, .fast)
        var ch = Channel(name: "X")
        ch.editLevel = .off
        XCTAssertEqual(try JSONDecoder().decode(Channel.self, from: JSONEncoder().encode(ch)).editLevel, .off)
    }
}

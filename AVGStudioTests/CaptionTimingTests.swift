import XCTest
@testable import AVGStudio

final class VoicePlanTests: XCTestCase {
    private func plan(_ texts: [String], pauses: Bool = true, pause: Double = 0.6, chapters: [Int: Double] = [:]) -> VoicePlan {
        VoicePlan.make(sceneTexts: texts, dynamicPauses: pauses, pauseSeconds: pause, chapterGaps: chapters)
    }

    func testATakeEndsAtAPauseNotAtASceneBoundary() {
        let p = plan(["One two. [pause] Three four.", "Five."])
        XCTAssertEqual(p.runs.map(\.text), ["One two.", "Three four. Five."], "scene 2 joins the take already in progress")
        XCTAssertEqual(p.runs.map(\.gapBefore), [0, 0.6])
        XCTAssertEqual(p.words.map(\.text), ["One", "two.", "Three", "four.", "Five."])
        XCTAssertEqual(p.words.map(\.scene), [0, 0, 0, 0, 1])
        XCTAssertEqual(p.words.map(\.run), [0, 0, 1, 1, 1])
    }

    func testDisablingPausesRecordsOneTakeAndStripsTheMarkers() {
        let p = plan(["One two. [pause] Three four.", "Five."], pauses: false)
        XCTAssertEqual(p.runs.count, 1)
        XCTAssertEqual(p.runs[0].text, "One two. Three four. Five.")
        XCTAssertEqual(p.runs[0].gapBefore, 0)
    }

    func testALeadingPauseIsIgnoredAndATrailingOneBecomesTheNextGap() {
        XCTAssertEqual(plan(["[pause] Hello there."]).runs.map(\.gapBefore), [0], "no dead air before the first word")
        let p = plan(["Hello there. [pause]", "Next one."])
        XCTAssertEqual(p.runs.map(\.text), ["Hello there.", "Next one."])
        XCTAssertEqual(p.runs.map(\.gapBefore), [0, 0.6])
    }

    func testAChapterSplitsTheTakeAndCarriesItsCardWindow() {
        let p = plan(["A b.", "C d.", "E f."], chapters: [1: 1.8])
        XCTAssertEqual(p.runs.map(\.text), ["A b.", "C d. E f."])
        XCTAssertEqual(p.runs.map(\.gapBefore), [0, 1.8])
        XCTAssertEqual(p.runs.map(\.gapKind), [nil, "chapter"])
    }

    func testAPauseNeverStacksOnAChapterGap() {
        let p = plan(["A b.", "[pause] C d."], chapters: [1: 1.8])
        XCTAssertEqual(p.runs.map(\.gapBefore), [0, 1.8])
    }

    func testAChapterBeforeTheFirstSceneDelaysTheNarration() {
        let p = plan(["A b.", "C d."], chapters: [0: 1.5])
        XCTAssertEqual(p.runs[0].gapBefore, 1.5)
        XCTAssertEqual(p.runs[0].gapKind, "chapter")
    }

    func testMarkersAreRemovedFromSpokenWords() {
        let p = plan(["Wait [pause] ((Why?)) now."])
        XCTAssertEqual(p.runs.map(\.text), ["Wait", "Why? now."])
    }
}

final class CaptionWeightTests: XCTestCase {
    func testWeightsFollowSyllablesNotLetters() {
        XCTAssertEqual(CaptionTiming.weight("a").word, 1.35, accuracy: 1e-9)
        XCTAssertEqual(CaptionTiming.weight("strength").word, 1.35, accuracy: 1e-9, "eight letters, one beat")
        XCTAssertEqual(CaptionTiming.weight("ubiquitous").word, 4.35, accuracy: 1e-9)
        XCTAssertGreaterThan(CaptionTiming.weight("2000").word, CaptionTiming.weight("cat").word, "a number takes longer to say than its digit count suggests")
    }

    func testPunctuationAddsAPauseAfterTheWord() {
        XCTAssertEqual(CaptionTiming.weight("word").gap, 0)
        XCTAssertEqual(CaptionTiming.weight("Hello,").gap, 0.55, accuracy: 1e-9)
        XCTAssertEqual(CaptionTiming.weight("end.").gap, 1.4, accuracy: 1e-9)
        XCTAssertEqual(CaptionTiming.weight("wow?!").gap, 1.4, accuracy: 1e-9)
        XCTAssertEqual(CaptionTiming.weight("\"Done.\"").gap, 1.4, accuracy: 1e-9, "a closing quote does not hide the full stop")
        XCTAssertEqual(CaptionTiming.weight("so;").gap, 0.9, accuracy: 1e-9)
    }

    func testEstimateFillsTheSpanExactly() {
        let even = CaptionTiming.estimate(["aa", "bb"], in: .init(start: 0, end: 2))
        XCTAssertEqual(even[0].start, 0, accuracy: 1e-9)
        XCTAssertEqual(even[0].end, 1.0, accuracy: 1e-9)
        XCTAssertEqual(even[1].start, 1.0, accuracy: 1e-9)
        XCTAssertEqual(even[1].end, 2.0, accuracy: 1e-9)

        let pausey = CaptionTiming.estimate(["aa.", "bb"], in: .init(start: 0, end: 4.1))
        XCTAssertEqual(pausey[0].end, 1.35, accuracy: 1e-9)
        XCTAssertEqual(pausey[1].start, 2.75, accuracy: 1e-9, "the full stop leaves a gap")
        XCTAssertEqual(pausey[1].end, 4.1, accuracy: 1e-9)

        XCTAssertEqual(CaptionTiming.estimate(["solo"], in: .init(start: 3, end: 5)), [.init(start: 3, end: 5)])
        XCTAssertTrue(CaptionTiming.estimate([], in: .init(start: 0, end: 1)).isEmpty)
    }
}

final class CaptionAnchorTests: XCTestCase {
    private func plan() -> VoicePlan {
        VoicePlan.make(sceneTexts: ["One two. [pause] Three four."], dynamicPauses: true, pauseSeconds: 0.6)
    }

    func testWordsStayInsideTheirRecordedSegment() {
        let segments = [AudioSegment(start: 0, end: 2), AudioSegment(start: 2.6, end: 5.0, gapBefore: 0.6)]
        let spans = CaptionTiming.anchored(plan: plan(), segments: segments, duration: 5.0)
        XCTAssertEqual(spans.count, 4)
        XCTAssertEqual(spans[1].end, 2.0, accuracy: 1e-9)
        XCTAssertEqual(spans[2].start, 2.6, accuracy: 1e-9, "the pause is real silence, not spread across the words")
        XCTAssertEqual(spans[3].end, 5.0, accuracy: 1e-9)
    }

    func testAMismatchedOrMissingRecordingFallsBackToTheWholeAudio() {
        for segments in [nil, [AudioSegment(start: 0, end: 5)]] as [[AudioSegment]?] {
            let spans = CaptionTiming.anchored(plan: plan(), segments: segments, duration: 5.0)
            XCTAssertEqual(spans.first?.start ?? -1, 0, accuracy: 1e-9)
            XCTAssertEqual(spans.last?.end ?? -1, 5.0, accuracy: 1e-9)
        }
    }

    func testChapterWindowsAreTheGapsBeforeChapterSegments() {
        let segments = [AudioSegment(start: 0, end: 4),
                        AudioSegment(start: 5.8, end: 9, gapBefore: 1.8, gapKind: "chapter"),
                        AudioSegment(start: 9.6, end: 12, gapBefore: 0.6)]
        let w = CaptionTiming.chapterWindows(segments)
        XCTAssertEqual(w.count, 1, "an ordinary pause is not a card window")
        XCTAssertEqual(w[0].0, 4.0, accuracy: 1e-9)
        XCTAssertEqual(w[0].1, 5.8, accuracy: 1e-9)
        XCTAssertTrue(CaptionTiming.chapterWindows(nil).isEmpty)
    }
}

final class CaptionAlignmentTests: XCTestCase {
    private func t(_ text: String, _ s: Double, _ e: Double) -> WordTiming { WordTiming(text: text, start: s, end: e) }

    func testAlignmentIgnoresCaseAndPunctuation() {
        let m = CaptionTiming.align(script: ["Octopuses", "have", "three", "hearts."],
                                    transcript: ["octopuses,", "HAVE", "three", "hearts"])
        XCTAssertEqual(m, [0, 1, 2, 3])
    }

    func testMissedAndExtraTranscriptWordsAreSkipped() {
        XCTAssertEqual(CaptionTiming.align(script: ["a", "b", "c"], transcript: ["a", "c"]), [0, nil, 1])
        XCTAssertEqual(CaptionTiming.align(script: ["a", "b"], transcript: ["a", "um", "b"]), [0, 2])
        XCTAssertEqual(CaptionTiming.align(script: ["2000", "years"], transcript: ["two", "thousand", "years"]), [nil, 2],
                       "a number the recogniser spelled out simply doesn't match")
        XCTAssertEqual(CaptionTiming.align(script: [], transcript: ["a"]), [])
    }

    func testScriptWordsTakeTheTranscriptsTimesAndGapsAreInterpolated() {
        let words = ["Octopuses", "have", "three", "hearts."]
        let transcript = [t("octopuses", 0.1, 0.8), t("three", 1.2, 1.5), t("hearts", 1.6, 2.0)]
        let fallback = CaptionTiming.estimate(words, in: .init(start: 0, end: 4))
        let merged = CaptionTiming.merge(words: words, transcript: transcript, fallback: fallback, duration: 4)
        XCTAssertEqual(merged.matched, 0.75, accuracy: 1e-9)
        XCTAssertEqual(merged.spans[0].start, 0.1, accuracy: 1e-9)
        XCTAssertEqual(merged.spans[2].start, 1.2, accuracy: 1e-9)
        XCTAssertEqual(merged.spans[3].end, 2.0, accuracy: 1e-9)
        XCTAssertEqual(merged.spans[1].start, 0.8, accuracy: 1e-9, "'have' sits between its neighbours")
        XCTAssertEqual(merged.spans[1].end, 1.2, accuracy: 1e-9)
    }

    func testAnUnrelatedTranscriptIsNotBelieved() {
        let words = ["alpha", "beta", "gamma", "delta"]
        let fallback = CaptionTiming.estimate(words, in: .init(start: 0, end: 4))
        let merged = CaptionTiming.merge(words: words, transcript: [t("one", 0, 1), t("two", 1, 2)], fallback: fallback, duration: 4)
        XCTAssertEqual(merged.spans, fallback)
        XCTAssertEqual(merged.matched, 0)
    }

    func testCleanKeepsTimesMovingForwardWithAMinimumDuration() {
        let messy = [CaptionTiming.Span(start: 1.0, end: 1.0), .init(start: 0.5, end: 0.6), .init(start: 9, end: 9.5)]
        let out = CaptionTiming.clean(messy, duration: 5)
        XCTAssertGreaterThan(out[0].end, out[0].start)
        XCTAssertGreaterThanOrEqual(out[1].start, out[0].start, "never moves backwards")
        XCTAssertLessThanOrEqual(out[2].end, 5, "never past the end of the audio")
    }
}

final class SceneBoundaryAndCueTests: XCTestCase {
    private func w(_ text: String, scene: Int, run: Int = 0) -> VoicePlan.Word { .init(text: text, scene: scene, run: run) }
    private func s(_ a: Double, _ b: Double) -> CaptionTiming.Span { .init(start: a, end: b) }

    func testScenesStartAHairBeforeTheirFirstWord() {
        let words = [w("a", scene: 0), w("b", scene: 0), w("c", scene: 1)]
        let b = CaptionTiming.sceneBoundaries(words: words, spans: [s(0.2, 0.5), s(0.5, 1.0), s(3.0, 3.4)], sceneCount: 2, duration: 6)
        XCTAssertEqual(b.count, 3)
        XCTAssertEqual(b[0], 0)
        XCTAssertEqual(b[1], 2.9, accuracy: 1e-9)
        XCTAssertEqual(b[2], 6)
    }

    func testASceneWithNoWordsGetsZeroLengthAndNeverGoesBackwards() {
        let words = [w("a", scene: 0), w("c", scene: 2)]
        let b = CaptionTiming.sceneBoundaries(words: words, spans: [s(0.2, 0.5), s(4.0, 4.4)], sceneCount: 3, duration: 6)
        XCTAssertEqual(b[1], 3.9, accuracy: 1e-9)
        XCTAssertEqual(b[2], 3.9, accuracy: 1e-9)
        XCTAssertEqual(zip(b, b.dropFirst()).map { $1 - $0 }.reduce(0, +), 6, accuracy: 1e-9, "scene lengths add up to the audio")
        let early = CaptionTiming.sceneBoundaries(words: [w("a", scene: 0), w("b", scene: 1)], spans: [s(0, 0.1), s(0.05, 0.2)], sceneCount: 2, duration: 3)
        XCTAssertEqual(early[1], 0, accuracy: 1e-9, "the lead can't push a boundary before the previous one")
    }

    func testCuesBreakAtSentencesAndStayWithinTheWordLimit() {
        let words = ["Hello", "there.", "How", "are", "you?"].map { w($0, scene: 0) }
        let spans = [s(0, 0.3), s(0.3, 0.6), s(0.7, 0.9), s(0.9, 1.1), s(1.1, 1.4)]
        let cues = CaptionTiming.cues(words: words, spans: spans, maxWords: 3)
        XCTAssertEqual(cues.map(\.text), ["Hello there.", "How are you?"])
        XCTAssertEqual(cues[0].end, 0.7, accuracy: 1e-9, "lingers until the next cue, no further")
        XCTAssertEqual(cues[1].end, 1.58, accuracy: 1e-9, "the last cue lingers a moment")
        XCTAssertEqual(cues[1].words.count, 3)
        XCTAssertEqual(CaptionTiming.cues(words: words, spans: spans, maxWords: 2).map(\.text), ["Hello there.", "How are", "you?"])
    }

    func testCuesNeverCrossAPauseOrALongGap() {
        let words = ["one", "two", "three", "four"].map { w($0, scene: 0) }
        let spans = [s(0, 0.3), s(0.3, 0.6), s(2.0, 2.3), s(2.3, 2.6)]
        XCTAssertEqual(CaptionTiming.cues(words: words, spans: spans, maxWords: 4).map(\.text), ["one two", "three four"])
        let twoTakes = [w("a", scene: 0, run: 0), w("b", scene: 0, run: 1)]
        XCTAssertEqual(CaptionTiming.cues(words: twoTakes, spans: [s(0, 0.2), s(0.2, 0.4)], maxWords: 4).count, 2)
    }

    func testAOneWordCueStaysLongEnoughToRead() {
        let cues = CaptionTiming.cues(words: [w("Stop.", scene: 0)], spans: [s(1.0, 1.1)], maxWords: 3)
        XCTAssertEqual(cues[0].end, 1.35, accuracy: 1e-9)
    }

    func testCardWindowsSuppressOverlappingCues() {
        let words = ["a", "b"].map { w($0, scene: 0) }
        let cues = CaptionTiming.cues(words: words, spans: [s(0, 0.4), s(3, 3.4)], maxWords: 1, suppressing: [(2.5, 4.0)])
        XCTAssertEqual(cues.map(\.text), ["a"])
    }
}

@MainActor
final class ChapterTests: XCTestCase {
    func testPartsBecomeCardsBeforeTheirFirstSceneAndPartOneGetsNone() {
        let raw = """
        {"scenes": [],
         "parts": [
           {"part":1,"title":"Intro","sub":"Start here","scenes":[1,2,3]},
           {"part":2,"title":"Deep Dive","sub":"Where it gets weird","scenes":[4,5],"duration_sec":5},
           {"part":3,"title":"","scenes":[6]},
           {"part":4,"title":"Dup","scenes":[4]},
           {"part":5,"title":"Ghost","scenes":[99]}
         ]}
        """
        let chapters = CreativePrompts.parseChapters(raw, sceneCount: 6)
        XCTAssertEqual(chapters.count, 1)
        XCTAssertEqual(chapters[0].beforeScene, 4)
        XCTAssertEqual(chapters[0].title, "Deep Dive")
        XCTAssertEqual(chapters[0].subtitle, "Where it gets weird")
        XCTAssertEqual(chapters[0].duration, Chapter.maxDuration, "a 5s ask is held to the 3s cap")
        XCTAssertTrue(CreativePrompts.parseChapters("{\"scenes\": []}", sceneCount: 6).isEmpty)
        XCTAssertTrue(CreativePrompts.parseChapters("nonsense", sceneCount: 6).isEmpty)
    }

    func testTheDirectorPromptOnlyAsksForPartsWhenChaptersAreOn() {
        let inputs = CreativeInputs(topic: "t")
        let off = CreativePrompts.director1(inputs, sceneTexts: ["a", "b"])
        XCTAssertFalse(off.contains("CHAPTER STRUCTURE"))
        XCTAssertFalse(off.contains("\"parts\""))
        let on = CreativePrompts.director1(inputs, sceneTexts: Array(repeating: "x", count: 12), chapters: true)
        XCTAssertTrue(on.contains("CHAPTER STRUCTURE"))
        XCTAssertTrue(on.contains("\"parts\""))
        XCTAssertTrue(on.contains("Group the 12 scenes into 2 sequential PARTS"))
    }

    func testChapterGapsMapScenePositionsAndRespectTheChannelSwitch() {
        var p = Project(id: "p", title: "T", topic: "t")
        p.scenes = (1...4).map { VideoScene(index: $0, text: "s\($0)", imagePrompt: "") }
        p.chapters = [Chapter(beforeScene: 3, title: "Part", subtitle: nil, duration: 9)]
        var ch = Channel(name: "c")
        XCTAssertTrue(Pipeline.chapterGaps(p, channel: ch).isEmpty, "off unless the channel turns chapters on")
        ch.chaptersEnabled = true
        XCTAssertEqual(Pipeline.chapterGaps(p, channel: ch), [2: Chapter.maxDuration], "scene 3 is position 2, duration clamped")
    }

    func testChapterAndQuestionCardsOnTheSameSceneDoNotCollide() {
        let q = TakeoverCard(scene: 4, text: "Why?", start: 5, end: 7)
        let c = TakeoverCard(scene: 4, text: "Part", start: 3, end: 5, kind: "chapter")
        XCTAssertNotEqual(q.id, c.id)
        XCTAssertTrue(c.isChapter)
        XCTAssertFalse(q.isChapter)
    }
}

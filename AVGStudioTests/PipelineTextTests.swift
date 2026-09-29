import XCTest
@testable import AVGStudio

/// The pure text/timing helpers behind the pipeline. Every one of these
/// mirrors a function in the PC app's Python (each has a docstring saying
/// which), and each is where a silent off-by-one produces a video that is
/// subtly wrong rather than a crash - exactly what unit tests are for. The PC
/// side has ~1000 tests; the iOS app had none.
@MainActor
final class PipelineTextTests: XCTestCase {

    // MARK: [pause] handling

    func testSplitOnPauseConsumesTheMarker() {
        XCTAssertEqual(Pipeline.splitOnPause("One [pause] Two"), ["One", "Two"])
        XCTAssertEqual(Pipeline.splitOnPause("A [PAUSE] B [ pause ] C"), ["A", "B", "C"])
        XCTAssertEqual(Pipeline.splitOnPause("No markers here"), ["No markers here"])
        XCTAssertFalse(Pipeline.splitOnPause("x [pause] y").joined().contains("[pause]"))
    }

    func testSpokenRemovesDeliveryMarkersAndCollapsesWhitespace() {
        XCTAssertEqual(Pipeline.spoken("Hello [pause] world"), "Hello world")
        XCTAssertEqual(Pipeline.spoken("[[Big]] idea ((now))"), "Big idea now")
    }

    func testStripPauseMarkers() {
        XCTAssertEqual(Pipeline.stripPauseMarkers("A [pause] B").trimmed, "A B")
    }

    func testCleanNarrationStripsQuotesAndAsterisks() {
        XCTAssertEqual(Pipeline.cleanNarration("\"Hello *world*\""), "Hello world")
        XCTAssertEqual(Pipeline.cleanNarration("  plain  "), "plain")
    }

    func testExtractQuestionMarkers() {
        XCTAssertEqual(Pipeline.extractQuestionMarkers("Why? ((Is it real?)) Maybe ((  ))"), ["Is it real?"])
        XCTAssertEqual(Pipeline.extractQuestionMarkers("((one)) and ((two))"), ["one", "two"])
        XCTAssertTrue(Pipeline.extractQuestionMarkers("nothing here").isEmpty)
    }

    // MARK: Timing

    func testAllocateProportionalSplitsByCharacterCount() {
        let spans = Pipeline.allocateProportional(["aa", "bbbb"], total: 6)
        XCTAssertEqual(spans.count, 2)
        XCTAssertEqual(spans[0].0, 0, accuracy: 1e-9)
        XCTAssertEqual(spans[0].1, 2, accuracy: 1e-9)
        XCTAssertEqual(spans[1].0, 2, accuracy: 1e-9)
        XCTAssertEqual(spans[1].1, 6, accuracy: 1e-9)
    }

    func testAllocateProportionalHonoursOffsetAndFloorsEmptyUnits() {
        let shifted = Pipeline.allocateProportional(["aa", "bbbb"], total: 6, offset: 1)
        XCTAssertEqual(shifted.last!.1, 7, accuracy: 1e-9)
        // An empty unit weighs 1 character so it cannot swallow zero time.
        let withEmpty = Pipeline.allocateProportional(["", "abc"], total: 4)
        XCTAssertEqual(withEmpty[0].1 - withEmpty[0].0, 1, accuracy: 1e-9)
        XCTAssertEqual(withEmpty[1].1 - withEmpty[1].0, 3, accuracy: 1e-9)
    }

    func testSyntheticWordTimingsFillTheSceneWindow() {
        let words = Pipeline.syntheticWordTimings(["a bb"], spans: [(0, 3)])
        XCTAssertEqual(words.map(\.text), ["a", "bb"])
        XCTAssertEqual(words[0].end, 1, accuracy: 1e-9)
        XCTAssertEqual(words[1].start, 1, accuracy: 1e-9)
        XCTAssertEqual(words[1].end, 3, accuracy: 1e-9)
    }

    func testOverlapsIsHalfOpen() {
        XCTAssertTrue(Pipeline.overlaps(0, 2, 1, 3))
        XCTAssertFalse(Pipeline.overlaps(0, 1, 1, 2), "touching intervals do not overlap")
    }

    // MARK: Parsing model output

    func testExtractJSONObjectToleratesFencesAndProse() {
        XCTAssertEqual(Pipeline.extractJSONObject("```json\n{\"a\":1}\n```"), "{\"a\":1}")
        XCTAssertEqual(Pipeline.extractJSONObject("Sure! {\"a\":{\"b\":2}} hope that helps"), "{\"a\":{\"b\":2}}")
        XCTAssertNil(Pipeline.extractJSONObject("no json at all"))
        XCTAssertNil(Pipeline.extractJSONObject("} backwards {"))
    }

    func testParseScenesNumbersSequentiallyAfterDroppingBlankEntries() {
        let raw = "Here you go: [{\"text\":\"Hello\",\"image_prompt\":\"a cat\"},{\"text\":\"   \"},{\"text\":\"Bye\"}] done"
        let scenes = Pipeline.parseScenes(raw)
        XCTAssertEqual(scenes.map(\.text), ["Hello", "Bye"])
        XCTAssertEqual(scenes.map(\.index), [1, 2], "no hole where the blank scene was")
        XCTAssertEqual(scenes[0].imagePrompt, "a cat")
        XCTAssertEqual(scenes[1].imagePrompt, "")
    }

    func testParseScenesReturnsEmptyOnGarbage() {
        XCTAssertTrue(Pipeline.parseScenes("not json").isEmpty)
        XCTAssertTrue(Pipeline.parseScenes("[1, 2, 3]").isEmpty)
    }

    func testSentenceScenesFallback() {
        let scenes = Pipeline.sentenceScenes("One. Two. Three.", prompts: ["p1"], topic: "T")
        XCTAssertEqual(scenes.count, 3)
        XCTAssertEqual(scenes.map(\.index), [1, 2, 3])
        XCTAssertEqual(scenes[0].imagePrompt, "p1")
        XCTAssertEqual(scenes[1].imagePrompt, "T: Two.")
    }

    // MARK: Recycle pool

    func testRecycleIndexReadsLooseSpellings() {
        XCTAssertEqual(Pipeline.recycleIndex("Use [RECYCLE #3]"), 3)
        XCTAssertEqual(Pipeline.recycleIndex("recycle #12"), 12)
        XCTAssertNil(Pipeline.recycleIndex("a sunset over water"))
    }

    func testNormalizeRecyclePicksCanonicalisesAndStripsOutOfRange() {
        var scenes = [
            VideoScene(index: 1, text: "a", imagePrompt: "Use recycle #2 please"),
            VideoScene(index: 2, text: "b", imagePrompt: "sunset [RECYCLE #9]"),
            VideoScene(index: 3, text: "c", imagePrompt: "recycle #0"),
            VideoScene(index: 4, text: "d", imagePrompt: "plain prompt"),
        ]
        Pipeline.normalizeRecyclePicks(&scenes, poolSize: 3)
        XCTAssertEqual(scenes[0].imagePrompt, "[RECYCLE #2]")
        XCTAssertEqual(scenes[1].imagePrompt, "sunset")
        XCTAssertEqual(scenes[2].imagePrompt, "")
        XCTAssertEqual(scenes[3].imagePrompt, "plain prompt")
    }

    func testForceFillAllModeCyclesRoundRobin() {
        var scenes = [
            VideoScene(index: 1, text: "a", imagePrompt: "[RECYCLE #2]"),
            VideoScene(index: 2, text: "b", imagePrompt: "x"),
            VideoScene(index: 3, text: "c", imagePrompt: "y"),
            VideoScene(index: 4, text: "d", imagePrompt: "z"),
        ]
        Pipeline.forceFillAllMode(&scenes, poolSize: 2)
        XCTAssertEqual(scenes.map(\.imagePrompt), ["[RECYCLE #2]", "[RECYCLE #1]", "[RECYCLE #2]", "[RECYCLE #1]"])
    }

    func testForceFillAllModeIsANoOpWithAnEmptyPool() {
        var scenes = [VideoScene(index: 1, text: "a", imagePrompt: "keep me")]
        Pipeline.forceFillAllMode(&scenes, poolSize: 0)
        XCTAssertEqual(scenes[0].imagePrompt, "keep me")
    }
}

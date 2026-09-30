import XCTest
@testable import AVGStudio

/// `VideoRenderer.plan` decides what every output frame contains (which scene,
/// where it sits, whether a transition or a takeover card applies). Both the
/// GPU path and the CPU fallback draw from it, so these tests pin the one
/// thing they must never disagree about.
final class RenderPlanTests: XCTestCase {
    private let canvas = CGRect(x: 0, y: 0, width: 720, height: 1280)
    private let starts = [0.0, 2.0]
    private let sizes: [CGSize?] = [CGSize(width: 1000, height: 1000), CGSize(width: 1000, height: 1000)]

    private func input(directions: [SceneDirection] = [], cards: [TakeoverCard] = [], kenBurns: Bool = false) -> RenderInput {
        RenderInput(
            images: [nil, nil],
            sceneDurations: [2, 2],
            audio: URL(fileURLWithPath: "/tmp/none.m4a"),
            cues: [],
            size: canvas.size,
            fps: 30,
            kenBurns: kenBurns,
            kbIntensity: 1,
            sceneDirections: directions,
            captions: CaptionStyle(enabled: false, fontSize: 60, position: .bottom),
            cards: cards,
            output: URL(fileURLWithPath: "/tmp/out.mp4")
        )
    }

    private func plan(_ t: Double, _ input: RenderInput, sizes: [CGSize?]? = nil) -> FramePlan {
        VideoRenderer().plan(t: t, input: input, starts: starts, sizes: sizes ?? self.sizes, canvas: canvas)
    }

    func testPlainSceneCoversTheCanvasCentred() {
        let p = plan(0.5, input())
        XCTAssertNil(p.card)
        XCTAssertEqual(p.layers.count, 1)
        XCTAssertEqual(p.layers[0].index, 0)
        // A 1000x1000 image must be scaled x1.28 to cover 720x1280.
        let r = p.layers[0].rect
        XCTAssertEqual(r.width, 1280, accuracy: 0.001)
        XCTAssertEqual(r.height, 1280, accuracy: 0.001)
        XCTAssertEqual(r.minX, -280, accuracy: 0.001)
        XCTAssertEqual(r.minY, 0, accuracy: 0.001)
        XCTAssertEqual(p.fadeToBlack, 0)
    }

    func testTheNextSceneTakesOverAtItsStart() {
        let p = plan(2.1, input())
        XCTAssertEqual(p.layers.map(\.index), [1])
    }

    func testCrossfadeIntoTheNextSceneWithoutADirectorPlan() {
        let p = plan(1.8, input())
        XCTAssertEqual(p.layers.map(\.index), [0, 1])
        XCTAssertEqual(p.layers[0].alpha, 1, accuracy: 1e-9)
        XCTAssertEqual(p.layers[1].alpha, 1 - 0.2 / 0.35, accuracy: 1e-6)
    }

    func testTakeoverCardOwnsTheWholeFrame() {
        let card = TakeoverCard(scene: 1, text: "Why?", start: 1, end: 1.5)
        let p = plan(1.2, input(cards: [card]))
        XCTAssertEqual(p.card?.text, "Why?")
        XCTAssertTrue(p.layers.isEmpty)
        // Outside its window the scene is back.
        XCTAssertNil(plan(1.6, input(cards: [card])).card)
    }

    func testSlideInEntranceOffsetsTheSceneByOneCanvasWidth() {
        let dir = [
            SceneDirection(scene: 1, motionType: .still, transitionIn: .slideLeft),
            SceneDirection(scene: 2, motionType: .still, transitionIn: .cut),
        ]
        let p = plan(0, input(directions: dir, kenBurns: true))
        XCTAssertEqual(p.layers.count, 1)
        XCTAssertEqual(p.layers[0].rect.minX, -280 - 720, accuracy: 0.001)
    }

    func testFadeEntranceDarkensTheOpeningFramesThenClears() {
        let dir = [
            SceneDirection(scene: 1, motionType: .still, transitionIn: .fade),
            SceneDirection(scene: 2, motionType: .still, transitionIn: .cut),
        ]
        XCTAssertEqual(plan(0, input(directions: dir)).fadeToBlack, 1, accuracy: 1e-6)
        XCTAssertEqual(plan(0.175, input(directions: dir)).fadeToBlack, 0.25, accuracy: 1e-6)
        XCTAssertEqual(plan(0.5, input(directions: dir)).fadeToBlack, 0, accuracy: 1e-6)
    }

    func testMissingImageLeavesABlackFrameNotACrash() {
        XCTAssertTrue(plan(0.5, input(), sizes: [nil, nil]).layers.isEmpty)
    }

    func testRenderStatsSummaryNamesTheEngine() {
        var s = RenderStats()
        s.frames = 900; s.gpuFrames = 900; s.frameSeconds = 9
        XCTAssertTrue(s.summary.contains("on GPU at 100 fps"), s.summary)
        s.gpuFrames = 0
        XCTAssertTrue(s.summary.contains("on CPU"), s.summary)
        s.gpuFrames = 300
        XCTAssertTrue(s.summary.contains("GPU 300 / CPU 600"), s.summary)
    }
}

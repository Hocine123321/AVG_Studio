import UIKit
import XCTest
@testable import AVGStudio

final class PreviewFrameTests: XCTestCase {
    private var dir: URL!

    override func setUp() {
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("preview-\(UUID().uuidString)", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }
    override func tearDown() { try? FileManager.default.removeItem(at: dir) }

    /// One blue 90x160 scene lasting two seconds, no camera motion and no
    /// filter, so two frames differ only when something was drawn on top.
    private func input(cards: [TakeoverCard] = [], cues: [CaptionCue] = [], plan: EditPlan? = nil) throws -> RenderInput {
        let image = dir.appendingPathComponent("a.png")
        // Two tones, so enlarging or re-cropping the picture visibly changes pixels.
        let png = UIGraphicsImageRenderer(size: CGSize(width: 90, height: 160)).pngData { ctx in
            UIColor.systemBlue.setFill()
            ctx.fill(CGRect(x: 0, y: 0, width: 90, height: 160))
            UIColor.systemRed.setFill()
            ctx.fill(CGRect(x: 0, y: 0, width: 30, height: 80))
        }
        try png.write(to: image)
        let s = AppSettings()
        let ch = Channel(name: "T")
        return RenderInput(
            images: [image], sceneDurations: [2], audio: URL(fileURLWithPath: "/dev/null"), cues: cues,
            size: CGSize(width: 90, height: 160), fps: 10, kenBurns: false, kbIntensity: 0,
            captions: CaptionStyle(enabled: true, fontSize: CGFloat(s.captionFontSize), position: s.captionPosition,
                                   animation: s.captionAnimationStyle, accent: Theme.accent, accent1: Theme.accent,
                                   accent2: Theme.accent, fontFamily: ch.fontFamily, overlay: ch.overlay),
            cards: cards, output: dir.appendingPathComponent("unused.mp4"), editPlan: plan)
    }

    private func bytes(_ image: CGImage?) -> Data? {
        guard let data = image?.dataProvider?.data else { return nil }
        return data as Data
    }

    func testAFrameHasTheRenderSizeAndTheVideoHasItsLength() throws {
        let preview = VideoRenderer.Preview(try input())
        let frame = try XCTUnwrap(preview.frame(at: 0.2))
        XCTAssertEqual(frame.width, 90)
        XCTAssertEqual(frame.height, 160)
        XCTAssertEqual(preview.total, 2, accuracy: 1e-9)
    }

    func testTimesOutsideTheVideoAreClampedNotRefused() throws {
        let preview = VideoRenderer.Preview(try input())
        XCTAssertNotNil(preview.frame(at: -5))
        XCTAssertNotNil(preview.frame(at: 99))
    }

    func testACardOnlyShowsWhileItIsOnScreen() throws {
        let card = TakeoverCard(scene: 1000, text: "Hello", start: 0.5, end: 1.5, kind: "explainer")
        let preview = VideoRenderer.Preview(try input(cards: [card]))
        let before = bytes(preview.frame(at: 0.2))
        let during = bytes(preview.frame(at: 1.0))
        let after = bytes(preview.frame(at: 1.8))
        XCTAssertNotNil(before)
        XCTAssertNotEqual(before, during, "the card changes the picture")
        XCTAssertEqual(before, after, "and is gone once its time is over")
    }

    func testUpdatingCardsRedrawsWithoutReloadingImages() throws {
        let card = TakeoverCard(scene: 1000, text: "Hello", start: 0.5, end: 1.5, kind: "explainer")
        let preview = VideoRenderer.Preview(try input(cards: [card]))
        let plain = bytes(preview.frame(at: 0.2))
        XCTAssertNotEqual(bytes(preview.frame(at: 1.0)), plain)
        preview.update(cards: [], cues: [])
        XCTAssertEqual(bytes(preview.frame(at: 1.0)), plain, "no cards left, so the mid-video frame is just the picture")
    }

    func testACaptionLineIsDrawnWhileItIsSpoken() throws {
        let words = [WordTiming(text: "Hello", start: 0.5, end: 1.0), WordTiming(text: "world", start: 1.0, end: 1.5)]
        let cue = CaptionCue(start: 0.5, end: 1.5, text: "Hello world", words: words)
        let preview = VideoRenderer.Preview(try input(cues: [cue]))
        XCTAssertNotEqual(bytes(preview.frame(at: 0.2)), bytes(preview.frame(at: 1.0)))
        let edited = CaptionEdit.setText(cue, to: "Hello there")
        let beforeEdit = bytes(preview.frame(at: 1.2))
        preview.update(cards: [], cues: [edited])
        XCTAssertNotEqual(bytes(preview.frame(at: 1.2)), beforeEdit, "a retyped caption changes the frame")
    }

    func testAPunchInPushesTheFrameAndSettlesBack() throws {
        let preview = VideoRenderer.Preview(try input(plan: EditPlan(punches: [1.0])))
        let before = bytes(preview.frame(at: 0.2))
        XCTAssertNotEqual(bytes(preview.frame(at: 1.0)), before, "the picture is larger the instant the punch lands")
        XCTAssertEqual(bytes(preview.frame(at: 1.6)), before, "and is back to normal once the pulse is over")
    }

    func testAShotCutReframesThePictureFromThatMomentOn() throws {
        let cut = ShotCut(scene: 0, at: 1.0, framing: Framing(zoom: 1.5, fx: 0.25, fy: 0.5))
        let preview = VideoRenderer.Preview(try input(plan: EditPlan(cuts: [cut])))
        let before = bytes(preview.frame(at: 0.9))
        XCTAssertEqual(before, bytes(preview.frame(at: 0.2)), "before the cut the scene looks as it always did")
        XCTAssertNotEqual(bytes(preview.frame(at: 1.05)), before, "the cut changes the crop")
    }

    func testAnEmptyPlanChangesNothing() throws {
        let plain = VideoRenderer.Preview(try input())
        let empty = VideoRenderer.Preview(try input(plan: EditPlan()))
        XCTAssertEqual(bytes(plain.frame(at: 1.0)), bytes(empty.frame(at: 1.0)))
    }
}

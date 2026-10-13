import XCTest
@testable import AVGStudio

/// The glass tabs' gesture decisions. The one that matters: a tap must not
/// pick the thumb up (it would teleport instead of gliding).
final class SegmentedGeometryTests: XCTestCase {

    private let g = SegmentedGeometry(width: 300, count: 3)    // segments of 100

    func testATapIsNotADrag() {
        XCTAssertFalse(SegmentedGeometry.beginsDrag(.zero))
        XCTAssertFalse(SegmentedGeometry.beginsDrag(CGSize(width: 3, height: 1)), "a finger's natural wobble")
        XCTAssertFalse(SegmentedGeometry.beginsDrag(CGSize(width: -6, height: 0)), "exactly at the threshold is still a tap")
    }

    func testAClearSidewaysMoveBeginsADrag() {
        XCTAssertTrue(SegmentedGeometry.beginsDrag(CGSize(width: 7, height: 0)))
        XCTAssertTrue(SegmentedGeometry.beginsDrag(CGSize(width: -40, height: 10)))
    }

    func testAMostlyVerticalMoveIsNeverADrag() {
        XCTAssertFalse(SegmentedGeometry.beginsDrag(CGSize(width: 10, height: 30)))
        XCTAssertTrue(SegmentedGeometry.wasScroll(CGSize(width: 10, height: 30)))
        XCTAssertFalse(SegmentedGeometry.wasScroll(CGSize(width: 40, height: 30)), "diagonal but mostly sideways is a drag")
        XCTAssertFalse(SegmentedGeometry.wasScroll(CGSize(width: 0, height: 10)), "a small vertical wobble is still a tap")
    }

    func testIndexAtClampsToTheControl() {
        XCTAssertEqual(g.index(at: 0), 0)
        XCTAssertEqual(g.index(at: 99), 0)
        XCTAssertEqual(g.index(at: 100), 1)
        XCTAssertEqual(g.index(at: 299), 2)
        XCTAssertEqual(g.index(at: -50), 0)
        XCTAssertEqual(g.index(at: 900), 2)
    }

    func testRestingThumbSitsOnTheSelectedSegment() {
        XCTAssertEqual(g.thumbX(selected: 0, dragCenter: nil), 0)
        XCTAssertEqual(g.thumbX(selected: 2, dragCenter: nil), 200)
    }

    func testADraggedThumbFollowsTheFingerAndStaysInside() {
        XCTAssertEqual(g.thumbX(selected: 0, dragCenter: 150), 100, "centred on the finger")
        XCTAssertEqual(g.thumbX(selected: 0, dragCenter: -40), 0, "cannot leave the left edge")
        XCTAssertEqual(g.thumbX(selected: 0, dragCenter: 999), 200, "cannot leave the right edge")
    }

    func testPickingUpTheThumbDoesNotMakeItJump() {
        // Selected segment 1 spans 100...200 (centre 150). Touching at 170 grabs it 20pt right of centre.
        XCTAssertEqual(g.grabOffset(start: 170, selected: 1), 20)
        // Touching a different segment grabs the thumb by its centre.
        XCTAssertEqual(g.grabOffset(start: 20, selected: 1), 0)
        XCTAssertEqual(g.grabOffset(start: 150, selected: 1), 0)
    }

    func testNoSegmentsOrNoWidthIsHarmless() {
        let empty = SegmentedGeometry(width: 0, count: 0)
        XCTAssertEqual(empty.segment, 0)
        XCTAssertEqual(empty.index(at: 10), 0)
        XCTAssertEqual(empty.thumbX(selected: 0, dragCenter: 5), 0)
        XCTAssertEqual(SegmentedGeometry(width: 200, count: 0).index(at: 50), 0)
    }

    func testTwoSegments() {
        let two = SegmentedGeometry(width: 200, count: 2)
        XCTAssertEqual(two.index(at: 99), 0)
        XCTAssertEqual(two.index(at: 100), 1)
        XCTAssertEqual(two.thumbX(selected: 1, dragCenter: nil), 100)
    }
}

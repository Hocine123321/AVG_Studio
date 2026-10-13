import Foundation
import CoreGraphics

/// The arithmetic behind the glass segmented control, kept out of the view so
/// it can be tested: a gesture cannot be unit-tested, but every decision it
/// makes can.
///
/// The decision that mattered: a TAP and a DRAG are different gestures. The
/// first version picked the thumb up the moment a finger landed, so a tap
/// teleported the thumb under the finger instead of letting it glide to the
/// new segment - the control felt like a row of buttons. Now nothing is picked
/// up until the finger has clearly moved sideways; a tap just selects (and the
/// view animates that), and a drag carries the thumb.
struct SegmentedGeometry: Equatable {
    var width: CGFloat
    var count: Int

    var segment: CGFloat { count > 0 ? width / CGFloat(count) : 0 }

    /// Sideways travel before a touch counts as a drag, and it must be more
    /// sideways than vertical (a scroll that starts on the control is not one).
    static let dragThreshold: CGFloat = 6
    /// Vertical travel that means "this was a scroll, not a choice".
    static let scrollThreshold: CGFloat = 24

    static func beginsDrag(_ translation: CGSize) -> Bool {
        abs(translation.width) > dragThreshold && abs(translation.width) > abs(translation.height)
    }

    static func wasScroll(_ translation: CGSize) -> Bool {
        abs(translation.height) >= scrollThreshold && abs(translation.height) > abs(translation.width)
    }

    /// The segment under x, clamped to the control.
    func index(at x: CGFloat) -> Int {
        guard segment > 0 else { return 0 }
        return min(max(Int(x / segment), 0), count - 1)
    }

    /// Where the thumb's leading edge is: on the selected segment, or under
    /// the finger (clamped to stay inside the control) while dragging.
    func thumbX(selected: Int, dragCenter: CGFloat?) -> CGFloat {
        guard let c = dragCenter else { return CGFloat(selected) * segment }
        return min(max(c - segment / 2, 0), max(width - segment, 0))
    }

    /// If the touch began ON the thumb, how far from its centre - so picking
    /// it up does not make it jump. A touch that began elsewhere grabs the
    /// thumb by its centre.
    func grabOffset(start: CGFloat, selected: Int) -> CGFloat {
        let centre = CGFloat(selected) * segment + segment / 2
        return abs(start - centre) <= segment / 2 ? start - centre : 0
    }
}

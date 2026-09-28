import SwiftUI
import UIKit

enum Theme {
    static let accent = Color(red: 1.0, green: 0.43, blue: 0.32)
    static let violet = Color(red: 0.52, green: 0.38, blue: 1.0)
    static let teal = Color(red: 0.2, green: 0.78, blue: 0.82)

    /// Parses a "RRGGBB" (optionally "#RRGGBB") hex string into a Color, or
    /// nil for anything blank/unparsable - the channel-level brand accent
    /// (`Channel.accentColorHex`) is optional, and "no brand colour set"
    /// must cleanly fall back to `Theme.accent`, not draw a black swatch.
    static func color(hex: String) -> Color? {
        var s = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.hasPrefix("#") { s.removeFirst() }
        guard s.count == 6, let v = UInt32(s, radix: 16) else { return nil }
        let r = Double((v >> 16) & 0xFF) / 255.0
        let g = Double((v >> 8) & 0xFF) / 255.0
        let b = Double(v & 0xFF) / 255.0
        return Color(red: r, green: g, blue: b)
    }

    static func hex(from color: Color) -> String {
        let ui = UIColor(color)
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        ui.getRed(&r, green: &g, blue: &b, alpha: &a)
        return String(format: "%02X%02X%02X", Int(r * 255), Int(g * 255), Int(b * 255))
    }
}

/// A soft, continuously-flowing colour field behind the glass - the app's
/// answer to Google Gemini's own moving backdrop.
///
/// Rewritten from scratch (was `MeshGradient`-based): a 3x3 mesh pins its
/// four outer control points to the exact viewport corners by
/// construction, and interpolates a smooth surface FROM them - on a tall,
/// narrow phone viewport (nothing like the square-ish canvases mesh
/// gradients are usually demoed on) that surface can collapse into a
/// visibly flat, almost-unblended wedge of a single corner colour before
/// it has room to mix toward the next control point, which is exactly the
/// "solid white/black corner patches" bug this was rewritten to fix - not
/// a one-line tweak, a wrong tool for this aspect ratio.
///
/// New approach: independent soft "blobs" - each an analytic
/// `RadialGradient` circle (colour at its centre fading smoothly to fully
/// transparent at its own edge, well before that edge reaches the
/// viewport bounds) drifting in slow, mutually-irrational orbits over a
/// flat base fill. Nothing here is a convolution filter (no `.blur()`)
/// sampling pixels outside the layer, and no control point is ever pinned
/// to a corner - so a corner the animation drifts away from just shows
/// the plain base colour, never a hard-edged patch, regardless of screen
/// aspect ratio, size class, or how far into an orbit any given blob is.
///
/// Respects Reduce Motion: a user who has it on gets ONE still frame (at
/// t=0), not a paused-mid-animation frame.
struct AppBackground: View {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// One drifting colour blob. `base`/`orbit*` are fractions of the
    /// viewport's own size (from `GeometryReader`, not a normalized mesh
    /// grid) - the actual reason this can't reproduce the old bug: a
    /// blob's position and size are always relative to the REAL frame
    /// being drawn into, on every aspect ratio.
    private struct Blob {
        let colorPhase: Double // this blob's own phase into the accent->violet->teal drift
        let baseX: Double, baseY: Double // 0...1, orbit center
        let orbitX: Double, orbitY: Double // 0...1, how far it wanders from centre
        let speed: Double
        let phase: Double // offsets this blob's own orbit, so no two ever sync up
        let sizeFraction: Double // of max(width, height)
    }

    private static let blobs: [Blob] = [
        Blob(colorPhase: 0.0, baseX: 0.22, baseY: 0.16, orbitX: 0.14, orbitY: 0.09, speed: 0.055, phase: 0.6, sizeFraction: 0.62),
        Blob(colorPhase: 1.8, baseX: 0.80, baseY: 0.22, orbitX: 0.12, orbitY: 0.11, speed: 0.048, phase: 2.1, sizeFraction: 0.68),
        Blob(colorPhase: 3.4, baseX: 0.28, baseY: 0.84, orbitX: 0.13, orbitY: 0.12, speed: 0.041, phase: 4.0, sizeFraction: 0.64),
        Blob(colorPhase: 5.0, baseX: 0.76, baseY: 0.80, orbitX: 0.11, orbitY: 0.10, speed: 0.063, phase: 1.2, sizeFraction: 0.58),
    ]

    var body: some View {
        GeometryReader { proxy in
            if reduceMotion {
                field(t: 0, size: proxy.size, scheme: scheme)
            } else {
                // Was `Float(tl.date.timeIntervalSinceReferenceDate)` further
                // down this file's history - that property is ~788
                // million-plus by now (2026), and Float's 24-bit mantissa
                // only has ~64-unit resolution at that magnitude, freezing
                // the animation for ~64 real seconds at a time then jumping.
                // `t` stays Double end to end here; only ever cast to
                // CGFloat for the final on-screen position/size, where
                // Double has plenty of precision at that magnitude.
                TimelineView(.animation(minimumInterval: 1.0 / 15.0)) { tl in
                    field(t: tl.date.timeIntervalSinceReferenceDate, size: proxy.size, scheme: scheme)
                }
            }
        }
        .ignoresSafeArea()
    }

    /// One brand hue slowly rotated toward its neighbour and back - `phase`
    /// offsets where in that rotation each blob sits, so the whole field
    /// never settles on a single dominant tone.
    private func drift(_ base: Color, phase: Double, t: Double) -> Color {
        let cycle = (sin(t * 0.05 + phase) + 1) / 2 // 0...1, ~125s period
        return base.mix(with: nextHue(base), by: cycle * 0.4)
    }

    private func nextHue(_ base: Color) -> Color {
        if base == Theme.accent { return Theme.violet }
        if base == Theme.violet { return Theme.teal }
        return Theme.accent
    }

    private func field(t: Double, size: CGSize, scheme: ColorScheme) -> some View {
        let base = scheme == .dark ? Color(white: 0.05) : Color(white: 0.97)
        let dim = max(size.width, size.height)
        // Cycle the three brand hues across the four blob slots rather than
        // hard-assigning one each, so which colour sits where itself drifts.
        let hues = [Theme.accent, Theme.violet, Theme.teal]
        return ZStack {
            base
            ForEach(Array(Self.blobs.enumerated()), id: \.offset) { i, b in
                let color = drift(hues[i % hues.count], phase: b.colorPhase, t: t)
                let cx = (b.baseX + b.orbitX * sin(t * b.speed + b.phase)) * size.width
                let cy = (b.baseY + b.orbitY * cos(t * b.speed * 0.82 + b.phase)) * size.height
                let blobDim = dim * b.sizeFraction
                Circle()
                    .fill(RadialGradient(
                        colors: [color.opacity(scheme == .dark ? 0.55 : 0.6), color.opacity(0)],
                        center: .center, startRadius: 0, endRadius: blobDim / 2
                    ))
                    .frame(width: blobDim, height: blobDim)
                    .position(x: cx, y: cy)
                    .blendMode(scheme == .dark ? .plusLighter : .normal)
            }
        }
        .compositingGroup()
    }
}

/// Tiny shared haptic helper - one call site per feeling rather than every
/// screen constructing/priming its own `UIFeedbackGenerator`. Feedback is
/// deliberately used sparingly: only on moments the user actively
/// initiated (create, render, save, archive/delete), never ambient state
/// changes like a progress tick.
enum Haptics {
    static func light() { UIImpactFeedbackGenerator(style: .light).impactOccurred() }
    static func success() { UINotificationFeedbackGenerator().notificationOccurred(.success) }
    static func warning() { UINotificationFeedbackGenerator().notificationOccurred(.warning) }
    static func error() { UINotificationFeedbackGenerator().notificationOccurred(.error) }
}

extension View {
    func glassCard(_ radius: CGFloat = 26, padding: CGFloat = 16) -> some View {
        self.padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .glassEffect(.regular, in: .rect(cornerRadius: radius))
    }

    func screenBackground() -> some View {
        self.scrollContentBackground(.hidden)
            .background(AppBackground())
    }
}

struct StatusBadge: View {
    let status: PhaseStatus
    var body: some View {
        Label(text, systemImage: symbol)
            .font(.caption.weight(.semibold))
            .foregroundStyle(color)
            .padding(.horizontal, 10).padding(.vertical, 5)
            .glassEffect(.regular.tint(color.opacity(0.25)), in: .capsule)
    }
    private var text: String {
        switch status { case .pending: "Draft"; case .running: "Rendering"; case .done: "Ready"; case .failed: "Failed" }
    }
    private var symbol: String {
        switch status { case .pending: "circle.dashed"; case .running: "bolt.fill"; case .done: "checkmark.circle.fill"; case .failed: "exclamationmark.triangle.fill" }
    }
    private var color: Color {
        switch status { case .pending: .secondary; case .running: Theme.accent; case .done: .green; case .failed: .red }
    }
}

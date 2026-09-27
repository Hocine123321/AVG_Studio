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
/// answer to Google Gemini's own moving backdrop, built from the same
/// `MeshGradient` bones the previous version used but with the motion and
/// palette doing more work: control points drift in loose, overlapping
/// orbits (never repeating on a visible cycle - each point's period is
/// mutually irrational-ish relative to the others) AND every colour's hue
/// itself slowly rotates through the three brand tones, so the blend
/// keeps evolving, not just where it sits.
///
/// Respects Reduce Motion: a user who has it on gets ONE still frame of
/// the same gradient (evaluated at t=0), not a paused-mid-animation frame -
/// this was already the standard for Welcome's entrance animation but the
/// background itself previously ignored the setting entirely.
struct AppBackground: View {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        if reduceMotion {
            content(t: 0, scheme: scheme).ignoresSafeArea()
        } else {
            TimelineView(.animation(minimumInterval: 1.0 / 15.0)) { tl in
                content(t: Float(tl.date.timeIntervalSinceReferenceDate), scheme: scheme)
                    .ignoresSafeArea()
            }
        }
    }

    /// One brand hue slowly rotated toward its neighbour and back - `phase`
    /// offsets where in that rotation each colour slot sits, so the whole
    /// mesh never settles on a single "dominant" tone.
    private func drift(_ base: Color, phase: Double, t: Float) -> Color {
        let cycle = (sin(Double(t) * 0.05 + phase) + 1) / 2 // 0…1, ~125s period
        return base.mix(with: nextHue(base), by: cycle * 0.4)
    }

    private func nextHue(_ base: Color) -> Color {
        if base == Theme.accent { return Theme.violet }
        if base == Theme.violet { return Theme.teal }
        return Theme.accent
    }

    private func content(t: Float, scheme: ColorScheme) -> some View {
        let base = scheme == .dark ? Color(white: 0.05) : Color(white: 0.97)
        return MeshGradient(
            width: 3, height: 3,
            points: [
                SIMD2<Float>(0.06 * sin(t * 0.09), 0.05 * cos(t * 0.07)),
                SIMD2<Float>(0.5 + 0.05 * sin(t * 0.11 + 1.3), 0.06 * sin(t * 0.08)),
                SIMD2<Float>(1 - 0.06 * sin(t * 0.06 + 2.1), 0.05 * cos(t * 0.1)),
                SIMD2<Float>(0.05 * cos(t * 0.08 + 0.7), 0.45 + 0.16 * sin(t * 0.13)),
                SIMD2<Float>(0.5 + 0.22 * sin(t * 0.1), 0.5 + 0.2 * cos(t * 0.085)),
                SIMD2<Float>(1 - 0.05 * cos(t * 0.07 + 1.6), 0.55 + 0.16 * cos(t * 0.12)),
                SIMD2<Float>(0.06 * sin(t * 0.075 + 2.6), 1 - 0.05 * cos(t * 0.09)),
                SIMD2<Float>(0.5 + 0.05 * cos(t * 0.095 + 0.4), 1 - 0.06 * sin(t * 0.065)),
                SIMD2<Float>(1 - 0.06 * cos(t * 0.085 + 1.9), 1 - 0.05 * sin(t * 0.11)),
            ],
            colors: [
                drift(Theme.accent, phase: 0.0, t: t).opacity(0.55), base, drift(Theme.violet, phase: 1.4, t: t).opacity(0.45),
                base, drift(Theme.violet, phase: 2.8, t: t).opacity(0.35), base,
                drift(Theme.teal, phase: 4.2, t: t).opacity(0.35), base, drift(Theme.accent, phase: 5.6, t: t).opacity(0.4),
            ]
        )
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

import SwiftUI

enum Theme {
    static let accent = Color(red: 1.0, green: 0.43, blue: 0.32)
    static let violet = Color(red: 0.52, green: 0.38, blue: 1.0)
    static let teal = Color(red: 0.2, green: 0.78, blue: 0.82)
}

/// Slowly drifting mesh behind the glass, so Liquid Glass has color to refract.
struct AppBackground: View {
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 15.0)) { tl in
            let t = Float(tl.date.timeIntervalSinceReferenceDate)
            let base = scheme == .dark ? Color(white: 0.05) : Color(white: 0.97)
            MeshGradient(
                width: 3, height: 3,
                points: [
                    SIMD2<Float>(0, 0), SIMD2<Float>(0.5, 0), SIMD2<Float>(1, 0),
                    SIMD2<Float>(0, 0.45 + 0.1 * sin(t * 0.21)),
                    SIMD2<Float>(0.5 + 0.18 * sin(t * 0.17), 0.5 + 0.16 * cos(t * 0.13)),
                    SIMD2<Float>(1, 0.55 + 0.1 * cos(t * 0.19)),
                    SIMD2<Float>(0, 1), SIMD2<Float>(0.5, 1), SIMD2<Float>(1, 1),
                ],
                colors: [
                    Theme.accent.opacity(0.55), base, Theme.violet.opacity(0.45),
                    base, Theme.violet.opacity(0.35), base,
                    Theme.teal.opacity(0.35), base, Theme.accent.opacity(0.4),
                ]
            )
            .ignoresSafeArea()
        }
    }
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

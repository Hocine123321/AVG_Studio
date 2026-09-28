import SwiftUI
import UIKit

/// Skin v2 - see `ios/SKIN_V2_PLAN.md`. Every value below is a direct port
/// of the owner's HTML mock's `:root` custom properties (and its dark-mode
/// override block), not a fresh design - if a color/shape/timing looks odd,
/// check the mock before assuming this file drifted from it.
enum Theme {
    /// Resolves per the environment's light/dark trait, matching the mock's
    /// `:root` / `prefers-color-scheme:dark` pair - the same technique
    /// `UIColor(dynamicProvider:)` always uses, just spelled out so every
    /// token below reads as one plain line.
    private static func dyn(_ light: UIColor, _ dark: UIColor) -> Color {
        Color(UIColor { $0.userInterfaceStyle == .dark ? dark : light })
    }
    private static func rgb(_ r: Int, _ g: Int, _ b: Int, _ a: CGFloat = 1) -> UIColor {
        UIColor(red: CGFloat(r) / 255, green: CGFloat(g) / 255, blue: CGFloat(b) / 255, alpha: a)
    }

    // --bg1 / --bg2 - the diagonal gradient AppBackground paints under
    // everything else.
    static let bg1 = dyn(rgb(244, 238, 229), rgb(20, 17, 13))
    static let bg2 = dyn(rgb(228, 238, 219), rgb(27, 36, 24))
    // --ink / --i2 (secondary text, ink at 60%/62% alpha)
    static let ink = dyn(rgb(36, 26, 18), rgb(246, 239, 230))
    static let ink2 = dyn(rgb(36, 26, 18, 0.6), rgb(246, 239, 230, 0.62))
    // --gl (glass fill) / --ed (glass edge) / --sh (glass shadow) / --fl
    // (subtle fill - segmented-inactive bg, switch track, chip bg)
    static let glassFill = dyn(rgb(255, 255, 255, 0.5), rgb(255, 255, 255, 0.09))
    static let glassEdge = dyn(rgb(255, 255, 255, 0.75), rgb(255, 255, 255, 0.22))
    static let glassShadow = dyn(rgb(90, 60, 30, 0.16), rgb(0, 0, 0, 0.45))
    static let fill = dyn(rgb(36, 26, 18, 0.07), rgb(255, 255, 255, 0.08))
    // --acc / --ok / --bad - accent is a single hue now (was accent/violet/
    // teal); --ok and --bad are the only other named colors in the mock.
    static let accent = Color(red: 0.878, green: 0.541, blue: 0.306) // #e08a4e, same both schemes
    static let ok = dyn(rgb(111, 135, 87), rgb(174, 191, 146))
    static let bad = Color(red: 0.769, green: 0.329, blue: 0.290) // #c4544a, same both schemes

    /// Kept only for screens Skin v2 hasn't reached yet (`HomeView`'s card
    /// gradient, `WelcomeView`'s feature icons) - not part of the new
    /// palette above. Retire the call site, not this constant, once that
    /// screen's own phase in `SKIN_V2_PLAN.md` lands.
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

    /// Registers the bundled Caprasimo font (OFL-licensed,
    /// `Resources/`, listed in `UIAppFonts` so iOS loads it at launch
    /// already - this call is a defensive double-check, cheap and
    /// idempotent, in case that Info.plist route ever silently fails on a
    /// given OS version) so `Font.disp` can find it by PostScript name.
    static func registerFonts() {
        guard UIFont(name: "Caprasimo-Regular", size: 12) == nil,
              let url = Bundle.main.url(forResource: "Caprasimo-Regular", withExtension: "ttf")
        else { return }
        CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
    }
}

extension Font {
    /// The mock's `--disp` stack's primary face ("Caprasimo","New York",
    /// ui-serif, Georgia, serif) - every screen title (`.lt`) and sheet
    /// heading (`.sh3`), regular weight only, the mock never bolds it.
    /// Falls back to the system serif (closest native analogue to the
    /// mock's own fallback, "New York") if the bundled font isn't found -
    /// belt and suspenders, shouldn't actually trigger once registered.
    static func disp(_ size: CGFloat) -> Font {
        if UIFont(name: "Caprasimo-Regular", size: size) != nil {
            return .custom("Caprasimo-Regular", size: size)
        }
        return .system(size: size, weight: .regular, design: .serif)
    }
}

/// A soft, continuously-flowing colour field behind the glass - the
/// mock's own two-orb `.orb`/`.orb.a`/`.orb.b` + the diagonal `#app`
/// gradient, not Apple's Liquid Glass or a `MeshGradient`; ported to a
/// `TimelineView` since CSS animations have no native SwiftUI equivalent.
///
/// Deliberately simpler than the palette this replaced (which used four
/// drifting radial blobs and a three-hue rotation): the mock specifies
/// exactly two large blurred circles, fixed brand colours, one shared
/// motion curve. Matching the mock precisely means matching ITS
/// simplicity too, not keeping the old version's extra motion.
///
/// Respects Reduce Motion: a user who has it on gets ONE still frame (at
/// t=0), not a paused-mid-animation frame - the mock's own
/// `@media(prefers-reduced-motion:reduce){*{animation:none}}` rule.
struct AppBackground: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        GeometryReader { proxy in
            if reduceMotion {
                field(t: 0, size: proxy.size)
            } else {
                TimelineView(.animation(minimumInterval: 1.0 / 15.0)) { tl in
                    field(t: tl.date.timeIntervalSinceReferenceDate, size: proxy.size)
                }
            }
        }
        .ignoresSafeArea()
    }

    /// One 0...1 cycle of the mock's `dr` keyframe (`translate(-40px,60px)
    /// scale(1.15)`, 18s ease-in-out, `alternate`) - a smooth sine
    /// oscillation stands in for CSS's exact cubic-bezier easing, close
    /// enough to read the same. `phase` (radians) is `.pi` apart for the
    /// two orbs, mirroring the mock's orb.b `animation-delay:-9s` - exactly
    /// half of the 18s period, i.e. a half-cycle head start.
    private func drift(_ t: Double, phase: Double) -> Double {
        (sin(t * (2 * .pi / 18) + phase) + 1) / 2
    }

    private func field(t: Double, size: CGSize) -> some View {
        ZStack {
            LinearGradient(colors: [Theme.bg1, Theme.bg2], startPoint: .topLeading, endPoint: .bottomTrailing)
            orb(color: Theme.accent, top: -80, right: -90, opacity: 0.45, phase: 0, t: t, size: size)
            orb(color: Color(red: 0.682, green: 0.749, blue: 0.573), bottom: 120, left: -120, opacity: 0.3, phase: .pi, t: t, size: size)
        }
    }

    /// `top`/`right` (or `bottom`/`left`) are the mock's own raw CSS values
    /// (sign included) for `.orb.a`/`.orb.b` - a 300pt circle positioned by
    /// two of its four edges relative to the container, never centred, so
    /// its actual centre has to be derived from whichever pair is given
    /// (CSS `right`/`bottom` measure INWARD from that edge; a negative
    /// value pushes the box back out past it, off-screen).
    private func orb(color: Color, top: CGFloat? = nil, right: CGFloat? = nil, bottom: CGFloat? = nil, left: CGFloat? = nil, opacity: Double, phase: Double, t: Double, size: CGSize) -> some View {
        let dim: CGFloat = 300
        let x: CGFloat = left.map { $0 + dim / 2 } ?? right.map { size.width - $0 - dim / 2 } ?? size.width / 2
        let y: CGFloat = top.map { $0 + dim / 2 } ?? bottom.map { size.height - $0 - dim / 2 } ?? size.height / 2
        let d = drift(t, phase: phase)
        return Circle()
            .fill(color)
            .frame(width: dim, height: dim)
            .blur(radius: 70)
            .opacity(opacity)
            .scaleEffect(1.0 + 0.15 * d)
            .position(x: x + (-40 * d), y: y + (60 * d))
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

/// The mock's `.g` glass recipe, hand-rolled rather than Apple's Liquid
/// Glass (`.glassEffect`): a translucent fill over a blur, a hairline
/// edge, an inset top highlight + a soft outer shadow, and a diagonal
/// white sheen (`.g::after`). Liquid Glass is a real, better-integrated
/// system material, but it's a black box with no fill-color/blur-radius/
/// border knobs to expose - it can't be tuned to reproduce a specific
/// hand-designed look. "Pixel for pixel" means matching the mock's actual
/// recipe by hand instead.
private struct GlassSurface<S: InsettableShape>: ViewModifier {
    let shape: S

    func body(content: Content) -> some View {
        content
            .background {
                ZStack {
                    // `backdrop-filter: blur(26px) saturate(190%)` -
                    // `.ultraThinMaterial` is the closest built-in stand-in;
                    // exact blur radius/saturation aren't independently
                    // tunable the way CSS's are.
                    shape.fill(.ultraThinMaterial)
                    shape.fill(Theme.glassFill)
                    // `.g::after`'s diagonal sheen.
                    shape.fill(
                        LinearGradient(colors: [.white.opacity(0.3), .white.opacity(0)], startPoint: .topLeading, endPoint: UnitPoint(x: 0.6, y: 0.6))
                    ).opacity(0.5)
                }
            }
            .overlay(shape.strokeBorder(Theme.glassEdge, lineWidth: 0.5))
            // `inset 0 1px 0 rgba(255,255,255,.35)` - a top highlight line;
            // stroking the full shape then masking to a sliver at the top
            // (rather than trying to draw just one edge) keeps this simple
            // and correct regardless of the shape's actual geometry.
            .overlay(
                shape.stroke(Color.white.opacity(0.35), lineWidth: 1)
                    .mask(LinearGradient(colors: [.white, .clear], startPoint: .top, endPoint: UnitPoint(x: 0.5, y: 0.06)))
            )
            .clipShape(shape)
            .compositingGroup()
            // `0 10px 30px var(--sh)` - a soft, downward-offset drop shadow.
            .shadow(color: Theme.glassShadow, radius: 15, x: 0, y: 10)
    }
}

extension View {
    /// The mock's `.card`/`.g` panel: rounded glass, no default padding
    /// beyond what `.card`/`.card.p` add at each call site.
    func glassCard(_ radius: CGFloat = 24, padding: CGFloat = 16) -> some View {
        self.padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .modifier(GlassSurface(shape: RoundedRectangle(cornerRadius: radius, style: .continuous)))
    }

    /// The mock's `.g` on a pill/circle shape - tab bar, segmented
    /// control, chips, round icon buttons.
    func glassPill() -> some View {
        self.modifier(GlassSurface(shape: Capsule(style: .continuous)))
    }

    func glassCircle() -> some View {
        self.modifier(GlassSurface(shape: Circle()))
    }

    /// The mock's `.chip`: small pill label (`background:var(--fl)`, a
    /// hairline inset border, no blur) - status/count badges, not a full
    /// glass surface.
    func chipStyle() -> some View {
        self
            .font(.system(size: 11.5, weight: .semibold))
            .padding(.horizontal, 9).padding(.vertical, 3)
            .background(Theme.fill, in: .capsule)
            .overlay(Capsule().strokeBorder(Theme.glassEdge, lineWidth: 0.5))
    }
}

/// The mock's `.toast`: a transient top-center pill notice that fades in
/// and auto-dismisses (`toast()`/`setTimeout(...,2300)`) - a primitive the
/// app didn't have before Skin v2. Attach with `.toast($message)`; set the
/// binding to a string to show it, it clears itself back to nil after the
/// mock's own 2.3s.
private struct ToastModifier: ViewModifier {
    @Binding var message: String?
    @State private var dismissTask: Task<Void, Never>?

    func body(content: Content) -> some View {
        content.overlay(alignment: .top) {
            if let message {
                Text(message)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Theme.ink)
                    .padding(.horizontal, 16).padding(.vertical, 10)
                    .glassPill()
                    .padding(.top, 58)
                    .transition(.opacity.animation(.easeOut(duration: 0.2)))
                    .onAppear {
                        dismissTask?.cancel()
                        dismissTask = Task {
                            try? await Task.sleep(for: .seconds(2.3))
                            if !Task.isCancelled { self.message = nil }
                        }
                    }
            }
        }
    }
}

extension View {
    func toast(_ message: Binding<String?>) -> some View {
        modifier(ToastModifier(message: message))
    }
}

/// The mock's `.sheet`/`.grab`/`.sh3`: a bottom glass panel with its own
/// grab handle and Caprasimo heading, presented over a dim scrim - built
/// as content for SwiftUI's OWN native `.sheet(isPresented:)` (which
/// already gives keyboard avoidance, swipe-to-dismiss and accessibility
/// for free) rather than hand-rolling the mock's custom scrim+overlay
/// system from scratch. `title` is optional since a couple of the mock's
/// sheets (the model picker) put their own header content inline instead.
struct GlassSheet<Content: View>: View {
    var title: String?
    @ViewBuilder var content: Content

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                Capsule().fill(Theme.ink2).frame(width: 38, height: 5).opacity(0.5)
                    .frame(maxWidth: .infinity).padding(.bottom, 4)
                if let title { Text(title).font(.disp(24)).foregroundStyle(Theme.ink) }
                content
            }
            .padding(.horizontal, 18).padding(.top, 10).padding(.bottom, 22)
        }
        .scrollBounceBehavior(.basedOnSize)
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.hidden)
        .presentationCornerRadius(34)
        .presentationBackground {
            Theme.bg1.opacity(0.62).background(.ultraThinMaterial)
        }
    }
}

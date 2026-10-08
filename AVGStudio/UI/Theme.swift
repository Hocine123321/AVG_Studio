import SwiftUI
import UIKit

/// Skin v2 - see `ios/SKIN_V2_PLAN.md`. Every value below is a direct port
/// of the owner's HTML mock's `:root` custom properties (and its dark-mode
/// override block), not a fresh design - if a color/shape/timing looks odd,
/// check the mock before assuming this file drifted from it.
private struct GlassLevelKey: EnvironmentKey { static let defaultValue: Double = 1.0 }
extension EnvironmentValues {
    /// App-wide glass opacity as an ENVIRONMENT value (set once at the root),
    /// so every glass view is invalidated the moment it changes. (A plain
    /// static is invisible to SwiftUI - that is why the setting once needed
    /// an app restart - and the static has since been deleted outright.)
    var glassLevel: Double {
        get { self[GlassLevelKey.self] }
        set { self[GlassLevelKey.self] = newValue }
    }
}

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
    /// Solid-enough panel behind long text in pop-up windows (the log), so it
    /// stays readable whatever the glass transparency is set to.
    static let logPanel = dyn(rgb(255, 250, 242, 0.82), rgb(20, 16, 12, 0.72))
    static let ink = dyn(rgb(36, 26, 18), rgb(246, 239, 230))
    static let ink2 = dyn(rgb(36, 26, 18, 0.6), rgb(246, 239, 230, 0.62))
    // --gl (glass fill) / --ed (glass edge) / --sh (glass shadow) / --fl
    // (subtle fill - segmented-inactive bg, switch track, chip bg)
    /// Glass fill at an opacity `level` of 0.15...1.0 (Settings → App). The
    /// level always arrives through `EnvironmentValues.glassLevel`; the old
    /// `Theme.glassLevel` static, mutated from inside `RootView.body`, is
    /// gone - a side effect during view evaluation that SwiftUI could not
    /// see (and that needed `nonisolated(unsafe)` to compile).
    static func glassFill(_ level: Double) -> Color {
        dyn(rgb(255, 255, 255, 0.5 * level), rgb(255, 255, 255, 0.09 * level))
    }
    static let glassEdge = dyn(rgb(255, 255, 255, 0.75), rgb(255, 255, 255, 0.22))
    static let glassShadow = dyn(rgb(90, 60, 30, 0.16), rgb(0, 0, 0, 0.45))
    static let fill = dyn(rgb(36, 26, 18, 0.07), rgb(255, 255, 255, 0.08))
    // --acc / --ok / --bad - accent is a single hue now (was accent/violet/
    // teal); --ok and --bad are the only other named colors in the mock.
    static let accent = Color(red: 0.878, green: 0.541, blue: 0.306) // #e08a4e, same both schemes
    static let ok = dyn(rgb(111, 135, 87), rgb(174, 191, 146))
    static let bad = Color(red: 0.769, green: 0.329, blue: 0.290) // #c4544a, same both schemes
    /// The mock's `#2a1508` - dark ink used ON TOP of solid accent fills
    /// (`.btn.pri`'s text, `.pi.lv`'s icon, a selected `.dot`'s checkmark),
    /// never a surface colour itself, same both schemes.
    static let onAccent = Color(red: 0.165, green: 0.082, blue: 0.031)
    /// The mock's `#1d2614` - the same idea as `onAccent` but for a solid
    /// `ok`-coloured fill (`.pi.ok`'s checkmark), same both schemes.
    static let onOk = Color(red: 0.114, green: 0.149, blue: 0.078)


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
    // The animated field blurs two 300pt circles many times a second on every
    // screen - real GPU/battery cost, and it competes with the video encoder
    // during a render. Low Power Mode gets the same still frame Reduce Motion
    // does.
    @State private var lowPower = ProcessInfo.processInfo.isLowPowerModeEnabled
    // Two full-screen blurred circles redrawn 60x a second fight the video
    // encoder for the same chip. While any render or pipeline is running the
    // field holds still.
    @Environment(Store.self) private var store

    var body: some View {
        GeometryReader { proxy in
            if reduceMotion || lowPower || !store.jobs.isEmpty {
                field(t: 0, size: proxy.size)
            } else {
                TimelineView(.animation(minimumInterval: 1.0 / 15.0)) { tl in
                    field(t: tl.date.timeIntervalSinceReferenceDate, size: proxy.size)
                }
            }
        }
        .ignoresSafeArea()
        .onReceive(NotificationCenter.default.publisher(for: .NSProcessInfoPowerStateDidChange)) { _ in
            lowPower = ProcessInfo.processInfo.isLowPowerModeEnabled
        }
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

/// Controls and floating bars use Apple's real Liquid Glass (`liquidGlass`
/// below); this hand-rolled recipe remains for large CONTENT panels (cards,
/// grouped rows), where a tunable fill - the Appearance "glass opacity"
/// setting - matters more than the system material. Apple's own guidance is
/// glass for the controls/navigation layer, not for content.
///
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
    @Environment(\.glassLevel) private var level

    func body(content: Content) -> some View {
        content
            .background {
                ZStack {
                    // `backdrop-filter: blur(26px) saturate(190%)` -
                    // `.ultraThinMaterial` is the closest built-in stand-in;
                    // exact blur radius/saturation aren't independently
                    // tunable the way CSS's are.
                    shape.fill(.ultraThinMaterial).opacity(level)
                    shape.fill(Theme.glassFill(level))
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
            // No `.compositingGroup()` here on purpose: it forces this
            // whole subtree into an isolated offscreen layer, and
            // `.ultraThinMaterial` above can no longer sample the REAL
            // live content behind it once isolated that way - it falls
            // back to a flat, noticeably more opaque/tinted color instead
            // of an actual blurred window onto the animated background,
            // which is exactly the "too tinted, not transparent" bug.
            // `.clipShape` above already bounds the shadow to one shape,
            // so the shadow doesn't need the isolation compositingGroup
            // would otherwise give it.
            // `0 10px 30px var(--sh)` - a soft, downward-offset drop shadow.
            .shadow(color: Theme.glassShadow, radius: 15, x: 0, y: 10)
    }
}

extension View {
    /// Apple's real Liquid Glass: the system material, which refracts what is
    /// behind it, reacts to touch when `interactive`, honours Reduce
    /// Transparency and the Tinted/Clear appearance, and morphs between
    /// siblings inside a `GlassEffectContainer`. Use it for controls and
    /// floating bars (search, composer, selection bar, chips, round buttons);
    /// it is a black box, so it ignores the "glass opacity" setting.
    func liquidGlass<S: Shape>(_ shape: S, tint: Color? = nil, interactive: Bool = false) -> some View {
        var glass = Glass.regular
        if let tint { glass = glass.tint(tint) }
        if interactive { glass = glass.interactive() }
        return self.glassEffect(glass, in: shape)
    }

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

    /// Any other shape needing the full glass recipe - the mock's own
    /// `.seg`/`.selbar` (rounded rect, not a card/pill/circle) reuse this
    /// rather than getting a fifth bespoke extension each.
    func glass<S: InsettableShape>(_ shape: S) -> some View {
        self.modifier(GlassSurface(shape: shape))
    }

    /// The mock's `.field`: a flat translucent input background - fill +
    /// hairline edge only, no blur/shadow/sheen (search boxes, text
    /// fields aren't full `.g` glass panels in the mock).
    func fieldStyle(_ radius: CGFloat = 18) -> some View {
        self.modifier(FieldGlass(radius: radius))
    }

    /// The mock's `.h2`: small semibold secondary-color label above a
    /// card group ("Pipeline", "Takes", "API keys"...) - plain system
    /// font, NOT the Caprasimo display face (that's screen titles and
    /// sheet headings only).
    func sectionHeader() -> some View {
        self.font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.ink2)
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

    /// Every scrollable/Form-based screen sits on `AppBackground` with the
    /// system's own opaque list/form backing hidden - unchanged by Skin v2,
    /// still needed on every screen regardless of which phase has restyled
    /// its foreground content so far.
    func screenBackground() -> some View {
        self.scrollContentBackground(.hidden)
            .background(AppBackground())
    }
}

/// The mock's `.seg.g`: a custom pill-of-pills filter control (Library's
/// All/Running/Done/Archived, Settings' Channel/Global, Create's aspect
/// picker) - NOT SwiftUI's native `.segmented` picker style, which has a
/// different look entirely. The whole strip is a glass rounded rect; the
/// selected option gets its own flat `Theme.fill` + hairline-edge chip.
struct SegmentedFilter<T: Hashable>: View {
    let options: [T]
    let label: (T) -> String
    @Binding var selection: T
    /// 8 keeps the compact 34pt strip Library/Create use; Settings passes 12
    /// for ~44pt segments a thumb can hit without aiming.
    var verticalPadding: CGFloat = 8
    @Namespace private var glassNamespace
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        // The selected segment is a real Liquid Glass capsule; because every
        // segment lives in one GlassEffectContainer and the thumb carries a
        // shared `glassEffectID`, changing the selection MORPHS the glass
        // from one segment to the next instead of cross-fading two fills.
        GlassEffectContainer(spacing: 4) {
            HStack(spacing: 4) {
                ForEach(options, id: \.self) { option in
                    let isOn = option == selection
                    ZStack {
                        if isOn {
                            Capsule()
                                .fill(.clear)
                                .glassEffect(.regular.tint(Theme.accent.opacity(0.22)).interactive(), in: .capsule)
                                .glassEffectID("segment-thumb", in: glassNamespace)
                        }
                        Button {
                            guard !isOn else { return }
                            Haptics.light()
                            withAnimation(reduceMotion ? nil : .smooth(duration: 0.3)) { selection = option }
                        } label: {
                            Text(label(option))
                                .font(.system(size: 13.5, weight: .semibold))
                                .foregroundStyle(isOn ? Theme.ink : Theme.ink2)
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, verticalPadding)
                                .contentShape(.capsule)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
        .padding(3)
        .background(Capsule().fill(Theme.fill))
        .overlay(Capsule().strokeBorder(Theme.glassEdge, lineWidth: 0.5))
        .accessibilityElement(children: .contain)
    }
}

/// The mock's `.chip` (`background:var(--fl)`, a hairline border, ONLY the
/// text tinted by status - not the fill) - ported status pill, still used
/// by `HomeView` until its own Skin v2 phase restyles the whole card.
struct StatusBadge: View {
    let status: PhaseStatus
    var body: some View {
        Label(text, systemImage: symbol)
            .foregroundStyle(color)
            .chipStyle()
    }
    private var text: String {
        switch status { case .pending: "Draft"; case .running: "Rendering"; case .done: "Ready"; case .failed: "Failed" }
    }
    private var symbol: String {
        switch status { case .pending: "circle.dashed"; case .running: "bolt.fill"; case .done: "checkmark.circle.fill"; case .failed: "exclamationmark.triangle.fill" }
    }
    private var color: Color {
        switch status { case .pending: Theme.ink2; case .running: Theme.accent; case .done: Theme.ok; case .failed: Theme.bad }
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
        .glassSheetBackground(scroll: false)
    }
}


private struct GlassSheetBackground: ViewModifier {
    @Environment(\.glassLevel) private var level
    var scroll: Bool
    func body(content: Content) -> some View {
        content
            .scrollContentBackground(scroll ? .hidden : .automatic)
            .presentationCornerRadius(34)
            .presentationBackground {
                Theme.bg1.opacity(0.62 * level).background(.ultraThinMaterial.opacity(level))
            }
    }
}

extension View {
    /// Same glass backdrop `GlassSheet` uses, for sheets built on a plain
    /// `Form` (ChannelEditor, key editor) so every pop-up window follows
    /// the one app-wide glass-opacity setting.
    func glassSheetBackground(scroll: Bool = true) -> some View {
        modifier(GlassSheetBackground(scroll: scroll))
    }
}

private struct FieldGlass: ViewModifier {
    @Environment(\.glassLevel) private var level
    let radius: CGFloat
    func body(content: Content) -> some View {
        content.padding(12)
            .background(Theme.glassFill(level), in: RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: radius, style: .continuous).strokeBorder(Theme.glassEdge, lineWidth: 0.5))
    }
}

// MARK: App font

private struct AppFontKey: EnvironmentKey { static let defaultValue: AppFontChoice = .system }
extension EnvironmentValues {
    var appFontChoice: AppFontChoice {
        get { self[AppFontKey.self] }
        set { self[AppFontKey.self] = newValue }
    }
}

extension Font {
    /// The user's chosen UI font at `size`: system, or the mock's Caprasimo.
    static func app(_ size: CGFloat, weight: Font.Weight = .regular, choice: AppFontChoice) -> Font {
        choice == .caprasimo ? .disp(size) : .system(size: size, weight: weight)
    }
}

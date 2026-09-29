import SwiftUI

/// First-run flow: five short steps instead of one long scroll.
///
///   0  Hero            - what the app does
///   1  How it works    - the five real pipeline steps (`PhaseID`)
///   2  First channel   - name + niche (what the old single screen collected)
///   3  Your AI         - what works with no key, plus optional keys
///   4  All set         - summary, then straight into Create
///
/// Design rules carried over from the rest of the app: one filled
/// (`.glassProminent`) call to action per screen, the accent used as
/// identity rather than decoration, every colour from `Theme`, and Reduce
/// Motion swaps the slide for a plain cross-fade.
///
/// Everything the previous `WelcomeView` did on "Get started" still happens,
/// once, in `finish(to:)`: the channel is created/activated, the writer falls
/// back to Pollinations where Apple Intelligence is unavailable, and
/// `onboarded` flips so `RootView` swaps in the tab bar.
struct WelcomeView: View {
    @Environment(Store.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @ScaledMetric(relativeTo: .largeTitle) private var titleSize: CGFloat = 34

    @State private var step = 0
    @State private var forward = true
    @State private var channelName = "My Channel"
    @State private var niche = ""
    @State private var savedKeys: Set<APIKey> = []
    @State private var editingKey: APIKey?
    @FocusState private var field: Field?

    private enum Field: Hashable { case name, niche }

    private let lastStep = 4
    /// The keys worth offering up front. Everything else lives in Settings.
    private let optionalKeys: [APIKey] = [.groq, .gemini, .openai, .elevenlabs]

    // MARK: Body

    var body: some View {
        ZStack(alignment: .topLeading) {
            AppBackground()

            ScrollView {
                ZStack {
                    page
                        .id(step)
                        .transition(pageTransition)
                }
                .frame(maxWidth: .infinity)
                .padding(.horizontal, 22)
                .padding(.top, 64)
                .padding(.bottom, 12)
            }
            .scrollBounceBehavior(.basedOnSize)
            .scrollDismissesKeyboard(.interactively)

            if step > 0 {
                Button(action: goBack) {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(Theme.ink)
                        .frame(width: 44, height: 44)
                        .glassCircle()
                }
                .buttonStyle(.plain)
                .padding(.leading, 16)
                .padding(.top, 8)
                .accessibilityLabel("Back")
            }
        }
        .safeAreaInset(edge: .bottom) { bottomBar }
        .sheet(item: $editingKey) { key in
            APIKeySheet(key: key) { refreshSavedKeys() }
        }
        .onAppear { refreshSavedKeys() }
    }

    @ViewBuilder private var page: some View {
        switch step {
        case 0: hero
        case 1: howItWorks
        case 2: channelStep
        case 3: aiStep
        default: allSet
        }
    }

    private var pageTransition: AnyTransition {
        if reduceMotion { return .opacity }
        return .asymmetric(
            insertion: .move(edge: forward ? .trailing : .leading).combined(with: .opacity),
            removal: .opacity
        )
    }

    // MARK: Step 0 - hero

    private var hero: some View {
        VStack(spacing: 18) {
            fan.padding(.top, 8)
            titleText("Turn a topic into a video").multilineTextAlignment(.center)
            leadText("Give AVG Studio an idea. It writes the script, draws every scene, records the narration and renders the finished video, all on your iPhone.")
                .multilineTextAlignment(.center)
        }
    }

    /// Three fanned 9:16 scene frames: the picture of "a topic becomes
    /// scenes becomes a video".
    private var fan: some View {
        ZStack {
            sceneFrame(hue: 340, size: CGSize(width: 120, height: 210), playing: false)
                .rotationEffect(.degrees(-11)).offset(x: -80, y: 14)
            sceneFrame(hue: 28, size: CGSize(width: 120, height: 210), playing: false)
                .rotationEffect(.degrees(11)).offset(x: 80, y: 14)
            sceneFrame(hue: 200, size: CGSize(width: 132, height: 232), playing: true)
        }
        .frame(height: 250)
        .accessibilityHidden(true)
    }

    private func sceneFrame(hue: Double, size: CGSize, playing: Bool) -> some View {
        let top = Color(hue: hue / 360, saturation: 0.60, brightness: 0.62)
        let bottom = Color(hue: ((hue + 50).truncatingRemainder(dividingBy: 360)) / 360, saturation: 0.50, brightness: 0.22)
        return RoundedRectangle(cornerRadius: 24, style: .continuous)
            .fill(LinearGradient(colors: [top, bottom], startPoint: .topLeading, endPoint: .bottomTrailing))
            .overlay(RoundedRectangle(cornerRadius: 24, style: .continuous).strokeBorder(Theme.glassEdge, lineWidth: 0.5))
            .overlay {
                if playing {
                    Image(systemName: "play.fill")
                        .font(.system(size: 38))
                        .foregroundStyle(.white.opacity(0.9))
                }
            }
            .frame(width: size.width, height: size.height)
            .shadow(color: Theme.glassShadow, radius: 15, x: 0, y: 10)
    }

    // MARK: Step 1 - how it works

    private var howItWorks: some View {
        VStack(alignment: .leading, spacing: 14) {
            titleText("How it works")
            leadText("Five steps run in order. You can redo any of them later from Studio.")
            VStack(spacing: 0) {
                ForEach(PhaseID.allCases) { phase in
                    if phase.order > 0 { hairline }
                    HStack(alignment: .top, spacing: 12) {
                        Image(systemName: phase.symbol)
                            .font(.footnote.weight(.semibold))
                            .foregroundStyle(Theme.accent)
                            .frame(width: 34, height: 34)
                            .background(Theme.accent.opacity(0.16), in: .circle)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(phase.title).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.ink)
                            Text(Self.blurb(for: phase)).font(.caption).foregroundStyle(Theme.ink2)
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(.vertical, 10)
                    .accessibilityElement(children: .combine)
                }
            }
            .padding(.horizontal, 14)
            .glassCard(24, padding: 6)
        }
    }

    private static func blurb(for phase: PhaseID) -> String {
        switch phase {
        case .writing: return "Your writer drafts a brief, the narration and the scenes."
        case .visuals: return "One image per scene, or one from your reusable pool."
        case .voice: return "Narration recorded with real pauses and dead air trimmed."
        case .captions: return "Word-timed captions, plus full-screen cards for big questions."
        case .render: return "Camera motion, colour grade and export, all on this iPhone."
        }
    }

    // MARK: Step 2 - first channel

    private var channelStep: some View {
        VStack(alignment: .leading, spacing: 14) {
            titleText("Set up your first channel")
            leadText("A channel holds your tone, format and brand. You can add more, or change this, any time.")
            VStack(spacing: 10) {
                TextField("Channel name", text: $channelName)
                    .focused($field, equals: .name)
                    .submitLabel(.next)
                    .textInputAutocapitalization(.words)
                    .onSubmit { field = .niche }
                    .fieldStyle(14)
                TextField("What's it about? (e.g. space facts)", text: $niche)
                    .focused($field, equals: .niche)
                    .submitLabel(.done)
                    .onSubmit { field = nil }
                    .fieldStyle(14)
            }
        }
    }

    // MARK: Step 3 - your AI

    private var aiStep: some View {
        VStack(alignment: .leading, spacing: 14) {
            titleText("Ready without a key")
            leadText("These work out of the box. Add a key only if you want a different writer, image maker or voice.")
            VStack(spacing: 0) {
                includedRow("Writer", writerName)
                hairline
                includedRow("Images", ImageProviderKind.pollinations.label)
                hairline
                includedRow("Voice", VoiceProviderKind.apple.label)
            }
            .padding(.horizontal, 14)
            .glassCard(24, padding: 6)

            Text("Optional keys").sectionHeader().padding(.leading, 6).padding(.top, 4)
            VStack(spacing: 0) {
                ForEach(Array(optionalKeys.enumerated()), id: \.element) { i, key in
                    if i > 0 { hairline }
                    Button { editingKey = key } label: {
                        HStack {
                            Text(key.label).foregroundStyle(Theme.ink)
                            Spacer()
                            KeyStatusPill(saved: savedKeys.contains(key))
                        }
                        .padding(.vertical, 12)
                        .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 14)
            .glassCard(24, padding: 6)

            Text("Keys stay in your iPhone's Keychain, on this device only. Add or change them any time in Settings.")
                .font(.system(size: 12.5)).foregroundStyle(Theme.ink2)
                .padding(.horizontal, 4)
        }
    }

    private func includedRow(_ label: String, _ value: String) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.ok)
            Text(label).foregroundStyle(Theme.ink)
            Spacer()
            Text(value).font(.callout).foregroundStyle(Theme.ink2).lineLimit(1)
        }
        .padding(.vertical, 12)
        .accessibilityElement(children: .combine)
    }

    /// Apple Intelligence when the device has it, otherwise the free online
    /// writer `finish(to:)` will select.
    private var writerName: String {
        (AppleText.isAvailable ? TextProviderKind.apple : TextProviderKind.pollinations).label
    }

    // MARK: Step 4 - all set

    private var allSet: some View {
        VStack(spacing: 18) {
            Image(systemName: "checkmark")
                .font(.system(size: 40, weight: .bold))
                .foregroundStyle(Theme.ok)
                .frame(width: 88, height: 88)
                .glassCircle()
                .padding(.top, 8)
                .accessibilityHidden(true)
            titleText("You're all set").multilineTextAlignment(.center)
            leadText("Start with a topic, or open your library.").multilineTextAlignment(.center)
            VStack(spacing: 0) {
                summaryRow("Channel", resolvedChannelName)
                hairline
                summaryRow("Writer", writerName)
                hairline
                summaryRow("Narrator", VoiceProviderKind.apple.label)
                hairline
                summaryRow("Keys", savedKeys.isEmpty ? "None yet" : "\(savedKeys.count) connected")
            }
            .padding(.horizontal, 14)
            .glassCard(24, padding: 6)
        }
    }

    private func summaryRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).foregroundStyle(Theme.ink)
            Spacer()
            Text(value).foregroundStyle(Theme.ink2).lineLimit(1)
        }
        .padding(.vertical, 12)
        .accessibilityElement(children: .combine)
    }

    // MARK: Bottom bar

    private var bottomBar: some View {
        VStack(spacing: 12) {
            HStack(spacing: 7) {
                ForEach(0...lastStep, id: \.self) { i in
                    Capsule()
                        .fill(i == step ? Theme.accent : Theme.fill)
                        .overlay(Capsule().strokeBorder(Theme.glassEdge, lineWidth: i == step ? 0 : 0.5))
                        .frame(width: i == step ? 22 : 7, height: 7)
                }
            }
            .animation(reduceMotion ? nil : .spring(response: 0.3, dampingFraction: 0.85), value: step)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Step \(step + 1) of \(lastStep + 1)")

            Button(action: primaryAction) {
                Text(primaryTitle).font(.headline).frame(maxWidth: .infinity).padding(.vertical, 6)
            }
            .buttonStyle(.glassProminent)

            if step == 3 && savedKeys.isEmpty {
                secondaryButton("Skip for now", action: goForward)
            }
            if step == lastStep {
                secondaryButton("Open library") { finish(to: .library) }
            }
        }
        .padding(.horizontal, 20)
        .padding(.top, 10)
        .padding(.bottom, 8)
    }

    private func secondaryButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).font(.subheadline.weight(.semibold)).frame(maxWidth: .infinity).padding(.vertical, 4)
        }
        .buttonStyle(.glass)
    }

    private var primaryTitle: String {
        switch step {
        case 0: return "Get started"
        case lastStep: return "Create your first video"
        default: return "Continue"
        }
    }

    private func primaryAction() {
        step == lastStep ? finish(to: .create) : goForward()
    }

    // MARK: Actions

    private func goForward() {
        Haptics.light()
        field = nil
        forward = true
        withAnimation(reduceMotion ? nil : .spring(response: 0.42, dampingFraction: 0.88)) { step = min(step + 1, lastStep) }
    }

    private func goBack() {
        Haptics.light()
        field = nil
        forward = false
        withAnimation(reduceMotion ? nil : .spring(response: 0.42, dampingFraction: 0.88)) { step = max(step - 1, 0) }
    }

    private func refreshSavedKeys() {
        savedKeys = Set(APIKey.allCases.filter { Keychain.has($0) })
    }

    private var resolvedChannelName: String {
        channelName.trimmed.isEmpty ? "My Channel" : channelName.trimmed
    }

    /// The single place first-run state is committed - identical in effect to
    /// the old "Get started" button, plus choosing where the user lands.
    private func finish(to destination: AppTab) {
        var channel = store.activeChannel
        channel.name = resolvedChannelName
        channel.niche = niche.trimmed
        store.upsert(channel)
        store.activate(channel)
        if !AppleText.isAvailable { store.settings.text = .pollinations }
        field = nil
        Haptics.success()
        store.tab = destination
        withAnimation(.spring) { store.settings.onboarded = true }
    }
}
import SwiftUI

/// The mock's `ch()` screen: a "Global defaults" entry row (its own card,
/// globe glyph - bridges to Settings since the Channel/Global mode
/// toggle lives there, real once Phase 7 re-merges Settings back to one
/// page), then every channel as a ROW inside ONE shared card (hairlines
/// between rows, not each channel in its own floating card the way
/// Library's project grid works), then a plain "New channel" button.
/// Tapping a row's main content ACTIVATES that channel (mock's `act`);
/// a separate trailing chevron opens the editor (mock's `edch`) - two
/// distinct tap targets, not the old single "tap row = edit, plus a
/// button = activate" split.
struct ChannelsView: View {
    @Environment(Store.self) private var store
    @State private var editing: Channel?

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                Button {
                    Haptics.light()
                    store.tab = .settings
                } label: {
                    HStack(spacing: 12) {
                        Image(systemName: "globe").font(.title3).foregroundStyle(Theme.accent)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Global defaults").font(.subheadline.weight(.semibold)).foregroundStyle(Theme.ink)
                            Text("Used when a channel leaves a value unset").font(.caption).foregroundStyle(Theme.ink2)
                        }
                        Spacer(minLength: 8)
                        Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(Theme.ink2)
                    }
                    .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .glassCard(24, padding: 14)

                VStack(spacing: 0) {
                    ForEach(Array(store.channels.enumerated()), id: \.element.id) { i, c in
                        if i > 0 { hairline }
                        channelRow(c)
                    }
                }
                .padding(.horizontal, 14)
                .glassCard(24, padding: 6)

                Button {
                    Haptics.light()
                    editing = Channel(name: "New Channel")
                } label: {
                    Label("New channel", systemImage: "plus").frame(maxWidth: .infinity).padding(.vertical, 4)
                }
                .buttonStyle(.glass)
            }
            .padding(16)
        }
        .screenBackground()
        .navigationTitle("Channels")
        .sheet(item: $editing) { c in ChannelEditor(channel: c) }
    }

    private var hairline: some View {
        Rectangle().fill(Theme.glassEdge).frame(height: 0.5)
    }

    @ViewBuilder private func channelRow(_ c: Channel) -> some View {
        HStack(spacing: 10) {
            Button {
                Haptics.light()
                withAnimation(.spring) { store.activate(c) }
            } label: {
                HStack(spacing: 12) {
                    ZStack {
                        Circle().fill(c.id == store.activeChannelID ? (Theme.color(hex: c.accentColorHex) ?? Theme.accent) : Theme.fill)
                        if c.id == store.activeChannelID {
                            Image(systemName: "checkmark").font(.system(size: 13, weight: .bold)).foregroundStyle(Theme.onAccent)
                        }
                    }
                    .frame(width: 32, height: 32)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(c.name).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.ink)
                        Text("\(c.niche.isEmpty ? "General" : c.niche) · \(c.targetSeconds)s · \(c.aspectRatio.rawValue)")
                            .font(.caption).foregroundStyle(Theme.ink2)
                    }
                    Spacer(minLength: 0)
                }
                .contentShape(.rect)
            }
            .buttonStyle(.plain)

            Button { editing = c } label: {
                Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(Theme.ink2)
                    .frame(width: 30, height: 30)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Edit \(c.name)")
        }
        .padding(.vertical, 8)
        .contextMenu {
            Button("Edit", systemImage: "pencil") { editing = c }
            Button("Delete", systemImage: "trash", role: .destructive) { store.deleteChannel(c) }
                .disabled(store.channels.count < 2)
        }
    }
}

struct ChannelEditor: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State var channel: Channel

    private func soundBinding(_ moment: SFXMoment) -> Binding<String?> {
        Binding(
            get: { channel.sfxMap[moment.rawValue] },
            set: { channel.sfxMap[moment.rawValue] = $0 }
        )
    }

    private var accentBinding: Binding<Color> {
        Binding(
            get: { Theme.color(hex: channel.accentColorHex) ?? Theme.accent },
            set: { channel.accentColorHex = Theme.hex(from: $0) }
        )
    }
    private var captionColorBinding: Binding<Color> {
        Binding(
            get: { Theme.color(hex: channel.overlay.captionTextColorHex) ?? .white },
            set: { channel.overlay.captionTextColorHex = Theme.hex(from: $0) }
        )
    }
    private var accent1Binding: Binding<Color> {
        Binding(
            get: { Theme.color(hex: channel.accent1ColorHex) ?? Theme.color(hex: channel.accentColorHex) ?? Theme.accent },
            set: { channel.accent1ColorHex = Theme.hex(from: $0) }
        )
    }
    private var accent2Binding: Binding<Color> {
        Binding(
            get: { Theme.color(hex: channel.accent2ColorHex) ?? Theme.color(hex: channel.accentColorHex) ?? Theme.accent },
            set: { channel.accent2ColorHex = Theme.hex(from: $0) }
        )
    }

    /// Matches the PC's own font_family shortlist, swapped for families
    /// that actually exist on iOS (the PC's list - Segoe UI, Calibri - is
    /// Windows-only and would silently no-op here). "" keeps the system
    /// font; "custom" reveals a free-text field for any other installed
    /// family name.
    /// One "Models" table row (PC mockup: provider+model / backup / if-both-
    /// fail per phase) as a Form Section. `provider`/`backup` are `nil` by
    /// default - "use the channel's global default text provider" - and
    /// the model id for whichever provider IS picked still comes from
    /// Settings' own per-provider fields (e.g. Gemini model), matching the
    /// simplification documented on Channel's phase-override properties.
    // Phase 3 polish (ios/UI_POLISH_PLAN.md): these 4 phases were 4
    // separate Sections, each repeating the same header/footer chrome for
    // 3 near-identical pickers - a third of this sheet's 11 sections were
    // this one repeated shape. Merged into a single "Per-phase models"
    // Section below, with each phase as a labelled group of sibling rows
    // (a bare Text title + 3 Pickers + a caption, the same
    // "plain Text row as a caption inside a Section" pattern already used
    // for the Apple-voice download hint in Settings) instead of 4 Section
    // wrappers.
    @ViewBuilder
    private func modelPhaseGroup(
        title: String, footer: String,
        provider: Binding<TextProviderKind?>, backup: Binding<TextProviderKind?>, onFailure: Binding<PhaseOnFailure>
    ) -> some View {
        Text(title).font(.subheadline.weight(.semibold))
        Picker("Model", selection: provider) {
            Text("Default").tag(TextProviderKind?.none)
            ForEach(TextProviderKind.allCases) { Text($0.label).tag(TextProviderKind?.some($0)) }
        }
        Picker("Backup", selection: backup) {
            Text("None").tag(TextProviderKind?.none)
            ForEach(TextProviderKind.allCases) { Text($0.label).tag(TextProviderKind?.some($0)) }
        }
        Picker("If both fail", selection: onFailure) {
            ForEach(PhaseOnFailure.allCases) { Text($0.label).tag($0) }
        }
        Text(footer).font(.caption2).foregroundStyle(.secondary)
    }

    // Creative: the PC's content_shape / visual_tone / style_lock / brand_preset.
    private static let toneOptions = OptionCatalog.options("video_tone")
    private static let brandOptions = OptionCatalog.options("brand_presets")

    /// The visual tone, with "custom" standing for any free text a person
    /// typed rather than a catalogue id (exactly how the PC stores it).
    private var toneSelection: Binding<String> {
        Binding(
            get: { Self.toneOptions.contains { $0.id == channel.visualTone } ? channel.visualTone : "custom" },
            set: { new in
                if new == "custom" {
                    if Self.toneOptions.contains(where: { $0.id == channel.visualTone }) { channel.visualTone = "" }
                } else {
                    channel.visualTone = new
                }
            }
        )
    }

    /// Picking a brand also seeds the channel's three accent colours from it,
    /// so the preset visibly does something right away; each colour can still
    /// be changed afterwards in the Brand section below.
    private func applyBrandColors(_ id: String) {
        func clean(_ key: String) -> String? { Brand.accentHex(id, key)?.replacingOccurrences(of: "#", with: "") }
        if let c = clean("accent1") { channel.accent1ColorHex = c }
        if let c = clean("accent2") { channel.accent2ColorHex = c }
        if let c = clean("highlight") { channel.accentColorHex = c }
    }

    private static let fontPresets = ["", "Georgia", "Avenir Next", "Futura", "Times New Roman", "Courier New", "Impact", "custom"]
    private var fontPickerSelection: Binding<String> {
        Binding(
            get: { Self.fontPresets.contains(channel.fontFamily) ? channel.fontFamily : "custom" },
            set: { if $0 != "custom" { channel.fontFamily = $0 } }
        )
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Identity") {
                    TextField("Name", text: $channel.name)
                    TextField("Niche (e.g. space facts)", text: $channel.niche)
                    TextField("Tone", text: $channel.tone)
                    TextField("Language", text: $channel.language)
                }
                Section {
                    Stepper("Length: \(channel.targetSeconds)s", value: $channel.targetSeconds, in: 15...600, step: 15)
                    Picker("Shape", selection: $channel.aspectRatio) {
                        ForEach(AspectRatio.allCases) { Text($0.rawValue).tag($0) }
                    }
                    Toggle("Research the topic first", isOn: $channel.topicResearchEnabled)
                } header: { Text("Videos") } footer: {
                    Text("Research spends one extra AI call gathering facts about the topic before the brief is written, matching the PC's topic_research.")
                }

                Section {
                    if !Personality.all.isEmpty {
                        Picker("Personality", selection: $channel.contentShape) {
                            ForEach(Personality.all) { Text($0.name.replacingOccurrences(of: "_", with: " ").capitalized).tag($0.name) }
                            // A saved value the file no longer has stays selectable
                            // (it degrades to the first preset when written).
                            if !Personality.all.contains(where: { $0.name == channel.contentShape }) {
                                Text(channel.contentShape.isEmpty ? "Default" : channel.contentShape).tag(channel.contentShape)
                            }
                        }
                    }
                    if !Self.toneOptions.isEmpty {
                        Picker("Visual tone", selection: toneSelection) {
                            ForEach(Self.toneOptions) { Text($0.label).tag($0.id) }
                        }
                    }
                    if Self.toneOptions.isEmpty || toneSelection.wrappedValue == "custom" {
                        TextField("Describe the look", text: $channel.visualTone, axis: .vertical)
                            .lineLimit(1...3)
                    }
                    TextField("Style lock (appended to every image prompt)", text: $channel.styleLock, axis: .vertical)
                        .lineLimit(1...3)
                    if !Self.brandOptions.isEmpty {
                        Picker("Brand preset", selection: $channel.brandPreset) {
                            ForEach(Self.brandOptions) { Text($0.label).tag($0.id) }
                            if !Self.brandOptions.contains(where: { $0.id == channel.brandPreset }) {
                                Text(channel.brandPreset.isEmpty ? "None" : channel.brandPreset).tag(channel.brandPreset)
                            }
                        }
                        .onChange(of: channel.brandPreset) { _, new in applyBrandColors(new) }
                    }
                    Toggle("Chapter cards between parts", isOn: $channel.chaptersEnabled)
                } header: { Text("Creative") } footer: {
                    Text(Personality.preset(named: channel.contentShape).description
                         + " Visual tone steers the brief, the narration voice and every image prompt. A style lock is added to the end of each image prompt so the pictures look like one set. The brand preset sets the motion feel and transitions the AI editor works toward, and fills in the colours below when you pick it. Chapter cards split a longer video into parts: each part after the first opens with a title card, and the narration pauses for it. Off by default, as on the PC.")
                }
                Section {
                    ColorPicker("Highlight", selection: accentBinding, supportsOpacity: false)
                    if !channel.accentColorHex.isEmpty {
                        Button("Use app default") { channel.accentColorHex = "" }
                    }
                    ColorPicker("Accent 1 (cards)", selection: accent1Binding, supportsOpacity: false)
                    ColorPicker("Accent 2 (cards)", selection: accent2Binding, supportsOpacity: false)
                    Picker("Font", selection: fontPickerSelection) {
                        ForEach(Self.fontPresets, id: \.self) { Text($0.isEmpty ? "System" : $0.capitalized).tag($0) }
                    }
                    if fontPickerSelection.wrappedValue == "custom" {
                        TextField("Font family name", text: $channel.fontFamily)
                    }
                } header: { Text("Brand") } footer: {
                    Text("Highlight tints captions' typewriter fill and a card's rule; Accent 1/2 tint takeover-card decoration. Font is shared by captions and cards, like the PC's font_family.")
                }

                Section {
                    Toggle("Reuse pool images", isOn: $channel.recycleEnabled)
                    if channel.recycleEnabled {
                        VStack(alignment: .leading) {
                            Text("Up to \(Int(channel.recycleRatio))% of scenes")
                            Slider(value: $channel.recycleRatio, in: 0...100, step: 5)
                        }
                    }
                } header: { Text("Recycling") } footer: {
                    Text("How often the writer should reach for Settings → Reusable images instead of generating fresh art for a scene. 100% forces every scene to reuse the pool.")
                }

                Section {
                    Toggle("Takeover cards", isOn: $channel.takeoverCardsEnabled)
                    if channel.takeoverCardsEnabled {
                        Toggle("Explainer cards", isOn: $channel.explainerCardsEnabled)
                    }
                } header: { Text("Cards") } footer: {
                    Text("Takeover cards is the master switch: off means no full-screen card of any kind, including the deterministic ((question)) ones. Explainer adds a FEW AI-picked scenes worth a full-screen concept card on top of those.")
                }

                Section {
                    Picker("Backup provider", selection: $channel.imageProviderBackup) {
                        Text("None").tag(ImageProviderKind?.none)
                        ForEach(ImageProviderKind.allCases) { Text($0.label).tag(ImageProviderKind?.some($0)) }
                    }
                    Picker("If both fail", selection: $channel.imageOnFailure) {
                        ForEach(ImageOnFailure.allCases) { Text($0.label).tag($0) }
                    }
                } header: { Text("Images") } footer: {
                    Text("A scene whose only provider fails stops the run by default, matching the PC. Set a backup and switch to \"fall back\" for resilience instead.")
                }

                Section {
                    Picker("Before the first render", selection: $channel.editingMode) {
                        ForEach(EditingMode.allCases) { Text($0.title).tag($0) }
                    }
                } header: { Text("Editing") } footer: {
                    Text(channel.editingMode.detail)
                }

                Section {
                    Toggle("Choose a track for me", isOn: $channel.musicAuto)
                    if !channel.musicAuto {
                        Picker("Track", selection: $channel.musicTrackID) {
                            Text("None").tag(String?.none)
                            if let missing = channel.musicTrackID, store.audioLibrary.item(missing) == nil {
                                Text("Removed track").tag(String?.some(missing))
                            }
                            ForEach(store.audioLibrary.music) { Text($0.name).tag(String?.some($0.id)) }
                        }
                    }
                    if channel.musicAuto || channel.musicTrackID != nil {
                        VStack(alignment: .leading) {
                            Text("Under the voice: \(Int(channel.musicGainDB)) dB")
                            Slider(value: $channel.musicGainDB, in: -35 ... -10, step: 1)
                        }
                    }
                } header: { Text("Music") } footer: {
                    Text(store.audioLibrary.music.isEmpty
                         ? "Upload tracks under Settings → Music first."
                         : (channel.musicAuto
                            ? "Each video gets the track whose tags fit its title and topic best, never the same one twice in a row. Tag your tracks under Settings → Music."
                            : "Plays under every video of this channel and drops while someone speaks. About -20 dB is a good start."))
                }

                Section {
                    Toggle("Sound effects", isOn: $channel.sfxEnabled)
                    if channel.sfxEnabled {
                        ForEach(SFXMoment.allCases) { moment in
                            Picker(selection: soundBinding(moment)) {
                                Text("None").tag(String?.none)
                                ForEach(store.audioLibrary.effects) { Text($0.name).tag(String?.some($0.id)) }
                            } label: {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(moment.title)
                                    Text(moment.detail).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                        if store.audioLibrary.effects.contains(where: { $0.tags.contains(StarterSounds.tag) }) {
                            Button("Use the starter pack sounds") {
                                channel.sfxMap = StarterSounds.fill(channel.sfxMap, from: store.audioLibrary.effects)
                            }
                        }
                        VStack(alignment: .leading) {
                            Text("Level: \(Int(channel.sfxGainDB)) dB")
                            Slider(value: $channel.sfxGainDB, in: -12 ... 6, step: 1)
                        }
                    }
                } header: { Text("Sound effects") } footer: {
                    Text(store.audioLibrary.effects.isEmpty
                         ? "Upload short sounds under Settings → Sound effects first. A moment with no sound does nothing."
                         : "Effects are used sparingly: not on every scene, and never crowded together. A moment with no sound does nothing.")
                }

                Section {
                    Picker("Trim aggressiveness", selection: $channel.voiceTrimAggressiveness) {
                        ForEach(VoiceTrimAggressiveness.allCases) { Text($0.label).tag($0) }
                    }
                    VStack(alignment: .leading) {
                        Text("Back off if a cut removes more than \(Int(channel.voiceMaxRemovableSilencePct))% of a segment")
                        Slider(value: $channel.voiceMaxRemovableSilencePct, in: 0...100, step: 5)
                    }
                } header: { Text("Voice cleanup") } footer: {
                    Text("How readily dead-air removal treats a quiet stretch in the narration as silence to cut, matching the PC's voice_trim_aggressiveness.")
                }

                Section {
                    if store.settings.voice == .elevenlabs {
                        VStack(alignment: .leading) {
                            Text("Stability \(Int(channel.voiceStability * 100))%")
                            Slider(value: $channel.voiceStability, in: 0...1, step: 0.05)
                        }
                    }
                    if store.settings.voice != .gemini {
                        VStack(alignment: .leading) {
                            Text("Speed \(String(format: "%.2f", channel.voiceSpeed))x")
                            Slider(value: $channel.voiceSpeed, in: 0.7...1.2, step: 0.05)
                        }
                    }
                    if store.settings.voice == .gemini {
                        Text("Gemini voices don't take a speed or stability setting.").foregroundStyle(.secondary)
                    }
                } header: { Text("Voice delivery") } footer: {
                    Text("Stability: lower is more expressive and varied, higher is more even (ElevenLabs only). Speed: 0.7x to 1.2x (ElevenLabs and the built-in voices). Both are the PC's voice_stability and voice_speed.")
                }

                Section {
                    OverlayPreview(style: channel.overlay, accent1: accent1Binding.wrappedValue, accent2: accent2Binding.wrappedValue)
                        .listRowInsets(EdgeInsets())
                    Picker("Background", selection: $channel.overlay.captionBackground) {
                        ForEach(CaptionBackground.allCases) { Text($0.label).tag($0) }
                    }
                    if channel.overlay.captionBackground != .none {
                        VStack(alignment: .leading) {
                            Text("Background opacity \(Int(channel.overlay.captionBackgroundOpacity * 100))%")
                            Slider(value: $channel.overlay.captionBackgroundOpacity, in: 0.1...1, step: 0.05)
                        }
                    }
                    VStack(alignment: .leading) {
                        Text("Outline \(channel.overlay.captionOutline == 0 ? "off" : String(format: "%.0f", channel.overlay.captionOutline))")
                        Slider(value: $channel.overlay.captionOutline, in: 0...10, step: 1)
                    }
                    Toggle("Shadow", isOn: $channel.overlay.captionShadow)
                    Toggle("Capital letters", isOn: $channel.overlay.captionUppercase)
                    ColorPicker("Text colour", selection: captionColorBinding, supportsOpacity: false)
                } header: { Text("Caption look") } footer: {
                    Text("How captions are drawn. The highlight colour for the spoken word is the Highlight colour in Brand above; where captions sit, how they animate and their size are in Settings → Captions.")
                }

                Section {
                    Picker("Background", selection: $channel.overlay.cardBackground) {
                        ForEach(CardBackground.allCases) { Text($0.label).tag($0) }
                    }
                    Picker("Alignment", selection: $channel.overlay.cardAlignment) {
                        ForEach(CardAlignment.allCases) { Text($0.label).tag($0) }
                    }
                    VStack(alignment: .leading) {
                        Text("Text size \(Int(channel.overlay.cardTextScale * 100))%")
                        Slider(value: $channel.overlay.cardTextScale, in: 0.6...1.4, step: 0.05)
                    }
                    Toggle("Capital letters", isOn: $channel.overlay.cardUppercase)
                    Toggle("Accent stripe and rule", isOn: $channel.overlay.cardAccentBars)
                    Picker("Entrance", selection: $channel.overlay.cardAnimation) {
                        ForEach(CardAnimation.allCases) { Text($0.label).tag($0) }
                    }
                } header: { Text("Card look") } footer: {
                    Text("Applies to question, explainer and chapter cards. The accent colours come from the Brand section above.")
                }

                // Mirrors the PC's "Models" settings tab: each text-writing
                // phase can override the channel's default text provider,
                // with its own backup and if-both-fail policy. Left blank
                // (None), a phase just uses Settings → the default text
                // provider, like an unconfigured PC channel.
                Section {
                    modelPhaseGroup(
                        title: "Brief",
                        footer: "Governs the creative brief AND the optional topic-research call ahead of it - the PC's own router shares one model between the two.",
                        provider: $channel.briefTextProvider, backup: $channel.briefTextBackup, onFailure: $channel.briefOnFailure
                    )
                    modelPhaseGroup(
                        title: "Script · Draft",
                        footer: "Governs writing the narration itself.",
                        provider: $channel.scriptDraftTextProvider, backup: $channel.scriptDraftTextBackup, onFailure: $channel.scriptDraftOnFailure
                    )
                    modelPhaseGroup(
                        title: "Script · Segmentation",
                        footer: "Governs splitting the finished narration into scenes and image prompts.",
                        provider: $channel.scriptSegmentationTextProvider, backup: $channel.scriptSegmentationTextBackup, onFailure: $channel.scriptSegmentationOnFailure
                    )
                    modelPhaseGroup(
                        title: "Direction (motion & grade)",
                        footer: "Governs the Director II call that plans each scene's camera move, entrance transition, and the video's one overall colour grade, right before render.",
                        provider: $channel.director2TextProvider, backup: $channel.director2TextBackup, onFailure: $channel.director2OnFailure
                    )
                } header: { Text("Per-phase models") } footer: {
                    Text("Leave a phase on Default to use the channel's global text provider above.")
                }
            }
            .navigationTitle(channel.name.isEmpty ? "Channel" : channel.name)
            .navigationBarTitleDisplayMode(.inline)
            .scrollDismissesKeyboard(.interactively)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { store.upsert(channel); dismiss() }.disabled(channel.name.trimmed.isEmpty)
                }
            }
            // A themed pass, not a rebuild: this stays a native Form (8
            // Sections of Pickers/Sliders/ColorPickers/Steppers - see the
            // "Phase 3 polish" comment above on why it isn't split further)
            // sitting on the app's own warm background instead of the
            // system grouped-list one, tinted with the real accent - not
            // hand-rolled glass rows per SKIN_V2_PLAN.md's own scope note
            // for this screen ("gets the new visual skin, not a re-split").
            .glassSheetBackground()
            .tint(Theme.accent)
        }
        .presentationDetents([.medium, .large])
    }
}

/// A small live picture of the overlay settings: one card and one caption,
/// drawn in SwiftUI from the same style values the renderer reads. It is a
/// guide, not a pixel match - the renderer draws with Core Graphics - but it
/// moves with every control, which is what makes the options understandable.
private struct OverlayPreview: View {
    let style: OverlayStyle
    let accent1: Color
    let accent2: Color

    private var palette: (background: Color, text: Color) {
        switch style.cardBackground {
        case .dark: return (Color(red: 0.043, green: 0.059, blue: 0.102), Color(white: 0.95))
        case .light: return (Color(red: 0.957, green: 0.945, blue: 0.918), Color(red: 0.08, green: 0.09, blue: 0.11))
        case .brand: return (accent1.opacity(0.28).mix(with: .black, by: 0.7), Color(white: 0.97))
        }
    }

    var body: some View {
        HStack(spacing: 12) {
            card.frame(maxWidth: .infinity)
            caption.frame(maxWidth: .infinity)
        }
        .padding(12)
        .accessibilityHidden(true)
    }

    private var card: some View {
        ZStack(alignment: style.cardAlignment == .left ? .leading : .center) {
            palette.background
            if style.cardAccentBars {
                VStack { accent2.frame(height: 3); Spacer() }
            }
            VStack(alignment: style.cardAlignment == .left ? .leading : .center, spacing: 6) {
                Text(style.cardUppercase ? "WHY DOES IT GLOW?" : "Why does it glow?")
                    .font(.system(size: 15 * CGFloat(style.cardTextScale), weight: .heavy))
                    .foregroundStyle(palette.text)
                    .multilineTextAlignment(style.cardAlignment == .left ? .leading : .center)
                if style.cardAccentBars { Capsule().fill(accent1).frame(width: 34, height: 3) }
            }
            .padding(12)
        }
        .frame(height: 118)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }

    private var caption: some View {
        ZStack {
            LinearGradient(colors: [.gray.opacity(0.55), .gray.opacity(0.25)], startPoint: .top, endPoint: .bottom)
            let color = Theme.color(hex: style.captionTextColorHex) ?? .white
            Text(style.captionUppercase ? "THE SEA GLOWS" : "The sea glows")
                .font(.system(size: 15, weight: .black, design: .rounded))
                .foregroundStyle(color)
                .shadow(color: style.captionOutline > 0 ? .black : .clear, radius: 0, x: 1, y: 1)
                .shadow(color: style.captionShadow ? .black.opacity(0.6) : .clear, radius: 4, y: 2)
                .padding(.horizontal, style.captionBackground == .none ? 0 : 10)
                .padding(.vertical, style.captionBackground == .none ? 0 : 5)
                .background {
                    if style.captionBackground != .none {
                        RoundedRectangle(cornerRadius: style.captionBackground == .pill ? 20 : 6, style: .continuous)
                            .fill(.black.opacity(style.captionBackgroundOpacity))
                    }
                }
        }
        .frame(height: 118)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}

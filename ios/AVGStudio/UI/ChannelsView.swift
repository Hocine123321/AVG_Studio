import SwiftUI

struct ChannelsView: View {
    @Environment(Store.self) private var store
    @State private var editing: Channel?

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                ForEach(store.channels) { c in
                    HStack(spacing: 14) {
                        Image(systemName: c.id == store.activeChannelID ? "star.circle.fill" : "person.crop.circle")
                            .font(.title)
                            .foregroundStyle(c.id == store.activeChannelID ? (Theme.color(hex: c.accentColorHex) ?? Theme.accent) : .secondary)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(c.name).font(.headline)
                            Text("\(c.niche.isEmpty ? "General" : c.niche) · \(c.targetSeconds)s · \(c.aspectRatio.rawValue)")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if c.id != store.activeChannelID {
                            Button("Activate") { withAnimation(.spring) { store.activate(c) } }
                                .buttonStyle(.glass)
                        }
                    }
                    .glassCard()
                    .contentShape(.rect)
                    .onTapGesture { editing = c }
                    .contextMenu {
                        Button("Edit", systemImage: "pencil") { editing = c }
                        Button("Delete", systemImage: "trash", role: .destructive) { store.deleteChannel(c) }
                            .disabled(store.channels.count < 2)
                    }
                }
            }
            .padding(16)
        }
        .screenBackground()
        .navigationTitle("Channels")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Add", systemImage: "plus") { editing = Channel(name: "New Channel") }
            }
        }
        .sheet(item: $editing) { c in ChannelEditor(channel: c) }
    }
}

struct ChannelEditor: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State var channel: Channel

    private var accentBinding: Binding<Color> {
        Binding(
            get: { Theme.color(hex: channel.accentColorHex) ?? Theme.accent },
            set: { channel.accentColorHex = Theme.hex(from: $0) }
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
    @ViewBuilder
    private func modelPhaseSection(
        title: String, footer: String,
        provider: Binding<TextProviderKind?>, backup: Binding<TextProviderKind?>, onFailure: Binding<PhaseOnFailure>
    ) -> some View {
        Section {
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
        } header: { Text(title) } footer: { Text(footer) }
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

                // Mirrors the PC's "Models" settings tab: each text-writing
                // phase can override the channel's default text provider,
                // with its own backup and if-both-fail policy. Left blank
                // (None), a phase just uses Settings → the default text
                // provider, like an unconfigured PC channel.
                modelPhaseSection(
                    title: "Brief",
                    footer: "Governs the creative brief AND the optional topic-research call ahead of it - the PC's own router shares one model between the two.",
                    provider: $channel.briefTextProvider, backup: $channel.briefTextBackup, onFailure: $channel.briefOnFailure
                )
                modelPhaseSection(
                    title: "Script · Draft",
                    footer: "Governs writing the narration itself.",
                    provider: $channel.scriptDraftTextProvider, backup: $channel.scriptDraftTextBackup, onFailure: $channel.scriptDraftOnFailure
                )
                modelPhaseSection(
                    title: "Script · Segmentation",
                    footer: "Governs splitting the finished narration into scenes and image prompts.",
                    provider: $channel.scriptSegmentationTextProvider, backup: $channel.scriptSegmentationTextBackup, onFailure: $channel.scriptSegmentationOnFailure
                )
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
        }
        .presentationDetents([.medium, .large])
    }
}

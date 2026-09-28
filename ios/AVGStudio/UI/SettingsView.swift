import SwiftUI
import AVFoundation

/// The mock's `set()` screen, re-merged back onto ONE page (Skin v2 Phase
/// 7) after `UI_POLISH_PLAN.md` had split it into three destinations
/// (AI Providers / Video & Render / Captions). Grouped under the mock's
/// own section names (Writing/Visuals/Voice/Captions) as closely as this
/// app's REAL fields actually correspond - Quality/FPS/Ken Burns/Filters
/// all fold into "Visuals" since the mock has no separate Video section.
///
/// One thing deliberately NOT ported: the mock's Channel/Global segmented
/// mode (`S.mode`) toggle, which assumes every field here has BOTH a
/// global default AND a per-channel override sitting right behind it.
/// That isn't how this app's real settings are shaped - provider choice/
/// video quality/captions are global-only, tone/niche/per-phase-model/
/// recycling/cards/brand are per-channel-only (Channels → tap a channel,
/// Skin v2 Phase 6) - there's no uniform pair to flip between. Building
/// that toggle here would only ever show a value with nothing behind it
/// to switch to. This screen stays what it honestly is: the global
/// defaults, same as before the merge.
struct SettingsView: View {
    @Environment(Store.self) private var store
    @State private var keys: [APIKey: String] = [:]
    @State private var refreshingCatalogs = false
    @State private var editingKey: APIKey?

    // ── Live model catalogues ───────────────────────────────────────
    // One ModelCatalog per provider endpoint (see ModelCatalog.swift's
    // doc comment for why this is small closures rather than near-
    // identical classes). Held as @State so the SAME instance - and its
    // fetched entries/disk cache - survives this view's body being
    // re-evaluated on every settings change.
    @State private var geminiTextCatalog = ModelCatalog(cacheKey: "gemini_text") {
        guard let key = Keychain.get(.gemini) else { throw AVGError.missingKey(APIKey.gemini.label) }
        return try await ModelCatalogSources.geminiText(key: key)
    }
    @State private var groqTextCatalog = ModelCatalog(cacheKey: "groq_text") {
        guard let key = Keychain.get(.groq) else { throw AVGError.missingKey(APIKey.groq.label) }
        return try await ModelCatalogSources.groqText(key: key)
    }
    @State private var stableHordeCatalog = ModelCatalog(cacheKey: "stablehorde_image") {
        try await ModelCatalogSources.stableHordeImage()
    }
    @State private var elevenVoicesCatalog = ModelCatalog(cacheKey: "elevenlabs_voices") {
        guard let key = Keychain.get(.elevenlabs) else { throw AVGError.missingKey(APIKey.elevenlabs.label) }
        return try await ModelCatalogSources.elevenlabsVoices(key: key)
    }
    @State private var elevenModelsCatalog = ModelCatalog(cacheKey: "elevenlabs_models") {
        guard let key = Keychain.get(.elevenlabs) else { throw AVGError.missingKey(APIKey.elevenlabs.label) }
        return try await ModelCatalogSources.elevenlabsModels(key: key)
    }
    @State private var pollinationsTextCatalog = ModelCatalog(cacheKey: "pollinations_text") {
        try await ModelCatalogSources.pollinationsText(key: Keychain.get(.pollinations))
    }
    @State private var pollinationsImageCatalog = ModelCatalog(cacheKey: "pollinations_image") {
        try await ModelCatalogSources.pollinationsImage(key: Keychain.get(.pollinations))
    }
    @State private var geminiImageCatalog = ModelCatalog(cacheKey: "gemini_image") {
        guard let key = Keychain.get(.gemini) else { throw AVGError.missingKey(APIKey.gemini.label) }
        return try await ModelCatalogSources.geminiImageModels(key: key)
    }
    @State private var geminiVoiceModelCatalog = ModelCatalog(cacheKey: "gemini_voice_model") {
        guard let key = Keychain.get(.gemini) else { throw AVGError.missingKey(APIKey.gemini.label) }
        return try await ModelCatalogSources.geminiVoiceModels(key: key)
    }

    private var allCatalogs: [ModelCatalog] {
        [geminiTextCatalog, groqTextCatalog, stableHordeCatalog, elevenVoicesCatalog,
         elevenModelsCatalog, pollinationsTextCatalog, pollinationsImageCatalog,
         geminiImageCatalog, geminiVoiceModelCatalog]
    }

    var body: some View {
        @Bindable var store = store
        Form {
            // ── Writing ──────────────────────────────────────────────
            Section {
                Picker("Writer", selection: $store.settings.text) {
                    ForEach(TextProviderKind.allCases) { Text($0.label).tag($0) }
                }
                if store.settings.text == .gemini {
                    ModelPickerRow(title: "Gemini model", value: $store.settings.geminiModel, catalog: geminiTextCatalog)
                } else if store.settings.text == .groq {
                    ModelPickerRow(title: "Groq model", value: $store.settings.groqModel, catalog: groqTextCatalog)
                } else if store.settings.text == .pollinations {
                    ModelPickerRow(title: "Pollinations model", value: $store.settings.pollinationsTextModel, catalog: pollinationsTextCatalog)
                }
                Toggle("Dynamic pauses", isOn: $store.settings.dynamicPausesEnabled)
                if store.settings.dynamicPausesEnabled {
                    Stepper("Pause length: \(Int(store.settings.pauseMs)) ms", value: $store.settings.pauseMs, in: 200...2000, step: 100)
                }
            } header: { Text("Writing") } footer: {
                Text("When the writer inserts [pause], on becomes a real held silence between two separately-recorded segments. Off just collapses it to a beat, spoken as part of one take.")
            }

            // ── Visuals ──────────────────────────────────────────────
            // Folds in Quality/FPS/Ken Burns/Filters too - the mock has
            // no separate "Video" section, everything about how a take
            // LOOKS lives here.
            Section {
                Picker("Images", selection: $store.settings.image) {
                    ForEach(ImageProviderKind.allCases) { Text($0.label).tag($0) }
                }
                if store.settings.image == .stablehorde {
                    ModelPickerRow(title: "Stable Horde model", value: $store.settings.stableHordeModel, catalog: stableHordeCatalog)
                } else if store.settings.image == .pollinations {
                    ModelPickerRow(title: "Pollinations model", value: $store.settings.pollinationsImageModel, catalog: pollinationsImageCatalog)
                } else if store.settings.image == .gemini {
                    ModelPickerRow(title: "Gemini image model", value: $store.settings.geminiImageModel, catalog: geminiImageCatalog)
                }
                TextField("Image style", text: $store.settings.imageStyle, axis: .vertical)
                Picker("Quality", selection: $store.settings.shortEdge) {
                    Text("720p").tag(720); Text("1080p").tag(1080); Text("1440p").tag(1440)
                }
                Picker("Frame rate", selection: $store.settings.fps) {
                    Text("24").tag(24); Text("30").tag(30); Text("60").tag(60)
                }
                Toggle("Ken Burns motion", isOn: $store.settings.kenBurns)
                if store.settings.kenBurns {
                    Slider(value: $store.settings.kbIntensity, in: 0.2...2) { Text("Motion") }
                }
                Picker("Filter style", selection: $store.settings.filterStyle) {
                    ForEach(FilterStyle.allCases) { Text($0.label).tag($0) }
                }
                if store.settings.filterStyle != .none {
                    Slider(value: $store.settings.filterIntensity, in: 0...100) { Text("Filter intensity") }
                }
            } header: { Text("Visuals") } footer: {
                Text("Filters are an on-device stand-in for the PC's grain/light-leak look, generated procedurally rather than from the same source footage.")
            }

            Section {
                NavigationLink("Reusable images", destination: ReusableImagesView())
            } footer: {
                Text("A shared pool the writer can pull from instead of generating fresh art. Each channel decides how heavily to use it under Channels → Recycling.")
            }

            // ── Voice ────────────────────────────────────────────────
            Section("Voice") {
                Picker("Voice provider", selection: $store.settings.voice) {
                    ForEach(VoiceProviderKind.allCases) { Text($0.label).tag($0) }
                }
            }
            switch store.settings.voice {
            case .apple:
                Section {
                    Picker("Voice", selection: $store.settings.appleVoiceIdentifier) {
                        Text("Automatic").tag("")
                        ForEach(AppleVoice.voices(), id: \.identifier) { v in
                            Text("\(v.name) · \(v.language)\(v.quality == .premium ? " · Premium" : (v.quality == .enhanced ? " · Enhanced" : ""))")
                                .tag(v.identifier)
                        }
                    }
                    Text("Download better voices in iOS Settings → Accessibility → Spoken Content → Voices.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            case .elevenlabs:
                Section {
                    ModelPickerRow(title: "Voice ID", value: $store.settings.elevenVoiceID, catalog: elevenVoicesCatalog)
                    ModelPickerRow(title: "Model", value: $store.settings.elevenModel, catalog: elevenModelsCatalog)
                }
            case .gemini:
                Section {
                    Picker("Voice", selection: $store.settings.geminiVoiceName) {
                        ForEach(GeminiVoice.prebuiltVoices, id: \.self) { Text($0).tag($0) }
                    }
                    ModelPickerRow(title: "TTS model", value: $store.settings.geminiVoiceModel, catalog: geminiVoiceModelCatalog)
                } footer: {
                    Text("Not a PC provider - added as a third voice option alongside ElevenLabs and Apple voices.")
                }
            }

            // ── Captions ─────────────────────────────────────────────
            Section("Captions") {
                Toggle("Burn in captions", isOn: $store.settings.captionsEnabled)
                Picker("Animation", selection: $store.settings.captionAnimationStyle) {
                    ForEach(CaptionAnimationStyle.allCases) { Text($0.label).tag($0) }
                }
                Stepper("Words per caption: \(store.settings.captionMaxWords)", value: $store.settings.captionMaxWords, in: 1...10)
                Picker("Position", selection: $store.settings.captionPosition) {
                    ForEach(CaptionPosition.allCases) { Text($0.rawValue.capitalized).tag($0) }
                }
                Slider(value: $store.settings.captionFontSize, in: 36...120) { Text("Size") }
            }

            // ── API keys ─────────────────────────────────────────────
            // The mock's own row: label + a "Connected"/"Add key" status
            // pill, tap to edit - not a bare always-visible SecureField
            // list. Editing itself still happens in a SecureField, just
            // inside `keyEditor`'s sheet instead of inline.
            Section {
                ForEach(APIKey.allCases) { k in
                    Button { editingKey = k } label: {
                        HStack {
                            Text(k.label).foregroundStyle(Theme.ink)
                            Spacer()
                            keyStatusPill(k)
                        }
                    }
                    .buttonStyle(.plain)
                }
            } header: { Text("API keys") } footer: {
                Text("Stored in your iPhone's Keychain. Free options need no keys. AssemblyAI is optional and only affects caption timing accuracy.")
            }

            // ── Model cache ──────────────────────────────────────────
            // The mock's own "Rebuild cache" button, minus its static
            // "Proven working: Groq 5 of 10..." text - this app has no
            // real per-provider probe history to report, just each
            // provider's live model LIST, which is exactly what this
            // refreshes: every ModelPickerRow's catalog above, at once,
            // instead of one at a time.
            Section {
                Button {
                    Task {
                        refreshingCatalogs = true
                        // Sequential, not a TaskGroup: ModelCatalog is
                        // @MainActor and isn't Sendable, so this stays a
                        // plain chain of awaited calls on the same actor
                        // rather than fanning capture-across-actors out to
                        // concurrent child tasks - a few extra seconds for
                        // an occasional manual refresh is a fine trade for
                        // not fighting the concurrency checker over it.
                        for c in allCatalogs { await c.refresh() }
                        refreshingCatalogs = false
                    }
                } label: {
                    HStack {
                        Text("Refresh all model lists")
                        if refreshingCatalogs { Spacer(); ProgressView() }
                    }
                }
                .disabled(refreshingCatalogs)
            } header: { Text("Model cache") } footer: {
                Text("Re-fetches every provider's live model list at once, instead of one at a time from its own picker.")
            }

            // ── App related ──────────────────────────────────────────
            Section {
                VStack(alignment: .leading, spacing: 12) {
                    HStack {
                        Text("Glass transparency")
                        Spacer()
                        Text("\(Int((1 - store.settings.glassOpacity) / 0.85 * 100))%")
                            .foregroundStyle(Theme.ink2).monospacedDigit()
                    }
                    // Slider runs left = solid, right = see-through.
                    Slider(
                        value: Binding(
                            get: { (1 - store.settings.glassOpacity) / 0.85 },
                            set: { store.settings.glassOpacity = 1 - $0 * 0.85 }
                        ),
                        in: 0...1
                    )
                    GlassPreview(level: store.settings.glassOpacity)
                }
                .padding(.vertical, 4)
                if store.settings.glassOpacity != 1 {
                    Button("Reset to default") { store.settings.glassOpacity = 1 }
                }
            } header: { Text("App related") } footer: {
                Text("Applies to every card, field and pop-up window. The preview below the slider updates live; the rest of the app follows as you drag.")
            }

            // ── Appearance ───────────────────────────────────────────
            Section("Appearance") {
                Picker("Theme", selection: $store.settings.themePreference) {
                    ForEach(ThemePreference.allCases) { Text($0.label).tag($0) }
                }
                .pickerStyle(.segmented)
            }

            Section {
                NavigationLink("App health", destination: HealthView())
                NavigationLink("Activity & errors", destination: ErrorLogView())
            }
        }
        .screenBackground()
        .tint(Theme.accent)
        .navigationTitle("Settings")
        .onAppear {
            for k in APIKey.allCases { keys[k] = Keychain.get(k) ?? "" }
        }
        .sheet(item: $editingKey) { k in
            APIKeyEditSheet(key: k, value: keys[k] ?? "") { newValue in
                keys[k] = newValue
                Keychain.set(k, newValue)
            }
        }
    }

    @ViewBuilder private func keyStatusPill(_ k: APIKey) -> some View {
        let saved = Keychain.has(k)
        Text(saved ? "Connected" : "Add key")
            .font(.caption.weight(.semibold))
            .foregroundStyle(saved ? Theme.ok : Theme.ink2)
            .padding(.horizontal, 12).padding(.vertical, 6)
            .background(saved ? Theme.ok.opacity(0.16) : Theme.fill, in: Capsule())
            .overlay(Capsule().strokeBorder(saved ? Theme.ok.opacity(0.4) : Theme.glassEdge, lineWidth: 0.5))
    }
}

/// Live miniature of the app (colored orbs, a glass card with a row, a
/// pill button) drawn with the SAME fill/material math as the real glass,
/// at the slider's current level, so the effect is visible before it's
/// applied everywhere.
private struct GlassPreview: View {
    let level: Double

    var body: some View {
        ZStack {
            LinearGradient(colors: [Theme.bg1, Theme.bg2], startPoint: .topLeading, endPoint: .bottomTrailing)
            Circle().fill(Theme.accent).frame(width: 110, height: 110).blur(radius: 26).offset(x: -70, y: -20)
            Circle().fill(Theme.ok).frame(width: 120, height: 120).blur(radius: 28).offset(x: 80, y: 30)
            VStack(spacing: 10) {
                HStack {
                    Text("Library").font(.footnote.weight(.semibold))
                    Spacer()
                    Text("Done").font(.caption).foregroundStyle(Theme.ink2)
                }
                .padding(12)
                .background(previewGlass(RoundedRectangle(cornerRadius: 16, style: .continuous)))
                Text("Create a video")
                    .font(.footnote.weight(.semibold)).foregroundStyle(Theme.onAccent)
                    .padding(.horizontal, 16).padding(.vertical, 8)
                    .background(Theme.accent, in: Capsule())
            }
            .padding(.horizontal, 24)
        }
        .frame(height: 150)
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).strokeBorder(Theme.glassEdge, lineWidth: 0.5))
    }

    private func previewGlass<S: InsettableShape>(_ shape: S) -> some View {
        ZStack {
            shape.fill(.ultraThinMaterial).opacity(level)
            shape.fill(Color.white.opacity(0.09 * level))
        }
        .overlay(shape.strokeBorder(Theme.glassEdge, lineWidth: 0.5))
    }
}

/// The mock's key-entry sheet: one `SecureField` plus Save, and Remove
/// when a key is already saved - opened by tapping a Settings API-key
/// row instead of that row being an inline always-visible field.
private struct APIKeyEditSheet: View {
    @Environment(\.dismiss) private var dismiss
    let key: APIKey
    @State var value: String
    let onSave: (String) -> Void

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    SecureField("API key", text: $value)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                } footer: {
                    Text("Stored in your iPhone's Keychain, never in a project file.")
                }
                if !value.trimmed.isEmpty {
                    Button("Remove key", role: .destructive) {
                        value = ""
                        onSave("")
                        dismiss()
                    }
                }
            }
            .glassSheetBackground()
            .navigationTitle(key.label)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { onSave(value); dismiss() }
                }
            }
        }
        .presentationDetents([.medium])
    }
}

struct ErrorLogView: View {
    @Environment(Store.self) private var store
    var body: some View {
        List(store.log) { e in
            VStack(alignment: .leading, spacing: 4) {
                Label(e.message, systemImage: e.isError ? "exclamationmark.triangle.fill" : "checkmark.circle")
                    .foregroundStyle(e.isError ? .red : .primary)
                Text(e.date, style: .time).font(.caption).foregroundStyle(.secondary)
            }
        }
        .overlay { if store.log.isEmpty { ContentUnavailableView("No activity yet", systemImage: "list.bullet.rectangle") } }
        .screenBackground()
        .navigationTitle("Activity")
    }
}

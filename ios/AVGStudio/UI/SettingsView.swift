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
            Section("Writing") {
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
            } footer: {
                Text("When the writer inserts [pause], on becomes a real held silence between two separately-recorded segments. Off just collapses it to a beat, spoken as part of one take.")
            }

            // ── Visuals ──────────────────────────────────────────────
            // Folds in Quality/FPS/Ken Burns/Filters too - the mock has
            // no separate "Video" section, everything about how a take
            // LOOKS lives here.
            Section("Visuals") {
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
            } footer: {
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
            Section {
                ForEach(APIKey.allCases) { k in
                    SecureField(k.label, text: Binding(
                        get: { keys[k] ?? "" },
                        set: { keys[k] = $0; Keychain.set(k, $0) }
                    ))
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
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

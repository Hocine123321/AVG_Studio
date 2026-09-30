import SwiftUI
import AVFoundation

// One screen per settings category. Every control the old single-page
// Settings had is here - nothing was dropped - regrouped so that a setting is
// at most two taps from the hub and its control sits in reach.
//
// Shared shape: `SettingsPage` (scrolling glass-card page) > `SettingsGroup`
// (titled card, hairline-separated rows) > the rows from SettingsKit.swift.
// Rows that belong to a provider appear only while that provider is selected,
// exactly as before.

// MARK: - Writing

struct WritingSettingsPage: View {
    @Environment(Store.self) private var store
    @Environment(SettingsCatalogs.self) private var catalogs
    var focus: SettingAnchor?

    var body: some View {
        @Bindable var store = store
        SettingsPage(title: "Writing", focus: focus) {
            SettingsGroup(title: "Writer") {
                ChoiceRow(title: "Writer",
                          options: TextProviderKind.allCases.map { ChoiceOption($0, $0.label) },
                          selection: $store.settings.text)
                    .anchored(.writer)
                switch store.settings.text {
                case .gemini:
                    ModelRow(title: "Gemini model", value: $store.settings.geminiModel, catalog: catalogs.geminiText)
                        .anchored(.writerModel)
                case .groq:
                    ModelRow(title: "Groq model", value: $store.settings.groqModel, catalog: catalogs.groqText)
                        .anchored(.writerModel)
                case .pollinations:
                    ModelRow(title: "Pollinations model", value: $store.settings.pollinationsTextModel, catalog: catalogs.pollinationsText)
                        .anchored(.writerModel)
                case .apple:
                    InfoNote(text: "Runs on your iPhone with Apple Intelligence. No key, no model to choose.")
                }
            }

            SettingsGroup(title: "Narration pacing",
                          footer: "When the writer inserts [pause], on becomes a real held silence between two separately-recorded segments. Off just collapses it to a beat, spoken as part of one take.") {
                ToggleRow(title: "Dynamic pauses", isOn: $store.settings.dynamicPausesEnabled)
                    .anchored(.dynamicPauses)
                if store.settings.dynamicPausesEnabled {
                    StepperRow(title: "Pause length",
                               valueText: "\(Int(store.settings.pauseMs)) ms",
                               value: $store.settings.pauseMs, range: 200...2000, step: 100)
                        .anchored(.pauseLength)
                }
            }
        }
    }
}

// MARK: - Images

struct ImagesSettingsPage: View {
    @Environment(Store.self) private var store
    @Environment(SettingsCatalogs.self) private var catalogs
    var focus: SettingAnchor?

    var body: some View {
        @Bindable var store = store
        SettingsPage(title: "Images", focus: focus) {
            SettingsGroup(title: "Image maker") {
                ChoiceRow(title: "Images",
                          options: ImageProviderKind.allCases.map { ChoiceOption($0, $0.label) },
                          selection: $store.settings.image)
                    .anchored(.imageProvider)
                switch store.settings.image {
                case .stablehorde:
                    ModelRow(title: "Stable Horde model", value: $store.settings.stableHordeModel, catalog: catalogs.stableHorde)
                        .anchored(.imageModel)
                case .pollinations:
                    ModelRow(title: "Pollinations model", value: $store.settings.pollinationsImageModel, catalog: catalogs.pollinationsImage)
                        .anchored(.imageModel)
                case .gemini:
                    ModelRow(title: "Gemini image model", value: $store.settings.geminiImageModel, catalog: catalogs.geminiImage)
                        .anchored(.imageModel)
                default:
                    EmptyView()
                }
                FieldRow(title: "Image style", placeholder: "e.g. cinematic, dramatic lighting", text: $store.settings.imageStyle)
                    .anchored(.imageStyle)
            }

            SettingsGroup(footer: "Pictures you can reuse across videos instead of generating new ones.") {
                NavigationLink(value: SettingsRoute.reusableImages) {
                    NavRow(symbol: "photo.stack", title: "Reusable images", detail: "\(store.reusableImages.count) in the pool")
                }
                .buttonStyle(.plain)
                .anchored(.reusableImages)
            }
        }
    }
}

// MARK: - Voice

struct VoiceSettingsPage: View {
    @Environment(Store.self) private var store
    @Environment(SettingsCatalogs.self) private var catalogs
    var focus: SettingAnchor?
    @State private var appleVoices: [ChoiceOption<String>] = [ChoiceOption("", "Automatic")]

    var body: some View {
        @Bindable var store = store
        SettingsPage(title: "Voice", focus: focus) {
            SettingsGroup(title: "Narrator") {
                ChoiceRow(title: "Voice provider",
                          options: VoiceProviderKind.allCases.map { ChoiceOption($0, $0.label) },
                          selection: $store.settings.voice)
                    .anchored(.voiceProvider)
                switch store.settings.voice {
                case .apple:
                    ChoiceRow(title: "Voice", options: appleVoices, selection: $store.settings.appleVoiceIdentifier)
                        .anchored(.voicePick)
                case .elevenlabs:
                    ModelRow(title: "Voice ID", value: $store.settings.elevenVoiceID, catalog: catalogs.elevenVoices)
                        .anchored(.voicePick)
                    ModelRow(title: "Model", value: $store.settings.elevenModel, catalog: catalogs.elevenModels)
                        .anchored(.voiceModel)
                case .gemini:
                    ChoiceRow(title: "Voice",
                              options: GeminiVoice.prebuiltVoices.map { ChoiceOption($0, $0) },
                              selection: $store.settings.geminiVoiceName)
                        .anchored(.voicePick)
                    ModelRow(title: "TTS model", value: $store.settings.geminiVoiceModel, catalog: catalogs.geminiVoiceModel)
                        .anchored(.voiceModel)
                }
            }
            if store.settings.voice == .apple {
                InfoNote(text: "Download better voices in iOS Settings → Accessibility → Spoken Content → Voices.")
                    .padding(.horizontal, 6)
                    .padding(.top, -12)
            }
        }
        // Only enumerated when it is about to be shown: listing every
        // installed voice is noticeably slow and the old screen did it on
        // every redraw.
        .task(id: store.settings.voice) {
            guard store.settings.voice == .apple else { return }
            appleVoices = [ChoiceOption("", "Automatic")] + AppleVoice.voices().map { v in
                let tier = v.quality == .premium ? " · Premium" : (v.quality == .enhanced ? " · Enhanced" : "")
                return ChoiceOption(v.identifier, "\(v.name) · \(v.language)\(tier)")
            }
        }
    }
}

// MARK: - Captions

struct CaptionsSettingsPage: View {
    @Environment(Store.self) private var store
    var focus: SettingAnchor?

    var body: some View {
        @Bindable var store = store
        SettingsPage(title: "Captions", focus: focus) {
            SettingsGroup {
                ToggleRow(title: "Burn in captions", subtitle: "Word-timed text on the video", isOn: $store.settings.captionsEnabled)
                    .anchored(.captionsToggle)
            }
            SettingsGroup(title: "Style") {
                ChoiceRow(title: "Animation",
                          options: CaptionAnimationStyle.allCases.map { ChoiceOption($0, $0.label) },
                          selection: $store.settings.captionAnimationStyle)
                    .anchored(.captionAnimation)
                StepperRow(title: "Words per caption",
                           valueText: "\(store.settings.captionMaxWords)",
                           value: $store.settings.captionMaxWords, range: 1...10, step: 1)
                    .anchored(.captionWords)
                SegmentRow(title: "Position",
                           options: Array(CaptionPosition.allCases),
                           label: { $0.rawValue.capitalized },
                           selection: $store.settings.captionPosition)
                    .anchored(.captionPosition)
                SliderRow(title: "Size", valueText: "\(Int(store.settings.captionFontSize)) pt",
                          value: $store.settings.captionFontSize, range: 36...120)
                    .anchored(.captionSize)
            }
        }
    }
}

// MARK: - Video

struct VideoSettingsPage: View {
    @Environment(Store.self) private var store
    var focus: SettingAnchor?

    var body: some View {
        @Bindable var store = store
        SettingsPage(title: "Video", focus: focus) {
            SettingsGroup(title: "Output",
                          footer: "Higher quality and frame rate make renders longer and files larger.") {
                SegmentRow(title: "Quality", options: [720, 1080, 1440], label: { "\($0)p" }, selection: $store.settings.shortEdge)
                    .anchored(.quality)
                SegmentRow(title: "Frame rate", options: [24, 30, 60], label: { "\($0) fps" }, selection: $store.settings.fps)
                    .anchored(.fps)
            }

            SettingsGroup(title: "Camera motion") {
                ToggleRow(title: "Ken Burns motion", subtitle: "Slow zoom and pan on each picture", isOn: $store.settings.kenBurns)
                    .anchored(.kenBurns)
                if store.settings.kenBurns {
                    SliderRow(title: "Motion amount", valueText: "\(Int(store.settings.kbIntensity * 100))%",
                              value: $store.settings.kbIntensity, range: 0.2...2)
                        .anchored(.motion)
                }
            }

            SettingsGroup(title: "Look",
                          footer: "Filters are an on-device stand-in for the PC's grain/light-leak look, generated procedurally rather than from the same source footage.") {
                ChoiceRow(title: "Film filter",
                          options: FilterStyle.allCases.map { ChoiceOption($0, $0.label) },
                          selection: $store.settings.filterStyle)
                    .anchored(.filterStyle)
                if store.settings.filterStyle != .none {
                    SliderRow(title: "Filter intensity", valueText: "\(Int(store.settings.filterIntensity))%",
                              value: $store.settings.filterIntensity, range: 0...100)
                        .anchored(.filterIntensity)
                }
            }
        }
    }
}

// MARK: - API keys

struct KeysSettingsPage: View {
    @Environment(SettingsCatalogs.self) private var catalogs
    var focus: SettingAnchor?
    @State private var savedKeys: Set<APIKey> = []
    @State private var editingKey: APIKey?
    @State private var refreshing = false

    var body: some View {
        SettingsPage(title: "API keys", focus: focus) {
            SettingsGroup(footer: "Stored in your iPhone's Keychain. Free options need no keys. AssemblyAI is optional and only affects caption timing accuracy.") {
                ForEach(Array(APIKey.allCases.enumerated()), id: \.element) { i, key in
                    Button {
                        Haptics.light()
                        editingKey = key
                    } label: {
                        HStack {
                            Text(key.label).foregroundStyle(Theme.ink)
                            Spacer()
                            KeyStatusPill(saved: savedKeys.contains(key))
                        }
                        .frame(minHeight: settingsRowMinHeight)
                        .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                    .accessibilityValue(savedKeys.contains(key) ? "Connected" : "No key")
                    .anchored(.apiKeys, if: i == 0)
                }
            }

            SettingsGroup(title: "Model lists",
                          footer: "Re-fetches every provider's live model list at once, instead of one at a time from its own picker.") {
                Button {
                    Haptics.light()
                    refreshing = true
                    Task {
                        await catalogs.refreshAll()
                        refreshing = false
                    }
                } label: {
                    HStack {
                        Text("Refresh all model lists").foregroundStyle(Theme.accent)
                        Spacer()
                        if refreshing { ProgressView() }
                    }
                    .frame(minHeight: settingsRowMinHeight)
                    .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .disabled(refreshing)
                .anchored(.refreshLists)
            }
        }
        .onAppear(perform: refreshSavedKeys)
        .sheet(item: $editingKey) { key in
            APIKeySheet(key: key) { refreshSavedKeys() }
        }
    }

    private func refreshSavedKeys() {
        savedKeys = Set(APIKey.allCases.filter { Keychain.has($0) })
    }
}

// MARK: - Fallbacks

struct FallbacksSettingsPage: View {
    @Environment(Store.self) private var store
    @Environment(SettingsCatalogs.self) private var catalogs
    var focus: SettingAnchor?

    var body: some View {
        @Bindable var store = store
        SettingsPage(title: "Fallbacks", focus: focus) {
            // The automatic-switch toggle governs ALL three fallbacks, so it
            // leads the page; it used to hang off the end of the voice
            // section, three sections below the ones it controls.
            SettingsGroup(footer: "With the switch on, a quota or billing error moves the writer, image or voice step to its fallback without asking. Otherwise you're asked first.") {
                ToggleRow(title: "Switch automatically", subtitle: "When a model is over quota or too expensive",
                          isOn: $store.settings.autoUseFallback)
                    .anchored(.autoFallback)
            }

            SettingsGroup(title: "Writer fallback", footer: "Used when the writer's main model is over quota, out of credit or too expensive.") {
                if let c = catalogs.writerCatalog(store.settings) {
                    ModelRow(title: "Fallback model", value: $store.settings.writerFallbackModel, catalog: c)
                        .anchored(.writerFallback)
                    InfoNote(text: "Empty = built-in light model (\(Recovery.fallbackModel(.writer, store: store) ?? "none")).")
                } else {
                    InfoNote(text: "\(store.settings.text.label) runs on your device, so there is no cheaper model to fall back to.")
                        .anchored(.writerFallback)
                }
            }

            SettingsGroup(title: "Image fallback", footer: "Used when the image model is over quota, out of credit or too expensive.") {
                if let c = catalogs.imageCatalog(store.settings) {
                    ModelRow(title: "Fallback model", value: $store.settings.imageFallbackModel, catalog: c)
                        .anchored(.imageFallback)
                    InfoNote(text: "Empty = built-in light model (\(Recovery.fallbackModel(.image, store: store) ?? "none")).")
                } else {
                    InfoNote(text: "\(store.settings.image.label) has a single model, so there is no cheaper one to fall back to.")
                        .anchored(.imageFallback)
                }
            }

            SettingsGroup(title: "Voice fallback", footer: "Used when the voice model is over quota, out of credit or too expensive.") {
                if let c = catalogs.voiceFallbackCatalog(store.settings) {
                    ModelRow(title: "Fallback model", value: $store.settings.voiceFallbackModel, catalog: c)
                        .anchored(.voiceFallback)
                    InfoNote(text: "Empty = built-in light model (\(Recovery.fallbackModel(.voice, store: store) ?? "none")).")
                } else {
                    InfoNote(text: "\(store.settings.voice.label) has no cheaper model to fall back to.")
                        .anchored(.voiceFallback)
                }
            }
        }
    }
}

// MARK: - Appearance

struct AppearanceSettingsPage: View {
    @Environment(Store.self) private var store
    var focus: SettingAnchor?
    @State private var draftTransparency = 0.0

    var body: some View {
        @Bindable var store = store
        SettingsPage(title: "Appearance", focus: focus) {
            SettingsGroup(title: "Look") {
                SegmentRow(title: "Theme", options: Array(ThemePreference.allCases), label: { $0.label },
                           selection: $store.settings.themePreference)
                    .anchored(.theme)
                SegmentRow(title: "Font", options: Array(AppFontChoice.allCases), label: { $0.label },
                           selection: $store.settings.appFont)
                    .anchored(.font)
            }

            SettingsGroup(title: "Glass",
                          footer: "Transparency applies to the whole app as soon as you let go of the slider. Font applies right away too; a few fixed-size headings and the tab bar keep their own type.") {
                // The slider edits a LOCAL value (cheap: only the small
                // preview redraws while dragging) and commits to the store -
                // which re-renders the whole app and writes to disk - once,
                // when the finger lifts. Writing every tick is what made it
                // laggy.
                SliderRow(title: "Glass transparency", valueText: "\(Int(draftTransparency * 100))%",
                          value: $draftTransparency, range: 0...1) { editing in
                    if !editing { store.settings.glassOpacity = 1 - draftTransparency * 0.85 }
                }
                .anchored(.glass)
                GlassPreview(level: 1 - draftTransparency * 0.85)
                    .padding(.vertical, 12)
                if store.settings.glassOpacity != 1 || draftTransparency != 0 {
                    Button {
                        Haptics.light()
                        draftTransparency = 0
                        store.settings.glassOpacity = 1
                    } label: {
                        Text("Reset to default").foregroundStyle(Theme.accent)
                            .frame(maxWidth: .infinity, minHeight: settingsRowMinHeight)
                            .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .onAppear { draftTransparency = (1 - store.settings.glassOpacity) / 0.85 }
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
        .accessibilityHidden(true)
    }

    private func previewGlass<S: InsettableShape>(_ shape: S) -> some View {
        ZStack {
            shape.fill(.ultraThinMaterial).opacity(level)
            shape.fill(Color.white.opacity(0.09 * level))
        }
        .overlay(shape.strokeBorder(Theme.glassEdge, lineWidth: 0.5))
    }
}

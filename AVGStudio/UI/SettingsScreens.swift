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

// MARK: - Performance

/// The levers that decide how fast and how hard the iPhone works. The PC offers
/// ffmpeg's `render_engine`, `render_codec` and `render_threads`; a phone has
/// different ones - which chip draws the frames, which format the encoder
/// writes, how many things run at once, and whether iOS is allowed to keep the
/// app going while it gets hot, locks, or is put away.
struct PerformanceSettingsPage: View {
    @Environment(Store.self) private var store
    var focus: SettingAnchor?

    var body: some View {
        @Bindable var store = store
        SettingsPage(title: "Performance", focus: focus) {
            SettingsGroup(title: "Rendering",
                          footer: "Automatic draws every frame on the GPU and falls back to the CPU only when iOS won't allow the GPU (the app in the background). CPU only is slower but never touches the GPU. HEVC makes smaller files; H.264 plays everywhere. Encoder quality sets how much detail the file keeps: Automatic lets iOS decide, the others ask for a fixed amount.") {
                ChoiceRow(title: "Render engine",
                          options: RenderEngine.allCases.map { ChoiceOption($0, $0.label) },
                          selection: $store.settings.renderEngine)
                    .anchored(.renderEngine)
                ChoiceRow(title: "Video format",
                          options: VideoCodec.allCases.map { ChoiceOption($0, $0.label) },
                          selection: $store.settings.videoCodec)
                    .anchored(.videoCodec)
                ChoiceRow(title: "Encoder quality",
                          options: EncoderQuality.allCases.map { ChoiceOption($0, $0.label) },
                          selection: $store.settings.encoderQuality)
                    .anchored(.encoderQuality)
            }

            SettingsGroup(title: "Working at once",
                          footer: "Extra videos wait in line and start as a slot frees; renders always take turns whatever this is. More images at once is faster, but free image makers rate-limit quickly - drop to 1 if they keep failing.") {
                SegmentRow(title: "Videos at once", options: [1, 2, 3], label: { "\($0)" },
                           selection: $store.settings.maxParallelJobs)
                    .anchored(.parallelJobs)
                SegmentRow(title: "Images at once", options: [1, 2, 3, 4], label: { "\($0)" },
                           selection: $store.settings.imageParallelism)
                    .anchored(.imageParallelism)
            }

            SettingsGroup(title: "Looking after the iPhone",
                          footer: "A hot iPhone slows itself down, so a long render only gets slower; this waits for it to cool, then carries on. A locked screen suspends the app (and its GPU access), so keeping the screen on is the surest way to finish quickly. Background work is slower and iOS may stop it early.") {
                ToggleRow(title: "Pause when the iPhone is hot", isOn: $store.settings.pauseWhenHot)
                    .anchored(.pauseWhenHot)
                ToggleRow(title: "Keep the screen on while working", isOn: $store.settings.keepScreenAwake)
                    .anchored(.keepAwake)
                ToggleRow(title: "Keep working in the background", isOn: $store.settings.allowBackgroundRendering)
                    .anchored(.backgroundRendering)
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
            SettingsGroup(footer: "Stored in your iPhone's Keychain. Free options need no keys. AssemblyAI is optional and only affects caption timing accuracy. Tap a service to also add a backup key: if the main one fails, the app uses the backup for 30 minutes.") {
                ForEach(Array(APIKey.allCases.enumerated()), id: \.element) { i, key in
                    Button {
                        Haptics.light()
                        editingKey = key
                    } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(key.label).foregroundStyle(Theme.ink)
                                if let caption = backupCaption(key) {
                                    Text(caption).font(.caption).foregroundStyle(Theme.ink2)
                                }
                            }
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

    /// "Backup key in use - 24 min left" / "Backup key saved" / nothing.
    private func backupCaption(_ key: APIKey) -> String? {
        guard Keychain.has(key, slot: .backup) else { return nil }
        if let left = KeyBackup.remaining(key) { return "Backup key in use - \(KeyBackup.remainingText(left))" }
        return "Backup key saved"
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
                    InfoNote(text: "Empty = automatic: the model that last worked, else a built-in light one (now: \(Recovery.fallbackModel(.writer, settings: store.settings, prefs: store.modelPrefs) ?? "none")).")
                } else {
                    InfoNote(text: "\(store.settings.text.label) runs on your device, so there is no cheaper model to fall back to.")
                        .anchored(.writerFallback)
                }
            }

            SettingsGroup(title: "Image fallback", footer: "Used when the image model is over quota, out of credit or too expensive.") {
                if let c = catalogs.imageCatalog(store.settings) {
                    ModelRow(title: "Fallback model", value: $store.settings.imageFallbackModel, catalog: c)
                        .anchored(.imageFallback)
                    InfoNote(text: "Empty = automatic: the model that last worked, else a built-in light one (now: \(Recovery.fallbackModel(.image, settings: store.settings, prefs: store.modelPrefs) ?? "none")).")
                } else {
                    InfoNote(text: "\(store.settings.image.label) has a single model, so there is no cheaper one to fall back to.")
                        .anchored(.imageFallback)
                }
            }

            SettingsGroup(title: "Voice fallback", footer: "Used when the voice model is over quota, out of credit or too expensive.") {
                if let c = catalogs.voiceFallbackCatalog(store.settings) {
                    ModelRow(title: "Fallback model", value: $store.settings.voiceFallbackModel, catalog: c)
                        .anchored(.voiceFallback)
                    InfoNote(text: "Empty = automatic: the model that last worked, else a built-in light one (now: \(Recovery.fallbackModel(.voice, settings: store.settings, prefs: store.modelPrefs) ?? "none")).")
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


// MARK: - Assistant

struct AssistantSettingsPage: View {
    @Environment(Store.self) private var store
    @Environment(SettingsCatalogs.self) private var catalogs
    var focus: SettingAnchor?
    @State private var testing = false
    @State private var testResult: Agent.SelfTestResult?
    @State private var cleared = false

    var body: some View {
        @Bindable var store = store
        SettingsPage(title: "Assistant", focus: focus) {
            SettingsGroup(title: "Model",
                          footer: "Your chats are sent to the service you pick. Models known to support tools: llama-3.3-70b-versatile, gpt-oss-120b, gpt-oss-20b.") {
                ChoiceRow(title: "Assistant provider",
                          options: AgentProviderKind.allCases.map { ChoiceOption($0, $0.label) },
                          selection: $store.settings.agentProvider)
                    .anchored(.assistantProvider)
                switch store.settings.agentProvider {
                case .groq:
                    ModelRow(title: "Groq model", value: $store.settings.agentGroqModel, catalog: catalogs.groqText)
                        .anchored(.assistantModel)
                case .gemini:
                    ModelRow(title: "Gemini model", value: $store.settings.agentGeminiModel, catalog: catalogs.geminiText)
                        .anchored(.assistantModel)
                }
                testRow.anchored(.assistantTest)
            }

            SettingsGroup(title: "Reliability",
                          footer: "Separate from the writer's fallbacks. Needs a key for the other service.") {
                ToggleRow(title: "Switch assistant on limits",
                          subtitle: "If this service is rate-limited or out of quota, try the other one.",
                          isOn: $store.settings.agentFailover)
                    .anchored(.assistantFailover)
            }

            // What the assistant is allowed to try. To the model each one is a
            // tool: switched off, it is not even offered.
            SettingsGroup(title: "Assistant capabilities · \(AgentCapability.enabledCount(store.settings)) of \(AgentCapability.allCases.count) on",
                          footer: "These are the tools the assistant can use to control the app. A capability that is off is not offered to it at all. Anything that changes something still asks you first. It can never read your API keys or change its own limits.") {
                ForEach(AgentCapability.allCases) { capability in
                    ToggleRow(title: capability.title, subtitle: capability.summary,
                              isOn: Binding(get: { store.settings[keyPath: capability.keyPath] },
                                            set: { store.settings[keyPath: capability.keyPath] = $0 }))
                        .anchored(Self.anchor(for: capability))
                }
            }

            SettingsGroup(title: "Asking and spending",
                          footer: "Videos that cost money always ask. The limit counts approved estimates, resets on New chat and on relaunch, and is a guard rail, not a bill.") {
                ChoiceRow(title: "Ask before starting",
                          options: AgentApprovalMode.allCases.map { ChoiceOption($0, $0.label) },
                          selection: $store.settings.agentApproval)
                    .anchored(.assistantApproval)
                StepperRow(title: "Spend limit per chat",
                           valueText: Agent.dollars(AgentSettingsRules.spendCap(store.settings.agentChatSpendCapUSD)),
                           value: $store.settings.agentChatSpendCapUSD,
                           range: AgentSettingsRules.spendCapRange,
                           step: AgentSettingsRules.spendCapStep)
                    .anchored(.assistantSpendCap)
            }

            SettingsGroup(title: "Replies") {
                SegmentRow(title: "Reply length", options: AgentReplyStyle.allCases, label: { $0.label },
                           selection: $store.settings.agentReplyStyle)
                    .anchored(.assistantReplyStyle)
                ToggleRow(title: "Announce finished videos",
                          subtitle: "Posts \u{201C}ready\u{201D} or \u{201C}stopped\u{201D} in the chat for videos it started. Uses no tokens.",
                          isOn: $store.settings.agentAnnounceResults)
                    .anchored(.assistantAnnounce)
            }

            SettingsGroup(title: "Memory",
                          footer: "Saves only your messages and its answers, on this phone.") {
                ToggleRow(title: "Keep chat", isOn: $store.settings.agentKeepChat)
                    .anchored(.assistantKeepChat)
                clearRow.anchored(.assistantClear)
            }

            SettingsGroup(title: "Custom instructions",
                          footer: "Sent with every message. They never turn off asking. Don't put keys here.") {
                FieldRow(title: "Custom instructions", placeholder: "For example: use my Facts channel",
                         text: Binding(
                            get: { store.settings.agentInstructions },
                            set: { store.settings.agentInstructions = String($0.prefix(AgentSettingsRules.instructionsLimit)) }))
                    .anchored(.assistantInstructions)
                Text("\(store.settings.agentInstructions.count)/\(AgentSettingsRules.instructionsLimit)")
                    .font(.caption2).foregroundStyle(Theme.ink2)
                    .frame(maxWidth: .infinity, alignment: .trailing)
                    .padding(.bottom, 8)
            }
        }
        .onChange(of: store.settings.agentProvider) { _, _ in testResult = nil }
        .onChange(of: store.settings.agentKeepChat) { _, on in
            if !on { store.agent.deleteSavedChat() }
        }
    }

    private static func anchor(for capability: AgentCapability) -> SettingAnchor {
        switch capability {
        case .startVideos: return .assistantAllowCreate
        case .redoSteps: return .assistantAllowRedo
        case .cancelRuns: return .assistantAllowCancel
        case .manageProjects: return .assistantAllowManage
        case .changeSettings: return .assistantAllowSettings
        case .editChannels: return .assistantAllowChannels
        case .diagnostics: return .assistantAllowDiagnostics
        }
    }

    private var testRow: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button {
                Haptics.light()
                runTest()
            } label: {
                HStack(spacing: 12) {
                    Text("Test assistant").foregroundStyle(Theme.ink)
                    Spacer(minLength: 8)
                    if testing {
                        ProgressView()
                    } else {
                        Image(systemName: "checkmark.seal").foregroundStyle(Theme.ink2)
                    }
                }
                .frame(minHeight: settingsRowMinHeight)
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .disabled(testing)
            .accessibilityLabel("Test assistant")
            if let result = testResult {
                switch result {
                case .ok(let label):
                    Label("Works: \(label)", systemImage: "checkmark.circle.fill")
                        .font(.footnote).foregroundStyle(Theme.ok)
                case .failed(let why):
                    Label(why, systemImage: "exclamationmark.triangle.fill")
                        .font(.footnote).foregroundStyle(Theme.bad)
                }
            }
        }
        .padding(.bottom, testResult == nil ? 0 : 10)
    }

    private var clearRow: some View {
        Button {
            Haptics.light()
            store.agent.deleteSavedChat()
            cleared = true
        } label: {
            HStack(spacing: 12) {
                Text("Clear saved chat").foregroundStyle(Theme.ink)
                Spacer(minLength: 8)
                if cleared {
                    Text("Cleared").font(.footnote).foregroundStyle(Theme.ink2)
                } else {
                    Image(systemName: "trash").foregroundStyle(Theme.ink2)
                }
            }
            .frame(minHeight: settingsRowMinHeight)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Clear saved chat")
    }

    private func runTest() {
        testing = true
        testResult = nil
        Task {
            let result = await store.agent.selfTest()
            testResult = result
            testing = false
        }
    }
}

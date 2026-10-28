import SwiftUI

// MARK: - Routing & search index
//
// Settings used to be ONE page: ~14 stacked sections in a system `Form`, so
// "Appearance" sat about eight screens of scrolling below the top, every
// picker was a menu that popped up wherever its row happened to be, and the
// small controls (32pt steppers, 34pt segments) needed a precise thumb.
//
// The rebuild is about REACH:
//   * a short hub (ten big rows, each showing its current value, so most
//     checks need no tap at all), never more than two taps to any setting;
//   * search is the system's own field (`.searchable`, a Liquid Glass bar where
//     iOS puts it), with the Quick settings button in the toolbar beside it;
//   * every choice opens a bottom sheet of large rows instead of a menu;
//   * rows are >= 56pt, steppers/segments are >= 44pt, and a whole toggle
//     row is the tap target, not just the small switch;
//   * search jumps straight to the exact row and flashes it.

/// Every screen the hub can open a setting on.
enum SettingsCategory: String, Hashable, CaseIterable, Identifiable {
    case writing, images, voice, captions, video, performance, assistant, keys, fallbacks, appearance
    var id: String { rawValue }

    var title: String {
        switch self {
        case .writing: return "Writing"
        case .images: return "Images"
        case .voice: return "Voice"
        case .captions: return "Captions"
        case .video: return "Video"
        case .performance: return "Performance"
        case .assistant: return "Assistant"
        case .keys: return "API keys"
        case .fallbacks: return "Fallbacks"
        case .appearance: return "Appearance"
        }
    }

    var symbol: String {
        switch self {
        case .writing: return "pencil.and.scribble"
        case .images: return "photo.on.rectangle.angled"
        case .voice: return "waveform"
        case .captions: return "captions.bubble"
        case .video: return "film"
        case .performance: return "speedometer"
        case .assistant: return "sparkle"
        case .keys: return "key.fill"
        case .fallbacks: return "arrow.triangle.branch"
        case .appearance: return "circle.lefthalf.filled"
        }
    }
}

/// One specific setting row - the target a search result scrolls to.
enum SettingAnchor: String, Hashable, CaseIterable {
    case writer, writerModel, dynamicPauses, pauseLength
    case imageProvider, imageModel, imageStyle, reusableImages
    case voiceProvider, voicePick, voiceModel
    case captionsToggle, captionAnimation, captionWords, captionPosition, captionSize
    case quality, fps, kenBurns, motion, filterStyle, filterIntensity
    case renderEngine, videoCodec, encoderQuality, parallelJobs, imageParallelism, pauseWhenHot, keepAwake, backgroundRendering
    case apiKeys, refreshLists
    case autoFallback, writerFallback, imageFallback, voiceFallback
    case theme, font, glass
    case assistantProvider, assistantModel, assistantTest, assistantFailover
    case assistantAllowCreate, assistantAllowRedo, assistantAllowCancel
    case assistantAllowManage, assistantAllowSettings, assistantAllowChannels, assistantAllowDiagnostics
    case assistantApproval, assistantSpendCap, assistantReplyStyle, assistantAnnounce
    case assistantKeepChat, assistantClear, assistantInstructions
}

enum SettingsRoute: Hashable {
    case category(SettingsCategory, focus: SettingAnchor?)
    case health
    case activity
    case reusableImages
    case music
    case soundEffects
}

/// The searchable list of settings. `SettingAnchor.allCases` must all appear
/// here (a test enforces it), so a new setting cannot ship unsearchable.
enum SettingsIndex {
    struct Entry: Identifiable {
        let title: String
        let place: String
        let symbol: String
        let keywords: String
        let route: SettingsRoute
        let anchor: SettingAnchor?
        var id: String { title }
    }

    private static func e(_ title: String, _ category: SettingsCategory, _ anchor: SettingAnchor, _ keywords: String) -> Entry {
        Entry(title: title, place: category.title, symbol: category.symbol, keywords: keywords,
              route: .category(category, focus: anchor), anchor: anchor)
    }

    static let all: [Entry] = [
        e("Writer", .writing, .writer, "text ai provider script groq gemini pollinations apple intelligence"),
        e("Writer model", .writing, .writerModel, "model groq gemini pollinations"),
        e("Dynamic pauses", .writing, .dynamicPauses, "pause silence narration held beat"),
        e("Pause length", .writing, .pauseLength, "pause milliseconds ms silence"),
        e("Image maker", .images, .imageProvider, "images provider art pollinations gemini stable horde openai"),
        e("Image model", .images, .imageModel, "model stable horde pollinations gemini"),
        e("Image style", .images, .imageStyle, "prompt look cinematic art style"),
        e("Reusable images", .images, .reusableImages, "pool recycle library shared"),
        e("Voice provider", .voice, .voiceProvider, "narrator tts apple elevenlabs gemini speech"),
        e("Narrator voice", .voice, .voicePick, "voice apple elevenlabs voice id name"),
        e("Voice model", .voice, .voiceModel, "tts model elevenlabs gemini"),
        e("Burn in captions", .captions, .captionsToggle, "subtitles text on video on off"),
        e("Caption animation", .captions, .captionAnimation, "pop fade rise highlight karaoke typewriter"),
        e("Words per caption", .captions, .captionWords, "max words line"),
        e("Caption position", .captions, .captionPosition, "top center bottom placement"),
        e("Caption size", .captions, .captionSize, "font size text"),
        e("Quality", .video, .quality, "resolution 720p 1080p 1440p size"),
        e("Frame rate", .video, .fps, "fps 24 30 60 smooth"),
        e("Ken Burns motion", .video, .kenBurns, "zoom pan movement camera"),
        e("Motion amount", .video, .motion, "ken burns intensity strength"),
        e("Render engine", .performance, .renderEngine, "gpu cpu metal graphics speed fast slow software"),
        e("Video format", .performance, .videoCodec, "codec hevc h264 h.265 h.264 compatible file size"),
        e("Encoder quality", .performance, .encoderQuality, "bitrate quality size compact balanced high compression"),
        e("Videos at once", .performance, .parallelJobs, "queue parallel jobs simultaneous concurrent render at the same time"),
        e("Images at once", .performance, .imageParallelism, "parallel concurrent image generation batch rate limit speed"),
        e("Pause when hot", .performance, .pauseWhenHot, "thermal heat temperature overheating cool throttle"),
        e("Keep screen on", .performance, .keepAwake, "auto-lock sleep awake idle screen timeout stall"),
        e("Keep working in background", .performance, .backgroundRendering, "background app switch leave continue"),
        e("Film filter", .video, .filterStyle, "filter grain light leak look"),
        e("Filter intensity", .video, .filterIntensity, "filter strength amount grain leak"),
        e("Assistant provider", .assistant, .assistantProvider, "assistant agent chat ai groq gemini service"),
        e("Assistant model", .assistant, .assistantModel, "assistant agent chat model groq gemini llama"),
        e("Test assistant", .assistant, .assistantTest, "assistant agent check connection key works tools verify"),
        e("Switch assistant on limits", .assistant, .assistantFailover, "assistant agent failover backup other rate limit quota"),
        e("Start videos", .assistant, .assistantAllowCreate, "assistant agent capabilities tools permission allow create make new"),
        e("Redo and resume steps", .assistant, .assistantAllowRedo, "assistant agent capabilities tools permission allow redo rerun restart resume"),
        e("Stop running videos", .assistant, .assistantAllowCancel, "assistant agent capabilities tools permission allow stop cancel"),
        e("Manage projects", .assistant, .assistantAllowManage, "assistant agent capabilities tools permission allow rename archive delete projects"),
        e("Change app settings", .assistant, .assistantAllowSettings, "assistant agent capabilities tools permission allow change settings writer voice quality captions"),
        e("Edit channels", .assistant, .assistantAllowChannels, "assistant agent capabilities tools permission allow channel niche tone language length switch"),
        e("Read settings and activity", .assistant, .assistantAllowDiagnostics, "assistant agent capabilities tools permission allow read status log errors diagnostics"),
        e("Ask before starting", .assistant, .assistantApproval, "assistant agent approve approval confirm ask tap paid free"),
        e("Spend limit per chat", .assistant, .assistantSpendCap, "assistant agent budget cost money dollars cap"),
        e("Reply length", .assistant, .assistantReplyStyle, "assistant agent brief short long verbose answers"),
        e("Announce finished videos", .assistant, .assistantAnnounce, "assistant agent notify ready done finished haptic"),
        e("Keep chat", .assistant, .assistantKeepChat, "assistant agent memory history save remember conversation"),
        e("Clear saved chat", .assistant, .assistantClear, "assistant agent delete erase memory history conversation"),
        e("Custom instructions", .assistant, .assistantInstructions, "assistant agent prompt rules preferences standing system"),
        e("API keys", .keys, .apiKeys, "keychain gemini groq openai elevenlabs assemblyai stable horde pollinations"),
        e("Refresh model lists", .keys, .refreshLists, "model cache catalog update"),
        e("Switch automatically", .fallbacks, .autoFallback, "fallback quota billing expensive cheaper"),
        e("Writer fallback", .fallbacks, .writerFallback, "fallback model writer cheaper"),
        e("Image fallback", .fallbacks, .imageFallback, "fallback model image cheaper"),
        e("Voice fallback", .fallbacks, .voiceFallback, "fallback model voice cheaper"),
        e("Theme", .appearance, .theme, "night day auto dark light appearance"),
        e("Font", .appearance, .font, "typeface caprasimo system text"),
        e("Glass transparency", .appearance, .glass, "opacity blur translucent see through"),
        Entry(title: "App health", place: "App", symbol: "heart.text.square", keywords: "status providers keys storage diagnostics check",
              route: .health, anchor: nil),
        Entry(title: "Activity & errors", place: "App", symbol: "list.bullet.rectangle", keywords: "log errors history problems",
              route: .activity, anchor: nil),
    ]

    /// Every word of the query must appear in the title, keywords or place;
    /// title matches rank first, in index order within a rank.
    static func search(_ query: String) -> [Entry] {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !q.isEmpty else { return [] }
        let tokens = q.split(separator: " ").map(String.init)
        return all.enumerated().compactMap { i, entry -> (score: Int, index: Int, entry: Entry)? in
            let title = entry.title.lowercased()
            let hay = "\(title) \(entry.keywords.lowercased()) \(entry.place.lowercased())"
            guard tokens.allSatisfy({ hay.contains($0) }) else { return nil }
            let score = title == q ? 0 : (title.hasPrefix(q) ? 1 : (title.contains(q) ? 2 : 3))
            return (score, i, entry)
        }
        .sorted { ($0.score, $0.index) < ($1.score, $1.index) }
        .map(\.entry)
    }
}

// MARK: - Page, group and focus

private struct SettingsFocusKey: EnvironmentKey { static let defaultValue: SettingAnchor? = nil }
extension EnvironmentValues {
    var settingsFocus: SettingAnchor? {
        get { self[SettingsFocusKey.self] }
        set { self[SettingsFocusKey.self] = newValue }
    }
}

/// Tags a row so a search result can scroll to it, then flashes it.
private struct AnchoredRow: ViewModifier {
    let anchor: SettingAnchor
    @Environment(\.settingsFocus) private var focus
    @State private var flash = false

    func body(content: Content) -> some View {
        content
            .id(anchor)
            .background {
                // Bleeds 8pt past the row's text into the card's own inset, so
                // the flash reads as a rounded highlight, not a hard bar.
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(Theme.accent.opacity(flash ? 0.18 : 0))
                    .padding(.horizontal, -8)
            }
            .task(id: focus) {
                guard focus == anchor else { return }
                try? await Task.sleep(for: .milliseconds(500))
                withAnimation(.easeOut(duration: 0.25)) { flash = true }
                try? await Task.sleep(for: .milliseconds(1300))
                withAnimation(.easeOut(duration: 0.5)) { flash = false }
            }
    }
}

extension View {
    func anchored(_ anchor: SettingAnchor) -> some View { modifier(AnchoredRow(anchor: anchor)) }

    /// Anchors only when `condition` holds (e.g. the FIRST of several rows).
    @ViewBuilder func anchored(_ anchor: SettingAnchor, if condition: Bool) -> some View {
        if condition { modifier(AnchoredRow(anchor: anchor)) } else { self }
    }
}

/// Scrolling glass-card page for one settings category.
struct SettingsPage<Content: View>: View {
    let title: String
    var focus: SettingAnchor?
    @ViewBuilder var content: Content

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(spacing: 20) { content }
                    .padding(.horizontal, 16)
                    .padding(.top, 8)
                    .padding(.bottom, 32)
            }
            .scrollDismissesKeyboard(.interactively)
            .task {
                guard let focus else { return }
                try? await Task.sleep(for: .milliseconds(250))
                withAnimation(.easeInOut(duration: 0.35)) { proxy.scrollTo(focus, anchor: .center) }
            }
        }
        .environment(\.settingsFocus, focus)
        .screenBackground()
        .tint(Theme.accent)
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
    }
}

/// A titled glass card whose rows are separated by hairlines.
struct SettingsGroup<Content: View>: View {
    var title: String?
    var footer: String?
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let title { Text(title).sectionHeader().padding(.leading, 6) }
            VStack(spacing: 0) {
                Group(subviews: content) { subviews in
                    ForEach(subviews.indices, id: \.self) { i in
                        if i > 0 { Rectangle().fill(Theme.glassEdge).frame(height: 0.5) }
                        subviews[i]
                    }
                }
            }
            .padding(.horizontal, 16)
            .glassCard(24, padding: 0)
            if let footer {
                Text(footer).font(.footnote).foregroundStyle(Theme.ink2).padding(.horizontal, 6)
            }
        }
    }
}

// MARK: - Rows

/// Minimum row height: comfortably above the 44pt touch target so a thumb
/// does not have to aim.
let settingsRowMinHeight: CGFloat = 56

struct RowText: View {
    let title: String
    var subtitle: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).foregroundStyle(Theme.ink)
            if let subtitle, !subtitle.isEmpty {
                Text(subtitle).font(.caption).foregroundStyle(Theme.ink2)
            }
        }
    }
}

/// A row that opens another screen (hub rows, links to tools).
struct NavRow: View {
    let symbol: String
    let title: String
    let detail: String
    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: symbol)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(Theme.accent)
                .frame(width: 36, height: 36)
                .background(Theme.accent.opacity(0.16), in: .circle)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.body.weight(.semibold)).foregroundStyle(Theme.ink)
                Text(detail).font(.footnote).foregroundStyle(Theme.ink2).lineLimit(1)
            }
            Spacer(minLength: 8)
            Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(Theme.ink2)
        }
        .frame(minHeight: 64)
        .contentShape(.rect)
    }
}

/// The WHOLE row is the tap target, not just the small switch.
struct ToggleRow: View {
    let title: String
    var subtitle: String?
    @Binding var isOn: Bool

    var body: some View {
        Button {
            Haptics.light()
            withAnimation(.snappy) { isOn.toggle() }
        } label: {
            HStack(spacing: 12) {
                RowText(title: title, subtitle: subtitle)
                Spacer(minLength: 8)
                Toggle("", isOn: $isOn).labelsHidden().tint(Theme.accent).allowsHitTesting(false)
            }
            .frame(minHeight: settingsRowMinHeight)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(title)
        .accessibilityValue(isOn ? "On" : "Off")
        .accessibilityAddTraits(.isToggle)
    }
}

/// Full-width segmented choice for 2-4 short options (44pt segments).
struct SegmentRow<T: Hashable>: View {
    let title: String
    let options: [T]
    let label: (T) -> String
    @Binding var selection: T

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).foregroundStyle(Theme.ink)
            SegmentedFilter(options: options, label: label, selection: $selection, verticalPadding: 12)
        }
        .padding(.vertical, 12)
    }
}

struct ChoiceOption<T: Hashable>: Identifiable {
    let value: T
    let title: String
    var subtitle: String?
    var id: T { value }
    init(_ value: T, _ title: String, subtitle: String? = nil) {
        self.value = value; self.title = title; self.subtitle = subtitle
    }
}

/// A choice from a longer list. Opens a BOTTOM sheet of large rows instead
/// of a menu, which pops up wherever the row happens to sit (often the top
/// of the screen, out of one-handed reach).
struct ChoiceRow<T: Hashable>: View {
    let title: String
    var subtitle: String?
    let options: [ChoiceOption<T>]
    @Binding var selection: T
    @State private var open = false

    private var current: String { options.first { $0.value == selection }?.title ?? "" }

    var body: some View {
        Button {
            Haptics.light()
            open = true
        } label: {
            HStack(spacing: 12) {
                RowText(title: title, subtitle: subtitle)
                Spacer(minLength: 8)
                Text(current).foregroundStyle(Theme.ink2).lineLimit(1).multilineTextAlignment(.trailing)
                Image(systemName: "chevron.up.chevron.down").font(.caption.weight(.semibold)).foregroundStyle(Theme.ink2)
            }
            .frame(minHeight: settingsRowMinHeight)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityValue(current)
        .sheet(isPresented: $open) {
            OptionSheet(title: title, options: options, selection: $selection)
        }
    }
}

private struct OptionSheet<T: Hashable>: View {
    @Environment(\.dismiss) private var dismiss
    let title: String
    let options: [ChoiceOption<T>]
    @Binding var selection: T

    var body: some View {
        GlassSheet(title: title) {
            LazyVStack(spacing: 0) {
                ForEach(Array(options.enumerated()), id: \.element.id) { i, option in
                    if i > 0 { Rectangle().fill(Theme.glassEdge).frame(height: 0.5) }
                    Button {
                        Haptics.light()
                        selection = option.value
                        dismiss()
                    } label: {
                        HStack(spacing: 12) {
                            RowText(title: option.title, subtitle: option.subtitle)
                            Spacer(minLength: 8)
                            if option.value == selection {
                                Image(systemName: "checkmark").font(.body.weight(.semibold)).foregroundStyle(Theme.accent)
                            }
                        }
                        .frame(minHeight: settingsRowMinHeight)
                        .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(option.value == selection ? .isSelected : [])
                }
            }
            .padding(.horizontal, 16)
            .glassCard(24, padding: 0)
        }
    }
}

struct SliderRow: View {
    let title: String
    let valueText: String
    @Binding var value: Double
    let range: ClosedRange<Double>
    var onEditingChanged: (Bool) -> Void = { _ in }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(title).foregroundStyle(Theme.ink)
                Spacer()
                Text(valueText).foregroundStyle(Theme.ink2).monospacedDigit()
            }
            Slider(value: $value, in: range, onEditingChanged: onEditingChanged).tint(Theme.accent)
        }
        .padding(.vertical, 12)
    }
}

/// − / + with 44pt buttons on the trailing (right-thumb) edge; replaces the
/// system `Stepper`, whose halves are far smaller than a thumb.
struct StepperRow<V: SignedNumeric & Comparable>: View {
    let title: String
    let valueText: String
    @Binding var value: V
    let range: ClosedRange<V>
    let step: V

    private func bump(_ delta: V) {
        let next = value + delta
        value = min(max(next, range.lowerBound), range.upperBound)
    }

    var body: some View {
        HStack(spacing: 8) {
            Text(title).foregroundStyle(Theme.ink)
            Spacer(minLength: 8)
            stepButton("minus", label: "Decrease", enabled: value > range.lowerBound) { bump(-step) }
            Text(valueText).foregroundStyle(Theme.ink).monospacedDigit().frame(minWidth: 64)
            stepButton("plus", label: "Increase", enabled: value < range.upperBound) { bump(step) }
        }
        .frame(minHeight: settingsRowMinHeight)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(title)
        .accessibilityValue(valueText)
        .accessibilityAdjustableAction { direction in
            switch direction {
            case .increment: bump(step)
            case .decrement: bump(-step)
            @unknown default: break
            }
        }
    }

    private func stepButton(_ symbol: String, label: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button {
            Haptics.light()
            action()
        } label: {
            Image(systemName: symbol)
                .font(.system(size: 16, weight: .bold))
                .foregroundStyle(enabled ? Theme.ink : Theme.ink2.opacity(0.5))
                .frame(width: 44, height: 44)
                .glassCircle()
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .accessibilityLabel(label)
    }
}

struct FieldRow: View {
    let title: String
    var placeholder: String = ""
    @Binding var text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).foregroundStyle(Theme.ink)
            TextField(placeholder, text: $text, axis: .vertical)
                .lineLimit(1...4)
                .fieldStyle(14)
        }
        .padding(.vertical, 12)
    }
}

struct InfoNote: View {
    let text: String
    var body: some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(Theme.ink2)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 12)
    }
}

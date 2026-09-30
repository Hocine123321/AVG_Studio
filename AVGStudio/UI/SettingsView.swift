import SwiftUI

/// Settings hub. See SettingsKit.swift for the reasoning: a short list of big
/// rows that each show their current value, a search field and a Quick
/// settings button docked at the BOTTOM (the thumb zone, not a nav bar), and
/// one sub-screen per category - never more than two taps to any setting.
struct SettingsView: View {
    @Environment(Store.self) private var store
    @State private var catalogs = SettingsCatalogs()
    @State private var query = ""
    @State private var showQuick = false
    @State private var savedKeyCount = 0
    @FocusState private var searchFocused: Bool

    private var searching: Bool {
        !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                if searching { results } else { hub }
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .padding(.bottom, 24)
        }
        .scrollDismissesKeyboard(.interactively)
        .screenBackground()
        .tint(Theme.accent)
        .navigationTitle("Settings")
        .safeAreaInset(edge: .bottom) { dock }
        .navigationDestination(for: SettingsRoute.self) { destination($0) }
        .sheet(isPresented: $showQuick) { QuickSettingsSheet() }
        .onAppear { savedKeyCount = APIKey.allCases.filter { Keychain.has($0) }.count }
        // Switching a provider refreshes that provider's model list (and its
        // cache) automatically, so nobody has to press refresh first. Lives
        // on the hub, which stays alive underneath every pushed screen.
        .onChange(of: store.settings.text) { _, _ in Task { await catalogs.refreshCurrent(store.settings) } }
        .onChange(of: store.settings.image) { _, _ in Task { await catalogs.refreshCurrent(store.settings) } }
        .onChange(of: store.settings.voice) { _, _ in Task { await catalogs.refreshCurrent(store.settings) } }
    }

    // MARK: Hub

    private var hub: some View {
        VStack(spacing: 20) {
            SettingsGroup(title: "Create") {
                row(.writing, writingSummary)
                row(.images, imagesSummary)
                row(.voice, store.settings.voice.label)
                row(.captions, captionsSummary)
                row(.video, videoSummary)
            }
            SettingsGroup(title: "Accounts") {
                row(.keys, "\(savedKeyCount) of \(APIKey.allCases.count) connected")
                row(.fallbacks, store.settings.autoUseFallback ? "Switches automatically when a model is too expensive" : "Asks before switching models")
            }
            SettingsGroup(title: "App") {
                row(.appearance, "\(store.settings.themePreference.label) · \(store.settings.appFont.label)")
                NavigationLink(value: SettingsRoute.health) {
                    NavRow(symbol: "heart.text.square", title: "App health", detail: "Providers, keys and storage")
                }
                .buttonStyle(.plain)
                NavigationLink(value: SettingsRoute.activity) {
                    NavRow(symbol: "list.bullet.rectangle", title: "Activity & errors", detail: errorSummary)
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func row(_ category: SettingsCategory, _ detail: String) -> some View {
        NavigationLink(value: SettingsRoute.category(category, focus: nil)) {
            NavRow(symbol: category.symbol, title: category.title, detail: detail)
        }
        .buttonStyle(.plain)
    }

    // Live summaries: the hub answers "what is it set to?" without a tap.

    private var writingSummary: String {
        let s = store.settings
        switch s.text {
        case .gemini: return "\(s.text.label) · \(s.geminiModel)"
        case .groq: return "\(s.text.label) · \(s.groqModel)"
        case .pollinations: return "\(s.text.label) · \(s.pollinationsTextModel)"
        case .apple: return s.text.label
        }
    }

    private var imagesSummary: String {
        let s = store.settings
        switch s.image {
        case .stablehorde: return "\(s.image.label) · \(s.stableHordeModel)"
        case .pollinations: return "\(s.image.label) · \(s.pollinationsImageModel)"
        case .gemini: return "\(s.image.label) · \(s.geminiImageModel)"
        default: return s.image.label
        }
    }

    private var captionsSummary: String {
        store.settings.captionsEnabled ? "On · \(store.settings.captionAnimationStyle.label)" : "Off"
    }

    private var videoSummary: String {
        let s = store.settings
        return "\(s.shortEdge)p · \(s.fps) fps · Ken Burns \(s.kenBurns ? "on" : "off")"
    }

    private var errorSummary: String {
        let n = store.log.filter(\.isError).count
        return n == 0 ? "No errors" : (n == 1 ? "1 error" : "\(n) errors")
    }

    // MARK: Search results

    @ViewBuilder private var results: some View {
        let found = SettingsIndex.search(query)
        if found.isEmpty {
            ContentUnavailableView.search(text: query).padding(.top, 40)
        } else {
            SettingsGroup {
                ForEach(found) { entry in
                    NavigationLink(value: entry.route) {
                        NavRow(symbol: entry.symbol, title: entry.title, detail: entry.place)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    // MARK: Bottom dock

    private var dock: some View {
        HStack(spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").foregroundStyle(Theme.ink2)
                TextField("Search settings", text: $query)
                    .focused($searchFocused)
                    .submitLabel(.search)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                if !query.isEmpty {
                    Button {
                        query = ""
                    } label: {
                        Image(systemName: "xmark.circle.fill").foregroundStyle(Theme.ink2).frame(width: 32, height: 32)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Clear search")
                }
            }
            .padding(.horizontal, 14)
            .frame(height: 48)
            .glass(Capsule())

            Button {
                Haptics.light()
                searchFocused = false
                showQuick = true
            } label: {
                Image(systemName: "slider.horizontal.3")
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(Theme.accent)
                    .frame(width: 48, height: 48)
                    .glassCircle()
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Quick settings")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    // MARK: Destinations

    @ViewBuilder private func destination(_ route: SettingsRoute) -> some View {
        switch route {
        case .category(let category, let focus):
            page(category, focus: focus).environment(catalogs)
        case .health:
            HealthView()
        case .activity:
            ErrorLogView()
        case .reusableImages:
            ReusableImagesView()
        }
    }

    @ViewBuilder private func page(_ category: SettingsCategory, focus: SettingAnchor?) -> some View {
        switch category {
        case .writing: WritingSettingsPage(focus: focus)
        case .images: ImagesSettingsPage(focus: focus)
        case .voice: VoiceSettingsPage(focus: focus)
        case .captions: CaptionsSettingsPage(focus: focus)
        case .video: VideoSettingsPage(focus: focus)
        case .keys: KeysSettingsPage(focus: focus)
        case .fallbacks: FallbacksSettingsPage(focus: focus)
        case .appearance: AppearanceSettingsPage(focus: focus)
        }
    }
}

/// The handful of settings people flip most, one tap from the hub and in a
/// bottom sheet - so the commonest changes never need a navigation at all.
private struct QuickSettingsSheet: View {
    @Environment(Store.self) private var store

    var body: some View {
        @Bindable var store = store
        GlassSheet(title: "Quick settings") {
            SettingsGroup {
                SegmentRow(title: "Theme", options: Array(ThemePreference.allCases), label: { $0.label },
                           selection: $store.settings.themePreference)
                ToggleRow(title: "Burn in captions", isOn: $store.settings.captionsEnabled)
                ToggleRow(title: "Ken Burns motion", isOn: $store.settings.kenBurns)
                SegmentRow(title: "Quality", options: [720, 1080, 1440], label: { "\($0)p" },
                           selection: $store.settings.shortEdge)
                SegmentRow(title: "Frame rate", options: [24, 30, 60], label: { "\($0) fps" },
                           selection: $store.settings.fps)
            }
        }
    }
}

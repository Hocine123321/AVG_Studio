import SwiftUI

@main
struct AVGStudioApp: App {
    @State private var store = Store()

    init() {
        // Must be registered before the app finishes launching; a struct
        // App's `init()` runs at that point, which is the documented place
        // to do this in a SwiftUI-lifecycle app.
        BackgroundRender.register()
        // Belt-and-suspenders alongside Info.plist's UIAppFonts entry - see
        // Theme.registerFonts's own doc comment.
        Theme.registerFonts()
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(store)
                .tint(Theme.accent)
        }
    }
}

struct RootView: View {
    @Environment(Store.self) private var store
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        @Bindable var store = store
        Group {
            if !store.settings.onboarded {
                WelcomeView()
            } else {
                // The native tab bar chrome is hidden in favour of
                // FloatingTabBar below (the mock's own `.tabbar.g`) - the
                // five `Tab`s themselves stay real system tabs (still get
                // proper state restoration/accessibility), only their
                // built-in bottom bar rendering is suppressed.
                TabView(selection: $store.tab) {
                    Tab("Library", systemImage: "square.grid.2x2.fill", value: AppTab.library) {
                        NavigationStack { HomeView() }
                    }
                    // Skin v2: Studio is a persistent tab now (shows
                    // Store.currentProject), not a screen pushed from a
                    // library card tap - see SKIN_V2_PLAN.md's "Navigation"
                    // note and Store.selectForStudio.
                    Tab("Studio", systemImage: "clapperboard.fill", value: AppTab.studio) {
                        NavigationStack { StudioView() }
                    }
                    Tab("Create", systemImage: "sparkles", value: AppTab.create) {
                        NavigationStack { CreateView() }
                    }
                    Tab("Channels", systemImage: "person.2.wave.2.fill", value: AppTab.channels) {
                        NavigationStack { ChannelsView() }
                    }
                    Tab("Settings", systemImage: "gearshape.fill", value: AppTab.settings) {
                        NavigationStack { SettingsView() }
                    }
                }
                .toolbar(.hidden, for: .tabBar)
                .safeAreaInset(edge: .bottom) { FloatingTabBar(tab: $store.tab) }
            }
        }
        // The mock's Settings → Appearance Night/Day/Auto switch (`S.theme`) -
        // `.auto` passes `nil` through, which is exactly the old always-
        // follow-the-system behaviour this app had before Settings gained
        // an explicit override.
        .preferredColorScheme(store.settings.themePreference.colorScheme)
        .onChange(of: scenePhase) { _, phase in
            if phase == .background { BackgroundRender.scheduleIfNeeded(store: store) }
        }
    }
}

/// The mock's `.tabbar.g`/`.tb`/`.tb.on`: one floating glass pill instead
/// of the system tab bar, same five destinations in the same order. SF
/// Symbols stand in for the mock's own hand-drawn SVG glyphs - matching a
/// specific 22px web icon's literal path data has no real native
/// equivalent, and Apple's HIG steers away from custom glyphs where a
/// system symbol already reads the same way, so this matches the mock's
/// ICONOGRAPHY INTENT (grid / clapperboard / plus / people / gear) rather
/// than trying to trace its exact vectors.
private struct FloatingTabBar: View {
    @Binding var tab: AppTab
    private let items: [(AppTab, String, String)] = [
        (.library, "Library", "square.grid.2x2.fill"),
        (.studio, "Studio", "clapperboard.fill"),
        (.create, "Create", "plus"),
        (.channels, "Channels", "person.2.wave.2.fill"),
        (.settings, "Settings", "gearshape.fill"),
    ]

    var body: some View {
        HStack(spacing: 0) {
            ForEach(items, id: \.0) { destination, label, symbol in
                let isOn = tab == destination
                Button {
                    if !isOn { Haptics.light() }
                    tab = destination
                } label: {
                    VStack(spacing: 2) {
                        Image(systemName: symbol).font(.system(size: 19, weight: .semibold))
                        Text(label).font(.system(size: 10.5, weight: .semibold))
                    }
                    .foregroundStyle(isOn ? Theme.accent : Theme.ink2)
                    .frame(maxWidth: .infinity, minHeight: 52)
                    .background {
                        if isOn {
                            RoundedRectangle(cornerRadius: 26, style: .continuous)
                                .fill(Theme.fill)
                                .overlay(RoundedRectangle(cornerRadius: 26, style: .continuous).strokeBorder(Theme.glassEdge, lineWidth: 0.5))
                        }
                    }
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(isOn ? [.isSelected] : [])
            }
        }
        .padding(6)
        .frame(height: 64)
        .glassPill()
        .padding(.horizontal, 14)
        .padding(.bottom, 4)
    }
}

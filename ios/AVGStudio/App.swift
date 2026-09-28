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
                // The mock's own tab bar WAS the OS's real iOS 26+ Liquid
                // Glass floating tab bar - the mock only ever mimicked
                // that system chrome, it never meant a from-scratch
                // replacement. TabView already renders exactly that
                // floating glass pill natively once built with the modern
                // `Tab(value:)` syntax, so this just lets it - no hidden
                // toolbar, no hand-rolled bar underneath duplicating it.
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

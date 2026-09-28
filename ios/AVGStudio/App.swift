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
                TabView(selection: $store.tab) {
                    Tab("Library", systemImage: "square.grid.2x2.fill", value: AppTab.library) {
                        NavigationStack(path: $store.libraryPath) { HomeView() }
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
                .tabBarMinimizeBehavior(.onScrollDown)
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background { BackgroundRender.scheduleIfNeeded(store: store) }
        }
    }
}

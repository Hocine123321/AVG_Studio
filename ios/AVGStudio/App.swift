import SwiftUI

@main
struct AVGStudioApp: App {
    @State private var store = Store()
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
    var body: some View {
        @Bindable var store = store
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
}

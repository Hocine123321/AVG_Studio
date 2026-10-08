import SwiftUI

@main
struct AVGStudioApp: App {
    @State private var store: Store

    init() {
        // Built here (not in a property initializer) so the very same
        // instance can be handed to BackgroundRender.
        let store = Store()
        _store = State(initialValue: store)
        BackgroundRender.attach(store)
        // The renderer asks this before every frame to know whether the GPU
        // may be used (it may not, in the background).
        AppActivity.shared.start()
        // Must be registered before the app finishes launching; a struct
        // App's `init()` runs at that point, which is the documented place
        // to do this in a SwiftUI-lifecycle app.
        BackgroundRender.register()
        // Belt-and-suspenders alongside Info.plist's UIAppFonts entry - see
        // Theme.registerFonts's own doc comment.
        Theme.registerFonts()
        Connectivity.shared.start()
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
        // `Store.agent` is a `let`, so a binding cannot be formed through
        // `$store.agent`; bind the agent directly.
        @Bindable var agent = store.agent
        return Group {
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
                    Tab("Studio", systemImage: "film.stack.fill", value: AppTab.studio) {
                        NavigationStack { StudioView() }
                    }
                    Tab("Create", systemImage: "sparkles", value: AppTab.create) {
                        NavigationStack { CreateView() }
                    }
                    Tab("Channels", systemImage: "person.2.wave.2.fill", value: AppTab.channels) {
                        NavigationStack { ChannelsView() }
                    }
                    Tab("Settings", systemImage: "gearshape.fill", value: AppTab.settings) {
                        NavigationStack(path: $store.settingsPath) { SettingsView() }
                    }
                }
                // The Liquid Glass tab bar shrinks out of the way while a
                // list or grid scrolls down, and returns on scroll up.
                .tabBarMinimizeBehavior(.onScrollDown)
            }
        }
        // The mock's Settings → Appearance Night/Day/Auto switch (`S.theme`) -
        // `.auto` passes `nil` through, which is exactly the old always-
        // follow-the-system behaviour this app had before Settings gained
        // an explicit override.
        //
        // While the assistant's chat is open the same choices are shown INSIDE
        // the chat (one sheet at a time, and the chat is where the person is
        // already looking); closing the chat brings this sheet back. The
        // binding must never write `nil` back: a programmatic dismissal would
        // otherwise clear the plan and leave the paused render waiting forever.
        .sheet(item: Binding<RecoveryPlan?>(
            get: { store.agent.isOpen ? nil : store.recoveryPlan },
            set: { if let plan = $0 { store.recoveryPlan = plan } }
        )) { plan in
            RecoverySheet(plan: plan) { store.resolveRecovery($0) }
        }
        .sheet(isPresented: $agent.isOpen) { AgentView() }
        // "Ready" / "stopped" announcements: felt even with the chat closed.
        .sensoryFeedback(trigger: store.agent.lastAnnouncement) { _, new in
            guard let new else { return nil }
            return new.ok ? .success : .error
        }
        .environment(\.glassLevel, store.settings.glassOpacity)
        .environment(\.appFontChoice, store.settings.appFont)
        .font(store.settings.appFont == .caprasimo ? Font.custom("Caprasimo-Regular", size: 17) : nil)
        .preferredColorScheme(store.settings.themePreference.colorScheme)
        .onChange(of: scenePhase) { _, phase in
            // Write any debounced settings edit before we can be suspended.
            if phase != .active { store.flushMeta() }
            if phase == .background { BackgroundRender.scheduleIfNeeded(store: store) }
        }
    }
}

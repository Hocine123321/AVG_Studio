import SwiftUI
import AVFoundation

struct SettingsView: View {
    @Environment(Store.self) private var store
    @State private var keys: [APIKey: String] = [:]

    var body: some View {
        @Bindable var store = store
        Form {
            Section("AI services") {
                Picker("Writer", selection: $store.settings.text) {
                    ForEach(TextProviderKind.allCases) { Text($0.label).tag($0) }
                }
                Picker("Images", selection: $store.settings.image) {
                    ForEach(ImageProviderKind.allCases) { Text($0.label).tag($0) }
                }
                Picker("Voice", selection: $store.settings.voice) {
                    ForEach(VoiceProviderKind.allCases) { Text($0.label).tag($0) }
                }
                TextField("Image style", text: $store.settings.imageStyle, axis: .vertical)
            }

            if store.settings.voice == .apple {
                Section("Apple voice") {
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
            } else {
                Section("ElevenLabs") {
                    TextField("Voice ID", text: $store.settings.elevenVoiceID)
                    TextField("Model", text: $store.settings.elevenModel)
                }
            }

            Section {
                Toggle("Dynamic pauses", isOn: $store.settings.dynamicPausesEnabled)
                if store.settings.dynamicPausesEnabled {
                    Stepper("Pause length: \(Int(store.settings.pauseMs)) ms", value: $store.settings.pauseMs, in: 200...2000, step: 100)
                }
            } header: { Text("Narration") } footer: {
                Text("When the writer inserts [pause], on becomes a real held silence between two separately-recorded segments. Off just collapses it to a beat, spoken as part of one take.")
            }

            Section {
                ForEach(APIKey.allCases) { k in
                    SecureField(k.label, text: Binding(
                        get: { keys[k] ?? "" },
                        set: { keys[k] = $0; Keychain.set(k, $0) }
                    ))
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                }
            } header: { Text("API keys") } footer: { Text("Stored in your iPhone's Keychain. Free options need no keys. AssemblyAI is optional and only affects caption timing accuracy.") }

            Section("Video") {
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
            }

            Section {
                Picker("Style", selection: $store.settings.filterStyle) {
                    ForEach(FilterStyle.allCases) { Text($0.label).tag($0) }
                }
                if store.settings.filterStyle != .none {
                    Slider(value: $store.settings.filterIntensity, in: 0...100) { Text("Intensity") }
                }
            } header: { Text("Filters") } footer: {
                Text("An on-device stand-in for the PC's grain/light-leak look, generated procedurally rather than from the same source footage.")
            }

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

            Section {
                NavigationLink("App health", destination: HealthView())
                NavigationLink("Activity & errors", destination: ErrorLogView())
            }
        }
        .screenBackground()
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

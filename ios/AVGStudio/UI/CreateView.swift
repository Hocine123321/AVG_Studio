import SwiftUI

struct CreateView: View {
    @Environment(Store.self) private var store
    @State private var topic = ""
    @State private var title = ""
    @State private var channelID: UUID?
    @State private var aspect: AspectRatio = .portrait
    @FocusState private var focused: Bool
    @State private var toastMessage: String?

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("What's the video about?").font(.headline)
                    TextField("e.g. Why octopuses have three hearts", text: $topic, axis: .vertical)
                        .lineLimit(3...8)
                        .focused($focused)
                        .padding(12)
                        .glassEffect(.regular, in: .rect(cornerRadius: 16))
                    TextField("Title (optional)", text: $title)
                        .padding(12)
                        .glassEffect(.regular, in: .rect(cornerRadius: 16))
                }
                .glassCard()

                VStack(alignment: .leading, spacing: 12) {
                    Text("Channel").font(.headline)
                    Picker("Channel", selection: Binding(get: { channelID ?? store.activeChannel.id }, set: { channelID = $0 })) {
                        ForEach(store.channels) { Text($0.name).tag($0.id) }
                    }
                    .pickerStyle(.menu)
                    Text("Shape").font(.headline)
                    Picker("Shape", selection: $aspect) {
                        ForEach(AspectRatio.allCases) { Text($0.rawValue).tag($0) }
                    }
                    .pickerStyle(.segmented)
                }
                .glassCard()

                VStack(alignment: .leading, spacing: 6) {
                    Label("Writer: \(store.settings.text.label)", systemImage: "text.quote")
                    Label("Images: \(store.settings.image.label)", systemImage: "photo")
                    Label("Voice: \(store.settings.voice.label)", systemImage: "waveform")
                }
                .font(.footnote).foregroundStyle(.secondary)
                .glassCard()

                Button {
                    create()
                } label: {
                    Label("Create & render", systemImage: "wand.and.stars")
                        .font(.headline)
                        .frame(maxWidth: .infinity).padding(.vertical, 6)
                }
                .buttonStyle(.glassProminent)
                .disabled(topic.trimmed.isEmpty)
            }
            .padding(16)
        }
        .screenBackground()
        .navigationTitle("Create")
        .onAppear { aspect = store.activeChannel.aspectRatio }
        .scrollDismissesKeyboard(.interactively)
        .toast($toastMessage)
    }

    private func create() {
        Haptics.light()
        let ch = store.channels.first { $0.id == channelID } ?? store.activeChannel
        let p = store.createProject(topic: topic.trimmed, title: title, channel: ch, aspect: aspect)
        store.start(p.id)
        topic = ""; title = ""; focused = false
        // Matches the mock's own gen() action - back to Library (with the
        // new card visible there), NOT straight into Studio; still sets
        // currentProjectID so a direct tap on the Studio tab shows it.
        store.currentProjectID = p.id
        store.tab = .library
        toastMessage = "Started rendering “" + p.title + "”"
    }
}

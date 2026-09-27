import SwiftUI

struct ChannelsView: View {
    @Environment(Store.self) private var store
    @State private var editing: Channel?

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                ForEach(store.channels) { c in
                    HStack(spacing: 14) {
                        Image(systemName: c.id == store.activeChannelID ? "star.circle.fill" : "person.crop.circle")
                            .font(.title)
                            .foregroundStyle(c.id == store.activeChannelID ? (Theme.color(hex: c.accentColorHex) ?? Theme.accent) : .secondary)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(c.name).font(.headline)
                            Text("\(c.niche.isEmpty ? "General" : c.niche) · \(c.targetSeconds)s · \(c.aspectRatio.rawValue)")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if c.id != store.activeChannelID {
                            Button("Activate") { withAnimation(.spring) { store.activate(c) } }
                                .buttonStyle(.glass)
                        }
                    }
                    .glassCard()
                    .contentShape(.rect)
                    .onTapGesture { editing = c }
                    .contextMenu {
                        Button("Edit", systemImage: "pencil") { editing = c }
                        Button("Delete", systemImage: "trash", role: .destructive) { store.deleteChannel(c) }
                            .disabled(store.channels.count < 2)
                    }
                }
            }
            .padding(16)
        }
        .screenBackground()
        .navigationTitle("Channels")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Add", systemImage: "plus") { editing = Channel(name: "New Channel") }
            }
        }
        .sheet(item: $editing) { c in ChannelEditor(channel: c) }
    }
}

struct ChannelEditor: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State var channel: Channel

    private var accentBinding: Binding<Color> {
        Binding(
            get: { Theme.color(hex: channel.accentColorHex) ?? Theme.accent },
            set: { channel.accentColorHex = Theme.hex(from: $0) }
        )
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Identity") {
                    TextField("Name", text: $channel.name)
                    TextField("Niche (e.g. space facts)", text: $channel.niche)
                    TextField("Tone", text: $channel.tone)
                    TextField("Language", text: $channel.language)
                }
                Section("Videos") {
                    Stepper("Length: \(channel.targetSeconds)s", value: $channel.targetSeconds, in: 15...600, step: 15)
                    Picker("Shape", selection: $channel.aspectRatio) {
                        ForEach(AspectRatio.allCases) { Text($0.rawValue).tag($0) }
                    }
                }
                Section {
                    ColorPicker("Accent colour", selection: accentBinding, supportsOpacity: false)
                    if !channel.accentColorHex.isEmpty {
                        Button("Use app default") { channel.accentColorHex = "" }
                    }
                } header: { Text("Brand") } footer: {
                    Text("Tints this channel's highlight/typewriter caption fill - a small, iPhone-sized version of the PC's brand palette.")
                }
            }
            .navigationTitle(channel.name.isEmpty ? "Channel" : channel.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { store.upsert(channel); dismiss() }.disabled(channel.name.trimmed.isEmpty)
                }
            }
        }
        .presentationDetents([.medium, .large])
    }
}

import SwiftUI

/// The mock's `cr()` "New video" screen. Its Channel/Text model/Image
/// model/Voice rows and Scenes stepper assume a per-creation draft config
/// this app doesn't have: writer/image/voice provider are GLOBAL settings
/// here (`Store.settings`), not something you pick fresh per video, and
/// there's no fixed "N scenes" - the AI script decides scene count. So:
/// Channel stays a real, interactive picker (this app genuinely has many
/// channels to choose from); Writer/Images/Voice become real but
/// READ-ONLY rows that jump to Settings instead of a fake inline picker
/// that would silently do nothing; Scenes and the mock's static "Free"/
/// "about $0.12" per-phase cost table are both dropped rather than faked
/// (flagged in SKIN_V2_PLAN.md).
struct CreateView: View {
    @Environment(Store.self) private var store
    @State private var topic = ""
    @State private var title = ""
    @State private var channelID: UUID?
    @State private var aspect: AspectRatio = .portrait
    @FocusState private var focused: Bool
    @State private var toastMessage: String?
    @State private var showChannelPicker = false

    private var channel: Channel {
        store.channels.first { $0.id == channelID } ?? store.activeChannel
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                VStack(spacing: 10) {
                    TextField("What is the video about? For example: why octopuses have three hearts", text: $topic, axis: .vertical)
                        .lineLimit(3...8)
                        .focused($focused)
                        .padding(14)
                        .liquidGlass(RoundedRectangle(cornerRadius: 22, style: .continuous))
                    TextField("Title (optional)", text: $title)
                        .padding(14)
                        .liquidGlass(RoundedRectangle(cornerRadius: 22, style: .continuous))
                }

                SegmentedFilter(options: AspectRatio.allCases, label: { $0.rawValue }, selection: $aspect)

                VStack(spacing: 0) {
                    pickerRow("Channel", channel.name, icon: "chevron.right") { showChannelPicker = true }
                    hairline
                    navRow("Writer", store.settings.text.label)
                    hairline
                    navRow("Images", store.settings.image.label)
                    hairline
                    navRow("Voice", store.settings.voice.label)
                }
                .padding(.horizontal, 16)
                .glassCard(24, padding: 12)

                Text("Writer, image and voice choices are global — change them in Settings.")
                    .font(.system(size: 12.5)).foregroundStyle(Theme.ink2)
                    .padding(.horizontal, 4)
                    .frame(maxWidth: .infinity, alignment: .leading)

                estimateCard

                Button {
                    create()
                } label: {
                    Label("Generate video", systemImage: "wand.and.stars")
                        .font(.headline)
                        .frame(maxWidth: .infinity).padding(.vertical, 6)
                }
                .buttonStyle(.glassProminent)
                .disabled(topic.trimmed.isEmpty)
            }
            .padding(16)
        }
        .screenBackground()
        .navigationTitle("New video")
        .toolbar { ToolbarItem(placement: .topBarLeading) { AgentToolbarButton() } }
        .onAppear {
            aspect = store.activeChannel.aspectRatio
            // Coming from "Reword the topic and retry": pre-fill the topic
            // that was blocked so the user edits it instead of retyping.
            if let id = store.pendingReword, let p = store.project(id) {
                topic = p.topic; title = p.title
                store.pendingReword = nil
                toastMessage = "Edit the topic, then create again"
            }
        }
        .scrollDismissesKeyboard(.interactively)
        .toast($toastMessage)
        .sheet(isPresented: $showChannelPicker) { channelSheet }
    }

    private var hairline: some View {
        Rectangle().fill(Theme.glassEdge).frame(height: 0.5)
    }

    /// What this video is estimated to cost with the CURRENT settings,
    /// before anything is spent (the PC shows the same on its Create screen).
    /// An estimate from a maintained rate table, never metered spend - see
    /// CostTable. Recomputed on every redraw: it is a few multiplications.
    private var estimate: CostTable.Estimate {
        CostTable.estimate(targetSeconds: channel.targetSeconds, settings: store.settings, channel: channel,
                           hasTranscriptionKey: Keychain.has(.assemblyai), poolSize: store.reusableImages.count)
    }

    private var estimateCard: some View {
        let est = estimate
        return VStack(alignment: .leading, spacing: 8) {
            Text("Estimated cost").sectionHeader().padding(.leading, 6)
            VStack(spacing: 0) {
                ForEach(Array(est.lines.enumerated()), id: \.element.id) { i, line in
                    if i > 0 { hairline }
                    HStack {
                        Text(line.phase).foregroundStyle(Theme.ink)
                        Spacer()
                        Text(CostTable.Estimate.format(line.usd)).foregroundStyle(Theme.ink2).monospacedDigit()
                    }
                    .padding(.vertical, 11)
                }
                hairline
                HStack {
                    Text("Total").font(.body.weight(.semibold)).foregroundStyle(Theme.ink)
                    Spacer()
                    Text(est.label).font(.body.weight(.semibold)).foregroundStyle(Theme.ink).monospacedDigit()
                }
                .padding(.vertical, 11)
            }
            .padding(.horizontal, 16)
            .glassCard(24, padding: 4)
            Text("A rough guess from a rate table for a ~\(channel.targetSeconds)s video, not a bill. Free and on-device providers cost nothing.")
                .font(.footnote).foregroundStyle(Theme.ink2).padding(.horizontal, 6)
        }
        .accessibilityElement(children: .contain)
    }

    /// A row that picks something IN PLACE (opens a sheet, no navigation).
    @ViewBuilder private func pickerRow(_ label: String, _ value: String, icon: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack {
                Text(label).foregroundStyle(Theme.ink)
                Spacer()
                Text(value).foregroundStyle(Theme.ink2).lineLimit(1)
                Image(systemName: icon).font(.caption.weight(.semibold)).foregroundStyle(Theme.ink2)
            }
            .padding(.vertical, 12)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
    }

    /// A row that jumps elsewhere (Settings) rather than picking in place -
    /// a distinct trailing glyph from `pickerRow` so it doesn't look like
    /// it opens an inline chooser it doesn't have.
    @ViewBuilder private func navRow(_ label: String, _ value: String) -> some View {
        Button {
            Haptics.light()
            store.tab = .settings
        } label: {
            HStack {
                Text(label).foregroundStyle(Theme.ink)
                Spacer()
                Text(value).foregroundStyle(Theme.ink2).lineLimit(1)
                Image(systemName: "arrow.up.forward").font(.caption.weight(.semibold)).foregroundStyle(Theme.ink2)
            }
            .padding(.vertical, 12)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityHint("Opens Settings")
    }

    private var channelSheet: some View {
        GlassSheet(title: "Channel") {
            VStack(spacing: 0) {
                ForEach(Array(store.channels.enumerated()), id: \.element.id) { i, ch in
                    if i > 0 { hairline }
                    Button {
                        Haptics.light()
                        channelID = ch.id
                        aspect = ch.aspectRatio
                        showChannelPicker = false
                    } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(ch.name).foregroundStyle(Theme.ink)
                                Text(ch.aspectRatio.rawValue).font(.caption).foregroundStyle(Theme.ink2)
                            }
                            Spacer()
                            if ch.id == channel.id {
                                Image(systemName: "checkmark").font(.callout.weight(.semibold)).foregroundStyle(Theme.accent)
                            }
                        }
                        .padding(.vertical, 12)
                        .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
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

import SwiftUI

// The editor: the person's own turn at a video before (or after) the AI's.
//
// This first version works on what the renderer already understands - the
// music bed, sound effects, full-screen text cards and the caption text - and
// has no live preview yet: changes apply when "Render" is pressed, and the
// result arrives as a new take, so nothing the AI made is ever lost.

/// The 10-second card ("Edit it yourself?") shown over any screen.
struct EditGateCard: View {
    @Environment(Store.self) private var store
    let gate: EditGate

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "scissors").foregroundStyle(Theme.accent).padding(.top, 2)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Edit “\(gate.title)” yourself?").font(.subheadline.weight(.semibold)).lineLimit(1)
                    Text("If you do nothing, the AI finishes it.").font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
                Text(gate.deadline, style: .timer)
                    .font(.subheadline.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            ProgressView(timerInterval: gate.deadline.addingTimeInterval(-EditGate.seconds)...gate.deadline, countsDown: true) {
                EmptyView()
            } currentValueLabel: {
                EmptyView()
            }
            HStack(spacing: 10) {
                Button("Let AI finish") { store.resolveEditGate(.letAIEdit) }
                    .buttonStyle(.glass)
                Button("Edit it myself") { store.resolveEditGate(.editMyself) }
                    .buttonStyle(.glassProminent)
            }
        }
        .padding(14)
        .glassCard()
    }
}

struct EditorView: View {
    let projectID: String
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var addingCard = false

    private var project: Project? { store.project(projectID) }

    var body: some View {
        NavigationStack {
            Group {
                if let p = project {
                    form(p)
                } else {
                    ContentUnavailableView("Video not found", systemImage: "film")
                }
            }
            .navigationTitle("Edit")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } }
            }
            .tint(Theme.accent)
        }
    }

    private func form(_ p: Project) -> some View {
        Form {
            Section {
                Text("Changes here apply to this video only. Nothing is rendered until you press Render, and the result is a new take, so the earlier ones stay.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            musicSection(p)
            effectsSection(p)
            cardsSection(p)
            Section {
                NavigationLink("Captions") { CaptionsEditorView(projectID: projectID) }
            } footer: {
                Text("Fix a misheard word or retype a line.")
            }
            Section {
                Button {
                    store.renderEdited(p.id)
                    dismiss()
                } label: {
                    Text(p.takes.isEmpty ? "Render video" : "Render a new take").frame(maxWidth: .infinity)
                }
                .buttonStyle(.glassProminent)
            }
        }
        .glassSheetBackground()
        .sheet(isPresented: $addingCard) { AddTitleCardSheet(projectID: projectID) }
    }

    // MARK: Music

    private func musicSection(_ p: Project) -> some View {
        Section {
            Picker("Music", selection: musicBinding) {
                Text("Same as channel").tag(MusicOverride?.none)
                Text("No music").tag(MusicOverride?.some(.silent))
                Text("Choose for me").tag(MusicOverride?.some(.auto))
                ForEach(store.audioLibrary.music) { Text($0.name).tag(MusicOverride?.some(.track($0.id))) }
            }
        } header: {
            Text("Music")
        } footer: {
            Text(channelMusicNote(p))
        }
    }

    private var musicBinding: Binding<MusicOverride?> {
        Binding(
            get: { store.project(projectID)?.edits?.music },
            set: { value in
                store.update(projectID) { proj in
                    var edits = proj.edits ?? ProjectEdits()
                    edits.music = value
                    proj.edits = edits.isEmpty ? nil : edits
                }
            }
        )
    }

    private func channelMusicNote(_ p: Project) -> String {
        let ch = store.channel(for: p)
        if ch.musicAuto { return "This channel picks its music for each video." }
        if let id = ch.musicTrackID, let item = store.audioLibrary.item(id) { return "This channel uses “\(item.name)”." }
        return "This channel has no music. Upload tracks under Settings → Music."
    }

    // MARK: Sound effects

    private func effectsSection(_ p: Project) -> some View {
        Section {
            Picker("Sound effects", selection: effectsBinding) {
                Text("Same as channel").tag(Bool?.none)
                Text("On").tag(Bool?.some(true))
                Text("Off").tag(Bool?.some(false))
            }
        } header: {
            Text("Sound effects")
        } footer: {
            Text(store.channel(for: p).sfxEnabled
                 ? "This channel has effects on. Choose their sounds in the channel's Sound effects section."
                 : "This channel has effects off. Turning them on here still needs sounds chosen in the channel.")
        }
    }

    private var effectsBinding: Binding<Bool?> {
        Binding(
            get: { store.project(projectID)?.edits?.sfxEnabled },
            set: { value in
                store.update(projectID) { proj in
                    var edits = proj.edits ?? ProjectEdits()
                    edits.sfxEnabled = value
                    proj.edits = edits.isEmpty ? nil : edits
                }
            }
        )
    }

    // MARK: Title cards

    private func cardsSection(_ p: Project) -> some View {
        Section {
            ForEach(p.cards.sorted { $0.start < $1.start }) { card in
                VStack(alignment: .leading, spacing: 2) {
                    Text(card.text).lineLimit(2)
                    Text("\(timeLabel(card.start)) to \(timeLabel(card.end)) · \(cardKind(card))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .swipeActions {
                    Button("Delete", systemImage: "trash", role: .destructive) {
                        store.update(projectID) { proj in proj.cards.removeAll { $0.id == card.id } }
                    }
                }
            }
            Button { addingCard = true } label: { Label("Add a title card", systemImage: "plus") }
        } header: {
            Text("Title cards")
        } footer: {
            Text("Full-screen text cards. Swipe to remove one the AI added. Redoing the captions step replaces all cards.")
        }
    }

    private func cardKind(_ card: TakeoverCard) -> String {
        switch card.kind ?? "question" {
        case "chapter": return "chapter"
        case "explainer": return "text card"
        default: return "question"
        }
    }

    private func timeLabel(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}

/// A full-screen text card the person writes, placed anywhere in the video.
private struct AddTitleCardSheet: View {
    let projectID: String
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var start = 1.0
    @State private var length = 2.5

    private var total: Double { (store.project(projectID)?.sceneDurations ?? []).reduce(0, +) }

    var body: some View {
        NavigationStack {
            Form {
                Section("Text") {
                    TextField("What the card says", text: $text, axis: .vertical).lineLimit(1...4)
                }
                Section("Timing") {
                    Stepper(value: $start, in: 0...max(0.5, total - 0.5), step: 0.5) {
                        Text("Appears at \(String(format: "%.1f", start)) s")
                    }
                    Stepper(value: $length, in: 1...6, step: 0.5) {
                        Text("Stays for \(String(format: "%.1f", length)) s")
                    }
                }
            }
            .navigationTitle("Title card")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Add") { add() }.disabled(text.trimmed.isEmpty)
                }
            }
            .glassSheetBackground()
            .tint(Theme.accent)
        }
        .presentationDetents([.medium])
    }

    private func add() {
        let end = total > 0 ? min(total, start + length) : start + length
        store.update(projectID) { proj in
            // Cards are told apart by kind + scene number, so a hand-made one
            // takes a number above every scene and every existing card.
            let scene = max(1000, (proj.cards.map { $0.scene }.max() ?? 0) + 1)
            proj.cards.append(TakeoverCard(scene: scene, text: text.trimmed, start: start, end: end, kind: "explainer"))
            proj.cards.sort { $0.start < $1.start }
        }
        dismiss()
    }
}

/// Find and replace a word everywhere, or retype single caption lines.
struct CaptionsEditorView: View {
    let projectID: String
    @Environment(Store.self) private var store
    @State private var find = ""
    @State private var replacement = ""
    @State private var note: String?
    @State private var lines: [String] = []
    @State private var loaded = false

    private var cues: [CaptionCue] { store.project(projectID)?.captions ?? [] }

    var body: some View {
        Form {
            Section {
                TextField("Word to find", text: $find)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                TextField("Replace with", text: $replacement)
                    .autocorrectionDisabled()
                Button("Replace everywhere") { replaceAll() }
                    .disabled(find.trimmed.isEmpty || replacement.trimmed.isEmpty)
            } header: {
                Text("Find and replace")
            } footer: {
                Text(note ?? "Replaces whole words and keeps their punctuation. Good for a name that was misheard.")
            }
            Section("Lines") {
                ForEach(lines.indices, id: \.self) { i in
                    VStack(alignment: .leading, spacing: 2) {
                        if i < cues.count {
                            Text(timeLabel(cues[i].start)).font(.caption2).foregroundStyle(.secondary)
                        }
                        TextField("", text: $lines[i], axis: .vertical).font(.callout)
                    }
                }
            }
        }
        .glassSheetBackground()
        .navigationTitle("Captions")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear {
            if !loaded {
                lines = cues.map { $0.text }
                loaded = true
            }
        }
        .onDisappear { commitLines() }
    }

    private func replaceAll() {
        let result = CaptionEdit.replace(find, with: replacement, in: cues)
        if result.count > 0 {
            store.update(projectID) { $0.captions = result.cues }
            lines = result.cues.map { $0.text }
        }
        note = result.count == 0 ? "No whole word “\(find.trimmed)” found." : "Replaced \(result.count) time\(result.count == 1 ? "" : "s")."
    }

    /// Writes back only the lines that were actually retyped.
    private func commitLines() {
        let current = cues
        guard loaded, lines.count == current.count else { return }
        var changed = false
        var next = current
        for i in lines.indices where lines[i].trimmed != current[i].text && !lines[i].trimmed.isEmpty {
            next[i] = CaptionEdit.setText(current[i], to: lines[i])
            changed = true
        }
        if changed { store.update(projectID) { $0.captions = next } }
    }

    private func timeLabel(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}

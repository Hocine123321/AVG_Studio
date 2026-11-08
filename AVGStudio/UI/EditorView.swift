import SwiftUI

// The editor: the person's own turn at a video before (or after) the AI's.
//
// A silent, approximate live preview sits on top, with a timeline showing the
// title cards, cuts and punch-ins. Below it, three pages - Cuts, Sound, Text -
// change this one video beside its channel. Changes show in the preview where
// they can; "Render" makes a new take, so nothing the AI made is ever lost.

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
            ProgressView(timerInterval: gate.deadline.addingTimeInterval(-gate.seconds)...gate.deadline, countsDown: true) {
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

/// Draws frames for the editor's preview off the main thread and keeps only the
/// newest request: dragging the scrub bar asks for many frames, the person only
/// ever needs the last one.
@MainActor @Observable
final class EditorPreviewModel {
    private(set) var image: UIImage?
    private(set) var duration = 0.0
    private(set) var loading = true
    private(set) var failed = false
    private(set) var playing = false
    /// The scrub position, in seconds. Bound to the slider.
    var time = 0.0

    @ObservationIgnored private var preview: VideoRenderer.Preview?
    @ObservationIgnored private let queue = DispatchQueue(label: "com.avgstudio.editor.preview", qos: .userInitiated)
    @ObservationIgnored private var wanted: Double?
    @ObservationIgnored private var busy = false
    @ObservationIgnored private var playTask: Task<Void, Never>?

    /// Prepares the video (decoding every image once), then shows the first frame.
    func load(_ input: RenderInput) async {
        loading = true
        failed = false
        let made: VideoRenderer.Preview = await withCheckedContinuation { c in
            queue.async { c.resume(returning: VideoRenderer.Preview(input)) }
        }
        preview = made
        duration = made.total
        loading = false
        show(time)
    }

    /// The editor changed cards or captions; redraw the current frame.
    func update(cards: [TakeoverCard], cues: [CaptionCue]) {
        guard let preview else { return }
        queue.async { preview.update(cards: cards, cues: cues) }
        show(time)
    }

    func show(_ t: Double) {
        wanted = t
        if !busy { pump() }
    }

    private func pump() {
        guard let t = wanted, let preview else { return }
        wanted = nil
        busy = true
        queue.async { [weak self] in
            let frame = preview.frame(at: t)
            Task { @MainActor in self?.finished(frame) }
        }
    }

    private func finished(_ frame: CGImage?) {
        busy = false
        if let frame { image = UIImage(cgImage: frame) } else if image == nil { failed = true }
        if wanted != nil { pump() }
    }

    /// Steps through the frames at about 8 per second, silently.
    func togglePlay() {
        if playing { stop(); return }
        guard duration > 0 else { return }
        if time >= duration - 0.2 { time = 0 }
        playing = true
        playTask = Task { [weak self] in
            while let self, self.playing, !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(125))
                guard self.playing else { break }
                self.time += 0.125
                if self.time >= self.duration { self.time = self.duration; self.show(self.time); break }
                self.show(self.time)
            }
            self?.playing = false
        }
    }

    func stop() {
        playing = false
        playTask?.cancel()
        playTask = nil
    }
}

/// The Editor tab: the editor for whichever video is current (the same one
/// Studio shows), with a menu to switch videos.
struct EditorTab: View {
    @Environment(Store.self) private var store

    var body: some View {
        Group {
            if let p = store.currentProject {
                // `.id` gives each video its own preview state.
                EditorView(projectID: p.id).id(p.id)
            } else {
                ContentUnavailableView {
                    Label("Nothing to edit yet", systemImage: "scissors")
                } description: {
                    Text("Create a video first, or open one from the Library.")
                } actions: {
                    Button("Create a video") { store.tab = .create }.buttonStyle(.glassProminent)
                }
            }
        }
        .navigationTitle(store.currentProject?.title ?? "Editor")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if store.projects.count > 1 {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        ForEach(store.projects) { p in
                            Button(p.title) { store.currentProjectID = p.id }
                        }
                    } label: {
                        Image(systemName: "film.stack")
                    }
                    .accessibilityLabel("Choose a video")
                }
            }
        }
    }
}

/// The editor's three pages.
private enum EditorPage: String, CaseIterable, Identifiable {
    case cuts, sound, text
    var id: String { rawValue }
    var title: String {
        switch self {
        case .cuts: return "Cuts"
        case .sound: return "Sound"
        case .text: return "Text"
        }
    }
}

/// The editor itself - a page of the Editor tab, not a sheet.
struct EditorView: View {
    let projectID: String
    @Environment(Store.self) private var store
    @State private var addingCard = false
    @State private var showAssistant = false
    @State private var assistant = EditAssistant()
    @State private var model = EditorPreviewModel()
    @State private var mixPlayer = AudioPreview()
    @State private var mixing = false
    @State private var tab: EditorPage = .cuts
    @State private var confirmingReset = false

    private var project: Project? { store.project(projectID) }

    var body: some View {
        Group {
            if let p = project {
                form(p)
            } else {
                ContentUnavailableView("Video not found", systemImage: "film")
            }
        }
        // The cuts level is part of the id: changing it for this video re-plans the preview.
        .task(id: "\(projectID)|\(store.settings.previewSharp)|\(project?.edits?.editLevel?.rawValue ?? "-")") {
            assistant.attach(store, projectID: projectID)
            await loadPreview()
        }
        .onChange(of: project?.cards) { _, _ in refreshPreview() }
        .onChange(of: project?.captions) { _, _ in refreshPreview() }
        .onDisappear {
            model.stop()
            mixPlayer.stop()
        }
    }

    // MARK: Preview

    private func loadPreview() async {
        guard let p = project else { return }
        let pipeline = Pipeline(store: store, projectID: projectID, resumeExisting: false)
        let input = pipeline.renderInput(project: p, channel: store.channel(for: p),
                                         audio: store.file(p, p.audioFile) ?? URL(fileURLWithPath: "/dev/null"),
                                         output: FileManager.default.temporaryDirectory.appendingPathComponent("preview-unused.mp4"),
                                         shortEdge: store.settings.previewSharp ? 540 : 360)
        await model.load(input)
    }

    private func refreshPreview() {
        guard let p = project else { return }
        model.update(cards: p.cards, cues: p.captions)
    }

    /// Mixes narration + this video's music and effects (the same mix a render
    /// would use) and plays it, so the level can be judged by ear before rendering.
    private func hearMix(_ p: Project) {
        if mixPlayer.playingID == "mix" { mixPlayer.stop(); return }
        guard let narration = store.file(p, p.audioFile) else { return }
        mixing = true
        Task {
            defer { mixing = false }
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("mix-preview-\(UUID().uuidString).m4a")
            let pipeline = Pipeline(store: store, projectID: projectID, resumeExisting: false)
            let mixed = await pipeline.mixedAudio(project: p, channel: store.channel(for: p), narration: narration, into: url)
            mixPlayer.toggle("mix", url: mixed)
        }
    }

    private func previewCard(_ p: Project) -> some View {
        VStack(spacing: 8) {
            ZStack {
                RoundedRectangle(cornerRadius: 14).fill(.black)
                if let image = model.image {
                    Image(uiImage: image).resizable().scaledToFit().clipShape(RoundedRectangle(cornerRadius: 14))
                } else if model.failed {
                    Text("Preview unavailable").font(.footnote).foregroundStyle(.secondary)
                } else {
                    ProgressView()
                }
            }
            .frame(height: 240)
            HStack(spacing: 12) {
                Button { model.togglePlay() } label: {
                    Image(systemName: model.playing ? "pause.fill" : "play.fill").frame(width: 28)
                }
                .buttonStyle(.glass)
                .disabled(model.loading || model.failed)
                Slider(value: Binding(get: { model.time }, set: { model.time = $0; model.show($0) }),
                       in: 0...max(model.duration, 0.5))
                    .disabled(model.loading || model.failed)
                Text(String(format: "%d:%02d", Int(model.time) / 60, Int(model.time) % 60))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            timelineStrip(p)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    /// Title cards (bars), cuts (ticks) and punch-ins (dots) along the video;
    /// tap or drag to jump the preview.
    private func timelineStrip(_ p: Project) -> some View {
        let total = max(model.duration, 0.5)
        let plan = Pipeline(store: store, projectID: projectID, resumeExisting: false)
            .editPlan(for: p, channel: store.channel(for: p))
        return VStack(spacing: 4) {
            GeometryReader { geo in
                let w = max(geo.size.width, 1)
                ZStack(alignment: .leading) {
                    Capsule().fill(.quaternary).frame(height: 6)
                    ForEach(p.cards) { card in
                        Capsule()
                            .fill(Theme.accent.opacity(0.7))
                            .frame(width: max(4, w * (card.end - card.start) / total), height: 6)
                            .offset(x: w * card.start / total)
                    }
                    ForEach(Array((plan?.cuts ?? []).enumerated()), id: \.offset) { _, cut in
                        Rectangle().fill(.secondary).frame(width: 2, height: 12).offset(x: w * cut.at / total - 1)
                    }
                    ForEach(Array((plan?.punches ?? []).enumerated()), id: \.offset) { _, t in
                        Circle().fill(.secondary).frame(width: 5, height: 5).offset(x: w * t / total - 2.5)
                    }
                    Rectangle().fill(.primary).frame(width: 2, height: 16).offset(x: w * model.time / total - 1)
                }
                .frame(height: 16)
                .contentShape(Rectangle())
                .gesture(DragGesture(minimumDistance: 0).onChanged { g in
                    let t = min(max(g.location.x / w, 0), 1) * total
                    model.time = t
                    model.show(t)
                })
            }
            .frame(height: 16)
            .disabled(model.loading || model.failed)
            Text("Bars are title cards, ticks are cuts, dots are punch-ins.")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    private func form(_ p: Project) -> some View {
        VStack(spacing: 0) {
            previewCard(p)
            Picker("Page", selection: $tab) {
                ForEach(EditorPage.allCases) { Text($0.title).tag($0) }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 16)
            .padding(.bottom, 8)
            settingsForm(p)
        }
        .screenBackground()
        .safeAreaInset(edge: .bottom) { actionBar(p) }
    }

    /// Always in reach: ask the assistant, or render without scrolling to the end.
    private func actionBar(_ p: Project) -> some View {
        HStack(spacing: 10) {
            if store.settings.editAssistantEnabled {
                Button {
                    Haptics.light()
                    showAssistant = true
                } label: {
                    Label("Assistant", systemImage: "sparkles")
                }
                .buttonStyle(.glass)
            }
            Button {
                Haptics.success()
                store.renderEdited(p.id)
                store.selectForStudio(p.id)   // watch it render in Studio
            } label: {
                Text(p.takes.isEmpty ? "Render video" : "Render a new take").frame(maxWidth: .infinity)
            }
            .buttonStyle(.glassProminent)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.bar)
    }

    private func settingsForm(_ p: Project) -> some View {
        Form {
            differencesSection(p)
            switch tab {
            case .cuts:
                aiEditSection(p)
            case .sound:
                hearSection(p)
                musicSection(p)
                effectsSection(p)
            case .text:
                cardsSection(p)
                Section {
                    NavigationLink("Captions") { CaptionsEditorView(projectID: projectID) }
                } footer: {
                    Text("Fix a misheard word or retype a line.")
                }
            }
        }
        .scrollContentBackground(.hidden)
        .sheet(isPresented: $addingCard) { AddTitleCardSheet(projectID: projectID) }
        .sheet(isPresented: $showAssistant) { EditAssistantView(assistant: assistant) }
        .confirmationDialog("Put music, effects and cuts back to the channel’s?",
                            isPresented: $confirmingReset, titleVisibility: .visible) {
            Button("Reset", role: .destructive) {
                Haptics.warning()
                store.update(projectID) { $0.edits = nil }
            }
        } message: {
            Text("Title cards and captions are not changed.")
        }
    }

    // MARK: Where this video differs from its channel

    private func differences(_ p: Project) -> [String] {
        guard let e = p.edits else { return [] }
        var out: [String] = []
        if e.editLevel != nil { out.append("cuts") }
        if e.music != nil { out.append("music") }
        if e.musicGainDB != nil { out.append("music level") }
        if e.sfxEnabled != nil { out.append("effects") }
        if e.sfxGainDB != nil { out.append("effects level") }
        return out
    }

    private func differencesSection(_ p: Project) -> some View {
        let changed = differences(p)
        return Section {
            if changed.isEmpty {
                Text("This video follows its channel’s music, effects and cuts.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                Text("Different from the channel: \(changed.joined(separator: ", ")).").font(.callout)
                Button("Reset to the channel’s", role: .destructive) { confirmingReset = true }
            }
        } footer: {
            Text("Changes here apply to this video only. The preview is silent and approximate; Render makes a new take, so the earlier ones stay.")
        }
    }

    // MARK: Hearing the mix

    private func hearSection(_ p: Project) -> some View {
        Section {
            Button { hearMix(p) } label: {
                Label(mixPlayer.playingID == "mix" ? "Stop" : "Hear the sound mix",
                      systemImage: mixPlayer.playingID == "mix" ? "stop.circle" : "speaker.wave.2")
            }
            .disabled(mixing)
        } footer: {
            Text("Plays the narration with this video’s music and effects, the way a render would mix them.")
        }
    }

    // MARK: What the AI edit did

    private func aiEditSection(_ p: Project) -> some View {
        let ch = store.channel(for: p)
        let level = EditResolution(project: p, channel: ch).editLevel
        let plan = Pipeline(store: store, projectID: projectID, resumeExisting: false).editPlan(for: p, channel: ch)
        return Section {
            Picker("Cuts and punch-ins", selection: cutsBinding) {
                Text("Same as channel").tag(EditLevel?.none)
                Text("Off").tag(EditLevel?.some(.off))
                Text("Cuts").tag(EditLevel?.some(.fast))
                Text("Smart").tag(EditLevel?.some(.smart))
            }
            if let plan {
                ForEach(plan.notes, id: \.self) { Text($0).font(.callout) }
            } else {
                Text(level == .off ? (p.edits?.editLevel != nil ? "Cuts and punch-ins are off for this video."
                                                                : "Cuts and punch-ins are off for this channel.")
                                   : "No cuts or punch-ins: the scenes are short and no key words stood out.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
        } header: {
            Text("AI edit")
        } footer: {
            Text("The preview above includes these. “Same as channel” follows the channel’s Editing section."
                 + (level == .smart ? " Smart crops need a Google key and are found when the video renders." : ""))
        }
    }

    private var cutsBinding: Binding<EditLevel?> {
        Binding(
            get: { store.project(projectID)?.edits?.editLevel },
            set: { value in
                store.update(projectID) { proj in
                    var edits = proj.edits ?? ProjectEdits()
                    edits.editLevel = value
                    proj.edits = edits.isEmpty ? nil : edits
                }
            }
        )
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
            if musicIsOn(p) {
                let ch = store.channel(for: p)
                VStack(alignment: .leading) {
                    Text("Music level: \(Int(EditResolution(project: p, channel: ch).musicGainDB)) dB under the voice")
                    Slider(value: musicLevelBinding(ch), in: EditResolution.musicGainRange, step: 1)
                }
                if p.edits?.musicGainDB != nil {
                    Button("Use the channel’s level") { setOwnLevel(music: nil) }
                }
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

    /// Whether this video will have music: its own choice, else its channel's.
    private func musicIsOn(_ p: Project) -> Bool {
        switch p.edits?.music {
        case .some(.silent): return false
        case .some: return true
        case .none:
            let ch = store.channel(for: p)
            return ch.musicAuto || ch.musicTrackID != nil
        }
    }

    private func musicLevelBinding(_ channel: Channel) -> Binding<Double> {
        Binding(
            get: { store.project(projectID).map { EditResolution(project: $0, channel: channel).musicGainDB } ?? channel.musicGainDB },
            set: { setOwnLevel(music: $0) }
        )
    }

    private func effectsLevelBinding(_ channel: Channel) -> Binding<Double> {
        Binding(
            get: { store.project(projectID).map { EditResolution(project: $0, channel: channel).sfxGainDB } ?? channel.sfxGainDB },
            set: { setOwnLevel(effects: $0) }
        )
    }

    /// Sets or clears this video's own music level (`music:`) or effects level
    /// (`effects:`); passing nil goes back to the channel's.
    private func setOwnLevel(music: Double?) {
        store.update(projectID) { proj in
            var edits = proj.edits ?? ProjectEdits()
            edits.musicGainDB = music
            proj.edits = edits.isEmpty ? nil : edits
        }
    }

    private func setOwnLevel(effects: Double?) {
        store.update(projectID) { proj in
            var edits = proj.edits ?? ProjectEdits()
            edits.sfxGainDB = effects
            proj.edits = edits.isEmpty ? nil : edits
        }
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
            if EditResolution(project: p, channel: store.channel(for: p)).sfxEnabled {
                let ch = store.channel(for: p)
                VStack(alignment: .leading) {
                    Text("Effects level: \(Int(EditResolution(project: p, channel: ch).sfxGainDB)) dB")
                    Slider(value: effectsLevelBinding(ch), in: EditResolution.sfxGainRange, step: 1)
                }
                if p.edits?.sfxGainDB != nil {
                    Button("Use the channel’s level") { setOwnLevel(effects: nil) }
                }
            }
        } header: {
            Text("Sound effects")
        } footer: {
            Text(store.channel(for: p).sfxEnabled
                 ? "This channel has effects on. Choose their sounds in the channel’s Sound effects section."
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
            if p.cards.isEmpty {
                Text("No title cards in this video.").foregroundStyle(.secondary)
            }
            ForEach(p.cards.sorted { $0.start < $1.start }) { card in
                VStack(alignment: .leading, spacing: 2) {
                    Text(card.text).lineLimit(2)
                    Text("\(timeLabel(card.start)) to \(timeLabel(card.end)) · \(cardKind(card))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
                .onTapGesture {
                    model.time = card.start
                    model.show(card.start)
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
            Text("Full-screen text cards. Tap one to jump the preview to it; swipe to remove one. Redoing the captions step replaces all cards.")
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
        store.update(projectID) { proj in
            proj.cards.append(TitleCards.make(text: text, start: start, length: length, total: total, existing: proj.cards))
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
        .screenBackground()
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

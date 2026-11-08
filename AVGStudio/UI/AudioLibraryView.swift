import SwiftUI
import AVFoundation
import UniformTypeIdentifiers

/// Plays a track from the library so it can be auditioned before it goes under
/// a video. One track at a time; tapping the playing one stops it.
@MainActor @Observable
final class AudioPreview: NSObject, AVAudioPlayerDelegate {
    private(set) var playingID: String?
    @ObservationIgnored private var player: AVAudioPlayer?

    func toggle(_ id: String, url: URL) {
        if playingID == id { stop(); return }
        stop()
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback)
            try AVAudioSession.sharedInstance().setActive(true)
            let next = try AVAudioPlayer(contentsOf: url)
            next.delegate = self
            next.prepareToPlay()
            next.play()
            player = next
            playingID = id
        } catch {
            playingID = nil
        }
    }

    func stop() {
        player?.stop()
        player = nil
        playingID = nil
    }

    /// How far through the playing track we are, 0...1 (read on a timer: the
    /// player itself is not observable).
    var progress: Double {
        guard let player, player.duration > 0 else { return 0 }
        return min(max(player.currentTime / player.duration, 0), 1)
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in self.stop() }
    }
}

/// Settings → Music and Settings → Sound effects: the shelf a channel picks
/// from. Everything here was uploaded on purpose - there is no bundled or
/// scraped catalogue (see AudioLibrary.swift for why).
struct AudioLibraryView: View {
    let kind: AudioItem.Kind
    @Environment(Store.self) private var store
    @State private var preview = AudioPreview()
    @State private var importing = false
    @State private var busy = false
    @State private var errorMessage: String?
    @State private var editing: AudioItem?
    @State private var generating = false
    @State private var confirmingPack = false
    @State private var search = ""
    @State private var sort: Sort = .newest
    @State private var tagFilter: String?
    @State private var pendingDelete: AudioItem?
    @State private var confirmingRemoveUnused = false

    private enum Sort: String, CaseIterable, Identifiable {
        case newest = "Newest", name = "Name", length = "Length"
        var id: String { rawValue }
    }

    /// Everything of this kind on the shelf, before search and filter.
    private var allItems: [AudioItem] { store.audioLibrary.items.filter { $0.kind == kind } }

    private var tags: [String] { Array(Set(allItems.flatMap(\.tags))).sorted() }

    /// What the page shows: searched, filtered by tag, sorted.
    private var visibleItems: [AudioItem] {
        let query = search.trimmed.lowercased()
        let found = allItems.filter { item in
            if let tag = tagFilter, !item.tags.contains(tag) { return false }
            if query.isEmpty { return true }
            return item.name.lowercased().contains(query)
                || item.tags.contains { $0.contains(query) }
                || item.license.lowercased().contains(query)
        }
        switch sort {
        case .newest: return found.sorted { $0.added > $1.added }
        case .name: return found.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
        case .length: return found.sorted { $0.duration > $1.duration }
        }
    }

    private var errorShown: Binding<Bool> {
        Binding(get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } })
    }

    var body: some View {
        List {
            Section {
                Button { importing = true } label: {
                    Label(busy ? "Adding…" : (kind == .music ? "Upload music" : "Upload a sound"),
                          systemImage: "square.and.arrow.down")
                }
                .disabled(busy)
                if kind == .music {
                    Button { generating = true } label: {
                        Label("Generate music with AI", systemImage: "wand.and.stars")
                    }
                    .disabled(busy)
                } else {
                    Button { confirmingPack = true } label: {
                        Label("Add a starter pack (AI)", systemImage: "sparkles")
                    }
                    .disabled(busy)
                }
            } footer: {
                Text(kind == .music
                     ? "Use tracks you have the right to use. Even “royalty-free” music can be flagged by YouTube’s Content ID, so keep the licence in each track’s details."
                     : "Short sounds only (up to 30 seconds). Use sounds you have the right to use, and keep the licence in each sound’s details.")
            }

            let tracks = visibleItems
            if allItems.isEmpty {
                Section {
                    ContentUnavailableView(kind == .music ? "No music yet" : "No sounds yet",
                                           systemImage: kind == .music ? "music.note" : "waveform",
                                           description: Text(kind == .music
                                                             ? "Upload an mp3, m4a or wav, then pick it for a channel in the channel’s Music section."
                                                             : "Upload a short mp3, m4a or wav, then give it a moment in a channel’s Sound effects section."))
                }
            } else if tracks.isEmpty {
                Section {
                    ContentUnavailableView("Nothing matches", systemImage: "magnifyingglass",
                                           description: Text("Try another word, or clear the tag filter."))
                }
            } else {
                let total = tracks.reduce(0) { $0 + $1.duration }
                Section {
                    ForEach(tracks) { item in row(item) }
                } header: {
                    Text("\(kind == .music ? "Your music" : "Your sounds") (\(tracks.count) · \(clock(total)))")
                } footer: {
                    Text(storageLine)
                }
            }
        }
        .screenBackground()
        .navigationTitle(kind == .music ? "Music" : "Sound effects")
        .searchable(text: $search, prompt: kind == .music ? "Search music" : "Search sounds")
        .toolbar {
            if !allItems.isEmpty {
                ToolbarItem(placement: .topBarTrailing) { sortMenu }
            }
        }
        .confirmationDialog("Remove \(unusedItems.count) unused \(kind == .music ? "track" : "sound")\(unusedItems.count == 1 ? "" : "s")?",
                            isPresented: $confirmingRemoveUnused, titleVisibility: .visible) {
            Button("Remove", role: .destructive) {
                Haptics.warning()
                preview.stop()
                store.removeAudioItems(unusedItems.map(\.id))
            }
        } message: {
            Text(removeUnusedMessage)
        }
        .confirmationDialog("Delete “\(pendingDelete?.name ?? "")”?",
                            isPresented: Binding(get: { pendingDelete != nil }, set: { if !$0 { pendingDelete = nil } }),
                            titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                Haptics.warning()
                if let item = pendingDelete {
                    preview.stop()
                    store.removeAudioItem(item.id)
                }
                pendingDelete = nil
            }
        } message: {
            Text(deleteMessage)
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.audio], allowsMultipleSelection: true) { handle($0) }
        .sheet(isPresented: $generating) { GenerateMusicSheet() }
        .confirmationDialog("Add a starter pack?", isPresented: $confirmingPack, titleVisibility: .visible) {
            Button("Generate 5 sounds") { startPack() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Makes five short sounds (impact, riser, whoosh, pop, soft hit) with ElevenLabs. It needs an ElevenLabs key and costs a few cents. You pay once; they stay on this shelf.")
        }
        .alert("That didn’t work", isPresented: errorShown) {
            Button("OK") {}
        } message: {
            Text(errorMessage ?? "")
        }
        .sheet(item: $editing) { AudioItemEditor(item: $0) }
        .onDisappear { preview.stop() }
    }

    private var sortMenu: some View {
        Menu {
            Picker("Sort by", selection: $sort) {
                ForEach(Sort.allCases) { Text($0.rawValue).tag($0) }
            }
            if !tags.isEmpty {
                Picker("Tag", selection: $tagFilter) {
                    Text("All tags").tag(String?.none)
                    ForEach(tags, id: \.self) { Text($0).tag(String?.some($0)) }
                }
            }
            Divider()
            Button("Remove unused…", systemImage: "trash") { confirmingRemoveUnused = true }
                .disabled(unusedItems.isEmpty)
        } label: {
            Image(systemName: tagFilter == nil ? "line.3.horizontal.decrease.circle" : "line.3.horizontal.decrease.circle.fill")
        }
    }

    private func row(_ item: AudioItem) -> some View {
        HStack(spacing: 12) {
            Button {
                Haptics.light()
                if let url = store.audioLibrary.fileURL(for: item.id) { preview.toggle(item.id, url: url) }
            } label: {
                Image(systemName: preview.playingID == item.id ? "stop.circle.fill" : "play.circle.fill")
                    .font(.title2)
                    .foregroundStyle(Theme.accent)
            }
            .buttonStyle(.borderless)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.name).font(.body.weight(.semibold)).lineLimit(1)
                Text(detail(item)).font(.footnote).foregroundStyle(.secondary).lineLimit(1)
                if preview.playingID == item.id {
                    TimelineView(.periodic(from: .now, by: 0.2)) { _ in
                        ProgressView(value: preview.progress).tint(Theme.accent)
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .contentShape(.rect)
        .onTapGesture { editing = item }
        .swipeActions {
            Button("Delete", systemImage: "trash", role: .destructive) { requestDelete(item) }
        }
        .contextMenu {
            Button("Details", systemImage: "pencil") { editing = item }
            Button("Delete", systemImage: "trash", role: .destructive) { requestDelete(item) }
        }
    }

    /// Deleting something a channel uses asks first; an unused one just goes.
    private func requestDelete(_ item: AudioItem) {
        if store.channelsUsing(audioItem: item.id).isEmpty {
            Haptics.warning()
            preview.stop()
            store.removeAudioItem(item.id)
        } else {
            pendingDelete = item
        }
    }

    /// Things on this shelf that no channel or video uses.
    private var unusedItems: [AudioItem] { store.unusedAudioItems(kind: kind) }

    /// "12 tracks use 8.4 MB on this iPhone. 3 are unused."
    private var storageLine: String {
        let bytes = ByteCountFormatter.string(fromByteCount: Int64(store.audioLibrary.totalBytes(of: allItems)), countStyle: .file)
        let noun = kind == .music ? "track" : "sound"
        var text = "\(allItems.count) \(noun)\(allItems.count == 1 ? "" : "s") use\(allItems.count == 1 ? "s" : "") \(bytes) on this iPhone."
        if kind == .music, store.musicIsPickedAutomatically {
            text += " A channel picks music automatically, so any track may be used."
        } else if !unusedItems.isEmpty {
            text += " \(unusedItems.count) not used by any channel or video."
        }
        return text
    }

    private var removeUnusedMessage: String {
        let names = unusedItems.prefix(5).map(\.name).joined(separator: ", ")
        let more = unusedItems.count > 5 ? " and \(unusedItems.count - 5) more" : ""
        let bytes = ByteCountFormatter.string(fromByteCount: Int64(store.audioLibrary.totalBytes(of: unusedItems)), countStyle: .file)
        return "\(names)\(more). Frees \(bytes). Nothing uses them now."
    }

    private var deleteMessage: String {
        guard let item = pendingDelete else { return "" }
        return "\(usedBy(item)) It will stop being used there."
    }

    private func usedBy(_ item: AudioItem) -> String {
        let names = store.channelsUsing(audioItem: item.id).map(\.name)
        return names.isEmpty ? "" : "Used by \(names.joined(separator: ", "))."
    }

    private func clock(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        return String(format: "%d:%02d", s / 60, s % 60)
    }

    private func detail(_ item: AudioItem) -> String {
        var parts = [clock(item.duration)]
        if !item.tags.isEmpty { parts.append(item.tags.joined(separator: ", ")) }
        parts.append(item.license)
        let uses = store.channelsUsing(audioItem: item.id).count
        if uses > 0 { parts.append("in use (\(uses))") }
        return parts.joined(separator: " · ")
    }

    private func startPack() {
        busy = true
        Task {
            defer { busy = false }
            do {
                _ = try await AudioGeneration.generateStarterSounds(store: store)
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    private func handle(_ result: Result<[URL], Error>) {
        switch result {
        case .failure(let error):
            errorMessage = error.localizedDescription
        case .success(let urls):
            busy = true
            Task {
                defer { busy = false }
                var failures: [String] = []
                for url in urls {
                    do {
                        try await store.audioLibrary.importFile(url, kind: kind)
                    } catch {
                        failures.append("\(url.lastPathComponent): \(error.localizedDescription)")
                    }
                }
                if !failures.isEmpty { errorMessage = failures.joined(separator: "\n") }
            }
        }
    }
}

/// Name, tags and rights for one track or sound.
private struct AudioItemEditor: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    let item: AudioItem
    @State private var name: String
    @State private var tags: String
    @State private var license: String
    @State private var attribution: String

    init(item: AudioItem) {
        self.item = item
        _name = State(initialValue: item.name)
        _tags = State(initialValue: item.tags.joined(separator: ", "))
        _license = State(initialValue: item.license)
        _attribution = State(initialValue: item.attribution)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Name") {
                    TextField(item.kind == .music ? "Track name" : "Sound name", text: $name)
                }
                Section {
                    TextField("calm, upbeat, cinematic", text: $tags)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                } header: {
                    Text("Tags")
                } footer: {
                    Text("Words that describe it (mood, genre, what it sounds like), separated by commas.")
                }
                Section {
                    TextField("Licence", text: $license)
                    TextField("Credit (optional)", text: $attribution)
                } header: {
                    Text("Rights")
                } footer: {
                    Text("“Mine” means you made it or own it. For anything else, note the licence so you can show it if a video is ever claimed.")
                }
            }
            .navigationTitle(item.kind == .music ? "Track details" : "Sound details")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        let list = tags.split(separator: ",").map { String($0).trimmed.lowercased() }.filter { !$0.isEmpty }
                        store.audioLibrary.update(item.id, name: name, tags: list, license: license, attribution: attribution)
                        dismiss()
                    }
                    .disabled(name.trimmed.isEmpty)
                }
            }
            .glassSheetBackground()
            .tint(Theme.accent)
        }
        .presentationDetents([.medium, .large])
    }
}

/// Asks Google Lyria for one background track and saves it to the shelf.
private struct GenerateMusicSheet: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var mood = MusicPrompt.moods[0]
    @State private var detail = ""
    @State private var length = AudioGeneration.Length.clip
    @State private var working = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("Mood") {
                    Picker("Mood", selection: $mood) {
                        ForEach(MusicPrompt.moods, id: \.self) { Text($0.capitalized).tag($0) }
                    }
                    TextField("Style notes (optional), e.g. lo-fi piano, slow build", text: $detail, axis: .vertical)
                }
                Section {
                    Picker("Length", selection: $length) {
                        ForEach(AudioGeneration.Length.allCases) { Text("\($0.title) · \($0.price)").tag($0) }
                    }
                    .pickerStyle(.inline)
                    .labelsHidden()
                } header: {
                    Text("Length and price")
                } footer: {
                    Text("Uses your Google key and needs a paid Google plan: Lyria has no free tier. The track is saved to your music, so you pay once and can reuse it in every video.")
                }
                if let errorMessage {
                    Section { Text(errorMessage).foregroundStyle(Theme.bad) }
                }
            }
            .navigationTitle("Generate music")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(working) }
                ToolbarItem(placement: .confirmationAction) {
                    if working { ProgressView() } else { Button("Generate") { generate() } }
                }
            }
            .interactiveDismissDisabled(working)
            .glassSheetBackground()
            .tint(Theme.accent)
        }
        .presentationDetents([.large])
    }

    private func generate() {
        working = true
        errorMessage = nil
        Task {
            defer { working = false }
            do {
                _ = try await AudioGeneration.generateMusic(store: store, mood: mood, detail: detail, length: length)
                dismiss()
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}

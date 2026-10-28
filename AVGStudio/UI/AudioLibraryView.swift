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
            } footer: {
                Text(kind == .music
                     ? "Use tracks you have the right to use. Even “royalty-free” music can be flagged by YouTube’s Content ID, so keep the licence in each track’s details."
                     : "Short sounds only (up to 30 seconds). Use sounds you have the right to use, and keep the licence in each sound’s details.")
            }

            let tracks = store.audioLibrary.items.filter { $0.kind == kind }
            if tracks.isEmpty {
                Section {
                    Text(kind == .music
                         ? "No music yet. Upload an mp3, m4a or wav, then pick it for a channel in the channel’s Music section."
                         : "No sounds yet. Upload a short mp3, m4a or wav, then give it a moment in a channel’s Sound effects section.")
                        .foregroundStyle(.secondary)
                }
            } else {
                Section("\(kind == .music ? "Your music" : "Your sounds") (\(tracks.count))") {
                    ForEach(tracks) { item in row(item) }
                }
            }
        }
        .screenBackground()
        .navigationTitle(kind == .music ? "Music" : "Sound effects")
        .fileImporter(isPresented: $importing, allowedContentTypes: [.audio], allowsMultipleSelection: true) { handle($0) }
        .alert("Couldn’t add that file", isPresented: errorShown) {
            Button("OK") {}
        } message: {
            Text(errorMessage ?? "")
        }
        .sheet(item: $editing) { AudioItemEditor(item: $0) }
        .onDisappear { preview.stop() }
    }

    private func row(_ item: AudioItem) -> some View {
        HStack(spacing: 12) {
            Button {
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
            }
            Spacer(minLength: 0)
        }
        .contentShape(.rect)
        .onTapGesture { editing = item }
        .swipeActions {
            Button("Delete", systemImage: "trash", role: .destructive) {
                preview.stop()
                store.removeAudioItem(item.id)
            }
        }
    }

    private func detail(_ item: AudioItem) -> String {
        let seconds = Int(item.duration.rounded())
        var parts = [String(format: "%d:%02d", seconds / 60, seconds % 60)]
        if !item.tags.isEmpty { parts.append(item.tags.joined(separator: ", ")) }
        parts.append(item.license)
        return parts.joined(separator: " · ")
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
                    TextField("Track name", text: $name)
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
            .navigationTitle("Track details")
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

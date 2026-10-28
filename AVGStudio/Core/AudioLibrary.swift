import AVFoundation
import Foundation
import Observation

// The app's own shelf of music (and, later, sound effects).
//
// Everything on it was put there deliberately: uploaded from Files, generated,
// or saved from a link. There is no bundled or scraped catalogue - licences
// forbid redistributing most "free" music inside an app, and a track the
// person chose and owns is the safest thing to put under their videos. Each
// item records where it came from and under what licence, so that is never a
// guess later.
//
// Files are COPIED in (the Files picker hands over a temporary, security-
// scoped URL), validated as real audio, and measured once, so a bad file fails
// at upload instead of in the middle of a render.

struct AudioItem: Codable, Identifiable, Hashable {
    enum Kind: String, Codable { case music, sfx }
    enum Origin: String, Codable { case uploaded, generated, linked }

    var id: String = UUID().uuidString
    var kind: Kind
    var name: String
    /// File name inside the library folder for its kind (`Music/` or `SFX/`).
    var fileName: String
    var duration: Double = 0
    /// Mood / genre chips, set by hand (or by the generator's prompt).
    var tags: [String] = []
    var origin: Origin = .uploaded
    /// "Mine" for an upload; the licence name for anything taken from elsewhere.
    var license: String = "Mine"
    var attribution: String = ""
    var added: Date = Date()
}

// Tolerant decoding, same pattern as Channel: a saved item missing a newer
// field keeps its default instead of the whole library failing to load.
extension AudioItem {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: AnyKey.self)
        var d = AudioItem(kind: c.value("kind", Kind.music), name: c.value("name", "Untitled"),
                          fileName: c.value("fileName", ""))
        d.id = c.value("id", d.id)
        d.duration = c.value("duration", d.duration)
        d.tags = c.value("tags", d.tags)
        d.origin = c.value("origin", d.origin)
        d.license = c.value("license", d.license)
        d.attribution = c.value("attribution", d.attribution)
        d.added = c.value("added", d.added)
        self = d
    }
}

/// What a file must satisfy to go on the shelf. Not on `AudioLibrary` itself so
/// the (nonisolated) error text below can read them without a main-actor hop.
enum AudioLimits {
    static let allowedExtensions: Set<String> = ["mp3", "m4a", "aac", "wav", "aif", "aiff", "caf", "flac"]
    static let maxBytes = 80_000_000
    static let maxMusicMinutes = 30
    static let maxEffectSeconds = 30.0
}

enum AudioLibraryError: LocalizedError {
    case unsupportedType(String)
    case tooLarge
    case notAudio
    case tooShort
    case tooLong(Int)

    var errorDescription: String? {
        switch self {
        case .unsupportedType(let ext): return "“.\(ext)” isn't a supported audio type. Use mp3, m4a, wav, aiff or caf."
        case .tooLarge: return "That file is over \(AudioLimits.maxBytes / 1_000_000) MB."
        case .notAudio: return "That file doesn't contain playable audio."
        case .tooShort: return "That clip is too short to use."
        case .tooLong(let minutes): return "That track is over \(minutes) minutes. Trim it first."
        }
    }
}

@MainActor @Observable
final class AudioLibrary {
    private(set) var items: [AudioItem] = []
    @ObservationIgnored private let root: URL
    @ObservationIgnored private let indexURL: URL

    /// `directory` is for tests; the app uses its Application Support folder.
    init(directory: URL? = nil) {
        let base = directory ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("AudioLibrary", isDirectory: true)
        root = base
        indexURL = base.appendingPathComponent("library.json")
        if let data = try? Data(contentsOf: indexURL),
           let saved = try? JSONDecoder().decode([AudioItem].self, from: data) {
            items = saved
        }
    }

    // MARK: Reading

    var music: [AudioItem] { items.filter { $0.kind == .music } }
    var effects: [AudioItem] { items.filter { $0.kind == .sfx } }

    func item(_ id: String) -> AudioItem? { items.first { $0.id == id } }

    /// Where the file for `id` lives, or nil when the item or its file is gone.
    func fileURL(for id: String) -> URL? {
        guard let item = item(id) else { return nil }
        let url = folder(for: item.kind).appendingPathComponent(item.fileName)
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }

    // MARK: Adding

    /// Copies `source` into the library after checking it is real, reasonably
    /// sized audio, and returns the new item. `source` may be a security-scoped
    /// URL from the Files picker.
    @discardableResult
    func importFile(_ source: URL, kind: AudioItem.Kind, name: String? = nil, origin: AudioItem.Origin = .uploaded,
                    license: String = "Mine", attribution: String = "", tags: [String] = []) async throws -> AudioItem {
        let scoped = source.startAccessingSecurityScopedResource()
        defer { if scoped { source.stopAccessingSecurityScopedResource() } }

        let ext = source.pathExtension.lowercased()
        guard AudioLimits.allowedExtensions.contains(ext) else { throw AudioLibraryError.unsupportedType(ext) }
        let size = (try? FileManager.default.attributesOfItem(atPath: source.path)[.size] as? Int) ?? 0
        guard size <= AudioLimits.maxBytes else { throw AudioLibraryError.tooLarge }

        var item = AudioItem(kind: kind, name: name ?? source.deletingPathExtension().lastPathComponent, fileName: "")
        item.fileName = "\(item.id).\(ext)"
        item.origin = origin
        item.license = license
        item.attribution = attribution
        item.tags = tags

        let folder = folder(for: kind)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let destination = folder.appendingPathComponent(item.fileName)
        try FileManager.default.copyItem(at: source, to: destination)

        do {
            let asset = AVURLAsset(url: destination)
            guard try await asset.loadTracks(withMediaType: .audio).first != nil else { throw AudioLibraryError.notAudio }
            let seconds = try await asset.load(.duration).seconds
            guard seconds.isFinite, seconds > 0.2 else { throw AudioLibraryError.tooShort }
            if kind == .music, seconds > Double(AudioLimits.maxMusicMinutes * 60) { throw AudioLibraryError.tooLong(AudioLimits.maxMusicMinutes) }
            if kind == .sfx, seconds > AudioLimits.maxEffectSeconds { throw AudioLibraryError.tooLong(Int(AudioLimits.maxEffectSeconds)) }
            item.duration = seconds
        } catch {
            try? FileManager.default.removeItem(at: destination)
            if error is AudioLibraryError { throw error }
            throw AudioLibraryError.notAudio
        }

        items.append(item)
        save()
        return item
    }

    /// Saves audio a provider returned (a generated track or effect) through the
    /// same checks as an upload, so a bad response fails here, not mid-render.
    @discardableResult
    func importData(_ data: Data, fileExtension: String, kind: AudioItem.Kind, name: String, origin: AudioItem.Origin,
                    license: String, attribution: String = "", tags: [String] = []) async throws -> AudioItem {
        let temp = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).\(fileExtension)")
        try data.write(to: temp)
        defer { try? FileManager.default.removeItem(at: temp) }
        return try await importFile(temp, kind: kind, name: name, origin: origin, license: license,
                                    attribution: attribution, tags: tags)
    }

    // MARK: Editing

    func rename(_ id: String, to name: String) {
        guard let i = items.firstIndex(where: { $0.id == id }), !name.trimmed.isEmpty else { return }
        items[i].name = name.trimmed
        save()
    }

    /// Everything the details sheet edits, saved in one write.
    func update(_ id: String, name: String, tags: [String], license: String, attribution: String) {
        guard let i = items.firstIndex(where: { $0.id == id }), !name.trimmed.isEmpty else { return }
        items[i].name = name.trimmed
        items[i].tags = tags
        items[i].license = license.trimmed.isEmpty ? "Mine" : license.trimmed
        items[i].attribution = attribution.trimmed
        save()
    }

    func setTags(_ id: String, _ tags: [String]) {
        guard let i = items.firstIndex(where: { $0.id == id }) else { return }
        items[i].tags = tags
        save()
    }

    func remove(_ id: String) {
        guard let item = item(id) else { return }
        try? FileManager.default.removeItem(at: folder(for: item.kind).appendingPathComponent(item.fileName))
        items.removeAll { $0.id == id }
        save()
    }

    // MARK: Storage

    private func folder(for kind: AudioItem.Kind) -> URL {
        root.appendingPathComponent(kind == .music ? "Music" : "SFX", isDirectory: true)
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(items) else { return }
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try? data.write(to: indexURL, options: .atomic)
    }
}

import Foundation

/// One selectable model/voice, as a provider's own catalogue endpoint
/// describes it - never something this app invents. `id` is exactly the
/// value a setting stores (e.g. `AppSettings.geminiModel`); `label`/
/// `sublabel` are for display only.
struct CatalogEntry: Identifiable, Codable, Equatable, Hashable {
    var id: String
    var label: String
    var sublabel: String?
}

/// A live-fetched, disk-cached list for ONE provider endpoint - the same
/// "fetch once, cache to disk, let the user pick from what's really there
/// instead of typing an id blind" shape as Palabra's own `ModelCatalogue`
/// (`Palabra/Services/Catalogue/ModelCatalogue.swift`: load the cache at
/// launch, `refresh` replaces it only on a non-empty success and otherwise
/// keeps the last good list), generalized so this app's five independent
/// catalogues (Gemini text models, Groq text models, ElevenLabs voices,
/// ElevenLabs TTS models, Stable Horde image models) share one class and
/// on-disk cache mechanism instead of five near-identical copies - only
/// the `fetch` closure differs per catalogue (see `ModelCatalogSources.swift`).
///
/// Deliberately smaller than the PC's own Live List
/// (`avg/core/model_prefs.py` + `web/src/ModelPicker.tsx`): no per-entry
/// ticking, favouriting, free-text note, or AI-generated shortlist
/// suggestion sitting on top of the fetched list - this is the PC
/// catalogue's REFRESH half only, not its curation half, which is its own,
/// much larger feature (a second document, a lock keyed per file, an
/// AI-suggestion worker) this port is not taking on right now. What IS
/// ported from the PC's `ModelPicker.tsx` is its one load-bearing UX rule:
/// when nothing comes back, fall back to a plain text field rather than
/// leaving the setting impossible to change (see `ModelPickerRow.swift`).
@MainActor
@Observable
final class ModelCatalog {
    enum Status: Equatable {
        case idle
        case loading
        case loaded(Date)
        case failed(String, hasCache: Bool)
    }

    private(set) var entries: [CatalogEntry] = []
    private(set) var status: Status = .idle

    private let cacheKey: String
    private let fetch: () async throws -> [CatalogEntry]

    init(cacheKey: String, fetch: @escaping () async throws -> [CatalogEntry]) {
        self.cacheKey = cacheKey
        self.fetch = fetch
    }

    private struct Snapshot: Codable { var entries: [CatalogEntry]; var fetchedAt: Date }

    private var cacheURL: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("ModelCatalogs", isDirectory: true)
        return dir.appendingPathComponent("\(cacheKey).json")
    }

    /// Call once, from the picker row's `.task`/`.onAppear` - shows last
    /// session's list immediately instead of an empty state while the
    /// first live fetch of THIS session is still in flight.
    func loadCacheIfPresent() {
        guard status == .idle else { return }
        guard let data = try? Data(contentsOf: cacheURL),
              let snapshot = try? JSONDecoder().decode(Snapshot.self, from: data) else { return }
        entries = snapshot.entries
        status = .loaded(snapshot.fetchedAt)
    }

    func refresh() async {
        status = .loading
        do {
            let fetched = try await fetch()
            guard !fetched.isEmpty else {
                status = .failed("The list came back empty.", hasCache: !entries.isEmpty)
                return
            }
            entries = fetched
            let now = Date()
            status = .loaded(now)
            write(Snapshot(entries: fetched, fetchedAt: now))
        } catch {
            // Keeps whatever list is already showing (this session's or a
            // cached one) rather than blanking the picker over one failed
            // refresh - matches ModelCatalogue.refresh's own "only replace
            // on a non-empty success" rule.
            status = .failed(error.localizedDescription, hasCache: !entries.isEmpty)
        }
    }

    private func write(_ snapshot: Snapshot) {
        guard let data = try? JSONEncoder().encode(snapshot) else { return }
        try? FileManager.default.createDirectory(at: cacheURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? data.write(to: cacheURL, options: .atomic)
    }
}

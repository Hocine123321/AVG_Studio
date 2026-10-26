import Foundation

// Model favourites and proven-working badges - the curation half of the PC's
// Live List (avg/core/model_prefs.py + avg/core/model_cache.py + the probe in
// avg/providers/probe.py).
//
// The problem it solves: a provider lists far more models than your account
// can actually use. The PC's own account notes record Groq listing 10 text
// models with 5 working, and Gemini listing 32 with ONE working - so a picker
// that just shows the list is mostly a menu of things that will fail.
//
//  * Favourites are YOUR choices (stars), kept apart from...
//  * Probe results, which are the APP's findings: each text model asked one
//    tiny question, recorded as works / failed. Replaced, never hand-edited.
//
// Only TEXT models are probed. Listing costs nothing everywhere, but a probe
// makes a real call, which on image and voice providers costs real money -
// exactly why the PC lists those instead of testing them.

struct ProbeResult: Codable, Hashable {
    var works: Bool
    var checked: Date
    /// Why it failed ("Over quota"); nil when it works.
    var note: String?
}

/// What YOU chose: favourites per model list (keyed by `ModelCatalog.cacheKey`).
struct ModelFavourites: Codable, Equatable {
    var byCatalog: [String: [String]] = [:]

    func contains(_ model: String, in catalog: String) -> Bool {
        byCatalog[catalog]?.contains(model) ?? false
    }

    /// Returns the new state.
    @discardableResult
    mutating func toggle(_ model: String, in catalog: String) -> Bool {
        var list = byCatalog[catalog] ?? []
        if let i = list.firstIndex(of: model) {
            list.remove(at: i)
            byCatalog[catalog] = list.isEmpty ? nil : list
            return false
        }
        list.append(model)
        byCatalog[catalog] = list
        return true
    }
}

/// What the APP found out: model id -> probe result, per model list.
struct ProbeResults: Codable, Equatable {
    var byCatalog: [String: [String: ProbeResult]] = [:]
}

/// Asks one text model one tiny question and says whether it answered.
enum ModelProbe {
    static let prompt = "Reply with the single word OK."

    enum Outcome: Equatable {
        case works
        /// The model definitively can't be used with this account.
        case failed(String)
        /// Nothing learned (rate limit, no connection): never recorded as a failure.
        case inconclusive(String)
        /// The key itself was refused, so every further probe would fail the
        /// same way: the sweep stops instead of marking the whole list bad.
        case authFailed(String)
    }

    /// A failure is only "the model doesn't work" when the provider said so.
    /// A rate limit or a dropped connection says nothing about the model, and
    /// recording it would mark good models bad.
    static func classify(_ error: Error) -> Outcome {
        if error is CancellationError { return .inconclusive("Cancelled") }
        guard let e = error as? AVGError else { return .inconclusive(error.localizedDescription) }
        switch e {
        case .missingKey: return .authFailed("Add the key first.")
        case .message(let m): return .inconclusive(m)
        case .provider(let kind, _, let message):
            switch kind {
            case .auth: return .authFailed("The key was rejected.")
            case .modelMissing: return .failed("Not available to this account")
            case .quota: return .failed("Over quota")
            case .billing: return .failed("Needs billing")
            // A malformed tool call (the in-app assistant's) says nothing about
            // whether the model can write text, so it is never recorded as a failure.
            case .rateLimit, .contentFilter, .network, .timeout, .dependency, .bug, .malformedOutput: return .inconclusive(message)
            }
        }
    }

    /// Only the three text model lists can be probed.
    static func isProbeable(_ catalogKey: String) -> Bool {
        ["gemini_text", "groq_text", "pollinations_text"].contains(catalogKey)
    }

    @MainActor
    static func provider(catalogKey: String, model: String) throws -> TextProvider? {
        switch catalogKey {
        case "gemini_text": return GeminiText(key: try Keychain.require(.gemini), model: model)
        case "groq_text": return ChatText.groq(key: try Keychain.require(.groq), model: model)
        case "pollinations_text": return ChatText.pollinations(key: Keychain.get(.pollinations), model: model)
        default: return nil
        }
    }

    @MainActor
    static func run(catalogKey: String, model: String) async -> Outcome {
        do {
            guard let provider = try provider(catalogKey: catalogKey, model: model) else {
                return .inconclusive("This list can't be tested.")
            }
            let reply = try await provider.complete(prompt, system: nil)
            return reply.trimmed.isEmpty ? .failed("Answered with nothing") : .works
        } catch {
            return classify(error)
        }
    }
}

@MainActor
@Observable
final class ModelPrefs {
    struct Sweep: Equatable { var done: Int; var total: Int }

    private(set) var favourites = ModelFavourites()
    private(set) var probes = ProbeResults()
    /// Models that recently finished a real phase, newest first (ModelHistory.swift).
    private(set) var history = ModelHistory()
    /// Models the APP chose (catalogue key -> model id), as opposed to ones the
    /// person set. ModelAutoSetup only ever replaces the factory default or a
    /// model it picked itself - anything else was a deliberate choice.
    private(set) var autoPicks = AutoPicks()
    /// What the last automatic setup did, newest last. Memory only: it is a
    /// status line for the screen, not something to keep.
    private(set) var setupNotes: [String] = []
    /// Sweeps in progress, by model list.
    private(set) var sweeping: [String: Sweep] = [:]
    /// Why the last sweep of a list stopped early (a rejected key), if it did.
    private(set) var lastNote: [String: String] = [:]

    @ObservationIgnored private var tasks: [String: Task<Void, Never>] = [:]
    @ObservationIgnored private let favouritesURL: URL
    @ObservationIgnored private let probesURL: URL
    @ObservationIgnored private let historyURL: URL
    @ObservationIgnored private let autoPicksURL: URL

    /// `directory` is injectable so tests never touch the real Application Support.
    init(directory: URL? = nil) {
        let dir = directory ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("ModelPrefs", isDirectory: true)
        favouritesURL = dir.appendingPathComponent("model_prefs.json")
        probesURL = dir.appendingPathComponent("working_models.json")
        historyURL = dir.appendingPathComponent("model_history.json")
        autoPicksURL = dir.appendingPathComponent("auto_picks.json")
        favourites = Self.read(ModelFavourites.self, from: favouritesURL) ?? ModelFavourites()
        probes = Self.read(ProbeResults.self, from: probesURL) ?? ProbeResults()
        history = Self.read(ModelHistory.self, from: historyURL) ?? ModelHistory()
        autoPicks = Self.read(AutoPicks.self, from: autoPicksURL) ?? AutoPicks()
    }

    // MARK: Favourites

    func isFavourite(_ model: String, in catalog: String) -> Bool { favourites.contains(model, in: catalog) }

    func toggleFavourite(_ model: String, in catalog: String) {
        favourites.toggle(model, in: catalog)
        Self.write(favourites, to: favouritesURL)
    }

    // MARK: Probe results

    func result(_ model: String, in catalog: String) -> ProbeResult? { probes.byCatalog[catalog]?[model] }
    func hasResults(_ catalog: String) -> Bool { !(probes.byCatalog[catalog]?.isEmpty ?? true) }

    func workingCount(_ catalog: String) -> Int { probes.byCatalog[catalog]?.values.filter(\.works).count ?? 0 }

    /// Favourites first, then models that worked, then untested, then ones
    /// that failed - each group keeping the provider's own order. Failed
    /// models are still listed (at the bottom): hiding them would make an id
    /// you know is fine impossible to find after one bad probe.
    static func order(_ entries: [CatalogEntry], favourites: Set<String>, probes: [String: ProbeResult]) -> [CatalogEntry] {
        func rank(_ id: String) -> Int {
            if favourites.contains(id) { return 0 }
            guard let r = probes[id] else { return 2 }
            return r.works ? 1 : 3
        }
        return entries.enumerated()
            .sorted { (rank($0.element.id), $0.offset) < (rank($1.element.id), $1.offset) }
            .map(\.element)
    }

    func ordered(_ entries: [CatalogEntry], catalog: String) -> [CatalogEntry] {
        Self.order(entries, favourites: Set(favourites.byCatalog[catalog] ?? []), probes: probes.byCatalog[catalog] ?? [:])
    }

    /// New findings win; models that were not retested (a cancelled sweep, or
    /// an inconclusive answer) keep what was known before.
    static func merged(existing: [String: ProbeResult], fresh: [String: ProbeResult]) -> [String: ProbeResult] {
        existing.merging(fresh) { _, new in new }
    }

    // MARK: Working-model history

    /// A phase just finished on `model`. Skips the write when it is already the
    /// newest entry, so a run of videos on one model does not rewrite the file
    /// every time (the date of the first success is kept; it only ages the entry).
    func recordSuccess(kind: String, provider: String, model: String) {
        if let newest = history.recent(kind: kind).first, newest.provider == provider, newest.model == model { return }
        history.record(kind: kind, provider: provider, model: model)
        Self.write(history, to: historyURL)
    }

    func recentUses(kind: String) -> [ModelUse] { history.recent(kind: kind) }

    // MARK: Sweeping

    /// Probes every model in `catalog`, one at a time (gently: a burst of
    /// calls is how a free tier gets rate-limited), recording as it goes.
    func startSweep(_ catalog: ModelCatalog) {
        startSweep(catalogKey: catalog.cacheKey, ids: catalog.entries.map(\.id))
    }

    /// The same sweep over just `ids` - the automatic setup checks a short list
    /// (the few best candidates), not every model the provider offers.
    func startSweep(catalogKey key: String, ids: [String]) {
        guard ModelProbe.isProbeable(key), tasks[key] == nil, !ids.isEmpty else { return }
        sweeping[key] = Sweep(done: 0, total: ids.count)
        lastNote[key] = nil
        tasks[key] = Task { [weak self] in
            var fresh: [String: ProbeResult] = [:]
            var stoppedBecause: String?
            for (i, id) in ids.enumerated() {
                if Task.isCancelled { break }
                switch await ModelProbe.run(catalogKey: key, model: id) {
                case .works: fresh[id] = ProbeResult(works: true, checked: Date(), note: nil)
                case .failed(let why): fresh[id] = ProbeResult(works: false, checked: Date(), note: why)
                case .inconclusive: break
                case .authFailed(let why): stoppedBecause = why
                }
                self?.sweeping[key] = Sweep(done: i + 1, total: ids.count)
                if stoppedBecause != nil { break }
            }
            self?.finishSweep(key, fresh: fresh, stoppedBecause: stoppedBecause)
        }
    }

    func cancelSweep(_ catalogKey: String) { tasks[catalogKey]?.cancel() }

    private func finishSweep(_ key: String, fresh: [String: ProbeResult], stoppedBecause: String?) {
        probes.byCatalog[key] = Self.merged(existing: probes.byCatalog[key] ?? [:], fresh: fresh)
        Self.write(probes, to: probesURL)
        lastNote[key] = stoppedBecause
        sweeping[key] = nil
        tasks[key] = nil
    }

    /// Probes `ids` and returns when done. If a sweep of this list is already
    /// running (the person pressed "check"), waits for that one instead.
    func check(catalogKey key: String, models ids: [String]) async {
        startSweep(catalogKey: key, ids: ids)
        await tasks[key]?.value
    }

    // MARK: Automatic picks

    func autoPick(_ catalogKey: String) -> String? { autoPicks.byCatalog[catalogKey] }

    func setAutoPick(_ model: String, for catalogKey: String) {
        autoPicks.byCatalog[catalogKey] = model
        Self.write(autoPicks, to: autoPicksURL)
    }

    func addSetupNote(_ line: String) {
        setupNotes.append(line)
        if setupNotes.count > 20 { setupNotes.removeFirst(setupNotes.count - 20) }
    }

    // MARK: Storage

    private static func read<T: Decodable>(_ type: T.Type, from url: URL) -> T? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(type, from: data)
    }

    private static func write<T: Encodable>(_ value: T, to url: URL) {
        guard let data = try? JSONEncoder().encode(value) else { return }
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? data.write(to: url, options: .atomic)
    }
}

/// Which model the app picked for each list, persisted so a later run can tell
/// "the app chose this" from "the person chose this".
struct AutoPicks: Codable, Equatable {
    var byCatalog: [String: String] = [:]
}

import Foundation
import Observation
import UIKit

enum AppTab: Hashable { case library, create, channels, settings }

struct JobState: Equatable {
    var phase: PhaseID
    var progress: Double
    var message: String
}

struct LogEntry: Identifiable {
    let id = UUID()
    let date = Date()
    let message: String
    let isError: Bool
}

private struct Meta: Codable {
    var settings: AppSettings
    var channels: [Channel]
    var activeChannelID: UUID?
    var reusableImages: [ReusableImage] = []
}

@MainActor
@Observable
final class Store {
    var projects: [Project] = []
    var channels: [Channel] = []
    var activeChannelID: UUID?
    var settings = AppSettings() { didSet { saveMeta() } }
    var reusableImages: [ReusableImage] = [] { didSet { saveMeta() } }
    var jobs: [String: JobState] = [:]
    var log: [LogEntry] = []
    var tab: AppTab = .library
    var libraryPath: [String] = []
    /// Set when `load()` finds a phase stuck `.running` from a previous
    /// launch (the app was killed or lost its background grace period
    /// mid-render - see `BackgroundRender`). Surfaced as a "tap to resume"
    /// banner rather than auto-resumed, since silently re-firing paid API
    /// calls without the user asking isn't something this should do quietly.
    var interruptedProjectID: String?

    @ObservationIgnored private var tasks: [String: Task<Void, Never>] = [:]
    @ObservationIgnored private var loading = false

    static let root: URL = {
        let u = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        return u
    }()
    static var projectsDir: URL { root.appendingPathComponent("Projects", isDirectory: true) }
    static var reusableImagesDir: URL { root.appendingPathComponent("ReusableImages", isDirectory: true) }
    private static var metaURL: URL { root.appendingPathComponent(".avg-meta.json") }

    init() { load() }

    // MARK: Persistence

    func load() {
        loading = true
        defer { loading = false }
        let fm = FileManager.default
        try? fm.createDirectory(at: Self.projectsDir, withIntermediateDirectories: true)
        if let d = try? Data(contentsOf: Self.metaURL), let m = try? JSONDecoder.avg.decode(Meta.self, from: d) {
            settings = m.settings
            channels = m.channels
            activeChannelID = m.activeChannelID
            reusableImages = m.reusableImages
        }
        if channels.isEmpty {
            let c = Channel(name: "My Channel", niche: "curious facts")
            channels = [c]
            activeChannelID = c.id
        }
        let dirs = (try? fm.contentsOfDirectory(at: Self.projectsDir, includingPropertiesForKeys: nil)) ?? []
        projects = dirs.compactMap { dir in
            guard let d = try? Data(contentsOf: dir.appendingPathComponent("project.json")) else { return nil }
            var p = try? JSONDecoder.avg.decode(Project.self, from: d)
            // A render interrupted by the app closing is not running anymore.
            if let pp = p {
                for (k, r) in pp.phases where r.status == .running {
                    p?.phases[k]?.status = .pending
                    interruptedProjectID = pp.id
                }
            }
            return p
        }.sorted { $0.created > $1.created }
    }

    func saveMeta() {
        guard !loading else { return }
        let m = Meta(settings: settings, channels: channels, activeChannelID: activeChannelID, reusableImages: reusableImages)
        if let d = try? JSONEncoder.avg.encode(m) { try? d.write(to: Self.metaURL, options: .atomic) }
    }

    func save(_ p: Project) {
        let dir = folder(for: p.id)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        if let d = try? JSONEncoder.avg.encode(p) { try? d.write(to: dir.appendingPathComponent("project.json"), options: .atomic) }
    }

    func folder(for id: String) -> URL { Self.projectsDir.appendingPathComponent(id, isDirectory: true) }
    func file(_ p: Project, _ name: String?) -> URL? { name.map { folder(for: p.id).appendingPathComponent($0) } }

    func project(_ id: String) -> Project? { projects.first { $0.id == id } }

    func update(_ id: String, _ body: (inout Project) -> Void) {
        guard let i = projects.firstIndex(where: { $0.id == id }) else { return }
        body(&projects[i])
        save(projects[i])
    }

    /// A rough, disclosed-as-rough cost estimate for one provider call (see
    /// `CostEntry`'s doc comment). No-op for a non-positive amount, so free
    /// providers never need their own guard at the call site.
    func addCost(_ id: String, _ label: String, _ amountUSD: Double) {
        guard amountUSD > 0 else { return }
        update(id) { p in
            p.costs.append(CostEntry(label: label, amountUSD: amountUSD))
            if p.costs.count > 200 { p.costs.removeFirst(p.costs.count - 200) }
        }
    }

    // MARK: Channels

    var activeChannel: Channel { channels.first { $0.id == activeChannelID } ?? channels.first ?? Channel(name: "Default") }
    func channel(for p: Project) -> Channel { channels.first { $0.id == p.channelID } ?? activeChannel }

    func upsert(_ c: Channel) {
        if let i = channels.firstIndex(where: { $0.id == c.id }) { channels[i] = c } else { channels.append(c) }
        saveMeta()
    }
    func deleteChannel(_ c: Channel) {
        channels.removeAll { $0.id == c.id }
        if activeChannelID == c.id { activeChannelID = channels.first?.id }
        saveMeta()
    }
    func activate(_ c: Channel) { activeChannelID = c.id; saveMeta() }

    // MARK: Reusable image pool
    //
    // A shared "recycle pool" the writer can reference instead of always
    // generating fresh art - see `ReusableImage`'s doc comment.

    func reusableImageURL(_ r: ReusableImage) -> URL { Self.reusableImagesDir.appendingPathComponent(r.fileName) }

    @discardableResult
    func addReusableImage(data: Data, description: String) -> ReusableImage {
        try? FileManager.default.createDirectory(at: Self.reusableImagesDir, withIntermediateDirectories: true)
        let name = "\(UUID().uuidString).jpg"
        try? data.write(to: Self.reusableImagesDir.appendingPathComponent(name))
        let r = ReusableImage(fileName: name, description: description.trimmed)
        reusableImages.append(r)
        return r
    }

    func updateReusableImage(_ id: String, description: String) {
        guard let i = reusableImages.firstIndex(where: { $0.id == id }) else { return }
        reusableImages[i].description = description.trimmed
    }

    func deleteReusableImage(_ id: String) {
        guard let i = reusableImages.firstIndex(where: { $0.id == id }) else { return }
        try? FileManager.default.removeItem(at: reusableImageURL(reusableImages[i]))
        reusableImages.remove(at: i)
    }

    // MARK: Projects

    @discardableResult
    func createProject(topic: String, title: String, channel: Channel, aspect: AspectRatio) -> Project {
        let stamp = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "-")
        let id = "\(stamp)-\(UUID().uuidString.prefix(6))"
        let t = title.trimmingCharacters(in: .whitespaces)
        let p = Project(id: id, title: t.isEmpty ? String(topic.prefix(48)) : t, topic: topic, channelID: channel.id, aspectRatio: aspect)
        projects.insert(p, at: 0)
        save(p)
        return p
    }

    func delete(_ id: String) {
        cancel(id)
        try? FileManager.default.removeItem(at: folder(for: id))
        projects.removeAll { $0.id == id }
        libraryPath.removeAll { $0 == id }
        if interruptedProjectID == id { interruptedProjectID = nil }
    }

    func setArchived(_ id: String, _ v: Bool) { update(id) { $0.archived = v } }

    /// Records one line to the app-wide Activity log (Settings → Activity &
    /// errors - unchanged behaviour) and, when `id` is given, appends it to
    /// that project's own persisted run log too - the iOS analogue of the
    /// PC's per-take `take.log` (`avg.core.take_log.TakeLog`): a plain,
    /// timestamped text file next to the project's other files, so
    /// "what happened to this video" survives the global list's 300-line
    /// cap scrolling it away, and survives the app being killed mid-run.
    ///
    /// One deliberate simplification versus the PC: the PC keeps one log
    /// FILE per take, never mixing two attempts' history together. This
    /// app doesn't split storage per take the same way, so `logPath(for:)`
    /// is one continuous file per PROJECT across every run - `start(_:
    /// from:)` writes a "▶ Started…" separator line at the top of each run
    /// so distinct attempts stay visually distinguishable without needing
    /// a second on-disk layout.
    func record(_ id: String?, _ message: String, error: Bool = false) {
        log.insert(LogEntry(message: message, isError: error), at: 0)
        if log.count > 300 { log.removeLast() }
        if let id { appendToProjectLog(id, message) }
    }

    private static let logStamp: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()

    func logPath(for id: String) -> URL { folder(for: id).appendingPathComponent("take.log") }

    private func appendToProjectLog(_ id: String, _ message: String) {
        let line = "\(Self.logStamp.string(from: Date())) \(message)\n"
        let path = logPath(for: id)
        // A log that can't be written must never break a run - same
        // tolerance the PC's own TakeLog._append applies (a read-only
        // folder, a locked file cost one failed attempt, not a crash).
        do {
            try FileManager.default.createDirectory(at: folder(for: id), withIntermediateDirectories: true)
            if let data = line.data(using: .utf8) {
                if let handle = try? FileHandle(forWritingTo: path) {
                    defer { try? handle.close() }
                    handle.seekToEndOfFile()
                    handle.write(data)
                } else {
                    try data.write(to: path)
                }
            }
        } catch {}
    }

    /// Tail of a project's run log for the Studio screen's "Log" panel -
    /// mirrors the PC's `GET /api/projects/{id}/takes/{take}/log` (tail
    /// rather than the whole file: a verbose run is a lot of lines, and
    /// "what happened, especially at the end" is almost always the
    /// question). No log yet is a normal, expected state - a project that
    /// hasn't rendered - so this returns an empty tail rather than throwing.
    func projectLogTail(_ id: String, lines: Int = 400) -> (lines: [String], truncated: Bool, total: Int) {
        guard let text = try? String(contentsOf: logPath(for: id), encoding: .utf8) else { return ([], false, 0) }
        let all = text.split(separator: "\n", omittingEmptySubsequences: true).map(String.init)
        return (Array(all.suffix(lines)), all.count > lines, all.count)
    }

    // MARK: Jobs

    func isRunning(_ id: String) -> Bool { tasks[id] != nil }
    var runningProjectID: String? { tasks.keys.first }

    func start(_ id: String, from phase: PhaseID = .writing) {
        guard tasks[id] == nil else { return }
        if interruptedProjectID == id { interruptedProjectID = nil }
        tasks[id] = Task { [weak self] in
            // Binds this render's project id as the `@TaskLocal` HTTP.swift
            // reads on a retry/give-up. Bound around just the `run()` call
            // (Swift concurrency propagates a task-local through every
            // `await` inside it, including several layers down into
            // `HTTP.retrying`) - two projects rendering at once each get
            // their own binding, correctly, since each has its own `Task`.
            await RenderLog.$record.withValue({ [weak self] message in
                await MainActor.run { self?.record(id, message) }
            }) {
                await self?.run(id, from: phase)
            }
            self?.tasks[id] = nil
            self?.jobs[id] = nil
        }
    }

    func cancel(_ id: String) {
        tasks[id]?.cancel()
    }

    private func run(_ id: String, from start: PhaseID) async {
        // Buys roughly the standard background grace period (historically up
        // to a few minutes, often less) - not enough on its own for a
        // multi-minute pipeline. `BackgroundRender` is the opportunistic
        // continuation for when this runs out; see its doc comment for why
        // neither mechanism can promise the render finishes unattended.
        let bg = UIApplication.shared.beginBackgroundTask(withName: "AVG render") {}
        defer { UIApplication.shared.endBackgroundTask(bg) }
        record(id, "▶ Started “\(project(id)?.title ?? id)” from \(start.title).")
        for phase in PhaseID.allCases where phase.order >= start.order {
            update(id) { $0.phases[phase.rawValue] = PhaseRecord(status: .pending) }
        }
        for phase in PhaseID.allCases where phase.order >= start.order {
            if Task.isCancelled { break }
            update(id) { $0.phases[phase.rawValue] = PhaseRecord(status: .running, started: Date()) }
            jobs[id] = JobState(phase: phase, progress: 0, message: "\(phase.title)…")
            record(id, "— \(phase.title) —")
            do {
                try await Pipeline(store: self, projectID: id).execute(phase)
                update(id) {
                    $0.phases[phase.rawValue]?.status = .done
                    $0.phases[phase.rawValue]?.finished = Date()
                }
                record(id, "\(phase.title) done.")
            } catch is CancellationError {
                update(id) { $0.phases[phase.rawValue] = PhaseRecord(status: .pending) }
                record(id, "Render cancelled: \(project(id)?.title ?? id)")
                break
            } catch {
                let msg = error.localizedDescription
                update(id) {
                    $0.phases[phase.rawValue]?.status = .failed
                    $0.phases[phase.rawValue]?.error = msg
                    $0.phases[phase.rawValue]?.finished = Date()
                }
                record(id, "\(phase.title) failed for “\(project(id)?.title ?? id)”: \(msg)", error: true)
                break
            }
        }
    }

    func progress(_ id: String, _ value: Double, _ message: String) {
        guard var j = jobs[id] else { return }
        // Every meaningfully NEW step message also becomes a log line -
        // the bulk of "everything happening" for the Log panel, at zero
        // extra call sites: every phase in Pipeline.swift already narrates
        // its own steps through this one function. Guarded on the message
        // actually changing (not just the progress fraction ticking up) so
        // a percentage-only update doesn't spam the log with a line per
        // frame, mirroring TakeLog's own repeat-collapsing intent.
        if message != j.message { record(id, message) }
        j.progress = value
        j.message = message
        jobs[id] = j
    }
}

extension JSONEncoder {
    static var avg: JSONEncoder {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .iso8601
        e.outputFormatting = [.prettyPrinted, .sortedKeys]
        return e
    }
}

extension JSONDecoder {
    static var avg: JSONDecoder {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }
}

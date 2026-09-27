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

    func record(_ message: String, error: Bool = false) {
        log.insert(LogEntry(message: message, isError: error), at: 0)
        if log.count > 300 { log.removeLast() }
    }

    // MARK: Jobs

    func isRunning(_ id: String) -> Bool { tasks[id] != nil }
    var runningProjectID: String? { tasks.keys.first }

    func start(_ id: String, from phase: PhaseID = .writing) {
        guard tasks[id] == nil else { return }
        if interruptedProjectID == id { interruptedProjectID = nil }
        tasks[id] = Task { [weak self] in
            await self?.run(id, from: phase)
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
        for phase in PhaseID.allCases where phase.order >= start.order {
            update(id) { $0.phases[phase.rawValue] = PhaseRecord(status: .pending) }
        }
        for phase in PhaseID.allCases where phase.order >= start.order {
            if Task.isCancelled { break }
            update(id) { $0.phases[phase.rawValue] = PhaseRecord(status: .running, started: Date()) }
            jobs[id] = JobState(phase: phase, progress: 0, message: "\(phase.title)…")
            do {
                try await Pipeline(store: self, projectID: id).execute(phase)
                update(id) {
                    $0.phases[phase.rawValue]?.status = .done
                    $0.phases[phase.rawValue]?.finished = Date()
                }
            } catch is CancellationError {
                update(id) { $0.phases[phase.rawValue] = PhaseRecord(status: .pending) }
                record("Render cancelled: \(project(id)?.title ?? id)")
                break
            } catch {
                let msg = error.localizedDescription
                update(id) {
                    $0.phases[phase.rawValue]?.status = .failed
                    $0.phases[phase.rawValue]?.error = msg
                    $0.phases[phase.rawValue]?.finished = Date()
                }
                record("\(phase.title) failed for “\(project(id)?.title ?? id)”: \(msg)", error: true)
                break
            }
        }
    }

    func progress(_ id: String, _ value: Double, _ message: String) {
        guard var j = jobs[id] else { return }
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

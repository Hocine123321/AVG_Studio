import Foundation
import Observation
import UIKit

/// Order matches the mock's own floating tab bar exactly: Library,
/// Studio, Create, Channels, Settings.
enum AppTab: Hashable { case library, studio, create, channels, settings }

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

    init(settings: AppSettings, channels: [Channel], activeChannelID: UUID?, reusableImages: [ReusableImage]) {
        self.settings = settings; self.channels = channels
        self.activeChannelID = activeChannelID; self.reusableImages = reusableImages
    }
    // Each part decodes independently, so one bad/missing part can't take
    // the others down with it.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: AnyKey.self)
        settings = c.value("settings", AppSettings())
        channels = c.value("channels", [Channel]())
        activeChannelID = c.value("activeChannelID", UUID?.none)
        reusableImages = c.value("reusableImages", [ReusableImage]())
    }
}

@MainActor
@Observable
final class Store {
    var projects: [Project] = []
    var channels: [Channel] = []
    var activeChannelID: UUID?
    // Debounced: a TextField bound to `settings` used to rewrite the whole
    // meta file (pretty-printed JSON, synchronously, on the main thread) on
    // every keystroke and every slider tick.
    var settings = AppSettings() { didSet { scheduleMetaSave(); refreshIdleTimer() } }
    var reusableImages: [ReusableImage] = [] { didSet { scheduleMetaSave() } }
    var jobs: [String: JobState] = [:]
    /// Projects waiting for a free slot, in the order they were started.
    var queuedIDs: [String] = []
    @ObservationIgnored private var pendingStarts: [String: PhaseID] = [:]
    @ObservationIgnored private var fallbackLedger = FallbackLedger()
    /// What a run is about to record on the take it produces: which take it
    /// branched from and why (see Takes.swift). Set when the run starts, read
    /// by the render phase when it creates the take.
    struct TakeContext { var basedOn: Int?; var note: String? }
    @ObservationIgnored var takeContext: [String: TakeContext] = [:]
    @ObservationIgnored private var pendingNotes: [String: String] = [:]
    /// Serialises the render phase across all running videos (RenderLease.swift).
    @ObservationIgnored let renderLease = RenderLease()
    var log: [LogEntry] = []
    /// Model favourites and proven-working results (ModelPrefs.swift).
    let modelPrefs = ModelPrefs()
    /// Errors, kept across launches (see ErrorHistory.swift), newest first.
    var errorHistory: [ErrorRecord] = []
    var tab: AppTab = .library
    /// What the Settings tab has pushed. Lives here (not in the view) so an
    /// error elsewhere - a bad key in the assistant chat, a failed render -
    /// can land the person on the exact settings page that fixes it.
    var settingsPath: [SettingsRoute] = []

    /// Switches to Settings and opens `route` there.
    func openSettings(_ route: SettingsRoute) {
        settingsPath = [route]
        tab = .settings
    }
    /// The failure prompt currently waiting on the user (nil = none), and
    /// the continuation the paused render resumes from once they choose.
    var recoveryPlan: RecoveryPlan?
    @ObservationIgnored private var triedAutoFallback: Set<String> = []
    @ObservationIgnored private var recoveryContinuation: CheckedContinuation<RecoveryAction, Never>?
    /// Set by the "Reword the topic" choice so Create can pre-open on it.
    var pendingReword: String?
    /// The mock's `S.cur` - which project the (now tab-hosted, not pushed)
    /// Studio screen shows. `nil` means "nothing explicitly picked yet";
    /// `currentProject` below falls back to the most recent project (index
    /// 0, same as `createProject` inserting new ones at the front) exactly
    /// like the mock's own `S.cur` starting at `0`.
    var currentProjectID: String?
    /// Set when `load()` finds a phase stuck `.running` from a previous
    /// launch (the app was killed or lost its background grace period
    /// mid-render - see `BackgroundRender`). Surfaced as a "tap to resume"
    /// banner rather than auto-resumed, since silently re-firing paid API
    /// calls without the user asking isn't something this should do quietly.
    var interruptedProjectID: String?

    @ObservationIgnored private var tasks: [String: Task<Void, Never>] = [:]
    /// The in-app chat assistant. It drives this store through `AgentHost`.
    let agent = Agent()
    @ObservationIgnored private var loading = false
    @ObservationIgnored private var metaSaveTask: Task<Void, Never>?

    static let root: URL = {
        let u = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        return u
    }()
    static var projectsDir: URL { root.appendingPathComponent("Projects", isDirectory: true) }
    static var reusableImagesDir: URL { root.appendingPathComponent("ReusableImages", isDirectory: true) }
    private static var metaURL: URL { root.appendingPathComponent(".avg-meta.json") }
    private static var errorsURL: URL { root.appendingPathComponent(".avg-errors.json") }

    init() {
        load()
        agent.host = self
    }

    // MARK: Persistence

    func load() {
        loading = true
        defer { loading = false }
        errorHistory = ErrorHistory.load(Self.errorsURL)
        let fm = FileManager.default
        try? fm.createDirectory(at: Self.projectsDir, withIntermediateDirectories: true)
        if let d = try? Data(contentsOf: Self.metaURL) {
            if let m = try? JSONDecoder.avg.decode(Meta.self, from: d) {
                settings = m.settings
                channels = m.channels
                activeChannelID = m.activeChannelID
                reusableImages = m.reusableImages
            } else {
                // Unreadable (corrupt, or a future incompatible format):
                // keep a copy BEFORE anything can overwrite it with
                // defaults, instead of silently wiping the user's data.
                let backup = Self.metaURL.deletingLastPathComponent().appendingPathComponent("meta.unreadable-\(Int(Date().timeIntervalSince1970)).json")
                try? d.write(to: backup, options: .atomic)
                log.append(LogEntry(message: "Saved settings couldn't be read; a backup copy was kept.", isError: true))
            }
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

    /// Coalesces a burst of settings edits into one write ~0.4s after the last.
    private func scheduleMetaSave() {
        guard !loading else { return }
        metaSaveTask?.cancel()
        metaSaveTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(400))
            guard !Task.isCancelled, let self else { return }
            self.metaSaveTask = nil
            self.saveMeta()
        }
    }

    /// Writes any pending settings change immediately. Called when the app
    /// leaves the foreground so a debounced edit can never be lost.
    func flushMeta() {
        guard metaSaveTask != nil else { return }
        metaSaveTask?.cancel()
        metaSaveTask = nil
        saveMeta()
    }

    func saveMeta() {
        guard !loading else { return }
        // Never persist a run's fallback: the file always holds what the
        // person chose, even if the app is killed mid-run.
        let m = Meta(settings: fallbackLedger.reverted(settings), channels: channels, activeChannelID: activeChannelID, reusableImages: reusableImages)
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

    /// Whatever Studio (a persistent tab, not a pushed screen - Skin v2)
    /// should show right now.
    var currentProject: Project? { currentProjectID.flatMap(project) ?? projects.first }

    /// The mock's "pick" action: set which project Studio shows AND switch
    /// to it, in one call - tapping a library card is a tab switch now,
    /// not a push.
    func selectForStudio(_ id: String) {
        currentProjectID = id
        tab = .studio
    }

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

    // MARK: Takes (see Takes.swift)

    /// What `phase` would depend on if it ran right now.
    func currentInputs(_ phase: PhaseID, for id: String) -> [String: String] {
        guard let p = project(id) else { return [:] }
        return PhaseInputs.describe(phase, settings: settings, channel: channel(for: p), hasTranscriptionKey: Keychain.has(.assemblyai))
    }

    /// Which finished phases the CURRENT settings have made out of date.
    func staleReport(_ p: Project) -> StaleReport {
        guard p.phases.values.contains(where: { $0.inputs != nil }) else { return StaleReport() }
        return StaleReport.compute(project: p, settings: settings, channel: channel(for: p), hasTranscriptionKey: Keychain.has(.assemblyai))
    }

    /// Re-runs from the earliest phase the settings made stale, as a NEW take
    /// (the old one is kept), noting what changed.
    func updateToSettings(_ id: String) {
        guard let p = project(id), !isBusy(id) else { return }
        let report = staleReport(p)
        guard let first = report.firstPhase else { return }
        let labels = report.changedLabels
        let shown = labels.prefix(4).joined(separator: ", ")
        let note = "Changed: " + shown + (labels.count > 4 ? " +\(labels.count - 4) more" : "")
        start(id, from: first, note: note)
    }

    /// Called when a run starts. If the run would rewrite the working files
    /// (anything from Voice back), the files behind every not-yet-archived
    /// take are copied aside first, so those takes stay restorable. Returns the
    /// take this run branches from. A failed copy is logged and the run
    /// carries on - the take stays watchable, just not restorable.
    private func prepareBranch(_ id: String, from start: PhaseID) -> Int? {
        guard let p = project(id) else { return nil }
        let based = p.workingTakeID ?? p.takes.last?.id
        // Captions and Render only rewrite JSON (which every take's snapshot
        // already holds) and the take's own video file; the images and audio
        // are untouched, so nothing needs saving and the working copy still
        // equals its take.
        guard start.order <= PhaseID.voice.order else { return based }
        archiveUnarchivedTakes(id)
        update(id) { $0.workingTakeID = nil }
        return based
    }

    /// Every take whose files are still the working copy shares them (an
    /// invariant: any run that rewrites them archives first), so ONE copy
    /// serves them all.
    private func archiveUnarchivedTakes(_ id: String) {
        guard let p = project(id) else { return }
        let waiting = p.takes.filter { $0.snapshot != nil && $0.archive == nil }
        guard let newest = waiting.map(\.id).max() else { return }
        do {
            let rel = try TakeArchive.archive(takeID: newest, projectFolder: folder(for: id))
            update(id) { proj in
                for i in proj.takes.indices where proj.takes[i].snapshot != nil && proj.takes[i].archive == nil {
                    proj.takes[i].archive = rel
                }
            }
        } catch {
            record(id, "Couldn't keep a copy of the earlier take's files (\(error.localizedDescription)); it can still be watched but not continued from.", error: true)
        }
    }

    /// Makes `takeID` the working version again: its content and files come
    /// back, and its video becomes the project's output. The version being
    /// replaced is saved first, so nothing is lost.
    func restoreTake(_ id: String, takeID: Int) throws {
        guard let p = project(id), let take = p.takes.first(where: { $0.id == takeID }) else { return }
        guard !isBusy(id) else { throw AVGError.message("Wait for this video to finish first.") }
        guard let snapshot = take.snapshot else { throw AVGError.message("This take was made before takes could be continued from.") }
        let wasArchived = take.archive != nil
        archiveUnarchivedTakes(id)
        if wasArchived, let rel = project(id)?.takes.first(where: { $0.id == takeID })?.archive {
            try TakeArchive.restore(archive: rel, projectFolder: folder(for: id))
        }
        update(id) { proj in
            snapshot.apply(to: &proj)
            proj.output = take.file
            proj.workingTakeID = takeID
        }
        record(id, "Continuing from \(take.label).")
    }

    /// Removes a take: its video, its saved snapshot and - once no other take
    /// uses it - its archived files. If it was the one on show, the newest
    /// remaining take takes over.
    func deleteTake(_ id: String, takeID: Int) {
        guard let p = project(id), !isBusy(id), let take = p.takes.first(where: { $0.id == takeID }) else { return }
        let orphans = TakeArchive.orphanedArchives(takes: p.takes, deleting: takeID)
        try? FileManager.default.removeItem(at: folder(for: id).appendingPathComponent(take.file))
        for rel in orphans { TakeArchive.remove(archive: rel, projectFolder: folder(for: id)) }
        update(id) { proj in
            proj.takes.removeAll { $0.id == takeID }
            if proj.workingTakeID == takeID { proj.workingTakeID = nil }
            if proj.output == take.file { proj.output = proj.takes.last?.file }
        }
        record(id, "Deleted \(take.label).")
    }

    /// A take's own content and where its files are: its snapshot applied to
    /// a copy of the project, rooted at its archive when it has one. A take
    /// with no snapshot (made before takes existed) is just the project as is.
    func takeContent(_ p: Project, _ take: Take) -> (project: Project, root: URL) {
        guard let snapshot = take.snapshot else { return (p, folder(for: p.id)) }
        var content = p
        snapshot.apply(to: &content)
        let root = take.archive.map { folder(for: p.id).appendingPathComponent($0, isDirectory: true) } ?? folder(for: p.id)
        return (content, root)
    }

    // MARK: Steps (see Steps.swift)

    /// Records one step's state. Repeating "running" for a step that is
    /// already running is ignored, so progress callbacks can call it freely.
    func setStep(_ id: String, _ phase: PhaseID, _ key: String, _ status: StepStatus, detail: String? = nil, seconds: Double? = nil) {
        guard let current = project(id), !(status == .running && current.step(phase, key).status == .running) else { return }
        update(id) { p in
            var all = p.stepRecords ?? [:]
            let k = p.stepKey(phase, key)
            var r = all[k] ?? StepRecord()
            switch status {
            case .running:
                r = StepRecord(status: .running, started: Date())
            case .done, .failed, .skipped:
                let now = Date()
                r.finished = now
                if r.started == nil { r.started = now }
            case .pending:
                r = StepRecord()
            }
            r.status = status
            if let detail { r.detail = detail }
            if let seconds { r.seconds = seconds }
            all[k] = r
            p.stepRecords = all
        }
    }

    /// Forgets every step of `phase` and the phases after it - called when a
    /// run restarts from there, so stale "done" ticks never outlive a redo.
    func clearSteps(_ id: String, from phase: PhaseID) {
        update(id) { p in
            guard var all = p.stepRecords else { return }
            let stale = Set(PhaseID.allCases.filter { $0.order >= phase.order }.map { $0.rawValue + "." })
            all = all.filter { entry in !stale.contains { entry.key.hasPrefix($0) } }
            p.stepRecords = all.isEmpty ? nil : all
        }
    }

    /// When a phase ends abnormally, whichever step was mid-flight ends the
    /// same way: failed after an error, back to pending after a cancel.
    func settleRunningSteps(_ id: String, _ phase: PhaseID, as status: StepStatus) {
        guard let p = project(id) else { return }
        for def in phase.steps where p.step(phase, def.key).status == .running {
            setStep(id, phase, def.key, status)
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
        if currentProjectID == id { currentProjectID = nil }
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
        // Nothing credential-shaped reaches the screen, the project log (which
        // the Files app can show) or the error history. Errors also scrub the
        // exact stored key values; routine lines only the cheap patterns.
        let secrets = error ? Keychain.allSecrets : []
        let message = Redactor.scrub(message, secrets: secrets)
        log.insert(LogEntry(message: message, isError: error), at: 0)
        if log.count > 300 { log.removeLast() }
        if let id { appendToProjectLog(id, message) }
        if error {
            let title = id.flatMap { project($0)?.title }
            errorHistory = ErrorHistory.append(ErrorRecord(date: Date(), message: message, project: title), to: Self.errorsURL)
        }
    }

    func clearErrorHistory() {
        ErrorHistory.clear(Self.errorsURL)
        errorHistory = []
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
    func isQueued(_ id: String) -> Bool { queuedIDs.contains(id) }
    /// 1-based place in the queue (1 = next to start), or nil when not queued.
    func queuePosition(_ id: String) -> Int? { queuedIDs.firstIndex(of: id).map { $0 + 1 } }
    /// Running OR waiting for a slot - what "is something already happening to
    /// this project" means for disabling its Render / Redo buttons.
    func isBusy(_ id: String) -> Bool { isRunning(id) || isQueued(id) }
    var runningProjectID: String? { tasks.keys.first }

    /// Starts a run, or QUEUES it when `maxParallelJobs` are already going.
    /// Queued, never refused (the PC's rule): with a cap of 1 this means one
    /// video at a time in the order asked for. Starting a project that is
    /// already running or queued is ignored - two runners on one
    /// `project.json` is the bug that guard exists for.
    func start(_ id: String, from phase: PhaseID = .writing, note: String? = nil) {
        guard !isBusy(id) else { return }
        if let note { pendingNotes[id] = note }
        if interruptedProjectID == id { interruptedProjectID = nil }
        let cap = max(1, settings.maxParallelJobs)
        if tasks.count >= cap {
            queuedIDs.append(id)
            pendingStarts[id] = phase
            record(id, "Queued: \(tasks.count) video\(tasks.count == 1 ? " is" : "s are") already running. Starts when a slot frees.")
            return
        }
        launch(id, from: phase)
    }

    /// Stops the screen auto-locking while anything is being made (when the
    /// setting allows). A locked screen suspends the app, which suspends its
    /// GPU access - the single most common reason a long render "stalls".
    // MARK: Run-scoped fallbacks (see FallbackLedger.swift)

    /// Switches a setting for ONE project's run (a fallback model, another
    /// provider). It is put back when that run ends and is never saved.
    func overrideForRun<T: Equatable>(_ keyPath: WritableKeyPath<AppSettings, T>, to value: T, project: String) {
        fallbackLedger.override(keyPath, to: value, owner: project, in: &settings)
    }

    private func releaseOverrides(_ id: String) {
        if fallbackLedger.release(owner: id, in: &settings) {
            record(id, "Back to your chosen models.")
        }
    }

    func refreshIdleTimer() {
        UIApplication.shared.isIdleTimerDisabled = settings.keepScreenAwake && !tasks.isEmpty
    }

    private func launch(_ id: String, from phase: PhaseID) {
        defer { refreshIdleTimer() }
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
            // The fallback this run may have switched to was for this run only.
            self?.releaseOverrides(id)
            // Tell the chat assistant (it only speaks for videos it started),
            // then give the freed slot to the next queued project.
            self?.agent.projectFinished(id)
            self?.startNextQueued()
            self?.refreshIdleTimer()
        }
    }

    /// Fills any free slots from the front of the queue.
    private func startNextQueued() {
        let cap = max(1, settings.maxParallelJobs)
        while tasks.count < cap, !queuedIDs.isEmpty {
            let next = queuedIDs.removeFirst()
            let phase = pendingStarts.removeValue(forKey: next) ?? .writing
            launch(next, from: phase)
        }
    }

    func cancel(_ id: String) {
        // Still waiting for a slot: just leave the queue.
        if let i = queuedIDs.firstIndex(of: id) {
            queuedIDs.remove(at: i)
            pendingStarts[id] = nil
            record(id, "Removed from the queue.")
            return
        }
        tasks[id]?.cancel()
        if recoveryPlan?.projectID == id { resolveRecovery(.stop) }
    }

    private func run(_ id: String, from start: PhaseID) async {
        // Buys roughly the standard background grace period (historically up
        // to a few minutes, often less) - not enough on its own for a
        // multi-minute pipeline. `BackgroundRender` is the opportunistic
        // continuation for when this runs out; see its doc comment for why
        // neither mechanism can promise the render finishes unattended.
        // The expiration handler MUST end the task: with the old empty `{}`
        // iOS treats a task that outlives its grace period without ending
        // as a watchdog violation and terminates the app.
        let bg = BackgroundTaskBox()
        if settings.allowBackgroundRendering { bg.begin(name: "AVG render") }
        defer { bg.end() }
        record(id, "▶ Started “\(project(id)?.title ?? id)” from \(start.title).")
        let basedOn = prepareBranch(id, from: start)
        takeContext[id] = TakeContext(basedOn: basedOn, note: pendingNotes.removeValue(forKey: id))
        defer { takeContext[id] = nil }
        for phase in PhaseID.allCases where phase.order >= start.order {
            update(id) { $0.phases[phase.rawValue] = PhaseRecord(status: .pending) }
        }
        clearSteps(id, from: start)
        for phase in PhaseID.allCases where phase.order >= start.order {
            if Task.isCancelled { break }
            // Captured BEFORE the phase runs: a setting changed mid-run must be
            // reported as a change next time, not silently absorbed.
            let inputs = currentInputs(phase, for: id)
            update(id) { $0.phases[phase.rawValue] = PhaseRecord(status: .running, started: Date()) }
            jobs[id] = JobState(phase: phase, progress: 0, message: "\(phase.title)…")
            record(id, "— \(phase.title) —")
            do {
                try await executeWithRecovery(phase, id: id)
                update(id) {
                    $0.phases[phase.rawValue]?.status = .done
                    $0.phases[phase.rawValue]?.finished = Date()
                    $0.phases[phase.rawValue]?.inputs = inputs
                }
                record(id, "\(phase.title) done.")
            } catch is CancellationError {
                settleRunningSteps(id, phase, as: .pending)
                update(id) { $0.phases[phase.rawValue] = PhaseRecord(status: .pending) }
                record(id, "Render cancelled: \(project(id)?.title ?? id)")
                break
            } catch {
                let msg = error.localizedDescription
                settleRunningSteps(id, phase, as: .failed)
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

    /// Runs one phase. On a failure the automatic retries couldn't fix,
    /// PAUSES the render and asks the user what to do (see Recovery.swift),
    /// then applies the choice and runs the phase again - looping until it
    /// succeeds, the user stops, or the failure has nothing useful to offer.
    private func executeWithRecovery(_ phase: PhaseID, id: String) async throws {
        var isRetry = false // true once this phase has failed at least once in this run
        while true {
            do {
                // Safeguard 7: offline -> wait for the connection instead of failing.
                if !Connectivity.shared.online {
                    record(id, "No internet. Waiting for a connection, the render resumes by itself…")
                    jobs[id]?.message = "Waiting for internet…"
                    _ = await Connectivity.shared.waitUntilOnline()
                }
                let phaseStart = ContinuousClock.now
                try await Pipeline(store: self, projectID: id, resumeExisting: isRetry).execute(phase)
                // Where the time goes, per phase, in the Live List - so a
                // slow video can be diagnosed instead of guessed at.
                record(id, "\(phase.title) took \(String(format: "%.1f", (ContinuousClock.now - phaseStart).seconds))s.")
                Recovery.noteSuccess()
                return
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                isRetry = true
                // Model fallback: on a "too expensive" failure (quota / billing),
                // drop to the fallback model by itself when the user opted in,
                // once per phase, instead of interrupting them.
                if settings.autoUseFallback, !triedAutoFallback.contains(id + phase.rawValue),
                   let avg = error as? AVGError, let k = avg.kind, k == .quota || k == .billing,
                   let m = Recovery.applyFallback(phase: phase, store: self, project: id) {
                    triedAutoFallback.insert(id + phase.rawValue)
                    record(id, "Too expensive / over quota: switched to fallback model \(m) for this video.")
                    continue
                }
                // Backup key: the main key failed (rejected, out of quota,
                // rate-limited past every retry) and the fallback model, if
                // there was one, did not help. Use the backup key for 30
                // minutes, then the main one again. If the BACKUP fails too,
                // the usual prompt appears.
                if let key = KeyBackup.activateIfPossible(after: error) {
                    record(id, "\(key.label): the main key failed, so the backup key is used for the next 30 minutes. Retrying.")
                    continue
                }
                guard let plan = Recovery.plan(for: error, project: id, phase: phase, store: self) else { throw error }
                record(id, "\(phase.title) paused: \(plan.title). Waiting for your choice.", error: true)
                jobs[id]?.message = "Needs your decision"
                Haptics.warning()
                let action: RecoveryAction = await withCheckedContinuation { c in
                    recoveryContinuation = c
                    recoveryPlan = plan
                }
                recoveryPlan = nil
                if action == .stop || action == .openKeys || action == .reword {
                    _ = await Recovery.apply(action, plan: plan, store: self)
                    throw error
                }
                if !(await Recovery.apply(action, plan: plan, store: self)) { throw error }
            }
        }
    }

    /// Called by the prompt sheet when the user taps a choice.
    func resolveRecovery(_ action: RecoveryAction) {
        recoveryContinuation?.resume(returning: action)
        recoveryContinuation = nil
    }

    /// `log: false` updates the bar and message without adding a Live List
    /// line. The renderer reports a new percentage every 15 frames; each used
    /// to become its own log line (and its own file append) because the text
    /// changed each time.
    func progress(_ id: String, _ value: Double, _ message: String, log: Bool = true) {
        guard var j = jobs[id] else { return }
        // Every meaningfully NEW step message also becomes a log line -
        // the bulk of "everything happening" for the Log panel, at zero
        // extra call sites: every phase in Pipeline.swift already narrates
        // its own steps through this one function. Guarded on the message
        // actually changing (not just the progress fraction ticking up) so
        // a percentage-only update doesn't spam the log with a line per
        // frame, mirroring TakeLog's own repeat-collapsing intent.
        if log, message != j.message { record(id, message) }
        j.progress = value
        j.message = message
        jobs[id] = j
    }
}

/// Owns one `UIBackgroundTaskIdentifier` so the expiration handler and the
/// normal-completion path can both end it, exactly once. Main-actor only:
/// iOS runs the expiration handler on the main thread and requires it to
/// call `endBackgroundTask` BEFORE returning, so ending must be synchronous
/// there (an earlier draft hopped through a `Task` and would have ended it
/// too late).
@MainActor
private final class BackgroundTaskBox {
    private var id: UIBackgroundTaskIdentifier = .invalid

    func begin(name: String) {
        id = UIApplication.shared.beginBackgroundTask(withName: name) { [weak self] in
            MainActor.assumeIsolated { self?.end() }
        }
    }

    func end() {
        guard id != .invalid else { return }
        UIApplication.shared.endBackgroundTask(id)
        id = .invalid
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

// The agent drives the app through exactly these members; see AgentTools.swift.
extension Store: AgentHost {
    /// `Store.start` gained a defaulted `note:` parameter; a protocol
    /// requirement is matched on the exact signature, so adapt it here.
    func start(_ id: String, from phase: PhaseID) { start(id, from: phase, note: nil) }
    var reusableImageCount: Int { reusableImages.count }
    func upsertChannel(_ channel: Channel) { upsert(channel) }
    func activateChannel(_ channel: Channel) { activate(channel) }
    func renameProject(_ id: String, to title: String) { update(id) { $0.title = title } }
    func deleteProject(_ id: String) { delete(id) }
    func recentActivity(limit: Int, errorsOnly: Bool) -> [String] {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return log.filter { !errorsOnly || $0.isError }.prefix(limit).map { "\(f.string(from: $0.date)) \($0.isError ? "ERROR " : "")\($0.message)" }
    }
}

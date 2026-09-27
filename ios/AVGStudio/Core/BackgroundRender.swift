import Foundation
import BackgroundTasks

/// Best-effort continuation for a render interrupted by the app being
/// backgrounded. Read this before assuming it "fixes" background rendering:
/// iOS does not let a non-exempt app run indefinitely in the background.
/// `Store.run()`'s `beginBackgroundTask` buys roughly the standard grace
/// period once the app leaves the foreground - nowhere near enough for a
/// multi-minute image+voice+encode pipeline. A `BGProcessingTask` is the
/// honest next step available: it lets the OS run one self-contained chunk
/// of work LATER, on ITS OWN schedule, when it judges the device has spare
/// capacity - not "keep going right now", not guaranteed to run soon, and
/// not going to happen at all if the user force-quits the app. That's why
/// `Store.interruptedProjectID` exists and is shown as a "tap to resume"
/// banner (StudioView/HomeView) rather than a silent promise: the honest
/// fix for unreliable background compute is a resilient, visible resume,
/// not pretending completion is guaranteed.
enum BackgroundRender {
    static let taskID = "com.avgstudio.app.render"
    private static let pendingKey = "AVGPendingRenderProjectID"

    static func register() {
        BGTaskScheduler.shared.register(forTaskWithIdentifier: taskID, using: nil) { task in
            handle(task as! BGProcessingTask)
        }
    }

    /// Call when the app is about to background while a render is running.
    /// `@MainActor` because it reads `Store` state synchronously - always
    /// called from a SwiftUI `.onChange` closure, which already runs on
    /// the main actor.
    @MainActor
    static func scheduleIfNeeded(store: Store) {
        guard let runningID = store.runningProjectID else { return }
        UserDefaults.standard.set(runningID, forKey: pendingKey)
        let request = BGProcessingTaskRequest(identifier: taskID)
        request.requiresNetworkConnectivity = true
        request.requiresExternalPower = false
        // Scheduling can legitimately fail (e.g. the system's pending-request
        // budget is already spent) - the render simply resumes next time the
        // user reopens the app instead, same outcome as not scheduling at all.
        try? BGTaskScheduler.shared.submit(request)
    }

    private static func handle(_ task: BGProcessingTask) {
        let work = Task { @MainActor in
            defer { UserDefaults.standard.removeObject(forKey: pendingKey) }
            guard let id = UserDefaults.standard.string(forKey: pendingKey) else { return }
            let store = Store()
            guard let p = store.project(id) else { return }
            let next = PhaseID.allCases.first { p.phase($0).status != .done } ?? .render
            store.start(id, from: next)
            while store.isRunning(id) {
                if Task.isCancelled { store.cancel(id); break }
                try? await Task.sleep(nanoseconds: 500_000_000)
            }
        }
        task.expirationHandler = { work.cancel() }
        Task {
            _ = await work.result
            task.setTaskCompleted(success: true)
        }
    }
}

import Foundation
import UIKit

/// Where a render's time went. Logged to the project's Live List after every
/// render so speed claims can be checked on the device instead of guessed:
/// it says how many frames the GPU drew, how many fell back to the CPU, and
/// how long each stage took.
struct RenderStats {
    var frames = 0
    var gpuFrames = 0
    var prepareSeconds = 0.0
    var frameSeconds = 0.0
    var muxSeconds = 0.0

    var framesPerSecond: Double { frameSeconds > 0 ? Double(frames) / frameSeconds : 0 }

    var summary: String {
        let engine: String
        if frames == 0 { engine = "no frames" }
        else if gpuFrames == frames { engine = "GPU" }
        else if gpuFrames == 0 { engine = "CPU" }
        else { engine = "GPU \(gpuFrames) / CPU \(frames - gpuFrames)" }
        return String(format: "%d frames on %@ at %.0f fps. Prepare %.1fs, frames %.1fs, mux %.1fs.",
                      frames, engine, framesPerSecond, prepareSeconds, frameSeconds, muxSeconds)
    }
}

extension Duration {
    /// The duration as fractional seconds (for log lines, not arithmetic).
    var seconds: Double {
        Double(components.seconds) + Double(components.attoseconds) / 1e18
    }
}

/// Whether iOS will currently let this app submit GPU work.
///
/// Apps may not use the GPU while in the background (Metal work submitted
/// then is refused). The app keeps rendering in the background through a
/// background task, so the renderer asks this before every frame and draws
/// that frame with Core Graphics on the CPU instead. That is the old, slower
/// path - correct, just not fast - and it is what a render started by
/// `BackgroundRender` (when the app never becomes active) uses throughout.
///
/// Thread-safe: read from the render queue, written from notifications.
final class AppActivity: @unchecked Sendable {
    static let shared = AppActivity()

    private let lock = NSLock()
    private var active = false
    private var started = false

    var isActive: Bool {
        lock.lock(); defer { lock.unlock() }
        return active
    }

    private func set(_ value: Bool) {
        lock.lock(); active = value; lock.unlock()
    }

    /// Call once at launch, on the main actor.
    @MainActor func start() {
        guard !started else { return }
        started = true
        set(UIApplication.shared.applicationState == .active)
        let center = NotificationCenter.default
        center.addObserver(forName: UIApplication.didBecomeActiveNotification, object: nil, queue: nil) { [weak self] _ in
            self?.set(true)
        }
        // willResignActive (not didEnterBackground): the GPU must be left
        // alone from the moment the app stops being frontmost.
        center.addObserver(forName: UIApplication.willResignActiveNotification, object: nil, queue: nil) { [weak self] _ in
            self?.set(false)
        }
    }
}

/// A flag a `withTaskCancellationHandler` can raise and a render queue can
/// poll without touching Swift concurrency.
final class CancelFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var raised = false
    func raise() { lock.lock(); raised = true; lock.unlock() }
    var isRaised: Bool { lock.lock(); defer { lock.unlock() }; return raised }
}

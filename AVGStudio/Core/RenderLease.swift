import Foundation

/// One render at a time, however many videos are in flight.
///
/// The PC app caps how many videos run at once (`max_parallel_jobs`) but
/// still serialises the render itself: "two renders at once is not a goal
/// and must not become possible" (its CLAUDE.md). On a phone that matters
/// more - two simultaneous encodes fight over memory, the GPU and the
/// hardware encoder, and the system may jetsam the app. Writing, images,
/// voice and captions of several videos still overlap freely; only the
/// render phase takes a turn here.
///
/// Waiters are served first-come-first-served. A waiter whose task is
/// cancelled leaves the line immediately (it throws `CancellationError`)
/// instead of holding up a turn it will never use.
actor RenderLease {
    private var held = false
    private var waiters: [(id: UUID, continuation: CheckedContinuation<Void, Error>)] = []

    var isHeld: Bool { held }
    var waitingCount: Int { waiters.count }

    func acquire() async throws {
        try Task.checkCancellation()
        if !held {
            held = true
            return
        }
        let id = UUID()
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                waiters.append((id, continuation))
            }
        } onCancel: {
            // Runs on the cancelling thread, possibly before the waiter is
            // queued; hopping onto the actor serialises it after `acquire`'s
            // own first turn, so the waiter is always in the list by then.
            Task { await self.leaveLine(id) }
        }
    }

    private func leaveLine(_ id: UUID) {
        guard let i = waiters.firstIndex(where: { $0.id == id }) else { return }
        waiters.remove(at: i).continuation.resume(throwing: CancellationError())
    }

    /// Hands the lease straight to the next waiter (it stays held), or frees it.
    func release() {
        if waiters.isEmpty {
            held = false
        } else {
            waiters.removeFirst().continuation.resume()
        }
    }
}

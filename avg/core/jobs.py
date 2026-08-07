"""Which projects are being worked on right now.

Replaces three singular fields that used to carry the whole idea of "the
job" - `app.state.render_thread`, `rendering_project_id` and
`pipeline_cancel_token`. They were read at three start sites and cleared at
two teardowns, and every one of them assumed there could only ever be one.

All three go away together. Leaving one behind as a "current job" pointer
would be exactly the bug this replacement exists to prevent: a second job
would quietly overwrite it and the first would become uncancellable.

Storage and bookkeeping only - no HTTP, no threads started here, no
knowledge of what a job does. `avg/api/app.py` owns starting and stopping;
this owns knowing.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any


@dataclass
class Job:
    """One project's in-flight work.

    `queued` means accepted but not started - the cap was full when it
    arrived. A queued job holds no thread yet, so it is never counted
    against the cap it is waiting on.
    """

    project_id: str
    kind: str
    thread: Any
    cancel_token: Any
    queued: bool = False


class JobRegistry:
    """Every job, keyed by project id, behind one lock."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        # Project ids `promote_next` has just claimed a slot for, whose
        # thread the CALLER has not started yet - see `promote_next`'s own
        # docstring for the gap this closes. Cleared by `remove`, which is
        # the only thing that ever frees the slot it holds.
        self._reserved: set[str] = set()

    def add(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.project_id] = job

    def get(self, project_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(project_id)

    def remove(self, project_id: str) -> None:
        """Forget a job. Absent is not an error - teardown runs in a
        `finally` and must never raise over a job that already went."""
        with self._lock:
            self._jobs.pop(project_id, None)
            self._reserved.discard(project_id)

    def _alive(self, job: Job) -> bool:
        thread = job.thread
        return thread is not None and bool(thread.is_alive())

    def _counts_as_running(self, job: Job) -> bool:
        """A dead thread whose entry has not been cleaned up yet is not
        running - a leaked entry would otherwise consume a slot forever
        (`_alive`). A job `promote_next` has just promoted counts too, even
        though its thread has not been started yet - `job.project_id in
        self._reserved` - because the caller's own `thread.start()` call
        necessarily happens AFTER the lock that promoted it is released, and
        without this, that gap is exactly the window a second, concurrent
        promotion could slip through and exceed the cap."""
        return not job.queued and (self._alive(job) or job.project_id in self._reserved)

    def running(self) -> list[Job]:
        """Jobs that count against the parallel-jobs cap right now - see
        `_counts_as_running`."""
        with self._lock:
            return [j for j in self._jobs.values() if self._counts_as_running(j)]

    def queued(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.queued]

    def count_running(self) -> int:
        return len(self.running())

    def snapshot(self) -> list[dict[str, Any]]:
        """Every job as plain data, for the API layer to serve.

        The screen has to distinguish three states that used to be one - a job
        running, a job waiting for a free slot, and (emitted separately, by the
        Render phase) a job waiting for the render lease. Without this the
        first two are indistinguishable from each other and from a job that
        never started, because a queued job holds no thread and so emits no
        events at all until it is promoted.

        `position` is 1-based within the queue and is `None` for a running job.
        It is derived here rather than stored on `Job`, because a position is a
        fact about the queue at a moment, not a property of the job - storing
        it would mean rewriting every later entry each time one is promoted,
        which is exactly the kind of bookkeeping that drifts.

        Taken under the one lock, so the running set and the queue cannot be
        read from either side of a promotion and disagree.
        """
        with self._lock:
            jobs = list(self._jobs.values())
            queue = [j for j in jobs if j.queued]
            positions = {job.project_id: index for index, job in enumerate(queue, start=1)}
            return [
                {
                    "project_id": job.project_id,
                    "kind": job.kind,
                    "queued": job.queued,
                    "position": positions.get(job.project_id),
                }
                for job in jobs
                if job.queued or self._alive(job) or job.project_id in self._reserved
            ]

    def promote_next(self, cap: int) -> Job | None:
        """Atomically decide the next queued job to start, if there is room.

        Everything `avg.api.app._start_next_queued` used to do - reading
        `count_running()`, reading `queued()`, picking `pending[0]`, flipping
        `job.queued` - happened as separate calls, each taking and releasing
        the lock on its own. That is check-then-act across four steps, called
        from three teardown sites on arbitrary threads: two teardowns landing
        within microseconds of each other could both read the same queued job
        as `pending[0]`, both flip `queued = False`, and the loser's
        `thread.start()` would raise `RuntimeError: threads can only be
        started once` - out of a `finally` block. A thread that has been
        `add`ed but not yet started also reads as not-alive, so it would not
        even count against the cap, letting two teardowns promote two
        different jobs and exceed it.

        This folds the whole decision inside ONE critical section, the same
        "derive it inside the one lock" discipline `snapshot()` already
        follows: count the live threads, look at the queue, and flip
        `queued` on the chosen job's own record, all without releasing the
        lock in between. Only one caller can ever win the flip for a given
        job, because only one caller ever runs this section at a time.

        Returns the `Job` the caller must now `.thread.start()` - deliberately
        NOT started here, since starting a thread while holding this lock
        would serialise every job's startup behind whichever teardown got
        here first, for no reason: nothing about starting a thread needs the
        registry lock, only the accounting that decided to do it does.
        Returns `None` when the cap is already met or the queue is empty.

        The chosen job is also added to `_reserved` before the lock is
        released, so it counts against the cap (`_counts_as_running`) from
        this instant - not from whenever the caller gets around to calling
        `.thread.start()`. Without that, the gap between this method
        returning and the caller's `.start()` call is exactly the window in
        which a SECOND concurrent call here would see the just-promoted job
        as neither queued nor alive, and promote a different one right on
        top of it - the cap would then be exceeded by real, running threads,
        not just racing to a `RuntimeError`.
        """
        with self._lock:
            running = sum(1 for j in self._jobs.values() if self._counts_as_running(j))
            if running >= cap:
                return None
            pending = [j for j in self._jobs.values() if j.queued]
            if not pending:
                return None
            job = pending[0]
            job.queued = False
            self._reserved.add(job.project_id)
            return job

    def drop_if_queued(self, project_id: str) -> bool:
        """Remove a job iff it is still queued, atomically.

        Cancelling a queued job means removing it from the registry outright
        - a queued `/render` job is built with `cancel_token=None`, so giving
        it a token to cancel would do nothing at all, and a queued pipeline
        job would still be promoted and started, only stopping at its first
        phase boundary. Neither is "cancelled" in any sense a user watching
        the queue would recognise.

        That removal has to happen inside the SAME lock `promote_next` uses,
        or the two could race: a promotion could flip `queued` to False and
        start the thread in the gap between this call reading the flag and
        removing the entry, which would silently orphan a now-running job
        with no registry entry left to track, cancel, or count against the
        cap.

        Returns whether a queued job was actually removed, so the caller can
        tell "queued and now dropped" apart from "already running" or "no
        job at all" without a second read racing this one.
        """
        with self._lock:
            job = self._jobs.get(project_id)
            if job is None or not job.queued:
                return False
            del self._jobs[project_id]
            return True

    def is_running(self, project_id: str) -> bool:
        job = self.get(project_id)
        return job is not None and not job.queued and self._alive(job)

    def is_queued(self, project_id: str) -> bool:
        job = self.get(project_id)
        return job is not None and job.queued

    def has_job(self, project_id: str) -> bool:
        """Whether this project has work in flight at all, running or waiting.

        The 409 guards must ask this rather than `is_running`. `add` is keyed
        by project id, so a second submission for a project that is merely
        QUEUED used to pass an `is_running` guard and then overwrite the
        queued entry - the first job's thread was never started, its worker's
        `finally` never ran, and for a take the row existed on disk having
        never executed a phase.
        """
        return self.is_running(project_id) or self.is_queued(project_id)

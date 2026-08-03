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

    def _alive(self, job: Job) -> bool:
        thread = job.thread
        return thread is not None and bool(thread.is_alive())

    def running(self) -> list[Job]:
        """Jobs with a live thread. A dead thread is not running even if its
        entry has not been cleaned up yet - a leaked entry would otherwise
        consume a slot forever."""
        with self._lock:
            return [j for j in self._jobs.values() if not j.queued and self._alive(j)]

    def queued(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.queued]

    def count_running(self) -> int:
        return len(self.running())

    def is_running(self, project_id: str) -> bool:
        job = self.get(project_id)
        return job is not None and not job.queued and self._alive(job)


def max_parallel_jobs(settings_path: Any) -> int:
    """How many jobs may run at once. `1` reproduces this app's original
    single-slot behaviour exactly, which is both the escape hatch when a
    provider starts rate-limiting and the regression test for this change.

    A free function here, not a closure in `avg.api.app`: `avg.api.
    routes_takes` needs the exact same cap and queue-promotion logic for its
    own take-creation job, and `app.py` imports the route modules rather
    than the reverse - a closure there would be unreachable from here.
    """
    from avg.config.settings import load_settings  # noqa: PLC0415
    from avg.core.settings_schema import get_spec  # noqa: PLC0415

    raw = load_settings(settings_path).values.get(
        "max_parallel_jobs", get_spec("max_parallel_jobs").default  # type: ignore[union-attr]
    )
    try:
        return max(1, int(str(raw)))
    except (TypeError, ValueError):
        return 1


def start_next_queued(app_state: Any) -> None:
    """Start the oldest queued job, if there is room.

    Called from every teardown, so a slot freeing always moves the queue.
    First started, first served - no priority, no reordering.
    """
    registry: JobRegistry = app_state.jobs
    if registry.count_running() >= max_parallel_jobs(app_state.settings_path):
        return
    pending = registry.queued()
    if not pending:
        return
    job = pending[0]
    job.queued = False
    job.thread.start()

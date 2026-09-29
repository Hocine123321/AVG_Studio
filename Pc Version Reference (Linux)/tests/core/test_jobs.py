"""The job registry that replaces the three singular `app.state` fields."""

from __future__ import annotations

import threading

from avg.core.jobs import Job, JobRegistry


class _Thread:
    def __init__(self, alive: bool = True) -> None:
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


def _job(project_id: str, *, queued: bool = False, alive: bool = True) -> Job:
    return Job(
        project_id=project_id,
        kind="pipeline",
        thread=_Thread(alive),
        cancel_token=None,
        queued=queued,
    )


def test_a_registry_starts_empty():
    registry = JobRegistry()
    assert registry.running() == []
    assert registry.count_running() == 0
    assert registry.get("nope") is None


def test_the_same_project_twice_is_visible_as_already_running():
    """Two runs of one project would fight over the same folder and the same
    project.json - that stays a 409 at any cap."""
    registry = JobRegistry()
    registry.add(_job("alpha"))
    assert registry.is_running("alpha") is True
    assert registry.is_running("beta") is False


def test_a_finished_thread_no_longer_counts_as_running():
    """A leaked entry would permanently consume a slot."""
    registry = JobRegistry()
    registry.add(_job("alpha", alive=False))
    assert registry.count_running() == 0
    assert registry.is_running("alpha") is False


def test_queued_jobs_are_not_counted_against_the_cap():
    registry = JobRegistry()
    registry.add(_job("alpha"))
    registry.add(_job("beta", queued=True))
    assert registry.count_running() == 1
    assert [j.project_id for j in registry.queued()] == ["beta"]


def test_removing_a_job_frees_its_slot():
    registry = JobRegistry()
    registry.add(_job("alpha"))
    registry.remove("alpha")
    assert registry.count_running() == 0
    assert registry.get("alpha") is None


def test_removing_something_absent_is_not_an_error():
    """Teardown runs in a `finally`; it must not raise on a job that already
    removed itself."""
    JobRegistry().remove("never-existed")


def test_a_snapshot_numbers_the_queue_and_leaves_running_jobs_unnumbered():
    """The screen has to tell "waiting for a slot" from "running", and say
    which place in the queue - a queued job emits no events of its own, so a
    snapshot is the only way it can be shown at all."""
    registry = JobRegistry()
    registry.add(Job(project_id="running", kind="pipeline", thread=_Thread(True), cancel_token=None))
    registry.add(Job(project_id="first", kind="pipeline", thread=None, cancel_token=None, queued=True))
    registry.add(Job(project_id="second", kind="pipeline", thread=None, cancel_token=None, queued=True))

    rows = {row["project_id"]: row for row in registry.snapshot()}

    assert rows["running"]["queued"] is False
    assert rows["running"]["position"] is None
    assert rows["first"]["position"] == 1
    assert rows["second"]["position"] == 2


def test_a_snapshot_omits_a_job_whose_thread_has_died():
    """A finished job that has not been cleaned up yet is not in flight, and
    showing it would leave a phantom on screen after the work was done."""
    registry = JobRegistry()
    registry.add(Job(project_id="done", kind="pipeline", thread=_Thread(False), cancel_token=None))

    assert registry.snapshot() == []


# ── promote_next ─────────────────────────────────────────────────────────


def test_promote_next_returns_none_when_the_cap_is_already_met():
    registry = JobRegistry()
    registry.add(_job("running"))
    registry.add(_job("queued", queued=True))
    assert registry.promote_next(cap=1) is None
    assert registry.is_queued("queued") is True


def test_promote_next_returns_none_when_nothing_is_queued():
    registry = JobRegistry()
    assert registry.promote_next(cap=2) is None


def test_promote_next_flips_the_oldest_queued_job_and_returns_it():
    registry = JobRegistry()
    registry.add(_job("first", queued=True))
    registry.add(_job("second", queued=True))

    job = registry.promote_next(cap=1)

    assert job is not None
    assert job.project_id == "first"
    assert job.queued is False
    # Not started here - see `promote_next`'s own docstring for why that is
    # deliberately the caller's job, not this method's.
    assert registry.is_queued("second") is True
    assert registry.is_queued("first") is False


def test_promote_next_under_racing_teardowns_promotes_exactly_once_and_never_raises():
    """The regression this exists for: `avg.api.app._start_next_queued` used
    to read `count_running()`, read `queued()`, pick `pending[0]` and flip
    `job.queued = False` as four separate, unlocked steps, called from three
    teardown sites on arbitrary threads. Two teardowns landing within
    microseconds of each other could both pick the SAME queued job and both
    call `thread.start()` on it - the second call raises `RuntimeError:
    threads can only be started once`, out of a `finally` block. The cap
    could also be exceeded: a thread that has been `add`ed but not yet
    `start`ed reads as not-alive, so it is invisible to `count_running()`,
    letting two racing teardowns each promote a DIFFERENT job and together
    exceed the cap.

    Forced with a real `threading.Barrier` so the two calls genuinely
    overlap - two sequential calls would pass regardless of whether the
    decision is locked, and prove nothing about the race. Real `Thread`
    objects (not the `_Thread` test double) are used for the two queued
    jobs so a double-`start()` bug would actually raise the real
    `RuntimeError`, not a fake one a test double happens not to model.

    With `cap=1` and two queued jobs, exactly one racer must win the single
    slot (the cap is respected) and neither racer may raise - `promote_next`
    folds "is there room" and "which job" into the one lock, so only one
    caller can ever observe a given job as still queued.
    """
    started_first = threading.Event()
    started_second = threading.Event()
    real_first = threading.Thread(target=started_first.set)
    real_second = threading.Thread(target=started_second.set)

    registry = JobRegistry()
    registry.add(Job(project_id="first", kind="pipeline", thread=real_first, cancel_token=None, queued=True))
    registry.add(Job(project_id="second", kind="pipeline", thread=real_second, cancel_token=None, queued=True))

    barrier = threading.Barrier(2)
    lock = threading.Lock()
    results: list[Job | None] = []
    errors: list[BaseException] = []

    def _teardown_race() -> None:
        try:
            barrier.wait(timeout=5)
            job = registry.promote_next(cap=1)
            if job is not None:
                # Exactly what `_start_next_queued` does with the job
                # `promote_next` hands back - a double-promotion bug would
                # make this raise `RuntimeError` on the second racer.
                job.thread.start()
            with lock:
                results.append(job)
        except BaseException as exc:  # noqa: BLE001 - captured, not swallowed
            with lock:
                errors.append(exc)

    racer_a = threading.Thread(target=_teardown_race)
    racer_b = threading.Thread(target=_teardown_race)
    racer_a.start()
    racer_b.start()
    racer_a.join(timeout=5)
    racer_b.join(timeout=5)

    assert not racer_a.is_alive() and not racer_b.is_alive()
    assert not errors, f"a racing teardown raised: {errors!r}"

    winners = [r for r in results if r is not None]
    losers = [r for r in results if r is None]
    assert len(winners) == 1, f"cap=1 must promote exactly one job, got {results!r}"
    assert len(losers) == 1

    # The winner actually started; the loser is still sitting in the queue,
    # untouched, ready for the next slot to free up.
    assert started_first.is_set() != started_second.is_set()
    still_queued = registry.queued()
    assert [j.project_id for j in still_queued] == [
        "first" if not started_first.is_set() else "second"
    ]

"""The job registry that replaces the three singular `app.state` fields."""

from __future__ import annotations

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

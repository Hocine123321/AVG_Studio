from contextlib import suppress
from enum import Enum
from typing import Any

class ErrorKind(str, Enum):
    auth = "auth"; quota = "quota"; billing = "billing"; rate_limit = "rate_limit"
    model_missing = "model_missing"; content_filter = "content_filter"
    network = "network"; timeout = "timeout"; resource = "resource"
    dependency = "dependency"; bug = "bug"

class AvgError(Exception):
    def __init__(self, kind: ErrorKind, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.kind, self.message, self.detail = kind, message, detail


def report_degradation(ctx: Any, summary: str) -> None:
    """Record that a phase produced something worse than it was asked for.

    Takes the phase CONTEXT and reads the store off it, so no phase reaches
    into `app.state` - the layering rule that keeps every phase testable
    without an app. A context without a store (every unit test builds one)
    does nothing, which is correct: the degradation is real, the recording of
    it is optional.

    Also written to the take's log, at WARN, so the run's own record carries
    it in sequence with the calls that caused it. The two are deliberately
    both: the log says when it happened during this run, the store says it
    happened at all, across runs, on a screen the user already checks.
    """
    log = getattr(ctx, "log", None)
    if log is not None:
        with suppress(Exception):
            log.warn(summary)

    store = getattr(ctx, "errors", None)
    if store is None:
        return
    project = getattr(ctx, "project", None)
    context = {}
    if project is not None:
        context = {
            "project_id": str(getattr(project, "id", "")),
            "take_id": str(getattr(project, "current_take_id", "") or ""),
        }
    with suppress(Exception):
        store.degraded(source="pipeline", summary=summary, context=context)

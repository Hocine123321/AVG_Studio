"""avg/core/events.py's ActivityEvent - the one event type every
long-running operation emits over /ws/events for the status pill
(Slice 17). See the design spec's §2-3 for the contract this enforces.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from avg.core.events import ActivityEvent


def test_a_running_event_needs_no_detail_or_percent():
    event = ActivityEvent(activity_id="render:p1", label="Rendering", state="running", sticky=False)
    assert event.type == "activity"
    assert event.percent is None
    assert event.detail is None


def test_percent_and_detail_can_be_carried():
    event = ActivityEvent(
        activity_id="render:p1",
        label="Rendering",
        state="running",
        percent=42,
        detail="phase 3 of 5",
        sticky=False,
    )
    assert event.percent == 42
    assert event.detail == "phase 3 of 5"


def test_state_is_restricted_to_the_four_values():
    with pytest.raises(ValidationError):
        ActivityEvent(activity_id="x", label="x", state="queued", sticky=False)  # type: ignore[arg-type]


def test_sticky_is_required_not_derived():
    """`sticky` has no default - the whole point (spec §2) is that it is
    something the EMITTER decides, never a client-side rule like "terminal
    states are sticky". A required field with no default is what forces
    every call site to make that decision explicitly."""
    with pytest.raises(ValidationError):
        ActivityEvent(activity_id="x", label="x", state="done")  # type: ignore[call-arg]


def test_a_terminal_state_can_still_be_not_sticky():
    """A Live List refresh finishing is `done` but never sticky (spec §3) -
    confirms the model does not couple `state` to `sticky` in either
    direction."""
    event = ActivityEvent(activity_id="live-list-refresh", label="Refreshing", state="done", sticky=False)
    assert event.state == "done"
    assert event.sticky is False


def test_a_running_state_can_be_sticky():
    """"a job that was queued behind another and has now started" - spec
    §2's own example of why sticky cannot be derived from state."""
    event = ActivityEvent(activity_id="pipeline:p2", label="Started", state="running", sticky=True)
    assert event.sticky is True

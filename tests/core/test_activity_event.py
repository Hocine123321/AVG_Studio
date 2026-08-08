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


# ── The drift test (spec §6) ───────────────────────────────────────────────
#
# "Every event in §3's sticky list is emitted with sticky: true, and every
# event in the not-sticky list with sticky: false. A drift test, because
# the two lists are the whole feature and nothing else enforces them."
#
# Scans the THREE real emit-site modules (rather than re-testing the model
# in isolation, which Task 1's tests above already do) and checks every
# actual `ActivityEvent(...)` call's `sticky=` literal against the
# classification the design spec's §3 lays out. Three deliberate
# exceptions to the "obvious" per-state default exist and are named below,
# each pointing at the spec line that justifies it - anyone changing one
# without updating this table has changed the classification, which is
# exactly what this test exists to catch.

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCANNED_FILES = [
    _REPO_ROOT / "avg" / "pipeline" / "runner.py",
    _REPO_ROOT / "avg" / "media" / "engine.py",
    _REPO_ROOT / "avg" / "api" / "routes_settings.py",
]

# activity_id -> the ONE non-default rule for that activity's terminal
# events (state in {"done", "failed", "cancelled"}). Anything not listed
# here follows the plain default: cancelled/failed sticky, done sticky,
# running never sticky - the ordinary "pipeline/render failed or finished"
# case spec §3 names directly.
_TERMINAL_STICKY_OVERRIDE = {
    # Spec §3: "a Live List refresh" is listed as not-sticky with no
    # success/failure qualifier - unconditionally not sticky either way.
    "live-list-refresh": {"done": False, "failed": False},
    # Not in either of spec §3's lists by name (see this slice's plan,
    # Task 3): treated as "an opinion becoming available", not sticky on
    # success; sticky on failure because that failure is the same
    # provider/API-key failure class spec §3 DOES name.
    "live-list-suggest": {"done": False, "failed": True},
}
# provider-probe's "done" sticky value is `not all_passed`, computed at
# runtime, not a literal - it cannot be checked by source-scanning. Its
# classification (pass -> not sticky, fail -> sticky, per spec §3's
# "a provider probe where everything passed" vs the provider/API-key
# failure case) is instead covered behaviorally by
# tests/api/test_model_cache_routes.py's
# test_a_build_with_a_failed_model_reports_a_sticky_activity_event and
# test_a_build_where_everything_passed_reports_a_not_sticky_activity_event.
_DYNAMIC_STICKY_SITES = {("provider-probe", "done")}

_CALL_RE = re.compile(r"ActivityEvent\((?P<body>(?:[^()]|\([^()]*\))*)\)", re.DOTALL)
_STATE_RE = re.compile(r'state="(\w+)"')
_STICKY_RE = re.compile(r"sticky=(True|False|not all_passed)")
_ACTIVITY_ID_RE = re.compile(r'activity_id=("(?:[^"]*)"|activity_id)')


def _scanned_calls():
    for path in _SCANNED_FILES:
        text = path.read_text(encoding="utf-8")
        for match in _CALL_RE.finditer(text):
            body = match.group("body")
            state_m = _STATE_RE.search(body)
            sticky_m = _STICKY_RE.search(body)
            id_m = _ACTIVITY_ID_RE.search(body)
            assert state_m and sticky_m and id_m, (
                f"{path.name}: an ActivityEvent(...) call did not match the "
                f"expected shape - update this test's regexes:\n{body}"
            )
            activity_id_literal = id_m.group(1).strip('"')
            yield path.name, activity_id_literal, state_m.group(1), sticky_m.group(1)


def test_every_real_activity_event_call_site_matches_spec_3s_classification():
    calls = list(_scanned_calls())
    assert len(calls) >= 20, "fewer ActivityEvent(...) calls found than Tasks 2-3 added - did the regex break?"

    checked = 0
    for filename, activity_id, state, sticky_literal in calls:
        if (activity_id, state) in _DYNAMIC_STICKY_SITES:
            continue  # covered behaviorally - see the comment above.

        if state == "running":
            expected = False  # spec §3: routine progress is never sticky.
        else:
            override = _TERMINAL_STICKY_OVERRIDE.get(activity_id, {})
            expected = override.get(state, True)  # the plain default: terminal = sticky.

        actual = sticky_literal == "True"
        assert actual is expected, (
            f"{filename}: activity_id={activity_id!r} state={state!r} "
            f"emits sticky={sticky_literal}, expected {expected} per spec §3"
        )
        checked += 1

    # Every non-dynamic call must actually have been checked - a silently
    # skipped site would defeat the whole test.
    assert checked == len(calls) - len(_DYNAMIC_STICKY_SITES & {(a, s) for _, a, s, _ in calls})

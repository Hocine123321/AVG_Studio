"""The release gate orchestrates other tools, so these tests pin its decisions
(what counts as a pass, what stops the run) with the tools themselves faked.
The secrets tripwire is the one step exercised for real."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "verify_release", Path(__file__).resolve().parents[2] / "tools" / "verify_release.py"
)
assert _SPEC and _SPEC.loader
verify = importlib.util.module_from_spec(_SPEC)
sys.modules["verify_release"] = verify  # a dataclass resolves its own module by name
_SPEC.loader.exec_module(verify)


# ── secrets tripwire ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    ["secrets.dat", "config/.env", ".env.local", "deploy/key.pem", "config/secrets.json", "a/apikey.txt"],
)
def test_key_material_paths_are_flagged(path):
    assert verify.suspicious_paths([path]) == [path]


@pytest.mark.parametrize(
    "path",
    ["avg/config/secrets.py", "tests/config/test_secrets.py", "docs/secrets.md", "web/src/secrets.tsx", "README.md"],
)
def test_source_and_docs_named_secret_are_not_flagged(path):
    assert verify.suspicious_paths([path]) == []


def test_the_real_repo_tracks_nothing_secrets_shaped():
    result = verify.step_secrets()
    assert result.ok, result.detail


# ── baseline ─────────────────────────────────────────────────────────────


def test_a_missing_baseline_file_means_zero_tolerance(tmp_path):
    assert verify.load_baseline(tmp_path / "nope.json") == {"pytest_failures": 0, "mypy_errors": 0}


def test_baseline_is_read_from_the_file(tmp_path):
    path = tmp_path / "b.json"
    path.write_text(json.dumps({"pytest_failures": 2, "mypy_errors": 9}), encoding="utf-8")
    assert verify.load_baseline(path) == {"pytest_failures": 2, "mypy_errors": 9}


def test_the_committed_baseline_is_valid():
    baseline = verify.load_baseline()
    assert baseline["pytest_failures"] >= 0
    assert baseline["mypy_errors"] >= 0


# ── tests step ───────────────────────────────────────────────────────────


def _stream(code: int, out: str):
    return lambda cmd, cwd: (code, out)


@pytest.mark.parametrize(
    ("code", "out", "allowed", "ok"),
    [
        (0, "1169 passed", 0, True),
        (1, "2 failed, 100 passed", 0, False),
        (1, "2 failed, 100 passed", 2, True),
        (1, "1 failed, 1 error, 100 passed", 5, False),  # an error is never "within baseline"
        (2, "Interrupted", 5, False),
    ],
)
def test_tests_step_decisions(monkeypatch, code, out, allowed, ok):
    monkeypatch.setattr(verify, "_run_streaming", _stream(code, out))
    assert verify.step_tests(allowed).ok is ok


# ── mypy step ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("code", "out", "baseline", "ok"),
    [
        (0, "Success: no issues found", 704, True),
        (1, "Found 704 errors in 23 files", 704, True),
        (1, "Found 705 errors in 23 files", 704, False),
        (1, "Found 600 errors in 20 files", 704, True),
        (2, "mypy: can't read config", 704, False),  # no parseable count is a failure, not a pass
    ],
)
def test_mypy_step_decisions(monkeypatch, code, out, baseline, ok):
    monkeypatch.setattr(verify, "_run_streaming", _stream(code, out))
    assert verify.step_mypy(baseline).ok is ok


def test_a_lower_mypy_count_asks_for_the_baseline_to_ratchet(monkeypatch):
    monkeypatch.setattr(verify, "_run_streaming", _stream(1, "Found 600 errors in 20 files"))
    assert "update-baseline" in verify.step_mypy(704).detail


# ── deep step ────────────────────────────────────────────────────────────


def _stage(key, *checks):
    return SimpleNamespace(key=key, checks=[SimpleNamespace(**c) for c in checks])


def test_failing_rows_reports_required_red_rows_only():
    stages = [
        _stage(
            "deep",
            {"name": "A", "ok": False, "required": True, "detail": "broken"},
            {"name": "B", "ok": False, "required": False, "detail": "informative"},
            {"name": "C", "ok": True, "required": True, "detail": ""},
        )
    ]
    assert verify.failing_rows(stages) == ["deep/A: broken"]


@pytest.mark.parametrize("name", ["Provider keys", "pywebview"])
def test_exempt_rows_never_gate_a_release(name):
    stages = [_stage("x", {"name": name, "ok": False, "required": True, "detail": "red"})]
    assert verify.failing_rows(stages) == []


def test_every_exemption_states_its_reason():
    assert all(reason.strip() for reason in verify._EXEMPT_ROWS.values())


# ── driver ───────────────────────────────────────────────────────────────


def _fake_steps(monkeypatch, outcomes: dict[str, bool]):
    ran: list[str] = []

    def make(name):
        def step(*_args):
            ran.append(name)
            return verify.StepResult(name, outcomes.get(name, True), "x")

        return step

    monkeypatch.setattr(verify, "step_secrets", make("secrets"))
    monkeypatch.setattr(verify, "step_tests", make("tests"))
    monkeypatch.setattr(verify, "step_mypy", make("mypy"))
    monkeypatch.setattr(verify, "step_lint", make("lint"))
    monkeypatch.setattr(verify, "step_deep", make("deep"))
    return ran


def test_a_full_pass_exits_zero_and_runs_cheapest_first(monkeypatch):
    ran = _fake_steps(monkeypatch, {})
    assert verify.main([]) == 0
    assert ran == ["secrets", "tests", "mypy", "lint", "deep"]


def test_the_first_failure_stops_the_run(monkeypatch):
    ran = _fake_steps(monkeypatch, {"tests": False})
    assert verify.main([]) == 1
    assert ran == ["secrets", "tests"]  # mypy/lint/deep never ran


def test_keep_going_runs_every_step_and_still_fails(monkeypatch):
    ran = _fake_steps(monkeypatch, {"tests": False})
    assert verify.main(["--keep-going"]) == 1
    assert ran == ["secrets", "tests", "mypy", "lint", "deep"]


def test_skip_flags_skip_and_warn(monkeypatch, capsys):
    ran = _fake_steps(monkeypatch, {})
    assert verify.main(["--skip-tests", "--skip-lint"]) == 0
    assert ran == ["secrets", "mypy", "deep"]
    assert "not a full verification" in capsys.readouterr().out


def test_the_secrets_step_cannot_be_skipped(monkeypatch):
    ran = _fake_steps(monkeypatch, {})
    verify.main(["--skip-tests", "--skip-mypy", "--skip-lint", "--skip-deep"])
    assert ran == ["secrets"]

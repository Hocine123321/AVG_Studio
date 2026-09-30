#!/usr/bin/env python3
"""Release verification gate - one command that says whether this tree is fit to ship.

It invents no new checking. It runs the tools this project already trusts,
cheapest first, stops at the first failure, and shows each tool's own output
untouched so a failure is exactly as debuggable as running that tool by hand:

    1. secrets  - no secrets-shaped file is tracked by git      (instant)
    2. tests    - pytest, against the documented baseline        (seconds)
    3. mypy     - error count, against the documented baseline
    4. lint     - oxlint over web/
    5. deep     - avg.core.health's Deep Check, on throwaway data

Usage:
    tools/verify_release.py                      all five steps
    tools/verify_release.py --skip-tests --skip-lint
    tools/verify_release.py --keep-going         run every step, report all failures
    tools/verify_release.py --update-baseline    record today's numbers (after a cleanup)

The skip flags are for fast iteration on one piece, never for a real release.
The secrets step has no flag: it is not optional.

Baselines live in `tools/verify_baseline.json`, not in prose. The project's
rule is "zero regressions against the documented baseline", not "zero
failures" (mypy runs strict over a codebase that predates it), and a rule
that lives in a paragraph gets enforced by memory. A number in a file gets
enforced by this script. A count LOWER than the baseline is never a failure -
it prints a reminder to ratchet the number down instead.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPO / "tools" / "verify_baseline.json"

# Source and docs legitimately have "secret" in their names
# (`avg/config/secrets.py`, `tests/config/test_secrets.py`). A file is only
# suspicious when its name says secret AND it is not one of these.
_CODE_SUFFIXES = {".py", ".md", ".ts", ".tsx", ".js", ".css"}
_SECRET_NAME = re.compile(r"secret|apikey|api_key", re.IGNORECASE)
_KEY_MATERIAL_SUFFIXES = {".dat", ".pem", ".key", ".p12", ".pfx"}

# Red rows that must not gate a release, each with the reason it is exempt.
# Exemptions are printed when they apply - a check being skipped is something
# the operator should be able to see, not infer.
_EXEMPT_ROWS = {
    # Describes the operator's ACCOUNTS, not the build: a release machine with
    # no provider keys configured still builds a perfectly good release.
    "Provider keys": "an account property of this machine, not of the build",
    # The `gui` extra (pywebview + PySide6, ~300 MB) is kept out of dev venvs
    # on purpose (see pyproject.toml) and is installed by the Flatpak manifest
    # itself, so this venv's copy says nothing about the shipped package.
    "pywebview": "lives in the `gui` extra, installed by the package build rather than the dev venv",
}


@dataclass(frozen=True)
class StepResult:
    name: str
    ok: bool
    detail: str = ""


# ── baseline ─────────────────────────────────────────────────────────────


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"pytest_failures": 0, "mypy_errors": 0}
    return {
        "pytest_failures": int(data.get("pytest_failures", 0)),
        "mypy_errors": int(data.get("mypy_errors", 0)),
    }


# ── step 1: secrets tripwire ─────────────────────────────────────────────


def suspicious_paths(paths: list[str]) -> list[str]:
    """Tracked paths whose name looks like key material. A tripwire, not a
    remediation: there is nothing to remediate today, this keeps it true."""
    hits: list[str] = []
    for raw in paths:
        p = PurePosixPath(raw)
        name, suffix = p.name.lower(), p.suffix.lower()
        if name.startswith(".env") or suffix in _KEY_MATERIAL_SUFFIXES:
            hits.append(raw)
        elif _SECRET_NAME.search(name) and suffix not in _CODE_SUFFIXES:
            hits.append(raw)
    return hits


def step_secrets() -> StepResult:
    result = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return StepResult("secrets", False, f"git ls-files failed: {result.stderr.strip()}")
    hits = suspicious_paths(result.stdout.splitlines())
    if hits:
        return StepResult("secrets", False, "tracked file(s) that look like secrets: " + ", ".join(hits))
    return StepResult("secrets", True, "no secrets-shaped file is tracked")


# ── step 2: tests ────────────────────────────────────────────────────────


def _run_streaming(cmd: list[str], cwd: Path) -> tuple[int, str]:
    """Run `cmd`, echoing its output live AND returning it - the tool's own
    words reach the terminal untouched, and we still get to read them."""
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    assert proc.stdout is not None
    lines: list[str] = []
    for line in proc.stdout:
        sys.stdout.write(line)
        lines.append(line)
    return proc.wait(), "".join(lines)


def _count(pattern: str, text: str) -> int:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else 0


def step_tests(allowed_failures: int) -> StepResult:
    code, out = _run_streaming(
        [sys.executable, "-m", "pytest", "tests", "-q", "--no-header", "-p", "no:cacheprovider"], REPO
    )
    failed, errors = _count(r"(\d+) failed", out), _count(r"(\d+) error", out)
    if code == 0:
        return StepResult("tests", True, "all tests passed")
    if code == 1 and errors == 0 and failed <= allowed_failures:
        return StepResult("tests", True, f"{failed} failure(s), within the documented baseline of {allowed_failures}")
    if code == 1:
        return StepResult(
            "tests", False, f"{failed} failure(s) and {errors} error(s); the documented baseline allows {allowed_failures}"
        )
    return StepResult("tests", False, f"pytest exited {code} (interrupted or misconfigured, not a test failure)")


# ── step 3: mypy ─────────────────────────────────────────────────────────


def step_mypy(baseline_errors: int) -> StepResult:
    code, out = _run_streaming([sys.executable, "-m", "mypy"], REPO)
    if code == 0:
        return StepResult("mypy", True, "no type errors")
    found = _count(r"Found (\d+) error", out)
    if found == 0:
        return StepResult("mypy", False, f"mypy exited {code} without a parseable error count")
    if found > baseline_errors:
        return StepResult("mypy", False, f"{found} errors, {found - baseline_errors} more than the baseline of {baseline_errors}")
    note = "" if found == baseline_errors else f" - {baseline_errors - found} fewer than baseline: run --update-baseline"
    return StepResult("mypy", True, f"{found} errors, within the baseline of {baseline_errors}{note}")


# ── step 4: lint ─────────────────────────────────────────────────────────


def step_lint() -> StepResult:
    web = REPO / "web"
    if not (web / "node_modules").is_dir():
        # Self-configuring, per this project's rule for its own tooling: a
        # missing install is not a reason to make the operator go run a
        # command by hand. Said out loud, so it is never a silent surprise.
        print("web/node_modules is missing - running `npm ci` first.", flush=True)
        code, _ = _run_streaming(["npm", "ci"], web)
        if code != 0:
            return StepResult("lint", False, f"`npm ci` failed ({code})")
    code, _ = _run_streaming(["npm", "run", "lint"], web)
    return StepResult("lint", code == 0, "oxlint is clean" if code == 0 else f"oxlint exited {code}")


# ── step 5: deep check ───────────────────────────────────────────────────


def failing_rows(stages: list) -> list[str]:  # type: ignore[type-arg]
    """Every `required` row that is red, minus the exempt ones
    (`_EXEMPT_ROWS`). Non-required rows are informative by definition
    (`Component.required`)."""
    bad: list[str] = []
    for stage in stages:
        for check in stage.checks:
            if check.ok or not check.required or check.name in _EXEMPT_ROWS:
                continue
            bad.append(f"{stage.key}/{check.name}: {check.detail}")
    return bad


def step_deep() -> StepResult:
    sys.path.insert(0, str(REPO))
    from avg.core import health  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        stages = health.report(
            deep=True,
            output_root=root / "library",
            settings_path=root / "settings.json",
            channels_dir=root / "channels",
        )
    for stage in stages:
        for check in stage.checks:
            exempt = f"  (exempt: {_EXEMPT_ROWS[check.name]})" if not check.ok and check.name in _EXEMPT_ROWS else ""
            print(f"  [{'ok' if check.ok else 'RED'}] {stage.key}: {check.name} - {check.detail}{exempt}")
    bad = failing_rows(stages)
    if bad:
        return StepResult("deep", False, "; ".join(bad))
    return StepResult("deep", True, "every required check is green")


# ── driver ───────────────────────────────────────────────────────────────


def update_baseline(path: Path = BASELINE_PATH) -> None:
    """Record today's numbers. For after a deliberate cleanup - never to make
    a red run green."""
    _, test_out = _run_streaming(
        [sys.executable, "-m", "pytest", "tests", "-q", "--no-header", "-p", "no:cacheprovider"], REPO
    )
    _, mypy_out = _run_streaming([sys.executable, "-m", "mypy"], REPO)
    data = {"pytest_failures": _count(r"(\d+) failed", test_out), "mypy_errors": _count(r"Found (\d+) error", mypy_out)}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Baseline written to {path}: {data}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Release verification gate.")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--skip-mypy", action="store_true")
    ap.add_argument("--skip-lint", action="store_true")
    ap.add_argument("--skip-deep", action="store_true")
    ap.add_argument("--keep-going", action="store_true", help="run every step even after a failure")
    ap.add_argument("--update-baseline", action="store_true", help="record today's numbers and exit")
    args = ap.parse_args(argv)

    if args.update_baseline:
        update_baseline()
        return 0

    baseline = load_baseline()
    steps: list[tuple[str, Callable[[], StepResult]]] = [("secrets", step_secrets)]  # never skippable
    if not args.skip_tests:
        steps.append(("tests", lambda: step_tests(baseline["pytest_failures"])))
    if not args.skip_mypy:
        steps.append(("mypy", lambda: step_mypy(baseline["mypy_errors"])))
    if not args.skip_lint:
        steps.append(("lint", step_lint))
    if not args.skip_deep:
        steps.append(("deep", step_deep))

    results: list[StepResult] = []
    for name, run in steps:
        print(f"\n=== verify: {name} ===", flush=True)
        result = run()
        results.append(result)
        print(f"--- {name}: {'PASS' if result.ok else 'FAIL'} - {result.detail}", flush=True)
        if not result.ok and not args.keep_going:
            break

    print("\n=== verify: summary ===")
    for r in results:
        print(f"  {'PASS' if r.ok else 'FAIL'}  {r.name}: {r.detail}")
    skipped = [n for n, f in (("tests", args.skip_tests), ("mypy", args.skip_mypy), ("lint", args.skip_lint), ("deep", args.skip_deep)) if f]
    if skipped:
        print(f"  WARNING: skipped {', '.join(skipped)} - not a full verification.")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

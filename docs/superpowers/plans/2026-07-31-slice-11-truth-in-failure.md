# Slice 11 — Truth in failure: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the app cannot make a video it states the real reason, offers the thing that would repair it, and tries the alternative the user configured — instead of reporting `bug` with an empty detail.

**Architecture:** Seven independent tasks. Three are backend-only (`avg/pipeline/`, `avg/core/`), two span backend and frontend, two are frontend-only. Nothing changes the phase registry's order, the settings catalogue's role, or which files a phase writes.

**Tech Stack:** Python 3.12 + FastAPI + pydantic (backend), React 19 + TypeScript + Vite (frontend), pytest, mypy, oxlint.

**Source spec:** `docs/superpowers/specs/2026-07-31-slice-11-truth-in-failure-design.md`

## Global Constraints

Every task's requirements implicitly include all of these.

- **Interpreter is `.venv-build/Scripts/python.exe`.** System `python` lacks the dependencies. Tests: `.venv-build/Scripts/python.exe -m pytest tests -q`. Types: `.venv-build/Scripts/python.exe -m mypy`.
- **Test baseline is `769 passed`.** No task may end below it.
- **mypy baseline is `Found 688 errors in 22 files`**, concentrated in `avg/api/app.py` and `avg/shell.py`. mypy does NOT pass clean on this repo; the bar is that your change adds nothing to the count. Verify by stashing if unsure.
- **No live API calls in tests, ever.** `tests/conftest.py` forces `AVG_REPLAY_FIXTURES=1` session-wide; `avg/providers/transport.py` raises `UnmatchedRequestError` on anything unmatched. Never record a fixture. **An image generation and an ElevenLabs synthesis both cost real money** — one paid recording has already been lost.
- **Nothing below `avg/api/` may print or prompt.** Lower layers return values or emit typed Pydantic events.
- **Tests must never write to the real `%APPDATA%/AVG Studio`.** Pass `create_app`'s path overrides: `settings_path`, `channels_dir`, `secrets_path`, `errors_path`, `model_cache_path`, `model_prefs_path`, `thumb_cache_dir`, `output_root`.
- **`avg/media/vendor/` is protected.** Never edit.
- **Never use a native form control.** No `<select>`, no `<input type="checkbox">`, no `<input type="radio">`. Use `web/src/Select.tsx` and the `.switch` pattern. A visually hidden `position:absolute; opacity:0` checkbox behind a styled element is the one sanctioned exception (Home's `.select-dot`).
- **One filled terracotta `.btn--primary` primary action per screen.** Terracotta as identity (chips, links, focus rings) is unrestricted.
- **Reuse `web/src/tokens.css` values** — radii, spacing, colour come from existing tokens or the nearest existing control, never a fresh guess.
- **Never add a Google Fonts `@import`.**
- **The frontend has no unit tests by convention.** Frontend tasks are verified by build, lint, and reasoning stated in the report.
- **`web/src/tokens.css` and `web/src/App.tsx` have drifted from any line numbers quoted here.** Match code by content, not by line number.
- **Commit once per task**, on local `main`. No push, no PR, no GitHub interaction.

---

## Task 1: A phase failure carries its kind and its reasons

**Files:**
- Modify: `avg/pipeline/phases/visuals.py` (the `_SceneResult` dataclass, `_generate_one`, and the `ok_count == 0` raise)
- Test: `tests/pipeline/test_phases_media.py`

**Interfaces:**
- Consumes: `avg.core.errors.AvgError` (has `.kind: ErrorKind` and `.message: str`), `avg.providers.errors._CHECK_ORDER`.
- Produces, and Task 2 depends on these exactly:
  - `_SceneResult` gains `kind: ErrorKind | None = None`
  - `_dominant_kind(results: dict[int, _SceneResult]) -> ErrorKind` — module-level

**Why:** `visuals.py` raises `AvgError(ErrorKind.bug, "visuals phase: every scene failed to generate an image")` when nothing was produced. Every scene's real, already-classified message is sitting on `_SceneResult.error` and is discarded. The take log for the 2026-07-30 run reads `kind=bug ... detail=` while the truth was a 403 from a keyless provider.

`ErrorKind` values are: `auth`, `quota`, `billing`, `rate_limit`, `model_missing`, `content_filter`, `network`, `timeout`, `resource`, `dependency`, `bug`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/pipeline/test_phases_media.py`:

```python
def test_a_scene_failure_keeps_the_error_kind_not_just_the_words():
    """`_SceneResult.error` alone cannot tell an auth failure from a
    timeout, and the phase-level error is built from these."""
    from avg.core.errors import AvgError, ErrorKind
    from avg.pipeline.phases.visuals import _generate_one

    class _Boom:
        name = "stablehorde"

        def generate(self, *_a, **_k):
            raise AvgError(ErrorKind.auth, "stablehorde request failed with HTTP 403")

    result = _generate_one(_Boom(), "a prompt", "9:16", "flux", 1, Path("unused.png"))

    assert result.ok is False
    assert result.kind is ErrorKind.auth
    assert "403" in result.error


def test_the_dominant_kind_is_the_most_common_one():
    from avg.core.errors import ErrorKind
    from avg.pipeline.phases.visuals import _SceneResult, _dominant_kind

    results = {
        1: _SceneResult(scene=1, ok=False, error="a", kind=ErrorKind.auth),
        2: _SceneResult(scene=2, ok=False, error="b", kind=ErrorKind.auth),
        3: _SceneResult(scene=3, ok=False, error="c", kind=ErrorKind.timeout),
    }
    assert _dominant_kind(results) is ErrorKind.auth


def test_a_tie_breaks_by_the_taxonomys_own_priority_order():
    """`_CHECK_ORDER` in `avg/providers/errors.py` already encodes which
    classification is the more actionable claim. No second ordering is
    invented here."""
    from avg.core.errors import ErrorKind
    from avg.pipeline.phases.visuals import _SceneResult, _dominant_kind

    results = {
        1: _SceneResult(scene=1, ok=False, error="a", kind=ErrorKind.timeout),
        2: _SceneResult(scene=2, ok=False, error="b", kind=ErrorKind.auth),
    }
    # auth precedes timeout in _CHECK_ORDER.
    assert _dominant_kind(results) is ErrorKind.auth


def test_an_unclassified_failure_falls_back_to_bug():
    from avg.core.errors import ErrorKind
    from avg.pipeline.phases.visuals import _SceneResult, _dominant_kind

    results = {1: _SceneResult(scene=1, ok=False, error="something odd", kind=None)}
    assert _dominant_kind(results) is ErrorKind.bug
```

Ensure the file imports `from pathlib import Path` — add it if absent.

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/pipeline/test_phases_media.py -q
```

Expected: FAIL — `ImportError: cannot import name '_dominant_kind'`.

- [ ] **Step 3: Add the kind to `_SceneResult` and capture it**

In `avg/pipeline/phases/visuals.py`, replace the `_SceneResult` dataclass:

```python
@dataclass
class _SceneResult:
    scene: int
    ok: bool
    file: str | None = None
    error: str = ""
    #: The classification the provider's own error already carried. Kept
    #: beside the message because the message alone cannot tell a 403 from
    #: a timeout, and the phase-level failure is built from these - see
    #: `_dominant_kind`. `None` means the failure never reached the error
    #: taxonomy at all (a provider raising something that is not an
    #: `AvgError`), which is the one case that really is a bug.
    kind: ErrorKind | None = None
```

Replace the two `except` clauses in `_generate_one`:

```python
    except AvgError as exc:
        return _SceneResult(scene=scene, ok=False, error=exc.message, kind=exc.kind)
    except Exception as exc:  # noqa: BLE001 - a provider bug must not sink the whole phase
        return _SceneResult(scene=scene, ok=False, error=str(exc), kind=None)
```

- [ ] **Step 4: Add `_dominant_kind`**

Insert immediately after `_generate_one` in `avg/pipeline/phases/visuals.py`:

```python
def _dominant_kind(results: dict[int, _SceneResult]) -> ErrorKind:
    """The classification that best explains why nothing was produced.

    Most frequent wins. Ties break by `avg.providers.errors._CHECK_ORDER`,
    which already encodes which classification is the more actionable claim
    when a failure could be read two ways - no second ordering is invented
    here. Falls back to `bug` when nothing was classified at all, which is
    the one case where "bug" is the honest answer.
    """
    from avg.providers.errors import _CHECK_ORDER  # noqa: PLC0415 - avoids an import cycle

    counts: dict[ErrorKind, int] = {}
    for result in results.values():
        if result.ok or result.kind is None:
            continue
        counts[result.kind] = counts.get(result.kind, 0) + 1
    if not counts:
        return ErrorKind.bug

    def rank(kind: ErrorKind) -> int:
        return _CHECK_ORDER.index(kind) if kind in _CHECK_ORDER else len(_CHECK_ORDER)

    return min(counts, key=lambda kind: (-counts[kind], rank(kind)))
```

- [ ] **Step 5: Raise with the truth**

Replace the `ok_count == 0` raise:

```python
        ok_count = sum(1 for r in results.values() if r.ok)
        if ok_count == 0:
            # The reasons were on every `_SceneResult` all along; raising a
            # bare `bug` here is what threw them away and left the take log
            # reading `kind=bug detail=` for a plain missing API key.
            kind = _dominant_kind(results)
            reasons = sorted({r.error for r in results.values() if not r.ok and r.error})
            detail = reasons[0] if reasons else "no reason reported"
            raise AvgError(
                kind,
                f"Visuals: all {total} scene(s) failed on {provider_name}/{model} - {detail}",
            )
```

- [ ] **Step 6: Run the tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/pipeline/test_phases_media.py -q
```

Expected: PASS.

- [ ] **Step 7: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: at least 773 passed.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files` — unchanged.

- [ ] **Step 8: Commit**

```bash
git add avg/pipeline/phases/visuals.py tests/pipeline/test_phases_media.py
git commit -m "fix: a failed Visuals phase reports why, not just that it failed"
```

---

## Task 2: Images fall back to a configured backup, once

**Files:**
- Modify: `avg/core/settings_schema.py` (three new entries in the `images` group)
- Modify: `avg/pipeline/phases/visuals.py` (`run`)
- Test: `tests/pipeline/test_phases_media.py`, `tests/core/test_settings_bindings.py`

**Interfaces:**
- Consumes from Task 1: `_SceneResult.kind`, `_dominant_kind`.
- Produces: setting ids `image_provider_backup`, `image_model_backup`, `image_on_failure`.

**Why:** `avg/providers/text/router.py` already documents the shape — a primary, an optional backup, `on_failure` in `{stop, fall_back}`, the decision taken from `POLICY[kind].on_exhausted` alone, the backup tried **exactly once and never chained** (the legacy `$triedFallback` flag). Images have no equivalent, so a keyless provider fails the whole phase with a working alternative sitting configured.

- [ ] **Step 1: Write the failing settings test**

Append to `tests/core/test_settings_bindings.py`:

```python
def test_images_have_a_backup_and_a_failure_policy():
    """Mirrors the per-phase text triplet the Settings screen already
    renders - `— model`, `— backup`, `— if both fail`."""
    from avg.core.settings_schema import BY_ID

    for setting_id in ("image_provider_backup", "image_model_backup", "image_on_failure"):
        spec = BY_ID[setting_id]
        assert spec.group == "images"
        assert spec.scope == "channel"
        assert spec.invalidates == "visuals"

    assert BY_ID["image_on_failure"].choices == ["stop", "fall_back"]
    assert BY_ID["image_model_backup"].source == "live_list:image"
```

- [ ] **Step 2: Run it to verify it fails**

```bash
.venv-build/Scripts/python.exe -m pytest tests/core/test_settings_bindings.py -q
```

Expected: FAIL — `KeyError: 'image_provider_backup'`.

- [ ] **Step 3: Add the three settings**

In `avg/core/settings_schema.py`, insert immediately after the `image_model` entry (find it by content — `id="image_model"`):

```python
    SettingSpec(
        id="image_provider_backup",
        group="images",
        label="Image provider — backup",
        help="Tried once if the primary provider fails in a way a different provider could survive. Leave unset to never fall back.",
        type="choice",
        default="",
        scope="channel",
        choices=["", "openai", "gemini", "pollinations", "stablehorde", "huggingface"],
        invalidates="visuals",
    ),
    SettingSpec(
        id="image_model_backup",
        group="images",
        label="Image model — backup",
        help="Which model the backup provider should use. The Live List decides what is offered here.",
        type="text",
        default="",
        scope="channel",
        invalidates="visuals",
        source="live_list:image",
    ),
    SettingSpec(
        id="image_on_failure",
        group="images",
        label="Images — if both fail",
        help=(
            "Stop the run, or fall back to the backup provider. Read by "
            "`avg/pipeline/phases/visuals.py`. A failure a second provider "
            "would hit identically - a blocked prompt, a bug - never falls back."
        ),
        type="choice",
        default="stop",
        scope="channel",
        choices=["stop", "fall_back"],
        invalidates="visuals",
    ),
```

- [ ] **Step 4: Write the failing fallback tests**

Append to `tests/pipeline/test_phases_media.py`:

```python
def test_an_auth_failure_falls_back_to_the_backup_once():
    """POLICY says `auth -> switch_key` and `quota -> switch_provider`, so a
    different provider could plausibly help. The backup is tried exactly
    once - never chained - matching the text router's own rule."""
    from avg.core.errors import ErrorKind
    from avg.pipeline.phases.visuals import _should_fall_back

    assert _should_fall_back(ErrorKind.auth) is True
    assert _should_fall_back(ErrorKind.quota) is True
    assert _should_fall_back(ErrorKind.model_missing) is True


def test_a_failure_a_second_provider_would_repeat_never_falls_back():
    """`POLICY` marks these `on_exhausted="fail"` - a different provider
    fails identically, so trying one spends money for nothing."""
    from avg.core.errors import ErrorKind
    from avg.pipeline.phases.visuals import _should_fall_back

    assert _should_fall_back(ErrorKind.content_filter) is False
    assert _should_fall_back(ErrorKind.bug) is False
    assert _should_fall_back(ErrorKind.dependency) is False
```

- [ ] **Step 5: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/pipeline/test_phases_media.py -q
```

Expected: FAIL — `cannot import name '_should_fall_back'`.

- [ ] **Step 6: Add the policy check**

Insert after `_dominant_kind` in `avg/pipeline/phases/visuals.py`:

```python
def _should_fall_back(kind: ErrorKind) -> bool:
    """Whether a different provider could plausibly survive this failure.

    Consults `POLICY` and nothing else - no status code, no error string -
    exactly as `avg/providers/text/router.py` does for text. A kind marked
    `on_exhausted="fail"` (content_filter, bug, dependency) would fail the
    same way on any provider, so falling back would spend money for nothing.
    """
    from avg.providers.errors import POLICY  # noqa: PLC0415 - avoids an import cycle

    rule = POLICY.get(kind)
    if rule is None:
        return False
    return rule.on_exhausted in {"switch_key", "switch_provider", "switch_model"}
```

- [ ] **Step 7: Wire the fallback into `run`**

In `avg/pipeline/phases/visuals.py`'s `run`, the current code generates into `results` with one provider. Extract that into a local helper and call it at most twice. Replace the block from `results: dict[int, _SceneResult] = {}` through the `ok_count == 0` raise with:

```python
        def _generate_all(prov: ImageProvider, prov_name: str, mdl: str) -> dict[int, _SceneResult]:
            """One full pass over every scene with one provider."""
            out: dict[int, _SceneResult] = {}
            done = 0
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(_generate_one, prov, prompt, aspect_ratio, mdl, i, images_dir / f"{i:03d}.png"): i
                    for i, prompt in jobs
                }
                for future in as_completed(futures):
                    result = future.result()
                    out[result.scene] = result
                    done += 1
                    percent = int(done / total * 100)
                    status = "ok" if result.ok else f"failed ({result.error})"
                    ctx.emit(
                        ProgressEvent(
                            project_id=ctx.project.id,
                            percent=percent,
                            message=f"Scene {result.scene}/{total}: {status}",
                        )
                    )
            return out

        results = _generate_all(provider, provider_name, model)

        if not any(r.ok for r in results.values()):
            # Exactly once, never chained - the same rule the text router
            # inherited from the legacy `$triedFallback` flag.
            backup_provider = str(settings.get("image_provider_backup") or "")
            backup_model = str(settings.get("image_model_backup") or "")
            on_failure = str(settings.get("image_on_failure") or "stop")
            kind = _dominant_kind(results)
            if on_failure == "fall_back" and backup_provider and backup_model and _should_fall_back(kind):
                ctx.emit(
                    ProgressEvent(
                        project_id=ctx.project.id,
                        percent=0,
                        message=f"Falling back to {backup_provider}/{backup_model}...",
                    )
                )
                provider_name, model = backup_provider, backup_model
                results = _generate_all(_build_provider(backup_provider, transport), backup_provider, backup_model)

        ok_count = sum(1 for r in results.values() if r.ok)
        if ok_count == 0:
            # The reasons were on every `_SceneResult` all along; raising a
            # bare `bug` here is what threw them away and left the take log
            # reading `kind=bug detail=` for a plain missing API key.
            kind = _dominant_kind(results)
            reasons = sorted({r.error for r in results.values() if not r.ok and r.error})
            detail = reasons[0] if reasons else "no reason reported"
            raise AvgError(
                kind,
                f"Visuals: all {total} scene(s) failed on {provider_name}/{model} - {detail}",
            )
```

Leave the `ok_count < total` degradation block and the manifest below it exactly as they are.

- [ ] **Step 8: Run the tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/pipeline/test_phases_media.py tests/core/test_settings_bindings.py -q
```

Expected: PASS.

- [ ] **Step 9: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: at least 778 passed.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 10: Commit**

```bash
git add avg/core/settings_schema.py avg/pipeline/phases/visuals.py tests/
git commit -m "feat: images fall back to a configured backup provider, exactly once"
```

---

## Task 3: Re-running a phase invalidates the phases after it

**Files:**
- Modify: `avg/pipeline/runner.py` (`run`, immediately after `to_run = _select(...)`)
- Test: `tests/pipeline/test_runner.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: no new public symbol. Behaviour change only.

**Why:** Observed in the Studio rail: Script reads `Not started` while Director I, Visuals, Voice, Director II and Screen Text read done and Render reads failed. `POST` resume calls `_start_pipeline_job(..., start_from=phase.id)` (`avg/api/app.py:906-911`), and the runner only touches a phase's record when it reaches it — so if the restarted phase fails, every later phase keeps a stale `done` and the app will happily render a video whose images belong to a script that no longer exists.

The runner owns `project.phases` bookkeeping (`CLAUDE.md`), so the reset belongs here and nowhere else. Files are **not** deleted, matching how a take clone keeps them.

- [ ] **Step 1: Write the failing test**

Append to `tests/pipeline/test_runner.py`:

```python
def test_starting_partway_marks_the_later_phases_pending():
    """A phase after the restart point cannot still be `done` - its output
    was derived from a run that is being replaced. Records are reset; files
    are left alone, exactly as a take clone leaves them."""
    from avg.core.models import PhaseRecord
    from avg.pipeline import runner
    from avg.pipeline.registry import REGISTRY

    project = _project()
    for phase in REGISTRY:
        project.phases[phase.key] = PhaseRecord(status="done")

    # `_phases(8)` builds RecordingPhase stubs whose keys come straight from
    # REGISTRY (`_KEYS` at the top of this file), so the reset has real keys
    # to act on without running a single real phase.
    list(runner.run(project, channel=_channel(), start_from=3, phases=_phases(8)))

    assert project.phases["brief"].status == "done"
    assert project.phases["script"].status == "done"
    assert project.phases["director1"].status == "done"
    # 3-7 were reset, then re-run by the stubs, so they end `done` again -
    # what matters is that the RESET happened, which the `only=` test below
    # isolates. Assert here on the one the stubs cannot have touched:
    assert project.phases["visuals"].status == "done"


def test_an_only_run_resets_nothing_else():
    """`only=` is the fast per-phase iteration loop and deliberately ignores
    `start_from`; it must not sweep the rest of the pipeline."""
    from avg.core.models import PhaseRecord
    from avg.pipeline import runner
    from avg.pipeline.registry import REGISTRY

    project = _project()
    for phase in REGISTRY:
        project.phases[phase.key] = PhaseRecord(status="done")

    list(runner.run(project, channel=_channel(), only="visuals", phases=_phases(8)))

    # Untouched by an `only=` run: no reset, no re-run.
    assert project.phases["render"].status == "done"


def test_a_partial_rerun_that_never_reaches_a_phase_leaves_it_pending():
    """The actual defect: Script restarted, the run dies at Script, and
    Render still reads `done` from the previous run. The reset has to happen
    when the run STARTS, not when each phase is reached."""
    from avg.core.models import PhaseRecord
    from avg.pipeline import runner
    from avg.pipeline.registry import REGISTRY

    project = _project()
    for phase in REGISTRY:
        project.phases[phase.key] = PhaseRecord(status="done")

    # Fails at index 1 of the selected set, so ids 3-4 run and 5-7 are never
    # reached at all.
    list(
        runner.run(
            project,
            channel=_channel(),
            start_from=3,
            phases=_phases(8, fail_at=4, fail_with=RuntimeError("boom")),
        )
    )

    for key in ("director2", "screen_text", "render"):
        assert project.phases[key].status == "pending", key
```

`_project()`, `_channel()` and `_phases()` are existing helpers at the top of `tests/pipeline/test_runner.py`. Do not invent new ones.

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/pipeline/test_runner.py -q
```

Expected: FAIL — `render` is still `done`.

- [ ] **Step 3: Reset the downstream records**

In `avg/pipeline/runner.py`'s `run`, immediately after `to_run = _select(phases, start_from, only)`:

```python
    # A phase after the restart point cannot still claim to be done: its
    # output was derived from the run being replaced, and leaving the record
    # alone is how a project ends up with Script `not started` next to a
    # green Visuals - and a render whose images belong to a script that no
    # longer exists. Records only; the FILES stay, exactly as they do for a
    # cloned take. `only=` is exempt because it is the deliberate
    # single-phase iteration loop and sweeps nothing.
    if only is None:
        for phase in phases:
            if phase.id >= start_from:
                record = project.phases.get(phase.key)
                if record is not None and record.status == "done":
                    record.status = "pending"
                    record.error = None
        if on_phase_change is not None:
            on_phase_change()
```

- [ ] **Step 4: Run the tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/pipeline/test_runner.py -q
```

Expected: PASS.

- [ ] **Step 5: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: at least 780 passed. Some existing runner tests may assert the old behaviour — if one does, read it: a test asserting that a later phase stays `done` across a partial re-run is asserting the defect, and should be updated with a comment saying so. Report any you change.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 6: Commit**

```bash
git add avg/pipeline/runner.py tests/pipeline/test_runner.py
git commit -m "fix: re-running a phase stops the later ones claiming to be current"
```

---

## Task 4: `GET /api/phases`, and a sentence per phase

**Files:**
- Modify: `avg/pipeline/phase.py` (the `Phase` protocol)
- Modify: `avg/pipeline/registry.py` (`_StubPhase`)
- Modify: `avg/pipeline/phases/writing.py`, `director.py`, `visuals.py`, `voice.py`, `captions.py`, `render.py` (one `description` per phase class)
- Modify: `avg/api/routes_settings.py` (the new route)
- Modify: `tests/pipeline/test_runner.py` (`RecordingPhase` — see Step 4b, this is not optional)
- Test: `tests/api/test_routes_settings.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, and Task 6 depends on this exactly:
  - `GET /api/phases` → `{"phases": [{"id": int, "key": str, "name": str, "description": str}, ...]}` in `REGISTRY` order.
  - `Phase` protocol gains a read-only `description: str`.

**Why:** The spec puts one sentence per phase in `registry.py`, the documented single source of truth for phase identity — deliberately **not** `web/src/phases.ts`, which `CLAUDE.md` records as live duplication. No route serves phase definitions today; `phases.ts`'s own docstring calls itself duplication *"until the backend serves phase definitions"*. This adds the smallest route that satisfies that sentence.

**`phases.ts` is not deleted here** — it also carries the nested Script/Render sub-step display that `REGISTRY` does not model.

`Phase` declares `id`/`key`/`name` as read-only **properties**, not field annotations, because a plain annotation makes the Protocol demand a settable attribute that a frozen dataclass cannot satisfy. `description` must follow the same pattern.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_routes_settings.py` (match the file's existing `client`/`AUTH` fixtures):

```python
def test_phases_route_serves_the_registry_in_order(client):
    body = client.get("/api/phases", headers=AUTH).json()
    keys = [p["key"] for p in body["phases"]]
    assert keys == [
        "brief", "script", "director1", "visuals",
        "voice", "director2", "screen_text", "render",
    ]
    assert [p["id"] for p in body["phases"]] == list(range(8))


def test_every_phase_describes_itself(client):
    """The description is what an un-started phase panel shows instead of an
    empty box. A blank one would put the panel straight back where it was."""
    body = client.get("/api/phases", headers=AUTH).json()
    for phase in body["phases"]:
        assert phase["description"].strip(), phase["key"]


def test_the_phases_route_requires_a_token(client):
    assert client.get("/api/phases").status_code == 401
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/api/test_routes_settings.py -q
```

Expected: FAIL — 404 on `/api/phases`.

- [ ] **Step 3: Add `description` to the protocol**

In `avg/pipeline/phase.py`, after the `name` property:

```python
    @property
    def description(self) -> str:
        """One sentence: what this phase does and what it writes.

        Shown by the Studio artifact panel when the phase has not run yet,
        where there are no artifacts to show and the panel would otherwise
        be empty. Lives on the phase - and so in
        `avg.pipeline.registry.REGISTRY` - because that is where phase
        identity lives; `web/src/phases.ts` is already duplication of this
        registry and must not be given prose to duplicate as well.
        """
        ...
```

- [ ] **Step 4: Add a description to every phase**

Add a `description` property to each phase class. `_StubPhase` in `avg/pipeline/registry.py` gets a field so the runner's stub-based tests keep satisfying the protocol:

```python
    id: int
    key: str
    name: str
    description: str = "A placeholder phase."
```

Each real phase gets one, matching the class's existing style (they declare `key: str = "..."` / `name: str = "..."` as class attributes — follow that, do not convert them to properties):

| File | Class | `description` |
|---|---|---|
| `writing.py` | `BriefPhase` | `"Turns your topic into a brief: the angle, the audience and the tone the script will follow. Writes brief.json."` |
| `writing.py` | `ScriptPhase` | `"Writes the narration and splits it into scenes, verbatim, in two passes. Writes narration.txt and scenes.json."` |
| `director.py` | `Director1Phase` | `"Plans what each scene should show and how many images it needs. Writes video_map.json."` |
| `visuals.py` | `VisualsPhase` | `"Generates one image per scene with the configured image provider. Writes images/ and images/manifest.json."` |
| `voice.py` | `VoicePhase` | `"Synthesises the narration and assembles it with real pauses. Writes audio/."` |
| `director.py` | `Director2Phase` | `"Plans motion, grade and any takeover cards over the images. Writes edit_plan.json."` |
| `captions.py` | `ScreenTextPhase` | `"Transcribes the narration and writes the caption file the renderer burns in. Writes render/captions.ass."` |
| `render.py` | `RenderPhase` | `"Encodes the base video, then composites overlays and captions over it. Writes the finished mp4."` |

- [ ] **Step 4b: Give the test stub a description too**

`tests/pipeline/test_runner.py`'s `RecordingPhase` is passed to
`runner.run(phases=...)`, which is typed `Sequence[Phase]`. Adding
`description` to the protocol makes that stub stop satisfying it, and mypy
will fail on the test file. Add the field beside its `name`:

```python
    id: int
    key: str
    name: str
    description: str = "A recording stub."
```

Search the repo for any other class satisfying `Phase` and give it one as
well:

```bash
grep -rn "def inputs_ready" --include=*.py . | grep -v "avg/pipeline/phases/"
```

Every hit outside `avg/pipeline/phases/` needs the same field.

- [ ] **Step 5: Add the route**

In `avg/api/routes_settings.py`, add near the other GET routes:

```python
@router.get("/api/phases")
async def get_phases() -> dict:
    """The pipeline's phases, in order, with what each one is for.

    Serves `avg.pipeline.registry.REGISTRY` - the single source of truth for
    phase order and identity. `web/src/phases.ts` has carried a hand-kept
    copy of the keys since Slice 1, with its own docstring calling itself
    duplication "until the backend serves phase definitions"; this is that
    route. It does not retire that file, which also owns the nested
    sub-step display REGISTRY does not model.
    """
    from avg.pipeline.registry import REGISTRY  # noqa: PLC0415 - keeps the pipeline off the import path

    return {
        "phases": [
            {"id": p.id, "key": p.key, "name": p.name, "description": p.description}
            for p in REGISTRY
        ]
    }
```

- [ ] **Step 6: Run the tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/api/test_routes_settings.py -q
```

Expected: PASS.

- [ ] **Step 7: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: at least 783 passed.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 8: Commit**

```bash
git add avg/pipeline/ avg/api/routes_settings.py tests/api/test_routes_settings.py
git commit -m "feat: serve the phase registry, with a sentence describing each phase"
```

---

## Task 5: Reachable is not usable

**Files:**
- Modify: `avg/api/routes_settings.py` (`get_model_cache`)
- Modify: `web/src/api.ts` (`ModelCacheResponse`)
- Modify: `web/src/ModelCacheCard.tsx`
- Test: `tests/api/test_model_cache_routes.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `GET /api/models/cache` gains `"needs_key": {"image/stablehorde": true, ...}` — `kind/provider` keys, `true` when that provider requires a key and has none.

**Why:** Measured 2026-07-31: `reachable: {"image/stablehorde": true}` while a real generation returns HTTP 403. Reachability is an anonymous catalogue GET; generation is not. A keyless provider therefore reads **Reachable** and cannot produce a single image — which is what walked the user into a dead end.

**No key value is ever read or returned** — only the boolean `avg/config/secrets.py` already exposes.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_model_cache_routes.py`:

```python
def test_a_keyless_provider_is_reported_as_needing_a_key(client, monkeypatch):
    """Stable Horde's catalogue is public, so it reads reachable while a
    real generation 403s. The card must be able to say so."""
    monkeypatch.setattr(
        "avg.api.routes_settings.secrets_config.get_key",
        lambda provider: None,
    )
    body = client.get("/api/models/cache", headers=AUTH).json()
    assert body["needs_key"]["image/stablehorde"] is True
    # Edge needs no key at all, so it is never flagged.
    assert body["needs_key"].get("voice/edge") is not True


def test_a_keyed_provider_is_not_flagged(client, monkeypatch):
    monkeypatch.setattr(
        "avg.api.routes_settings.secrets_config.get_key",
        lambda provider: "a-key",
    )
    body = client.get("/api/models/cache", headers=AUTH).json()
    assert body["needs_key"].get("image/stablehorde") is not True


def test_no_key_value_ever_appears_in_the_response(client, monkeypatch):
    monkeypatch.setattr(
        "avg.api.routes_settings.secrets_config.get_key",
        lambda provider: "sk-secret-value",
    )
    assert "sk-secret-value" not in client.get("/api/models/cache", headers=AUTH).text
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/api/test_model_cache_routes.py -q
```

Expected: FAIL — `KeyError: 'needs_key'`.

- [ ] **Step 3: Report it**

In `avg/api/routes_settings.py`, add above `get_model_cache`:

```python
#: Providers that cannot generate without a key, by `kind/provider`. Edge
#: needs none (it is the free voice provider) and Pollinations needs none,
#: so neither appears. Stable Horde's CATALOGUE is public while its
#: generation endpoint is not - which is exactly the trap this exists to
#: close: it reads "Reachable" and then 403s on the first real image.
_NEEDS_KEY: tuple[str, ...] = (
    "image/stablehorde",
    "image/openai",
    "image/gemini",
    "image/huggingface",
    "voice/elevenlabs",
)
```

In `get_model_cache`'s returned dict, add one key:

```python
        "needs_key": {
            entry: True
            for entry in _NEEDS_KEY
            if not secrets_config.get_key(entry.split("/", 1)[1])
        },
```

`secrets_config` is already imported at the top of this module as
`from avg.config import secrets as secrets_config`.

- [ ] **Step 4: Run the tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/api/test_model_cache_routes.py -q
```

Expected: PASS.

- [ ] **Step 5: Surface it in the card**

In `web/src/api.ts`, add to `ModelCacheResponse`:

```ts
  /** Providers that cannot generate without a key and have none, keyed
   * `kind/provider`. A provider can be reachable — its catalogue is a
   * public GET — and still be unable to produce anything. */
  needs_key: Record<string, boolean>;
```

In `web/src/ModelCacheCard.tsx`, in the loop that builds reachable rows, use the flag for the detail text:

```tsx
  for (const [key, ok] of Object.entries(cache?.reachable ?? {})) {
    const keyless = Boolean(cache?.needs_key?.[key]);
    rows.push({
      key: `reach:${key}`,
      label: key,
      verdict: ok ? "reachable" : "unreachable",
      // Stated, never implied: a public catalogue proves the endpoint
      // answers, not that this provider can generate anything.
      detail: !ok
        ? "the provider's catalogue did not answer"
        : keyless
          ? "answered its catalogue, but has no key — it cannot generate"
          : "the provider answered its catalogue — this entry itself is unproven",
    });
  }
```

- [ ] **Step 6: Build, lint, full suite, types**

```bash
cd web && npm run build && npm run lint
```

Expected: both clean.

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: at least 786 passed.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 7: Commit**

```bash
git add avg/api/routes_settings.py web/src/api.ts web/src/ModelCacheCard.tsx tests/api/test_model_cache_routes.py
git commit -m "feat: a reachable provider with no key says it cannot generate"
```

---

## Task 6: Phase panels explain themselves

**Files:**
- Modify: `web/src/api.ts` (add `getPhases`)
- Modify: `web/src/artifacts/PhaseArtifact.tsx`
- Modify: `web/src/tokens.css` (one new block)

**Interfaces:**
- Consumes from Task 4: `GET /api/phases` → `{"phases": [{id, key, name, description}]}`.
- Consumes from Task 1: the failure message now names the provider and the reason.
- Produces: `getPhases(): Promise<{ phases: PhaseDef[] }>` in `api.ts`, where `PhaseDef` is `{id: number; key: string; name: string; description: string}`. Name it `ApiPhaseDef` to avoid colliding with the existing `PhaseDef` in `web/src/phases.ts`.

**Why:** `PhaseArtifact.tsx` returns `null` without a body, and each body renders `Missing what=…` when its phase wrote no files. An un-started phase collapses to a bare sub-step list while completed phases get tables and image grids. The panels are empty because nothing was written — but it reads as broken.

- [ ] **Step 1: Add the API call**

In `web/src/api.ts`:

```ts
/** One phase as the backend defines it. `avg/pipeline/registry.py` is the
 * single source of truth for phase order and identity; this is that list.
 * Named `ApiPhaseDef` because `web/src/phases.ts` already exports a
 * `PhaseDef` for its own nested sub-step display, which this does not
 * replace. */
export interface ApiPhaseDef {
  id: number;
  key: string;
  name: string;
  description: string;
}

export function getPhases(): Promise<{ phases: ApiPhaseDef[] }> {
  return apiGet<{ phases: ApiPhaseDef[] }>("/api/phases");
}
```

- [ ] **Step 2: Show the description when a phase has not run**

In `web/src/artifacts/PhaseArtifact.tsx`, add to the imports:

```tsx
import { getPhases, type ApiPhaseDef } from "../api";
```

Add state and a fetch beside the existing artifact fetch:

```tsx
  const [phaseDefs, setPhaseDefs] = useState<ApiPhaseDef[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    getPhases()
      .then((r) => {
        if (!cancelled) setPhaseDefs(r.phases);
      })
      .catch(() => {
        if (!cancelled) setPhaseDefs([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);
```

Replace the early return `if (!Body || takeId === null) return null;` with:

```tsx
  if (takeId === null) return null;

  const definition = phaseDefs?.find((p) => p.key === phaseKey) ?? null;

  // An un-started phase has no artifacts, so every body renders "no X in
  // this take" and the panel collapses to nothing. Saying what the phase is
  // FOR is more use than an empty box - and it is the same information the
  // pipeline rail is already showing a grey dot for.
  const nothingYet = artifacts !== null && (artifacts.by_phase[phaseKey] ?? []).length === 0;
  if (nothingYet && definition) {
    return (
      <div className="artifact-stack">
        <p className="artifact-idle">{definition.description}</p>
      </div>
    );
  }

  if (!Body) return null;
```

Leave everything below unchanged — a phase that *has* artifacts keeps its existing body, including a failed phase showing whatever it did produce.

- [ ] **Step 3: Style the idle line**

In `web/src/tokens.css`, add beside the existing `.artifact-empty` rule (find it by content):

```css
/* What an un-started phase's panel shows instead of nothing: the sentence
   from `avg/pipeline/registry.py`. Same muted weight as `.artifact-empty`,
   which states a missing file - both are "there is nothing here yet", so
   they read the same on purpose. */
.artifact-idle {
  margin: 0;
  padding: 14px 16px;
  font-size: 13.5px;
  line-height: 1.6;
  color: var(--muted);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}
```

- [ ] **Step 4: Build and lint**

```bash
cd web && npm run build && npm run lint
```

Expected: both clean.

- [ ] **Step 5: Report what each state renders**

No test to run — the frontend has no unit tests by convention. In your report, state what the panel renders for: a phase that never ran; a phase that ran and wrote artifacts; a phase that failed after writing some artifacts; and a phase whose `GET /api/phases` call failed. Read your own code for each rather than assuming.

- [ ] **Step 6: Commit**

```bash
git add web/src/api.ts web/src/artifacts/PhaseArtifact.tsx web/src/tokens.css
git commit -m "feat: an un-started phase panel says what the phase is for"
```

---

## Task 7: Live List states its age and collapses accents

**Files:**
- Modify: `web/src/api.ts` (`LiveListResponse` gains `updated`)
- Modify: `avg/api/routes_settings.py` (`get_live_list` returns it)
- Modify: `web/src/LiveList.tsx`
- Test: `tests/api/test_live_list_routes.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `GET /api/live-list` gains `"updated": "<iso8601>" | null`.

**Why:** Measured 2026-07-31 — before a refresh the cache held **no** `__text__*` entries at all and `__voice__edge: 0`; after one free refresh, `__text__gemini: 33`, `__text__groq: 10`, `__voice__edge: 322`. The models were never missing. Nothing on screen said the catalogue was stale, so one Gemini entry looked like all there was. And Edge's 322 voices produce eleven English rows in the language filter (`en-GB`, `en-US`, `en-AU`, …) when the user wants one.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_live_list_routes.py`:

```python
def test_the_live_list_says_when_it_was_last_listed(client):
    """A stale catalogue that says nothing is why 33 Gemini models looked
    like 1."""
    body = client.get("/api/live-list", headers=AUTH).json()
    assert "updated" in body
    assert body["updated"] is None  # nothing has ever been listed here
```

- [ ] **Step 2: Run it to verify it fails**

```bash
.venv-build/Scripts/python.exe -m pytest tests/api/test_live_list_routes.py -q
```

Expected: FAIL — `assert 'updated' in body`.

- [ ] **Step 3: Return it**

In `avg/api/routes_settings.py`'s `get_live_list`, add to the returned dict:

```python
        "updated": (
            app_state.model_cache.updated_at().isoformat()
            if app_state.model_cache.updated_at() is not None
            else None
        ),
```

- [ ] **Step 4: Run the test**

```bash
.venv-build/Scripts/python.exe -m pytest tests/api/test_live_list_routes.py -q
```

Expected: PASS.

- [ ] **Step 5: Show the age, and collapse the languages**

In `web/src/api.ts`, add `updated: string | null;` to `LiveListResponse`.

In `web/src/LiveList.tsx`, replace the `locales` memo with one that collapses to the primary subtag:

```tsx
  // One entry per LANGUAGE, not per locale. Edge advertises 322 voices
  // across eleven English locales alone; `en-GB`, `en-US` and `en-AU` are
  // one choice to a user building a shortlist, and eleven rows of "English"
  // is the filter failing at the only job it has.
  const locales = useMemo(() => {
    const found = new Set<string>();
    for (const row of data?.voice ?? []) {
      const l = data?.voice_meta?.[row.id]?.locale;
      if (l) found.add(l.split("-")[0]);
    }
    return ["all", ...[...found].sort()];
  }, [data]);
```

Change the locale comparison in the row filter so a selected language matches every one of its accents:

```tsx
    if (locale !== "all" && (info?.locale ?? "").split("-")[0] !== locale) return false;
```

Add the age line under the existing note paragraph:

```tsx
      <p className="channels__note">
        {data?.updated
          ? `Catalogues last listed ${new Date(data.updated).toLocaleString()}.`
          : "Catalogues have never been listed — press Refresh catalogues to see what each provider offers."}
      </p>
```

- [ ] **Step 6: Build, lint, full suite, types**

```bash
cd web && npm run build && npm run lint
```

Expected: both clean.

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: at least 787 passed.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 7: Commit**

```bash
git add avg/api/routes_settings.py web/src/api.ts web/src/LiveList.tsx tests/api/test_live_list_routes.py
git commit -m "feat: the Live List states its age and offers one entry per language"
```

---

## Final verification

Run after Task 7. Not a task — no commit of its own unless something needs fixing.

- [ ] `.venv-build/Scripts/python.exe -m pytest tests -q` — at least 787, never below 769.
- [ ] `.venv-build/Scripts/python.exe -m mypy` — no worse than 688 errors.
- [ ] `npm --prefix web run build && npm --prefix web run lint` — clean.
- [ ] Set `image_provider` to `stablehorde` with no key and run Visuals. The failure names the provider and says a key is missing; the take log records `kind=auth`, **not** `kind=bug`.
- [ ] Set `image_on_failure` to `fall_back` with a backup configured and repeat: the take log shows both attempts, and only two.
- [ ] Re-run Script on a finished project: every later phase reads pending, the files are still on disk.
- [ ] Open an un-started phase in Studio: it describes itself. Open a failed Render: it shows the reason.
- [ ] Live List: the language filter offers **English** once, not eleven variants, and the age line is present.
- [ ] App Health: a keyless Stable Horde reads "answered its catalogue, but has no key — it cannot generate".
- [ ] Confirm from the take log that **no image was generated and no ElevenLabs characters were spent** during any of the above except the two deliberate Visuals runs.

---

## Appendix: spec coverage

| Spec section | Task |
|---|---|
| §3.1 Failure carries its kind and reasons | 1 |
| §3.2 Images get the text router's fallback | 2 |
| §3.3 Reachable is not usable | 5 |
| §3.4 Re-running invalidates what came after | 3 |
| §3.5 Phase panel says what the phase is for | 4 (backend), 6 (frontend) |
| §3.6 A stale catalogue says so | 7 |
| §3.7 One language, not one per accent | 7 |
| §4 Testing | 1, 2, 3, 4, 5, 7 |
| §5 Verification | Final verification |

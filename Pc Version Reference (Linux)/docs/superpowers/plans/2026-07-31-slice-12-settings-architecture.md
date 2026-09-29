# Slice 12 — Settings architecture: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the app's own behaviour a home in Settings, let a channel choose between its own settings document and the app-wide defaults document without reintroducing the merge layer that caused three legacy bugs, move the Live List to where it is configured, and give App Health a check that exercises the app rather than describing it.

**Architecture:** Six tasks. One new config module owning one new document, one settings-catalogue reorganisation, one resolution change, one new health stage, and two frontend moves. Nothing changes the phase registry or what a phase writes.

**Tech Stack:** Python 3.12 + FastAPI + pydantic (backend), React 19 + TypeScript + Vite (frontend), pytest, mypy, oxlint.

**Source spec:** `docs/superpowers/specs/2026-07-31-slice-12-settings-architecture-design.md`

## Global Constraints

Every task's requirements implicitly include all of these.

- **Interpreter is `.venv-build/Scripts/python.exe`.** Tests: `.venv-build/Scripts/python.exe -m pytest tests -q`. Types: `.venv-build/Scripts/python.exe -m mypy`.
- **Test baseline is `769 passed`** (Slice 11 raises it further; whatever the suite reports when you start, never go below it).
- **mypy baseline is `Found 688 errors in 22 files`.** mypy does NOT pass clean on this repo; the bar is that your change adds nothing to the count.
- **No live API calls in tests, ever.** `tests/conftest.py` forces `AVG_REPLAY_FIXTURES=1`. Never record a fixture. An image generation and an ElevenLabs synthesis both cost real money.
- **A key value must never leave the process.** `avg/config/secrets.py` is the only module that encrypts, decrypts or persists them. `/api/settings/values` refuses `type="secret"` ids and `SettingsDoc.values` never holds one. Nothing in this slice may put a key into a document, a response, a log line, or a Deep Check result.
- **Every `SettingSpec` must declare `invalidates`** — a required field with no default. A test enforces completeness.
- **A new setting must be wired to something.** `settings_schema.py:79-93` records three settings deleted on 2026-07-28 because the UI saved them and no Python read them. Every entry this slice adds names its reader in its `help` or an adjacent comment.
- **Tests must never write to the real `%APPDATA%/AVG Studio`.** Pass `create_app`'s path overrides.
- **Nothing below `avg/api/` may print or prompt.**
- **`avg/media/vendor/` is protected.**
- **Never use a native form control.** Use `web/src/Select.tsx` and the `.switch` pattern.
- **One filled terracotta `.btn--primary` primary action per screen.** On Settings that is `Save changes`; on App Health, `Re-run everything`.
- **Reuse `web/src/tokens.css` values** — never a fresh guess.
- **The frontend has no unit tests by convention.** Frontend work is verified by build, lint, and reasoning stated in the report.
- **Match code by content, not by line number** — `tokens.css`, `App.tsx` and `Settings.tsx` have drifted from any numbers quoted here.
- **Commit once per task**, on local `main`. No push, no PR, no GitHub interaction.

---

## Task 1: The `channel_defaults.json` document

**Files:**
- Create: `avg/config/channel_defaults.py`
- Create: `tests/config/test_channel_defaults.py`
- Modify: `avg/api/app.py` (`create_app` gains `channel_defaults_path`)

**Interfaces:**
- Consumes: nothing.
- Produces, and Tasks 2 and 3 depend on these exactly:
  - `default_channel_defaults_path() -> Path` — `%APPDATA%/AVG Studio/channel_defaults.json`
  - `load_defaults(path: Path | None) -> dict[str, Any]`
  - `save_defaults(values: dict[str, Any], path: Path | None) -> None`
  - `create_app(..., channel_defaults_path: Path | None = None)`, stored as `app.state.channel_defaults_path`

**Why:** `settings_source="global"` needs one app-wide document holding channel-scope values. It gets its own module for the same reason `secrets.py`, `channels.py` and `settings.py` each have one — one document, one owner.

- [ ] **Step 1: Write the failing tests**

Create `tests/config/test_channel_defaults.py`:

```python
"""The app-wide defaults document a channel can read INSTEAD of its own.

Storage only. The either/or resolution lives in `avg/api/routes_settings.py`
and is tested there.
"""

from __future__ import annotations

from avg.config.channel_defaults import load_defaults, save_defaults


def test_a_missing_document_is_empty_not_an_error(tmp_path):
    """Same tolerance every other document in this app is read with: a
    missing or corrupt file means "nothing set yet", never an exception."""
    assert load_defaults(tmp_path / "nope.json") == {}


def test_a_corrupt_document_is_empty_not_an_error(tmp_path):
    path = tmp_path / "channel_defaults.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_defaults(path) == {}


def test_saving_then_loading_round_trips(tmp_path):
    path = tmp_path / "channel_defaults.json"
    save_defaults({"image_provider": "stablehorde", "length_preset": "60s"}, path)
    assert load_defaults(path) == {"image_provider": "stablehorde", "length_preset": "60s"}


def test_saving_replaces_wholesale(tmp_path):
    """A snapshot, not a merge. A field dropped from the new set is gone -
    a merge here would be the per-field inheritance this whole design
    exists to avoid."""
    path = tmp_path / "channel_defaults.json"
    save_defaults({"a": 1, "b": 2}, path)
    save_defaults({"a": 9}, path)
    assert load_defaults(path) == {"a": 9}
```

Create `tests/config/__init__.py` if `tests/config/` has none.

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/config/test_channel_defaults.py -q
```

Expected: FAIL — `ModuleNotFoundError: avg.config.channel_defaults`.

- [ ] **Step 3: Write the module**

Create `avg/config/channel_defaults.py`:

```python
"""The app-wide defaults a channel can read INSTEAD of its own document.

`CLAUDE.md`'s Config scopes rule deletes the legacy copy mechanism: "No
global copy, no apply/capture step, no absent-means-inherit. Three bugs in
the legacy app came from the copy mechanism this deletes." Those three bugs
came from PER-FIELD, IMPLICIT inheritance - a field missing from a channel
silently resolving to a global one.

This document is not that. A channel reads its own settings or it reads
this one, whole, chosen explicitly by `settings_source` and defaulting to
its own. No field is ever resolved by looking in one document and falling
back to the other, so there is no per-field resolution to get wrong. The
shape is the one `live_list_source` already ships
(`avg/api/routes_settings.py`'s `_prefs_for_scope`).

Storage only. The either/or resolution lives in `avg/api/routes_settings.py`.
Never holds a secret: `type="secret"` values live in `avg.config.secrets`
alone and the porting action skips them.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any


def default_channel_defaults_path() -> Path:
    """`%APPDATA%/AVG Studio/channel_defaults.json`.

    Mirrors `avg.config.settings`'s own default; `create_app` passes an
    override so tests never touch the real profile.
    """
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "AVG Studio" / "channel_defaults.json"


def load_defaults(path: Path | None = None) -> dict[str, Any]:
    """The stored values, or `{}`.

    A missing file, unreadable JSON or a malformed shape all mean "nothing
    set yet" rather than an exception - the same tolerance
    `avg.core.model_cache` and `avg.core.error_store` are read with. A
    broken document must never be the thing that stops the app.
    """
    target = Path(path) if path is not None else default_channel_defaults_path()
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - any read failure means "nothing set yet"
        return {}
    values = data.get("values") if isinstance(data, dict) else None
    return values if isinstance(values, dict) else {}


def save_defaults(values: dict[str, Any], path: Path | None = None) -> None:
    """Replace the document with `values`, atomically.

    A wholesale replace, not a merge: this document is a snapshot the user
    took, and merging would quietly turn it into the per-field inheritance
    the module docstring rules out. Temp file then `os.replace`, matching
    `avg.pipeline.create.save_project` and `avg.config.settings`.
    """
    target = Path(path) if path is not None else default_channel_defaults_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".channel-defaults-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "values": values}, handle, indent=2, sort_keys=True)
        os.replace(tmp_name, target)
    except BaseException:
        with suppress(OSError):
            os.remove(tmp_name)
        raise
```

- [ ] **Step 4: Add the `create_app` override**

In `avg/api/app.py`, add `channel_defaults_path: Path | None = None,` to `create_app`'s signature beside `model_prefs_path`, and in the body beside the other path assignments:

```python
    # The app-wide defaults a channel can read instead of its own document
    # (`settings_source`). Its own file, beside settings.json, for the same
    # reason the error log and the model cache have theirs.
    app.state.channel_defaults_path = (
        Path(channel_defaults_path) if channel_defaults_path is not None else None
    )
```

- [ ] **Step 5: Run the tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/config/test_channel_defaults.py -q
```

Expected: PASS (4 tests).

- [ ] **Step 6: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: 4 more than your starting count, zero failures.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 7: Commit**

```bash
git add avg/config/channel_defaults.py avg/api/app.py tests/config/
git commit -m "feat: an app-wide channel-defaults document, replaced wholesale"
```

---

## Task 2: The `app` group, and the settings that describe the app

**Files:**
- Modify: `avg/core/settings_schema.py`
- Modify: `web/src/Settings.tsx` (`GROUP_LABELS`, and the Components panel condition)
- Test: `tests/core/test_settings_bindings.py`

**Interfaces:**
- Consumes from Task 1: nothing directly.
- Produces, and Task 3 depends on `settings_source` exactly:
  - group `app` exists; group `runtime` no longer does
  - `motion_scale` (`app` scope, choices `full`/`reduced`/`none`, default `full`)
  - `startup_check` (`app` scope, bool, default `true`)
  - `settings_source` (**`channel` scope**, choices `channel`/`global`, default `channel`)

**Why:** Measured 2026-07-31 — of 56 settings, exactly two are app-scope and not a key (`theme`, `verbose_log`), both in `runtime`. There is no home for "how should this application behave" as distinct from "how should this video be made".

`settings_source` is `scope="channel"` because the choice belongs to a channel, exactly as `live_list_source` is. It sits in the `app` group because it describes how configuration behaves.

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_settings_bindings.py`:

```python
def test_the_app_group_replaces_runtime():
    from avg.core.settings_schema import SETTINGS

    groups = {s.group for s in SETTINGS}
    assert "app" in groups
    assert "runtime" not in groups


def test_the_app_group_holds_the_apps_own_behaviour():
    from avg.core.settings_schema import BY_ID

    for setting_id in ("theme", "verbose_log", "motion_scale", "startup_check"):
        assert BY_ID[setting_id].group == "app", setting_id
        assert BY_ID[setting_id].scope == "app", setting_id

    assert BY_ID["motion_scale"].choices == ["full", "reduced", "none"]
    assert BY_ID["startup_check"].default is True


def test_settings_source_is_a_channel_choice_like_live_list_source():
    """Whole-document either/or, the shape `live_list_source` already
    ships - never per-field inheritance."""
    from avg.core.settings_schema import BY_ID

    spec = BY_ID["settings_source"]
    assert spec.group == "app"
    assert spec.scope == "channel"
    assert spec.choices == ["channel", "global"]
    assert spec.default == "channel"
    assert spec.invalidates is None
```

- [ ] **Step 2: Run it to verify it fails**

```bash
.venv-build/Scripts/python.exe -m pytest tests/core/test_settings_bindings.py -q
```

Expected: FAIL — `"runtime" not in groups` fails, and `KeyError: 'motion_scale'`.

- [ ] **Step 3: Move the two existing settings and add three**

In `avg/core/settings_schema.py`, change `group="runtime"` to `group="app"` on both the `theme` and `verbose_log` entries (find them by `id="theme"` and `id="verbose_log"`). Then add, immediately after `verbose_log`:

```python
    SettingSpec(
        id="motion_scale",
        group="app",
        label="Animation",
        help=(
            "How much the interface moves. Read by `web/src/tokens.css` through the "
            "`--motion-scale` custom property. Your operating system's own "
            "reduced-motion preference always wins over this."
        ),
        type="choice",
        default="full",
        scope="app",
        choices=["full", "reduced", "none"],
        # Nothing is invalidated: this changes how the UI moves, never what a
        # run produces.
        invalidates=None,
    ),
    SettingSpec(
        id="startup_check",
        group="app",
        label="Check components at startup",
        help=(
            "Run the dependency check while the library loads. Read by "
            "`avg/shell.py`. Turning it off starts marginally faster and tells "
            "you nothing until you open App health."
        ),
        type="bool",
        default=True,
        scope="app",
        invalidates=None,
    ),
    SettingSpec(
        id="settings_source",
        group="app",
        label="This channel's settings",
        help=(
            "Use this channel's own settings, or the app-wide defaults - whole "
            "documents, never mixed field by field. Read by "
            "`avg/api/routes_settings.py`."
        ),
        type="choice",
        default="channel",
        scope="channel",
        choices=["channel", "global"],
        # Nothing is invalidated: this changes WHERE a value is read from,
        # not what any existing artifact was made with.
        invalidates=None,
    ),
```

- [ ] **Step 4: Update the two frontend references**

In `web/src/Settings.tsx`, in `GROUP_LABELS` replace the `runtime` entry:

```ts
  app: "App & components",
```

and change the Components panel condition from `g.group === "runtime"` to:

```tsx
                {g.group === "app" && !searching && <HealthPanel />}
```

(Slice 11 or an earlier fix may already have it keyed to `runtime`; either way it follows the group.)

- [ ] **Step 5: Run the tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/core/test_settings_bindings.py -q
```

Expected: PASS.

- [ ] **Step 6: Build, lint, full suite, types**

```bash
cd web && npm run build && npm run lint
```

Expected: both clean.

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: 3 more than after Task 1.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 7: Commit**

```bash
git add avg/core/settings_schema.py web/src/Settings.tsx tests/core/test_settings_bindings.py
git commit -m "feat: an App settings group for the app's own behaviour"
```

---

## Task 3: A channel reads its own document, or the app's

**Files:**
- Modify: `avg/api/routes_settings.py` (`_channel_values`, and a new port route)
- Test: `tests/api/test_settings_routes.py`

**Interfaces:**
- Consumes from Task 1: `load_defaults`, `save_defaults`, `app.state.channel_defaults_path`.
- Consumes from Task 2: the `settings_source` setting.
- Produces: `POST /api/settings/port-to-global` → `{"ported": <count>}`.

**Why:** This is the task the whole slice turns on, and the one that must not become the mechanism `CLAUDE.md` deletes.

**The rule, stated once and enforced by the tests below:** `settings_source` is itself always read from the **channel's own document** — otherwise a channel switched to `global` could never switch back. It governs where every *other* channel-scope setting is read from. A field missing from whichever document is selected resolves to the `SettingSpec`'s own `default` and **never** to the other document.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_settings_routes.py` (match the file's existing fixtures; add `channel_defaults_path` to its `paths` fixture, pointing at `tmp_path / "channel_defaults.json"`):

```python
def _make_channel(client, label="Docs"):
    created = client.post("/api/channels", json={"label": label}, headers=AUTH).json()
    client.post(f"/api/channels/{created['id']}/activate", headers=AUTH)
    return created["id"]


def test_a_channel_reads_its_own_document_by_default(client):
    _make_channel(client)
    client.post("/api/settings/values", json={"length_preset": "30s"}, headers=AUTH)
    body = client.get("/api/settings/values", headers=AUTH).json()
    assert body["channel"]["length_preset"] == "30s"
    assert body["channel"]["settings_source"] == "channel"


def test_switching_to_global_reads_the_defaults_document(client):
    from avg.config.channel_defaults import save_defaults

    _make_channel(client)
    client.post("/api/settings/values", json={"length_preset": "30s"}, headers=AUTH)
    save_defaults({"length_preset": "90s"}, client.app.state.channel_defaults_path)

    client.post("/api/settings/values", json={"settings_source": "global"}, headers=AUTH)

    body = client.get("/api/settings/values", headers=AUTH).json()
    assert body["channel"]["length_preset"] == "90s"
    # The switch itself is ALWAYS read from the channel's own document -
    # otherwise a channel on "global" could never be switched back.
    assert body["channel"]["settings_source"] == "global"


def test_switching_back_returns_the_channels_own_value_unchanged(client):
    """The round trip must not write through. A channel's own value has to
    survive a visit to the global document untouched."""
    from avg.config.channel_defaults import save_defaults

    _make_channel(client)
    client.post("/api/settings/values", json={"length_preset": "30s"}, headers=AUTH)
    save_defaults({"length_preset": "90s"}, client.app.state.channel_defaults_path)

    client.post("/api/settings/values", json={"settings_source": "global"}, headers=AUTH)
    client.post("/api/settings/values", json={"settings_source": "channel"}, headers=AUTH)

    assert client.get("/api/settings/values", headers=AUTH).json()["channel"]["length_preset"] == "30s"


def test_a_field_absent_from_the_selected_document_never_falls_back(client):
    """The one assertion that guards CLAUDE.md's deleted mechanism. On
    `global`, a field the defaults document does not hold resolves to the
    SettingSpec default - NOT to the channel's own value."""
    from avg.config.channel_defaults import save_defaults
    from avg.core.settings_schema import BY_ID

    _make_channel(client)
    client.post("/api/settings/values", json={"length_preset": "30s"}, headers=AUTH)
    save_defaults({}, client.app.state.channel_defaults_path)
    client.post("/api/settings/values", json={"settings_source": "global"}, headers=AUTH)

    resolved = client.get("/api/settings/values", headers=AUTH).json()["channel"]["length_preset"]
    assert resolved == BY_ID["length_preset"].default
    assert resolved != "30s"


def test_porting_copies_channel_values_and_no_secret(client):
    from avg.config.channel_defaults import load_defaults

    _make_channel(client)
    client.post("/api/settings/values", json={"length_preset": "30s"}, headers=AUTH)
    client.post("/api/keys/groq", json={"key": "sk-should-never-be-copied"}, headers=AUTH)

    res = client.post("/api/settings/port-to-global", headers=AUTH)
    assert res.status_code == 200

    stored = load_defaults(client.app.state.channel_defaults_path)
    assert stored["length_preset"] == "30s"
    assert "groq" not in stored
    assert "sk-should-never-be-copied" not in str(stored)
    # App-scope settings belong to the app, not to a channel snapshot.
    assert "theme" not in stored


def test_porting_without_an_active_channel_is_refused(client):
    assert client.post("/api/settings/port-to-global", headers=AUTH).status_code == 400
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/api/test_settings_routes.py -q
```

Expected: FAIL — `KeyError: 'settings_source'` and 404 on the port route.

- [ ] **Step 3: Resolve through the selected document**

In `avg/api/routes_settings.py`, add the import at the top:

```python
from avg.config import channel_defaults as channel_defaults_config
```

Replace `_channel_values` entirely:

```python
def _channel_values(app_state: Any) -> dict[str, Any]:
    """Current value for every `scope="channel"` setting.

    Read from the active channel's own document, or - when that channel's
    `settings_source` says `"global"` - from the app-wide defaults document,
    WHOLE. There is no third case: a field absent from whichever document is
    selected resolves to the `SettingSpec`'s own default, never to the other
    document.

    That is what keeps this from being the mechanism `CLAUDE.md` deletes.
    The three legacy bugs came from per-field, implicit inheritance; there is
    no per-field resolution here to get wrong. Same shape as
    `live_list_source` (see `_prefs_for_scope`).

    `settings_source` itself is ALWAYS read from the channel's own document -
    otherwise a channel switched to `"global"` could never be switched back.
    """
    active_id = channels_config.active_channel_id(app_state.settings_path)
    channel = channels_config.get_channel(active_id, app_state.channels_dir) if active_id else None
    own = channel.settings if channel is not None else {}

    source = str(own.get("settings_source", "channel"))
    if source == "global":
        selected: dict[str, Any] = channel_defaults_config.load_defaults(
            getattr(app_state, "channel_defaults_path", None)
        )
    else:
        selected = dict(own)

    values = {
        spec.id: selected.get(spec.id, spec.default)
        for spec in SETTINGS
        if spec.scope == "channel"
    }
    # The switch is the channel's own, whichever document the rest came from.
    values["settings_source"] = source
    return values
```

- [ ] **Step 4: Add the port route**

In `avg/api/routes_settings.py`:

```python
@router.post("/api/settings/port-to-global")
async def port_to_global(request: Request) -> dict:
    """Copy the active channel's channel-scope values into the app-wide defaults.

    One explicit, user-initiated snapshot - never an automatic capture. That
    distinction is the whole reason this is allowed to exist beside
    `CLAUDE.md`'s "no apply/capture step": the rule deletes the LIVE copy
    mechanism, where a value silently tracked another document. This writes
    once, when asked, and the two documents are unrelated afterwards.

    Secrets are never copied - `type="secret"` values live in
    `avg.config.secrets` alone and are not part of any channel document.
    App-scope settings are not copied either: they belong to the app, not to
    a channel's snapshot.
    """
    app_state = request.app.state
    active_id = channels_config.active_channel_id(app_state.settings_path)
    channel = channels_config.get_channel(active_id, app_state.channels_dir) if active_id else None
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no active channel — activate a channel before porting its settings",
        )

    ported = {
        spec.id: channel.settings[spec.id]
        for spec in SETTINGS
        if spec.scope == "channel" and spec.type != "secret" and spec.id in channel.settings
    }
    channel_defaults_config.save_defaults(
        ported, getattr(app_state, "channel_defaults_path", None)
    )
    return {"ported": len(ported)}
```

- [ ] **Step 5: Run the tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/api/test_settings_routes.py -q
```

Expected: PASS.

- [ ] **Step 6: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: 7 more than after Task 2.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 7: Update the rule so code and documentation agree**

In `CLAUDE.md`, in the **Config scopes (no merge layer)** section, replace the `scope="channel"` bullet with:

```markdown
- `scope="channel"` settings live in the **active channel's** document, or —
  when that channel's `settings_source` is `"global"` — in the app-wide
  `channel_defaults.json`, **whole**. Never both: no field is ever resolved
  by looking in one and falling back to the other, and there is no
  absent-means-inherit. Three bugs in the legacy app came from per-field
  implicit inheritance, which this shape has no room for. `settings_source`
  itself is always read from the channel's own document. Porting a channel's
  values into the defaults is one explicit, user-pressed snapshot
  (`POST /api/settings/port-to-global`), never a live copy.
```

A rule and the code disagreeing is worse than either.

- [ ] **Step 8: Commit**

```bash
git add avg/api/routes_settings.py CLAUDE.md tests/api/test_settings_routes.py
git commit -m "feat: a channel reads its own settings document or the app-wide one"
```

---

## Task 4: Deep Check — a fourth stage that exercises the app

**Files:**
- Modify: `avg/core/health.py` (a `_deep_checks` function and a fourth `Stage`)
- Modify: `avg/api/routes_settings.py` (`get_health_report` gains a `deep` flag)
- Test: `tests/core/test_health.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `GET /api/health/report?deep=true` returns a fourth stage with `key="deep"`. Without the flag the report is exactly as it is today.

**Why:** Measured 2026-07-31 — `AVG APP/_internal` holds 76 entries and contains **no `tests/` and no pytest**. A Deep Check defined as "run `/tests`" would be absent from every installed copy, which is where a user most needs a diagnostic. `health.py` already is the in-app check framework: `Component`, `Stage`, three ordered stages, `report()`.

**No provider is called and nothing is generated**, so Deep Check costs nothing.

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_health.py`:

```python
def test_deep_checks_exercise_things_and_clean_up(tmp_path):
    """The three existing stages ask "is it present and does it parse".
    This one does the thing and undoes it."""
    from avg.core.health import _deep_checks

    output_root = tmp_path / "library"
    output_root.mkdir()
    checks = _deep_checks(settings_path=tmp_path / "settings.json", output_root=output_root)

    names = {c.name for c in checks}
    assert "Settings round-trip" in names
    assert "Output folder write" in names
    # Nothing may be left behind.
    assert list(output_root.iterdir()) == []


def test_deep_checks_report_a_failure_rather_than_raising(tmp_path):
    """A check that cannot run is a red row, never an exception - the same
    contract the other three stages already keep."""
    from avg.core.health import _deep_checks

    checks = _deep_checks(
        settings_path=tmp_path / "settings.json",
        output_root=tmp_path / "does" / "not" / "exist" / "and" / "cannot",
    )
    assert any(c.ok is False for c in checks)


def test_the_report_has_four_stages_only_when_asked(tmp_path):
    from avg.core.health import report

    shallow = report(settings_path=tmp_path / "s.json", channels_dir=tmp_path / "c", output_root=tmp_path)
    assert [s.key for s in shallow] == ["dependencies", "warmup", "safeguards"]

    deep = report(
        settings_path=tmp_path / "s.json",
        channels_dir=tmp_path / "c",
        output_root=tmp_path,
        deep=True,
    )
    assert [s.key for s in deep] == ["dependencies", "warmup", "safeguards", "deep"]


def test_no_secret_value_appears_in_a_deep_check(tmp_path):
    """The secret round-trip proves DPAPI works on a throwaway string. It
    must never touch, or echo, a stored key."""
    from avg.core.health import _deep_checks

    checks = _deep_checks(settings_path=tmp_path / "settings.json", output_root=tmp_path)
    blob = " ".join(f"{c.name} {c.detail} {c.version}" for c in checks)
    assert "sk-" not in blob
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv-build/Scripts/python.exe -m pytest tests/core/test_health.py -q
```

Expected: FAIL — `cannot import name '_deep_checks'`.

- [ ] **Step 3: Write the deep checks**

In `avg/core/health.py`, add before `report`:

```python
def _deep_checks(*, settings_path: Path | None, output_root: Path | None) -> list[Component]:
    """Stage 4: does it actually work, rather than is it merely present.

    Every check here DOES the thing and undoes it. This is deliberately not
    the pytest suite: `tools/build.py` ships neither `tests/` nor pytest, so
    a check defined as "run the suite" would be absent from every installed
    copy - which is exactly where a user most needs a diagnostic. These run
    identically in a dev checkout and a packaged build.

    No provider is called and nothing is generated, so this costs nothing
    and cannot spend money. Every failure is a red row, never an exception.
    """
    checks: list[Component] = []

    # Settings round-trip: write, reload, compare, discard. Uses a temp file
    # rather than the real document - a diagnostic must not be able to
    # corrupt the thing it is diagnosing.
    try:
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "settings.json"
            probe.write_text(json.dumps({"schema_version": 1, "values": {"theme": "day"}}), encoding="utf-8")
            reloaded = json.loads(probe.read_text(encoding="utf-8"))
        ok = reloaded["values"]["theme"] == "day"
        checks.append(Component(name="Settings round-trip", ok=ok, detail="written and read back"))
    except Exception as exc:  # noqa: BLE001
        checks.append(Component(name="Settings round-trip", ok=False, detail=str(exc)))

    # Secret round-trip: DPAPI encrypt then decrypt a throwaway string.
    # Never touches a stored key, and the value below is not a credential.
    try:
        from avg.config.secrets import _protect, _unprotect  # noqa: PLC0415

        # `_protect`/`_unprotect` take and return BYTES and each return None
        # on a DPAPI failure rather than raising - so the None checks below
        # are the real failure path, not defensive padding.
        sealed = _protect(b"avg-deep-check")
        opened = _unprotect(sealed) if sealed is not None else None
        ok = opened == b"avg-deep-check"
        detail = "DPAPI sealed and opened a throwaway value" if ok else "DPAPI did not round-trip"
        checks.append(Component(name="Secret round-trip", ok=ok, detail=detail))
    except Exception as exc:  # noqa: BLE001
        checks.append(Component(name="Secret round-trip", ok=False, detail=str(exc)))

    # Output folder: create and delete a real file where renders land.
    if output_root is not None:
        try:
            output_root.mkdir(parents=True, exist_ok=True)
            probe_file = output_root / ".avg-deep-check"
            probe_file.write_text("ok", encoding="utf-8")
            probe_file.unlink()
            checks.append(Component(name="Output folder write", ok=True, detail=str(output_root)))
        except Exception as exc:  # noqa: BLE001
            checks.append(Component(name="Output folder write", ok=False, detail=str(exc)))

    # FFmpeg encode: a one-second colour clip, produced and thrown away.
    # This is the only check that costs real time, and it is the one that
    # proves the render path rather than describing it.
    try:
        import subprocess
        import tempfile

        from avg.media.environment import ffmpeg_exe  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "probe.mp4"
            result = subprocess.run(
                [
                    ffmpeg_exe(), "-y", "-f", "lavfi",
                    "-i", "color=c=black:s=128x128:d=1",
                    "-pix_fmt", "yuv420p", str(out),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            ok = result.returncode == 0 and out.is_file() and out.stat().st_size > 0
            detail = "encoded a one-second clip" if ok else f"ffmpeg exited {result.returncode}"
        checks.append(Component(name="FFmpeg encode", ok=ok, detail=detail))
    except Exception as exc:  # noqa: BLE001
        checks.append(Component(name="FFmpeg encode", ok=False, detail=str(exc)))

    return checks
```

`_protect` and `_unprotect` are the real pair in `avg/config/secrets.py` (verified 2026-07-31) — do **not** add a new public API to that module, and do not read, decrypt or echo a stored key. The literal `b"avg-deep-check"` is not a credential; it exists so the check proves DPAPI works without going near one.

- [ ] **Step 4: Add the stage**

In `avg/core/health.py`, change `report`'s signature to take `deep: bool = False`, and append conditionally:

```python
    stages = [
        Stage(
            key="dependencies",
            name="Dependency check",
            detail="The tools a render cannot run without",
            checks=check_all(refresh=refresh),
        ),
        Stage(
            key="warmup",
            name="App warmup",
            detail="Configuration and catalogue loading",
            checks=_warmup_checks(settings_path, channels_dir),
        ),
        Stage(
            key="safeguards",
            name="Safeguards",
            detail="What would stop the next run",
            checks=_safeguard_checks(output_root),
        ),
    ]
    if deep:
        # On demand only: it spends real disk and an ffmpeg subprocess, so it
        # must never run merely because a screen was opened.
        stages.append(
            Stage(
                key="deep",
                name="Deep check",
                detail="Does it actually work, not just is it present",
                checks=_deep_checks(settings_path=settings_path, output_root=output_root),
            )
        )
    return stages
```

- [ ] **Step 5: Pass the flag through the route**

In `avg/api/routes_settings.py`'s `get_health_report`, add `deep: bool = False` to the signature and `deep=deep` to the `health.report(...)` call.

- [ ] **Step 6: Run the tests**

```bash
.venv-build/Scripts/python.exe -m pytest tests/core/test_health.py -q
```

Expected: PASS.

- [ ] **Step 7: Full suite and types**

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

Expected: 4 more than after Task 3.

```bash
.venv-build/Scripts/python.exe -m mypy
```

Expected: `Found 688 errors in 22 files`.

- [ ] **Step 8: Commit**

```bash
git add avg/core/health.py avg/api/routes_settings.py tests/core/test_health.py
git commit -m "feat: Deep Check exercises the app instead of describing it"
```

---

## Task 5: Deep Check on App Health, and the Live List leaves it

**Files:**
- Modify: `web/src/api.ts` (`getHealthReport` gains `deep`)
- Modify: `web/src/AppHealth.tsx`

**Interfaces:**
- Consumes from Task 4: `GET /api/health/report?deep=true`.
- Produces: `AppHealth` no longer takes `onOpenLiveList`.

**Why:** The Live List decides which models the Settings pickers may offer — it is configuration, filed under diagnosis. Deep Check belongs where diagnosis lives.

- [ ] **Step 1: Add the flag to the API call**

In `web/src/api.ts`:

```ts
/** `deep` adds the fourth stage, which encodes a one-second clip and does a
 * real settings and DPAPI round-trip. On demand only — it costs real disk
 * and a subprocess, so nothing should request it on mount. */
export function getHealthReport(refresh = false, deep = false): Promise<HealthReport> {
  const params = new URLSearchParams();
  if (refresh) params.set("refresh", "true");
  if (deep) params.set("deep", "true");
  const query = params.toString();
  return apiGet<HealthReport>(`/api/health/report${query ? `?${query}` : ""}`);
}
```

- [ ] **Step 2: Add the button and drop the Live List card**

In `web/src/AppHealth.tsx`:

- Change the component signature to `export default function AppHealth()` — no props.
- Delete the entire `{onOpenLiveList && ( … )}` Live List `<section>`.
- Change `load` to take the deep flag: `const load = useCallback(async (refresh: boolean, deep = false) => { … setReport(await getHealthReport(refresh, deep)); … }, [])`.
- Add a plain button beside the existing `Re-run everything` primary — **not** a second `.btn--primary`:

```tsx
        <button type="button" className="btn" onClick={() => void load(true, true)} disabled={busy}>
          {busy ? "Checking…" : "Deep check"}
        </button>
```

The existing stage-rendering loop needs no change: a fourth stage renders through the same code.

- [ ] **Step 3: Build and lint**

```bash
cd web && npm run build && npm run lint
```

Expected: both clean. If `App.tsx` still passes `onOpenLiveList`, the TypeScript build will fail — that prop is removed in Task 6, so for this task remove the prop **at the call site too** and temporarily route the `livelist` screen from nowhere; Task 6 gives it its real entry point. State in your report that you did this.

- [ ] **Step 4: Report what renders**

No tests to run. In your report, state what App Health shows: before any deep check; while one runs; after one passes; and after one fails. Read your own code rather than assuming.

- [ ] **Step 5: Commit**

```bash
git add web/src/api.ts web/src/AppHealth.tsx web/src/App.tsx
git commit -m "feat: Deep check on App health, and the Live List card leaves it"
```

---

## Task 6: The Live List opens from Settings, and motion obeys the setting

**Files:**
- Modify: `web/src/App.tsx` (the `livelist` screen's entry point)
- Modify: `web/src/Settings.tsx` (the entry point, and applying `motion_scale`)
- Modify: `web/src/tokens.css` (the `--motion-scale` property)

**Interfaces:**
- Consumes from Task 2: `motion_scale`, and the `app` group.
- Consumes from Task 5: `AppHealth` takes no props.
- Produces: nothing further.

**Why:** The Live List belongs beside the fields whose options it decides (`image_model` and the per-phase model fields declare `source="live_list:image"`). And `motion_scale` must actually be read, or it becomes the fourth dead switch `settings_schema.py:79-93` warns about.

- [ ] **Step 1: Give the Live List its Settings entry point**

In `web/src/App.tsx`, the `livelist` screen currently returns to `health`. Change its back destination to `settings`, and pass an opener into `Settings`:

```tsx
    } else if (screen.name === "livelist") {
    content = <LiveList onBack={() => setScreen({ name: "settings" })} />;
```

and where `Settings` is constructed:

```tsx
      content = <Settings onOpenLiveList={() => setScreen({ name: "livelist" })} />;
```

In `web/src/Settings.tsx`, accept the prop and render the entry point inside the `models` group block, beside `ModelsPanel`:

```tsx
export default function Settings({ onOpenLiveList }: { onOpenLiveList?: () => void }) {
```

```tsx
                {g.group === "models" && !searching && onOpenLiveList && (
                  <section className="models-panel" aria-label="Live List">
                    <div className="spend-card">
                      <div>
                        <p className="lbl">Live List</p>
                        <b>Which models these pickers may offer</b>
                      </div>
                      <span className="spend-card__note">
                        Tick the models and voices you want offered, mark favourites, and note what
                        each one is good for.
                      </span>
                      <button type="button" className="btn" onClick={onOpenLiveList}>
                        Open Live List
                      </button>
                    </div>
                  </section>
                )}
```

- [ ] **Step 2: Make `motion_scale` real**

In `web/src/Settings.tsx`, apply the value to the document root whenever it is known — beside the existing load effect:

```tsx
  useEffect(() => {
    const scale = String(appValues["motion_scale"] ?? "full");
    // One custom property drives every duration in tokens.css, rather than
    // this being read at dozens of call sites. `prefers-reduced-motion`
    // still wins - see that block in tokens.css, which is unchanged.
    document.documentElement.style.setProperty(
      "--motion-scale",
      scale === "none" ? "0" : scale === "reduced" ? "0.5" : "1",
    );
  }, [appValues]);
```

In `web/src/tokens.css`, declare the property in `:root` beside the other tokens:

```css
  /* Scales every transition and animation duration. Set from the
     `motion_scale` setting by Settings.tsx; 1 is full, 0.5 reduced, 0 none.
     The `prefers-reduced-motion` block below is unchanged and still wins -
     this setting can only reduce motion, never restore it. */
  --motion-scale: 1;
```

Then multiply the durations of the app's own animations by it. Apply to the status pill's ring and label, `.screen-enter`, and the card transitions — for example:

```css
.statuspill__ring {
  animation: avg-spin calc(0.9s / max(var(--motion-scale), 0.01)) linear infinite;
}
```

Use `calc(<duration> * var(--motion-scale))` for transitions and the `max()` guard above for infinite animations, so a scale of `0` cannot produce a zero-duration infinite animation.

- [ ] **Step 3: Build and lint**

```bash
cd web && npm run build && npm run lint
```

Expected: both clean.

- [ ] **Step 4: Verify motion by measurement, not by eye**

Serve the app (`.venv-build/Scripts/python.exe tools/_dev_serve.py`, port 8756, `http://127.0.0.1:8756/?token=devtoken`) and use `javascript_tool` to read the computed value:

```js
getComputedStyle(document.documentElement).getPropertyValue('--motion-scale')
```

Set the setting to each of `full`, `reduced` and `none`, saving each time, and report the three values you read back. State in your report whether the OS reduced-motion block still overrides them.

- [ ] **Step 5: Confirm no native control crept in**

```bash
grep -rn "<select\|type=\"checkbox\"" web/src/
```

Expected: only Home's sanctioned hidden `.select-dot` checkbox.

- [ ] **Step 6: Commit**

```bash
git add web/src/App.tsx web/src/Settings.tsx web/src/tokens.css
git commit -m "feat: the Live List opens from Settings, and motion obeys its setting"
```

---

## Final verification

Run after Task 6. Not a task — no commit unless something needs fixing.

- [ ] `.venv-build/Scripts/python.exe -m pytest tests -q` — never below your starting count; expect roughly 18 new tests.
- [ ] `.venv-build/Scripts/python.exe -m mypy` — no worse than 688 errors.
- [ ] `npm --prefix web run build && npm --prefix web run lint` — clean.
- [ ] Settings shows an **App & components** group and no Runtime group; the Components table is on screen under it.
- [ ] Set `motion_scale` to `reduced`, reload, confirm transitions are visibly shorter. Set the OS to prefer reduced motion with `motion_scale` at `full`: motion is still suppressed — the OS wins.
- [ ] Switch a channel to `settings_source="global"`, change a value in the defaults, confirm the channel reads it. Switch back and confirm the channel's own value returns **unchanged**.
- [ ] Press Port to global, then check `channel_defaults.json`: it holds the channel's values, no key, and no app-scope setting.
- [ ] Deep check on App Health: four stages report, a one-second clip really is encoded, and **no file is left in the library folder** afterwards.
- [ ] The Live List opens from Settings; App Health no longer offers it.
- [ ] `"Build Installer.bat"` — Deep check behaves identically in `AVG APP/`, which is the whole reason it is not pytest.

---

## Appendix: spec coverage

| Spec section | Task |
|---|---|
| §3.1 An "App" group | 2 |
| §3.1 `motion_scale`, `startup_check` | 2 (declared), 6 (`motion_scale` wired) |
| §3.2 Channel chooses its whole document | 1 (storage), 3 (resolution + porting) |
| §3.3 Deep Check | 4 (backend), 5 (frontend) |
| §3.4 Live List moves to Settings | 5 (removal), 6 (entry point) |
| §4 Testing | 1, 2, 3, 4 |
| §5 Verification | Final verification |

**Note on `startup_check`:** Task 2 declares it and names `avg/shell.py` as its reader. Wiring it there is deliberately left to Slice 13, which is the slice that touches startup — but it must not ship unread. If Slice 13 is not executed immediately after this one, either wire it in Task 2 or drop the entry, per the Global Constraint that a new setting is wired to something.

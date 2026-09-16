# Slice 32 (sub-slice 1) — Provider Resilience: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a quota/billing failure is remembered — persisted, per provider,
across calls and across sessions — so the same dead provider is never
tried again until it clears; a rate-limit failure waits the real time a
provider reports instead of a guessed exponential backoff, when that real
time is available.

**Architecture:** one new module, `avg/config/provider_health.py`, owns
persisted cooldown state (same atomic-write shape every sibling config
module already uses). `avg/providers/transport.py` — already the one
choke point every HTTP-based provider goes through — gains a pre-flight
check (skip a call to a provider in cooldown) and a post-failure write
(record one, on a quota/billing failure). `avg/providers/errors.py` gains
one small, pure, standard-library-only function (`retry_after_seconds`)
that `_send_with_retry` consults for the real wait time. Edge TTS (the one
voice provider that bypasses `Transport` entirely) gets the identical two
calls made explicitly at its own two `classify()` sites. `avg/core/
health.py` gains one new safeguard row so an active cooldown is visible
without reading a log.

**One refinement from the spec, found while grounding this plan in the
real current code:** the spec described `classify()` itself growing an
optional `headers` parameter. Reading `classify()`'s actual body (`avg/
providers/errors.py`) shows no realistic case where a header changes the
*kind* decision — kind already comes entirely from status + body text.
`retry_after_seconds()` is therefore a fully independent function, called
by `_send_with_retry` alongside `classify()`, not through it.
`classify()`'s signature does not change at all. Smaller, and the same
behavior the spec asked for.

**Tech Stack:** Python 3, pytest, `httpx` (already a dependency, used by
`Transport`) — no frontend change.

**Spec:** `docs/superpowers/specs/2026-09-13-slice-32-provider-resilience-design.md`

## Global Constraints

- No live API calls in tests — every existing `Transport`/provider test
  already runs `mode="record"` against a fake `httpx.Client`/handler or
  `mode="replay"` against a fixture; nothing here changes that.
- `avg/providers/errors.py` stays pure: "no imports beyond the standard
  library and `avg.core.errors`" (its own docstring's explicit rule) — the
  new `retry_after_seconds()` uses only `json`, `email.utils`,
  `datetime`, `contextlib.suppress`, `collections.abc.Mapping`, all
  stdlib.
- `avg/media/vendor/` is never touched — this sub-slice has no reason to
  go near it.
- Every `SettingSpec` requires `invalidates` (both new ones are `None` —
  a resilience policy value, not something that should invalidate a
  cached phase result).
- Tests must not touch the real user profile. `tests/conftest.py` already
  redirects `APPDATA`/`LOCALAPPDATA`/`USERPROFILE` to a disposable
  session-scoped temp directory (`_avg_test_env`, autouse) — every config
  module (including the new `provider_health.py`) resolves its default
  path from those env vars at call time, so this is inherited for free.
  No test needs an explicit path override to stay isolated, matching how
  `avg.config.secrets`/`channels`/`settings` tests already work; a `path=`
  parameter still exists on every new function for the rare test that
  wants one pointed at a specific `tmp_path` directly.
- Verify with `.venv-linux/bin/python -m pytest tests -q` in this sandbox
  (per this project's own Linux dev-sandbox convention); the real gate is
  `.venv-build\Scripts\python.exe -m pytest tests -q` on Windows.

## Files touched

| File | Change |
|---|---|
| `avg/core/settings_schema.py` | +2 `SettingSpec`s (`resilience_rate_limit_wait_ceiling_sec`, `resilience_quota_cooldown_hours`) |
| `avg/config/provider_health.py` | New — `ProviderCooldown`, `record_cooldown`, `active_cooldown`, `is_in_cooldown`, `clear_cooldown`, `default_quota_cooldown_until`, `cooldown_error`, `cooldown_message_suffix` |
| `avg/providers/errors.py` | +`retry_after_seconds()` |
| `avg/providers/transport.py` | `Transport.request()`: pre-flight cooldown check. `_send_with_retry`: real wait time on `rate_limit`, cooldown recorded on `quota`/`billing` before raising |
| `avg/providers/voice/providers.py` | `EdgeVoiceProvider.synthesize`/`.list_voices`: same pre-flight check + cooldown recording at their own `classify()` sites |
| `avg/core/health.py` | `_safeguard_checks`: +1 row (`Provider cooldowns`) |
| `avg/pipeline/phases/visuals.py` | Its one existing fallback `ProgressEvent` message gains the cooldown detail when applicable |
| `tests/core/test_settings_schema.py`, new `tests/config/test_provider_health.py`, `tests/providers/test_errors.py`, `tests/providers/test_transport.py`, `tests/providers/test_voice.py`, `tests/core/test_health.py`, `tests/pipeline/test_phases_media.py` | New/extended tests per task |

---

## Task 1 — Two new settings

### Step 1.1 — Test

Add to `tests/core/test_settings_schema.py`:

```python
def test_resilience_settings_exist_with_no_invalidation():
    ids = {s.id: s for s in SETTINGS}
    assert ids["resilience_rate_limit_wait_ceiling_sec"].default == 120
    assert ids["resilience_rate_limit_wait_ceiling_sec"].invalidates is None
    assert ids["resilience_rate_limit_wait_ceiling_sec"].scope == "app"
    assert ids["resilience_quota_cooldown_hours"].default == 24.0
    assert ids["resilience_quota_cooldown_hours"].invalidates is None
    assert ids["resilience_quota_cooldown_hours"].scope == "app"
```

Confirm `SETTINGS`/`Scope` are already imported at the top of that test
file (they are, for every existing test in it) before adding this.

Run it — fails, the two ids don't exist yet.

### Step 1.2 — Implementation

**Verified against the real file before writing this block** (the
compact style below is the actual `group="keys"` convention — an earlier
draft of this plan guessed a more verbose multi-line `SettingSpec` shape
that does not match the file and would have failed to anchor). In
`avg/core/settings_schema.py`, the `SETTINGS` list's last entry before its
closing `]` is the `assemblyai` secret placeholder. Replace:

```python
    SettingSpec(id="assemblyai", group="keys", label="AssemblyAI API key",
                help="Used for AssemblyAI caption transcription.", type="secret", default=None, scope="app",
                invalidates=None),
]
```

with:

```python
    SettingSpec(id="assemblyai", group="keys", label="AssemblyAI API key",
                help="Used for AssemblyAI caption transcription.", type="secret", default=None, scope="app",
                invalidates=None),
    SettingSpec(id="resilience_rate_limit_wait_ceiling_sec", group="app", label="Rate-limit wait ceiling (seconds)",
                help="The longest this app will ever sleep for a single rate-limit retry, even if a provider "
                     "reports a longer wait.", type="int", default=120, scope="app", invalidates=None),
    SettingSpec(id="resilience_quota_cooldown_hours", group="app", label="Quota cooldown (hours)",
                help="How long a provider is skipped after its quota is reported exhausted, when the provider "
                     "itself gives no better reset time.", type="float", default=24.0, scope="app",
                invalidates=None),
]
```

Run Step 1.1's test — passes. Run the full `test_settings_schema.py` file
— passes (the existing "every setting has `invalidates`" completeness
test already covers these two automatically, since `invalidates=None` is
present and valid, not omitted).

**Mechanically verified** (`python3 -c "from avg.core.settings_schema
import SETTINGS; ..."`, real interpreter, real file): both entries import
cleanly, `SETTINGS` grows from 67 to 69 entries, both new ids resolve
`default`/`invalidates`/`scope`/`type` exactly as specified above.

**Commit:** `feat(settings): add resilience_rate_limit_wait_ceiling_sec and resilience_quota_cooldown_hours`

---

## Task 2 — `avg/config/provider_health.py`

### Step 2.1 — Test

New file `tests/config/test_provider_health.py`:

```python
"""Tests for avg.config.provider_health."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from avg.config import provider_health as ph
from avg.core.errors import AvgError, ErrorKind


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "provider_health.json"


def test_no_entry_means_not_in_cooldown(path: Path):
    assert ph.active_cooldown("groq", path=path) is None
    assert ph.is_in_cooldown("groq", path=path) is False


def test_quota_cooldown_round_trips_while_active(path: Path):
    until = datetime.now(timezone.utc) + timedelta(hours=1)
    ph.record_cooldown("groq", "quota", cooldown_until=until, path=path)

    cooldown = ph.active_cooldown("groq", path=path)
    assert cooldown is not None
    assert cooldown.provider == "groq"
    assert cooldown.status == "quota"
    assert cooldown.cooldown_until == until
    assert ph.is_in_cooldown("groq", path=path) is True


def test_expired_quota_cooldown_is_not_active(path: Path):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    ph.record_cooldown("groq", "quota", cooldown_until=past, path=path)

    assert ph.active_cooldown("groq", path=path) is None
    assert ph.is_in_cooldown("groq", path=path) is False


def test_billing_cooldown_has_no_timer_and_persists(path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ph, "get_key", lambda provider: "same-key")
    ph.record_cooldown("elevenlabs", "billing", path=path)

    cooldown = ph.active_cooldown("elevenlabs", path=path)
    assert cooldown is not None
    assert cooldown.status == "billing"
    assert cooldown.cooldown_until is None


def test_billing_cooldown_clears_when_the_key_changes(path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ph, "get_key", lambda provider: "old-key")
    ph.record_cooldown("elevenlabs", "billing", path=path)
    assert ph.is_in_cooldown("elevenlabs", path=path) is True

    monkeypatch.setattr(ph, "get_key", lambda provider: "new-key")
    assert ph.is_in_cooldown("elevenlabs", path=path) is False


def test_billing_cooldown_clears_when_the_key_is_removed(path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ph, "get_key", lambda provider: "old-key")
    ph.record_cooldown("elevenlabs", "billing", path=path)

    monkeypatch.setattr(ph, "get_key", lambda provider: None)
    assert ph.is_in_cooldown("elevenlabs", path=path) is False


def test_clear_cooldown_is_a_no_op_when_nothing_is_recorded(path: Path):
    ph.clear_cooldown("groq", path=path)  # must not raise
    assert ph.active_cooldown("groq", path=path) is None


def test_clear_cooldown_removes_an_active_entry(path: Path):
    ph.record_cooldown("groq", "quota", cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1), path=path)
    ph.clear_cooldown("groq", path=path)
    assert ph.active_cooldown("groq", path=path) is None


def test_cooldowns_for_different_providers_are_independent(path: Path):
    ph.record_cooldown("groq", "quota", cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1), path=path)
    assert ph.active_cooldown("gemini", path=path) is None
    assert ph.active_cooldown("groq", path=path) is not None


def test_provider_matching_is_case_insensitive(path: Path):
    ph.record_cooldown("Groq", "quota", cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1), path=path)
    assert ph.is_in_cooldown("groq", path=path) is True
    assert ph.is_in_cooldown("GROQ", path=path) is True


def test_a_corrupt_file_is_treated_as_empty_not_as_a_crash(path: Path):
    path.write_text("{not json", encoding="utf-8")
    assert ph.active_cooldown("groq", path=path) is None
    # And a subsequent write still succeeds, overwriting the corrupt file.
    ph.record_cooldown("groq", "quota", cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1), path=path)
    assert ph.is_in_cooldown("groq", path=path) is True


def test_default_quota_cooldown_until_reads_the_setting(monkeypatch: pytest.MonkeyPatch):
    from avg.config import settings as settings_module

    doc = settings_module.SettingsDoc()
    doc.values["resilience_quota_cooldown_hours"] = 2.0
    monkeypatch.setattr(ph, "load_settings", lambda: doc)

    before = datetime.now(timezone.utc)
    until = ph.default_quota_cooldown_until("groq")
    assert timedelta(hours=1, minutes=55) < (until - before) < timedelta(hours=2, minutes=5)


def test_cooldown_error_carries_the_right_kind():
    cooldown = ph.ProviderCooldown(
        provider="groq", status="quota", since=datetime.now(timezone.utc), cooldown_until=None
    )
    err = ph.cooldown_error(cooldown)
    assert isinstance(err, AvgError)
    assert err.kind == ErrorKind.quota

    cooldown = ph.ProviderCooldown(
        provider="elevenlabs", status="billing", since=datetime.now(timezone.utc), cooldown_until=None
    )
    assert ph.cooldown_error(cooldown).kind == ErrorKind.billing


def test_cooldown_message_suffix_formats_quota_and_billing():
    assert ph.cooldown_message_suffix(None) == ""

    until = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
    quota = ph.ProviderCooldown(provider="groq", status="quota", since=until, cooldown_until=until)
    assert ph.cooldown_message_suffix(quota) == " (groq: quota, back ~00:00 UTC)"

    billing = ph.ProviderCooldown(provider="elevenlabs", status="billing", since=until, cooldown_until=None)
    assert ph.cooldown_message_suffix(billing) == " (elevenlabs: billing issue)"


def test_concurrent_writes_do_not_lose_an_entry(path: Path):
    # Mirrors avg/pipeline/phases/visuals.py's own ThreadPoolExecutor shape
    # - several threads in one process, not separate processes - which is
    # exactly the concurrency this module's in-process lock has to survive.
    from concurrent.futures import ThreadPoolExecutor

    providers = [f"provider-{i}" for i in range(8)]

    def _record(name: str) -> None:
        ph.record_cooldown(name, "quota", cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1), path=path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_record, providers))

    for name in providers:
        assert ph.is_in_cooldown(name, path=path) is True
```

Run it — fails, the module doesn't exist yet.

### Step 2.2 — Implementation

New file `avg/config/provider_health.py`:

```python
"""Persisted per-provider cooldown state for quota/billing failures.

Slice 32 sub-slice 1 (`docs/superpowers/specs/2026-09-13-slice-32-
provider-resilience-design.md`). Before this module existed, a `quota` or
`billing` failure (`avg.providers.errors.POLICY`, `on_exhausted=
"switch_provider"`) was remembered for exactly as long as the current
run's fallback decision lasted - the very next call, in the same run or a
brand-new session, tried the same dead provider again from scratch. This
module is what makes "dead until it clears" a fact the app actually
remembers.

Two cooldown shapes, because they end differently:

  - `quota` gets a real `cooldown_until` timer (see
    `default_quota_cooldown_until`) - a quota resets on its own.
  - `billing` gets none. It clears only when the provider's configured key
    changes, detected here by comparing a fingerprint of the key at the
    time the cooldown was recorded against its current value - never the
    raw key itself (see `_fingerprint`). There is deliberately no hook
    into `avg.config.secrets.set_key` for this: that module's own
    docstring is explicit that it is the ONLY module that persists keys,
    full stop, so the comparison is made lazily, here, at read time
    instead.

Granularity is per-provider, not per-key. `avg.config.secrets` is one key
per provider name (ElevenLabs' own primary/backup pair is handled entirely
inside `avg.providers.voice.providers.ElevenLabsVoiceProvider` and is not
this module's concern - a cooldown here means "this provider, in effect,
whichever of its own keys it tried").

Same atomic-write shape every sibling module in this package already
uses: a temp file in the same directory, then `os.replace` - `path` either
holds the old complete contents or the new complete contents, never a
half-written file. A module-level lock serialises read-modify-write across
threads in this one process (this app never runs the pipeline in separate
OS processes - `avg.pipeline.phases.visuals`' own `ThreadPoolExecutor` is
several threads in one interpreter, which is exactly what this lock has to
survive; a cross-process mutex, which the legacy PowerShell app needed
because its worker jobs and render engine genuinely were separate
processes, has nothing to do here).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from avg.config.secrets import get_key
from avg.config.settings import load_settings
from avg.config.user_assets import user_assets_root
from avg.core.errors import AvgError, ErrorKind

CooldownStatus = Literal["quota", "billing"]

_LOCK = threading.Lock()


def default_provider_health_path() -> Path:
    """`Documents/AVG Studio/UserAssets/Config/provider_health.json` - the
    same `Config/` directory `settings.json`/`channel_defaults.json`/
    `quick_settings.json`/`model_prefs.json` already share. Not a
    credential, so not `Secrets/`."""
    return user_assets_root() / "Config" / "provider_health.json"


@dataclass(frozen=True, slots=True)
class ProviderCooldown:
    """One provider's active cooldown, as read back from disk."""

    provider: str
    status: CooldownStatus
    since: datetime
    cooldown_until: datetime | None
    note: str = ""


def _fingerprint(key: str | None) -> str:
    """First 12 hex chars of the key's SHA-256 - never the raw key, on disk
    or in memory beyond this one call."""
    if not key:
        return ""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _provider_key(provider: str) -> str:
    return provider.strip().lower()


def _load(path: Path | None) -> dict[str, dict[str, object]]:
    p = Path(path) if path is not None else default_provider_health_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    providers = data.get("providers")
    return providers if isinstance(providers, dict) else {}


def _save(providers: dict[str, dict[str, object]], path: Path | None) -> None:
    p = Path(path) if path is not None else default_provider_health_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "providers": providers}
    fd, tmp_name = tempfile.mkstemp(dir=p.parent, prefix=".provider-health-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, indent=2, sort_keys=True))
        os.replace(tmp_name, p)
    except BaseException:
        with suppress(OSError):
            os.remove(tmp_name)
        raise


def record_cooldown(
    provider: str,
    status: CooldownStatus,
    *,
    cooldown_until: datetime | None = None,
    note: str = "",
    path: Path | None = None,
) -> None:
    """Persist a cooldown for `provider`. Overwrites any existing entry for
    the same provider (case-insensitive)."""
    key = _provider_key(provider)
    with _LOCK:
        providers = _load(path)
        providers[key] = {
            "status": status,
            "since": datetime.now(timezone.utc).isoformat(),
            "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
            "note": note,
            "key_fingerprint": _fingerprint(get_key(provider)),
        }
        _save(providers, path)


def active_cooldown(provider: str, *, path: Path | None = None) -> ProviderCooldown | None:
    """The active cooldown for `provider`, or `None` if there isn't one.

    A `quota` entry expires on its own once `cooldown_until` passes. A
    `billing` entry clears when the provider's currently configured key no
    longer matches the fingerprint stored at write time (including "the
    key was removed entirely").
    """
    key = _provider_key(provider)
    with _LOCK:
        entry = _load(path).get(key)
    if not isinstance(entry, dict):
        return None

    status = entry.get("status")
    if status not in ("quota", "billing"):
        return None

    try:
        since = datetime.fromisoformat(str(entry.get("since")))
    except (TypeError, ValueError):
        return None

    cooldown_until: datetime | None = None
    raw_until = entry.get("cooldown_until")
    if raw_until:
        try:
            cooldown_until = datetime.fromisoformat(str(raw_until))
        except (TypeError, ValueError):
            cooldown_until = None

    if status == "quota":
        if cooldown_until is not None and datetime.now(timezone.utc) >= cooldown_until:
            return None
    else:  # billing
        if _fingerprint(get_key(provider)) != entry.get("key_fingerprint", ""):
            return None

    return ProviderCooldown(
        provider=provider,
        status=status,  # type: ignore[arg-type]
        since=since,
        cooldown_until=cooldown_until,
        note=str(entry.get("note", "")),
    )


def is_in_cooldown(provider: str, *, path: Path | None = None) -> bool:
    """Convenience wrapper: `True` iff `active_cooldown` is not `None`."""
    return active_cooldown(provider, path=path) is not None


def clear_cooldown(provider: str, *, path: Path | None = None) -> None:
    """Remove `provider`'s entry, if any. A no-op if there isn't one."""
    key = _provider_key(provider)
    with _LOCK:
        providers = _load(path)
        if key in providers:
            del providers[key]
            _save(providers, path)


def default_quota_cooldown_until(provider: str) -> datetime:
    """`now + resilience_quota_cooldown_hours` (a `SettingSpec`, default
    24). `provider` is currently unused - a hook for a future per-provider
    override table, not a promise one exists yet."""
    del provider
    hours = load_settings().values.get("resilience_quota_cooldown_hours", 24.0)
    try:
        hours = float(hours)
    except (TypeError, ValueError):
        hours = 24.0
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def cooldown_error(cooldown: ProviderCooldown) -> AvgError:
    """The `AvgError` a pre-flight check raises when skipping a call
    outright, rather than ever reaching the network."""
    kind = ErrorKind.quota if cooldown.status == "quota" else ErrorKind.billing
    detail = cooldown.note or cooldown.status
    return AvgError(kind, f"{cooldown.provider} is in cooldown ({detail})", detail=detail)


def cooldown_message_suffix(cooldown: ProviderCooldown | None) -> str:
    """A short human-readable suffix for a fallback message, or `""` when
    there is no cooldown to mention.

    e.g. " (groq: quota, back ~14:00 UTC)" / " (elevenlabs: billing issue)".
    """
    if cooldown is None:
        return ""
    if cooldown.status == "quota" and cooldown.cooldown_until is not None:
        return f" ({cooldown.provider}: quota, back ~{cooldown.cooldown_until:%H:%M} UTC)"
    if cooldown.status == "quota":
        return f" ({cooldown.provider}: quota)"
    return f" ({cooldown.provider}: billing issue)"
```

Run Step 2.1's tests — all pass.

**Note on `test_default_quota_cooldown_until_reads_the_setting` and
`test_billing_cooldown_*`:** these monkeypatch `ph.get_key`/`ph.load_settings`
directly (the names as imported into this module), not
`avg.config.secrets.get_key`/`avg.config.settings.load_settings` — matching
how this module calls them (a bare name looked up in its own module
namespace, per how `from X import Y` binds).

**Commit:** `feat(config): add provider_health.py - persisted per-provider quota/billing cooldown`

---

## Task 3 — `retry_after_seconds()` in `avg/providers/errors.py`

### Step 3.1 — Test

Add to `tests/providers/test_errors.py`:

```python
from avg.providers.errors import POLICY, RetryRule, classify, retry_after_seconds


def test_retry_after_seconds_reads_the_seconds_form_of_the_header():
    assert retry_after_seconds({"Retry-After": "30"}, "") == 30.0


def test_retry_after_seconds_is_case_insensitive_on_header_name():
    assert retry_after_seconds({"retry-after": "15"}, "") == 15.0


def test_retry_after_seconds_reads_an_http_date_header():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    future = datetime.now(timezone.utc) + timedelta(seconds=45)
    header = format_datetime(future, usegmt=True)
    value = retry_after_seconds({"Retry-After": header}, "")
    assert value is not None
    assert 40.0 < value <= 45.0


def test_retry_after_seconds_reads_a_past_http_date_as_none():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    past = datetime.now(timezone.utc) - timedelta(seconds=45)
    header = format_datetime(past, usegmt=True)
    assert retry_after_seconds({"Retry-After": header}, "") is None


def test_retry_after_seconds_reads_geminis_body_shaped_retry_delay():
    body = (
        '{"error": {"code": 429, "message": "quota", "details": '
        '[{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "15s"}]}}'
    )
    assert retry_after_seconds(None, body) == 15.0


def test_retry_after_seconds_returns_none_when_neither_is_present():
    assert retry_after_seconds({}, "") is None
    assert retry_after_seconds(None, "not json") is None
    assert retry_after_seconds({"Content-Type": "application/json"}, "{}") is None


def test_retry_after_seconds_never_raises_on_garbage_input():
    assert retry_after_seconds({"Retry-After": "not-a-number-or-date"}, "") is None
    assert retry_after_seconds(None, '{"error": {"details": "not-a-list"}}') is None
    assert retry_after_seconds(None, '{"error": {"details": [{"retryDelay": "abc"}]}}') is None
```

Run it — fails, `retry_after_seconds` doesn't exist yet.

### Step 3.2 — Implementation

In `avg/providers/errors.py`, replace the import block:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from avg.core.errors import ErrorKind
```

with:

```python
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Literal

from avg.core.errors import ErrorKind
```

(Every added name — `json`, `collections.abc.Mapping`, `contextlib.suppress`,
`datetime`/`timezone`, `email.utils.parsedate_to_datetime` — is standard
library, preserving this module's own "no imports beyond the standard
library and `avg.core.errors`" rule.)

Then, at the end of the file, after `classify()`'s closing line (`return
ErrorKind.bug`), append:

```python


def retry_after_seconds(headers: Mapping[str, str] | None, body: str) -> float | None:
    """How long a provider says to wait before retrying, in seconds.

    Checked in this order: a `Retry-After` response header (the seconds
    form, or an HTTP-date — RFC 9110 §10.2.3 allows both, though only the
    seconds form has been seen from any provider this app actually calls),
    then a Gemini-shaped JSON body's `error.details[].retryDelay` (e.g.
    `"15s"`). Returns `None` when neither is present or neither parses —
    the caller's own fixed backoff formula is exactly that fallback,
    unaffected by this function existing.

    Never raises. `headers` is intentionally untyped beyond `Mapping[str,
    str]` — an `httpx.Headers` object satisfies this without a cast.
    """
    if headers:
        raw = headers.get("Retry-After") or headers.get("retry-after")
        if raw:
            raw = raw.strip()
            if raw.isdigit():
                return float(raw)
            with suppress(Exception):
                parsed = parsedate_to_datetime(raw)
                if parsed is not None:
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    delta = (parsed - datetime.now(timezone.utc)).total_seconds()
                    if delta > 0:
                        return delta

    if body:
        with suppress(Exception):
            data = json.loads(body)
            details = data.get("error", {}).get("details", [])
            if isinstance(details, list):
                for detail in details:
                    if isinstance(detail, dict) and "retryDelay" in detail:
                        value = str(detail["retryDelay"]).strip()
                        return float(value[:-1]) if value.endswith("s") else float(value)

    return None
```

Run Step 3.1's tests — all pass. Run the full `test_errors.py` file — all
existing tests still pass (no existing signature changed).

**Commit:** `feat(providers): add retry_after_seconds() - real Retry-After/body-delay parsing`

---

## Task 4 — Wire it into `Transport`

### Step 4.1 — Test

Add to `tests/providers/test_transport.py` (mirroring the file's existing
`_client`/sleeper fixtures — see the top of that file for both):

```python
from datetime import datetime, timedelta, timezone

from avg.config import provider_health


def test_a_provider_in_cooldown_is_never_called(tmp_path, monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(
        provider_health, "default_provider_health_path", lambda: tmp_path / "provider_health.json"
    )
    provider_health.record_cooldown(
        "groq", "quota", cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1)
    )

    transport = Transport(client=_client(handler), fixtures_dir=tmp_path, mode="record")
    with pytest.raises(AvgError) as excinfo:
        transport.request("POST", "https://api.groq.com/x", provider="groq")

    assert excinfo.value.kind == ErrorKind.quota
    assert calls == []  # the network was never reached


def test_a_provider_not_in_cooldown_is_called_normally(tmp_path, monkeypatch):
    monkeypatch.setattr(
        provider_health, "default_provider_health_path", lambda: tmp_path / "provider_health.json"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = Transport(client=_client(handler), fixtures_dir=tmp_path, mode="record")
    response = transport.request("POST", "https://api.groq.com/x", provider="groq")
    assert response.status_code == 200


def test_a_quota_failure_records_a_cooldown_before_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(
        provider_health, "default_provider_health_path", lambda: tmp_path / "provider_health.json"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "daily quota exceeded"})

    transport = Transport(client=_client(handler), fixtures_dir=tmp_path, mode="record", sleep=lambda s: None)
    with pytest.raises(AvgError):
        transport.request("POST", "https://api.groq.com/x", provider="groq")

    assert provider_health.is_in_cooldown("groq") is True


def test_a_billing_failure_records_a_cooldown_before_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(
        provider_health, "default_provider_health_path", lambda: tmp_path / "provider_health.json"
    )
    monkeypatch.setattr(provider_health, "get_key", lambda provider: "same-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": "insufficient credits"})

    transport = Transport(client=_client(handler), fixtures_dir=tmp_path, mode="record")
    with pytest.raises(AvgError):
        transport.request("POST", "https://api.elevenlabs.io/x", provider="elevenlabs")

    assert provider_health.is_in_cooldown("elevenlabs") is True


def test_rate_limit_uses_retry_after_header_instead_of_the_fixed_backoff(tmp_path, monkeypatch):
    monkeypatch.setattr(
        provider_health, "default_provider_health_path", lambda: tmp_path / "provider_health.json"
    )
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, json={"error": "rate limited"})
        return httpx.Response(200, json={"ok": True})

    sleeper = _SleepRecorder()
    transport = Transport(client=_client(handler), fixtures_dir=tmp_path, mode="record", sleep=sleeper)
    response = transport.request("POST", "https://api.groq.com/x", provider="groq")

    assert response.status_code == 200
    assert sleeper.calls == [7.0]  # not POLICY[rate_limit].backoff_seconds * 2**0 == 2.0


def test_rate_limit_wait_is_capped_at_the_configured_ceiling(tmp_path, monkeypatch):
    from avg.config import settings as settings_module

    monkeypatch.setattr(
        provider_health, "default_provider_health_path", lambda: tmp_path / "provider_health.json"
    )
    doc = settings_module.SettingsDoc()
    doc.values["resilience_rate_limit_wait_ceiling_sec"] = 10
    monkeypatch.setattr(settings_module, "load_settings", lambda: doc)

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "9999"}, json={"error": "rate limited"})
        return httpx.Response(200, json={"ok": True})

    sleeper = _SleepRecorder()
    transport = Transport(client=_client(handler), fixtures_dir=tmp_path, mode="record", sleep=sleeper)
    transport.request("POST", "https://api.groq.com/x", provider="groq")

    assert sleeper.calls == [10.0]


def test_rate_limit_without_a_real_wait_time_falls_back_to_the_existing_formula(tmp_path, monkeypatch):
    monkeypatch.setattr(
        provider_health, "default_provider_health_path", lambda: tmp_path / "provider_health.json"
    )
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate limited"})  # no Retry-After
        return httpx.Response(200, json={"ok": True})

    sleeper = _SleepRecorder()
    transport = Transport(client=_client(handler), fixtures_dir=tmp_path, mode="record", sleep=sleeper)
    transport.request("POST", "https://api.groq.com/x", provider="groq")

    assert sleeper.calls == [POLICY[ErrorKind.rate_limit].backoff_seconds]  # unchanged behavior


def test_replay_mode_never_checks_or_touches_provider_health(tmp_path, monkeypatch):
    monkeypatch.setattr(
        provider_health, "default_provider_health_path", lambda: tmp_path / "provider_health.json"
    )
    provider_health.record_cooldown(
        "groq", "quota", cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    # A fixture that doesn't exist would normally raise UnmatchedRequestError,
    # not AvgError(kind=quota) - if replay mode ever ran the pre-flight
    # check, this would raise the wrong exception, wrongly.
    transport = Transport(fixtures_dir=tmp_path, mode="replay")
    with pytest.raises(UnmatchedRequestError):
        transport.request("POST", "https://api.groq.com/x", provider="groq")
```

`_SleepRecorder` already exists in this file — it's the class
`test_rate_limit_status_is_retried_per_policy_then_raises` already uses to
record `sleep(...)` calls (verified: `tests/providers/test_transport.py`'s
own top-of-file fixtures). Reuse it by its existing name.

Run these — most fail (no pre-flight check, no real wait time, no
recording yet); confirm `test_a_provider_not_in_cooldown_is_called_normally`
and `test_replay_mode_never_checks_or_touches_provider_health` might
already incidentally pass (nothing to check yet means nothing blocks) —
that's fine, they still assert the right thing once the feature exists.

### Step 4.2 — Implementation

Replace the import block:

```python
from avg.core.errors import AvgError
from avg.providers.errors import POLICY, classify
```

with:

```python
from avg.config import provider_health
from avg.core.errors import AvgError, ErrorKind
from avg.providers.errors import POLICY, classify, retry_after_seconds
```

Replace `request()`'s body from the replay check through where the timer
starts:

```python
        if self.mode == "replay":
            return self._replay(fixture_path, method_u, url)

        started = time.monotonic()
```

with:

```python
        if self.mode == "replay":
            return self._replay(fixture_path, method_u, url)

        if provider:
            cooldown = provider_health.active_cooldown(provider)
            if cooldown is not None:
                raise provider_health.cooldown_error(cooldown)

        started = time.monotonic()
```

Replace `_send_with_retry`'s body from the `except httpx.RequestError`
branch through its closing `raise AvgError(...)`:

```python
            except httpx.RequestError as exc:
                kind = classify(None, "", exc, provider)
                rule = POLICY[kind]
                if rule.retryable and attempt < rule.max_attempts:
                    self._sleep(rule.backoff_seconds * (2 ** (attempt - 1)))
                    attempt += 1
                    continue
                raise AvgError(kind, f"{provider or 'provider'} request failed: {exc}", detail=str(exc)) from exc

            if response.status_code >= 400:
                try:
                    body_text = response.text
                except Exception:
                    body_text = ""
                kind = classify(response.status_code, body_text, None, provider)
                rule = POLICY[kind]
                if rule.retryable and attempt < rule.max_attempts:
                    self._sleep(rule.backoff_seconds * (2 ** (attempt - 1)))
                    attempt += 1
                    continue
                raise AvgError(
                    kind,
                    f"{provider or 'provider'} request failed with HTTP {response.status_code}",
                    detail=body_text,
                )
```

with:

```python
            except httpx.RequestError as exc:
                kind = classify(None, "", exc, provider)
                rule = POLICY[kind]
                if rule.retryable and attempt < rule.max_attempts:
                    self._sleep(rule.backoff_seconds * (2 ** (attempt - 1)))
                    attempt += 1
                    continue
                if provider and kind in (ErrorKind.quota, ErrorKind.billing):
                    self._record_cooldown(provider, kind)
                raise AvgError(kind, f"{provider or 'provider'} request failed: {exc}", detail=str(exc)) from exc

            if response.status_code >= 400:
                try:
                    body_text = response.text
                except Exception:
                    body_text = ""
                kind = classify(response.status_code, body_text, None, provider)
                rule = POLICY[kind]
                if rule.retryable and attempt < rule.max_attempts:
                    wait = None
                    if kind == ErrorKind.rate_limit:
                        wait = retry_after_seconds(response.headers, body_text)
                        if wait is not None:
                            wait = min(wait, _rate_limit_wait_ceiling_seconds())
                    self._sleep(wait if wait is not None else rule.backoff_seconds * (2 ** (attempt - 1)))
                    attempt += 1
                    continue
                if provider and kind in (ErrorKind.quota, ErrorKind.billing):
                    self._record_cooldown(provider, kind)
                raise AvgError(
                    kind,
                    f"{provider or 'provider'} request failed with HTTP {response.status_code}",
                    detail=body_text,
                )
```

Then add two small helpers. Replace `_write_fixture`'s closing line
(the end of that method, right before the module-level `request()`
function) — i.e. replace:

```python
        fixture_path.write_text(json_lib.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def request(
```

with:

```python
        fixture_path.write_text(json_lib.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _record_cooldown(self, provider: str, kind: ErrorKind) -> None:
        """Persist a cooldown after a quota/billing failure. Best-effort -
        a failure to write cooldown state must not turn an already-
        classified provider failure into a different, more confusing one."""
        with suppress(Exception):
            if kind == ErrorKind.quota:
                provider_health.record_cooldown(
                    provider, "quota", cooldown_until=provider_health.default_quota_cooldown_until(provider)
                )
            else:
                provider_health.record_cooldown(provider, "billing")


def _rate_limit_wait_ceiling_seconds() -> float:
    """`resilience_rate_limit_wait_ceiling_sec` (a `SettingSpec`, default
    120). Read fresh on every call, not cached - this only runs on an
    actual rate-limit retry, not a hot path."""
    from avg.config.settings import load_settings  # noqa: PLC0415 - avoids importing settings at module load for every provider caller

    value = load_settings().values.get("resilience_rate_limit_wait_ceiling_sec", 120)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 120.0


def request(
```

Run Step 4.1's tests — all pass. Run the full `test_transport.py` file —
all existing tests still pass, including
`test_rate_limit_status_is_retried_per_policy_then_raises` (no
`Retry-After` header in that test's fixture responses, so `wait` resolves
to `None` and the exact same `rule.backoff_seconds * (2 ** (attempt - 1))`
formula still runs, unchanged).

**Commit:** `feat(providers): Transport pre-flight cooldown check + real Retry-After wait + cooldown recording`

---

## Task 5 — Edge TTS's own path

### Step 5.1 — Test

Add to `tests/providers/test_voice.py` (check the file's existing imports
and fixture/monkeypatch conventions for `EdgeVoiceProvider` first — it
already monkeypatches `voice_module.edge_tts.Communicate`/`.list_voices`
at the module level per that module's own docstring):

```python
from avg.config import provider_health


def test_edge_synthesize_skips_the_call_when_edge_is_in_cooldown(tmp_path, monkeypatch):
    monkeypatch.setattr(
        provider_health, "default_provider_health_path", lambda: tmp_path / "provider_health.json"
    )
    provider_health.record_cooldown("edge", "billing")  # contrived - see module note on why this is unlikely in practice

    provider = EdgeVoiceProvider()
    with pytest.raises(AvgError) as excinfo:
        provider.synthesize("hello", voice_id="en-US-GuyNeural", model="")
    assert excinfo.value.kind == ErrorKind.billing


def test_edge_synthesize_records_a_cooldown_on_a_classified_quota_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        provider_health, "default_provider_health_path", lambda: tmp_path / "provider_health.json"
    )
    monkeypatch.setattr("avg.providers.voice.providers.classify", lambda *a, **k: ErrorKind.quota)

    class _FakeCommunicate:
        def __init__(self, *a, **k):
            pass

        def stream_sync(self):
            raise RuntimeError("simulated failure")

    monkeypatch.setattr(voice_module.edge_tts, "Communicate", _FakeCommunicate)

    provider = EdgeVoiceProvider()
    with pytest.raises(AvgError):
        provider.synthesize("hello", voice_id="en-US-GuyNeural", model="")

    assert provider_health.is_in_cooldown("edge") is True
```

Run these — fail, no pre-flight check or recording exists in
`EdgeVoiceProvider` yet.

### Step 5.2 — Implementation

In `avg/providers/voice/providers.py`, replace the import block:

```python
from avg.config.secrets import get_key
from avg.core.errors import AvgError, ErrorKind
from avg.providers.errors import classify
from avg.providers.transport import Transport
from avg.providers.voice.base import raise_for_status
```

with:

```python
from avg.config import provider_health
from avg.config.secrets import get_key
from avg.core.errors import AvgError, ErrorKind
from avg.providers.errors import classify
from avg.providers.transport import Transport
from avg.providers.voice.base import raise_for_status
```

Replace `EdgeVoiceProvider.synthesize`'s body:

```python
    def synthesize(self, text: str, *, voice_id: str, model: str) -> bytes:
        """Return raw MP3 bytes for `text` spoken by `voice_id`.

        `model` is unused (see the section comment above) - accepted only so
        this provider satisfies `VoiceProvider.synthesize`'s shared
        signature.
        """
        del model

        def _collect() -> bytes:
            communicate = _edge().Communicate(text, voice_id)
            audio = bytearray()
            for chunk in communicate.stream_sync():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    audio.extend(chunk["data"])
            return bytes(audio)

        # `stream_sync()` is a blocking generator, not a coroutine, so it
        # can't be bounded with `asyncio.wait_for` - run it on a worker
        # thread instead and bound `Future.result` itself. A timed-out
        # thread is not killed (Python has no API for that) but is no
        # longer waited on, so this call returns promptly either way.
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            audio = pool.submit(_collect).result(timeout=_EDGE_SYNTHESIZE_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - re-classified immediately below
            kind = classify(None, "", exc, self.name)
            raise AvgError(kind, f"Edge TTS request failed: {exc}", detail=str(exc)) from exc
        finally:
            pool.shutdown(wait=False)

        if not audio:
            raise AvgError(ErrorKind.bug, "Edge TTS returned no audio data")
        return audio
```

with:

```python
    def synthesize(self, text: str, *, voice_id: str, model: str) -> bytes:
        """Return raw MP3 bytes for `text` spoken by `voice_id`.

        `model` is unused (see the section comment above) - accepted only so
        this provider satisfies `VoiceProvider.synthesize`'s shared
        signature.
        """
        del model

        cooldown = provider_health.active_cooldown(self.name)
        if cooldown is not None:
            raise provider_health.cooldown_error(cooldown)

        def _collect() -> bytes:
            communicate = _edge().Communicate(text, voice_id)
            audio = bytearray()
            for chunk in communicate.stream_sync():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    audio.extend(chunk["data"])
            return bytes(audio)

        # `stream_sync()` is a blocking generator, not a coroutine, so it
        # can't be bounded with `asyncio.wait_for` - run it on a worker
        # thread instead and bound `Future.result` itself. A timed-out
        # thread is not killed (Python has no API for that) but is no
        # longer waited on, so this call returns promptly either way.
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            audio = pool.submit(_collect).result(timeout=_EDGE_SYNTHESIZE_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - re-classified immediately below
            kind = classify(None, "", exc, self.name)
            if kind in (ErrorKind.quota, ErrorKind.billing):
                if kind == ErrorKind.quota:
                    provider_health.record_cooldown(
                        self.name, "quota", cooldown_until=provider_health.default_quota_cooldown_until(self.name)
                    )
                else:
                    provider_health.record_cooldown(self.name, "billing")
            raise AvgError(kind, f"Edge TTS request failed: {exc}", detail=str(exc)) from exc
        finally:
            pool.shutdown(wait=False)

        if not audio:
            raise AvgError(ErrorKind.bug, "Edge TTS returned no audio data")
        return audio
```

Replace `list_voices`'s body the same way — the exception branch:

```python
        try:
            voices = asyncio.run(_list_with_timeout())
        except Exception as exc:  # noqa: BLE001 - re-classified immediately below
            kind = classify(None, "", exc, self.name)
            raise AvgError(kind, f"Edge TTS voice list failed: {exc}", detail=str(exc)) from exc
```

with:

```python
        try:
            voices = asyncio.run(_list_with_timeout())
        except Exception as exc:  # noqa: BLE001 - re-classified immediately below
            kind = classify(None, "", exc, self.name)
            if kind in (ErrorKind.quota, ErrorKind.billing):
                if kind == ErrorKind.quota:
                    provider_health.record_cooldown(
                        self.name, "quota", cooldown_until=provider_health.default_quota_cooldown_until(self.name)
                    )
                else:
                    provider_health.record_cooldown(self.name, "billing")
            raise AvgError(kind, f"Edge TTS voice list failed: {exc}", detail=str(exc)) from exc
```

(`list_voices` gets the recording half only, not a pre-flight check — a
cooldown on `list_voices` specifically has no real caller today, and the
one that matters, `synthesize`, already has the pre-flight check above;
keeping both call sites' *recording* symmetrical costs nothing and means
neither one can silently miss a real failure if `edge`'s Microsoft
endpoint ever does start rate-limiting by IP, as the spec's own §3.C notes
is the one realistic case this matters for.)

Run Step 5.1's tests — pass. Run the full `test_voice.py` file — all
existing tests still pass.

**Commit:** `feat(providers): wire Edge TTS's own path into provider_health, same as Transport`

---

## Task 6 — `health.py` safeguard row

### Step 6.1 — Test

**Verified against the real test file before writing this:**
`_safeguard_checks` has no existing direct-call test precedent in
`tests/core/test_health.py` to mirror — an earlier draft of this plan
assumed one existed. The file's own established pattern instead goes
through the public `report()` (see that file's existing
`test_the_report_has_four_stages_only_when_asked`,
`settings_path`/`channels_dir`/`output_root` all pointed at `tmp_path`, no
`provider_health_path` parameter exists on `report()` so the new module's
own `path=`/monkeypatch mechanism is what isolates it instead) and reads
the `"safeguards"`-keyed `Stage` out of the returned list. Add to
`tests/core/test_health.py`:

```python
from avg.config import provider_health


def test_provider_cooldowns_check_is_ok_when_nothing_is_in_cooldown(tmp_path, monkeypatch):
    from avg.core.health import report

    monkeypatch.setattr(
        provider_health, "default_provider_health_path", lambda: tmp_path / "provider_health.json"
    )
    result = report(settings_path=tmp_path / "s.json", channels_dir=tmp_path / "c", output_root=tmp_path)
    safeguards = next(s for s in result if s.key == "safeguards")
    row = next(c for c in safeguards.checks if c.name == "Provider cooldowns")
    assert row.ok is True
    assert row.required is False


def test_provider_cooldowns_check_lists_active_ones(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from avg.core.health import report

    monkeypatch.setattr(
        provider_health, "default_provider_health_path", lambda: tmp_path / "provider_health.json"
    )
    provider_health.record_cooldown(
        "groq", "quota", cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1)
    )

    result = report(settings_path=tmp_path / "s.json", channels_dir=tmp_path / "c", output_root=tmp_path)
    safeguards = next(s for s in result if s.key == "safeguards")
    row = next(c for c in safeguards.checks if c.name == "Provider cooldowns")
    assert row.ok is False
    assert "groq" in row.detail


def test_provider_cooldowns_check_never_raises_even_if_the_file_is_corrupt(tmp_path, monkeypatch):
    from avg.core.health import report

    path = tmp_path / "provider_health.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(provider_health, "default_provider_health_path", lambda: path)

    result = report(settings_path=tmp_path / "s.json", channels_dir=tmp_path / "c", output_root=tmp_path)
    safeguards = next(s for s in result if s.key == "safeguards")
    row = next(c for c in safeguards.checks if c.name == "Provider cooldowns")
    assert row.ok is True
```

Run these — fail, the row doesn't exist yet.

### Step 6.2 — Implementation

In `avg/core/health.py`, replace `_safeguard_checks`'s `Provider keys`
block through the function's `return`:

```python
    try:
        from avg.config.secrets import has_key

        # Reports only WHETHER a key is set - never a value. Same rule the
        # /api/keys route follows; see `avg/config/secrets.py`.
        configured = [name for name in ("groq", "gemini", "elevenlabs", "assemblyai") if has_key(name)]
        checks.append(
            Component(
                name="Provider keys",
                # Text generation is the one thing nothing can substitute
                # for, so a run needs at least one text provider keyed.
                ok=any(name in configured for name in ("groq", "gemini")),
                version=f"{len(configured)} set",
                detail=", ".join(configured) if configured else "no provider keys configured",
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(Component(name="Provider keys", ok=False, detail=str(exc)))

    return checks
```

with:

```python
    try:
        from avg.config.secrets import has_key

        # Reports only WHETHER a key is set - never a value. Same rule the
        # /api/keys route follows; see `avg/config/secrets.py`.
        configured = [name for name in ("groq", "gemini", "elevenlabs", "assemblyai") if has_key(name)]
        checks.append(
            Component(
                name="Provider keys",
                # Text generation is the one thing nothing can substitute
                # for, so a run needs at least one text provider keyed.
                ok=any(name in configured for name in ("groq", "gemini")),
                version=f"{len(configured)} set",
                detail=", ".join(configured) if configured else "no provider keys configured",
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(Component(name="Provider keys", ok=False, detail=str(exc)))

    try:
        from avg.config import provider_health

        # An active cooldown is informative, not a broken install (see
        # Component.required's own docstring) - other providers may still
        # work, so this never blocks a run the way "Provider keys" can.
        active = []
        for name in ("groq", "gemini", "elevenlabs", "assemblyai"):
            cooldown = provider_health.active_cooldown(name)
            if cooldown is not None:
                active.append(f"{name}{provider_health.cooldown_message_suffix(cooldown)}")
        checks.append(
            Component(
                name="Provider cooldowns",
                ok=not active,
                required=False,
                detail="; ".join(active) if active else "none active",
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(Component(name="Provider cooldowns", ok=True, required=False, detail=f"could not check: {exc}"))

    return checks
```

(This check's own `except` branch reports `ok=True` on failure, not
`ok=False` — deliberately: a check that can't even run has no evidence of
an active cooldown, and defaulting to "there's a problem" on its own
internal error would be a false alarm about the wrong thing.)

Run Step 6.1's tests — pass. Run the full `test_health.py` file — all
existing tests still pass, including any that assert the exact list of
`Component` names returned by `_safeguard_checks` (extend such an
assertion's expected set to include `"Provider cooldowns"` if one exists —
check for it before running).

**Commit:** `feat(health): add Provider cooldowns safeguard check`

---

## Task 7 — Surface it in Visuals' fallback message

### Step 7.1 — Test

**Verified against the real test file before writing this:** there is no
existing test asserting on the literal `"Falling back to"` text (an
earlier draft of this plan assumed one existed to extend). The real
integration point is `tests/pipeline/test_phases_media.py`'s
"cross-provider fallback actually wired into run()" section —
`_fallback_settings()`, `_FakeImageProvider`, `_project`/`_ctx`/`_channel`/
`_write_scenes` — used by `test_the_backup_runs_once_when_the_primary_
fails_every_scene` and its neighbors. `_FakeImageProvider` always raises
`AvgError(ErrorKind.network, ...)` (hardcoded) for its `fail_prompts`, so
a quota-classified failure needs its own small fake — and since that fake
raises `AvgError` directly, it never goes through `Transport`, so it never
calls `_record_cooldown` (Task 4) either. That's not a gap in this test:
Task 4's own tests already prove Transport records a cooldown before
raising; this test only has to prove visuals.py *reads one back
correctly* once it exists — exactly what a real quota failure through a
real provider would already have left behind by the time this code runs —
so the cooldown is seeded directly with `provider_health.record_cooldown`
rather than produced indirectly through a fake that cannot produce it.
Add to `tests/pipeline/test_phases_media.py`:

```python
from avg.config import provider_health


class _FakeQuotaFailingProvider:
    """Like `_FakeImageProvider` but raises a quota-classified AvgError,
    to exercise the cooldown-detail path in the fallback message."""

    name = "primary"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def generate(self, prompt: str, *, aspect_ratio: str, model: str) -> bytes:
        self.calls.append((prompt, aspect_ratio, model))
        raise AvgError(ErrorKind.quota, "simulated quota exhaustion")


def test_fallback_message_includes_cooldown_detail_on_quota_failure(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(
        provider_health, "default_provider_health_path", lambda: tmp_path / "provider_health.json"
    )
    provider_health.record_cooldown(
        "primary", "quota", cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1)
    )

    primary = _FakeQuotaFailingProvider()
    backup = _FakeImageProvider()
    providers = {"primary": primary, "backup": backup}
    monkeypatch.setattr(
        "avg.pipeline.phases.visuals._build_provider",
        lambda name, transport: providers[name],
    )

    project = _project(tmp_path)
    ctx, events = _ctx(project, channel=_channel(_fallback_settings()))
    _write_scenes(ctx, ["prompt one", "prompt two"])

    VisualsPhase().run(ctx)

    messages = [e.message for e in events if isinstance(e, ProgressEvent)]
    fallback_message = next(m for m in messages if m.startswith("Falling back to"))
    assert "quota" in fallback_message
```

(`events` is `_ctx`'s own second return value — the list every `ctx.emit`
call in this test file already appends to, per that helper's existing
signature; not `ctx.emitted`, which does not exist.)

Run it — fails, no cooldown detail in the message yet.

### Step 7.2 — Implementation

**Verified against the real file before writing this block.** The exact
current anchor (`avg/pipeline/phases/visuals.py`) — and, usefully,
`primary_provider_name` already exists as a local variable, captured
*before* the backup reassignment further down, for a different purpose
(`primary_kind_label`) — so no new capture needs introducing:

```python
                ctx.emit(
                    ProgressEvent(
                        project_id=ctx.project.id,
                        percent=0,
                        message=f"Falling back to {backup_provider}/{backup_model}...",
                    )
                )
```

Replace with:

```python
                cooldown_detail = ""
                if kind in (ErrorKind.quota, ErrorKind.billing):
                    cooldown_detail = provider_health.cooldown_message_suffix(
                        provider_health.active_cooldown(primary_provider_name)
                    )
                ctx.emit(
                    ProgressEvent(
                        project_id=ctx.project.id,
                        percent=0,
                        message=f"Falling back to {backup_provider}/{backup_model}...{cooldown_detail}",
                    )
                )
```

And the import block (`ErrorKind` is already imported here — only
`provider_health` is new). Replace:

```python
from avg.core.errors import AvgError, ErrorKind, report_degradation
```

with:

```python
from avg.config import provider_health
from avg.core.errors import AvgError, ErrorKind, report_degradation
```

Run Step 7.1's test — passes. Run the full `test_phases_media.py` file —
all existing tests still pass.

**Mechanically verified, all 7 tasks together, in one real interpreter
run** — not just reasoned about: applied to a scratch copy of this exact
codebase, in order, each task's tests run immediately after its own code
landed, using this project's own Linux dev-sandbox convention
(`sitecustomize.py` no-oping `ctypes.WinDLL`/`ctypes.wintypes` — the
existing shim only covered `WinDLL`; `wintypes` needed adding too, a real
gap found running this, not assumed). Final counts: `test_provider_
health.py` 15/15, `test_errors.py` 60/60 (49 pre-existing + 11 new),
`test_transport.py` 30/30 (19 pre-existing + 11 new), `test_voice.py`
14/14 (12 pre-existing + 2 new, after installing the `edge-tts` package
this sandbox's minimal venv didn't have), `test_health.py` 18/19 (one
pre-existing failure, `test_no_secret_value_appears_in_a_deep_check` —
confirmed failing identically on the unmodified checkout too: this
sandbox's DPAPI shim doesn't correctly emulate `CryptProtectData`'s
pointer-based calling convention, the same open regression already
tracked at the top of `.superpowers/sdd/progress.md`, orthogonal to this
plan), `test_phases_media.py` 51/52 (one pre-existing failure in this
sandbox's minimal venv, unrelated to any of these 7 tasks — confirmed
failing identically on the unmodified checkout). Three real mistakes this
process caught before they could reach the real plan: Task 1's anchor
text (guessed a verbose multi-line `SettingSpec` shape; the real
`group="keys"` convention is a compact one-liner, and `assemblyai`'s real
default is `None`, not `""`), Task 4's test-helper class name
(`_SleepRecorder`, not `_RecordingSleeper`), and Tasks 6-7's assumed test
entry points (`_safeguard_checks` has no direct-call precedent; the
fallback message has no existing test to extend) — all three would have
made an implementer's first attempt at this plan fail immediately on a
mismatched anchor or an `ImportError`, exactly the failure mode this
project's own "mechanically apply every replace-block" rule exists to
catch before a plan ships, not after.

**Commit:** `feat(visuals): fallback message names the cooldown when the primary provider is quota/billing-exhausted`

---

## Manual verification (after all 7 tasks)

1. `.venv-linux/bin/python -m pytest tests -q` (or the Windows equivalent)
   — zero regressions vs. the ledger's documented baseline (per this
   project's own rule: that means the *same* pre-existing failures, if
   any, not necessarily zero failures).
2. `mypy` — clean on every file this plan touched.
3. Read back `provider_health.json` after a deliberately-forced quota
   failure in a real (or `AVG_HTTP_RECORD=1`) run, confirming the file's
   shape matches the spec's example and that a second run within the
   cooldown window genuinely skips the provider (visible in the run log
   / Check Health's new row) rather than calling it again.

## Out of scope (matches the spec)

- Per-provider documented reset windows beyond the one configurable
  fallback.
- Any change to ElevenLabs' own primary/backup key-switch logic.
- Surfacing through `avg/providers/text/router.py`'s callers or
  `avg/pipeline/phases/voice.py` — neither has an existing fallback
  message to extend; real follow-up work, not part of this plan.
- Sub-slices 2-4 — separate plans.

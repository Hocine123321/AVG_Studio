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

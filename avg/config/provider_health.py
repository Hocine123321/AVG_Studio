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

Granularity is per-provider, keyed by whichever credential actually failed
- not per-provider-name alone. That distinction exists because of a real
bug: `avg.providers.voice.providers.ElevenLabsVoiceProvider` retries a
`quota`/`auth` failure once against a *different* key (its configured
backup) under the *same* provider name. An earlier version of this module
recorded cooldowns by provider name only, so a primary-key quota failure
put "elevenlabs" in cooldown and the very next call - the backup-key retry,
milliseconds later, same `synthesize()` call - was blocked by this
module's own pre-flight check before it ever reached the network, even
though the backup is a different account with its own quota. `Transport`
now passes `key_fingerprint=fingerprint_headers(...)` computed from the
actual request headers (whichever credential header is present -
`xi-api-key`, `x-goog-api-key`, `apikey`, or a `Bearer `-stripped
`Authorization`) at both record and check time, so a cooldown recorded
against one credential's fingerprint does not block a request carrying a
different one. Callers that never pass `key_fingerprint` (this module's
other, older callers - `avg.core.health`'s summary check,
`avg.pipeline.phases.visuals`'s message-suffix lookup, `EdgeVoiceProvider`,
which has no separate-key concept at all) keep exactly the behaviour they
had before this fix: quota expires on its own timer, billing clears when
`avg.config.secrets.get_key(provider)`'s current value stops matching what
was fingerprinted at write time.

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


_CREDENTIAL_HEADER_NAMES = ("xi-api-key", "x-goog-api-key", "apikey", "authorization")


def fingerprint_headers(headers: object) -> str | None:
    """Fingerprint of whichever credential-bearing header is present in a
    request's headers, or `None` if none of the known header names are
    present. `None` means "this request carries no header this function
    recognises as a credential" - the caller should then skip the
    per-credential distinction entirely (fall back to the provider-name-
    only behaviour), not treat it as "definitely no credential", since a
    header name this app hasn't seen yet is a gap in this list, not a
    signal.

    `authorization` has its `Bearer ` prefix stripped before fingerprinting
    so that hashing a header value here and hashing a raw key elsewhere
    (`avg.config.secrets.get_key`) agree when they hold the same secret.
    """
    if not headers or not isinstance(headers, dict):
        return None
    lower = {str(k).lower(): v for k, v in headers.items()}
    for name in _CREDENTIAL_HEADER_NAMES:
        value = lower.get(name)
        if not value:
            continue
        value = str(value)
        if name == "authorization" and value.lower().startswith("bearer "):
            value = value[len("bearer ") :]
        return _fingerprint(value)
    return None


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


def _entries_for(providers: dict[str, dict[str, object]], provider: str) -> list[dict[str, object]]:
    """Every stored entry for `provider`, one per credential that has failed.

    Entries are stored under `"<provider>:<fingerprint>"` with the provider
    repeated inside the entry; a legacy entry (a bare provider-name key from
    before cooldowns were per-credential) has no such field and is matched by
    its key instead, so an existing file keeps working.
    """
    wanted = _provider_key(provider)
    found: list[dict[str, object]] = []
    for storage_key, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("provider", storage_key.split(":", 1)[0])) == wanted:
            found.append(entry)
    return found


def _configured_fingerprints(provider: str) -> set[str]:
    """Fingerprints of the keys currently configured for `provider` - its
    own, and the `<provider>_backup` slot `avg.config.secrets` also knows."""
    return {_fingerprint(get_key(provider)), _fingerprint(get_key(f"{provider}_backup"))}


def record_cooldown(
    provider: str,
    status: CooldownStatus,
    *,
    cooldown_until: datetime | None = None,
    note: str = "",
    key_fingerprint: str | None = None,
    path: Path | None = None,
) -> None:
    """Persist a cooldown for one credential of `provider`.

    One entry per (provider, credential), never one per provider: a provider
    with a primary and a backup key (ElevenLabs) can have BOTH exhausted, and
    a single per-provider entry let each key's failure overwrite the other's,
    so neither ever blocked a call. Re-recording the same credential replaces
    its own entry.

    `key_fingerprint` (`Transport` passes `fingerprint_headers(headers)`)
    names which credential failed. When omitted it falls back to fingerprinting
    `avg.config.secrets.get_key(provider)` - correct for any single-key
    provider.
    """
    name = _provider_key(provider)
    fingerprint = key_fingerprint if key_fingerprint is not None else _fingerprint(get_key(provider))
    with _LOCK:
        providers = _load(path)
        providers[f"{name}:{fingerprint}"] = {
            "provider": name,
            "status": status,
            "since": datetime.now(timezone.utc).isoformat(),
            "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
            "note": note,
            "key_fingerprint": fingerprint,
        }
        providers.pop(name, None)  # a legacy bare-name entry is superseded
        _save(providers, path)


def active_cooldown(
    provider: str, *, key_fingerprint: str | None = None, path: Path | None = None
) -> ProviderCooldown | None:
    """The active cooldown for `provider`, or `None` if there isn't one.

    `key_fingerprint` given (`Transport`'s pre-flight check passes the
    credential its request is about to use): only that credential's own entry
    counts, so one key's cooldown never blocks another's. Omitted (summary
    callers - the health row, the Visuals fallback message - which have no
    request in hand): the most recent active entry across all of the
    provider's credentials.

    A `quota` entry expires on its own once `cooldown_until` passes. A
    `billing` entry has no timer: with a `key_fingerprint` it stays active
    while that credential is the one in use; without one, it counts only
    while the credential it was recorded against is still configured (so
    replacing the key clears it, including "the key was removed").
    """
    with _LOCK:
        entries = _entries_for(_load(path), provider)

    now = datetime.now(timezone.utc)
    best: ProviderCooldown | None = None
    for entry in entries:
        status = entry.get("status")
        if status not in ("quota", "billing"):
            continue
        if key_fingerprint is not None and entry.get("key_fingerprint", "") != key_fingerprint:
            continue
        try:
            since = datetime.fromisoformat(str(entry.get("since")))
        except (TypeError, ValueError):
            continue
        cooldown_until: datetime | None = None
        raw_until = entry.get("cooldown_until")
        if raw_until:
            try:
                cooldown_until = datetime.fromisoformat(str(raw_until))
            except (TypeError, ValueError):
                cooldown_until = None
        if status == "quota":
            if cooldown_until is not None and now >= cooldown_until:
                continue
        elif key_fingerprint is None and entry.get("key_fingerprint", "") not in _configured_fingerprints(provider):
            continue
        candidate = ProviderCooldown(
            provider=provider,
            status=status,
            since=since,
            cooldown_until=cooldown_until,
            note=str(entry.get("note", "")),
        )
        if best is None or candidate.since > best.since:
            best = candidate
    return best


def is_in_cooldown(provider: str, *, key_fingerprint: str | None = None, path: Path | None = None) -> bool:
    """Convenience wrapper: `True` iff `active_cooldown` is not `None`."""
    return active_cooldown(provider, key_fingerprint=key_fingerprint, path=path) is not None


def clear_cooldown(provider: str, *, path: Path | None = None) -> None:
    """Remove every credential's entry for `provider`. A no-op if none."""
    wanted = _provider_key(provider)
    with _LOCK:
        providers = _load(path)
        keep = {k: e for k, e in providers.items() if e not in _entries_for(providers, wanted)}
        if len(keep) != len(providers):
            _save(keep, path)


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

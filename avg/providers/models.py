"""The live model catalogue: fetch when possible, cache to disk, fall back
to the cache when the API can't be reached.

This module owns exactly that mechanism and nothing else - it has no idea
what a "Groq model" or a "Gemini model" looks like. Each text backend
supplies its own `fetch_live` closure (the provider-specific endpoint, auth,
and response parsing live there, per `avg/providers/text/*.py`); this module
only decides what to do with the result: cache a non-empty success, or fall
back to whatever was cached last time.

The old PowerShell app kept small hardcoded fallback catalogues alongside
its live-fetch functions (`Get-FallbackModelCatalog`, since deleted) and
`settings_menu.ps1` had its own hardcoded lists too - both were removed
deliberately (B2.19.4, see `ai.ps1`'s "AI Model Catalog" section header
comment) because a hardcoded list silently drifts out of date the moment a
provider retires or renames a model. That decision stands here: the only
fallback this module ever returns is the *previous live fetch*, never a
literal baked into source.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Callable


def default_cache_path() -> Path:
    """`%APPDATA%/AVG Studio/model_cache.json`.

    Falls back to `~/AppData/Roaming` if `APPDATA` is somehow unset,
    mirroring `avg.config.settings.default_settings_path` and
    `avg.config.secrets.default_secrets_path`.
    """
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "AVG Studio" / "model_cache.json"


def _load_cache(path: Path) -> dict[str, list[str]]:
    """Read the on-disk `{provider: [model_id, ...]}` map.

    A missing file, unreadable JSON, or a malformed shape all yield `{}`
    rather than raising - a broken or absent cache must mean "nothing
    cached yet", not a crash, matching `avg.config.secrets._load_store`'s
    treatment of its own store file.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    cache: dict[str, list[str]] = {}
    for provider, models in raw.items():
        if isinstance(provider, str) and isinstance(models, list) and all(isinstance(m, str) for m in models):
            cache[provider] = models
    return cache


def _save_cache(cache: dict[str, list[str]], path: Path) -> None:
    """Write the cache atomically (temp file + `os.replace`), matching
    `avg.config.settings.save_settings` / `avg.config.secrets._save_store`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".model_cache-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
        os.replace(tmp_name, path)
    except BaseException:
        with suppress(OSError):
            os.remove(tmp_name)
        raise


def get_models(
    provider: str,
    fetch_live: Callable[[], list[str]],
    *,
    cache_path: Path | str | None = None,
) -> list[str]:
    """Return `provider`'s model list: live if reachable, cache otherwise.

    Calls `fetch_live()`. Any exception it raises (a network error, an
    `AvgError` from a missing key, `Transport`'s own `UnmatchedRequestError`
    in a replay-mode test, ...) is treated identically to "the API is
    unreachable right now" - this function itself never inspects *why*
    the live fetch failed, it only decides what to serve instead.

    A non-empty live result is written to the cache (so the *next*
    unreachable moment has something to fall back to) and returned as-is.
    An empty or failed live fetch falls back to whatever `provider` last
    resolved to in the cache - `[]` if there is no prior entry. The cache
    is never seeded with a hardcoded list; a fresh install with no
    connectivity yet simply has nothing to offer until one live fetch
    succeeds.
    """
    path = Path(cache_path) if cache_path is not None else default_cache_path()

    try:
        live = fetch_live()
    except Exception:
        live = []

    if live:
        cache = _load_cache(path)
        cache[provider] = live
        _save_cache(cache, path)
        return live

    return _load_cache(path).get(provider, [])

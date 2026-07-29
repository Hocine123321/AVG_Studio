"""Which models a provider actually answered on, last time we asked.

Ported from the legacy app's `config/working_models_cache.json`
(`D:\\AI_Video_Gen\\modules\\visuals.ps1`, `Invoke-WorkingModelsCacheUpdate`),
same document shape:

    {"updated": "<iso8601>", "models": {"<provider>": {"<model id>": bool}}}

**The rule inherited verbatim, because the legacy app learned it the hard
way:** only models a probe PROVED working are ever returned, and a provider
with nothing proven returns an empty list rather than a hardcoded
catalogue. The old app's built-in model list went stale and it became
impossible to tell what had actually been verified - so an empty result has
to read as "build your cache", never as a list of guesses.

Not to be confused with `avg/providers/models.py`, which caches a
provider's *advertised* catalogue (the list endpoint's answer, so the app
still has something to offer when that endpoint is unreachable). This file
records the much smaller set that survived a real generation call. One is
"what the provider says exists", the other is "what worked".

Storage only. Probing lives in `avg.providers.probe`; nothing here touches
the network, and nothing here imports from `avg.api`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class ModelCache:
    """Read/write access to one working-models document."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _load(self) -> dict:
        """The whole document, or `{}`.

        A missing file, unreadable JSON or a malformed shape all mean
        "nothing probed yet" rather than an exception - matching
        `avg.providers.models._load_cache` and `avg.core.error_store`. A
        broken cache must never be the thing that stops the app.
        """
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def working(self, provider: str) -> list[str]:
        """Model ids this provider proved working, sorted.

        Empty when unprobed. Never a guess - see the module docstring.
        """
        models = self._load().get("models", {})
        if not isinstance(models, dict):
            return []
        entry = models.get(provider)
        if not isinstance(entry, dict):
            return []
        return sorted(model for model, ok in entry.items() if ok is True)

    def providers(self) -> list[str]:
        """Every provider with an entry, probed or not."""
        models = self._load().get("models", {})
        return sorted(models) if isinstance(models, dict) else []

    def updated_at(self) -> datetime | None:
        """When the last sweep finished, or `None` if there has never been one."""
        raw = self._load().get("updated")
        if not isinstance(raw, str):
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def save(self, results: dict[str, dict[str, bool]]) -> None:
        """Replace the document with one sweep's results.

        A whole-document replace, not a merge: a model that stopped
        working has to be able to disappear, and a merge would keep a
        stale `true` alive forever.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "updated": datetime.now(timezone.utc).isoformat(),
                    "models": {p: dict(sorted(m.items())) for p, m in results.items()},
                },
                indent=2,
            ),
            encoding="utf-8",
        )

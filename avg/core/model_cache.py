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

    def verdicts(self, provider: str) -> dict[str, bool]:
        """Every verdict this provider's last sweep recorded, `False` included.

        `working()` answers "what can I pick today" and therefore drops the
        failures. This answers "what did the sweep prove", which is what a
        screen reporting `working` / `failed` / `untested` needs: an absent
        entry is untested, and a `False` entry is a real, earned failure.
        """
        models = self._load().get("models", {})
        if not isinstance(models, dict):
            return {}
        entry = models.get(provider)
        if not isinstance(entry, dict):
            return {}
        return {str(model): bool(ok) for model, ok in entry.items()}

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

    def meta(self) -> dict[str, dict[str, str]]:
        """Descriptive detail for entries that have some, by entry id.

        Voices carry language and gender; a model id carries nothing, so
        most entries are simply absent here. It lives in this document
        rather than a fourth file because it is the same kind of thing as
        the rest of it: machine-owned, derived from a provider's own list,
        and replaced wholesale on the next sweep.
        """
        raw = self._load().get("meta")
        if not isinstance(raw, dict):
            return {}
        return {
            str(key): {str(k): str(v) for k, v in value.items()}
            for key, value in raw.items()
            if isinstance(value, dict)
        }

    def reach(self) -> dict[str, bool]:
        """Whether each provider answered its own catalogue, by `kind/provider`.

        Reachability is a fact about a PROVIDER, not about a model - an
        image provider that answers its catalogue has proved the key works
        and the endpoint is up, and has proved nothing at all about any one
        model. Storing it per model would be a claim the check never made.

        A provider absent from this map is UNTESTED, which is a different
        statement from `False`. Nothing here ever writes an absent provider
        as `False`.
        """
        raw = self._load().get("reachable")
        if not isinstance(raw, dict):
            return {}
        return {str(key): bool(value) for key, value in raw.items()}

    def save(
        self,
        results: dict[str, dict[str, bool]],
        meta: dict[str, dict[str, str]] | None = None,
        reachable: dict[str, bool] | None = None,
    ) -> None:
        """Replace the document with one sweep's results.

        A whole-document replace, not a merge: a model that stopped
        working has to be able to disappear, and a merge would keep a
        stale `true` alive forever.

        `meta` and `reachable` are the exceptions, and only in one
        direction: passing `None` keeps whatever was stored, so a text
        probe - which knows nothing about voices or image endpoints - does
        not wipe the language and gender a catalogue refresh put there, nor
        the reachability an earlier provider test proved. Passing an
        explicit `{}` still clears them.
        """
        kept_meta = self.meta() if meta is None else meta
        kept_reach = self.reach() if reachable is None else reachable
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "updated": datetime.now(timezone.utc).isoformat(),
                    "models": {p: dict(sorted(m.items())) for p, m in results.items()},
                    "meta": dict(sorted(kept_meta.items())),
                    "reachable": dict(sorted(kept_reach.items())),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

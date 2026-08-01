"""Ask a text model which models and voices are worth shortlisting.

**What is grounded and what is not.** The model is sent the catalogue this
app actually holds, so it chooses from real entries rather than recalling
names - and every id it returns is validated back against that catalogue,
because a model inventing a plausible id is the failure this exists to
catch. The TIER and the REASON are its opinion: `avg.core.costs` holds
per-PHASE prices and there is no per-model price anywhere in this codebase,
so a cost ranking is recollection from training data for a market that
changes without notice.

That is why a suggestion is stored in `model_prefs.json` beside the user's
own ticks and never in `working_models.json`, and why the Live List chips it
`Suggested` rather than `Verified`. `avg.core.model_cache`'s rule is
inherited verbatim: only models a probe PROVED working are ever reported as
working, and an empty result reads as "build your cache", never as a list of
guesses.

Voices carry no tier. They have no cost axis, so a tier there would be a
meaningless value dressed as information.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

#: Cost tiers, for text and image only. The model's claim, never a measured
#: price - see the module docstring.
TIERS: tuple[str, ...] = ("cheap", "balanced", "high", "ultra")


@dataclass(frozen=True, slots=True)
class Suggestion:
    """One recommendation. `tier` is `None` for voices, and for any tier the
    model returned that is not one of `TIERS` - an unrecognised tier is
    dropped rather than passed through as though it meant something."""

    entry_id: str
    kind: str
    tier: str | None
    reason: str


def _generate(prompt: str, config: Any) -> Any:
    """The one call out. A seam so tests inject a reply instead of spending
    one, and so this module holds no HTTP of its own."""
    from avg.providers.text import router  # noqa: PLC0415

    return router.generate(prompt, "brief", config=config)


def _parse(raw: str, allowed: set[str], *, tiers_allowed: bool) -> list[Suggestion]:
    """Validated suggestions out of a model's reply.

    Anything not parseable yields nothing rather than raising: a malformed
    reply is a suggestion run that produced nothing, which the screen can
    state, and never an exception reaching the route.
    """
    text = raw.strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
    except Exception:  # noqa: BLE001 - an unparseable reply is simply no suggestions
        return []
    if not isinstance(parsed, list):
        return []

    out: list[Suggestion] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        entry_id = str(item.get("entry_id", "")).strip()
        # The guard that matters: a model may only recommend what it was
        # shown. An invented id would otherwise reach the Live List looking
        # exactly like a real one.
        if entry_id not in allowed:
            continue
        raw_tier = str(item.get("tier", "")).strip().lower()
        tier = raw_tier if (tiers_allowed and raw_tier in TIERS) else None
        out.append(
            Suggestion(
                entry_id=entry_id,
                kind=str(item.get("kind", "")).strip() or "text",
                tier=tier,
                reason=str(item.get("reason", "")).strip(),
            )
        )
    return out


def suggest_models(catalogue: dict[str, list[str]], *, config: Any) -> list[Suggestion]:
    """Text and image models, in one call - they share a cost axis.

    `catalogue` is `{"text": [...], "image": [...]}` of `provider/model` ids.
    """
    text_ids = list(catalogue.get("text", []))
    image_ids = list(catalogue.get("image", []))
    allowed = set(text_ids) | set(image_ids)
    if not allowed:
        return []

    prompt = (
        "You are helping choose AI models for a short-form video generator.\n"
        "Pick at most four TEXT models and at most four IMAGE models from the "
        "lists below, one for each cost tier where you can.\n"
        f"Cost tiers: {', '.join(TIERS)}.\n\n"
        f"TEXT MODELS:\n{chr(10).join(text_ids)}\n\n"
        f"IMAGE MODELS:\n{chr(10).join(image_ids)}\n\n"
        "Reply with ONLY a JSON array, no prose. Each element: "
        '{"entry_id": "<exactly one id from the lists above>", '
        '"kind": "text" or "image", '
        f'"tier": one of {list(TIERS)}, '
        '"reason": "<one short sentence>"}'
    )
    return _parse(_generate(prompt, config).text, allowed, tiers_allowed=True)


def suggest_voices(catalogue: list[str], *, config: Any) -> list[Suggestion]:
    """Voices, in their own call. No cost axis, so no tier is asked for."""
    allowed = set(catalogue)
    if not allowed:
        return []

    prompt = (
        "You are helping choose narration voices for a short-form video generator.\n"
        "Pick at most four voices from the list below - the best for clear, "
        "engaging narration.\n\n"
        f"VOICES:\n{chr(10).join(sorted(allowed))}\n\n"
        "Reply with ONLY a JSON array, no prose. Each element: "
        '{"entry_id": "<exactly one id from the list above>", '
        '"kind": "voice", "reason": "<one short sentence>"}'
    )
    return _parse(_generate(prompt, config).text, allowed, tiers_allowed=False)

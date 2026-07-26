"""Parses `personality.txt` presets into the text prompt builders inject.

The legacy PowerShell app kept the tuned "voice" of every video in one flat
file (`D:\\AI_Video_Gen\\Assets\\personality.txt`, rewritten B2.19.10 into
three prose-first presets: `documentary`, `news_report`, `zack_d_films`,
each with a `## PRESET: <name>` header, a `## DESCRIPTION: <one-line
summary>` line, and a body of writing instructions). That file is copied
verbatim into `avg/assets/personality.txt` in this repo (see this module's
`assets_dir` for why a *copy* rather than a read of `D:\\AI_Video_Gen` at
runtime) rather than paraphrased - the phrasing in it is tuned copy, not
boilerplate, and it is the product this task exists to preserve.

This module's one job: turn that file into `PersonalityPreset` objects and
serve them by name. It does not build prompt strings (that is
`avg.pipeline.prompts`'s job) and it does not know what `content_shape`
means to the rest of the app - a preset's `name` simply IS the
`content_shape` value a channel stores.

Degrading, never raising: `get_preset` mirrors the legacy
`Get-PersonalityPresetBody` contract exactly, because that contract was
added after a real regression - an unrecognised or renamed preset name
used to silently fall through to an EMPTY persona, which produced generic,
voiceless scripts with no visible error. `get_preset` instead degrades to
the first preset in the file (a project written before a preset was
renamed/removed still gets a full, real persona), and if the file itself
is missing or unparseable, degrades further to a small built-in generic
preset - a prompt can always be built, even in a broken install.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_ASSET_NAME = "personality.txt"

# The legacy app's ultimate fallback when personality.txt itself is missing
# (`Get-PersonalityPresetBody`'s caller: "personality.txt not found - using
# built-in default"). Kept as a last resort here for the same reason: a
# prompt must always be buildable, never raise for a broken install.
_GENERIC_PRESET_NAME = "generic"
_GENERIC_BODY = "You are a video script writer. Follow ALL instructions exactly."

# One preset block: `## PRESET: <name>` then (optionally, on the very next
# line) `## DESCRIPTION: <text>`, then everything up to the next `## PRESET:`
# or end of file. DOTALL so `.*?` in `body` can span multiple lines;
# MULTILINE so `^` anchors each line, not just the start of the whole file
# (the legacy app shipped a bug where, without re.MULTILINE, every preset
# after the first silently failed to match and the caller fell back to the
# entire file as one giant "preset").
_PRESET_RE = re.compile(
    r"^##\s*PRESET:\s*(?P<name>\S+)\s*\r?\n"
    r"(?:##\s*DESCRIPTION:\s*(?P<description>.*?)\r?\n)?"
    r"(?P<body>.*?)"
    r"(?=^##\s*PRESET:|\Z)",
    re.MULTILINE | re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class PersonalityPreset:
    """One parsed preset.

    `body` never contains the `## DESCRIPTION:` line - that line is a short
    catalogue-style summary for humans (settings UI, this module's own
    fallback logic), not a writing instruction, and the legacy app stripped
    it for exactly that reason before ever handing preset text to the
    model. `description` is kept separately for callers (e.g. the Brief
    prompt) that want a one-line, human-readable label instead of the full
    persona instructions.
    """

    name: str
    description: str
    body: str


def assets_dir() -> Path:
    """Directory holding bundled prompt assets (`personality.txt`,
    `dynamic_options.txt`), in both a dev checkout and a frozen build.

    Frozen (PyInstaller): `tools/avg.spec` must bundle `avg/assets` as
    `datas` landing at `avg/assets` under `sys._MEIPASS` (mirroring how
    `avg.media.ffmpeg_locate.bundled_ffmpeg_dir` locates `vendor/ffmpeg`
    the same way) - this app ships as an installer to machines where
    `D:\\AI_Video_Gen` does not exist, so these files must travel inside
    the package, never be read from that path at runtime.
    Dev checkout: falls back to the source-tree path
    (`avg/pipeline/../assets`) so tests and `python -m avg` run unmodified.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidate = Path(meipass) / "avg" / "assets"
            if candidate.is_dir():
                return candidate
    return Path(__file__).resolve().parent.parent / "assets"


def _generic_preset() -> PersonalityPreset:
    return PersonalityPreset(name=_GENERIC_PRESET_NAME, description="", body=_GENERIC_BODY)


def _parse(raw: str) -> "dict[str, PersonalityPreset]":
    # Strip a leading UTF-8 BOM - the legacy app hit this exact bug: a BOM
    # blocks the `^##` anchor on the very first preset, silently dropping it.
    raw = raw.lstrip("\ufeff")
    presets: dict[str, PersonalityPreset] = {}
    for match in _PRESET_RE.finditer(raw):
        name = match.group("name").strip().lower()
        description = (match.group("description") or "").strip()
        body = match.group("body").strip()
        if not name or not body:
            continue
        presets[name] = PersonalityPreset(name=name, description=description, body=body)
    return presets


@lru_cache(maxsize=1)
def _load_bundled() -> "dict[str, PersonalityPreset]":
    path = assets_dir() / _ASSET_NAME
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    return _parse(raw)


def load_presets(path: Path | None = None) -> "dict[str, PersonalityPreset]":
    """Every preset in `path` (default: the bundled `personality.txt`),
    keyed by lowercased preset name, in file order.

    Returns an empty dict (never raises) if the file is missing or has no
    parseable `## PRESET:` blocks - callers go through `get_preset`, which
    degrades that into the generic fallback rather than propagating an
    empty catalogue as an error.
    """
    if path is None:
        return dict(_load_bundled())
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    return _parse(raw)


def get_preset(
    name: str,
    presets: "dict[str, PersonalityPreset] | None" = None,
) -> PersonalityPreset:
    """The preset named `name` (case-insensitive), or a sensible fallback.

    Degradation ladder, matching the legacy `Get-PersonalityPresetBody`
    contract:
      1. Exact (case-insensitive) name match.
      2. Unknown/blank name but the catalogue has at least one preset:
         the FIRST preset in file order (never an empty persona).
      3. Catalogue itself is empty (file missing/unparseable): a small
         built-in generic preset.
    This never raises - a missing or renamed `content_shape` value must
    still produce a usable prompt, exactly as it must in the legacy app.
    """
    if presets is None:
        presets = _load_bundled()
    if not presets:
        return _generic_preset()
    key = (name or "").strip().lower()
    if key and key in presets:
        return presets[key]
    # Degrade to the first preset in file order (dict preserves insertion
    # order) rather than raising or returning an empty/generic persona.
    return next(iter(presets.values()))

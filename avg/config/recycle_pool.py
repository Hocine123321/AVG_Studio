"""The hand-maintained image recycling pool: `avg.pipeline.phases.writing.
ScriptPhase` reads this once per run to offer Pass 2 (scene segmentation) a
set of verified images it may reuse instead of writing a fresh prompt,
cutting both API cost and generation time for the scenes a pool image
genuinely fits.

Ported from `Get-RecyclableImagePool`
(`D:\\AI_Video_Gen\\modules\\utilities.ps1`) - same shape, same tolerance.
No in-app editor exists in either app version; the manifest is edited by
hand, same as `dynamic_options.txt`.
"""

from __future__ import annotations

import json
from pathlib import Path

from avg.config.user_assets import default_reusable_images_dir


def read_recyclable_pool(root: Path | None = None) -> list[dict[str, str]]:
    """`[{"file": <absolute path str>, "description": str}, ...]`, in
    manifest order, for every `images.json` entry whose image file
    actually exists on disk.

    Tolerant of everything short of that: a missing directory or
    manifest, unparseable JSON, a manifest that isn't `{"images": [...]}`,
    and an individual entry missing `file`/`description` (either blank)
    are all skipped rather than raised - the feature is a no-op for an
    empty/absent/malformed pool, never a crash, matching every other
    catalogue reader in this codebase (`avg.pipeline.prompts.
    _load_bundled_catalog`, `avg.core.model_cache`).

    `root` overrides `default_reusable_images_dir()` - tests use this to
    avoid the real `Documents/AVG Studio` (`CLAUDE.md`: tests must not
    touch the real user profile).
    """
    d = root if root is not None else default_reusable_images_dir()
    manifest_path = d / "images.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []

    entries = data.get("images") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []

    pool: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        file_name = str(entry.get("file") or "").strip()
        description = str(entry.get("description") or "").strip()
        if not file_name or not description:
            continue
        full_path = d / file_name
        if full_path.is_file():
            pool.append({"file": str(full_path), "description": description})
    return pool

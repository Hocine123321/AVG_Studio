"""Import render-relevant settings from legacy app config.json.

Reads only four groups from the old 21-key config: video.*, edit.*,
video_format.*, runtime.*. Everything else (AI providers, API keys,
voice, images, channel profiles) is out of scope for Slice 1.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel


class RenderSettings(BaseModel):
    """Render settings extracted from legacy app config."""

    fps: int
    codec: str
    aspect_ratio: str
    short_edge: int
    ken_burns: bool
    kb_intensity: int
    kb_mode: str
    render_engine: str
    render_opts: dict
    worker_cap: int
    drop_grain: bool
    skip_prebuild: bool


def _get(cfg: dict, dotted_path: str, default=None):
    """Get a value from nested dict using dotted path notation.

    Never raises on missing branches. Returns default if any intermediate
    level is missing or is not a dict (e.g., null, a scalar).

    Examples:
        _get({"video": {"fps": 24}}, "video.fps", 24) -> 24
        _get({}, "video.fps", 24) -> 24
        _get({"video": None}, "video.fps", 24) -> 24
    """
    parts = dotted_path.split(".")
    current = cfg

    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            # current is not a dict (None, scalar, list, etc.)
            return default

    return current if current is not None else default


def load_render_settings(config_path: Path, total_ram_gb: float) -> RenderSettings:
    """Load render settings from legacy app config, applying RAM gates.

    Args:
        config_path: Path to config.json
        total_ram_gb: Total system RAM in GB (used for RAM gates)

    Returns:
        RenderSettings object with all render-relevant settings

    RAM gates:
        - worker_cap = 2 if total_ram_gb <= 8 else 4
        - drop_grain = total_ram_gb <= 8.5
        - skip_prebuild = total_ram_gb < 12
    """
    config_path = Path(config_path)
    cfg = json.loads(config_path.read_text(encoding="utf-8"))

    # Extract values with sensible defaults
    fps = _get(cfg, "video.fps", 24)
    codec = _get(cfg, "video.codec", "libx264")
    aspect_ratio = _get(cfg, "video_format.aspect_ratio", "9:16")
    short_edge = _get(cfg, "video_format.short_edge", 1080)
    ken_burns = _get(cfg, "video.ken_burns", False)
    kb_intensity = _get(cfg, "video.kb_intensity", 100)
    kb_mode = _get(cfg, "video.kb_mode", "flat")
    render_engine = _get(cfg, "video.render_engine", "auto")
    render_opts = _get(cfg, "runtime.render_opts", {})

    # Apply RAM gates
    worker_cap = 2 if total_ram_gb <= 8 else 4
    drop_grain = total_ram_gb <= 8.5
    skip_prebuild = total_ram_gb < 12

    return RenderSettings(
        fps=fps,
        codec=codec,
        aspect_ratio=aspect_ratio,
        short_edge=short_edge,
        ken_burns=ken_burns,
        kb_intensity=kb_intensity,
        kb_mode=kb_mode,
        render_engine=render_engine,
        render_opts=render_opts,
        worker_cap=worker_cap,
        drop_grain=drop_grain,
        skip_prebuild=skip_prebuild,
    )

"""Tests for render settings importer."""

import json
from avg.config.settings import load_render_settings


def _write(tmp_path, cfg):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def test_ram_gates_at_8gb(tmp_path):
    """Verify RAM gates at 8GB boundary."""
    s = load_render_settings(_write(tmp_path, {}), total_ram_gb=8.0)
    assert s.worker_cap == 2
    assert s.drop_grain is True
    assert s.skip_prebuild is True


def test_ram_gates_on_a_big_machine(tmp_path):
    """Verify RAM gates on a large machine."""
    s = load_render_settings(_write(tmp_path, {}), total_ram_gb=32.0)
    assert s.worker_cap > 2 and s.drop_grain is False and s.skip_prebuild is False


def test_grain_boundary_is_8_5(tmp_path):
    """Verify the precise grain boundary at 8.5GB."""
    assert load_render_settings(_write(tmp_path, {}), total_ram_gb=8.5).drop_grain is True
    assert load_render_settings(_write(tmp_path, {}), total_ram_gb=8.6).drop_grain is False


def test_missing_keys_fall_back_to_defaults(tmp_path):
    """Verify defaults when config is missing keys."""
    s = load_render_settings(_write(tmp_path, {"video": {}}), total_ram_gb=16.0)
    assert s.fps == 24 and s.codec == "libx264" and s.render_engine == "auto"


def test_prebuild_boundary_at_12(tmp_path):
    """Verify prebuild boundary at 12GB."""
    assert load_render_settings(_write(tmp_path, {}), total_ram_gb=11.9).skip_prebuild is True
    assert load_render_settings(_write(tmp_path, {}), total_ram_gb=12.0).skip_prebuild is False


def test_worker_cap_boundary_at_8(tmp_path):
    """Verify worker cap boundary at 8GB."""
    assert load_render_settings(_write(tmp_path, {}), total_ram_gb=8.0).worker_cap == 2
    assert load_render_settings(_write(tmp_path, {}), total_ram_gb=8.1).worker_cap == 4


def test_dotted_path_with_null_intermediate(tmp_path):
    """Verify _get handles null values in intermediate levels."""
    s = load_render_settings(
        _write(tmp_path, {"video": None, "video_format": None}), total_ram_gb=16.0
    )
    assert s.fps == 24  # Falls back to default
    assert s.aspect_ratio == "9:16"  # Falls back to default

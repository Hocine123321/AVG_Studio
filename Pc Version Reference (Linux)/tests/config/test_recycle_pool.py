"""Tests for avg.config.recycle_pool.read_recyclable_pool - the hand-
maintained image recycling pool `avg.pipeline.phases.writing.ScriptPhase`
reads. Ported from `Get-RecyclableImagePool`
(`D:\\AI_Video_Gen\\modules\\utilities.ps1`): tolerant of everything short
of a well-formed entry whose file actually exists.
"""

from __future__ import annotations

import json
from pathlib import Path

from avg.config.recycle_pool import read_recyclable_pool


def _write_manifest(root: Path, images: list[dict]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "images.json").write_text(json.dumps({"images": images}), encoding="utf-8")


def test_missing_directory_is_an_empty_pool_not_an_error(tmp_path):
    assert read_recyclable_pool(root=tmp_path / "does_not_exist") == []


def test_missing_manifest_is_an_empty_pool(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    assert read_recyclable_pool(root=tmp_path) == []


def test_malformed_manifest_json_is_an_empty_pool_not_a_crash(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "images.json").write_text("{not valid json", encoding="utf-8")
    assert read_recyclable_pool(root=tmp_path) == []


def test_a_well_formed_entry_whose_file_exists_is_returned(tmp_path):
    (tmp_path / "sunset.jpg").write_bytes(b"fake image bytes")
    _write_manifest(tmp_path, [{"file": "sunset.jpg", "description": "A warm sunset."}])
    pool = read_recyclable_pool(root=tmp_path)
    assert pool == [{"file": str(tmp_path / "sunset.jpg"), "description": "A warm sunset."}]


def test_an_entry_whose_file_does_not_exist_is_skipped(tmp_path):
    _write_manifest(tmp_path, [{"file": "missing.jpg", "description": "Never uploaded."}])
    assert read_recyclable_pool(root=tmp_path) == []


def test_an_entry_missing_file_or_description_is_skipped(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    _write_manifest(
        tmp_path,
        [
            {"file": "a.jpg"},  # no description
            {"description": "no file key at all"},
        ],
    )
    assert read_recyclable_pool(root=tmp_path) == []


def test_manifest_order_is_preserved(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "b.jpg").write_bytes(b"y")
    _write_manifest(
        tmp_path,
        [{"file": "b.jpg", "description": "second"}, {"file": "a.jpg", "description": "first"}],
    )
    pool = read_recyclable_pool(root=tmp_path)
    assert [p["description"] for p in pool] == ["second", "first"]

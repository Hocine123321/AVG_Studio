"""Packaging must ship the audio/image stack the pipeline imports at call time."""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_flatpak_manifest_installs_media_extra() -> None:
    text = (ROOT / "packaging/flatpak/io.github.Hocine123321.AVGStudio.yml").read_text()
    cmds = re.findall(r"^\s*- pip3 install --prefix=/app \"([^\"]+)\"", text, re.M)
    assert cmds, "manifest has no pip3 install line"
    extras = re.search(r"\[([^\]]*)\]", cmds[0])
    assert extras and {"gui", "media"} <= set(extras.group(1).split(","))


def test_media_extra_covers_pydub_on_python_313() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    media = data["project"]["optional-dependencies"]["media"]
    assert any(d.startswith("pydub") for d in media)
    # pydub imports the stdlib `audioop` removed in 3.13 (PEP 594).
    assert any(d.startswith("audioop-lts") and "3.13" in d for d in media)

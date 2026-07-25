"""Packaging entry point.

Wraps the two freeze commands from the Task 1 packaging spike so later
tasks (Task 12 in particular) have a single place to invoke them instead
of re-deriving the incantations:

    python tools/build.py pyinstaller
    python tools/build.py nuitka

Both variants must be run from the build virtualenv described in
docs/decisions/2026-07-24-packaging.md (pyinstaller + nuitka + the media
deps), not the project's dev .venv — this script does not create or
activate that environment, it only shells out to whichever interpreter
invoked it.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ENTRY = Path(__file__).with_name("spike_window.py")


def build_pyinstaller(entry: Path = ENTRY, name: str = "avg-spike-pyi") -> None:
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--onefile",
            "--noconsole",
            "--name", name,
            str(entry),
        ],
        check=True,
    )


def build_nuitka(entry: Path = ENTRY, output_filename: str = "avg-spike-nuitka.exe") -> None:
    subprocess.run(
        [
            sys.executable, "-m", "nuitka",
            "--onefile",
            "--windows-console-mode=disable",
            "--enable-plugin=no-qt",
            f"--output-filename={output_filename}",
            str(entry),
        ],
        check=True,
    )


ENGINES = {"pyinstaller": build_pyinstaller, "nuitka": build_nuitka}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("engine", choices=sorted(ENGINES))
    args = parser.parse_args()
    ENGINES[args.engine]()


if __name__ == "__main__":
    main()

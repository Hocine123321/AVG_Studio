# Packaging decision — Slice 1 spike

**Date:** 2026-07-25 (task dated 2026-07-24)
**Status:** partial — PyInstaller measured, Nuitka not yet run
**Machine:** Windows 11 Pro 26200, Python 3.12.10, 7.9 GB RAM

## What was measured

| | PyInstaller onefile |
|---|---|
| Bundle contents | numpy, Pillow, moviepy, pywebview |
| Size | **63.0 MB** |
| Build time | 1.1 min |
| Launches | **3/3** |
| Warm start to WebView2 init | **0.38 s** median (first run 1.03 s) |

Nuitka was not built — the spike was interrupted twice (an API failure, then a
machine power-off). Given the PyInstaller result below, Nuitka is no longer on
the critical path; it is a later optimisation, not a gate.

## The failure that mattered, and the fix

The first build produced a 63 MB exe that **built fine and would not run**.
`--noconsole` hid the reason; a screenshot of the error dialog gave it up:

```
Failed to execute script 'spike_window' due to unhandled exception:
No package metadata was found for imageio
  File "moviepy\__init__.py", line 5, in <module>
  File "moviepy\audio\fx\__init__.py", line 5, in <module>
```

`moviepy` imports `imageio`, which resolves its own distribution through
`importlib.metadata`. PyInstaller does not bundle `.dist-info` metadata unless
told to. The fix is one flag per affected distribution:

```
--copy-metadata imageio --copy-metadata imageio_ffmpeg --copy-metadata moviepy
--copy-metadata numpy --copy-metadata Pillow
```

With those, the same 63 MB exe launched 3/3.

**Lesson for later tasks:** never ship a `--noconsole` build without first
running a console build of the same code. A silent frozen app is unfalsifiable.

## WebView2

The architecture assumes the WebView2 runtime is already present on Windows 11
and does not need bundling. **Verified on this machine:** runtime
`150.0.4078.48`, found at
`HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}`.
A launched app spawns ~12 `msedgewebview2` child processes, which is the
reliable signal that every import succeeded and the browser control started —
`Process.MainWindowHandle` is **always 0** for a pywebview app and must not be
used to detect a running window.

## Decision

**Use PyInstaller.** It builds in about a minute, produces a working 63 MB
artifact, and starts fast enough that startup is not a design concern. Nuitka's
advantages (faster start, compiled output) are not worth its build times while
the app is changing daily. Revisit before v1 ships.

## The install-then-import question is now much less urgent

Task 1 Step 7 framed three options because a ~400 MB fat bundle was assumed
unacceptable. **A fat bundle of the heavy media dependencies is 63 MB, not 400 MB.**

Extrapolating (NOT measured): adding FastAPI/uvicorn (~10 MB), edge-tts, pydub
and assemblyai (~5 MB), and an essentials FFmpeg build (~80 MB) lands the whole
app near **160 MB** — a normal size for one installer download, with **no
first-run pip step at all**.

That would remove an entire subsystem from the plan: no private venv, no
install-then-import problem, no per-component retry UI. The installer would
still offer the FFmpeg step, because FFmpeg is the one genuinely large piece and
the one a user might already have.

**Not yet measured, and required before committing to this:**

1. Real bundle size with the *full* dependency set plus FFmpeg.
2. True cold start on a machine that has never run the app (Windows Sandbox),
   rather than the warm-cache numbers above.
3. Whether a bundled FFmpeg is preferable to a downloaded one for licensing and
   update reasons.

## Open items carried forward

- Nuitka comparison — deferred, not blocking.
- Windows Sandbox cold-start verification — belongs with Task 12 acceptance.
- The Step 7 three-way decision is **provisionally resolved as option (a), fat
  bundle**, pending the three measurements above.

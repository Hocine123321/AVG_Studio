"""Best-effort machine info the RAM-gated parts of this app need.

Before this module existed, `avg.pipeline.phases.visuals` and `avg.api.app`
each carried their own copy of a Windows-only `_total_ram_gb` (the Windows
build still has that pair - see `docs/linux-port/WINDOWS_VS_LINUX.md`).
Neither could import the other's private helper, since `avg.pipeline` may
not import from `avg.api` (see `visuals.py`'s docstring). `avg.core` sits
below both in the layer stack (README.md's "Layer rule"), so it is the one
place both were always allowed to share this from - this module is that
place, and on this fork it's Linux-only: no `sys.platform` branch, no
ctypes.

One function, one responsibility: a total-physical-RAM number, in GB,
used only to pick a worker-pool size. Never raises - every caller treats
"couldn't tell" as "assume a small machine" (see `total_ram_gb`'s
docstring), not as an error.
"""

from __future__ import annotations

#: This project's own dev machine's real profile - the fallback when
#: detection fails, so a failure keeps the low-resource path active
#: rather than silently assuming a big machine. Mirrors the constant both
#: `avg.pipeline.phases.visuals` and `avg.config.settings.
#: load_render_settings` already key their own low-RAM ceiling off of.
_FALLBACK_RAM_GB = 8.0


def total_ram_gb() -> float:
    """Best-effort total physical RAM in GB, from `MemTotal` in
    `/proc/meminfo`.

    No extra dependency needed (psutil is not a dependency of this
    package, and pulling it in for one number isn't worth it - same
    reasoning `avg.providers.voice.providers` gives for lazily importing
    `edge_tts`): every Linux kernel this app runs under exposes
    `/proc/meminfo`, sandboxed or not (`/proc` is never hidden from a
    Flatpak app, unlike most of the real filesystem).

    Falls back to `_FALLBACK_RAM_GB` on any failure - a missing file, an
    unparsable line - rather than raising.
    """
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:  # noqa: PTH123
            for line in f:
                if line.startswith("MemTotal:"):
                    # e.g. "MemTotal:       16384000 kB\n"
                    kb = int(line.split()[1])
                    return kb / (1024**2)
        return _FALLBACK_RAM_GB
    except Exception:
        return _FALLBACK_RAM_GB

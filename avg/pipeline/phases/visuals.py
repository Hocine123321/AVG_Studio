"""Phase 3: Visuals - one image per scene.

Runs after Director I (Phase 2), before Voice & Audio (Phase 4). Reads the
finished script's per-scene image prompts (`scenes.json`, written by
`avg.pipeline.phases.writing.ScriptPhase`) and generates one image per scene
through Slice 3's image providers (`avg/providers/images/`), in parallel,
capped by the machine's RAM profile.

**RAM gate.** This app's own 8GB dev box is the reason
`avg.config.settings.load_render_settings` caps render workers at 2
below 8.5/8GB - exceeding that is what made the legacy PowerShell app die
mid-render on this hardware (see that module's docstring and this repo's
`dev-machine-8gb` memory note). This phase applies the identical rule
(<=8GB -> 2 workers, else 4) to its own image-generation worker pool. It
restates the constant rather than importing `RenderSettings` because this
phase has no `config.json` path to read from - just `ctx` - and RAM
detection has no shared helper in this codebase yet (`avg.api.app`'s own
`_total_ram_gb` is the same kind of small, per-layer copy, not something
`avg.pipeline` may import from `avg.api` - that dependency would run
backwards).

**One image failing must not kill the phase** (this task's other named
invariant). Each scene's `provider.generate()` call is wrapped individually
in `_generate_one`: an `AvgError` - or any other exception, a provider bug
is still just one image, not the whole run - is caught there and turned
into a failed `_SceneResult`, never raised out of the worker. The phase
itself only raises when NOTHING at all was produced (every scene failed) -
a `run()` that let one bad provider call take eleven good images down with
it is exactly the failure this exists to prevent (see this slice's task
brief). Every scene's outcome, good or bad, is recorded in
`images/manifest.json` so a resume or a human can tell "eleven images and
one recorded failure" apart from "only eleven scenes existed".

No retry loop here - `avg.providers.transport.Transport`/`POLICY` already
own that inside a provider's own record-mode HTTP calls. **Slice 11 added a
cross-provider fallback** (`_should_fall_back`, wired into `run`): if every
scene fails on the configured provider, `image_on_failure == "fall_back"`
is set, a backup provider/model are configured, and `POLICY` says the
dominant failure kind is one a different provider could plausibly survive
(`switch_key`, `switch_provider`, `switch_model` - never `fail`), the whole
scene set is regenerated once on the backup. This mirrors
`avg/providers/text/router.py`'s own primary/backup/`on_failure` shape and
its "exactly once, never chained" rule (the legacy `$triedFallback` flag) -
a third attempt after both providers fail is not attempted.

Writes `<project>/images/{NNN}.png` (1-indexed, zero-padded to 3 digits -
the legacy app's own naming, and the order `avg.media.thumbnails.
list_image_files` sorts by to pick the project grid's thumbnail) plus
`<project>/images/manifest.json`.
"""

from __future__ import annotations

import ctypes
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from avg.config import provider_health
from avg.core.errors import AvgError, ErrorKind, report_degradation
from avg.core.events import ProgressEvent
from avg.pipeline.phase import PhaseContext
from avg.pipeline.phases._common import read_json, render_dir, script_dir, write_json
from avg.providers.images.base import ImageProvider
from avg.providers.images.community import (
    HuggingFaceImageProvider,
    PollinationsImageProvider,
    StableHordeImageProvider,
)
from avg.providers.images.premium import GeminiImageProvider, OpenAIImageProvider
from avg.providers.transport import Transport

# Matches avg.core.settings_schema's own `image_provider` choices/default.
_PROVIDERS: dict[str, type] = {
    "openai": OpenAIImageProvider,
    "gemini": GeminiImageProvider,
    "pollinations": PollinationsImageProvider,
    "huggingface": HuggingFaceImageProvider,
    "stablehorde": StableHordeImageProvider,
}
_DEFAULT_PROVIDER = "pollinations"
_DEFAULT_MODEL = "flux"

# The render phase's identical RAM gate (avg.config.settings.
# load_render_settings: `worker_cap = 2 if total_ram_gb <= 8 else 4`) -
# restated here, see module docstring for why this isn't a shared import.
_LOW_RAM_GB_CEILING = 8.0
_LOW_RAM_WORKERS = 2
_DEFAULT_WORKERS = 4


def _total_ram_gb() -> float:
    """Best-effort total physical RAM in GB.

    Windows-only, via `GlobalMemoryStatusEx` - mirrors `avg.api.app.
    _total_ram_gb`'s identical approach (psutil is not a dependency of this
    package; pulling it in for one number isn't worth it). Falls back to
    8.0 - this project's own dev machine - on any failure, which keeps the
    low-resource path active rather than silently assuming a big machine.
    """
    try:
        class _MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
        return stat.ullTotalPhys / (1024**3)
    except Exception:
        return 8.0


def _worker_cap(total_ram_gb: float) -> int:
    """2 workers at <=8GB (this machine's own real profile - 7.9GB - is the
    live path, not a theoretical branch), 4 otherwise."""
    return _LOW_RAM_WORKERS if total_ram_gb <= _LOW_RAM_GB_CEILING else _DEFAULT_WORKERS


def _build_provider(name: str, transport: Transport | None) -> ImageProvider:
    """One image provider instance for `name`, falling back to the
    catalogue default for an unrecognised name rather than raising - a
    channel document from a future/rolled-back settings catalogue must
    still produce something runnable."""
    cls = _PROVIDERS.get(name, _PROVIDERS[_DEFAULT_PROVIDER])
    return cls(transport=transport)


def _images_dir(ctx: PhaseContext) -> Path:
    return Path(ctx.project.folder) / "images"


def _scenes_json_path(ctx: PhaseContext) -> Path:
    return script_dir(ctx) / "scenes.json"


@dataclass
class _SceneResult:
    scene: int
    ok: bool
    file: str | None = None
    error: str = ""
    #: The classification the provider's own error already carried. Kept
    #: beside the message because the message alone cannot tell a 403 from
    #: a timeout, and the phase-level failure is built from these - see
    #: `_dominant_kind`. `None` means the failure never reached the error
    #: taxonomy at all (a provider raising something that is not an
    #: `AvgError`), which is the one case that really is a bug.
    kind: ErrorKind | None = None


def _generate_one(
    provider: ImageProvider, prompt: str, aspect_ratio: str, model: str, scene: int, out_path: Path
) -> _SceneResult:
    """Generate and save exactly one scene's image. Never raises - every
    failure (provider `AvgError`, or any other exception a buggy provider
    might throw) becomes a failed `_SceneResult` instead, which is what
    lets one bad call run alongside eleven good ones in the same worker
    pool without a single `Future.result()` blowing up the whole phase."""
    try:
        data = provider.generate(prompt, aspect_ratio=aspect_ratio, model=model)
        if not data:
            raise AvgError(ErrorKind.bug, f"{provider.name} returned no image data")
        out_path.write_bytes(data)
        return _SceneResult(scene=scene, ok=True, file=out_path.name)
    except AvgError as exc:
        return _SceneResult(scene=scene, ok=False, error=exc.message, kind=exc.kind)
    except Exception as exc:  # noqa: BLE001 - a provider bug must not sink the whole phase
        return _SceneResult(scene=scene, ok=False, error=str(exc), kind=None)


def _dominant_kind(results: dict[int, _SceneResult]) -> ErrorKind:
    """The classification that best explains why nothing was produced.

    Most frequent wins. Ties break by `avg.providers.errors._CHECK_ORDER`,
    which already encodes which classification is the more actionable claim
    when a failure could be read two ways - no second ordering is invented
    here. Falls back to `bug` when nothing was classified at all, which is
    the one case where "bug" is the honest answer.
    """
    from avg.providers.errors import _CHECK_ORDER  # noqa: PLC0415 - avoids an import cycle

    counts: dict[ErrorKind, int] = {}
    for result in results.values():
        if result.ok or result.kind is None:
            continue
        counts[result.kind] = counts.get(result.kind, 0) + 1
    if not counts:
        return ErrorKind.bug

    def rank(kind: ErrorKind) -> int:
        return _CHECK_ORDER.index(kind) if kind in _CHECK_ORDER else len(_CHECK_ORDER)

    return min(counts, key=lambda kind: (-counts[kind], rank(kind)))


def _should_fall_back(kind: ErrorKind) -> bool:
    """Whether a different provider could plausibly survive this failure.

    Consults `POLICY` and nothing else - no status code, no error string -
    exactly as `avg/providers/text/router.py` does for text. A kind marked
    `on_exhausted="fail"` (content_filter, bug, dependency) would fail the
    same way on any provider, so falling back would spend money for nothing.
    """
    from avg.providers.errors import POLICY  # noqa: PLC0415 - avoids an import cycle

    rule = POLICY.get(kind)
    if rule is None:
        return False
    return rule.on_exhausted in {"switch_key", "switch_provider", "switch_model"}


def index_entries(results: dict[int, _SceneResult], total: int) -> list[str]:
    """`render/index.json`'s contents: one path per scene, in scene order.

    The vendored engine maps LIST POSITION to scene number
    (`avg/media/vendor/avg.py:2236`), so this list must carry exactly one
    entry per scene whether or not that scene's image was generated. A
    failed scene takes the nearest previous successful image (the nearest
    FOLLOWING one when it is the first scene, since there is nothing
    behind it): its narration still plays, so the choice is between a
    repeated image - visible, honest, in sync - and every later scene
    sliding one position earlier against the voice track.

    Scenes with no result at all are treated exactly like failed ones;
    from the render's point of view there is no image either way. The
    all-failed case never reaches here - the phase raises first.
    """
    ordered = [results.get(scene) for scene in range(1, total + 1)]
    files = [
        f"images/{result.file}" if result is not None and result.ok and result.file else None
        for result in ordered
    ]

    entries: list[str] = []
    for position, current in enumerate(files):
        if current is not None:
            entries.append(current)
            continue
        previous = next((f for f in reversed(files[:position]) if f is not None), None)
        following = next((f for f in files[position + 1 :] if f is not None), None)
        fill = previous or following
        if fill is not None:
            entries.append(fill)
    return entries


@dataclass(frozen=True, slots=True)
class VisualsPhase:
    id: int = 3
    key: str = "visuals"
    name: str = "Visuals"
    description: str = (
        "Generates one image per scene with the configured image provider. Writes images/ and images/manifest.json."
    )

    def inputs_ready(self, ctx: PhaseContext) -> bool:
        return _scenes_json_path(ctx).exists()

    def run(self, ctx: PhaseContext) -> None:
        scenes = read_json(_scenes_json_path(ctx))
        if not isinstance(scenes, list) or not scenes:
            raise AvgError(ErrorKind.bug, "visuals phase: no finished scenes.json to generate images from")

        jobs: list[tuple[int, str]] = []
        for i, scene in enumerate(scenes, start=1):
            if not isinstance(scene, dict):
                continue
            prompt = str(scene.get("image_prompt") or scene.get("text") or "").strip()
            if not prompt:
                prompt = f"{ctx.project.title}, scene {i}"
            jobs.append((i, prompt))
        if not jobs:
            raise AvgError(ErrorKind.bug, "visuals phase: scenes.json had no usable scene entries")

        settings = ctx.channel_values
        provider_name = str(settings.get("image_provider") or _DEFAULT_PROVIDER)
        model = str(settings.get("image_model") or _DEFAULT_MODEL)
        aspect_ratio = ctx.project.aspect_ratio

        transport = ctx.router if isinstance(ctx.router, Transport) else None
        provider = _build_provider(provider_name, transport)

        images_dir = _images_dir(ctx)
        images_dir.mkdir(parents=True, exist_ok=True)

        total = len(jobs)
        workers = max(1, min(_worker_cap(_total_ram_gb()), total))

        ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=0, message=f"Generating {total} image(s)..."))

        def _generate_all(prov: ImageProvider, mdl: str) -> dict[int, _SceneResult]:
            """One full pass over every scene with one provider."""
            out: dict[int, _SceneResult] = {}
            done = 0
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(_generate_one, prov, prompt, aspect_ratio, mdl, i, images_dir / f"{i:03d}.png"): i
                    for i, prompt in jobs
                }
                for future in as_completed(futures):
                    result = future.result()
                    out[result.scene] = result
                    done += 1
                    percent = int(done / total * 100)
                    status = "ok" if result.ok else f"failed ({result.error})"
                    ctx.emit(
                        ProgressEvent(
                            project_id=ctx.project.id,
                            percent=percent,
                            message=f"Scene {result.scene}/{total}: {status}",
                        )
                    )
            return out

        results = _generate_all(provider, model)

        primary_provider_name = provider_name
        primary_kind_label = ""
        fell_back = False
        if not any(r.ok for r in results.values()):
            # Exactly once, never chained - the same rule the text router
            # inherited from the legacy `$triedFallback` flag.
            backup_provider = str(settings.get("image_provider_backup") or "")
            backup_model = str(settings.get("image_model_backup") or "")
            on_failure = str(settings.get("image_on_failure") or "stop")
            kind = _dominant_kind(results)
            if (
                on_failure == "fall_back"
                and backup_provider
                and backup_model
                and backup_provider != provider_name
                and _should_fall_back(kind)
            ):
                cooldown_detail = ""
                if kind in (ErrorKind.quota, ErrorKind.billing):
                    cooldown_detail = provider_health.cooldown_message_suffix(
                        provider_health.active_cooldown(primary_provider_name)
                    )
                ctx.emit(
                    ProgressEvent(
                        project_id=ctx.project.id,
                        percent=0,
                        message=f"Falling back to {backup_provider}/{backup_model}...{cooldown_detail}",
                    )
                )
                primary_kind_label = kind.value
                fell_back = True
                provider_name, model = backup_provider, backup_model
                results = _generate_all(_build_provider(backup_provider, transport), backup_model)

        ok_count = sum(1 for r in results.values() if r.ok)
        if ok_count == 0:
            # The reasons were on every `_SceneResult` all along; raising a
            # bare `bug` here is what threw them away and left the take log
            # reading `kind=bug detail=` for a plain missing API key.
            kind = _dominant_kind(results)
            reasons = sorted({r.error for r in results.values() if not r.ok and r.error})
            detail = reasons[0] if reasons else "no reason reported"
            message = f"Visuals: all {total} scene(s) failed on {provider_name}/{model} - {detail}"
            if fell_back:
                # The primary's own reason would otherwise survive only in
                # `ProgressEvent`s - visible while the run is live, gone from
                # the take log the moment it finishes. A slice whose whole
                # point is a truthful failure message cannot let the record
                # of the FIRST failure disappear just because a second one
                # also happened.
                message += f" (primary {primary_provider_name} also failed - {primary_kind_label})"
            raise AvgError(kind, message)
        if ok_count < total:
            # A partial set of images is a finished phase with a worse video:
            # the missing scenes repeat their neighbour's image (see
            # `index_entries`). Worth recording - it is the reason a video
            # has a picture on screen twice in a row.
            report_degradation(
                ctx,
                f"Visuals: {total - ok_count} of {total} scene(s) had no image; "
                "each repeats the nearest one instead.",
            )

        manifest = {
            "version": 1,
            "provider": provider_name,
            "model": model,
            "scenes": [
                {
                    "scene": s,
                    "status": "ok" if results[s].ok else "failed",
                    "file": results[s].file,
                    "error": results[s].error,
                }
                for s in sorted(results)
            ],
        }
        write_json(images_dir / "manifest.json", manifest)

        # `render/index.json` is what the render engine actually reads
        # (`avg/media/vendor/avg.py:2228` exits 1 without it). `manifest.json`
        # above stays: it records the provider, the model and each scene's
        # error, which index.json has no room for - the vendored engine
        # expects a flat list of paths and nothing else.
        rdir = render_dir(ctx)
        rdir.mkdir(parents=True, exist_ok=True)
        write_json(rdir / "index.json", index_entries(results, total))

        _write_safezones(ctx, rdir)

        ctx.emit(
            ProgressEvent(
                project_id=ctx.project.id,
                percent=100,
                message=f"Visuals complete: {ok_count}/{total} image(s) generated.",
            )
        )


def _write_safezones(ctx: PhaseContext, rdir: Path) -> None:
    """Analyse where text may sit over each image, into
    `render/scene_safezones.json`.

    Runs on the SOURCE images rather than rendered frames, which is why it
    can run here at all rather than waiting for a render
    (`avg/media/vendor/avg.py:221`). Bad images therefore fail fast, before
    voice and render time is spent on them.

    Non-fatal in every direction: a missing safezone file costs overlay
    PLACEMENT quality, never the video. `cmd_safezones` reports by raising
    SystemExit, including on success, so that is caught rather than
    allowed to end the phase.
    """
    from avg.media.vendor.avg import cmd_safezones

    argv = [str(ctx.project.folder), str(rdir / "index.json"), str(rdir / "scene_safezones.json")]
    try:
        cmd_safezones(argv)  # type: ignore[no-untyped-call]  # the vendored engine is untyped by design
    except SystemExit as exc:
        if exc.code in (0, None):
            return
        _warn_safezones(ctx, f"exit code {exc.code}")
    except Exception as exc:  # noqa: BLE001 - never fatal, see above
        _warn_safezones(ctx, str(exc))


def _warn_safezones(ctx: PhaseContext, reason: str) -> None:
    message = f"Safezone analysis was skipped ({reason}); overlay placement will use defaults."
    ctx.emit(ProgressEvent(project_id=ctx.project.id, percent=100, message=message))
    report_degradation(ctx, message)

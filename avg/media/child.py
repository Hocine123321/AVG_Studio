"""The function that runs inside the spawned render child process.

Wraps `avg.media.vendor.avg.render_project`, which does the actual moviepy /
ffmpeg work. Runs in a separate OS process (started by `avg.media.engine`
via `multiprocessing.get_context("spawn")`) specifically so that an
out-of-memory kill takes down the render, not the whole app.

`run_child` MUST stay a plain module-level function, not a closure or a
lambda: under the spawn start method (the only one Windows has, and the one
the packaged executable uses), the child interpreter re-imports this module
and looks up `run_child` by name to call it. A closure can't be found that
way, and a lambda can't be pickled by reference at all.

Everything `run_child` needs is passed in as plain, picklable arguments — a
dict of `render_project` kwargs, plus the `multiprocessing.Queue` it reports
through. It never returns a value (multiprocessing discards the target's
return value); progress and the final result travel over the queue instead,
and success/failure of the render itself is read by the parent from the
child's exit code (see `avg.media.engine.classify_exit`).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from avg.media.environment import suppress_console_windows

# Messages put on the queue are plain tuples so they stay cheaply picklable:
#   ("progress", percent: int, message: str)
#   ("result", output_path: str, size_bytes: int)
#   ("error", message: str)
# A crash puts nothing — the parent infers that from the exit code.

# Matches the old app's guard: an output file smaller than this never
# represents a real render, however cleanly the render code returned.
_MIN_OUTPUT_BYTES = 1024


def run_child(kwargs: dict[str, Any], q: Any) -> None:
    """Entry point executed inside the spawned child process.

    `kwargs` are the keyword arguments for `render_project` (already
    translated from `RenderRequest` by the engine — see
    `avg.media.engine._request_to_kwargs`). `q` is the queue the child
    reports progress and its final result on.

    Two environment variables let the engine be tested without a real
    render (set by tests, never by production callers):

      AVG_CHILD_CRASH         - raise immediately, simulating a hard crash
                                 before any work happens.
      AVG_CHILD_FAKE_PROGRESS - emit synthetic 0/50/100 progress and a fake
                                 result over the *same* queue-and-sink code
                                 path a real render uses, without touching
                                 moviepy/ffmpeg. This exercises the real
                                 queue-drain and exit-classification plumbing
                                 in the engine; it does not bypass it.
    """
    # Before anything else, and before the vendored engine is imported
    # below: this is the process that shells out to ffmpeg dozens of times
    # per render, and every one of those calls would otherwise flash a
    # console window on the user's desktop. The vendored engine and moviepy
    # both make those calls and neither can be edited, so the suppression is
    # installed process-wide here. See `environment.suppress_console_windows`.
    suppress_console_windows()

    if os.environ.get("AVG_CHILD_CRASH"):
        raise RuntimeError("AVG_CHILD_CRASH: forced crash for testing")

    def _sink(percent: int, message: str) -> None:
        q.put(("progress", int(percent), str(message)))

    try:
        if os.environ.get("AVG_CHILD_FAKE_PROGRESS"):
            _sink(0, "starting (fake)")
            _sink(50, "halfway (fake)")
            _sink(100, "done (fake)")
            output = Path(kwargs["project_path"]) / "master_output.mp4"
            q.put(("result", str(output), 0))
        else:
            # Imported here, not at module scope: this import pulls in
            # moviepy/numpy/etc. (via render_project's own lazy
            # _assemble_imports), which the fake-progress test path and the
            # crash test path have no need to pay for.
            import avg.media.vendor.avg as vendor_avg

            vendor_avg.PROGRESS_SINK = _sink  # type: ignore[assignment]  # vendored global is declared None
            try:
                # May call sys.exit(1) on hard errors (see its docstring) —
                # that SystemExit propagates out of run_child and becomes
                # the child's exit code, which is exactly what
                # classify_exit is for. Not caught here.
                vendor_avg.render_project(**kwargs)
            finally:
                vendor_avg.PROGRESS_SINK = None

            # render_project can return normally having produced nothing —
            # the old app guarded this explicitly (missing or <1KB output was
            # a failure, not a success) and dropping that guard would report
            # an empty render as finished. Report the reason rather than
            # letting the parent infer a bare "exited with code 0".
            output = Path(kwargs["project_path"]) / "master_output.mp4"
            size = output.stat().st_size if output.exists() else 0
            if size < _MIN_OUTPUT_BYTES:
                q.put((
                    "error",
                    f"render finished but {output.name} is "
                    f"{'missing' if size == 0 else f'only {size} bytes'}",
                ))
            else:
                q.put(("result", str(output), size))
    finally:
        # multiprocessing.Queue buffers puts and feeds them to the
        # underlying pipe on a background thread. Closing and joining that
        # thread here guarantees every message we put is actually flushed
        # before this process exits, instead of trusting interpreter-
        # shutdown ordering to do it for us.
        q.close()
        q.join_thread()


# ── part 2 of a render: overlays, cards and captions ─────────────────────
#
# Messages this child puts on the queue, alongside the ("progress", …) shape
# `run_child` uses:
#   ("composite", overlays: int, captions: str)
#   ("error", message: str)

#: Which vendored passes the last composite child ran. A module-level list
#: rather than a return value because the real child runs in another process,
#: where multiprocessing discards the target's return value - the parent
#: learns the outcome over the queue, and a test that wants the CALL ORDER
#: invokes `run_composite_child` in-process and reads this.
_VENDOR_CALLS: list[str] = []


def _composite_argv_overlay(kwargs: dict[str, Any]) -> list[str]:
    """`cmd_overlay_render`'s positional arguments, in its own order.

    Positional because that is the vendored contract
    (`avg/media/vendor/avg.py:3096-3111`): project, edit plan, timings,
    dynamic options, brand preset, beat map, render engine, defer flag,
    global font.

    `dynamic_options.txt` is passed as an empty string on purpose -
    `_load_brand` falls back to the drawing library's own DEFAULT_BRAND when
    the path does not exist (`avg.py:2750-2753`), so this app ships no copy of
    the legacy catalogue. The defer flag is "0": this route only runs when
    nothing was pre-rendered, so there is nothing to defer.
    """
    project = kwargs["project_path"]
    render = os.path.join(project, "render")
    return [
        project,
        os.path.join(render, "edit_plan.json"),
        os.path.join(render, "scene_timings.json"),
        "",
        "",
        "",
        str(kwargs.get("render_engine") or "auto"),
        "0",
        str(kwargs.get("font") or ""),
    ]


def _composite_argv_captions(kwargs: dict[str, Any]) -> list[str]:
    """`cmd_caption_burn`'s sixteen positional arguments.

    The API keys are deliberately EMPTY: this app transcribes in the Screen
    Text phase and seeds the vendored cache
    (`avg.pipeline.phases.captions.seed_transcript_cache`), so the burn reads
    that cache and never needs a key. `provider` must be the same string that
    phase used, because it is part of the cache key.

    The two flags near the end are, in order, `cut_dash` ("1") and `ass_only`
    ("0") - this pass BURNS, unlike the phase that wrote the `.ass`.
    """
    return [
        kwargs["project_path"],
        str(kwargs.get("provider") or "estimated"),
        "",  # assemblyai key - never needed, the cache is seeded
        "",  # openai key - same
        str(kwargs.get("font_size") or 56),
        str(kwargs.get("font_color") or "&HFFFFFF"),
        "3",
        str(kwargs.get("position") or "bottom"),
        str(kwargs.get("max_words") or 7),
        "600",
        str(kwargs.get("animation") or "pop"),
        str(kwargs.get("animation_speed") or "normal"),
        "1",
        "0",
        str(kwargs.get("font") or ""),
        "underline",
    ]


def _run_vendor(call: Any, argv: list[str]) -> int:
    """Run one vendored `cmd_*` and return its exit code.

    Every one of them reports by raising SystemExit, success included.
    Catching it here is what preserves the three-way contract - 0 applied,
    2 skipped, 1 fatal - instead of letting the first call's clean exit end
    the whole child process before the second one runs.
    """
    try:
        call(argv)
    except SystemExit as exc:
        code = exc.code
        return 0 if code in (0, None) else int(code)
    return 0


def run_composite_child(kwargs: dict[str, Any], q: Any) -> None:
    """Part 2 of a render: overlays and captions onto the finished base.

    Two routes, exactly as the vendored engine intends. When
    `render/_final_layers.json` exists - the normal case, because Director II
    pre-rendered the assets - the caption pass ALONE bakes those layers and
    the captions in one encode (`avg/media/vendor/avg.py:3399-3408`); running
    the overlay pass first would encode the overlays a second time, costing
    time and a generation of quality for nothing. When it does not exist,
    overlays are composited onto the base first and the captions burn after.

    A `2` from either pass is a SKIP: reported, never raised. The base video
    is real and finished, and calling the render failed would throw it away.
    The vendored code agrees - a caption skip still bakes the deferred
    overlays before exiting 2 (`_bake_deferred_overlays`).
    """
    suppress_console_windows()

    project = Path(kwargs["project_path"])
    fake = os.environ.get("AVG_COMPOSITE_FAKE")

    def _sink(percent: int, message: str) -> None:
        q.put(("progress", int(percent), str(message)))

    try:
        deferred = (project / "render" / "_final_layers.json").is_file()
        captions_enabled = bool(kwargs.get("captions_enabled", True))
        overlays_code = 0
        _sink(0, "Compositing overlays and captions...")

        if fake is not None:
            # The same queue-and-classification path a real run takes,
            # without moviepy: "overlays=0,captions=2".
            codes = dict(pair.split("=", 1) for pair in fake.split(",") if "=" in pair)
            overlays_code = int(codes.get("overlays", 0))
            captions_code = int(codes.get("captions", 0))
        else:
            import avg.media.vendor.avg as vendor_avg

            vendor_avg.PROGRESS_SINK = _sink  # type: ignore[assignment]  # vendored global is declared None
            try:
                if not deferred:
                    overlays_code = _run_vendor(vendor_avg.cmd_overlay_render, _composite_argv_overlay(kwargs))
                    _VENDOR_CALLS.append("overlay_render")
                if captions_enabled:
                    captions_code = _run_vendor(vendor_avg.cmd_caption_burn, _composite_argv_captions(kwargs))
                    _VENDOR_CALLS.append("caption_burn")
                else:
                    # Not attempted, so not a failure - "off" and "tried and
                    # could not" are different outcomes and the record says
                    # which one happened.
                    captions_code = 2
            finally:
                vendor_avg.PROGRESS_SINK = None

        if 1 in (overlays_code, captions_code):
            q.put(("error", f"composite pass failed (overlays={overlays_code}, captions={captions_code})"))
        else:
            q.put(("composite", _layer_count(project) if deferred else 0, _captions_outcome(captions_code, captions_enabled)))
        _sink(100, "Overlays and captions applied.")
    finally:
        # Same flush discipline as `run_child`: close and join the feeder
        # thread so every message is on the pipe before this process exits.
        q.close()
        q.join_thread()


def _layer_count(project: Path) -> int:
    """How many pre-rendered layers the burn had to bake, for the record."""
    import json
    from contextlib import suppress as _suppress

    with _suppress(Exception):
        layers = json.loads((project / "render" / "_final_layers.json").read_text(encoding="utf-8"))
        if isinstance(layers, list):
            return len(layers)
    return 0


def _captions_outcome(code: int, enabled: bool) -> str:
    if code == 0:
        return "burned"
    if not enabled:
        return "skipped: captions are switched off for this channel"
    return "skipped: no transcription was available"

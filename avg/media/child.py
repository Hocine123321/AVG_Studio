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

# Messages put on the queue are plain tuples so they stay cheaply picklable:
#   ("progress", percent: int, message: str)
#   ("result", output_path: str, size_bytes: int)
# Nothing is put on failure — the parent infers failure from the exit code.


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

            vendor_avg.PROGRESS_SINK = _sink
            try:
                # May call sys.exit(1) on hard errors (see its docstring) —
                # that SystemExit propagates out of run_child and becomes
                # the child's exit code, which is exactly what
                # classify_exit is for. Not caught here.
                vendor_avg.render_project(**kwargs)
            finally:
                vendor_avg.PROGRESS_SINK = None

            output = Path(kwargs["project_path"]) / "master_output.mp4"
            size = output.stat().st_size if output.exists() else 0
            q.put(("result", str(output), size))
    finally:
        # multiprocessing.Queue buffers puts and feeds them to the
        # underlying pipe on a background thread. Closing and joining that
        # thread here guarantees every message we put is actually flushed
        # before this process exits, instead of trusting interpreter-
        # shutdown ordering to do it for us.
        q.close()
        q.join_thread()

"""Native desktop shell for AVG Studio.

Wraps the FastAPI bridge (`avg.api.app.create_app`) in a `pywebview` window
so the app runs as a normal Windows desktop program rather than "open a
browser and hit localhost". The server and the window live in the same
process: uvicorn serves the ASGI app on a background daemon thread while
`webview.start()` blocks the main thread running the native window's
message loop.

Two things here are load-bearing for safety, not just style:

- **Loopback only.** The server binds `HOST` (`127.0.0.1`), never
  `0.0.0.0`. Nothing outside this machine can ever reach it, regardless of
  firewall state - see `main`.
- **The token never touches disk.** `make_token()` generates a fresh
  `secrets.token_urlsafe(32)` every time `main()` runs. It lives only in
  this process's memory for the lifetime of this run; it is not written to
  a file, a config key, or an environment variable, and a new run always
  gets a new one.

`OUTPUT_ROOT` is not derived from anything and nothing here derives
`config.json` from it either - both are fixed by an explicit decision
recorded after Task 7 (`.superpowers/sdd/progress.md`): for Slice 1 the
legacy on-disk layout is `output_root = D:\\AI_Video_Gen\\output` and
`config.json = D:\\AI_Video_Gen\\config\\config.json`. `create_app` already
derives the second path from the first internally
(`output_root.parent / "config" / "config.json"`), and for this exact
`OUTPUT_ROOT` that derivation already lands on the right file - so
`create_app`'s existing two-argument signature did not need widening;
this module just has to pass the real root instead of a stand-in, which is
what `OUTPUT_ROOT` below does.
"""

from __future__ import annotations

import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import uvicorn
import webview

from avg.api.app import create_app
from avg.media.ffmpeg_locate import prepend_to_path

HOST = "127.0.0.1"

# Fixed per the controller's decision after Task 7 (see module docstring) -
# not guessed here, and nothing in this module derives one path from the
# other.
OUTPUT_ROOT = Path(r"D:\AI_Video_Gen\output")

_READY_TIMEOUT_S = 10.0
_READY_POLL_INTERVAL_S = 0.05


def pick_port() -> int:
    """Return a free ephemeral TCP port on the loopback interface.

    Binds `127.0.0.1:0` (port `0` asks the OS to assign any currently-free
    port), reads the assigned port back via `getsockname()`, then the `with`
    block closes the socket immediately - it is never left open past this
    function returning. There is an unavoidable race between that close and
    uvicorn's later bind to the same number (something else on the machine
    could grab it in between); that race is inherent to "ask the OS for a
    free port, then hand the number to a second process" and is accepted
    here, but this function does not widen it by holding the socket open any
    longer than it takes to read the port back.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def make_token() -> str:
    """Generate a fresh, unguessable per-session token.

    `secrets.token_urlsafe(32)` - 32 random bytes, base64url-encoded, well
    above the 32-character floor `tests/test_shell.py` checks. Never
    persisted anywhere: it lives only in process memory for this run, and
    every run mints its own.
    """
    return secrets.token_urlsafe(32)


def _wait_until_ready(url: str, token: str, timeout: float = _READY_TIMEOUT_S) -> None:
    """Block until `url` answers authenticated with `token`, or raise.

    Starting uvicorn on a thread and immediately opening the webview window
    is a race: the window can start loading before the server has bound its
    socket and finished constructing the ASGI app, and would show a
    connection-error page instead of the app. Polling a real authenticated
    route (`GET /api/projects`, the same token the window itself will use)
    proves the whole stack - socket bound, ASGI app built, auth dependency
    wired - is actually answering, not just that something is listening on
    the port.

    Retries on any connection-level failure (nobody home yet) every
    `_READY_POLL_INTERVAL_S` seconds until `timeout` elapses, then raises a
    `RuntimeError` naming what never answered - never lets the window open
    against a dead server.
    """
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    request = urllib.request.Request(url, headers={"X-AVG-Token": token})
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=1.0) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
        time.sleep(_READY_POLL_INTERVAL_S)
    raise RuntimeError(
        f"AVG API server did not answer {url!r} within {timeout}s"
    ) from last_error


def main() -> None:
    """Wire up the background server and the native window, then run.

    Picks a port and a token, builds the app, starts uvicorn on a daemon
    thread bound to loopback only, waits for it to actually answer, opens
    the window, and runs the native event loop until the window closes -
    at which point the server thread is told to stop and joined so nothing
    outlives the window.
    """
    # Frozen builds ship their own ffmpeg/ffprobe (vendor/ffmpeg) because a
    # clean target machine has neither on PATH. This must run before
    # anything shells out - including a render child process, which
    # inherits this modified PATH from the environment at the time it is
    # spawned. No-op in development. See avg.media.ffmpeg_locate.
    prepend_to_path()

    port = pick_port()
    token = make_token()
    app = create_app(token=token, output_root=OUTPUT_ROOT)

    server = uvicorn.Server(
        uvicorn.Config(app, host=HOST, port=port, log_level="warning")
    )
    server_thread = threading.Thread(target=server.run, daemon=True, name="avg-api-server")
    server_thread.start()

    try:
        _wait_until_ready(f"http://{HOST}:{port}/api/projects", token)
    except RuntimeError:
        server.should_exit = True
        raise

    webview.create_window(
        "AVG Studio",
        f"http://{HOST}:{port}/?token={token}",
        width=1280,
        height=820,
    )
    webview.start()

    server.should_exit = True
    server_thread.join(timeout=5)


if __name__ == "__main__":
    # Required before anything else runs in a frozen build: avg.media.engine
    # spawns render children via multiprocessing's "spawn" start method
    # (the only one Windows has), which re-executes this same frozen exe
    # with special argv. Without freeze_support() that re-execution would
    # re-enter main() instead of the multiprocessing bootstrap and spawn
    # copies of the whole app recursively. It is a no-op in development and
    # a no-op on any platform other than Windows, so it is always safe here.
    import multiprocessing

    multiprocessing.freeze_support()
    main()

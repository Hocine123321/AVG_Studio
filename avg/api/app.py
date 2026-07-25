"""FastAPI bridge between the AVG engine and the web UI.

This is the first layer allowed to face outward. Everything below it
(`avg.core`, `avg.config`, `avg.media`) is forbidden from printing or
prompting - they emit typed events, and this layer decides what actually
reaches the user, as an HTTP response or a WebSocket message.

Routes (see the task-7 brief for the exact contract):

    GET  /api/projects                      -> {"projects": [ProjectDoc, ...]}
    POST /api/projects/{project_id}/render   -> 202 {"started": true}
    POST /api/projects/{project_id}/cancel   -> {"cancelled": true}
    WS   /ws/events                          -> a stream of avg.core.events models as JSON

Construction goes exclusively through `create_app(token, output_root)` so
tests (and `avg.shell`) can each build a fully isolated instance - no
module-level globals, no shared state between instances.
"""

from __future__ import annotations

import asyncio
import ctypes
import threading
from contextlib import suppress
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status

from avg.config.legacy_import import import_all
from avg.config.settings_import import load_render_settings
from avg.core.events import PhaseFailed, ProgressEvent, RenderCancelled, RenderFinished
from avg.core.models import PhaseRecord, ProjectDoc, RenderRequest
from avg.media.engine import MediaEngine

from .deps import require_token

Event = ProgressEvent | PhaseFailed | RenderCancelled | RenderFinished


def _total_ram_gb() -> float:
    """Best-effort total physical RAM in GB, for `load_render_settings`'s RAM gates.

    Windows-only (this project targets Windows exclusively) via
    `GlobalMemoryStatusEx` - psutil is not one of this package's
    dependencies, and pulling it in just for this one number is not worth
    it. Falls back to 8.0 (this project's own dev machine) if the call
    fails for any reason, which keeps the low-resource render paths active
    rather than silently assuming a big machine.
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


def _build_render_request(project: ProjectDoc, output_root: Path) -> RenderRequest:
    """Combine a project's folder with the legacy app's render settings.

    Slice 1 has no settings-editing UI yet (that is Task 11's territory),
    so the only `config.json` in play is the legacy one, expected as a
    sibling of `output_root` (mirroring the old app's layout: `<root>/config/
    config.json` next to `<root>/output/`). If it is missing or unreadable
    - which is every test that exercises this bridge, since they all point
    `output_root` at an empty `tmp_path` - fall back to `RenderRequest`'s
    own defaults plus what the imported project doc already knows (aspect
    ratio, short edge).
    """
    config_path = output_root.parent / "config" / "config.json"
    try:
        settings = load_render_settings(config_path, _total_ram_gb())
    except Exception:
        return RenderRequest(
            project_folder=project.folder,
            aspect_ratio=project.aspect_ratio,
            short_edge=project.short_edge,
        )

    return RenderRequest(
        project_folder=project.folder,
        fps=settings.fps,
        codec=settings.codec,
        aspect_ratio=settings.aspect_ratio,
        ken_burns=settings.ken_burns,
        short_edge=settings.short_edge,
        kb_intensity=settings.kb_intensity,
        kb_mode=settings.kb_mode,
        render_engine=settings.render_engine,
        render_opts=settings.render_opts,
    )


class _ConnectionManager:
    """Tracks connected `/ws/events` sockets and fans engine events out to them.

    A dead or slow socket must never block delivery to the others and must
    never take the whole broadcast down with it - each send is isolated,
    and a socket that fails to receive is dropped rather than retried.
    """

    def __init__(self) -> None:
        self._sockets: set[WebSocket] = set()

    def add(self, ws: WebSocket) -> None:
        self._sockets.add(ws)

    def remove(self, ws: WebSocket) -> None:
        self._sockets.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        for ws in list(self._sockets):
            try:
                await ws.send_json(payload)
            except Exception:
                self._sockets.discard(ws)


def _apply_terminal_event(project: ProjectDoc | None, event: Event) -> None:
    """Reflect a render's outcome in the cached ProjectDoc.

    Projects are imported once at startup (see `create_app`) precisely so
    the home grid never re-walks the output folder - but that means a
    finished/failed/cancelled render has to be folded back into the same
    cached copy by hand, or `GET /api/projects` would show stale state
    forever after the first render.
    """
    if project is None or isinstance(event, ProgressEvent):
        return

    record = project.phases.get("render")
    if record is None:
        record = PhaseRecord(name="render")
        project.phases["render"] = record

    if isinstance(event, RenderFinished):
        record.status = "done"
        record.error = None
        project.master_output = event.output
    elif isinstance(event, PhaseFailed):
        record.status = "failed"
        record.error = event.message
    elif isinstance(event, RenderCancelled):
        record.status = "pending"
        record.error = None


def create_app(token: str, output_root: Path) -> FastAPI:
    """Build one isolated FastAPI instance.

    This is the only construction entry point - tests build a throwaway
    instance per test (see `tests/api/test_app.py`), and `avg.shell` builds
    exactly one per process with a freshly generated token and the real
    output folder.
    """
    output_root = Path(output_root)
    app = FastAPI()

    app.state.token = token
    app.state.output_root = output_root
    app.state.engine = MediaEngine()
    app.state.manager = _ConnectionManager()
    # A single background thread at a time - MediaEngine itself only ever
    # tracks one child process (see its docstring), so a second render
    # starting while one is alive would silently overwrite that handle and
    # leave `cancel()` unable to reach the first one. Slice 1 renders one
    # project at a time; this is what enforces that instead of assuming it.
    app.state.render_thread = None
    app.state.rendering_project_id = None

    # Imported exactly once, here, at construction time - not per-request,
    # and not behind an ASGI lifespan/startup event: TestClient only runs
    # lifespan handlers when used as a context manager (`with TestClient(...)`),
    # which the brief's own fixture does not do, and the real app only ever
    # calls create_app() once per process anyway. "Once at startup" means
    # here.
    report = import_all(output_root)
    app.state.projects: dict[str, ProjectDoc] = {p.id: p for p in report.imported}

    @app.get("/api/projects", dependencies=[Depends(require_token)])
    async def list_projects() -> dict:
        return {"projects": list(app.state.projects.values())}

    @app.post(
        "/api/projects/{project_id}/render",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_token)],
    )
    async def start_render(project_id: str) -> dict:
        project = app.state.projects.get(project_id)
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown project")

        if app.state.render_thread is not None and app.state.render_thread.is_alive():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"a render is already running for project {app.state.rendering_project_id!r}",
            )

        req = _build_render_request(project, app.state.output_root)
        loop = asyncio.get_running_loop()

        async def _dispatch(event: Event) -> None:
            # Runs on the event loop (via run_coroutine_threadsafe below),
            # never on the worker thread - so the cached-project mutation
            # below never races a concurrent GET /api/projects.
            await app.state.manager.broadcast(event.model_dump(mode="json"))
            _apply_terminal_event(app.state.projects.get(project_id), event)

        def _worker() -> None:
            try:
                # A plain `for` loop that is never broken out of early -
                # this is what guarantees MediaEngine.render's generator
                # (and the child-process join in its `finally`) runs to
                # completion even when it ends in PhaseFailed or
                # RenderCancelled rather than RenderFinished. Nobody else
                # consumes this generator, so if this loop stopped early
                # the child would never be joined.
                for event in app.state.engine.render(req, project_id):
                    with suppress(Exception):
                        # Both submitting to the loop and waiting for
                        # delivery are inside the same suppress: if the
                        # loop has gone away (or a socket write blows up)
                        # `run_coroutine_threadsafe` itself raises
                        # synchronously, before `fut.result()` is ever
                        # reached. Either failure must never abort this
                        # `for` loop early - that would skip the rest of
                        # the drain and leave the child process unjoined.
                        fut = asyncio.run_coroutine_threadsafe(_dispatch(event), loop)
                        fut.result()
            finally:
                app.state.render_thread = None
                app.state.rendering_project_id = None

        app.state.rendering_project_id = project_id
        thread = threading.Thread(target=_worker, daemon=True, name=f"avg-render-{project_id}")
        app.state.render_thread = thread
        thread.start()
        return {"started": True}

    @app.post("/api/projects/{project_id}/cancel", dependencies=[Depends(require_token)])
    async def cancel_render(project_id: str) -> dict:
        if project_id not in app.state.projects:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown project")
        app.state.engine.cancel()
        return {"cancelled": True}

    @app.websocket("/ws/events")
    async def ws_events(websocket: WebSocket, token: str | None = None) -> None:
        # Browsers cannot set headers on a WebSocket handshake, so the
        # token travels as a query parameter here instead of the
        # X-AVG-Token header the HTTP routes use. Rejecting before accept()
        # denies the handshake outright (the client sees a close, never a
        # connection) rather than accepting and then dropping it.
        if token != websocket.app.state.token:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await websocket.accept()
        app.state.manager.add(websocket)
        try:
            await websocket.send_json({"type": "hello"})
            while True:
                # The client never needs to send anything; awaiting here is
                # just how this coroutine notices the client disconnecting.
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            app.state.manager.remove(websocket)

    return app

"""FastAPI bridge between the AVG engine and the web UI.

This is the first layer allowed to face outward. Everything below it
(`avg.core`, `avg.config`, `avg.media`) is forbidden from printing or
prompting - they emit typed events, and this layer decides what actually
reaches the user, as an HTTP response or a WebSocket message.

Routes (see the task-7 and task-10.5 briefs for the exact contract):

    GET  /api/projects                          -> {"projects": [ProjectDoc-shaped dict + script_substate, ...]}
    POST /api/projects                           -> 201 ProjectDoc (creates + starts the full pipeline)
    POST /api/projects/{project_id}/render       -> 202 {"started": true}
    POST /api/projects/{project_id}/cancel       -> {"cancelled": true}
    GET  /api/projects/{project_id}/thumbnail    -> image/jpeg
    GET  /api/projects/{project_id}/video        -> video/mp4 (honours Range -> 206)
    GET  /api/projects/{project_id}/images       -> {"images": [filename, ...]}
    GET  /api/projects/{project_id}/images/{f}   -> image/* (one file from images/)
    GET  /api/settings                           -> the settings document, plus a "suggestion"
    POST /api/settings                           -> save {"output_root": ...}, set onboarded
    GET  /api/settings/scan?path=                -> {"project_count": N} for an arbitrary path
    WS   /ws/events                              -> a stream of avg.core.events models as JSON

Construction goes exclusively through `create_app(token, output_root)` so
tests (and `avg.shell`) can each build a fully isolated instance - no
module-level globals, no shared state between instances.
"""

from __future__ import annotations

import asyncio
import ctypes
import threading
import traceback
from contextlib import suppress
from pathlib import Path
from collections.abc import Iterable
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from avg.config import channels as channels_config
from avg.config.legacy_import import import_all
from avg.config.settings import SettingsDoc, default_settings_path, load_settings, save_settings, suggest_output_root
from avg.config.settings import load_render_settings
from avg.core.error_store import ErrorStore, classify
from avg.core.model_cache import ModelCache
from avg.core.events import PhaseFailed, ProgressEvent, RenderCancelled, RenderFinished
from avg.core.models import PhaseRecord, ProjectDoc, RenderRequest
from avg.core.settings_schema import SettingValidationError, get_spec, validate_value
from avg.core.takes import take_folder
from avg.media.engine import MediaEngine
from avg.media.thumbnails import IMAGE_EXTENSIONS, default_cache_dir, list_image_files, thumbnail_for
from avg.media.environment import render_workspace
from avg.pipeline.create import new_project, save_project
from avg.pipeline.phase import CancellationToken
from avg.pipeline.phases.render import _render_kwargs
from avg.pipeline.phases.writing import script_substate
from avg.pipeline.runner import run as run_pipeline
from avg.providers.text.router import config_from_settings

from .deps import require_token
from .routes_settings import router as settings_router
from .routes_takes import router as takes_router

Event = ProgressEvent | PhaseFailed | RenderCancelled | RenderFinished


class SettingsUpdate(BaseModel):
    """`POST /api/settings`'s body. Module-level (not nested in `create_app`)
    so FastAPI's `from __future__ import annotations`-deferred type
    resolution can actually find it via the module's globals - a class
    defined inside `create_app` is only reachable through the enclosing
    function's locals, which FastAPI's dependant analysis does not see,
    and the route silently degrades to expecting a query parameter instead
    of a JSON body."""

    output_root: str


class ArchiveRequest(BaseModel):
    """`POST /api/projects/archive` - one call for a whole selection.

    `archived` is explicit rather than a toggle: a mixed selection has no
    sensible thing to toggle to, and the caller always knows which of
    "Archive" or "Restore" the user pressed.
    """

    ids: list[str]
    archived: bool = True


class ProjectCreate(BaseModel):
    """`POST /api/projects`'s body: a topic plus any per-video overrides of
    the active channel's creative settings (`avg.core.settings_schema`'s
    `scope="channel"` ids only - anything else is rejected).

    `overrides` never touches the channel document on disk: the route below
    folds it onto a throwaway `Channel.model_copy`, never `save_channel`,
    matching `avg.config.channels`'s "a channel's document is the only
    place its settings live" rule - a per-video choice must not leak back
    into the channel every other video on it will use.
    """

    topic: str
    overrides: dict[str, Any] = Field(default_factory=dict)


# The built web UI (`cd web && npm run build`) - a sibling of `avg/` at the
# repo root, not a package resource, since it is built by npm and gitignored
# (`web/dist` and `web/node_modules`), never committed. `avg/api/app.py` ->
# parents[0] is `avg/api`, parents[1] is `avg`, parents[2] is the repo root.
WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

# How long a request will wait for the startup library scan before answering
# with whatever it has. Generous: the scan is local filesystem work and this
# is a ceiling, not a delay anyone normally pays.
_LIBRARY_WAIT_S = 30.0


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


def _library_bytes(projects: Iterable[ProjectDoc]) -> int:
    """Total bytes across every project folder.

    Counts each project's WHOLE folder - every take's subfolder plus any
    legacy top-level artifacts - matching what the Studio header already
    reports per project, so the library total and the per-project figures
    are the same measurement at two scales rather than two different ones.

    A project whose files were removed out-of-band contributes nothing
    rather than raising: this feeds a number on a card, and one missing
    folder must not cost the whole figure.
    """
    total = 0
    for project in projects:
        folder = Path(project.folder)
        if not folder.is_dir():
            continue
        with suppress(OSError):
            total += sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
    return total


def _build_render_request(
    project: ProjectDoc, output_root: Path, *, channel_settings: dict | None = None
) -> RenderRequest:
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
            **_render_kwargs(channel_settings or {}),
        )

    # Merged into ONE dict rather than spread after the explicit keywords.
    # Spreading collided: `_render_kwargs` returns `fps`, `codec`,
    # `ken_burns` and `render_engine` too, so any channel that had one of
    # them set raised `RenderRequest() got multiple values for keyword
    # argument 'render_engine'` and every render returned 500. `.update()`
    # keeps the intent - the user's own channel settings win over the
    # legacy config.json, which they cannot edit from this app - without
    # the duplicate-keyword crash.
    kwargs: dict[str, object] = {
        "fps": settings.fps,
        "codec": settings.codec,
        "aspect_ratio": settings.aspect_ratio,
        "ken_burns": settings.ken_burns,
        "short_edge": settings.short_edge,
        "kb_intensity": settings.kb_intensity,
        "kb_mode": settings.kb_mode,
        "render_engine": settings.render_engine,
        "render_opts": settings.render_opts,
    }
    kwargs.update(_render_kwargs(channel_settings or {}))
    return RenderRequest(project_folder=project.folder, **kwargs)  # type: ignore[arg-type]


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


def create_app(
    token: str,
    output_root: Path,
    thumb_cache_dir: Path | None = None,
    settings_path: Path | None = None,
    channels_dir: Path | None = None,
    secrets_path: Path | None = None,
    errors_path: Path | None = None,
    model_cache_path: Path | None = None,
) -> FastAPI:
    """Build one isolated FastAPI instance.

    This is the only construction entry point - tests build a throwaway
    instance per test (see `tests/api/test_app.py`), and `avg.shell` builds
    exactly one per process with a freshly generated token and the real
    output folder.

    `thumb_cache_dir` defaults to `avg.media.thumbnails.default_cache_dir()`
    (`%LOCALAPPDATA%/AVG Studio/thumbs`) - the optional override exists so
    tests can point it at a throwaway `tmp_path` instead of writing into the
    real user profile on every test run. `settings_path`, `channels_dir`
    and `secrets_path` are the same idea for `avg.config.settings`'s
    `%APPDATA%/AVG Studio/settings.json`, `avg.config.channels`'s
    `.../channels/` folder, and `avg.config.secrets`'s `.../secrets.dat` -
    all three stay `None` by default so each module resolves its own real
    default path, exactly like `settings_path` already did.
    """
    output_root = Path(output_root)
    app = FastAPI()

    app.state.token = token
    app.state.output_root = output_root
    app.state.engine = MediaEngine()
    app.state.manager = _ConnectionManager()
    app.state.thumb_cache_dir = Path(thumb_cache_dir) if thumb_cache_dir is not None else default_cache_dir()
    app.state.settings_path = Path(settings_path) if settings_path is not None else None
    app.state.channels_dir = Path(channels_dir) if channels_dir is not None else None
    app.state.secrets_path = Path(secrets_path) if secrets_path is not None else None
    # Errors live beside the settings document, so a user who moves their
    # profile takes the log with them. Overridable like every other path so
    # tests never write into the real %APPDATA%.
    app.state.error_store = ErrorStore(
        Path(errors_path)
        if errors_path is not None
        else default_settings_path().with_name("errors.json")
    )
    # Which models a probe proved working. Beside the settings document for
    # the same reason the error log is, and NOT the same file as
    # `avg/providers/models.py`'s `model_cache.json`, which caches what a
    # provider *advertises* - see `avg.core.model_cache`'s docstring.
    app.state.model_cache = ModelCache(
        Path(model_cache_path)
        if model_cache_path is not None
        else default_settings_path().with_name("working_models.json")
    )
    # One probe sweep at a time: it costs a call per model on every keyed
    # provider, and two at once would double that for nothing.
    app.state.probe_thread = None

    @app.middleware("http")
    async def _record_unhandled(request: Request, call_next):
        """Every unhandled exception becomes a stored, severity-tagged error.

        Without this a 500 from a packaged build is a dead end: the user
        sees "500 internal server error", the traceback goes to a console
        that a windowed build does not have, and nothing in the app
        remembers what happened.

        Re-raises after recording. This observes; it does not swallow - a
        request that failed must still fail.
        """
        try:
            return await call_next(request)
        except Exception as exc:
            with suppress(Exception):
                app.state.error_store.record(
                    severity=classify(kind=None, status=500, source="api"),
                    source="api",
                    summary=f"{type(exc).__name__}: {exc}",
                    detail="".join(traceback.format_exception(exc)),
                    context={"path": request.url.path, "method": request.method},
                )
            raise

    # A single background thread at a time - MediaEngine itself only ever
    # tracks one child process (see its docstring), so a second render
    # starting while one is alive would silently overwrite that handle and
    # leave `cancel()` unable to reach the first one. Slice 1 renders one
    # project at a time; this is what enforces that instead of assuming it.
    app.state.render_thread = None
    app.state.rendering_project_id = None
    # Set only while a `POST /api/projects`-started full pipeline is
    # running (never by `/render`'s MediaEngine path) - `/cancel` below
    # pokes this in addition to `engine.cancel()` so "Cancel" works no
    # matter which of the two job kinds is currently occupying
    # `render_thread`.
    app.state.pipeline_cancel_token = None

    # Imported exactly once, here, at construction time - not per-request,
    # and not behind an ASGI lifespan/startup event: TestClient only runs
    # lifespan handlers when used as a context manager (`with TestClient(...)`),
    # which the brief's own fixture does not do, and the real app only ever
    # calls create_app() once per process anyway. "Once at startup" means
    # here.
    # Scanned on a background thread, NOT here. Reading and parsing every
    # project on disk used to happen inline at construction, which meant the
    # server could not bind, `_wait_until_ready` could not return, and the
    # window could not open until the whole library had been walked - the
    # user stared at nothing for the length of a filesystem scan that has
    # nothing to do with drawing a window.
    #
    # The cost of deferring it is a brief window where the library is empty
    # and a project id is genuinely not found yet. `projects_loading` is
    # what makes that honest rather than indistinguishable from "you have no
    # projects": `GET /api/projects` reports it, and the UI says "Loading"
    # instead of "No projects yet".
    app.state.projects: dict[str, ProjectDoc] = {}
    app.state.projects_loading = True
    # Total bytes across every project folder, for Home's "Storage used"
    # card. Summed once by the background scan that is already walking this
    # tree, rather than per request: it is a full recursive stat of the whole
    # library, which is far too expensive to do on every list call and does
    # not change unless a render or an import does.
    app.state.storage_bytes = 0
    # Set once the first scan has finished, successfully or not. Requests
    # wait on this; the WINDOW never does, which is the whole point.
    app.state.library_loaded = threading.Event()

    def _load_library() -> None:
        """Scan the output root, then warm thumbnails. One thread, in order.

        Sequential and in this order on purpose: the grid needs the project
        list to show anything at all, and a thumbnail is useless before the
        card it belongs to exists.
        """
        try:
            report = import_all(output_root)
            app.state.projects = {p.id: p for p in report.imported}
        except Exception:
            # A library that fails to scan leaves the app usable and empty
            # rather than dead. Nothing below the API layer may print, and
            # there is no user yet to tell.
            pass
        finally:
            app.state.projects_loading = False
            # Released BEFORE the size walk below. `GET /api/projects` waits
            # on this event, and a full recursive stat of every project
            # folder is not something the library list should be held up by -
            # it is one card's number, not the grid. Overview shows a dash
            # until it lands, which is already what an absent
            # `storage_bytes` means to it.
            app.state.library_loaded.set()

        with suppress(Exception):
            app.state.storage_bytes = _library_bytes(app.state.projects.values())
        _warm_thumbnails()

    def _warm_thumbnails() -> None:
        """Generate every thumbnail once, in the background, at startup.

        Extraction shells out to ffmpeg and is lazy per request. Without this,
        a first launch loads the grid and fires one cold request per project
        at the same instant - ten ffmpeg processes at once on an 8 GB machine
        - and the images lose the race, so the user's first impression is a
        wall of empty cards that only fills in on a reload.

        Sequential on purpose: warming is background work and must not
        contend with a render for CPU. Failures are per-project and silent
        here; a genuinely missing thumbnail surfaces as the UI's fallback.
        """
        for proj in list(app.state.projects.values()):
            with suppress(Exception):
                thumbnail_for(proj, app.state.thumb_cache_dir)

    threading.Thread(target=_load_library, daemon=True, name="avg-library-load").start()

    def _library() -> dict[str, ProjectDoc]:
        """The project cache, once the startup scan has had its chance.

        Every by-id lookup goes through this rather than touching
        `app.state.projects` directly. Without it, deferring the scan off
        the startup path would turn "open the app straight into a project"
        into a 404 for as long as the scan took - a real race for anyone
        deep-linking, not just a test artifact.

        Blocking here is safe and blocking at construction was not: this
        waits inside ONE request, while the window and every other route
        carry on.
        """
        app.state.library_loaded.wait(_LIBRARY_WAIT_S)
        return app.state.projects

    @app.get("/api/projects", dependencies=[Depends(require_token)])
    async def list_projects() -> dict:
        # Waits for the first scan, but only here - a REQUEST may block on
        # the library; the window may not. That split is what makes startup
        # fast without ever answering "you have no projects" to someone who
        # has fifteen. The bound exists so a pathological library cannot
        # hang the route forever; past it, the partial list plus
        # `loading: true` is the honest answer.
        await asyncio.to_thread(app.state.library_loaded.wait, _LIBRARY_WAIT_S)

        # ``script_substate`` is derived from artifacts at read time, not put
        # on ProjectDoc: the document is persisted, while this display-only
        # value must always reflect the current take's files.
        projects = []
        for project in app.state.projects.values():
            root = (
                take_folder(project, project.current_take_id)
                if project.current_take_id is not None
                else Path(project.folder)
            )
            projects.append(
                {
                    **project.model_dump(mode="json"),
                    "script_substate": script_substate(root / "script"),
                }
            )
        return {
            "projects": projects,
            "loading": bool(app.state.projects_loading),
            # Zero until the scan finishes, which `loading` is what
            # distinguishes from a genuinely empty library.
            "storage_bytes": int(app.state.storage_bytes),
        }

    @app.post("/api/projects/archive", dependencies=[Depends(require_token)])
    async def archive_projects(body: ArchiveRequest) -> dict:
        """Archive or unarchive a set of projects in one call.

        A set rather than one id per request because Home's select bar acts
        on a selection: five separate PATCHes could half-succeed and leave
        the user looking at a partially-applied bulk action. Unknown ids are
        a 404 naming them, checked before anything is written - so this
        either applies to every id or to none.

        Archiving hides a project from Home's default view and does nothing
        else. No file moves, nothing is deleted, and it is reversible by
        sending the same ids with `archived: false`.
        """
        library = _library()
        unknown = [pid for pid in body.ids if pid not in library]
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown project(s): {unknown}"
            )
        for pid in body.ids:
            project = library[pid]
            project.archived = body.archived
            save_project(project)
        return {"archived": body.archived, "ids": list(body.ids)}

    @app.post("/api/projects", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_token)])
    async def create_project(body: ProjectCreate) -> ProjectDoc:
        """Create a project for `body.topic` under the active channel, then
        run the whole pipeline (`avg.pipeline.registry.REGISTRY`, phase 0
        on) against it on a background thread - the exact
        drain-a-generator-and-broadcast pattern `start_render` below already
        uses for `MediaEngine.render()`, reused rather than duplicated (see
        this module's docstring on why that split exists). Returns the
        freshly created `ProjectDoc` immediately, well before the pipeline
        itself finishes, so the caller can navigate to Studio and watch it
        run over `/ws/events`.
        """
        topic = body.topic.strip()
        if not topic:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="topic must not be empty")

        active_id = channels_config.active_channel_id(app.state.settings_path)
        if active_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="no active channel — activate a channel before creating a video",
            )
        channel = channels_config.get_channel(active_id, app.state.channels_dir)
        if channel is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"active channel {active_id!r} no longer exists",
            )

        # Validate every override against the catalogue and fold it onto a
        # THROWAWAY copy of the channel's settings dict - `channel` itself
        # is never mutated or saved (see `ProjectCreate`'s docstring above).
        effective_settings = dict(channel.settings)
        for setting_id, raw_value in body.overrides.items():
            spec = get_spec(setting_id)
            if spec is None or spec.scope != "channel":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{setting_id!r} is not a per-video creative setting",
                )
            try:
                effective_settings[setting_id] = validate_value(spec, raw_value)
            except SettingValidationError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        effective_channel = channel.model_copy(update={"settings": effective_settings})

        # Built here, not inside the worker: a malformed 'provider/model'
        # setting is a bad request, and must be a 400 the user sees rather
        # than a phase that fails minutes later. Until this existed the
        # runner was handed `router=None` and every text phase died on
        # `None.phases` - the whole per-phase routing layer was unreachable.
        try:
            router_config = config_from_settings(
                effective_channel.settings,
                # Channels written before the models group existed have no
                # `text_model_default` key; the catalogue's own default is
                # what they get, not an error.
                fallback_default=str(get_spec("text_model_default").default),  # type: ignore[union-attr]
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        # The same one-job-at-a-time guard `start_render` enforces below -
        # a full pipeline run and a `/render` MediaEngine run share the one
        # `render_thread` slot, never a second job mechanism.
        if app.state.render_thread is not None and app.state.render_thread.is_alive():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"a pipeline is already running for project {app.state.rendering_project_id!r}",
            )

        project = new_project(topic, effective_channel, app.state.output_root)
        app.state.projects[project.id] = project

        cancel_token = CancellationToken()
        app.state.pipeline_cancel_token = cancel_token
        loop = asyncio.get_running_loop()

        async def _dispatch(event: Any) -> None:
            await app.state.manager.broadcast(event.model_dump(mode="json"))

        def _worker() -> None:
            try:
                # `project` is the SAME object cached in `app.state.projects`
                # - `run_pipeline` mutates its `.phases[key]` records in
                # place (see `avg.pipeline.runner.run`'s own docstring), so
                # no separate `_apply_terminal_event`-style step is needed
                # here: a `GET /api/projects` (or this very object) already
                # reflects each phase's outcome as it happens.
                for event in run_pipeline(
                    project,
                    channel=effective_channel,
                    router=router_config,
                    start_from=0,
                    cancel_token=cancel_token,
                    on_phase_change=lambda: save_project(project),
                ):
                    with suppress(Exception):
                        fut = asyncio.run_coroutine_threadsafe(_dispatch(event), loop)
                        fut.result()
            finally:
                app.state.render_thread = None
                app.state.rendering_project_id = None
                app.state.pipeline_cancel_token = None

        app.state.rendering_project_id = project.id
        thread = threading.Thread(target=_worker, daemon=True, name=f"avg-pipeline-{project.id}")
        app.state.render_thread = thread
        thread.start()

        return project

    @app.get("/api/settings", dependencies=[Depends(require_token)])
    async def get_settings() -> dict:
        # Read straight from disk (not app.state) - this is the one small
        # document this app owns, and there is no in-memory copy to drift
        # from it between requests. `suggest_output_root` is recomputed
        # every call rather than cached, since it does its own directory
        # walk and the welcome screen's whole point is a fresh, honest count.
        doc = load_settings(app.state.settings_path)
        path, count, source = suggest_output_root()
        return {
            **doc.model_dump(),
            "suggestion": {"path": str(path), "project_count": count, "source": source},
        }

    @app.post("/api/settings", dependencies=[Depends(require_token)])
    async def post_settings(body: SettingsUpdate) -> dict:
        root = Path(body.output_root).expanduser()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"can't use {root} as a library folder: {exc}",
            ) from exc

        doc = SettingsDoc(output_root=str(root), onboarded=True)
        save_settings(doc, app.state.settings_path)

        # Adopt the new root in this already-running process, exactly what
        # create_app does at startup - so "Start" works immediately and
        # never requires relaunching the app to see the chosen library.
        app.state.output_root = root
        report = import_all(root)
        app.state.projects = {p.id: p for p in report.imported}
        app.state.storage_bytes = _library_bytes(app.state.projects.values())
        app.state.projects_loading = False
        threading.Thread(target=_warm_thumbnails, daemon=True, name="avg-thumb-warm-onboard").start()

        return doc.model_dump()

    @app.get("/api/settings/scan", dependencies=[Depends(require_token)])
    async def scan_settings(path: str) -> dict:
        # Powers the live count next to Browse's chosen folder - tolerates a
        # path that doesn't exist yet (import_all does) rather than 404ing,
        # since a not-yet-created folder is a perfectly valid choice (it
        # will be created on accept, see POST /api/settings above).
        count = len(import_all(Path(path).expanduser()).imported)
        return {"project_count": count}

    @app.post(
        "/api/projects/{project_id}/render",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_token)],
    )
    async def start_render(project_id: str) -> dict:
        project = _library().get(project_id)
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown project")

        if app.state.render_thread is not None and app.state.render_thread.is_alive():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"a render is already running for project {app.state.rendering_project_id!r}",
            )

        # The active channel's render_threads, when a channel is active -
        # a direct /render has no channel context of its own, so it reads
        # the same setting the pipeline's render phase would have used.
        active_id = channels_config.active_channel_id(app.state.settings_path)
        channel = channels_config.get_channel(active_id, app.state.channels_dir) if active_id else None
        req = _build_render_request(
            project,
            app.state.output_root,
            channel_settings=(channel.settings if channel else None),
        )
        loop = asyncio.get_running_loop()

        async def _dispatch(event: Event) -> None:
            # Runs on the event loop (via run_coroutine_threadsafe below),
            # never on the worker thread - so the cached-project mutation
            # below never races a concurrent GET /api/projects.
            await app.state.manager.broadcast(event.model_dump(mode="json"))
            _apply_terminal_event(app.state.projects.get(project_id), event)

        def _worker() -> None:
            try:
                # Renders run against a disposable COPY of the project, never
                # the folder itself. render_project writes master_output.mp4
                # into the folder it is given, so rendering in place would
                # overwrite the finished video the user already has - their
                # actual work, with no undo - and destroy the original that
                # Slice 1 exists to compare the new output against.
                with render_workspace(req.project_folder, keep=True) as workspace:
                    _run_render(req.model_copy(update={"project_folder": workspace}))
            finally:
                app.state.render_thread = None
                app.state.rendering_project_id = None

        def _run_render(req: RenderRequest) -> None:
            # A plain `for` loop that is never broken out of early - this is
            # what guarantees MediaEngine.render's generator (and the
            # child-process join in its `finally`) runs to completion even
            # when it ends in PhaseFailed or RenderCancelled rather than
            # RenderFinished. Nobody else consumes this generator, so if this
            # loop stopped early the child would never be joined.
            for event in app.state.engine.render(req, project_id):
                with suppress(Exception):
                    # Both submitting to the loop and waiting for delivery
                    # are inside the same suppress: if the loop has gone away
                    # (or a socket write blows up) `run_coroutine_threadsafe`
                    # itself raises synchronously, before `fut.result()` is
                    # ever reached. Either failure must never abort this
                    # `for` loop early - that would skip the rest of the
                    # drain and leave the child process unjoined.
                    fut = asyncio.run_coroutine_threadsafe(_dispatch(event), loop)
                    fut.result()

        app.state.rendering_project_id = project_id
        thread = threading.Thread(target=_worker, daemon=True, name=f"avg-render-{project_id}")
        app.state.render_thread = thread
        thread.start()
        return {"started": True}

    @app.post("/api/projects/{project_id}/cancel", dependencies=[Depends(require_token)])
    async def cancel_render(project_id: str) -> dict:
        if project_id not in _library():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown project")
        app.state.engine.cancel()
        # Best-effort for a full-pipeline run too - a no-op if `render_thread`
        # is currently a `/render` MediaEngine job instead (or nothing at
        # all). The runner only checks this BETWEEN phases (see
        # `avg.pipeline.runner.run`'s docstring), so this stops the run at
        # the next phase boundary, not mid-phase.
        if app.state.pipeline_cancel_token is not None:
            app.state.pipeline_cancel_token.cancel()
        return {"cancelled": True}

    @app.get("/api/projects/{project_id}/thumbnail", dependencies=[Depends(require_token)])
    def project_thumbnail(project_id: str) -> FileResponse:
        # A plain `def` (not `async def`) - FastAPI runs synchronous path
        # operations in a worker thread automatically, so the blocking
        # ffmpeg/ffprobe subprocess calls inside thumbnail_for() never stall
        # the event loop (and, with it, every other in-flight request,
        # including the WebSocket carrying live render progress).
        project = _library().get(project_id)
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown project")

        thumb = thumbnail_for(project, app.state.thumb_cache_dir)
        if thumb is None:
            # No master_output.mp4 AND no image in images/ (or ffmpeg itself
            # is missing) - the UI's own placeholder covers this, so a plain
            # 404 is honest rather than synthesizing a fake image.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no thumbnail available")
        return FileResponse(thumb, media_type="image/jpeg")

    @app.get("/api/projects/{project_id}/video", dependencies=[Depends(require_token)])
    def project_video(project_id: str) -> FileResponse:
        # FileResponse honours an incoming Range header itself (it reads the
        # request headers straight off the ASGI scope) and answers with a
        # real 206 Partial Content + Content-Range - required for a <video>
        # element's seek bar to work at all in WebView2. Verified with an
        # actual ranged request against a real project's master_output.mp4
        # rather than assumed - see the task-10.5 report.
        project = _library().get(project_id)
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown project")

        if project.master_output is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no video for this project")

        video_path = Path(project.master_output)
        if not video_path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="video file missing on disk")

        return FileResponse(video_path, media_type="video/mp4")

    @app.get("/api/projects/{project_id}/images", dependencies=[Depends(require_token)])
    def list_project_images(project_id: str) -> dict:
        # Backs Studio's "visuals" panel: real filenames from the project's
        # own images/ folder, not a fabricated per-scene manifest - Slice 1
        # has no generation-progress data per scene, only whichever files
        # have landed on disk so far.
        project = _library().get(project_id)
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown project")
        files = list_image_files(project.folder / "images")
        return {"images": [f.name for f in files]}

    @app.get("/api/projects/{project_id}/images/{filename}", dependencies=[Depends(require_token)])
    def project_image(project_id: str, filename: str) -> FileResponse:
        project = _library().get(project_id)
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown project")

        # Belt and suspenders against path traversal: a "/" or "\\" in a
        # single {filename} path segment can't actually reach here (Starlette's
        # router wouldn't match this route for a request path containing an
        # extra segment), but a bare ".." could still resolve upward, so both
        # the character check and the resolved-path containment check below
        # guard independently rather than trusting routing alone.
        if "/" in filename or "\\" in filename or filename in (".", ".."):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

        images_dir = (project.folder / "images").resolve()
        candidate = (images_dir / filename).resolve()
        if candidate.parent != images_dir or candidate.suffix.lower() not in IMAGE_EXTENSIONS:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
        if not candidate.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

        media_type = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".bmp": "image/bmp",
        }[candidate.suffix.lower()]
        return FileResponse(candidate, media_type=media_type)

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

    # The settings/channels/keys surface (Task 3) - a separate router kept
    # thin on purpose (validate, delegate to avg.config.*, return; see its
    # own module docstring) rather than folded into this already-large
    # function. Included before the static mount below for the same reason
    # every /api/... route above is defined before it: registration order
    # is match order, and the catch-all mount must never get first crack at
    # an /api/ path.
    app.include_router(settings_router)

    # The takes surface (Slice 5, Task 3) - same reasoning as settings_router
    # above: a separate router kept thin, included before the static mount.
    app.include_router(takes_router)

    # Serves the built UI. Mounted last and at "/" deliberately: FastAPI
    # matches routes in registration order, so the exact-path routes above
    # (/api/..., /ws/events) are always tried first and this catch-all mount
    # never shadows them - it only ever answers requests nothing above it
    # claimed. `html=True` makes "/" resolve to index.html. Every test in
    # `tests/api/test_app.py` builds `create_app` against a throwaway
    # `tmp_path` with no built UI anywhere near it, and the real repo has no
    # `web/dist` until someone runs `npm run build` (it is gitignored) - so
    # this has to tolerate the directory not existing rather than raising,
    # or every one of those tests (and a fresh clone before its first
    # frontend build) would fail at app construction.
    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")

    return app

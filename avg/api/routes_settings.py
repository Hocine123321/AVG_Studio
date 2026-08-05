"""The settings/channels/keys surface (Slice 2, Task 3).

Renders itself from `avg.core.settings_schema.SETTINGS` - adding a setting
is one entry there, never a route change here. Routes stay thin: validate
against the schema, delegate to `avg.config.settings` / `avg.config.channels`
/ `avg.config.secrets`, return. No merge layer, no diffing engine, no undo
stack - those modules already refuse to have one (see their docstrings)
and this router does not reintroduce one at the HTTP layer.

    GET    /api/errors                   -> recent failures, newest first
    POST   /api/errors                   -> record one (the frontend's own failures)
    DELETE /api/errors                   -> clear the log
    GET    /api/costs                    -> the maintained cost table
    GET    /api/health                   -> component states (?refresh=true to recheck)
    GET    /api/settings/schema           -> the catalogue
    GET    /api/settings/values           -> app values + the active channel's values
    POST   /api/settings/values           -> save; app-scope -> settings.json,
                                              channel-scope -> the active channel
    GET    /api/channels                  -> {"channels": [...], "active_channel_id": ...}
    POST   /api/channels                  -> create {"label": ..., "note": ...}
    PATCH  /api/channels/{id}             -> rename / edit note {"label"?: ..., "note"?: ...}
    DELETE /api/channels/{id}             -> delete (no-op if absent)
    POST   /api/channels/{id}/activate    -> set the active channel
    GET    /api/keys                      -> {provider: bool} for every known provider
    POST   /api/keys/{provider}           -> set a key {"key": ...}
    DELETE /api/keys/{provider}           -> clear a key

The one rule every route here is built around: **a key value must never
leave the process.** `/api/keys` and every `type="secret"` entry surfaced
through `/api/settings/values` report only whether a provider has a key,
never what it is - see `_KeysProvider` view. `POST /api/settings/values`
refuses outright to accept a value for a `type="secret"` id (via
`avg.core.settings_schema.validate_value`), so a key can never be smuggled
in through the generic values route either.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from avg.config import channels as channels_config
from avg.config import secrets as secrets_config
from avg.config.settings import load_settings, save_settings
from avg.core import costs, health
from avg.core.settings_schema import (
    KEY_PROVIDER_IDS,
    SETTINGS,
    SettingValidationError,
    get_spec,
    validate_value,
)
from avg.core.model_prefs import KINDS, ModelPrefs
from avg.providers import catalogues, probe

from .deps import require_token

router = APIRouter(dependencies=[Depends(require_token)])


class ChannelCreate(BaseModel):
    label: str
    note: str = ""


class ChannelUpdate(BaseModel):
    """Both fields optional and independently omittable - a rename that
    only wants to touch the label must not require re-sending the note."""

    label: str | None = None
    note: str | None = None


class SettingsValuesUpdate(BaseModel):
    values: dict[str, Any]


class KeySet(BaseModel):
    key: str


def _app_values(app_state: Any) -> dict[str, Any]:
    """Current value for every `scope="app"` setting.

    `type="secret"` entries report a boolean (whether a key is set) via
    `avg.config.secrets.has_key` - never the stored value, and never even
    consulting `SettingsDoc.values` for them (a secret id is never written
    there in the first place; see `validate_value`).
    """
    doc = load_settings(app_state.settings_path)
    values: dict[str, Any] = {}
    for spec in SETTINGS:
        if spec.scope != "app":
            continue
        if spec.type == "secret":
            values[spec.id] = secrets_config.has_key(spec.id, app_state.secrets_path)
        else:
            values[spec.id] = doc.values.get(spec.id, spec.default)
    return values


def _channel_values(app_state: Any) -> dict[str, Any]:
    """Current value for every `scope="channel"` setting, read from the
    active channel's document - or each setting's default if no channel is
    active, or the active id no longer resolves to a real channel."""
    active_id = channels_config.active_channel_id(app_state.settings_path)
    channel = channels_config.get_channel(active_id, app_state.channels_dir) if active_id else None
    source = channel.settings if channel is not None else {}
    return {
        spec.id: source.get(spec.id, spec.default)
        for spec in SETTINGS
        if spec.scope == "channel"
    }


def _values_payload(app_state: Any) -> dict[str, Any]:
    return {
        "app": _app_values(app_state),
        "channel": _channel_values(app_state),
        "active_channel_id": channels_config.active_channel_id(app_state.settings_path),
    }


@router.get("/api/settings/schema")
async def get_settings_schema() -> dict:
    return {"settings": [s.model_dump() for s in SETTINGS]}


@router.get("/api/phases")
async def get_phases() -> dict[str, Any]:
    """The pipeline's phases, in order, with what each one is for.

    Serves `avg.pipeline.registry.REGISTRY` - the single source of truth for
    phase order and identity. `web/src/phases.ts` has carried a hand-kept
    copy of the keys since Slice 1, with its own docstring calling itself
    duplication "until the backend serves phase definitions"; this is that
    route. It does not retire that file, which also owns the nested
    sub-step display REGISTRY does not model.
    """
    from avg.pipeline.registry import REGISTRY  # noqa: PLC0415 - keeps the pipeline off the import path

    return {
        "phases": [
            {"id": p.id, "key": p.key, "name": p.name, "description": p.description}
            for p in REGISTRY
        ]
    }


@router.get("/api/health")
async def get_health(refresh: bool = False) -> dict:
    """Component states for the settings screen's Components table.

    `refresh=true` is the Recheck button and skips the cache, paying for the
    `ffmpeg -version` subprocess again. Never raises for an unhealthy
    component: a failure is an ordinary row with `ok: false` and a reason,
    which is what lets this screen report a broken FFmpeg instead of 500ing
    about it.

    `can_install` is false in a frozen build. A required dependency missing
    THERE is a packaging bug in the build, not something a user should paper
    over at runtime - so the row explains instead of offering an action. See
    `POST /api/health/install`.
    """
    components = health.check_all(refresh=refresh)
    return {
        "components": [
            {
                "name": c.name,
                "ok": c.ok,
                "version": c.version,
                "detail": c.detail,
                "required": c.required,
                "package": c.package,
            }
            for c in components
        ],
        "ok": all(c.ok for c in components),
        "can_install": not getattr(sys, "frozen", False),
    }


class InstallRequest(BaseModel):
    """`POST /api/health/install` - one package, by distribution name."""

    package: str


@router.post("/api/health/install")
async def install_package(body: InstallRequest, request: Request) -> dict:
    """Install one missing dependency with pip, in a dev checkout only.

    Two refusals, both deliberate:

    - **Not a package this app knows.** The request body names something
      that gets shelled, so it is validated against
      `health.installable_packages()` - derived from `_PACKAGES` - before
      any argv exists. There is no path from a request to an arbitrary
      install, and the name is passed as one argv element, never through a
      shell.
    - **A frozen build.** A required dependency missing from a packaged app
      is a packaging bug in the build. That is the `pydub` lesson from Slice
      9, where the real fix was to bundle it and correct this module's claim
      that it was optional - and it is why no private package directory is
      ever put on `sys.path`. An app running a mix of bundled and downloaded
      code is the hardest kind of "works on my machine" to debug, and it
      needs the network at exactly the moment something is already wrong.

    Runs on a worker thread (pip is tens of seconds) and returns pip's own
    combined output, so the user reads what pip said rather than a verdict
    this route invented.
    """
    if getattr(sys, "frozen", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "this is a packaged build - a missing dependency here is a bug in the "
                "build, not something to install at runtime. Reinstall AVG Studio."
            ),
        )

    package = body.package.strip()
    if package not in health.installable_packages():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{package!r} is not one of this app's own dependencies",
        )

    def _run() -> tuple[int, str]:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", package],
            capture_output=True,
            text=True,
            timeout=600,
            # No console window flashes on Windows when the app is frozen
            # and windowed; harmless everywhere else.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode, f"{result.stdout or ''}{result.stderr or ''}"

    try:
        code, output = await asyncio.to_thread(_run)
    except Exception as exc:  # noqa: BLE001 - the reason is the answer
        return {"ok": False, "output": f"could not run pip: {exc}"}

    if code == 0:
        # The five-minute component cache would otherwise keep reporting the
        # package as missing for the rest of it.
        health.clear_cache()
    return {"ok": code == 0, "output": output}


class ErrorReport(BaseModel):
    """`POST /api/errors` - how the frontend reports what only it can see.

    A thumbnail that would not load, a WebSocket that dropped: failures the
    backend never hears about, and exactly the ones a user cannot describe
    accurately afterwards.
    """

    severity: Literal["critical", "needs_review", "low"]
    source: str
    summary: str
    detail: str = ""
    context: dict[str, str] = Field(default_factory=dict)


@router.get("/api/errors")
async def list_errors(request: Request) -> dict:
    """Recent failures, newest first. Never raises for a broken store - an
    error log that can itself error is worse than none."""
    return {
        "errors": [
            {
                "id": r.id,
                "when": r.when.isoformat(),
                "severity": r.severity,
                "source": r.source,
                "summary": r.summary,
                "detail": r.detail,
                "context": r.context,
            }
            for r in request.app.state.error_store.all()
        ]
    }


@router.post("/api/errors", status_code=status.HTTP_201_CREATED)
async def report_error(body: ErrorReport, request: Request) -> dict:
    record = request.app.state.error_store.record(
        severity=body.severity,
        source=body.source,
        summary=body.summary,
        detail=body.detail,
        context=body.context,
    )
    return {"recorded": record.id}


@router.delete("/api/errors")
async def clear_errors(request: Request) -> dict:
    request.app.state.error_store.clear()
    return {"cleared": True}


#: Providers that cannot generate without a key, by `kind/provider`. Edge
#: needs none (it is the free voice provider) and Pollinations needs none,
#: so neither appears. Stable Horde's CATALOGUE is public while its
#: generation endpoint is not - which is exactly the trap this exists to
#: close: it reads "Reachable" and then 403s on the first real image.
_NEEDS_KEY: tuple[str, ...] = (
    "image/stablehorde",
    "image/openai",
    "image/gemini",
    "image/huggingface",
    "voice/elevenlabs",
)


@router.get("/api/models/cache")
async def get_model_cache(request: Request) -> dict:
    """What the last provider test proved, in the three states it can prove.

    - `providers` - models a real completion succeeded on, per provider.
      Proven only; a provider probed with nothing working appears with an
      empty list, which is a different statement from being absent.
    - `text` - the full per-model verdicts including the failures, so the
      UI can say `failed` rather than fall back to `untested`.
    - `reachable` - per `kind/provider`, whether the provider answered its
      own catalogue. THIS MODEL is unproven either way; reachability is a
      fact about a provider.

    An entry absent from `text` or `reachable` is UNTESTED. `updated: null`
    means no test has ever run - the UI says so and never invents a list.
    """
    cache = request.app.state.model_cache
    updated = cache.updated_at()
    return {
        "updated": updated.isoformat() if updated is not None else None,
        "providers": {p: cache.working(p) for p in cache.providers()},
        "text": {
            p: cache.verdicts(p) for p in cache.providers() if not p.startswith("__")
        },
        "reachable": cache.reach(),
        "needs_key": {
            entry: True
            for entry in _NEEDS_KEY
            if not secrets_config.get_key(entry.split("/", 1)[1])
        },
        "building": _probe_running(request.app.state),
    }


def _by_provider(entry_ids: list[str]) -> dict[str, list[str]]:
    """`["groq/a", "groq/b"]` -> `{"groq": ["a", "b"]}`. Ids without a
    provider prefix are dropped rather than guessed at."""
    out: dict[str, list[str]] = {}
    for entry_id in entry_ids:
        provider, _, model = entry_id.partition("/")
        if not provider or not model:
            continue
        out.setdefault(provider, []).append(model)
    return {provider: sorted(models) for provider, models in out.items()}


def _probe_running(app_state: Any) -> bool:
    thread = getattr(app_state, "probe_thread", None)
    return thread is not None and thread.is_alive()


@router.post("/api/models/cache/build", status_code=status.HTTP_202_ACCEPTED)
async def build_model_cache(request: Request) -> dict:
    """Test Providers: prove the ticked text models, reach the rest.

    Three verdicts, and only one of them costs anything:

      | Kind  | What runs                        | Verdict                   |
      |-------|-----------------------------------|---------------------------|
      | Text  | A real minimal completion per    | `working` / `failed`      |
      |       | TICKED model                     |                            |
      | Image | The provider's catalogue fetch   | `reachable`/`unreachable` |
      | Voice | The provider's voice list        | `reachable`/`unreachable` |

    **Nothing that costs money is probed.** An image probe is a paid image
    and an ElevenLabs probe is paid characters, which is why Slice 8 made
    images and voices listed rather than probed; reachability is a
    catalogue GET, which both serve for free.

    Only ticked text models, and only providers with at least one ticked
    entry: a sweep over everything a provider advertises is precisely what
    the Live List exists to replace, and a provider nobody shortlisted is
    not worth a call.

    Runs on a background thread with a single slot and a 409 on a second
    start, matching how renders are guarded. Deliberately NOT sharing the
    render slot - testing providers touches no project and no media engine.
    """
    app_state = request.app.state
    if _probe_running(app_state):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a provider test is already running",
        )

    cache = app_state.model_cache
    prefs, _scope = _prefs_for_scope(app_state)

    def _sweep() -> None:
        # Never raises out of the thread: a failed sweep leaves the
        # previous verdicts in place, which is more useful than none.
        try:
            results = probe.probe_models(_by_provider(prefs.selected_ids("text")))

            reachable: dict[str, bool] = {}
            for kind in ("image", "voice"):
                for provider in sorted(_by_provider(prefs.selected_ids(kind))):
                    verdict = probe.check_reachable(kind, provider)
                    # `None` is "there was nothing to ask" - an unkeyed
                    # ElevenLabs, or a provider that publishes no catalogue
                    # at all. Storing it would turn untested into a verdict.
                    if verdict is not None:
                        reachable[f"{kind}/{provider}"] = verdict

            # The `__`-namespaced catalogue entries are what the Live List
            # lists. `save` replaces the whole document, so they are carried
            # forward explicitly - dropping them would empty the very screen
            # the ticks came from.
            merged: dict[str, dict[str, bool]] = {
                provider: {model: True for model in cache.working(provider)}
                for provider in cache.providers()
                if provider.startswith("__")
            }
            merged.update(results)
            cache.save(merged, reachable=reachable)
        except Exception:
            pass

    thread = threading.Thread(target=_sweep, name="provider-test", daemon=True)
    app_state.probe_thread = thread
    thread.start()
    return {"started": True}


# ── Live List ──────────────────────────────────────────────

class LiveEntryUpdate(BaseModel):
    """`PATCH /api/live-list/{kind}/{entry_id}` - one field at a time.

    Every field is optional and only the given ones change: the screen
    sends a tick, a star, or a note losing focus, and a whole-entry
    replace would make ticking a box erase the note beside it.
    """

    selected: bool | None = None
    favourite: bool | None = None
    note: str | None = None


class LiveEntryCreate(BaseModel):
    """`POST /api/live-list/{kind}` - an entry the user typed themselves.

    For providers that publish no catalogue (OpenAI and Gemini images).
    Marked `manual` wherever it is shown, so it can never be mistaken for
    something the app verified.
    """

    entry_id: str


def _prefs_for_scope(app_state: Any) -> tuple[ModelPrefs, str]:
    """The Live List document this request should read, and which scope it is.

    A channel set to `live_list_source="channel"` keeps its own shortlist,
    in its own document, and does NOT fall back to the global one - the
    same no-merge-layer rule every channel-scoped setting follows. Three
    legacy config bugs came from the copy-and-merge mechanism that rule
    replaces.
    """
    values = _channel_values(app_state)
    if values.get("live_list_source") != "channel":
        return app_state.model_prefs, "global"

    channel_id = channels_config.active_channel_id(app_state.settings_path)
    if channel_id is None:
        return app_state.model_prefs, "global"
    # Beside the global document, named for the channel - so it is obvious
    # on disk which shortlist belongs to which channel.
    base = Path(app_state.model_prefs.path).parent
    return ModelPrefs(base / "live-lists" / f"{channel_id}.json"), "channel"


def _entry_rows(prefs: ModelPrefs, kind: str, known: dict[str, str]) -> list[dict]:
    """Merge what the app knows about with what the user has said about it.

    `known` maps entry id -> source (`probed` / `catalogue`). Anything in
    the prefs document but not in `known` is reported as `manual` rather
    than silently dropped: a model can vanish from a catalogue between
    refreshes, and losing the note attached to it would be worse than
    showing an entry that no longer resolves.
    """
    saved = prefs.all(kind)
    rows: list[dict] = []
    for entry_id in sorted(set(known) | set(saved)):
        entry = saved.get(entry_id)
        rows.append(
            {
                "id": entry_id,
                "source": known.get(entry_id, "manual"),
                "selected": bool(entry.selected) if entry else False,
                "favourite": bool(entry.favourite) if entry else False,
                "note": entry.note if entry else "",
            }
        )
    rows.sort(key=lambda r: (not r["favourite"], r["id"].lower()))
    return rows


def _known_text(app_state: Any) -> dict[str, str]:
    """Text models the pickers could offer, as `provider/model`.

    Two sources, and the probed one wins: a model the catalogue merely
    lists is `catalogue` ("Listed"), and a model a real completion
    succeeded on is `probed` ("Verified"). Listing has to be here at all
    because Test Providers only proves the models the user TICKED - and on
    a fresh install nothing has been ticked yet, so without the advertised
    list the text tab would have nothing to tick.
    """
    cache = app_state.model_cache
    known = _known_from_catalogue(app_state, "text")
    known.update(
        {
            f"{provider}/{model}": "probed"
            for provider in cache.providers()
            if not provider.startswith("__")
            for model in cache.working(provider)
        }
    )
    return known


def _known_from_catalogue(app_state: Any, kind: str) -> dict[str, str]:
    """Image models / voices as last listed.

    Stored in the same cache document the text probe writes, under a
    namespaced provider key, so there is one cache file rather than three.
    `refresh` is what fills it - a plain GET never touches the network.
    """
    cache = app_state.model_cache
    prefix = f"__{kind}__"
    return {
        f"{provider.removeprefix(prefix)}/{model}": "catalogue"
        for provider in cache.providers()
        if provider.startswith(prefix)
        for model in cache.working(provider)
    }


@router.get("/api/live-list")
async def get_live_list(request: Request) -> dict:
    """Everything the pickers could offer, plus what the user said about it.

    Never touches the network: text, image and voice entries all come from
    whatever the last catalogue refresh and the last provider test wrote to
    the cache. A GET that went listing would make opening a screen cost
    provider calls.
    """
    app_state = request.app.state
    prefs, scope = _prefs_for_scope(app_state)
    return {
        "scope": scope,
        "text": _entry_rows(prefs, "text", _known_text(app_state)),
        "image": _entry_rows(prefs, "image", _known_from_catalogue(app_state, "image")),
        "voice": _entry_rows(prefs, "voice", _known_from_catalogue(app_state, "voice")),
        "voice_meta": app_state.model_cache.meta(),
        "updated": (
            app_state.model_cache.updated_at().isoformat()
            if app_state.model_cache.updated_at() is not None
            else None
        ),
    }


@router.post("/api/live-list/refresh")
async def refresh_live_list(request: Request) -> dict:
    """Re-list text, image and voice catalogues. Listing only - nothing is
    generated and nothing is proved.

    Probing an image model means paying for an image and probing an
    ElevenLabs voice costs characters, so the Live List curates what a
    provider advertises rather than proving it. Text models are listed here
    too - free, same as the other two - because that is what puts a model
    in front of the user to tick in the first place. Test Providers, on its
    own button, is what actually proves the ticked ones: there the verdict
    is a real completion, cheap enough to run only on what was chosen.
    """
    app_state = request.app.state
    cache = app_state.model_cache

    def _list_everything() -> tuple[dict[str, dict[str, bool]], dict[str, dict[str, str]]]:
        """All the network work, off the event loop.

        Two reasons this is a thread rather than inline. It is seconds of
        HTTP against several providers, and blocking the loop would stall
        every other request including the one carrying render progress.
        And Edge's voice list is an async API that `EdgeVoiceProvider`
        drives with `asyncio.run`, which RAISES inside a running loop - so
        called inline it returned nothing at all, and the voice tab came
        back with only ElevenLabs in it.
        """
        # The text probe's own results are preserved: `save` replaces the
        # whole document, and dropping them here would silently un-verify
        # every text model every time someone refreshed the voice list.
        results: dict[str, dict[str, bool]] = {
            provider: {model: True for model in cache.working(provider)}
            for provider in cache.providers()
            if not provider.startswith("__")
        }

        # Text models are LISTED here and PROVED by Test Providers. Listing
        # is a plain catalogue GET on every text provider - it costs
        # nothing, exactly like the image and voice lists below - and it is
        # what puts a model in front of the user to tick in the first place.
        for provider in probe.known_providers():
            try:
                models = probe.list_models(provider, None)
            except Exception:
                models = []
            if models:
                results[f"__text__{provider}"] = {model: True for model in models}

        for provider in catalogues.image_providers():
            models = catalogues.list_image_models(provider)
            results[f"__image__{provider}"] = {model: True for model in models}

        meta: dict[str, dict[str, str]] = dict(cache.meta())
        for provider in catalogues.voice_providers():
            voices = catalogues.list_voices(provider)
            results[f"__voice__{provider}"] = {voice["voice_id"]: True for voice in voices}
            for voice in voices:
                meta[f"{provider}/{voice['voice_id']}"] = {
                    "name": voice["name"],
                    "locale": voice["locale"],
                    "gender": voice["gender"],
                }
        return results, meta

    results, voice_meta = await asyncio.to_thread(_list_everything)
    cache.save(results, voice_meta)
    return {"refreshed": True}


@router.patch("/api/live-list/{kind}/{entry_id:path}")
async def patch_live_entry(kind: str, entry_id: str, body: LiveEntryUpdate, request: Request) -> dict:
    if kind not in KINDS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown kind {kind!r}")
    prefs, _scope = _prefs_for_scope(request.app.state)
    entry = prefs.set(kind, entry_id, selected=body.selected, favourite=body.favourite, note=body.note)
    return {
        "id": entry_id,
        "selected": entry.selected,
        "favourite": entry.favourite,
        "note": entry.note,
    }


@router.post("/api/live-list/{kind}", status_code=status.HTTP_201_CREATED)
async def add_live_entry(kind: str, body: LiveEntryCreate, request: Request) -> dict:
    if kind not in KINDS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown kind {kind!r}")
    entry_id = body.entry_id.strip()
    if "/" not in entry_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="an entry id is 'provider/model' - e.g. openai/gpt-image-1",
        )
    prefs, _scope = _prefs_for_scope(request.app.state)
    prefs.set(kind, entry_id, selected=True)
    return {"added": entry_id}


@router.delete("/api/live-list/{kind}/{entry_id:path}")
async def delete_live_entry(kind: str, entry_id: str, request: Request) -> dict:
    """Remove a MANUAL entry.

    A probed or catalogued entry is not the user's to delete - it is what
    the provider says exists, and it would reappear on the next refresh
    anyway. Untick it instead; that is what the tick is for.
    """
    if kind not in KINDS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown kind {kind!r}")
    app_state = request.app.state
    known = _known_text(app_state) if kind == "text" else _known_from_catalogue(app_state, kind)
    if entry_id in known:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="this entry comes from the provider's own list - untick it instead of removing it",
        )
    prefs, _scope = _prefs_for_scope(app_state)
    prefs.remove(kind, entry_id)
    return {"removed": entry_id}


@router.get("/api/health/report")
async def get_health_report(request: Request, refresh: bool = False) -> dict:
    """The three-stage startup report behind the App Health screen.

    Dependency check -> App warmup -> Safeguards, in that order, because
    each stage assumes the one before it passed and "which stage failed" is
    the whole diagnosis when a launch goes wrong.

    Paths come from `app.state`, never resolved inside `avg.core.health`, so
    a test's isolated tmp_path is what gets checked rather than the real
    `%APPDATA%`.
    """
    app_state = request.app.state
    stages = health.report(
        refresh=refresh,
        settings_path=app_state.settings_path,
        channels_dir=app_state.channels_dir,
        output_root=app_state.output_root,
    )
    return {
        "ok": all(stage.ok for stage in stages),
        "stages": [
            {
                "key": stage.key,
                "name": stage.name,
                "detail": stage.detail,
                "ok": stage.ok,
                "checks": [
                    {
                        "name": c.name,
                        "ok": c.ok,
                        "version": c.version,
                        "detail": c.detail,
                        "required": c.required,
                    }
                    for c in stage.checks
                ],
            }
            for stage in stages
        ],
    }


@router.get("/api/costs")
async def get_costs() -> dict:
    """The maintained cost table, served rather than restated in the UI.

    `web/src/phases.ts` is already a hand-kept copy of the phase order that
    `avg.pipeline.registry` exists to be the only copy of; a second
    hardcoded cost table in TypeScript would be the same mistake with money
    attached. The frontend sums these against the phase statuses it already
    has. See `avg.core.costs` - every figure is a maintained estimate, not
    metered spend, and the UI must say so.
    """
    return {
        "currency": costs.CURRENCY,
        "leaf_cost": costs.LEAF_COST,
        "phase_leaves": {key: list(leaves) for key, leaves in costs.PHASE_LEAVES.items()},
        "length_multiplier": costs.LENGTH_MULTIPLIER,
        "base_cost": costs.base_cost(),
    }


@router.get("/api/settings/values")
async def get_settings_values(request: Request) -> dict:
    return _values_payload(request.app.state)


@router.post("/api/settings/values")
async def post_settings_values(body: SettingsValuesUpdate, request: Request) -> dict:
    app_state = request.app.state

    app_updates: dict[str, Any] = {}
    channel_updates: dict[str, Any] = {}
    for setting_id, raw_value in body.values.items():
        spec = get_spec(setting_id)
        if spec is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown setting: {setting_id!r}"
            )
        try:
            value = validate_value(spec, raw_value)
        except SettingValidationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        if spec.scope == "app":
            app_updates[setting_id] = value
        else:
            channel_updates[setting_id] = value

    # Validate the whole batch, including "is a channel actually active",
    # before writing anything - a request that fails partway must not leave
    # some settings saved and others not (see the scope rule's "fail
    # cleanly, not write somewhere arbitrary" requirement).
    active_id = channels_config.active_channel_id(app_state.settings_path)
    if channel_updates and active_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no active channel — activate a channel before saving channel-scoped settings",
        )

    channel = channels_config.get_channel(active_id, app_state.channels_dir) if active_id else None
    if channel_updates and channel is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"active channel {active_id!r} no longer exists",
        )

    if channel_updates:
        assert channel is not None  # guarded above
        channel.settings.update(channel_updates)
        channels_config.save_channel(channel, app_state.channels_dir)

    if app_updates:
        doc = load_settings(app_state.settings_path)
        doc.values.update(app_updates)
        save_settings(doc, app_state.settings_path)

    return _values_payload(app_state)


@router.get("/api/channels")
async def list_channels_route(request: Request) -> dict:
    app_state = request.app.state
    channels = channels_config.list_channels(app_state.channels_dir)
    return {
        "channels": [c.model_dump() for c in channels],
        "active_channel_id": channels_config.active_channel_id(app_state.settings_path),
    }


@router.post("/api/channels", status_code=status.HTTP_201_CREATED)
async def create_channel_route(body: ChannelCreate, request: Request) -> dict:
    app_state = request.app.state
    channel = channels_config.create_channel(body.label, note=body.note, channels_dir=app_state.channels_dir)
    return channel.model_dump()


@router.patch("/api/channels/{channel_id}")
async def update_channel_route(channel_id: str, body: ChannelUpdate, request: Request) -> dict:
    app_state = request.app.state
    channel = channels_config.update_channel(
        channel_id, label=body.label, note=body.note, channels_dir=app_state.channels_dir
    )
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown channel")
    return channel.model_dump()


@router.delete("/api/channels/{channel_id}")
async def delete_channel_route(channel_id: str, request: Request) -> dict:
    app_state = request.app.state
    channels_config.delete_channel(
        channel_id, channels_dir=app_state.channels_dir, settings_path=app_state.settings_path
    )
    return {"deleted": True}


@router.post("/api/channels/{channel_id}/activate")
async def activate_channel_route(channel_id: str, request: Request) -> dict:
    app_state = request.app.state
    channel = channels_config.get_channel(channel_id, app_state.channels_dir)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown channel")
    channels_config.set_active(channel_id, app_state.settings_path)
    return {"active_channel_id": channel_id}


@router.get("/api/keys")
async def list_keys_route(request: Request) -> dict[str, bool]:
    """`{provider: bool}` for every known provider - whether a key is set,
    never the key itself. Covers every `type="secret"` schema id plus any
    provider that happens to have a stored entry but isn't in the
    catalogue yet, so this never silently hides a key someone set outside
    the schema's current provider list."""
    app_state = request.app.state
    providers = sorted(set(KEY_PROVIDER_IDS) | set(secrets_config.list_providers(app_state.secrets_path)))
    return {p: secrets_config.has_key(p, app_state.secrets_path) for p in providers}


@router.post("/api/keys/{provider}")
async def set_key_route(provider: str, body: KeySet, request: Request) -> dict[str, bool]:
    app_state = request.app.state
    if not body.key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="key must not be empty")
    try:
        secrets_config.set_key(provider, body.key, app_state.secrets_path)
    except RuntimeError as exc:
        # DPAPI itself refused to encrypt - avg.config.secrets.set_key's
        # own docstring says this must not be swallowed as "saved". The
        # exception message (see that module) never contains the key.
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return {provider: True}


@router.delete("/api/keys/{provider}")
async def delete_key_route(provider: str, request: Request) -> dict[str, bool]:
    app_state = request.app.state
    secrets_config.delete_key(provider, app_state.secrets_path)
    return {provider: False}

"""The takes surface (Slice 5, Task 3): list/preview/create/delete/select/rename.

A project used to be one video; it now owns zero or more `avg.core.models.Take`s
(Task 2), and `avg.core.invalidation` (Task 1) knows which phases a settings
change invalidates. This router is where those two meet the HTTP layer and
the background-job machinery already living in `avg.api.app`:

    GET    /api/projects/{id}/takes            -> {"takes": [...], "current_take_id": N}
    POST   /api/projects/{id}/takes/preview     -> {"rerun": [...], "keeps": [...],
                                                      "estimate_seconds": N|None, "unknown": [...]}
                                                     computes only - creates nothing, starts nothing
    POST   /api/projects/{id}/takes             -> 201 Take - clones and starts the pipeline
    DELETE /api/projects/{id}/takes/{tid}
    POST   /api/projects/{id}/takes/{tid}/select
    PATCH  /api/projects/{id}/takes/{tid}       -> {"label": ...}, rename only

Routes stay thin on purpose (see `avg.api.routes_settings`'s own docstring
for the same rule): validate, delegate to `avg.core.takes` / `avg.core.
invalidation`, return. Phase *order* is never restated here beyond a plain
`REGISTRY` id comparison - `avg.pipeline.registry.REGISTRY` remains the only
place that order is declared. The one background-job mechanism this app has
(one job at a time, a live `render_thread` on `app.state`, `/ws/events`
broadcast, 409 naming the in-flight project) is reused verbatim from
`avg.api.app.create_project` - this module does not invent a second one.

**Overrides never reach a channel document.** Exactly like
`avg.api.app.ProjectCreate`, a request's `overrides` are validated against
the settings catalogue (`scope="channel"` ids only) and folded onto a
throwaway `Channel.model_copy` - `channels_config.save_channel` is never
called from anywhere in this module.

**The re-run targets the new take's own folder.** Every real phase resolves
its paths from `ctx.project.folder` alone (see `avg.pipeline.phases._shared`
and each phase module) - there is no second "which folder" concept for a
phase to know about. So redirecting a re-run into `takes/<id>/` is exactly
one field: the `ProjectDoc` handed to `avg.pipeline.runner.run` is a
`model_copy` of the real project with `folder` swapped for
`avg.core.takes.take_folder(project, new_take.id)` and `phases` swapped for
the new take's own `phases` dict - passed by reference (`model_copy` does
not deep-copy an `update` value), so the runner's in-place `PhaseRecord`
mutations land directly on `Take.phases`, not a throwaway copy of it.
`master_output` is synced back onto the take by hand in the worker's
`finally`, since `RenderPhase.run` only ever writes `ctx.project.
master_output`, not any take object.

**`current_take_id` only advances on success.** The worker below tracks
whether any `PhaseFailed`/`PipelineCancelled` event was seen; only a clean
run (no such event, `to_run` exhausted) calls `_sync_current_take`, mirroring
`ProjectDoc`'s own docstring ("only ever advances to a take once IT finishes
successfully").
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from avg.config import channels as channels_config
from avg.core.events import PhaseFailed, PipelineCancelled
from avg.core.invalidation import first_invalid_phase
from avg.core.models import ProjectDoc, Take
from avg.core.settings_schema import SettingValidationError, get_spec, validate_value
from avg.core.takes import clone_take, delete_take as _delete_take, estimate_seconds, migrate_to_takes, take_folder
from avg.pipeline.phase import CancellationToken
from avg.pipeline.registry import BY_KEY, REGISTRY
from avg.pipeline.runner import run as run_pipeline

from .deps import require_token

router = APIRouter(dependencies=[Depends(require_token)])


class TakeRequest(BaseModel):
    """Body shared by `preview` and the real create route."""

    overrides: dict[str, Any] = Field(default_factory=dict)
    reroll_from: str | None = None


class TakeRename(BaseModel):
    label: str


# --------------------------------------------------------------------------
# Shared helpers - project/take lookup, override validation, and the
# rerun/keep/label plan every one of "preview" and the real create route
# needs identically.
# --------------------------------------------------------------------------


def _get_project(app_state: Any, project_id: str) -> ProjectDoc:
    project = app_state.projects.get(project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown project")
    return project


def _get_take(project: ProjectDoc, take_id: int) -> Take:
    take = next((t for t in project.takes if t.id == take_id), None)
    if take is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown take")
    return take


def _next_take_id(project: ProjectDoc) -> int:
    """The id `clone_take` is about to assign - mirrors `avg.core.takes.
    _next_take_id` (private there) so the label can name the take before
    `clone_take` itself returns it. Reading the same "one past the highest
    existing id" rule twice (here and inside `clone_take`) is safe: nothing
    runs between this call and `clone_take` in `create_take` below that
    could create another take (this route holds the one-job-at-a-time slot
    for the whole request), so the id this computes is exactly the one
    `clone_take` will assign.
    """
    return max((t.id for t in project.takes), default=0) + 1


def _validate_override_ids(overrides: dict[str, Any]) -> None:
    """Reject any override id that isn't a `scope="channel"` catalogue entry.

    Deliberately id-only (no `validate_value` type coercion) - `preview`
    never needs a coerced value, only whether the id is legitimate, and the
    real create route below re-validates with `validate_value` anyway when
    it actually builds the effective settings dict.
    """
    for setting_id in overrides:
        spec = get_spec(setting_id)
        if spec is None or spec.scope != "channel":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{setting_id!r} is not a per-video creative setting",
            )


@dataclass
class _Plan:
    start_id: int
    override_phase_id: int | None
    reroll_phase_id: int | None
    rerun: list[str]
    keeps: list[str]

    @property
    def reroll_is_dominant(self) -> bool:
        """Whether `reroll_from` (not the overrides) is what actually
        determined the start phase - ties go to the reroll, matching
        `Take.rerolled_from`'s "what actually happened" contract (see this
        module's docstring)."""
        return self.reroll_phase_id is not None and (
            self.override_phase_id is None or self.reroll_phase_id <= self.override_phase_id
        )


def _build_plan(changed_ids: list[str], reroll_from: str | None) -> _Plan:
    """Everything both `preview` and the real create route need, in one
    place: the earlier-of-the-two start phase (rule: "reroll_from and
    overrides may be combined; the start phase is the earlier of the two"),
    and the resulting rerun/keep phase-key lists, entirely from `REGISTRY`
    order - no phase list is restated anywhere else in this module.

    `changed_ids` must already be validated (`_validate_override_ids`) by
    the time this is called - every id here is assumed to be a real
    `scope="channel"` catalogue id, which per Task 1's own map always
    invalidates something, so an empty `changed_ids` is the only way
    `override_phase_id` comes back `None`.
    """
    if not changed_ids and reroll_from is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="request must include overrides or reroll_from",
        )

    reroll_phase_id: int | None = None
    if reroll_from is not None:
        phase = BY_KEY.get(reroll_from)
        if phase is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown phase key: {reroll_from!r}",
            )
        reroll_phase_id = phase.id

    override_phase_id = first_invalid_phase(changed_ids)
    candidates = [p for p in (override_phase_id, reroll_phase_id) if p is not None]
    # Non-empty by construction: the guard above already refused a request
    # with no overrides AND no reroll_from, and every validated override id
    # invalidates some phase (see docstring) - so at least one candidate
    # always exists here.
    start_id = min(candidates)

    rerun = [phase.key for phase in REGISTRY if phase.id >= start_id]
    keeps = [phase.key for phase in REGISTRY if phase.id < start_id]
    return _Plan(
        start_id=start_id,
        override_phase_id=override_phase_id,
        reroll_phase_id=reroll_phase_id,
        rerun=rerun,
        keeps=keeps,
    )


def _phase_id_of_setting(setting_id: str) -> int:
    spec = get_spec(setting_id)
    assert spec is not None and spec.invalidates is not None  # validated by _validate_override_ids
    return BY_KEY[spec.invalidates].id


def _auto_label(new_id: int, changed_ids: list[str], reroll_from: str | None, plan: _Plan) -> str:
    """A take names itself from what produced it (spec section 5).

    A dominant reroll (see `_Plan.reroll_is_dominant`) reads `"Take N ·
    re-roll <phase name, lowercased>"`, using `REGISTRY`'s own phase `name`
    - never the phase's key. Otherwise, the earliest-invalidating changed
    setting's catalogue `label` (lowercased) names the take, with `"+K
    more"` appended when more than one setting changed. This function is
    only ever consulted at take-creation time (`clone_take`'s `label=`
    argument); nothing later in this module ever recomputes a take's label,
    which is what makes a user rename via `PATCH` permanent - see this
    module's `rename_take`.
    """
    if plan.reroll_is_dominant:
        assert reroll_from is not None
        phase = BY_KEY[reroll_from]
        return f"Take {new_id} · re-roll {phase.name.lower()}"

    best_id = min(changed_ids, key=_phase_id_of_setting)
    spec = get_spec(best_id)
    assert spec is not None
    label = f"Take {new_id} · {spec.label.lower()}"
    extra = len(changed_ids) - 1
    if extra > 0:
        label += f" +{extra} more"
    return label


def _sync_current_take(project: ProjectDoc, take: Take) -> None:
    """Project a take onto `ProjectDoc`'s own canonical fields.

    `ProjectDoc.current_take_id` is what Home's thumbnail, `master_output`
    and the export default key off (see `avg.core.takes`'s module
    docstring) - this is the one place in the app that fulfils "advancing
    it when a take finishes is a pipeline-runner concern (Task 3)" that
    `avg.core.takes` deliberately left undone. `aspect_ratio` is restored
    from the take's own `settings_snapshot` (present since `avg.pipeline.
    create.new_project`/the create route below always records it) rather
    than added as a new field on `Take` - every real phase reads
    `ctx.project.aspect_ratio` directly (see `avg.pipeline.phases._shared`),
    so this is what makes an `aspect_ratio` override actually take effect
    for the phases that ran, and what a later `select` of a different take
    restores correctly.
    """
    project.current_take_id = take.id
    project.master_output = take.master_output
    if "aspect_ratio" in take.settings_snapshot:
        project.aspect_ratio = take.settings_snapshot["aspect_ratio"]


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.get("/api/projects/{project_id}/takes")
async def list_takes(project_id: str, request: Request) -> dict:
    project = _get_project(request.app.state, project_id)
    # Safe on every call (idempotent, a no-op once takes exist) - see
    # `avg.core.takes.migrate_to_takes`'s own docstring. A legacy project
    # that has never touched this API still shows a "Take 1".
    migrate_to_takes(project)
    return {"takes": list(project.takes), "current_take_id": project.current_take_id}


@router.post("/api/projects/{project_id}/takes/preview")
async def preview_take(project_id: str, body: TakeRequest, request: Request) -> dict:
    """Compute-only: no take is cloned, no folder is created, no pipeline
    starts, and `project`/`app.state` are never mutated."""
    project = _get_project(request.app.state, project_id)

    _validate_override_ids(body.overrides)
    changed_ids = list(body.overrides.keys())
    plan = _build_plan(changed_ids, body.reroll_from)

    secs, unknown = estimate_seconds(project, plan.rerun)
    return {"rerun": plan.rerun, "keeps": plan.keeps, "estimate_seconds": secs, "unknown": unknown}


@router.post("/api/projects/{project_id}/takes", status_code=status.HTTP_201_CREATED)
async def create_take(project_id: str, body: TakeRequest, request: Request) -> Take:
    app_state = request.app.state
    project = _get_project(app_state, project_id)

    # The exact one-job-at-a-time guard `avg.api.app.create_project`/
    # `start_render` already enforce - a take's pipeline run shares the same
    # `render_thread` slot, never a second job mechanism.
    if app_state.render_thread is not None and app_state.render_thread.is_alive():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"a pipeline is already running for project {app_state.rendering_project_id!r}",
        )

    _validate_override_ids(body.overrides)
    changed_ids = list(body.overrides.keys())
    plan = _build_plan(changed_ids, body.reroll_from)

    active_id = channels_config.active_channel_id(app_state.settings_path)
    if active_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no active channel — activate a channel before creating a take",
        )
    channel = channels_config.get_channel(active_id, app_state.channels_dir)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"active channel {active_id!r} no longer exists",
        )

    # Validate + coerce every override and fold it onto a THROWAWAY copy of
    # the channel's settings dict - `channel` itself is never mutated or
    # saved (see this module's docstring; matches `avg.api.app.
    # ProjectCreate`'s own rule verbatim).
    effective_settings = dict(channel.settings)
    for setting_id, raw_value in body.overrides.items():
        spec = get_spec(setting_id)
        assert spec is not None and spec.scope == "channel"  # already checked by _validate_override_ids
        try:
            effective_settings[setting_id] = validate_value(spec, raw_value)
        except SettingValidationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    effective_channel = channel.model_copy(update={"settings": effective_settings})

    # A legacy/never-touched project has no takes yet - this is the one
    # spot in this module allowed to create take 1 (unlike `preview`, this
    # route is already in the business of creating a take).
    migrate_to_takes(project)
    source_take_id = project.current_take_id
    assert source_take_id is not None  # migrate_to_takes always sets this

    stored_reroll = body.reroll_from if plan.reroll_is_dominant else None
    label = _auto_label(_next_take_id(project), changed_ids, body.reroll_from, plan)

    new_take = clone_take(
        project,
        source_take_id,
        plan.keeps,
        label=label,
        changed_ids=changed_ids,
        rerolled_from=stored_reroll,
        settings_snapshot=effective_settings,
    )

    # The pipeline runs against the NEW take's own folder, never the
    # project root - see this module's docstring for why swapping `folder`
    # (and sharing `phases` by reference) is all that's needed. `aspect_ratio`
    # is swapped too since (unlike every other creative setting) phases read
    # it straight off `ctx.project`, not `ctx.channel.settings` - see
    # `_sync_current_take`.
    shadow_project = project.model_copy(
        update={
            "folder": take_folder(project, new_take.id),
            "phases": new_take.phases,
            "master_output": new_take.master_output,
            "aspect_ratio": effective_settings.get("aspect_ratio", project.aspect_ratio),
        }
    )

    cancel_token = CancellationToken()
    app_state.pipeline_cancel_token = cancel_token
    loop = asyncio.get_running_loop()

    async def _dispatch(event: Any) -> None:
        await app_state.manager.broadcast(event.model_dump(mode="json"))

    def _worker() -> None:
        success = True
        try:
            for event in run_pipeline(
                shadow_project,
                channel=effective_channel,
                start_from=plan.start_id,
                cancel_token=cancel_token,
            ):
                if isinstance(event, (PhaseFailed, PipelineCancelled)):
                    success = False
                with suppress(Exception):
                    fut = asyncio.run_coroutine_threadsafe(_dispatch(event), loop)
                    fut.result()
        finally:
            # `shadow_project.phases` IS `new_take.phases` (shared by
            # reference), already up to date - only `master_output` needs
            # copying by hand, since `RenderPhase.run` only ever writes
            # `ctx.project.master_output` (the shadow), never the take.
            new_take.master_output = shadow_project.master_output
            if success:
                _sync_current_take(project, new_take)
            app_state.render_thread = None
            app_state.rendering_project_id = None
            app_state.pipeline_cancel_token = None

    app_state.rendering_project_id = project_id
    thread = threading.Thread(target=_worker, daemon=True, name=f"avg-take-{project_id}-{new_take.id}")
    app_state.render_thread = thread
    thread.start()

    return new_take


@router.delete("/api/projects/{project_id}/takes/{take_id}")
async def delete_take_route(project_id: str, take_id: int, request: Request) -> dict:
    project = _get_project(request.app.state, project_id)
    _get_take(project, take_id)  # 404 before delete_take's own (ValueError-based) checks

    try:
        _delete_take(project, take_id)
    except ValueError as exc:
        # The only ValueError left once existence is already confirmed above
        # is "cannot delete the last remaining take" - a business rule, not
        # a missing-resource error.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {"deleted": True, "current_take_id": project.current_take_id}


@router.post("/api/projects/{project_id}/takes/{take_id}/select")
async def select_take_route(project_id: str, take_id: int, request: Request) -> dict:
    project = _get_project(request.app.state, project_id)
    take = _get_take(project, take_id)
    _sync_current_take(project, take)
    return {"current_take_id": project.current_take_id}


@router.patch("/api/projects/{project_id}/takes/{take_id}")
async def rename_take_route(project_id: str, take_id: int, body: TakeRename, request: Request) -> Take:
    project = _get_project(request.app.state, project_id)
    take = _get_take(project, take_id)

    label = body.label.strip()
    if not label:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="label must not be empty")

    # A user rename is permanent - nothing else in this module ever
    # recomputes `take.label` after creation (`_auto_label` is only ever
    # consulted inside `create_take`, at the moment `clone_take` is called),
    # so simply overwriting it here can never be clobbered by a later
    # auto-label pass, because there isn't one.
    take.label = label
    return take

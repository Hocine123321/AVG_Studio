"""The settings/channels/keys surface (Slice 2, Task 3).

Renders itself from `avg.core.settings_schema.SETTINGS` - adding a setting
is one entry there, never a route change here. Routes stay thin: validate
against the schema, delegate to `avg.config.settings` / `avg.config.channels`
/ `avg.config.secrets`, return. No merge layer, no diffing engine, no undo
stack - those modules already refuse to have one (see their docstrings)
and this router does not reintroduce one at the HTTP layer.

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

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from avg.config import channels as channels_config
from avg.config import secrets as secrets_config
from avg.config.settings import load_settings, save_settings
from avg.core.settings_schema import (
    KEY_PROVIDER_IDS,
    SETTINGS,
    SettingValidationError,
    get_spec,
    validate_value,
)

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

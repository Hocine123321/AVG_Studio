# Slice 2 — Configure the app — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Everything you need to set the app up, with no generation yet — channels, securely stored API keys, and a settings surface that replaces the old app's 4,385-line settings menu.

**Architecture:** Extends Slice 1. Python engine behind FastAPI on loopback, React UI in WebView2. Settings and channels are documents under `%APPDATA%/AVG Studio/`; secrets go to Windows DPAPI, never to those documents.

**Tech Stack:** Python 3.12 · FastAPI · pydantic v2 · ctypes (DPAPI) · React 19 + Vite · pytest

## Execution mode

Carried over from Slice 1, at the user's request: speed over ceremony.

- No per-step RED/GREEN cycles. Write code and tests, run the suite once at the end of a task.
- One commit per task.
- **No front-end unit tests.** Verify UI by looking at it.
- Python tests stay — they cover what you cannot see.
- Reviews at two checkpoints: after Task 3, and a final whole-branch review.

## Global Constraints

Unchanged from Slice 1, plus:

- **Windows only.** Python 3.12.
- **Nothing below `avg/api/` may print or prompt.**
- **API keys never appear in `settings.json`, in a channel document, in a log line, or in any API response.** A response may say a key *is set*, never what it is.
- **The channel is the settings scope.** There is no global copy of a creative setting, no apply step, no capture step, no absent-means-inherit rule. This is the structural fix for three of the seven bugs found in the PowerShell app on 2026-07-24.
- **Every persisted document carries `schema_version`**, forward-only migrations.
- Design tokens in `web/src/tokens.css` are authoritative: warm canvas `#1c1a16`, terracotta `#e08a4e` for the single primary action, sage `#aebf92` for settled state, Caprasimo display over Figtree, radii 12/20/28. Both `night` and `day` themes must work.
- Sentence case everywhere.
- Reference app (read-only): `D:\AI_Video_Gen`.

---

## File structure

| Path | Responsibility |
|---|---|
| `avg/config/secrets.py` | DPAPI protect/unprotect, key storage |
| `avg/config/channels.py` | Channel documents: load, save, list, create, delete |
| `avg/core/settings_schema.py` | The catalogue of every setting: id, group, type, default, help |
| `avg/api/routes_settings.py` | Settings, channels and keys routes |
| `web/src/Settings.tsx` | Settings screen shell + search |
| `web/src/SettingsGroup.tsx` | One group of fields |
| `web/src/Channels.tsx` | Channel list, create, switch, delete |
| `web/src/fields/` | Field renderers by type |
| `tests/config/test_secrets.py` | Round-trip, tamper, absent |
| `tests/config/test_channels.py` | CRUD, isolation between channels |
| `tests/api/test_routes_settings.py` | Routes, and that keys never leak |

---

### Task 1: Secret storage on DPAPI

The old app stores keys DPAPI-encrypted in `config/secrets.json` via PowerShell's `ConvertFrom-SecureString`. The rewrite needs the same protection from Python, and the program spec flagged reading the old format as an unverified spike.

**Files:**
- Create: `avg/config/secrets.py`
- Test: `tests/config/test_secrets.py`

**Interfaces:**
- `set_key(provider: str, key: str) -> None`
- `get_key(provider: str) -> str | None`
- `has_key(provider: str) -> bool`
- `list_providers() -> list[str]`
- `delete_key(provider: str) -> None`

**Rules:**
- Encrypt with `CryptProtectData` / decrypt with `CryptUnprotectData` via `ctypes`, `CRYPTPROTECT_UI_FORBIDDEN`, current-user scope. Ciphertext is base64 in `%APPDATA%/AVG Studio/secrets.dat`.
- **A decrypt failure returns `None`, never raises and never crashes the app.** Copying the file to another machine or another user account makes it undecryptable by design — that is DPAPI working, and it must present as "no key set", not as a broken app.
- `VAF_<PROVIDER>_API_KEY` environment variables win over stored keys, matching the old app's escape hatch.
- **Also attempt to read the legacy `D:\AI_Video_Gen\config\secrets.json`** and report per provider whether it could be decrypted. This finally answers the spike the program spec left open. If PowerShell's format cannot be read, that is a finding to record, not a failure — the welcome screen can ask the user to paste keys once.

- [ ] Implement, test, commit. Tests must cover: round-trip, absent file, corrupt base64, tampered ciphertext, env-var precedence.

---

### Task 2: Channel documents

**Files:**
- Create: `avg/config/channels.py`
- Test: `tests/config/test_channels.py`

**Interfaces:**
- `Channel(schema_version: int, id: str, label: str, note: str, settings: dict)`
- `list_channels() -> list[Channel]`, `get_channel(id)`, `save_channel(ch)`, `create_channel(label, note="") -> Channel`, `delete_channel(id)`, `active_channel_id() -> str | None`, `set_active(id)`

**Rules:**
- One JSON file per channel in `%APPDATA%/AVG Studio/channels/<id>.json`. The id is a slug of the label, deduplicated.
- **A channel owns its creative settings outright.** Reading a setting means reading the active channel's document. There is no merge with a global layer.
- Deleting the active channel clears the active pointer; the app must behave with no channel selected.
- Seed three channels on first run only if the folder is absent entirely — and **never re-seed**. The old app re-created deleted built-in profiles on every launch because defaults were back-filled; a deleted channel stays deleted.

- [ ] Implement, test, commit. Tests must cover: create/save/load round-trip, slug dedup, delete-active behaviour, and that a deleted seed does not come back.

---

### Task 3: Settings schema and routes

**Files:**
- Create: `avg/core/settings_schema.py`, `avg/api/routes_settings.py`
- Modify: `avg/api/app.py` to mount the router
- Test: `tests/api/test_routes_settings.py`

**The schema is the single source of truth.** One entry per setting: `id`, `group`, `label`, `help`, `type` (`bool` | `int` | `float` | `text` | `choice` | `path` | `secret`), `default`, `choices`, `scope` (`app` | `channel`). The UI renders itself from this — adding a setting is one entry, not a UI change.

**Routes:**
- `GET /api/settings/schema` → the catalogue
- `GET /api/settings/values` → app values plus the active channel's values
- `POST /api/settings/values` → save; app-scope goes to `settings.json`, channel-scope to the active channel
- `GET /api/channels`, `POST /api/channels`, `DELETE /api/channels/{id}`, `POST /api/channels/{id}/activate`
- `POST /api/keys/{provider}`, `DELETE /api/keys/{provider}`, `GET /api/keys` → **`{provider: bool}` only, never values**

- [ ] Implement, test, commit. A test must assert that no route ever returns a key's value.

**CHECKPOINT REVIEW after this task.**

---

### Task 4: Settings screen

**Files:**
- Create: `web/src/Settings.tsx`, `web/src/SettingsGroup.tsx`, `web/src/fields/*`
- Modify: `web/src/App.tsx`, `web/src/Rail.tsx`

**Search leads, categories follow.** The old menu needed 4,385 lines because every option required a numbered home. Here: type "silence" and get the settings that mention it, wherever they live. The category list is the fallback, not the primary control.

- Fields render from the schema by `type`; adding a setting must require no change here.
- A `secret` field shows "set" or "not set" and offers replace or remove. It never displays a value, and never round-trips one it did not receive from the user.
- A channel-scoped field is visibly marked as belonging to the active channel, so it is always obvious where a change lands. This is the honest-by-design fix for the old app's silent auto-save.
- One terracotta action per screen.

- [ ] Implement, look at it in both themes, commit.

---

### Task 5: Channels screen

**Files:**
- Create: `web/src/Channels.tsx`
- Modify: `web/src/App.tsx`

- Cards per channel: label, note, how many settings it pins, whether it is active.
- Create, rename, delete with confirmation, switch active.
- With no channel selected the app still works — say so plainly rather than blocking.

- [ ] Implement, look at it, commit.

---

## Out of scope for Slice 2

AI providers, image generation, the pipeline phases, the create wizard, trend discovery, app chat. Slice 3 covers generation.

# Slice 12 — Settings architecture

**Goal:** give the app's own behaviour a home in Settings, let a channel
choose between its own settings and the app-wide defaults without
reintroducing the merge layer that caused three legacy bugs, move the Live
List to where it is configured rather than where it is diagnosed, and give
App Health a check that exercises the app rather than describing it.

**Date:** 2026-07-31. Every count and path below was read from the running
app and the packaged build on that date.

Second slice of the 2026-07-31 backlog. It comes before Live Preview
(Slice 14) and the Smart model picker (Slice 15) because both of those hang
new settings and a relocated Live List off decisions made here.

---

## 1. What the evidence shows

### 1.1 The app has almost no settings about itself

Measured from `avg.core.settings_schema.SETTINGS` on 2026-07-31: **56
settings, of which exactly two are app-scope and not a key** — `theme` and
`verbose_log`, both in the `runtime` group. Everything else is channel-scope
production configuration or one of the eight provider keys.

There is no home for "how should this application behave" as distinct from
"how should this video be made". Motion, startup, logging and diagnostics
preferences all want one.

### 1.2 The dead-switch lesson is already written down

`settings_schema.py:79-93` records three settings removed on 2026-07-28
because the UI rendered and saved them and no Python ever read them:
`python_exe`, `performance_mode`, `run_as_admin`. The live `settings.json`
on this machine still carries the last two as leftover keys — harmless, and
explicitly documented as harmless.

**They are not to be revived.** The new group holds settings that are wired
to something, or it repeats the mistake the comment exists to prevent. Every
entry added by this slice names, in its `help` or in a comment, the code
that reads it.

### 1.3 The channel/global toggle collides with a non-negotiable

`CLAUDE.md`, Config scopes:

> `scope="channel"` settings live in the **active channel's** document, full
> stop. No global copy, no apply/capture step, no absent-means-inherit.
> Three bugs in the legacy app came from the copy mechanism this deletes.

A "use the global preset instead" toggle is that mechanism, if built the
legacy way. But the codebase already ships a sanctioned shape for exactly
this question — `live_list_source` (`settings_schema.py:270`):

```python
id="live_list_source", scope="channel", choices=["global", "channel"],
default="global",
```

and `avg/api/routes_settings.py`'s `_prefs_for_scope`, whose docstring says
it "does NOT fall back to the global one - the same no-merge-layer rule
every channel-scoped setting follows."

That is the distinction that matters. The three legacy bugs came from
**per-field, implicit** inheritance — a field absent from a channel silently
reading a global value. An **explicit, whole-document either/or** has no
per-field resolution to get wrong.

### 1.4 The packaged app cannot run its own test suite

`AVG APP/_internal` holds 76 entries on 2026-07-31 and contains **no
`tests/` directory and no pytest**. `tools/build.py` references neither.

So a Deep Check defined as "run `/tests`" would work in a dev checkout and be
absent from every installed copy — the exact place a user most needs a
diagnostic. Meanwhile `avg/core/health.py` already is an in-app check
framework: `Component`, `Stage`, three ordered stages, `report()`.

### 1.5 The Live List is filed under diagnosis, not configuration

It is reached from App Health, which is where the app reports on itself. But
the Live List decides which models the Settings pickers may offer — it is
configuration, and it is the thing `image_model` and the per-phase model
fields read from (`source="live_list:image"`).

---

## 2. Decisions

Each chosen from stated alternatives during brainstorming on 2026-07-31.

1. **Channel vs global is a whole-document either/or**, mirroring
   `live_list_source`. Not per-field inheritance, not named presets.
2. **Deep Check is in-app self-checks extending `health.py`.** Not pytest
   shelled from a checkout, and the test suite is not bundled.
3. **The new group holds only wired settings**, per §1.2.

---

## 3. Architecture

Four pieces. Two are settings-catalogue work, one is a new health stage, one
is a screen move. Nothing here changes the phase registry or what a phase
writes.

### 3.1 An "App" group for the app's own behaviour

A new `app` group in `SETTINGS`, holding the settings that describe the
application rather than the video.

`theme` and `verbose_log` move into it from `runtime`, which then has no
entries and ceases to exist as a group. The Settings screen derives its nav
from the distinct `group` values in `GET /api/settings/schema` (Slice 10,
§3.2), so a group appearing and another vanishing costs no UI change —
except `GROUP_LABELS` in `web/src/Settings.tsx`, which gains `app` and loses
`runtime`, and the Components panel condition, which currently keys on
`runtime` and moves to `app` with it.

New entries, all `scope="app"`:

| id | type | default | read by |
|---|---|---|---|
| `motion_scale` | `choice` — `full`, `reduced`, `none` | `full` | `web/src/tokens.css` via a `--motion-scale` custom property set on `<html>` |
| `startup_check` | `bool` | `true` | `avg/shell.py` — whether to run the health warmup before showing the library |

`motion_scale` multiplies every transition and animation duration in
`tokens.css` through one custom property, rather than being read at dozens
of call sites. **`prefers-reduced-motion` still wins**: the existing
reduced-motion block is unchanged and a user whose OS asks for less motion
gets less motion regardless of this setting. The setting can only reduce
motion below the OS preference, never restore it.

Both entries name their reader above, per §1.2.

### 3.2 A channel chooses its whole settings document, or the app's

One new entry, mirroring `live_list_source` exactly:

```python
id="settings_source", group="app", scope="channel",
type="choice", choices=["channel", "global"], default="channel",
invalidates=None,
```

`scope="channel"` because the choice belongs to a channel — the same reason
`live_list_source` is channel-scoped. It sits in the `app` group because it
describes how configuration behaves rather than how a video is made.

Resolution, and this is the whole design:

- `settings_source == "channel"` — channel-scope values come from the active
  channel's document. Exactly today's behaviour, and the default, so an
  existing install behaves identically until the user changes it.
- `settings_source == "global"` — channel-scope values come from **one
  app-wide defaults document**, `%APPDATA%/AVG Studio/channel_defaults.json`,
  in its entirety.

**There is no third case.** No field is ever resolved by looking in one
document and falling back to the other. A missing field in whichever
document is selected means the `SettingSpec`'s own `default`, which is
already how an absent value resolves today. That is what makes this not the
forbidden mechanism: there is no per-field inheritance to get wrong, so the
three legacy bugs have no shape to recur in.

**Porting** is one explicit action — a button that copies the active
channel's channel-scope values into `channel_defaults.json`, wholesale, after
naming what it will overwrite and asking. It is a snapshot the user takes,
never an automatic capture, and it happens at a moment they chose.

`CLAUDE.md`'s Config scopes section is updated by this slice to record the
sanctioned shape and why it is not the deleted mechanism. A rule and the code
disagreeing is worse than either.

### 3.3 Deep Check: a fourth stage that exercises the app

`avg/core/health.py` gains a fourth stage after Dependency check, App warmup
and Safeguards. The existing three answer "is it present and does it parse".
This one answers "does it work", by doing the thing and undoing it:

| Check | What it actually does |
|---|---|
| Settings round-trip | Writes a value to a temp settings path, reloads, compares, discards |
| Channel document | Parses every channel document and reports any that fail |
| Secret round-trip | DPAPI encrypt then decrypt a throwaway string — **never touches a stored key** |
| Output root | Creates and deletes a file in the library folder |
| FFmpeg encode | Encodes a one-second colour clip to a temp file and checks it plays |
| Thumbnail | Generates a thumbnail from that clip |
| Model cache | Reads `working_models.json` and reports entry counts |

It runs on demand from a plain `.btn` on App Health — never on load, because
it spends real disk and a subprocess. It reuses `Stage`/`Component`, so
`GET /api/health/report` grows a stage and the screen renders it with no new
component.

**No provider is called and nothing is generated**, so Deep Check costs
nothing and cannot spend money. It works identically in a dev checkout and a
packaged build, which is the property §1.4 rules out for pytest.

### 3.4 The Live List moves to Settings

It becomes a screen reached from Settings rather than App Health. App
Health's Live List card is removed; the models group in Settings gains the
entry point, next to the fields whose options it decides.

The screen itself is unchanged — this is a move, not a rewrite.

---

## 4. Testing

Python covers the catalogue and the resolution rules; the frontend is
verified by looking, per convention.

- **Scope resolution**: with `settings_source="channel"` a channel's own
  value wins; with `"global"` the defaults document's value is used; a field
  absent from the selected document resolves to the `SettingSpec` default and
  **never** to the other document. That last assertion is the one that
  guards §1.3.
- **Porting** copies every channel-scope value into the defaults document,
  copies no app-scope value, and copies no secret.
- **The catalogue** still satisfies the existing completeness test: every
  `SettingSpec` declares `invalidates`, and the `runtime` group is gone with
  no entry orphaned.
- **Deep Check** reports a failed component rather than raising when ffmpeg
  is missing, writes nothing outside the temp and output paths it is given,
  and leaves no file behind. Tests pass `create_app`'s path overrides so
  nothing touches the real `%APPDATA%`.
- **No secret value** appears in any Deep Check output or health response.

Target: **769 tests still passing** (Slice 10's figure, carried through Slice
11), mypy no worse than its `Found 688 errors in 22 files` baseline.

---

## 5. Verification

- `.venv-build/Scripts/python.exe -m pytest tests -q` — never fewer than 769.
- `.venv-build/Scripts/python.exe -m mypy` — no worse than 688 errors.
- `npm --prefix web run build && npm --prefix web run lint` — clean.
- Settings shows an **App** group and no Runtime group; the Components panel
  is still on screen, now under App.
- Set `motion_scale` to `reduced`, reload: transitions are visibly shorter.
  Set the OS to prefer reduced motion with `motion_scale` at `full`: motion
  is still suppressed — the OS wins.
- Switch a channel to `settings_source="global"`, change a value in the
  defaults, and confirm the channel reads it. Switch back and confirm the
  channel's own value returns **unchanged** — the round trip must not have
  written through.
- Press Port to global, confirm the dialogue names what it overwrites, and
  check `channel_defaults.json` afterwards.
- Deep Check on App Health: every check reports, a one-second clip really is
  encoded, and no file is left in the library folder afterwards.
- The Live List opens from Settings; App Health no longer offers it.
- `"Build Installer.bat"` — Deep Check behaves identically in `AVG APP/`,
  which is the whole reason it is not pytest.

---

## 6. Out of scope

- Live Preview (Slice 14) and the Smart model picker (Slice 15), both of
  which depend on this slice.
- Motion and startup polish (Slice 13). `motion_scale` is defined here
  because it is a setting; the pill and startup animations it will scale are
  that slice's subject.
- Parallel pipelines (Slice 16).
- Named presets. The either/or switch covers the stated need; presets are a
  larger feature and would want their own evidence.
- Retiring `web/src/phases.ts`. Slice 11 adds the endpoint that would allow
  it; the nested sub-step display still has no backend model.

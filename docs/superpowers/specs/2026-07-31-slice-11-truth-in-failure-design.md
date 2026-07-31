# Slice 11 — Truth in failure

**Goal:** when the app cannot make a video, it says exactly why, offers the
thing that would repair it, and tries the alternative the user already
configured — instead of reporting `bug` with an empty detail.

**Date:** 2026-07-31. Every measurement and log line quoted below was taken
from the running app and the real library on that date, not estimated.

This is the first slice of the 2026-07-31 backlog, chosen because the app
currently cannot produce a video at all. The remaining slices (settings
architecture, motion and startup, Live Preview, Smart model picker, parallel
pipelines) are polish or new capability on top of a base that does not yet
work.

---

## 1. What the evidence shows

### 1.1 The real reason a video fails is thrown away

`D:\AI_Video_Gen\output\20260730_152345_dan_harmon…\take.log`, the newest
failing run:

```
POST aihorde.net ms=593 provider=stablehorde outcome=stablehorde request failed with HTTP 403
ERROR phase_failed phase=visuals kind=bug message=visuals phase: every scene failed to generate an image detail=
```

Three facts, in order:

- `avg/providers/errors.py:292` already maps 401/403 to `ErrorKind.auth`
  before any body text is inspected. The classification is correct.
- `avg/pipeline/phases/visuals.py:163` catches each scene's `AvgError` and
  keeps `exc.message` — the real, classified sentence — on `_SceneResult`.
- `avg/pipeline/phases/visuals.py:267` then raises
  `AvgError(ErrorKind.bug, "visuals phase: every scene failed to generate an
  image")`, discarding every one of those messages and the kind with them.

So the app knew it was a 403 from a keyless provider, and told the user
`kind=bug`, `detail=` empty. `_SceneResult` (`visuals.py:141`) keeps only
`error: str` and no `ErrorKind`, so the kind is lost one line earlier than
the raise.

The same run shows Pollinations failing differently and equally silently:
`WinError 10060`, a connection timeout after 129 seconds, classified
`network`. Two unrelated causes, one indistinguishable message.

### 1.2 Stable Horde has no key, and nothing said so

`POST aihorde.net → 403`. Stable Horde's catalogue is public, so
`GET /api/models/cache` reports `reachable: {"image/stablehorde": true}` —
measured on 2026-07-31. Reachability is an anonymous catalogue GET;
generation is not. A provider can therefore read **Reachable** and be
incapable of producing a single image.

That is honest under Slice 10's own definition ("the provider answered its
catalogue — this model is unproven") and it is still what walked the user
into a dead end.

### 1.3 The Google-models complaint is not a bug

Reported as "google provider models nowhere to be seen except flash 2.5".
Measured before and after a single free catalogue refresh on 2026-07-31:

| Cache entry | Before | After |
|---|---|---|
| `__text__gemini` | absent | **33** |
| `__text__groq` | absent | 10 |
| `__text__huggingface` | absent | 20 |
| `__voice__edge` | 0 | **322** |
| `gemini` (probe verdicts) | 1 | 1 |

Slice 10's text-catalogue listing works. The stored cache simply predated
it, and the one Gemini entry on screen was the *probe* result — exactly the
"32 listed, 1 working" that `CLAUDE.md` already records.

The defect is therefore not missing models. It is that **a stale catalogue
is invisible**: the screen showed one model and gave no indication that a
free refresh would show thirty-three. That cost the user thirty-two models
and an inaccurate bug report.

### 1.4 Re-running a phase leaves the ones after it lying

Observed in the Studio pipeline rail: Script reads `Not started` while
Director I, Visuals, Voice, Director II and Screen Text all read done and
Render reads failed. Downstream phases cannot be current if the script they
were derived from never ran. (The `(2)` badge beside Script and Render is
the sub-step count — Draft/Segmentation, Base video/Overlays — not a take
number.)

Nothing warns that rendering now would produce a video whose images belong
to a script that no longer exists.

### 1.5 Two phase panels look unlike every other

`web/src/artifacts/PhaseArtifact.tsx:86` returns `null` without a body, and
each body renders `Missing what=…` when its phase wrote no files. An
un-started phase therefore collapses to a bare sub-step list, and a failed
Render to that list plus a red banner, while every completed phase gets a
table, an image grid or an audio player. The panels are not inconsistent by
accident; they are empty because nothing was written. It still reads as
broken.

---

## 2. Decisions

Each chosen from stated alternatives during brainstorming on 2026-07-31.

1. **Failure reporting: say it, offer the fix, and fall back.** Not
   report-only, and not a silent retry.
2. **Image fallback copies the text router's shape**, rather than inventing
   a second fallback mechanism.
3. **Re-running a phase cascades downstream to pending**, reusing the
   existing invalidation machinery. Not a new "stale" phase state.
4. **An idle phase panel describes the phase; a failed one shows partials
   plus the reason.** Not a uniform dimmed-artifact list.
5. **Catalogue staleness is stated on screen**, replacing the
   Google-models item that the evidence disproved.

---

## 3. Architecture

Seven pieces. Three are backend-only, three frontend-only, one spans both.
Nothing here changes the phase registry's order, the settings catalogue's
role, or which files a phase writes.

### 3.1 A phase failure carries its kind and its reasons

`_SceneResult` gains `kind: ErrorKind | None` beside its existing `error`
string, set from the caught `AvgError`. When every scene fails,
`visuals.py` raises with **the dominant kind among the scene failures**
rather than `ErrorKind.bug`, and a message naming the provider, the model
and the reason.

Dominant kind is the most frequent; ties break by `_CHECK_ORDER` in
`avg/providers/errors.py`, which already encodes which classification is the
more actionable claim. No new ordering is invented.

The message reads, for the run in §1.1:

> Visuals: all 2 scenes failed on `stablehorde/flux` — Stable Horde refused
> the request (no API key set).

`ErrorKind.bug` remains correct for the two genuine bugs in that file
(`no finished scenes.json`, `no usable scene entries`) and is unchanged.

### 3.2 Images get the text router's fallback, not a new one

`avg/providers/text/router.py` already documents the shape: a required
primary, an optional backup, and `on_failure` in `{stop, fall_back}`. The
decision to switch consults **only** `POLICY[kind].on_exhausted` — never a
status code or an error string — and the backup is tried **exactly once,
never chained**, matching the legacy `$triedFallback` flag.

Visuals adopts that shape through three `SettingSpec` entries in the
`images` group, all `scope="channel"`, all `invalidates="visuals"`:

| id | type | mirrors |
|---|---|---|
| `image_provider_backup` | `choice` | `image_provider` |
| `image_model_backup` | `text`, `source="live_list:image"` | `image_model` |
| `image_on_failure` | `choice` (`stop`, `fall_back`) | the text phases' "if both fail" |

Settings already renders "— model / — backup / — if both fail" triplets for
every text phase, so this costs three catalogue entries and no new UI.

**A fallback that cannot help is not attempted.** `POLICY` marks
`content_filter`, `bug` and `dependency` as `on_exhausted="fail"`; a second
provider would fail identically, and the original error propagates.

### 3.3 Reachable is not usable

`GET /api/models/cache`'s `reachable` map keeps its current meaning — it is
a true statement about a catalogue GET and Slice 10 chose it deliberately.
What changes is that a provider which **needs a key and has none** is
reported as such, from `avg/config/secrets.py`'s existing
key-presence check, so the Live List and the Test Providers card can say
"Reachable · no key — cannot generate" instead of a bare "Reachable".

No key value is read, logged or returned; this uses only the boolean
`GET /api/keys` already exposes.

### 3.4 Re-running a phase invalidates what came after it

Re-running a phase marks every phase after it in `REGISTRY` pending, through
`avg/core/invalidation.py` — the same machinery a changed setting already
drives. **Files are not deleted**, matching how a take clone keeps them; only
the records stop claiming to be current.

This adds no phase state. `PhaseRecord.status` keeps its existing values.

### 3.5 A phase panel says what the phase is for

Each phase gains one sentence describing what it does and what it writes,
stored **once**, in `avg/pipeline/registry.py` beside the phase's `id`,
`key` and `name` — the file that already owns phase identity and is the
documented single source of truth for it.

Deliberately **not** `web/src/phases.ts`: `CLAUDE.md` records that file as
live duplication of `REGISTRY` and an outstanding problem, and adding prose
to it would widen exactly the duplication the codebase wants to close.

No route serves phase definitions today — `phases.ts`'s own docstring calls
itself "deliberate frontend duplication **until the backend serves phase
definitions**". So this slice adds the smallest one that satisfies that
sentence:

    GET /api/phases -> [{id, key, name, description}, ...] in REGISTRY order

The panel reads its description from there. **`phases.ts` is not deleted in
this slice**: it also carries the nested Script/Render sub-step display
(Draft/Segmentation, Base video/Overlays), which `REGISTRY` does not model —
its docstring says so explicitly — and collapsing that is a larger change
than this slice's subject. What this does is stop the duplication growing,
and put the endpoint in place that closing it will need.

- **Idle** (`pending`) — the sentence, and the artifacts the phase will
  write, named from `avg/core/artifacts.py`'s existing labels.
- **Failed** — whatever artifacts do exist, plus the failure reason from
  §3.1 and, where the kind is `auth` or `model_missing`, a link to the
  setting that would repair it.
- **Done** — unchanged.

### 3.6 A stale catalogue says so

The Live List states when catalogues were last listed, from the `updated`
timestamp `working_models.json` already carries, and says plainly when it
has never been refreshed. This is the §1.3 defect: the number on screen was
right about the probe and silent about everything else.

### 3.7 One language, not one per accent

The Live List's language filter groups by the primary subtag: `en-GB`,
`en-US` and `en-AU` collapse to a single **English** entry that matches all
three. Voices keep their full locale in their own row — only the filter's
options collapse.

---

## 4. Testing

Python covers everything with logic worth asserting. The frontend has no
unit tests by convention.

- **Failure reporting**: a Visuals run where every scene fails with a 403
  raises `ErrorKind.auth`, not `bug`, and its message names the provider and
  the reason. A run mixing an auth failure and a timeout reports the
  dominant kind. A run where one scene succeeds still does not raise.
- **Image fallback**: a primary failing with `auth` tries the backup exactly
  once; a primary failing with `content_filter` does not try it at all; a
  backup that also fails propagates the primary's error. No test may make a
  real image call — `tests/conftest.py` forces `AVG_REPLAY_FIXTURES=1` and
  an image generation costs money.
- **Cascade invalidation**: re-running a mid-pipeline phase marks every
  later phase pending, leaves earlier ones alone, and deletes no file.
- **Key presence**: a provider with a catalogue but no key reports
  reachable-without-key, and no key value appears in any response.

Target: **769 tests still passing** (Slice 10's figure), mypy no worse than
its current `Found 688 errors in 22 files` baseline.

---

## 5. Verification

- `.venv-build/Scripts/python.exe -m pytest tests -q` — never fewer than 769.
- `.venv-build/Scripts/python.exe -m mypy` — no worse than 688 errors.
- `npm --prefix web run build && npm --prefix web run lint` — clean.
- Set an image provider with no key and run Visuals: the failure names the
  provider and says a key is missing, and offers the way to set one. The
  take log records `kind=auth`, not `kind=bug`.
- Configure a backup image provider and repeat: the backup is attempted once
  and the take log shows both attempts.
- Re-run Script on a finished project: every later phase reads pending, the
  files are still on disk, and Render refuses to claim it is current.
- Open an un-started phase: it describes itself instead of showing an empty
  panel. Open the failed Render: it shows the reason and any partial output.
- Live List: the language filter offers **English** once, not eleven
  variants, and states when catalogues were last listed.
- Nothing in this verification generates a paid image or spends ElevenLabs
  characters, checked against the take log.

---

## 6. Out of scope

- The five remaining slices from the 2026-07-31 backlog: the "App" settings
  category and channel/global porting, Live Preview, the Smart model picker,
  motion and startup polish, and parallel pipelines. Each gets its own spec.
- Making Stable Horde or Pollinations actually work. This slice makes the
  app **state** why they do not; obtaining a key is the user's to do.
- The card-background timing defect (`avg/media/overlays.py`), still open
  from Slice 9.
- **Closing** `web/src/phases.ts`'s duplication of `REGISTRY`. §3.5 adds the
  `GET /api/phases` route that closing it requires, and declines to widen the
  duplication meanwhile — but `phases.ts` also owns the nested sub-step
  display that `REGISTRY` does not model, so retiring it is its own change.

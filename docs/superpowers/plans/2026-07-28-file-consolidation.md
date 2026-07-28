# File Consolidation — corrected plan

Rewrite of `F:\Downloads\implementation_plan.md`, checked against the real
import graph and module contracts on 2026-07-28. That document was written
without reading this codebase: it describes an SSE event bus, an MUI theme,
an INI-format settings migration and abstract base classes, none of which
exist here, and four of its twelve merges introduce circular imports.

This plan keeps only merges that are **cycle-free** and **topically honest**
— the merged file has to be one coherent thing, not two files that happen to
be small. Net effect: **17 files removed** (~50 `avg/` source files → 41,
~32 `web/src/` → 26), not the 30 the original claimed.

---

## Why the layering matters

The package is a clean DAG today. Merging file B into file A gives A's
importers all of B's dependencies, so a merge is only safe when the two
files sit at compatible depths:

```
L0  core/errors  core/models  core/settings_schema  config/settings
    config/settings_import  config/secrets  media/child  media/ffmpeg_locate
    media/workspace  providers/models  pipeline/personality  api/deps
    pipeline/phases/_parsing
L1  core/events  core/takes  config/channels  providers/errors
    media/thumbnails  pipeline/prompts
L2  providers/transport  media/engine  pipeline/phase  pipeline/create
L3  providers/{text,images,voice}/*  pipeline/phases/_shared
L4  providers/text/router
L5  pipeline/phases/{brief,script,director1,director2,visuals,voice,captions,render}
L6  pipeline/registry
L7  core/invalidation  config/legacy_import  pipeline/runner
L8  export/capcut  api/routes_*  api/app
L9  shell
```

Rule used throughout: a merge is rejected if it would pull an L6+ module
into anything at L2 or below.

---

## Merges to perform

Each is one commit: create merged file with `# ── Section ──` headers,
update imports across `avg/` and `tests/`, delete originals, run tests.

### 1. `config/settings_import.py` → `config/settings.py`  (−1)

`settings_import` has **zero** `avg` imports — it reads the legacy app's
`config.json` into render settings. Both L0. No cycle.

Test files to update: 1 (`tests/config/test_settings_import.py`).

> The original plan also folded in `legacy_import.py`. **Rejected:** it
> imports `avg.pipeline.registry` ([legacy_import.py:42](../../../avg/config/legacy_import.py)),
> which lands at L7. `config.settings` is imported by `config.channels` →
> `pipeline.phase` → `phases/*` → `registry`. Circular.

### 2. `media/ffmpeg_locate.py` + `media/workspace.py` → `media/environment.py`  (−1)

Both L0 with no `avg` imports. Coherent: "where the render's inputs and
tools live on disk."

Also update the prose reference to `avg.media.ffmpeg_locate.bundled_ffmpeg_dir()`
in [tools/avg.spec](../../../tools/avg.spec) (a comment, not a hiddenimport —
`hiddenimports=[]`), and the import in [avg/shell.py:46](../../../avg/shell.py).

Test files to update: 0 (both are exercised indirectly).

### 3. `phases/_parsing.py` + `phases/_shared.py` → `phases/_common.py`  (−1)

`_parsing` is L0, `_shared` is L3, but every `_parsing` importer already
imports `_shared`. No new edge. `export/capcut.py` imports `_shared` and is
L8 — unaffected.

Test files to update: 3.

### 4. `phases/brief.py` + `phases/script.py` → `phases/writing.py`  (−1)

Both L5. Only `registry.py` and tests import them. Genuinely one unit — the
two text-generation phases, and `script.py`'s two-pass verbatim-partition
invariant reads better next to the brief it consumes.

Test files to update: 3.

### 5. `phases/director1.py` + `phases/director2.py` → `phases/director.py`  (−1)

Both L5, same importers. Keep the two classes' docstrings verbatim — they
carry the structure-planning and motion/grade contracts respectively.

Test files to update: 2 (may overlap with #4 — both are in
`tests/pipeline/test_phases_text.py` / `test_phases_polish.py`).

### 6. `providers/images/` — 6 → 3  (−3)

- `huggingface.py` + `pollinations.py` + `stablehorde.py` → `community.py`
- `gemini.py` + `openai.py` → `premium.py`
- `base.py` unchanged

All L3; importers are `phases/visuals.py` and tests only. The free/paid
split is a real distinction here (see the account-state note in CLAUDE.md:
Pollinations and Stable Horde work, OpenAI and Gemini do not).

Test files to update: 1 (`tests/providers/test_images.py`).

### 7. `providers/text/{groq,huggingface,pollinations}.py` → `text/community.py`  (−2)

All L3; importers are `router.py` and tests. `base.py`, `gemini.py`,
`router.py` unchanged.

> Note: the original plan justified keeping `gemini.py` separate as "primary
> provider." It is not — Gemini is at zero free-tier quota and **Groq is the
> only text backend that currently works.** Keeping Gemini out is fine, but
> for symmetry with images (paid vs free), not because it is primary.

Test files to update: 2.

### 8. `providers/voice/{edge,elevenlabs}.py` → `voice/providers.py`  (−1)

Both L3; importers are `phases/voice.py` and tests. `base.py` unchanged.

Test files to update: 1.

### 9. `core/events.py` → `core/errors.py`  (−1) — optional

`events.py` already imports `core.errors` and nothing else from `avg`.
Merging is cycle-free. Low value; include only if the file count target
matters more than `core/errors.py` staying a 528-byte leaf that 20+ modules
import. **Default: skip.**

### 10. `web/src/fields/{Bool,Choice,Float,Int,Path,Text}Field.tsx` → `fields/Fields.tsx`  (−5)

Each is 300–700 bytes and differs only in its input element. `index.tsx`
keeps the `Field` dispatcher and re-exports. `SecretField.tsx`,
`DeferredInput.tsx`, `types.ts` stay separate — the first has real logic,
the second is shared by four of the merged fields, the third is types.

The best merge in either plan.

### 11. `web/src/fonts.css` → top of `web/src/tokens.css`  (−1)

`tokens.css` already opens with `@import url("./fonts.css")`. Inline it and
drop the import. **Preserve the "do NOT replace this with a
fonts.googleapis.com @import" comment block verbatim** — it is a standing
rule (offline-first, no Google ping on launch), not a note.

---

## Merges rejected, with reasons

| Proposed | Verdict |
|---|---|
| `core/{errors,events,invalidation}` → `internals.py` | **Cycle.** `invalidation` imports `pipeline.registry` (L7); `errors` is an L0 leaf imported by `providers/errors`, `transport`, every phase. Merged, `import avg.core.errors` drags in the whole pipeline and comes back to a partially-initialised module. |
| `legacy_import.py` → `config/settings.py` | **Cycle.** See merge 1. |
| `pipeline/registry.py` → `pipeline/phase.py` | **Cycle.** All eight phases import `PhaseContext` from `pipeline.phase`; the merged file imports the phases back. Whether it explodes depends on which module the process imports first — the worst failure mode available. |
| `api/deps.py` → `api/app.py` | **Cycle.** `deps` is used by three modules, not one: `app.py`, `routes_settings.py`, `routes_takes.py`. `app.py` imports both routers, so the routers would import `app.py`. |
| `config/secrets.py` → `config/channels.py` | **Boundary violation.** Cycle-free, but `channels.py` is a config document the app freely reads, writes and logs, while `secrets.py` holds live DPAPI-protected credentials whose entire contract is that no value reaches a log line, an exception message, or a `repr()`. Also: secrets are per-**provider**, not per-channel as the original claimed. |
| `pipeline/personality.py` → `pipeline/create.py` | **Incoherent.** Cycle-free but wrong: `personality.py` parses tuned prompt copy out of `assets/personality.txt`; `create.py` scaffolds a project folder. Its only consumer is `pipeline/prompts.py`. If it must move, it moves there — but `prompts.py` is already 25 KB. |
| `providers/models.py` → `providers/transport.py` | **Incoherent.** Cycle-free, but a static model-catalogue table does not belong inside the record/replay HTTP transport, which is deliberately scoped to "send, record/replay, retry" and nothing else. |
| `SettingsGroup.tsx` → `Settings.tsx` | **False premise.** `SettingsGroup` is used by `NewTakePanel.tsx` as well as `Settings.tsx`. Merging makes the take panel import the entire settings page. |
| `Welcome.tsx` → `Home.tsx` | **False premise.** Both are rendered by `App.tsx` as siblings; `Welcome` is not inside `Home`. |
| `TakeStrip.tsx` → `ProjectCard.tsx` | **False premise.** `TakeStrip` lives in `Studio.tsx`, `ProjectCard` in `Home.tsx`. Different screens. |
| `Transport.tsx` + `Player.tsx` | **False premise.** `Transport` is the global bottom bar in `App.tsx`; `Player` is the video element inside `Studio.tsx`. |
| `phases.ts` + `theme.ts` → `constants.ts` | **Wrong shape.** `theme.ts` exports a React hook (`useTheme`) that writes `data-theme` and persists to `localStorage`, plus the `Theme` type `Rail.tsx` needs. It is not constants, and there is no MUI in this project. |

---

## Separate finding, not a merge

`web/src/phases.ts` exports `PHASE_ORDER`, consumed by `Studio.tsx` and
`NewTakePanel.tsx`. That is a **second copy of phase order**, the exact
duplication [avg/pipeline/registry.py](../../../avg/pipeline/registry.py)'s
docstring exists to forbid (the legacy app kept `$script:VAF_PHASES` and
`$PHASE_ORDER` in sync by hand, and they drifted). The frontend should read
it from an endpoint rather than restate it. Worth a task; out of scope here.

---

## Execution order

Dependency-independent, so ordered cheapest-to-riskiest:

1. `web/src/fields/` → `Fields.tsx`
2. `fonts.css` → `tokens.css`
3. `providers/voice/`
4. `providers/text/`
5. `providers/images/`
6. `media/environment.py`
7. `config/settings_import.py` → `settings.py`
8. `phases/_common.py`
9. `phases/writing.py`
10. `phases/director.py`

Per commit: create merged file with section headers → update imports in
`avg/` and `tests/` → delete originals → verify → commit.

**Module docstrings are the deliverable, not overhead.** Every file here
carries a docstring naming the legacy PowerShell function it replaces and
the defect it prevents. When merging, keep both docstrings under their
section headers; do not compress them into one summary.

---

## Verification

After every commit:

```bash
.venv-build/Scripts/python.exe -m pytest tests -q
```

```bash
.venv-build/Scripts/python.exe -m mypy
```

(The original plan's `python -m pytest tests/ -v` fails — the system
interpreter has none of the dependencies.)

Frontend commits additionally:

```bash
cd web && npm run build && npm run lint
```

**Import-order check** — the guard against exactly the cycles this plan
rejects. A cycle can hide when the test suite always imports the deep
modules first, so import each merged module standalone in a fresh
interpreter:

```bash
for m in avg.core.errors avg.config.settings avg.media.environment avg.providers.transport avg.pipeline.phase avg.pipeline.registry avg.api.app; do .venv-build/Scripts/python.exe -c "import $m" || echo "FAILED: $m"; done
```

Final manual pass: `tools/_dev_serve.py`, confirm the UI loads and a
pipeline run completes end to end.

---

## Recommendation

Worth saying plainly: this buys no functional improvement, rewrites
`git blame` across most of the package, and lands on top of the pending
restyle and Slice 6 work. Merges 10 and 11 (the field components, the font
faces) are clear wins on their own. The rest is taste. If file count is not
actually causing pain, doing 10 and 11 alone and stopping is a defensible
outcome.

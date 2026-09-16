# Slice 32 (sub-slice 2) — Visual & Motion Consistency: Design

**Source:** ports `AI_Video_Gen`'s B2.22 Part 2
(`spec_B2.22_part2_visual_motion_consistency.md`). Second priority, after
provider resilience (sub-slice 1) since a still-flaky provider layer would
make consistency work hard to even observe.

**Scope note:** this sub-slice ships seed-lock, Gemini reference-image
consistency, and safezone/structure-aware Director II now. Per-style
recycle pools — B2.22's fourth piece — are **deferred**, not built here:
they extend a base image-recycling mechanism that doesn't exist in AVG
Studio yet (Slice 31 sub-slice 2, spec+plan already written, not
implemented). Building a per-style variant of a feature that isn't there
would mean designing against a mechanism that could still change shape.
Once Slice 31 sub-slice 2 lands, its `channel`-scoped recycle pool is the
correct foundation for this — no new mechanism needed, just a style-keyed
subdivision of it (see §5).

**Where this sub-slice goes further than B2.22 did:** B2.22 Part 2 assumed
Director 2 already had access to per-scene busy-zone data and just needed
its prompt tightened. That's not true here — verified in code, AVG
Studio's Director II **never reads `video_map.json` or
`scene_safezones.json` at all**, despite both already being written
upstream by Director I and Visuals respectively. Wiring that connection is
a bigger, more valuable fix than a prompt-wording change, so it's the
larger of this sub-slice's three tasks.

## 1. Current state (verified in code, `development` @ `4d48ec8`)

- **Seed:** no image provider accepts a seed parameter, and no project
  field stores one. `avg.core.models.ProjectDoc` has no generic
  extras/metadata dict — every field is named and typed explicitly, so a
  seed needs its own field, not a bag to stuff it in.
  `PollinationsImageProvider.generate()` builds a query-string URL with no
  seed slot; `StableHordeImageProvider._submit()`'s `params` dict (`avg/
  providers/images/community.py`) is exactly where StableHorde's real
  `seed` field would go; `GeminiImageProvider`/`OpenAIImageProvider` have
  no seed concept in the request bodies they build (`avg/providers/images/
  premium.py`) — Gemini's `:generateContent`/`:predict` endpoints and
  OpenAI's image endpoint don't expose one, matching B2.22's own finding
  that seed-lock only applies to Pollinations/StableHorde.
- **Gemini reference-image consistency:** `GeminiImageProvider.generate()`
  has two paths — `_predict` (Imagen models, via `:predict`, an
  `instances`/`prompt`-only body with no image-conditioning slot in this
  shape) and `_generate_content` (native Gemini image models, via
  `:generateContent`, whose `contents[0].parts` list currently holds one
  `{"text": prompt}` part). Only the second path can take an `inline_data`
  image part alongside the text part — reference-image support is
  therefore scoped to non-Imagen Gemini models only, silently skipped
  (still generates from text) for Imagen.
- **Director II is disconnected from data that already exists:**
  `avg/pipeline/phases/director.py`'s `Director2Phase.run` calls
  `build_director2_prompt(inputs, scene_texts)` — `scene_texts` are the
  raw scene strings, nothing else. `avg/pipeline/prompts.py`'s
  `build_director2_prompt` has no parameter for role/beat/visual_intent or
  safezone data at all. Meanwhile:
  - `avg/pipeline/phases/director.py`'s **Director I** already writes
    `script/video_map.json` (`{"version": 1, "image_count": N, "parts":
    [{"scene": i, "role": "hook"|"payoff"|..., "beat": "HIT"|"PAUSE"|
    "ZOOM"|"SLOWDOWN"|"none", "visual_intent": "CLOSEUP"|"WIDE"|
    "BACKGROUND"|..., ...}]}`) and only ever reads it back inside itself,
    to build `timeline.json` — nothing downstream touches it.
  - `avg/pipeline/phases/visuals.py`'s `_write_safezones` already writes
    `render/scene_safezones.json` (`[{"scene": i, "safe_zone": <zone
    name>, "busy_zone": <zone name>}, ...]`, produced by the vendored
    `cmd_safezones` — see that function's docstring, `avg/media/vendor/
    avg.py:221`) specifically so a downstream phase has it "without
    needing Render to happen first." Director II runs *after* Visuals in
    `REGISTRY` order, so this file is guaranteed to exist by the time
    Director II runs, and nothing reads it.
  This means Director II currently plans motion **blind to which scene is
  the hook, which beat it's supposed to hit, what shot type Director I
  already called for it, and where in the frame the generated image is
  actually busy** — all four already computed, all four thrown away.
- Reference for `channels.py` already existing as the right home for
  per-video/per-channel creative state: `avg/config/channels.py`'s own
  docstring frames it as the deliberate replacement for legacy's
  `channel_profiles` copy-on-write mechanism, which caused three real bugs
  (cited in that module). Anything B2.22 ties to a "channel profile"
  belongs on `ctx.channel`/`ctx.channel_values` here — never a new
  parallel concept.

## 2. Decisions

1. **Seed is per-project, not per-channel.** A seed governs visual
   consistency *within one video's* generated images — the same reasoning
   B2.22 gave for legacy. New field on `avg.core.models.ProjectDoc`, and
   the same field on `avg.core.models.Take` — a take re-run
   (`avg/api/routes_takes.py`'s `create_take`) hands `VisualsPhase.run` a
   `model_copy` ("shadow_project": `folder`/`phases`/`master_output`
   swapped to the new take's own), never the real project object
   `on_phase_change`'s closure saves, exactly the reason `Take` already
   has its own `master_output` field and `routes_takes.py`'s worker
   copies `shadow_project.master_output` onto `new_take.master_output` by
   hand after the run finishes. `visual_seed` needs the identical
   treatment — see §3.A.
   `visual_seed: int | None = None`. `None` (the default for every
   existing and new project until Visuals first runs) means "not yet
   assigned"; Pydantic's default handles old projects loading with no
   migration needed, matching how every other optional field on that model
   already behaves.
2. **Assigned once, on first Visuals run for a take, then reused for every
   scene and every re-run of that take** — locks the *video's* look, not
   just one image. A settings toggle controls whether seed-lock applies at
   all (default **on** — matches B2.22's own default; a user who wants
   maximum per-scene variety can turn it off).
3. **Only Pollinations and StableHorde accept it** — the two providers
   whose APIs expose a real seed parameter. Gemini/OpenAI/HuggingFace
   silently ignore the project's seed; this is not a gap to work around,
   it's simply how those APIs work (matches B2.22's own scoping).
4. **Gemini reference-image consistency is opt-in, not automatic** — a
   settings toggle, off by default. Sending a full-resolution reference
   image on every subsequent call is real bandwidth/latency/cost per
   image; B2.22 made the same call for legacy for the same reason. When
   on, the *first successfully generated scene image of a take* becomes
   the reference sent alongside every later scene's prompt on Gemini calls
   only, for that take.
5. **Director II gets fed everything already computed for it** — role,
   beat, visual_intent per scene (from `video_map.json`) and safe/busy
   zone per scene (from `scene_safezones.json`) — folded into the existing
   prompt-construction call, not a new phase and not a new file. Both
   inputs are read defensively: a missing or malformed file degrades to
   today's behavior (empty context for that scene) rather than failing the
   phase, matching `_write_safezones`'s own "non-fatal in every direction"
   philosophy.
6. **Per-style recycle pools deferred** (see scope note above).

## 3. Design

### A. Seed

- `avg.core.models.ProjectDoc` gains `visual_seed: int | None = None`.
  `avg.core.models.Take` gains the identical field, for the shadow-copy
  reason above — `avg/api/routes_takes.py`'s `_worker`, in its `finally`
  block, gains one line right beside its existing `new_take.master_output
  = shadow_project.master_output`: `new_take.visual_seed = shadow_project.
  visual_seed`.
- `avg/pipeline/phases/visuals.py`: at the start of a Visuals run, if
  `ctx.channel_values["visuals_seed_lock"]` (new setting, default `True`)
  is on and `ctx.project.visual_seed` is `None`, generate one
  (`random.randint(0, 2**31 - 1)` — Pollinations/StableHorde both accept
  32-bit-range integers) and assign it directly to `ctx.project.
  visual_seed` — **no direct `save_project` call**. No phase in this
  codebase calls `avg.pipeline.create.save_project` itself
  (`avg/pipeline/runner.py`'s own docstring: `on_phase_change` is a
  callback specifically so the runner, not a phase, decides when and
  *which* project object gets persisted — a take re-run hands phases a
  throwaway `model_copy`, "shadow_project", not the real object
  `on_phase_change`'s closure actually saves). The mutation reaches disk
  through the exact mechanism every other phase already relies on for its
  own side effects: the runner's `on_phase_change` fires unconditionally
  when a phase finishes, success or failure, reading `ctx.project`'s
  *live* state at that moment — the seed assignment, made synchronously
  near the top of `run()`, is already covered by that same guarantee, not
  a new one. The one real gap this leaves: a true process-level crash
  mid-phase (not a caught, raised `AvgError`) loses the seed exactly as it
  would lose any other artifact that phase was mid-way through writing —
  an accepted, pre-existing risk class in this codebase, not a new one
  seed-lock introduces.
- `PollinationsImageProvider.generate()` and `StableHordeImageProvider.
  generate()`/`_submit()` each gain an optional `seed: int | None = None`
  keyword parameter, appended to the URL query string (Pollinations) or
  `params["seed"]` (StableHorde) only when not `None` — omitted entirely
  when seed-lock is off, which is exactly today's behavior (no seed
  param sent).
- `avg/pipeline/phases/visuals.py`'s per-scene generation call site passes
  `ctx.project.visual_seed` through to whichever provider is selected,
  when that provider is one of the two that accepts it — a small
  provider-name check at the call site (`provider.name in
  {"pollinations", "stablehorde"}`), not a protocol change, since every
  other provider's `generate()` signature stays exactly as it is.

### B. Gemini reference-image consistency

- New setting `visuals_gemini_reference_lock` (`bool`, default `False`,
  group `"images"`, `invalidates="visuals"`).
- `avg/pipeline/phases/visuals.py`: when the setting is on and the
  selected provider is Gemini, the phase keeps the raw bytes of the first
  successfully generated scene image in memory for the rest of that take's
  run (not written anywhere new — it's already on disk as that scene's own
  output file; kept in memory purely to avoid a re-read per subsequent
  call).
- `GeminiImageProvider.generate()` gains an optional `reference_image:
  bytes | None = None` keyword parameter. When present and the resolved
  path is `_generate_content` (never `_predict`/Imagen — see §1), an
  `inline_data` part (`{"inline_data": {"mime_type": "image/jpeg", "data":
  <base64>}}`) is prepended to `contents[0]["parts"]` ahead of the text
  part, matching Gemini's documented image-plus-text multimodal input
  shape. When the resolved model is an Imagen model, the parameter is
  accepted but ignored (silent, matching decision #5's "degrade, don't
  fail" pattern) rather than raising — a user who enables the toggle
  while routed to an Imagen model should not get a phase failure over it.

### C. Director II: wire in what already exists

- `avg/pipeline/phases/director.py`'s `Director2Phase.run` reads
  `script/video_map.json` (already on disk — Director I runs first in
  `REGISTRY` order) and `render/scene_safezones.json` (already on disk —
  Visuals runs before Director II) alongside the `scenes.json` it already
  reads. Both reads are wrapped exactly like `_write_safezones`'s own
  pattern: missing file or malformed JSON logs via
  `avg.core.errors.report_degradation` and proceeds with an empty
  per-scene context for whichever scenes lack data, never a phase failure.
- `avg/pipeline/prompts.py`'s `build_director2_prompt` gains a third
  parameter, `scene_context: dict[int, dict[str, str]]` — keyed by scene
  number, each entry merging that scene's `role`/`beat`/`visual_intent`
  (from `video_map.json`) with `safe_zone`/`busy_zone` (from
  `scene_safezones.json`) when present. The prompt template gets one new
  per-scene line, e.g. `"Scene 4 [hook, beat=HIT, intent=CLOSEUP, busy
  zone=lower-third]: <scene text>"` — the model is told what Director I
  already decided this scene needs to *do* and where the image is already
  visually busy, so it doesn't plan a caption-heavy motion move into a
  zone that's already full, or a slow drift on a scene marked for a hard
  `HIT` beat.
- No change to `Director2Phase`'s output shape (`direction_plan`'s
  existing fields) — this only changes what informs the values already
  written, not what's written.

## 4. Config additions

| Setting id | Group | Type | Default | `invalidates` |
|---|---|---|---|---|
| `visuals_seed_lock` | `images` | `bool` | `True` | `visuals` |
| `visuals_gemini_reference_lock` | `images` | `bool` | `False` | `visuals` |

Both `scope="channel"` — a creative choice per channel, exactly like every
other `group="images"` setting already in `settings_schema.py` (e.g. the
existing image-model/style settings), resolved through
`resolve_channel_settings` like everything else; no new resolution path.

## 5. Data/schema summary

| File | Change |
|---|---|
| `avg/core/models.py` | `ProjectDoc` and `Take` both +`visual_seed: int \| None = None` |
| `avg/api/routes_takes.py` | `_worker`'s `finally` block: +1 line copying `shadow_project.visual_seed` onto `new_take.visual_seed`, beside the existing `master_output` copy |
| `avg/providers/images/community.py` | `PollinationsImageProvider.generate()`, `StableHordeImageProvider.generate()`/`_submit()`: +optional `seed` param |
| `avg/providers/images/premium.py` | `GeminiImageProvider.generate()`: +optional `reference_image: bytes \| None` param, wired only on the `_generate_content` path |
| `avg/pipeline/phases/visuals.py` | seed assignment/persistence at phase start; reference-image capture-and-reuse when enabled; per-call wiring of both into the selected provider |
| `avg/pipeline/phases/director.py` | `Director2Phase.run`: reads `video_map.json` + `scene_safezones.json`, defensively |
| `avg/pipeline/prompts.py` | `build_director2_prompt`: +`scene_context` param, +one prompt line per scene when context is present |
| `avg/core/settings_schema.py` | +2 `SettingSpec`s (`visuals_seed_lock`, `visuals_gemini_reference_lock`) |

## 6. Testing

Seed: assigned once and persisted (a second Visuals call on the same take
sees the same `ctx.project.visual_seed`, not a fresh one); passed to
Pollinations/StableHorde requests, absent from Gemini/OpenAI/HuggingFace
requests; absent entirely from every request when the setting is off — all
via recorded fixtures (`AVG_HTTP_RECORD`-gated, no live call), asserting on
the request body/query string the fixture-driven `Transport` receives.
Reference-image: `_generate_content` body includes the `inline_data` part
only on non-first-scene calls when enabled; `_predict`/Imagen path
unaffected either way. Director II: a `video_map.json`+`scene_safezones.json`
fixture pair produces the expected `scene_context` dict; a missing or
malformed one of either file produces an empty context for those scenes
and the phase still completes (mirrors the existing "safezone analysis was
skipped" degradation test already covering `_write_safezones`).

## 7. Out of scope

- Per-style recycle pools (deferred — see scope note).
- Composite multi-scene grids — dropped from the project entirely
  (2026-09-02 decision, not part of this sub-slice or any future one).
- Seed support for any provider whose API doesn't expose one
  (Gemini/OpenAI/HuggingFace) — not a gap, just what those APIs are.
- Sub-slices 1, 3, 4 — separate documents.

# Slice 32 (sub-slice 3) — Captions: TTS-Native Word Timing: Design

**Source:** ports `AI_Video_Gen`'s B2.22 Part 3
(`spec_B2.22_part3_captions_rearchitecture.md`). Third priority — depends on
nothing from sub-slices 1-2, but ranked after them because it's the
sub-slice most worth getting exactly right, and resilience/consistency
landing first means fewer moving parts while this one is reviewed.

**Bigger win here than it was in legacy.** B2.22 replaced AssemblyAI STT
with TTS-native timing as an *upgrade* — legacy already fell back to STT
when no key was configured, which is itself a real transcription, just a
paid/rate-limited one. AVG Studio's no-key fallback
(`avg/pipeline/phases/captions.py`'s `_synthetic_word_timings`) is a
**proportional character-count guess**, not a transcription at all — it
has never listened to the audio. Most users won't have an AssemblyAI key
(free tier only), so today's *default* captioning experience is the guess.
TTS-native timing is free (it rides the TTS call already being made),
always available, and — because it comes from the engine that actually
spoke the words — more reliably in-sync with the real audio than a
transcription's own recognition of it could be. This sub-slice changes
what most users' captions actually run on.

## 1. Current state (verified in code, `development` @ `4d48ec8`)

- `avg/pipeline/phases/captions.py`'s `_get_words` tries AssemblyAI only if
  a key exists, and on **any** failure (missing key, network error, empty
  result) falls through to `_synthetic_word_timings` — proportional
  allocation of each scene's window (`_scene_windows`, itself proportional
  by character count) then each scene's own words within that window
  (`allocate_proportional` again). Two layers of guessing, not one.
- `avg/pipeline/phases/voice.py` synthesizes per **pause-segment**
  (`_split_on_pause`, text between `[pause]` markers — which do not
  necessarily align to scene boundaries; one segment may span several
  scenes or end mid-scene), not per scene. Each segment goes through
  `provider.synthesize(...) -> bytes` independently, gets written to a
  temp `raw_NNN.mp3`, assembled with exact-length silence gaps
  (`_assemble_with_gaps`) into `raw_voice.mp3`, then **each raw segment is
  cleaned independently** (`_clean_segment`, calling the vendored
  `remove_silences_core` once per segment — never on the whole assembled
  file) before final reassembly into `optimized_voice.mp3`. Any
  word-timing capture has to slot into this exact per-segment shape, not a
  single-file shape.
- `remove_silences_core` (`avg/media/vendor/avg.py:642`, protected — "never
  edit" per `CLAUDE.md`) computes `remap_points` internally (original-ms →
  post-strip-ms checkpoints) but **does not return them**. What it *does*
  do, already, is rewrite an arbitrary `{"scenes": [{"start": s, "end": e,
  ...}, ...]}` JSON file's `start`/`end`/`duration` fields in place through
  those same `remap_points`, when a `scene_map` path is passed
  (`_remap_scene_audio_map`, called from inside `remove_silences_core`
  itself when `scene_map and os.path.exists(scene_map)`). That function
  doesn't care what the spans semantically represent — it only reads
  `data["scenes"]`, each entry's `start`/`end` in seconds — so it is, in
  effect, a general-purpose "remap a list of (start, end) spans through
  this cleaning pass" utility, not scene-specific in what it actually does.
  `avg/pipeline/phases/voice.py`'s `_clean_segment` currently calls
  `remove_silences_core(..., "", "", "")` — `beat_map`/`timeline`/
  `scene_map` all empty, so this remap path never fires today.
- `EdgeVoiceProvider.synthesize()` (`avg/providers/voice/providers.py`)
  already iterates `edge_tts.Communicate(...).stream_sync()` chunk-by-chunk,
  filtering `chunk.get("type") == "audio"`. `edge-tts` also yields
  `type == "WordBoundary"` chunks on the same stream (`offset`/`duration`
  in 100-nanosecond ticks, `text`) — already flowing past this exact loop,
  currently discarded.
- `ElevenLabsVoiceProvider._call()` posts to
  `/v1/text-to-speech/{voice_id}` and returns raw audio bytes only.
  ElevenLabs' own `/v1/text-to-speech/{voice_id}/with-timestamps` endpoint
  (same request body) returns `{"audio_base64": ..., "alignment": {
  "characters": [...], "character_start_times_seconds": [...],
  "character_end_times_seconds": [...]}}` — character-level, needs grouping
  into words by whitespace boundary (same grouping legacy's own B2.22 Part
  3 plan worked out for its equivalent call).
- `avg.providers.voice.base.VoiceProvider` is a `Protocol` exposing only
  `name`, `synthesize`, `list_voices` — narrow by design (that module's own
  docstring: "What every provider *does* share... is this Protocol").
  Adding a required method would force every implementer (including any
  future one) to have it; this sub-slice adds an *optional* one instead
  (checked with `hasattr`, the same structural-typing spirit the module
  already uses).

## 2. Decisions

1. **New optional provider capability, not a changed one.** Both
   `ElevenLabsVoiceProvider` and `EdgeVoiceProvider` gain
   `synthesize_with_timing(text, *, voice_id, model) -> tuple[bytes,
   list[dict] | None]` — same audio `synthesize()` would have returned,
   plus a per-word `{"text", "start", "end"}` (seconds, relative to that
   segment's own pre-clean timeline) list, or `None` if this specific call
   couldn't produce one. `synthesize()` itself is untouched — existing
   callers, existing tests, unaffected. `voice.py` calls
   `synthesize_with_timing` when the selected provider has it
   (`hasattr(provider, "synthesize_with_timing")`), else falls back to
   plain `synthesize()`, exactly today's call.
2. **Whole-run, not partial.** If `synthesize_with_timing` fails or is
   unavailable for *any* segment in the run, the phase does not write
   `audio/word_timings.json` at all for this run — captions.py's existing
   AssemblyAI/estimated fallback handles the whole track instead. A file
   that's word-timed for some segments and silently estimated for others
   would be a worse, harder-to-notice failure mode than falling all the
   way back once.
3. **Remap word timings through the vendored engine's own mechanism, not a
   reimplementation.** See §3.B — passing the captured word list as a
   throwaway `{"scenes": [...]}` file to `remove_silences_core`'s existing
   `scene_map` parameter reuses the exact, already-correct remap path
   `_remap_scene_audio_map` provides, with **zero changes to
   `avg/media/vendor/avg.py`**. This was chosen over independently
   recomputing `detect_nonsilent` to reconstruct `remap_points` by hand —
   that would work, but would be a second implementation of a checkpoint
   list the vendored function already computes once, correctly, and is
   provably guaranteed to match exactly by construction rather than by two
   pieces of code happening to agree.
4. **TTS-native becomes the new default priority, ahead of AssemblyAI.**
   `captions_timing_source` (new setting, default `"auto"`): tts-native
   word timing when available, else AssemblyAI when a key exists, else the
   proportional estimate — same three-deep fallback shape captions.py
   already has, re-ordered. `"assemblyai"` and `"estimated"` force one path
   explicitly (a user who has reasons to prefer AssemblyAI's own speech
   recognition — e.g. it may handle mispronunciations or filler words
   differently from what the TTS engine claims it said — isn't forced off
   it). AssemblyAI is not removed; it's no longer the thing most users
   silently depend on by default.
5. **No STT upload happens at all when native timing is used** — the
   `audio.read_bytes()` + AssemblyAI upload path in `_get_words` is
   entirely skipped, not just deprioritized, when
   `audio/word_timings.json` is present and the source resolves to
   `"tts_native"`. Saves the network round-trip and, for a user who *does*
   have an AssemblyAI key, saves the transcription cost outright on the
   common path.

## 3. Design

### A. Capturing timing at synthesis

- **Edge TTS:** `EdgeVoiceProvider.synthesize_with_timing` runs the same
  `_collect()` shape as `synthesize()` (same `ThreadPoolExecutor(1)` +
  timeout wrapper — no change to that safety net), but the inner loop also
  appends `type == "WordBoundary"` chunks to a second list, converting
  `offset`/`duration` (100-ns ticks) to seconds (`/ 1e7`) and building
  `{"text": chunk["text"], "start": offset_s, "end": offset_s +
  duration_s}` per word. Returns `(audio_bytes, words)` — `words` is never
  `None` here (Edge always includes boundary events on its stream); an
  empty list only if the segment was somehow empty, which `_split_on_pause`
  already prevents upstream.
- **ElevenLabs:** `_call` gains a `with_timestamps: bool = False` keyword;
  when `True`, posts to `/v1/text-to-speech/{voice_id}/with-timestamps`
  instead of the plain endpoint (identical `json` body — the endpoint
  differs, not the request), and parses `{"audio_base64", "alignment"}`
  instead of returning `response.content` directly. A new
  `_words_from_alignment(alignment) -> list[dict]` groups
  `alignment["characters"]` into words at whitespace boundaries using the
  parallel `character_start_times_seconds`/`character_end_times_seconds`
  arrays (a word's `start` is its first character's start, `end` is its
  last non-space character's end) — pure function, easy to test in
  isolation against a small fixed alignment fixture. `synthesize_with_timing`
  calls `_call(..., with_timestamps=True)` and reuses the **exact same**
  primary/backup key-switch `try`/`except AvgError` block `synthesize`
  already has (refactored so both methods share it, rather than
  duplicating the retry rule the module's own docstring is emphatic about
  getting right exactly once).

### B. Remapping through the pause-segment cleaning pass

- `avg/pipeline/phases/voice.py`'s main synthesis loop, when the selected
  provider has `synthesize_with_timing`, calls it instead of `synthesize`
  for every segment, keeping each segment's raw (pre-clean) word list
  alongside its raw audio bytes. If any segment's call raises or returns
  `words=None`, the whole run drops to plain `synthesize()` for every
  segment from that point (decision #2 — whole-run, not partial; the loop
  restarts synthesis-only, no double-billing a provider for audio already
  successfully generated is avoided by checking word-timing capability
  once, up front, via a one-segment capability probe... **actually simpler
  and avoids any probe call**: `synthesize_with_timing` is attempted for
  every segment from the start since providers don't fail differently by
  segment content in practice; a failure on segment N discards the
  word-list for segments before N too and the run proceeds using the audio
  bytes already generated with `synthesize()`-shaped calls it would need to
  redo only if it hadn't kept the bytes — the *implementation* keeps
  `synthesize_with_timing`'s returned bytes regardless of whether the
  timing half succeeded, so a mid-run drop costs nothing but the timing
  data, never re-synthesis).
- `_clean_segment` gains an optional `word_timings: list[dict] | None`
  parameter. When present, before each `remove_silences_core` call (both
  the primary-aggressiveness call and the one retry at a lower level —
  each independently, from the *original* captured word list, never
  chained through a prior remap — see decision #3's "provably correct by
  construction" point, which only holds if each call starts from the
  untouched original), the word list is written to a temp file shaped
  exactly as `_remap_scene_audio_map` expects:
  `{"scenes": [{"start": w["start"], "end": w["end"], "text": w["text"]},
  ...]}`. That temp path is passed as `remove_silences_core`'s `scene_map`
  argument. After the call, the same temp file — now rewritten in place by
  `_remap_scene_audio_map` with post-clean `start`/`end` — is read back;
  its (still-present) `text` field pairs each remapped span back to its
  word. Whichever of the (up to two) calls' result is ultimately kept
  (matching `_clean_segment`'s existing "keep the retried result" rule) is
  the one whose remapped file is kept.
- After all segments are cleaned, `voice.py`'s existing
  `_assemble_with_gaps(cleaned_paths, gap_ms, .../"optimized_voice.mp3")`
  call is unchanged — but immediately before it, each segment's remapped
  (post-clean, still segment-local) word list is offset by the **cumulative
  duration of every prior cleaned segment plus every prior gap already
  accounted for** (`audio_duration_seconds(clean_path)` for each earlier
  segment, `+ gap_ms / 1000` between them — the identical arithmetic
  `_assemble_with_gaps` itself performs when it concatenates, read rather
  than re-derived by a second formula) to produce one flat list spanning
  the final `optimized_voice.mp3` timeline. Written to
  `audio/word_timings.json` as `{"words": [{"text", "start", "end"},
  ...]}`.

### C. Consuming it in Captions

- `avg/pipeline/phases/captions.py`'s `_get_words` gains a first check,
  ahead of the AssemblyAI branch: if `audio/word_timings.json` exists,
  is non-empty, and `ctx.channel_values.get("captions_timing_source",
  "auto")` is `"auto"` or `"tts_native"`, return `(words, "tts_native")`
  directly — no AssemblyAI upload attempted at all (decision #5).
  `_get_words`'s return type widens from `tuple[list[dict], bool]`
  (`used_assemblyai`) to `tuple[list[dict], str]` (a `source` string:
  `"tts_native"` / `"assemblyai"` / `"estimated"`) — the one call site in
  `ScreenTextPhase.run` that currently branches on the bool
  (`provider = "assemblyai" if used_assemblyai else "estimated"`, and the
  `source = "AssemblyAI" if ... else "estimated timing"` progress message)
  updates to a direct 3-way match on the new string, which is *also*
  exactly the string `seed_transcript_cache`'s own `provider` parameter and
  `render/_transcript_source.json` already expect — no shape change to
  either of those, just a third real value flowing into the same field
  that already accepted arbitrary provider-name strings.
  `captions_timing_source == "assemblyai"` skips the native-timing check
  even when the file exists (decision #4's explicit-override case);
  `"estimated"` skips both and goes straight to `_synthetic_word_timings`.

## 4. Config additions

| Setting id | Group | Type | Default | `invalidates` |
|---|---|---|---|---|
| `captions_timing_source` | `captions` | `select` (`auto`/`tts_native`/`assemblyai`/`estimated`) | `auto` | `screen_text` |

`scope="channel"`, matching every existing `group="captions"` setting.

## 5. Data/schema summary

| File | Change |
|---|---|
| `avg/providers/voice/base.py` | Docstring note only — the new method is optional, not added to the `Protocol` class itself (structural typing already permits extra methods; `hasattr` is the check, not `isinstance` against a widened Protocol) |
| `avg/providers/voice/providers.py` | `EdgeVoiceProvider.synthesize_with_timing` (new); `ElevenLabsVoiceProvider._call` gains `with_timestamps`; `.synthesize_with_timing` (new, shares the key-switch block with `.synthesize`); `_words_from_alignment` (new, pure) |
| `avg/pipeline/phases/voice.py` | Synthesis loop tries `synthesize_with_timing` when available; `_clean_segment` +`word_timings` param, scene_map-remap-file round trip; new offset-accumulation step before final assembly; writes `audio/word_timings.json` |
| `avg/pipeline/phases/captions.py` | `_get_words` return type widens to a 3-way `source` string; new first-check branch for `audio/word_timings.json`; `ScreenTextPhase.run`'s provider/source branching updates from bool to string match |
| `avg/core/settings_schema.py` | +1 `SettingSpec` (`captions_timing_source`) |
| `audio/word_timings.json` | New per-project artifact (not a config file — lives alongside `raw_voice.mp3`/`optimized_voice.mp3`, same take-scoped lifecycle, already covered by `avg.core.takes.PHASE_ARTIFACTS`'s voice-phase entry once added there) |

## 6. Testing

`_words_from_alignment`: a small fixed character-alignment fixture →
expected word list, including a multi-space and a leading/trailing-space
edge case. Edge TTS: a fake `stream_sync()` yielding both `audio` and
`WordBoundary` chunk types → both the audio bytes and the word list are
correct, ticks-to-seconds conversion exact. ElevenLabs: fixture-driven
`with-timestamps` response → correct word list; the existing
primary/backup key-switch tests re-run unchanged against
`synthesize_with_timing` to confirm the shared retry block still behaves
identically. Remap round-trip: a synthetic raw segment with known silence
gaps + a known word list → after `_clean_segment`, each word's remapped
`start`/`end` falls inside the expected post-clean span (this is the one
new integration-style test worth being careful with, since it's exercising
the real vendored `remove_silences_core` against a real short audio
fixture, not a mock). Offset-accumulation: three segments of known cleaned
duration + known gap → the flat `word_timings.json` matches hand-computed
expected offsets. Captions: `_get_words` returns `tts_native` when the file
is present and source is `auto`/`tts_native`; falls through to AssemblyAI
when source is forced or the file is absent; falls through to estimated
when both are unavailable — extends the existing degradation tests rather
than replacing them, since AssemblyAI's and the estimate's own behavior
must not change.

## 7. Out of scope

- Removing AssemblyAI support — stays available as an explicit override.
- Any change to how cues are grouped (`_group_cues`'s fixed 6-word
  chunking) or how takeover cards suppress captions — this sub-slice only
  changes where the underlying word timestamps come from.
- HuggingFace or any future voice provider without a timing-capable
  endpoint — falls back to plain `synthesize()` automatically via the
  `hasattr` check; no work needed for that case to degrade correctly.
- Sub-slices 1, 2, 4 — separate documents.

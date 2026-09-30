# Slice 32 - errata found while implementing on `linux-port`

The Slice 32 specs/plans in this folder were written against `development`
and verified mechanically against a scratch copy. Implementing them on the real
`linux-port` tree found these. **Where a plan and this file disagree, this file
wins.**

| Plan | Error | Consequence if followed literally | Resolution |
|---|---|---|---|
| visual-motion-consistency, Task 5 | reads `video_map["parts"]` for per-scene role/beat/visual_intent | `parts` holds chapter takeover cards on this branch; per-scene data is in `scenes[]`. Director II would silently see no context | reader uses `scenes`; test pinned to `_normalize_scene_entry`'s real output |
| visual-motion-consistency, Task 5 | fixtures use `visual_intent` `CLOSEUP`/`WIDE` | not in the vocabulary (`BACKGROUND`/`ASSET`/`POP`); normalised away. Fixtures masked the `parts` bug | real vocabulary |
| visual-motion-consistency, Task 4 | import replace-block assumes `import ctypes` | anchor fails on this branch | imports merged by hand |
| visual-motion-consistency, Task 4 | existing tests fake `_generate_one` with 6 args | `TypeError` | fakes accept optional `extra_kwargs` |
| captions-native-timing | `audio/word_timings.json` not in Voice's `PHASE_ARTIFACTS` | changing `captions_timing_source` invalidates `screen_text` but keeps `voice`; the clone would re-run Captions without native timing and silently fall back | added to `avg/core/takes.py`, tested |
| captions-native-timing, Task 3 | provider narrowing via `hasattr` | new mypy error | `TimedVoiceProvider` runtime-checkable protocol |
| provider-resilience (design, not just plan) | cooldown keyed by provider name | a quota failure on ElevenLabs' **primary** key blocked its **backup** key - the pre-existing key-switch silently stopped working. No test exercised it | cooldown scoped to the failing credential (`fingerprint_headers`); regression + deep-check test |
| every plan | "SETTINGS grows N -> N+2" | tripwire count differs per branch | bump `tests/core/test_invalidation.py` by the number actually added |

Also: Task 1 of captions-native-timing alone leaves `test_settings_bindings`
red (its reader lands in Task 4); do not stop between them.

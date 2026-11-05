# Remaining updates

What is left to build, in order. Written November 1, 2026 (branch `iOS-native`). The two plans this comes from are
`DIRECTORS_CUT_PLAN.md` and `SETTINGS_PLAN.md`; read the relevant section there before starting an item.

## Already done (built, CI green)

- **M1** models and onboarding: AI team presets in Welcome, automatic model check on key save, newest-working-model fallback.
- **M2** sound: music library (upload, generate with Lyria, automatic pick), sound effects with a moments map and starter pack, mixer with ducking.
- **M3** edit gate ("Edit it yourself?" card), the Editor as its own tab (Studio now opens from the Library), live preview, caption/title-card/music editing.
- **M4.1 / M4.2** shot cuts and punch-ins, Smart crops (one vision request finds each picture's subject).
- **M5** the editing assistant (chat inside the Editor, edits this one video only).
- **S1** Settings → Editing (gate length, new-channel defaults, preview quality).

## Next, in order

1. **S2 — per-video overrides** (`SETTINGS_PLAN.md` 3.4). Add `musicGainDB`, `sfxGainDB`, `editLevel` to `ProjectEdits`; an `EditResolution` helper (video beats channel) used by `Pipeline`, the preview and the editor; level sliders and a cuts picker in the editor; the assistant gets `level_db` on `set_music` / `set_sound_effects` and a new `set_cuts` tool; the preview must re-plan when edits change. *Started in a working copy and not pushed: build it from this description.*
2. **S3 — Settings → AI team page** (3.1): preset picker, `autoPickModels` toggle, "Check my models now", "Undo automatic picks".
3. **S4 — search and Quick settings** (3.6): standalone entries for Music, Sound effects and the AI team; the gate picker in Quick settings; an Activity line when a model is changed automatically.
4. **S5 — audio shelf housekeeping** (3.3): storage line and "Remove unused".
5. **S6 — docs**: README and CLAUDE.md rows for the new settings.

## Then, from the Director's Cut plan

6. **M4.3 — "Thorough" edit level**: a draft-video critique with Gemini video input (budgeted, skipped without quota), and optional hero shots with Veo (paid, off by default).
7. **M6 — Playground and the human layer**: Style Lab, Hook Bench, storyboard-first, rehearsal render, response cache, token meter, recipes; Director's Brief on Create, review gates, record-your-own-voice, import your own footage and photos.
8. **Publish kit** (later, not planned in detail): title options, thumbnail, description, chapters, AI-disclosure checklist.

## Not yet verified on a device

CI only proves the code compiles and its logic tests pass. Check by eye and ear: the editor preview, the music level (-20 dB under the voice is a guess), whether close-up crops aim at the right part of a picture, the 10-second card, Lyria and ElevenLabs generation with real keys (`lyria-3.5` is a reported model id; confirm from the live model list).

## Money and dates to remember

Gemini 3.8 Flash and Gemini TTS intro prices end **December 31, 2026** (they roughly double). Lyria and Veo have no free tier.

## Housekeeping that needs the owner's OK

- Delete the stray lowercase `ios-native` branch (the real one is `iOS-native`); then drop it from `.github/workflows/ios.yml`.
- Remove the four unrelated template workflows (`c-cpp`, `django`, `jekyll-docker`, `python-app`) and the committed `AVG_Studio-ios-native-reviewed.zip`.
- A few lines in README.md and CLAUDE.md are stale: `director1` and chapters are described as "not ported" but exist, and the test count is old.

## Rules that still apply

See `CLAUDE.md`: a new setting needs a default, a tolerant decoder line, an anchor and a search entry; sound is mixed before the renderer's mux; the edit gate holds only a video's first render; the app only changes a main model it owns; CI (macOS) is the only compiler.

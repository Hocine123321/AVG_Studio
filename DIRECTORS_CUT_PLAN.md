# AVG Studio — Director’s Cut: the next-update plan

> Written as a Claude doc on October 25–26, 2026, copied here so it lives with the code.
> What is done and what is next is tracked in `REMAINING_UPDATES.md`; this file is the plan as approved.

*Plan for the next AVG Studio update · October 25, 2026 · based on a full read of the iOS code and fresh research on Google, ElevenLabs and music-licensing terms. Anything marked “verify” should be checked on a device or on the vendor’s page before we build on it.*

## 1. The vision in one page

**One sentence:** AVG Studio stops being “topic in, finished video out” and becomes a *directed* studio: **AI drafts, you direct.** The AI still does the heavy lifting (script, images, voice, a first edit), but you can step in at every stage, and a real editor sits behind the draft so the final video carries your taste, not just a model’s.

**How I read your list**

- **“User-demanding” and “not generic”:** the app asks for the human input that makes a video distinctive (angle, opinion, facts, voice, footage) and offers review points instead of silently guessing. Your notes say “generic”; I read it as “*not* generic”.
- **AI-editing revamp + Editing page + Editing chatbot:** one shared editing engine with three front doors (the AI editor, your hands, the assistant) using the same vocabulary, the same undo and the same preview.
- **Music and sound effects:** today a video has narration only, so this is the biggest perceived-quality jump available.
- **Models:** a welcome-screen chooser, better defaults, automatic checks, a smarter fallback.
- **Playground:** cheap experimentation, so exploring doesn’t burn tokens.

**Pipeline after this update:** Create (with a Director’s Brief) → Script → *review* → Visuals → Voice → Captions → **AI Edit** (10-second “edit it yourself?” gate) → **Editor** (any time) → Render → Publish kit (later).

**“Premium” is our checklist for every feature**

1. Every AI action is visible, reversible and labelled “AI” or “You”.
2. Money is never spent without a price shown first.
3. No dead ends: every failure offers a fix.
4. Fast feedback: previews in seconds, not full renders.
5. Native feel: Liquid Glass, `Theme` tokens, one filled button per screen, Dynamic Type checked (the one open item in the README).

## 2. What Google and others offer now (checked October 2026)

- **Gemini 3.8 Flash** (`gemini-3.8-flash`, released Sept 2, 2026): takes text, image, video, audio and PDF; 1M-token context; works on the free tier with rate limits. Intro price $0.75 in / $3.75 out per 1M tokens through Dec 31, 2026, then $1.50 / $7.50. It can be both the editing **brain** and the editing **eyes**: it can watch a draft video.
- **Gemini 3.8 Flash TTS and Flash-Lite TTS** (preview, Oct 6, 2026): $0.50 per 1M text tokens in, $9 / $6 per 1M audio tokens out through Dec 31, doubling on Jan 1, 2027.
- **Lyria 3.5** (Sept 4, 2026, Gemini API): music generation at **$0.08 per full song** and **$0.04 per ~30 s clip**, text- or image-conditioned, single-turn, **no free tier**. Reported model id `lyria-3.5`; older ids `lyria-3-pro-preview` and `lyria-3-clip-preview` (verify from the live model list).
- **Veo 3.1** (Gemini API): image-to-video, **no free tier**. Lite $0.05/s, Fast $0.10/s, Standard $0.40/s at 720p; clips are short (about 8 s by default).
- **ElevenLabs sound effects:** listed around $0.12 per minute of audio (its own pages describe the billing three different ways, so verify). That is cents per effect. We already have an ElevenLabs key slot.
- **Freesound API:** CC0 / CC-BY sounds, previews downloadable without OAuth. Per an open-source client’s summary of the terms, the API is free for non-commercial use only, commercial use needs a separate agreement, and sounds need attribution (verify on Freesound).
- **Pixabay:** royalty-free, but its terms forbid distributing content on a standalone basis, and some tracks can still trigger YouTube Content ID claims.

**Strategy:** one **Google AI Studio key can power six roles**: writer, artist, narrator, composer, editor’s eyes and hero clips. That is the simplest onboarding and the most consistent code (header auth, one bill). We keep the provider protocols, live model lists and fallbacks anyway, because of vendor churn: preview ids change, intro prices end Dec 31, 2026, and Google now calls the Interactions API the main way to call Gemini 3.x while the app uses `generateContent` (verify it keeps working, then adapt behind the provider protocol).

## 3. Architecture: seven changes that make the rest possible

**3.1 EditList: new things live beside the old ones.** A new optional `EditList` on `Project` (and inside `TakeSnapshot`) holds only what is new: music cue, SFX cues, overlays and title cards, per-scene overrides (shot splits, motion, transition, punch-ins), caption overrides, and each item’s `origin` (AI or You). Old projects have none and render exactly as today (tolerant decoding, same pattern as `stepRecords`). Render = base project (scenes, captions, cards, directions) + EditList. “Revert to AI” = keep a copy of the AI’s EditList. This protects the ~490 tests and every saved project; we can unify the models later.

**3.2 Edit primitives registry.** One list of everything the renderer can do: `punchIn`, `shake`, `flash`, `splitShot`, `textOverlay`, `lowerThird`, `statCallout`, `titleCard`, `captionEmphasis`, `musicCue`, `sfxCue`, `duck`, and so on. Each has typed parameters with bounds, a flag for “spends money or not”, a one-line description, and one `FramePlan` implementation drawn by both the GPU and CPU paths (the existing rule). The same registry is the **schema the AI editor outputs, the source the Editor builds its controls from, and the tool set the assistant exposes.** This is the fix for last time’s failure: the AI could only pick among seven camera moves; now its vocabulary grows whenever the renderer’s does.

**3.3 Audio engine.** A new `Mixdown` step before the mux: voice + music + SFX become one `mix.m4a` (an `AVMutableComposition` with volume ramps, exported the way `narration.m4a` is today), then the existing passthrough video mux. Music **ducks** under speech using the speech spans Voice already records (`audioSegments`), lifts in pauses and chapter gaps, and fades in and out. A loudness pass targets roughly YouTube’s playback level (about −14 LUFS; verify). Ducking curves, SFX spacing caps and loudness gain are pure functions, so they are unit-tested.

**3.4 Preview.** Scrub = compose one frame with the same GPU/CPU path (instant, what you see is what you get). Play = a low-res draft proxy (for example 360p at 15 fps) rendered on demand and cached; later versions re-render only the changed time ranges. Measure with `RenderStats` on a real device before promising speed.

**3.5 Asset libraries.** A shared `AudioLibrary` index (Music, SFX) in Documents: file, kind, tags (mood, genre, energy 1–5, tempo, vocals or not), duration, source, **license and attribution**, cost, dates. Same pattern as the reusable-image pool.

**3.6 Eyes.** Gemini 3.8 Flash accepts images, video and audio, so the AI editor can *look* at a storyboard sheet and *watch* its own draft, then critique it. One pass on a 60 s draft should cost on the order of a cent or two (an estimate; measure it in the Playground).

**3.7 Dev loop.** CI is the only compiler, so: move pure logic (edit operations, ducking, SFX placement, model scoring, library index) into a Swift package so tests run in seconds without booting a simulator; add a CI **render harness** that renders a fixture project on the CPU path and uploads a contact sheet and an mp4 as artifacts, so we can *see* output without a phone; build every feature behind a flag and ship in small CI-green steps.

## 4. The workstreams

### A. Models: welcome chooser, smart defaults, auto-check, fallback

**Welcome (“Your AI team”).** Replace per-category digging with one step: five role cards (Writer, Artist, Narrator, Composer, Editor’s eyes), each with a **Free / Balanced / Premium** switch and “Customize”. Then keys, then a live check, then confirm. A banner says “one Google key covers all five”. Bundles are resolved from the live model lists, never hard-coded names: *Free* = Apple on-device writer and voice + Pollinations; *Balanced* = Gemini 3.8 Flash + Gemini image + Gemini TTS; *Premium* = adds ElevenLabs voice, Lyria music and optional Veo hero shots.

**Smart defaults.** Today’s defaults are the weakest stack: Apple’s on-device writer, the system voice, `gemini-2.5-flash` and a 2.5-era TTS id. After the check, the app picks the best *working* option per role and marks it “auto-picked”. A later re-check may re-pick auto-picked roles but never overrides something you chose.

**Auto-check, tiered by cost** (extends `ModelProbe`, which is text-only today):

1. *Free:* model lists, key validity, quota endpoints. Run automatically.
2. *Micro-cost* (under $0.001: a one-word text reply, a three-word TTS). Run automatically, a few at a time with backoff. A rate limit is “inconclusive”, never “failed” (already how the probe works).
3. *Real cost* (an image ≈ $0.04, a music clip $0.04, Veo $0.05+ per second): never automatic. A “Test it (about $0.04)” button shows the price.

Results become a scoreboard: works / fails and why / latency / last checked. Results go stale after 7 days and are re-verified on failure.

**Fallback.** Keep the invariant that “Stop” means stop. When you haven’t named a fallback, keep a `lastSuccess` log per role (provider, model, time, latency) next to the probe results. On failure try, in order: (1) the most recently working *other* model of the same provider; (2) the most recently working model of another provider you hold a key for; (3) the free on-device option. This replaces today’s hard-coded “light” presets (`gemini-2.5-flash-lite`, `llama-3.1-8b-instant`), which go stale. The switch stays per video (`RunOverrides`), is logged, and is reverted when the run ends.

**Also:** refresh default model ids and `CostTable` (add Lyria, Veo and SFX rates) with an “as of” date on every rate.

### B. Sound: music and sound effects

**Music sources: my recommendation.** Don’t scrape and bundle a catalogue. Pixabay’s terms forbid distributing its content on a standalone basis (an in-app catalogue is exactly that), Freesound’s API is non-commercial by default and wants attribution, scraping breaks site terms, and “royalty-free” tracks can still get Content ID claims. Instead, four **sources** feed one library:

1. **Generate once, reuse forever (primary): Lyria 3.5.** $0.08 per full song, $0.04 per clip. A starter pack of 10 moods × 1 full song ≈ $0.80, then free for every future video. Prompt for instrumental, low dynamic range, “sits under narration”. Tags come from the prompt, so tagging is free.
2. **Import** from Files (tracks you own or license). The app analyses it once with Gemini audio input for tags (cents).
3. **Add by link:** paste an audio URL; the app downloads it, asks for license and attribution, stores provenance and shows “you are responsible for the rights; Content ID may still claim it”.
4. **Browse CC0 (later, optional):** Freesound with your own API key, CC0 filter by default, attribution captured automatically, with the commercial-use warning.

**Music page** (Settings → Sound → Music): grid with mood chips; preview, rename, retag, delete; a **Generate a pack** wizard (pick moods, see the total price, confirm); storage used; license badges; sort by recently used.

**Selection modes:** *Auto* (AI picks by tags against the brief’s emotional arc, channel tone and story-map roles) · *Ask me* (three candidates with 5-second previews, auto-pick after 10 s) · *Fixed track per channel* · *None*. Also level (default about −20 dB under voice), ducking depth, fade lengths, and **“vary tracks across videos”** (no back-to-back repeats; YouTube’s policy targets samey, template-style uploads).

**Sound effects page:** library plus a **Moments map**. For each moment choose a default sound, “mine”, or off, with gain and a frequency cap. Moments: *impact* (story-map HIT), *whoosh* (slide and whip transitions), *riser* (before a reveal), *pop/ding* (a card or key-word highlight), *tick* (typewriter), *soft hit* (chapter card), *shutter/flash* (image punch), *breath* (PAUSE beat). Triggers come from data we already have: transition type, card start, story-map beats and word timings. The sound leads the picture by about 2–3 frames, with a minimum spacing and a per-10-second cap so it never turns into a meme edit. The default pack is generated once with ElevenLabs sound effects (cents) or taken from CC0 sounds; “add your own” works from Files or a link with the same license capture.

**Cost display:** the Create screen’s estimate shows “Music: reused (free)” or the generation price.

### C. The AI Edit phase (revamp)

Today Director II reads only text, chooses among seven moves, and runs inside Render. New: a dedicated phase `edit` between Captions and Render (Director II moves in, keeping its deterministic fallback), run as short passes:

1. **Storyboard sheet.** Per scene: thumbnail, text, duration, story role and beat, caption-emphasis candidates (from word timings), audio energy.
2. **Edit plan as structured JSON** (schema = the primitives registry). The model sees the sheet, including images, and returns: shot splits for long scenes (different crops and motions of the same picture, so nothing sits still for 6+ seconds), punch-ins on key words, transitions, overlay and callout suggestions, SFX cues, a music mood and energy curve, hero-shot requests, chapter titles. Unsupported or invalid items are dropped with a log line; the plan never fails the run.
3. **Deterministic polish (no AI).** Target shot lengths by format (a new visual every ~2–4 s for Shorts, longer for long-form; tunable), no same motion twice in a row, SFX spacing, loudness.
4. **Draft render + critique.** Render the proxy, send it to Gemini as video with a rubric: hook in the first 3 s, pacing, repeated visuals, caption readability, music masking narration, dead air. It returns timestamped issues; one revision pass (at most two loops).
5. **Optional hero shots.** For up to N scenes (the hook, the reveal), animate the still with Veo 3.1 Lite (image-to-video, $0.05/s, so about $0.40 per 8 s clip). Off by default, shown in the estimate.

**The 10-second gate.** When the phase starts, a Liquid Glass card appears on whatever screen you’re on: “AI editing starts in 10 s: **Edit it myself** / **Start now**”. No tap, and the AI proceeds. “Edit it myself” parks the project in the Editor with the deterministic base cut. A per-channel (or global) **Editing mode**: *Auto* (no prompt) · *Ask* (the 10 s gate; my default) · *Co-pilot* (AI edits, then opens the Editor to review) · *Manual*. If the app is in the background the gate resolves to the default so a queue never stalls. Add `PhaseStatus.skipped`, treated as done for ordering. The README’s “Add a phase” recipe applies: `PhaseID`, `Pipeline.execute`, StudioView rows, Welcome copy, Steps, `PhaseInputs`, Recovery, costs.

### D. The Editor page

Entry: an **Edit** button on Studio’s player card. Not a sixth tab: iPhone tab bars collapse past five. Full-screen, portrait.

- **Preview** on top (scrub = instant frame, Play = proxy), transport, safe-area guides.
- **Timeline** below: tracks for Scenes, Overlays, Captions, Voice (waveform from the recorded spans), Music, SFX; pinch to zoom. **The voice is the master clock**: scene cuts follow the first spoken word of each scene, so you nudge cuts between words rather than free-dragging; changing the script is a “Redo from Voice”.
- **Inspector sheet** per selection: scene (swap image: regenerate, library or Photos; split shot; motion; transition; hold), caption (fix text, style preset, per-word emphasis, position, animation), overlay (template, text, colours, in/out animation), music (track, level, ducking), SFX (sound, gain, nudge).
- **Add:** title card, lower third, stat callout, quote, keyword highlight, shape/arrow, emoji/sticker, watermark/outro. Templates only in v1 (a consistent premium look, GPU-friendly), using the channel’s brand colours and font.
- **Safety net:** undo/redo (every operation is invertible), autosave, “AI / You” chips, compare with the AI version, revert to AI.
- **Export:** renders a new Take through the existing takes system, noting “Edited: music, 3 overlays”.
- **Scope:** v1 = audio, overlays, captions, shots. Later = multi-clip B-roll and your own video clips.

### E. The Editing assistant (second chatbot)

Reuse the existing infrastructure (`AgentProvider`, the Groq and Gemini codecs, bounded steps, compaction, failover) with a **separate** tool set, system prompt and sheet inside the Editor.

- **Tools (about 12–14, to protect Groq’s free-tier token limits):** `get_timeline`, `look` (send frames or a range to the model; Gemini only), `add_overlay`, `add_title_card`, `edit_caption`, `set_music`, `add_sfx`, `set_shot` (motion, transition, split), `remove`, `retime`, `undo`, and `generate_*` for new assets.
- **Edits apply as a pending change** shown on the preview with Keep / Undo (cheap and reversible). Anything that **spends money** (new image, music, SFX, hero clip) shows the price and needs a tap, using the same `AgentApproval` and spend-cap pattern as the main assistant.
- **It cannot** change settings, channels, keys or projects. A test asserts its tool names never overlap the main assistant’s and that no tool can reach the Keychain.
- **Context** = a compact timeline summary + the selection + the playhead, never the whole project.

### F. Playground

Purpose: explore approaches with almost no tokens. Everything runs through a containment layer like `RunOverrides`, so nothing touches real settings.

- **Style Lab (zero AI):** apply caption looks, overlay styles, transitions, music and SFX maps to any past project’s cached assets and preview instantly.
- **Hook Bench (a few hundred tokens):** ten hook options for a topic from one call; pick one; only then write the script.
- **Storyboard-first:** generate scene text and image prompts only, review them as placeholder frames, *then* pay for images.
- **Rehearsal render:** stand-ins (Apple voice, placeholder or pool images, 360p) to test pacing and structure.
- **Response cache** (hash of provider + model + system + prompt): repeats cost nothing; it also speeds retries.
- **Token and price meter** with a per-session cap.
- **Recipes:** save a combination (style + music mode + SFX map + editing mode + prompts) and apply it to Create.

### G. The human layer (“user-demanding”)

The strongest “not generic” signals are human ones, so the update asks for them:

- **Director’s Brief** on Create (all optional): your angle or opinion, one thing viewers won’t expect, facts or links to ground on, must-include and avoid. It feeds the brief and narration prompts as authoritative context.
- **Review gates** (toggles, per channel): review script, review storyboard (before paid images), review edit. Default: script review on, the others off.
- **Your voice:** record the narration yourself (teleprompter view, pause detection), reusing the existing silence-trim and word-timing path.
- **Your footage and photos:** import clips and photos into scenes, so real material sits next to AI images.

## 5. Order of work

Principle: ship the biggest quality jump earliest, and don’t build the editor on sand. Sizes are relative (S < M < L < XL).

1. **M1 — Models and onboarding (M).** Welcome chooser, refreshed model ids, cost-tiered auto-check, recent-working fallback. Independent and low risk; it also fixes the weak defaults immediately.
2. **M2 — Sound v1 (L).** Mixdown engine, Music library (generate, import, link), Music settings, SFX library + Moments map, automatic placement from existing events. No editor needed; the first videos with music and sound effects.
3. **M3 — EditList + Editor v1 (XL).** EditList and primitives, preview, Editor page, undo/redo, the 10 s gate and editing modes (with the deterministic base cut).
4. **M4 — AI Edit revamp (L).** The `edit` phase: storyboard sheet, plan, polish, draft-render critique; shot splitting, punch-ins, overlays; optional hero shots.
5. **M5 — Editing assistant (M).** On the same operations.
6. **M6 — Playground + human layer (L).** Cache, Style Lab, Hook Bench, storyboard-first, recipes; Director’s Brief, review gates, record-your-voice, footage import.

Every milestone ends with CI green, a sample render artifact, and a short update to the README and CLAUDE.md.

## 6. What a video costs (rough, per 60 s Short)

- Text (writer, story map, edit plan, critique): cents.
- Images: 8–10 × about $0.04–0.07 ≈ $0.35–0.70 (Pollinations is free).
- Voice: Gemini TTS, cents; Apple voice, free.
- Music and SFX: reused = $0; one-off pack generation = cents to under a dollar.
- Optional hero shots: about $0.40 per 8 s clip on Veo 3.1 Lite.

Roughly **$0.4–0.8 without hero shots, up to about $1.5 with two.** The real numbers will live in `CostTable` and the Create screen’s estimate.

## 7. Risks and guardrails

- **Preview speed is unknown** until measured on a device. Measure first; fall back to frame-scrub plus an on-demand proxy.
- **Vendor churn:** preview ids, the Interactions API, intro prices ending Dec 31, 2026 (3.8 Flash and TTS roughly double). Mitigation: provider protocols, live model lists, one price table with “as of” dates.
- **Music rights:** keep provenance for everything; no bundled third-party catalogue; link-import is the user’s responsibility, with a warning.
- **YouTube policy:** its July 2026 clarification lists generic, template-style videos with little variation as ineligible for monetization, while AI use alone doesn’t cost monetization. Variation (music rotation, edit styles) and human input are features, not decoration.
- **Cost surprises:** nothing paid runs without a visible estimate; hero shots are off by default.
- **Scope:** seven workstreams become six milestones, each shippable behind a flag.
- **Uncompiled code:** no local Swift compiler, so small steps, CI at each step, and the pure-logic package.
- **Token limits:** Groq’s free tier limits the assistant’s tool set; contact sheets and video go only to Gemini.

## 8. Decisions (approved October 26, 2026)

1. **Music:** generate + import + link, with no bundled or scraped catalogue.
2. **Editor entry:** a button in Studio, not a sixth tab. (Later changed by the owner: the Editor became its own tab in place of Studio, and Studio opens from the Library.)
3. **Default editing mode:** Ask (the 10-second gate).
4. **Hero shots (Veo):** built in M4, off by default.
5. **Record-your-own-voice:** included in M6.

## 9. Housekeeping before M1

- Update CLAUDE.md and README: they are stale on takes (snapshots and restore exist), Director I (the story map exists), chapters, and the test count (57 → about 490).
- Remove four unrelated template workflows (`c-cpp`, `django`, `jekyll-docker`, `python-app`) and the committed `AVG_Studio-ios-native-reviewed.zip`.
- Add the CI render harness.

## 10. Approved decisions and amendments (October 26, 2026)

**Approved:** the order of work (section 5) and the five decisions in section 8 (Editor opens from a button in Studio; default editing mode is Ask with the 10-second gate; hero shots are off by default; record-your-own-voice lands in M6; music = generate + import + link, no scraped catalogue). This section supersedes section 8 and adjusts sections 2, 3.6, 4A, 4B and 4C.

### Amendment 1: your own music is first-class

- **Upload my music** is the primary action on the Music page: Files, iCloud Drive, the share sheet from other apps; mp3, m4a, wav, aiff. License badge “Mine”. It needs no network and no paid AI.
- **Free, on-device analysis:** duration, loudness and a rough energy curve. Mood and genre tags are set by hand; AI tagging is optional and runs only when a Gemini key has quota.
- **Per-channel pinned tracks:** pin tracks to a channel. The Auto picker draws from pinned tracks plus the library and prefers your uploads over generated ones when tags tie.
- Generation (Lyria) stays optional and paid. Source order on the page: **Upload → Generate (paid) → Link → CC0 browse (later)**.

### Amendment 2: Google is the preferred backbone, never a requirement

What the research says: Gemini text models are free-tier but rate-limited per project (requests and tokens per minute, requests per day, reset at midnight Pacific). Google no longer prints a per-model free table, and reported limits have dropped before, so we must not hard-code numbers. Image and video models (Nano Banana Pro, Veo 3.1) and Lyria have **no free tier**.

Rules that follow:

- **Every feature has a free path and a paid path.** Paid-only features carry a “Paid Google tier” badge and are never part of a default run.
- **Edit quality ladder** (setting, per channel): *Fast* = deterministic rules + one text plan call (works on Groq, Apple or Gemini free) · *Smart* (default when a Gemini key exists) = adds one vision call on a storyboard contact sheet (a handful of frames, far cheaper than video) · *Thorough* = adds the draft-video critique. The video critique never runs unless chosen.
- **Quota-aware calls:** a small request budget per provider learned from 429s and Retry-After; cache every identical call; batch small asks into one; skip optional AI passes with a log line (“skipped: rate-limited”) instead of failing the video.
- **Spread the load:** the assistant and cheap steps can use Groq or Apple, keeping Gemini free quota for the steps that benefit most (script, storyboard vision).
- **Welcome bundles, redefined:** *Free* = Apple + Pollinations, no keys · **Balanced = a free Google key** (Gemini text on the free tier, Pollinations images, Apple voice, your uploaded music) · *Premium* = paid Google and ElevenLabs (Gemini images and TTS, Lyria, Veo).
- **Hero shots** stay off by default and only appear with a paid tier.

## Sources

- Lyria 3.5 launch: https://pasqualepillitteri.it/en/news/14486/lyria-3-5-gemini-app-api-en
- Lyria 3.5 pricing: https://aicybr.com/blog/google-lyria-3-5-gemini-api-guide and https://scalevise.com/resources/google-lyria-3-5-gemini-music-controls/
- Veo 3.1 pricing: https://benchlm.ai/media-pricing/veo
- Gemini 3.8 Flash: https://apidog.com/blog/how-to-use-gemini-3-8-flash-api/ and https://thebridge.jp/2026/09/gemini-3-8-flash-specs-api-pricing
- Gemini 3.8 Flash TTS: https://www.usagepricing.com/blueprint/activity/google-gemini-2026-10-06-flash-tts-launch
- Sound-effects API pricing: https://modelslab.com/sound-effects-api-pricing
- Freesound terms summary: https://github.com/codybrom/FreesoundKit
- Pixabay terms: https://pixabay.com/service/terms/ and https://pixabay.com/service/license
- YouTube inauthentic-content clarification: https://techcrunch.com/2026/07/20/youtube-clarifies-policies-around-ai-slop-and-upsetting-videos/

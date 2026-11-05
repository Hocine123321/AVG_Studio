# AVG Studio — Settings plan for the new features

> Written as a Claude doc on November 1, 2026, approved the same day, and copied here so it lives with the code.
> Progress is tracked in `REMAINING_UPDATES.md`. Steps S1 (the Editing page) and S2 (per-video overrides) are done; S3 onward is not.

*Plan · November 1, 2026 · covers everything added since the Director’s Cut plan: the AI team and automatic model setup, music and sound effects, the edit gate, the editor (now its own tab), shot cuts and Smart crops, and the editing assistant.*

## 1. Where things stand

There are three levels of control: the **Settings tab** (how the app behaves), the **Channels tab** (a channel’s identity) and the **Editor** (this one video). The new features landed mostly in the channel editor and the editor, which was right for building them, but it leaves gaps:

- **AI team:** it can only be chosen in Welcome. There is no way to change it later, re-run the model check, or see what the automatic setup changed.
- **Edit gate:** the 10-second window is a fixed constant and there is no global “never ask”.
- **Defaults:** a new channel gets hard-coded values (editing mode “ask”, cuts “on”).
- **Per-video overrides** stop at the music choice and sound effects on/off. There is no level and no cuts switch for one video.
- **Search:** Settings search cannot find Music, Sound effects or anything newer.
- **Audio shelf:** there is no storage view and no way to clear unused tracks.

## 2. Principles

1. **A setting is a choice a person would plausibly change.** Tuned constants stay constants (section 4).
2. **Put it at the right level.** Global = how the app behaves. Channel = what makes a channel itself. Video = this edit only.
3. **Every setting has a safe default, a one-line explanation, and a test.** None may silently spend money.
4. **Follow the existing rules** (CLAUDE.md): a new `AppSettings` field needs a default, a matching line in the tolerant `init(from:)`, a `SettingAnchor` and a `SettingsIndex` entry, or the tests fail. Channel fields live in `ChannelEditor`. No new tab.

## 3. What to add

### 3.1 Settings → AI team (new page, first row of the Create group)

The Welcome step, available any time.

- **Preset picker** (Free / Balanced / Premium) using the existing `AITeamPreset.assignment`, showing who staffs the writer, images and voice right now, and which keys are missing. “Apply” changes the three providers.
- **“Pick the best model when I add a key”** toggle, `autoPickModels` (default on). When off, `ModelAutoSetup` does nothing; the manual model pickers still work.
- **“Check my models now”** button, with the date of the last check and the lines `ModelPrefs.setupNotes` produced (“Gemini writer: now X (was Y)”). Today those lines are only visible in Welcome.
- **“Undo automatic picks”:** puts every model listed in `ModelPrefs.autoPicks` back to its factory default. Never touches a model the person chose.
- Fields: `autoPickModels: Bool = true`. Anchors: `.aiTeam`, `.autoPickModels`.

### 3.2 Settings → Editing (new page, Create group, after Video) — DONE (S1)

Global behaviour of the new editing features.

- **Edit gate:** a picker “Offer to edit before the first render” with *Never / 5 s / 10 s / 20 s*, field `editGateSeconds: Int = 10` (0 = never ask). It is a master switch above the per-channel mode: “Never” behaves like every channel set to “Let AI finish”. `EditGate` carries its own `seconds`, so the card’s countdown always matches.
- **Defaults for new channels:** editing mode and cuts level (`defaultEditingMode: EditingMode = .ask`, `defaultEditLevel: EditLevel = .fast`). Existing channels are not changed; the channel editor still has the last word.
- **Editor preview quality:** *Fast (360p)* or *Sharp (540p)*, `previewSharp: Bool = false`.
- A footer explains that Smart crops use one small request to the Google key per video and are skipped without a key or free quota.
- Anchors: `.editGateSeconds`, `.defaultEditingMode`, `.defaultEditLevel`, `.previewQuality`.

### 3.3 Settings → Music and Sound effects (existing pages): the shelf

- **Storage line** per page: number of items and total size.
- **“Remove unused”:** lists tracks no channel or video uses, with a confirm; deletes only on confirmation.
- **Licence reminder** stays as it is; nothing new to ask.

### 3.4 Editor: per-video overrides (higher value than any global)

Add to `ProjectEdits` (all optional, “nil = follow the channel”):

- `musicGainDB` — a “Music level” slider in the editor’s Music section (shown when music is on).
- `sfxGainDB` — an effects level slider.
- `editLevel` — “Cuts and punch-ins”: Same as channel / Off / Cuts / Smart, so one video can drop the cuts without touching its channel.

A small `EditResolution` helper (video-over-channel in one place) is used by the render, the preview and the editor, so they never disagree.

The editing assistant gets the same three through its existing `set_music` / `set_sound_effects` tools (a new optional `level_db`) plus one new `set_cuts` tool (still inside its “this video only” scope; the tool count stays under 14).

### 3.5 Channels: small additions

- **“Use the same sound settings as another channel”:** copies music, effects, levels and moments in one tap.
- **Smart crops status line:** shows “needs a Google key” when the level is Smart and no key is saved.
- Existing sections (Editing, Music, Sound effects) keep their place; no change to their fields.

### 3.5b New-channel creation

Done in S1: a channel created from now on starts with the defaults from Settings → Editing.

### 3.6 Search, Quick settings, Activity

- **Search:** `SettingsIndex` entries already can point at a standalone route (App health does). Add entries for Music, Sound effects and the AI team page. Terms: music, ducking, volume, sound effects, whoosh, riser, gate, edit it yourself, captions, cuts, punch-in, crops, assistant.
- **Quick settings sheet:** add the gate picker (the one setting someone will want to flip in a hurry).
- **Activity:** a line when the automatic model setup changes a model, so “why did my writer change?” always has an answer.

## 4. Deliberately not added

These are tuned constants. Exposing them invites broken combinations and adds nothing a person can judge without listening or watching for an hour.

- Ducking depth, attack, release and fade lengths; the loop dip at a repeat seam.
- Sound-effect spacing, the “at most two in five seconds” cap, the lead before the picture.
- Punch-in strength and length, shot length, minimum gaps, the zoom recipes.
- The editing assistant’s step limit and history window.

If real videos show a constant is wrong, change the constant. If different people genuinely need different values, add a single “Advanced” page later, not scattered switches.

## 5. Tests

- **Settings round trip:** every new field survives encode/decode and an older file without it keeps its default (the pattern in `ModelsTests`).
- **Index:** every new anchor has an entry and every entry resolves (`SettingsIndexTests`); standalone-route entries resolve to a real route.
- **Behaviour:** `autoPickModels` off → `ModelAutoSetup.run` changes nothing; `editGateSeconds = 0` → `EditGatePolicy` returns “go on” for every mode; an `EditGate` carries its own length; per-video `editLevel` beats the channel’s in `Pipeline.editPlan`; per-video gains beat the channel’s in the mix.
- **Safety:** “Undo automatic picks” never changes a model that is not in `autoPicks`; “Remove unused” never deletes a track a channel or video still references.
- **Assistant:** the new tool is in the editing set and not in the main assistant’s.

## 6. Order of work

Each step ships on its own, with CI green before the next.

1. **S1 — Editing page + gate seconds** (small). DONE.
2. **S2 — Per-video overrides + assistant tools** (small). Level sliders, the cuts switch, `level_db`, `set_cuts`. DONE.
3. **S3 — AI team page** (medium). Preset picker, auto-pick toggle, re-check, undo automatic picks.
4. **S4 — Search and Quick settings** (small). Standalone-route index entries for Music, Sound effects and the AI team; the gate picker in Quick settings; an Activity line for automatic model changes.
5. **S5 — Audio shelf housekeeping** (small). Storage line and “Remove unused”.
6. **S6 — Docs.** README and CLAUDE.md rows for each new setting and the rule that a constant is not a setting.

## 7. Decisions (approved November 1, 2026)

1. **Gate choices:** Never / 5 s / 10 s / 20 s, default 10 s.
2. **Where it lives:** a new **Editing** page in the Create group, not inside Video.
3. **Auto-pick:** on by default, with the toggle to turn it off.
4. **Per-video sliders** (music level, effects level) and the per-video cuts switch: yes.
5. **New-channel defaults:** editing mode “ask”, cuts level “Cuts”, as today.
6. **Preview quality choice:** include it, default Fast.

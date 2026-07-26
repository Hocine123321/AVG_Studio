# Slice 5 — Takes and export — design

**Date:** 2026-07-26
**Status:** approved
**Follows:** Slices 1–4 (installer, config, providers, pipeline)

---

## 1. Why this slice, and why not the obvious one

After Slice 4 the app can turn a topic into a video. The audit that followed listed twelve features the PowerShell app had and this one does not. Rather than port them, we asked what the user actually does, and the answer redirected the slice.

**The daily loop is: generate → not quite right → change something → generate again → compare → hand the best one to CapCut.**

Two things follow, and both cut against what a feature-parity port would have built:

- **Surgical editing is not wanted.** The obvious GUI move — make the artifacts on the Studio canvas editable, fix one line, regenerate downstream — solves a problem the user does not have. It was the direction this design started in and was dropped.
- **Cost control is not urgent.** The recycling pool and composite images exist to spend less on image generation. But this user's paid providers are all out of quota, so they run on Pollinations and Stable Horde, which are free. Building cost control now would optimise a cost of zero.

What the loop does demand is cheap re-runs, a way to hold results against each other, and a clean handoff.

## 2. The three ideas behind twelve features

The audit's twelve missing features are three ideas wearing console costumes:

- **"Do that bit again"** — Preview a Phase, Quick Redo, script review mode. Three menu items because a console can only offer modes. This slice replaces all three.
- **"Get better over time"** — the learning system, Train sessions, App Chat. Deferred; the user re-rolls rather than teaches.
- **"Spend less"** — recycling pool, composites. Deferred as above.

Script review is the clearest example of console shape: it shelled out to **Notepad**, blocked the pipeline, and re-read the file. Here the script is already on screen, and re-rolling is a click.

## 3. Takes

A project holds **takes**. Take 1 is the original generation; each later take is a re-run with something changed. A take carries a settings snapshot, its own phase records, and its own output.

**Take folders are self-contained copies, not overlays.** Copying ~20–40 MB of shared artifacts costs about a second and keeps every path in the app trivial. An overlay model would push "which take does this file come from?" into every read site, for a saving that does not matter.

**"Current" and "selected" are different things, and conflating them would be a bug:**

- The **current take** is what the project treats as canonical — the thumbnail on Home, `master_output`, the default for export. It advances automatically to each take that finishes successfully, and the user can pin a different one.
- The **selected take** is merely what you are looking at in Studio. Selecting take 1 to inspect it does not demote take 3.

`ProjectDoc.master_output` therefore becomes *the current take's output* rather than a fact about the project. The ten imported projects migrate cleanly: their existing output becomes take 1, and take 1 is current.

## 4. The invalidation map — the heart of the slice

Each setting declares the first phase its change invalidates. That phase and everything after it re-runs; everything before is kept.

| Change | First invalid phase | Keeps |
|---|---|---|
| `content_shape`, `video_form` | Brief (0) | nothing |
| `length_preset`, `dynamic_pauses_enabled` | Script (1) | brief |
| `visual_tone`, `style_lock`, `auto_style_lock` | Director I (2) | + script |
| `aspect_ratio`, `image_provider`, `image_model`, `image_quality` | Visuals (3) | + video map |
| `voice_provider`, `voice_id`, `voice_model_id`, `voice_stability`, `voice_speed` | Voice (4) | + images |
| `ken_burns`, `brand_preset` | Director II (5) | + narration |
| `captions_*`, `font_family` | Captions (6) | + motion plan |
| `fps`, `render_codec`, `render_engine` | Render (7) | everything but the composite |
| `theme`, `python_exe`, `performance_mode`, `run_as_admin`, key settings | *nothing* | not creative inputs |

**Implemented as one new field on each `SettingSpec`.** The schema already exists and already drives the settings UI; adding `invalidates` gives the whole feature for free and keeps the mapping next to the setting it describes.

**One row is genuinely non-obvious and is the reason this must be explicit rather than inferred:** `dynamic_pauses_enabled` invalidates the **script**, not voice. `[pause]` markers are placed during script generation; the voice phase only reads them. Infer it from the name and you would re-run voice, get identical audio, and have no idea why.

## 5. Making a take

Two entry points, because they are different intentions:

- **Change a setting.** The start phase is computed from the map.
- **Re-roll from a phase.** Change nothing, pick a phase, run again. Models are non-deterministic, so "that script was weak, give me another" is frequent. Without this the only way to re-roll would be to fake a settings change.

Before committing, the panel states the consequence:

> Changing **visual tone** re-runs Director I, Visuals, Director II, Captions and Render.
> Keeps your brief, script and narration. **About 4 minutes** — last take took 4:12 for these phases.

**The estimate is measured, not guessed.** Every `PhaseRecord` already stores `duration_ms`, so the quote comes from this machine's own history: for each phase about to re-run, the duration from the most recent take in which that phase actually completed, summed. Phases with no recorded run contribute nothing and are named as unknown. If no phase in the set has ever run, no number is shown at all — a fabricated estimate is worse than none.

Takes label themselves from what changed (*"Take 2 · visual tone"*, *"Take 3 · re-roll script"*) and can be renamed. A take that changes nothing and re-rolls nothing is refused; it would spend tokens to reproduce what already exists.

## 6. Export for CapCut

Not a pipeline phase — nothing is generated. A button assembles a folder from **the selected take's** artifacts (not the project's, and not the current take's — you export what you are looking at):

```
<project>/exports/take-2/
  images/           001.png, 002.png … in order
  narration.mp3
  captions.srt
  timing.csv        image · start · end · duration · what is said over it
  master_output.mp4 the reference cut
  how-to-import.txt
```

`timing.csv` is the piece that earns its keep. CapCut lays images out in filename order but gives them a uniform default duration, so the real work is setting each one. The sheet carries the exact duration beside the line of narration it covers, so nothing has to be scrubbed for.

Explorer opens on the folder when it finishes.

**CapCut only.** It takes images, audio and `.srt` but has no project-interchange format, so a well-organised folder is the whole opportunity. Resolve and Premiere would justify real XML/EDL export; the user does not use them, and building interchange for an unused editor is waste.

## 7. On screen

**Two perpendicular axes: phases down the left rail, takes across the top.** No new screen.

```
Take 1        Take 2 · visual tone        Take 3 · re-roll script  ●        + New take
0:18:42       0:04:12                     running · 1:20
```

Selecting a take repoints the existing rail, canvas and activity log at that take's data.

**Colour discipline holds.** Selection is an elevated surface with a stronger border, never the accent — terracotta stays reserved for the take actually running, sage marks done takes, red marks failed. Letting selection take the accent would put two things on screen competing for the eye and make the running take hard to find.

**New take** is a slide-over from the right, so the take being branched from stays visible. It reuses Slice 2's schema-driven field renderers unchanged — the reason they were built that way. The consequence preview updates live. One terracotta action: *Create take*.

**Export** sits in the header actions beside Folder, acting on the selected take.

**Home** gains `3 takes` in a card's mono meta line; the thumbnail comes from the current take.

**Single-take projects still show the strip** (`Take 1 · + New take`), or the ten imported projects would never reveal the feature.

## 8. Failure and disk

**A failed take cannot damage a good one** — takes are separate folders, so a failing run writes only inside its own. This is structural, not something the code must remember.

The one hazard is a **half-copied take** when disk fills mid-clone. Copying happens *before* the take is registered in `project.json`, so a failure removes the folder and the take never existed. The document never holds a half-state.

**Disk is this design's honest cost:** roughly 30–75 MB per take; ten projects at three takes is a couple of gigabytes.

**No automatic pruning.** Silently deleting a video the user may want is not the app's decision. Each project shows its total size, every take can be deleted, and deleting the last remaining take is refused. A retention policy is easy to add later and hard to un-ship if wrong.

## 9. Testing

**One test matters more than the others:** a completeness check that walks `SETTINGS` and fails if any entry lacks `invalidates`. The dangerous failure is not a wrong row today — it is someone adding a setting in six months, forgetting the field, and re-runs quietly doing the wrong thing from then on. That must be impossible to merge.

Also covered:

- Each map row, including `dynamic_pauses_enabled → script`.
- Take creation: copy semantics, shared artifacts present, and a new take starting with **no** `master_output`, so a failed render cannot inherit the previous take's video and look successful — the same guard `MediaEngine` already carries.
- A failed take leaving the previous take's files untouched.
- Export: `timing.csv` row count matching scene count; a valid `.srt`.
- Estimates derived from history, and **absent** rather than fabricated without it.

No live API calls, per the standing rule.

## 10. Out of scope

Side-by-side take playback (asked and declined), the learning system, Train, App Chat, trend discovery, topic research, the recycling pool, composite images, Resolve/Premiere interchange, automatic retention, and surgical artifact editing.

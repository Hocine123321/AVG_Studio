"""
avg.py  —  AVG unified Python worker
Kept separate: overlay_design.py (pure rendering library, imported by server.py),
               server.py (persistent render server)

Usage:
    python avg.py splice          <manifest.json> <out_voice.mp3> <out_scene_audio_map.json>
    python avg.py splice_flat     <flat_voice.mp3> <scene_texts.json> <timeline.json|-> <marker_ms> <out_voice.mp3> <out_scene_audio_map.json>
    python avg.py remove_silences <input> <output> [thresh] [min_sil] [keep] [beat_map] [timeline] [scene_map]
    python avg.py assemble        <project_path> [fps] [codec] [threads] [aspect] [ken_burns] [scenes] [brief] [timeline] [scene_map]
    python avg.py overlay_render  <project_path> <edit_plan.json> <scene_timings.json> <dynamic_options.txt> [brand_preset] [beat_map]
    python avg.py caption_burn    <project_path> <provider> <assemblyai_key> <openai_key> <font_size> <font_color> <outline_width> <position> <max_words> <timeout> <animation_style> [animation_speed]

Exit codes per subcommand:
    splice:          0 = ok, 1 = fatal
    splice_flat:     0 = ok, 1 = fatal (e.g. marker gaps not found — TTS dropped <break> tags)
    remove_silences: 0 = ok, 1 = fatal
    assemble:        0 = ok, 1 = fatal
    overlay_render:  0 = applied, 2 = skipped (non-fatal), 1 = fatal
    caption_burn:    0 = applied, 2 = skipped (non-fatal), 1 = fatal
"""

import os
import sys
import json
import re
import time as _time

# ── Ensure python\ dir is on path so overlay_design imports correctly ─────────
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# B2.3.4 — explicit MP3 bitrate for every voice export. pydub's default export
# has NO bitrate, so ffmpeg picks a low default and each re-encode degrades the
# audio. The voice track is re-encoded twice (splice -> remove_silences), so the
# final raw/optimized voice sounded noticeably worse than the raw per-scene TTS
# clips. 192 kbps is transparent for mono speech and keeps the file small.
VOICE_MP3_BITRATE = "192k"


def _replace_with_retry(src, dst, attempts=5, delay=0.4):
    """
    os.replace with retry-with-backoff for WinError 5 (access denied). The
    persistent render server keeps moviepy/ffmpeg-reader objects around
    across jobs in the same process — a prior job (e.g. overlay_render)
    closing its VideoFileClip doesn't always release the Windows file handle
    immediately, so the very next job's replace on the same file can lose
    that race. gc.collect() nudges any unreferenced moviepy readers to
    actually close their subprocess pipes before the next attempt.
    """
    import gc
    last_err = None
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            last_err = e
            gc.collect()
            _time.sleep(delay * (i + 1))
    raise last_err


def _resolve_input(*candidates):
    """
    Return the first candidate path that exists, else the first candidate.
    Supports pre-B1.10.0 projects whose phase files sit at the project root
    instead of the new script/audio/render subfolders — same non-destructive
    legacy fallback the PowerShell side (Invoke-VideoRender / Load-
    ProjectDocument) uses. Pass the new-layout path first, legacy root second.
    """
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return candidates[0]


def _write_progress(project_path, percent, message):
    """
    Writes progress.json ({percent, message}) to the project folder, throttled
    by the caller to 5% steps. PowerShell polls this file for the live
    progress line — avoids streaming stdout / touching the server's JSON
    protocol, which stdout print() calls would otherwise corrupt (this
    function replaces those prints for anything that runs under the
    persistent server).
    """
    try:
        render_dir = os.path.join(project_path, "render")
        os.makedirs(render_dir, exist_ok=True)
        path = os.path.join(render_dir, "progress.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"percent": int(percent), "message": message}, f)
    except Exception:
        pass  # progress reporting is never allowed to fail the phase itself


_HW_ENCODER_CACHE = None

def _detect_hw_encoder(preferred_codec):
    """
    Probes `ffmpeg -encoders` once (cached) for a hardware H.264 encoder.
    Software libx264 is CPU-bound and dominates render time; nvenc/qsv/amf
    are typically 5-10x faster where the hardware exists. Only auto-swaps
    when the caller asked for the default 'libx264' — an explicit codec
    choice (e.g. user picked libx264 deliberately) is respected as-is.
    """
    global _HW_ENCODER_CACHE
    if preferred_codec != "libx264":
        return preferred_codec, "faster"   # explicit choice — don't override

    if _HW_ENCODER_CACHE is not None:
        return _HW_ENCODER_CACHE

    import subprocess
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                              capture_output=True, text=True, timeout=10).stdout
    except Exception:
        _HW_ENCODER_CACHE = ("libx264", "veryfast")   # B2.4: was 'faster'
        return _HW_ENCODER_CACHE

    # B2.5.3 — `ffmpeg -encoders` lists every encoder COMPILED into the build, not the
    # ones the machine can actually use. On an Intel-only laptop (no NVIDIA card) that
    # still lists h264_nvenc, so the old "is it in the list?" check picked nvenc, which
    # then died at runtime with -22 (Invalid argument) on EVERY render/overlay/caption
    # pass before falling back to libx264. Now we functionally PROBE each candidate with
    # a throwaway 1-frame encode and only commit to one that truly works — so this
    # machine correctly lands on Intel QSV instead of a doomed nvenc attempt.
    def _probe_encoder(codec):
        try:
            r = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1",
                 "-frames:v", "1", "-c:v", codec, "-f", "null", "-"],
                capture_output=True, text=True, timeout=20)
            return r.returncode == 0
        except Exception:
            return False

    # Priority: nvenc (NVIDIA) > qsv (Intel Quick Sync) > amf (AMD) > software.
    # preset/quality param differs per hw encoder API — each needs its own tuning.
    for _cand, _preset in (("h264_nvenc", "p4"),      # p1(fastest)-p7(best); p4 balanced
                           ("h264_qsv",   "faster"),
                           ("h264_amf",   "balanced")):
        if _cand in out and _probe_encoder(_cand):
            _HW_ENCODER_CACHE = (_cand, _preset)
            return _HW_ENCODER_CACHE
    _HW_ENCODER_CACHE = ("libx264", "veryfast")   # B2.4: was 'faster' — software
    return _HW_ENCODER_CACHE


def _use_ffmpeg_engine(render_engine):
    """B2.9 — decide whether the native ffmpeg engine (render / overlay) runs, given the
    config value 'auto' | 'ffmpeg' | 'moviepy'. Default is ON (auto): the engine tries
    first and any 'unsupported'/error still degrades to the exact moviepy path. Only an
    explicit 'moviepy' disables it. The legacy AVG_RENDER_ENGINE env still overrides
    (dev escape hatch): env 'moviepy' forces off, env 'ffmpeg' forces on."""
    env = os.environ.get("AVG_RENDER_ENGINE", "").strip().lower()
    if env == "moviepy":
        return False
    if env == "ffmpeg":
        return True
    return str(render_engine or "auto").strip().lower() != "moviepy"


def _h264_quality_args(codec, preset, crf="18"):
    """
    B2.4 — per-encoder ffmpeg args for a visually-transparent H.264 re-encode of an
    already-finished video (the caption/overlay burn passes). libx264 uses -crf; the
    hardware encoders use their own constant-quality knobs. Any hw failure is caught
    by the caller, which re-runs with libx264 (poisoning _HW_ENCODER_CACHE) — so a
    wrong/unsupported hw flag degrades to software, never to a broken output. Mirrors
    _detect_hw_encoder's codec set.
    """
    if codec == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", preset, "-rc", "vbr", "-cq", "20", "-b:v", "0"]
    if codec == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", preset, "-global_quality", "20"]
    if codec == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", preset, "-rc", "cqp",
                "-qp_i", "20", "-qp_p", "20", "-qp_b", "20"]
    return ["-c:v", "libx264", "-preset", preset, "-crf", crf]


# ══════════════════════════════════════════════════════════════════════════════
#  SHARED HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _load_json(path, default=None):
    """Load JSON from path, return default on any error."""
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


# ══════════════════════════════════════════════════════════════════════════════
#  SUBCOMMAND: splice
#  (was splice_voice.py)
#  Concatenates per-scene TTS clips + inserts exact silence at takeover
#  positions → raw_voice.mp3 + scene_audio_map.json
# ══════════════════════════════════════════════════════════════════════════════

def _splice_silent(duration_sec):
    from pydub import AudioSegment
    return AudioSegment.silent(duration=int(round(duration_sec * 1000)))


def cmd_safezones(argv):
    """
    argv: [project_path, index_json_path, out_path]
    Standalone safezone analysis — split out of render_project so Edit Plan
    (its own phase now, running before Voice & Audio) has this data without
    needing Render to happen first. Only needs raw generated images
    (index.json), not the rendered/Ken-Burns'd frames — same input
    render_project used to feed it, just called earlier in the pipeline.
    Uses _analyze_safezone/_GRID/_ZONE_NAMES/_cell_busyness defined later in
    this file — fine, Python resolves names at call time, not definition
    order, and this only runs via the _COMMANDS dispatch table below.
    """
    if len(argv) < 3:
        print("Usage: avg.py safezones <project_path> <index.json> <out_scene_safezones.json>",
              file=sys.stderr)
        sys.exit(1)

    project_path = argv[0]
    index_path   = argv[1]
    out_path     = argv[2]

    global np, _PILImage, _LANCZOS
    import numpy as np
    from PIL import Image as _PILImage
    try:
        _LANCZOS = _PILImage.Resampling.LANCZOS
    except AttributeError:
        _LANCZOS = _PILImage.LANCZOS

    index = _load_json(index_path, [])
    if not index:
        print(f"safezones: {index_path} empty or not found", file=sys.stderr)
        sys.exit(1)

    safezones = []
    for i, rel in enumerate(index, start=1):
        img_path = os.path.join(project_path, rel)
        z = _analyze_safezone(img_path)
        safezones.append({"scene": i, "safe_zone": z["safe_zone"], "busy_zone": z["busy_zone"]})

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(safezones, f, indent=2)
    print(f"safezones: {len(safezones)} scene(s) analyzed", file=sys.stderr)
    sys.exit(0)


def cmd_splice(argv):
    """
    argv: [manifest_path, out_audio, out_map]
    """
    if len(argv) < 3:
        print("Usage: avg.py splice <manifest.json> <out_voice.mp3> <out_scene_audio_map.json>",
              file=sys.stderr)
        sys.exit(1)

    code, msg = splice_clips(argv[0], argv[1], argv[2])
    print(msg, file=sys.stderr if code != 0 else sys.stdout)
    sys.exit(code)


# ══════════════════════════════════════════════════════════════════════════════
#  SUBCOMMAND: splice_flat
#  B1.4.0 — replaces per-scene TTS generation (N API calls) with ONE flattened
#  call. Scene texts were joined with <break time="Xms" /> SSML marker tags
#  before TTS; this locates those marker gaps via silence-detect to recover
#  exact per-scene boundaries, then rebuilds the final track scene-by-scene
#  (dropping the marker gaps, inserting REAL takeover silence from timeline.json
#  in their place) — output shape identical to splice_clips's scene_audio_map.json
#  so remove_silences/assemble/overlay_render need zero changes downstream.
# ══════════════════════════════════════════════════════════════════════════════

def cmd_splice_flat(argv):
    """
    argv: [flat_voice_path, scene_texts_path, timeline_path, marker_ms, out_audio, out_map]
    timeline_path may be "" (no takeovers — flat preset).
    """
    if len(argv) < 5:
        print("Usage: avg.py splice_flat <flat_voice.mp3> <scene_texts.json> "
              "<timeline.json|-> <marker_ms> <out_voice.mp3> <out_scene_audio_map.json>",
              file=sys.stderr)
        sys.exit(1)

    flat_voice_path  = argv[0]
    scene_texts_path = argv[1]
    timeline_path    = argv[2] if argv[2] not in ("", "-") else ""
    marker_ms        = int(argv[3])
    out_audio        = argv[4]
    out_map          = argv[5] if len(argv) > 5 else os.path.join("audio", "scene_audio_map.json")

    code, msg = splice_flat(flat_voice_path, scene_texts_path, timeline_path,
                             marker_ms, out_audio, out_map)
    print(msg, file=sys.stderr if code != 0 else sys.stdout)
    sys.exit(code)


def _weighted_split_boundaries(scene_texts, total_ms):
    """Proportional fallback split by character count when marker detection misses."""
    lengths = [max(1, len(t)) for t in scene_texts]
    total_chars = sum(lengths)
    boundaries, cursor = [], 0
    for length in lengths[:-1]:
        cursor += int(total_ms * (length / total_chars))
        boundaries.append(cursor)
    return boundaries


def splice_flat(flat_voice_path, scene_texts_path, timeline_path, marker_ms,
                 out_audio, out_map):
    """
    Pure callable entry point (server.py job handler calls this directly —
    must NOT call sys.exit). Returns (exit_code, message).
    """
    from pydub import AudioSegment
    from pydub.silence import detect_silence

    if not os.path.exists(flat_voice_path):
        return 1, f"flat voice file not found: {flat_voice_path}"

    scene_texts = _load_json(scene_texts_path, [])
    if not scene_texts:
        return 1, f"could not read scene texts ({scene_texts_path})"
    n_scenes = len(scene_texts)

    ext   = os.path.splitext(flat_voice_path)[1].lstrip(".").lower() or "mp3"
    sound = AudioSegment.from_file(flat_voice_path, format=ext)
    total_ms = len(sound)

    # Marker gaps are near-silent by design (700ms default) — detect at 80% of
    # the injected duration to tolerate TTS engine rounding/trim on break tags.
    min_gap = max(150, int(marker_ms * 0.8))
    silence_thresh = -45
    gaps = detect_silence(sound, min_silence_len=min_gap, silence_thresh=silence_thresh)

    degraded = False
    if len(gaps) < n_scenes - 1:
        # Don't throw here — that would force the caller to re-pay for a full
        # TTS regeneration just because marker detection came up short (e.g.
        # the provider trimmed some <break> tags). The audio we already paid
        # for is still usable: fall back to a text-length-weighted proportional
        # split across the flat track. Less precise than real boundaries, but
        # costs nothing extra and beats discarding paid-for audio.
        print(f"  [splice_flat] found {len(gaps)}/{n_scenes - 1} boundary marker(s) — "
              f"falling back to weighted-duration split for missing boundaries.", file=sys.stderr)
        degraded = True
        boundaries_ms = _weighted_split_boundaries(scene_texts, total_ms)
    else:
        boundaries_ms = [g[0] + (g[1] - g[0]) // 2 for g in gaps[:n_scenes - 1]]

    # Per-scene slices INSIDE the flat file (marker gaps still included at the
    # edges here — trimmed below when rebuilding).
    raw_bounds = []
    prev_end = 0
    for i in range(n_scenes):
        start = prev_end
        end   = boundaries_ms[i] if i < len(boundaries_ms) else total_ms
        raw_bounds.append((start, end))
        prev_end = end

    # timeline.json tells us where takeover gaps belong; missing/empty = flat
    # all-scene sequence (no takeovers), matching splice_clips's default.
    timeline = _load_json(timeline_path, []) if timeline_path else []
    if not timeline:
        timeline = [{"type": "scene", "scene": i + 1, "part": 0} for i in range(n_scenes)]

    combined  = AudioSegment.empty()
    scene_map = []
    cursor_ms = 0
    skipped   = []

    for node in timeline:
        ntype = node.get("type")

        if ntype == "scene":
            scene_num = int(node.get("scene", 0))
            idx = scene_num - 1
            if idx < 0 or idx >= n_scenes:
                print(f"  [splice_flat] scene {scene_num}: out of range — skipping.", file=sys.stderr)
                skipped.append(scene_num)
                continue
            s_start, s_end = raw_bounds[idx]
            if degraded:
                # Weighted-split boundaries land mid-speech, not on real silence —
                # trimming here would chop narration instead of a marker gap.
                clip = sound[s_start:s_end]
            else:
                # Trim a little padding off each edge so the marker's silence doesn't
                # bleed into the final track as an unnatural micro-pause.
                pad = min_gap // 4
                trim_start = min(s_start + pad, s_end)
                trim_end   = max(s_end - pad, trim_start)
                clip = sound[trim_start:trim_end]

            start_ms   = cursor_ms
            combined  += clip
            cursor_ms += len(clip)
            scene_map.append({
                "scene":    scene_num,
                "start":    round(start_ms / 1000.0, 3),
                "end":      round(cursor_ms / 1000.0, 3),
                "duration": round(len(clip)  / 1000.0, 3),
            })

        elif ntype == "takeover":
            dur = float(node.get("duration_sec", 1.8))
            dur = max(0.1, min(10.0, dur))
            gap        = _splice_silent(dur)
            combined  += gap
            cursor_ms += len(gap)

    if len(combined) == 0:
        return 1, "no usable scene audio after boundary split"

    os.makedirs(os.path.dirname(os.path.abspath(out_audio)) or ".", exist_ok=True)
    combined.export(out_audio, format="mp3", bitrate=VOICE_MP3_BITRATE)

    result = {
        "total_duration": round(len(combined) / 1000.0, 3),
        "scenes":         scene_map,
        "skipped_scenes": skipped,
        "degraded":       degraded,
    }
    with open(out_map, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    total_s = len(combined) / 1000.0
    msg = f"splice_flat: {len(scene_map)} scene(s) split from 1 TTS call, {total_s:.2f}s total"
    if degraded:
        msg += " [degraded: weighted-split fallback used, some markers missed]"
    if skipped:
        msg += f", {len(skipped)} scene(s) skipped: {skipped}"
    return 0, msg


def splice_clips(manifest_path, out_audio, out_map):
    """
    Pure callable entry point (used by server.py's persistent process —
    must NOT call sys.exit). Mirrors original splice_voice.py's splice_clips().
    Returns (exit_code, message). exit_code 0 = success, 1 = no usable clips.
    """
    from pydub import AudioSegment

    manifest = _load_json(manifest_path)
    if not manifest:
        return 1, f"could not read manifest ({manifest_path})"

    combined  = AudioSegment.empty()
    scene_map = []
    cursor_ms = 0
    skipped   = []

    for node in manifest:
        ntype = node.get("type")

        if ntype == "scene":
            scene_num = int(node.get("scene", 0))
            path      = node.get("path", "")
            if not path or not os.path.exists(path):
                print(f"  [splice] scene {scene_num}: clip missing ({path}) — skipping.",
                      file=sys.stderr)
                skipped.append(scene_num)
                continue
            try:
                ext  = os.path.splitext(path)[1].lstrip(".").lower() or "mp3"
                clip = AudioSegment.from_file(path, format=ext)
            except Exception as e:
                print(f"  [splice] scene {scene_num}: unreadable ({e}) — skipping.",
                      file=sys.stderr)
                skipped.append(scene_num)
                continue

            start_ms   = cursor_ms
            combined  += clip
            cursor_ms += len(clip)
            scene_map.append({
                "scene":    scene_num,
                "start":    round(start_ms / 1000.0, 3),
                "end":      round(cursor_ms / 1000.0, 3),
                "duration": round(len(clip)  / 1000.0, 3),
            })

        elif ntype == "takeover":
            dur = float(node.get("duration_sec", 1.8))
            dur = max(0.1, min(10.0, dur))
            gap        = _splice_silent(dur)
            combined  += gap
            cursor_ms += len(gap)

    if len(combined) == 0:
        return 1, "no usable scene clips — every clip was missing or unreadable"

    os.makedirs(os.path.dirname(os.path.abspath(out_audio)) or ".", exist_ok=True)
    combined.export(out_audio, format="mp3", bitrate=VOICE_MP3_BITRATE)

    result = {
        "total_duration": round(len(combined) / 1000.0, 3),
        "scenes":         scene_map,
        "skipped_scenes": skipped,
    }
    with open(out_map, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    total_s = len(combined) / 1000.0
    msg = f"spliced {len(scene_map)} scene(s), {total_s:.2f}s total"
    if skipped:
        msg += f", {len(skipped)} scene(s) skipped: {skipped}"
    return 0, msg


# ══════════════════════════════════════════════════════════════════════════════
#  SUBCOMMAND: remove_silences
#  (was remove_silences.py)
#  Strips silence from raw_voice.mp3, protecting PAUSE beats + takeover windows
# ══════════════════════════════════════════════════════════════════════════════

def load_protected_windows(beat_map_path, audio_duration_ms):
    """PAUSE-beat windows (±400ms/700ms) that must not be stripped."""
    bm = _load_json(beat_map_path, {})
    windows = []
    for b in bm.get('beats', []):
        if str(b.get('beat', '')).upper() != 'PAUSE':
            continue
        t_start = b.get('time_start', -1)
        if t_start < 0:
            continue
        win_start = max(0, int(t_start * 1000) - 400)
        win_end   = min(audio_duration_ms, int(t_start * 1000) + 700)
        windows.append((win_start, win_end))
    return windows


def load_takeover_windows(timeline_path, scene_map_path):
    """Exact-position takeover gaps — avg.py's splice/splice_flat already placed them."""
    if not timeline_path or not scene_map_path:
        return []
    timeline  = _load_json(timeline_path)
    scene_map = _load_json(scene_map_path)
    if not timeline or not scene_map:
        return []

    scene_end   = {}
    scene_start = {}
    for s in scene_map.get('scenes', []):
        try:
            sn = int(s['scene'])
            scene_end[sn]   = int(round(float(s['end'])   * 1000))
            scene_start[sn] = int(round(float(s['start']) * 1000))
        except Exception:
            continue

    windows      = []
    prev_scene   = None
    pending_take = False
    for node in timeline:
        ntype = node.get('type')
        if ntype == 'scene':
            sn = int(node.get('scene', 0))
            if pending_take and prev_scene is not None \
               and prev_scene in scene_end and sn in scene_start:
                windows.append((scene_end[prev_scene], scene_start[sn]))
            pending_take = False
            prev_scene   = sn
        elif ntype == 'takeover':
            pending_take = True
    return windows


def load_dynamic_pause_gaps(pause_map_path, scene_map_path, gaps):
    """
    B2.13 Polishing — pick which detected silence gaps are DELIBERATE dynamic pauses (the
    [pause] beats Phase 1 marked and Director 1 counted per scene). For each scene, take the
    K longest gaps whose midpoint falls inside that scene's window (K = the scene's [pause]
    count from pause_map.json; window from scene_audio_map.json, raw/pre-strip timeline).
    Returns a set of (start_ms, end_ms) gap tuples to RESIZE to the target length. Empty when
    the feature is off or the inputs are missing — the caller then behaves exactly as before.
    """
    pm = _load_json(pause_map_path, {}) if pause_map_path else {}
    if not isinstance(pm, dict) or not pm.get("enabled", False):
        return set()
    per_scene = pm.get("per_scene", {}) or {}
    sm = _load_json(scene_map_path, {}) if scene_map_path else {}
    windows = []
    for s in (sm.get("scenes", []) if isinstance(sm, dict) else []):
        try:
            windows.append((int(s["scene"]),
                            int(round(float(s["start"]) * 1000)),
                            int(round(float(s["end"])   * 1000))))
        except Exception:
            continue
    if not windows:
        # Single-scene videos have no scene_audio_map — treat the whole track as one window
        # with K = the total pause count, so those pauses are still shaped (not trimmed away).
        try:
            k_total = int(pm.get("total", 0)) or sum(int(v) for v in per_scene.values())
        except (TypeError, ValueError):
            k_total = 0
        if k_total <= 0 or not gaps:
            return set()
        end = max(g[1] for g in gaps) + 1
        windows = [(1, 0, end)]
        per_scene = {"1": k_total}
    _DYN_MIN = 250   # ignore micro-gaps — a real spoken pause is clearly longer
    chosen = set()
    for scene, s0, s1 in windows:
        try:
            k = int(per_scene.get(str(scene), per_scene.get(scene, 0)))
        except (TypeError, ValueError):
            k = 0
        if k <= 0:
            continue
        cand = [g for g in gaps
                if s0 <= (g[0] + g[1]) // 2 < s1 and (g[1] - g[0]) >= _DYN_MIN]
        cand.sort(key=lambda g: g[1] - g[0], reverse=True)
        for g in cand[:k]:
            chosen.add((g[0], g[1]))
    return chosen


def _rs_overlaps(seg_s, seg_e, windows):
    return any(seg_s < we and seg_e > ws for ws, we in windows)


def remove_silences_core(inp, out, thresh, min_sil, keep, beat_map, timeline,
                         scene_map, pause_map='', pause_target_ms=0):
    """
    B2.15 — SHARED silence-strip + dynamic-pause resize. The single source of truth used by
    BOTH the child-process subcommand (cmd_remove_silences) and the render server
    (server.job_remove_silences), so the loop lives in exactly one place (was duplicated).
    Never calls sys.exit; returns a result dict. ALL status prints go to stderr — under the
    server, stdout is the one-JSON-line-per-job channel and a stray stdout print corrupts it.
    Exports `out`, and remaps scene_map in place when present. Raises on a missing input.
    """
    from pydub import AudioSegment
    from pydub.silence import detect_nonsilent

    if not os.path.exists(inp):
        raise FileNotFoundError(inp)

    ext   = os.path.splitext(inp)[1].lstrip('.').lower() or 'mp3'
    sound = AudioSegment.from_file(inp, format=ext)
    total_ms     = len(sound)
    original_dur = total_ms / 1000.0
    print(f"Input:  {original_dur:.2f}s  ({os.path.basename(inp)})", file=sys.stderr)

    protected = load_protected_windows(beat_map, total_ms)
    if protected:
        print(f"  Beat map: {len(protected)} PAUSE window(s) protected", file=sys.stderr)
    takeovers = load_takeover_windows(timeline, scene_map)
    if takeovers:
        print(f"  Timeline: {len(takeovers)} takeover window(s) protected (exact)", file=sys.stderr)
    protected = protected + takeovers

    nonsilent = detect_nonsilent(sound, min_silence_len=min_sil, silence_thresh=thresh)
    if not nonsilent:
        print("Warning: no speech detected — writing input as-is.", file=sys.stderr)
        sound.export(out, format='mp3', bitrate=VOICE_MP3_BITRATE)
        return {"no_speech": True, "original_dur": original_dur, "cleaned_dur": original_dur,
                "removed_dur": 0.0, "chunks": 0, "pauses_kept": 0}

    # B2.13 — classify deliberate dynamic-pause gaps (resize to the target length) up front.
    gaps = []
    _prev = 0
    for (cs, ce) in nonsilent:
        if cs > _prev:
            gaps.append((_prev, cs))
        _prev = ce
    dyn_gaps = load_dynamic_pause_gaps(pause_map, scene_map, gaps) if pause_target_ms > 0 else set()
    if dyn_gaps:
        print(f"  Dynamic pauses: {len(dyn_gaps)} pause(s) set to {pause_target_ms}ms", file=sys.stderr)

    combined      = AudioSegment.empty()
    prev_end_ms   = 0
    kept_pauses   = 0
    # (original_ms, new_ms) checkpoints at every segment boundary — lets us remap any
    # original-timeline timestamp (scene_audio_map's start/end) to its post-strip position.
    remap_points  = [(0, 0)]

    for (cs, ce) in nonsilent:
        gap_s, gap_e = prev_end_ms, cs
        if gap_e > gap_s:
            if dyn_gaps and (gap_s, gap_e) in dyn_gaps:
                # deliberate [pause]: replace the gap with an EXACT target-length silence
                combined += AudioSegment.silent(duration=pause_target_ms)
                kept_pauses += 1
            elif protected and _rs_overlaps(gap_s, gap_e, protected):
                combined += sound[gap_s:gap_e]
                kept_pauses += 1
            else:
                pad = min(keep, gap_e - gap_s)
                if pad > 0:
                    combined += sound[gap_e - pad:gap_e]
        remap_points.append((cs, len(combined)))
        combined   += sound[cs:ce]
        remap_points.append((ce, len(combined)))
        prev_end_ms = ce

    if prev_end_ms < total_ms:
        if protected and _rs_overlaps(prev_end_ms, total_ms, protected):
            combined += sound[prev_end_ms:total_ms]
    remap_points.append((total_ms, len(combined)))

    cleaned_dur  = len(combined) / 1000.0
    removed_dur  = original_dur - cleaned_dur
    combined.export(out, format='mp3', bitrate=VOICE_MP3_BITRATE)
    pause_note   = f", {kept_pauses} pause(s) preserved" if kept_pauses else ""
    print(f"Output: {cleaned_dur:.2f}s  ({len(nonsilent)} chunk(s), "
          f"-{removed_dur:.2f}s removed{pause_note})  -> {os.path.basename(out)}", file=sys.stderr)

    if scene_map and os.path.exists(scene_map):
        _remap_scene_audio_map(scene_map, remap_points, cleaned_dur)

    return {"no_speech": False, "original_dur": original_dur, "cleaned_dur": cleaned_dur,
            "removed_dur": removed_dur, "chunks": len(nonsilent), "pauses_kept": kept_pauses}


def cmd_remove_silences(argv):
    """
    argv: [input, output, thresh, min_sil, keep, beat_map, timeline, scene_map,
           pause_map, pause_target_ms]. Thin wrapper over remove_silences_core.
    """
    inp         = argv[0] if len(argv) > 0 else "raw_voice.mp3"
    out         = argv[1] if len(argv) > 1 else "optimized_voice.mp3"
    thresh      = float(argv[2]) if len(argv) > 2 else -40.0
    min_sil     = int(argv[3])   if len(argv) > 3 else 400
    keep        = int(argv[4])   if len(argv) > 4 else 100
    beat_map    = argv[5]        if len(argv) > 5 else ''
    timeline    = argv[6]        if len(argv) > 6 else ''
    scene_map   = argv[7]        if len(argv) > 7 else ''
    pause_map   = argv[8]        if len(argv) > 8 else ''          # B2.13 dynamic pauses
    pause_target_ms = int(argv[9]) if (len(argv) > 9 and str(argv[9]).strip().lstrip('-').isdigit()) else 0

    try:
        remove_silences_core(inp, out, thresh, min_sil, keep, beat_map, timeline,
                             scene_map, pause_map, pause_target_ms)
    except FileNotFoundError as e:
        print(f"Error: {e} not found.", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


def _remap_scene_audio_map(scene_map_path, remap_points, cleaned_dur_s):
    """
    Rewrites scene_audio_map.json's per-scene start/end/duration to reflect
    the post-silence-strip audio, using remap_points (sorted original_ms ->
    new_ms checkpoints from cmd_remove_silences) with linear interpolation
    between checkpoints. Without this, Phase 5 render reads pre-strip
    boundaries against post-strip audio and falls back to a blind global
    rescale instead of accurate per-scene timing.
    """
    try:
        with open(scene_map_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"  [remap] could not read {scene_map_path}: {e} — skipping remap.", file=sys.stderr)
        return

    scenes = data.get("scenes", [])
    if not scenes:
        return

    def remap_ms(orig_ms):
        orig_ms = orig_ms * 1000.0
        for i in range(len(remap_points) - 1):
            o0, n0 = remap_points[i]
            o1, n1 = remap_points[i + 1]
            if o0 <= orig_ms <= o1:
                if o1 == o0:
                    return n0
                frac = (orig_ms - o0) / (o1 - o0)
                return n0 + frac * (n1 - n0)
        # Past the last checkpoint (shouldn't happen — last point is total_ms)
        return remap_points[-1][1]

    for sc in scenes:
        new_start = remap_ms(sc["start"]) / 1000.0
        new_end   = remap_ms(sc["end"]) / 1000.0
        sc["start"]    = round(new_start, 3)
        sc["end"]      = round(new_end, 3)
        sc["duration"] = round(max(0.0, new_end - new_start), 3)

    data["total_duration"] = round(cleaned_dur_s, 3)
    data["silence_stripped"] = True

    with open(scene_map_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    print(f"  [remap] scene_audio_map.json boundaries updated for post-strip audio "
          f"({len(scenes)} scene(s))", file=sys.stderr)


# ══════════════════════════════════════════════════════════════════════════════
#  SUBCOMMAND: assemble
#  (was assemble_video.py)
#  Images + audio → master_output.mp4; writes scene_timings.json + scene_safezones.json
# ══════════════════════════════════════════════════════════════════════════════

# ── moviepy / PIL lazy imports (paid once at subcommand dispatch) ─────────────
def _assemble_imports():
    global ImageClip, AudioFileClip, ColorClip, CompositeVideoClip, \
           concatenate_videoclips, np, _PILImage, _LANCZOS, _BILINEAR, ProgressBarLogger
    try:
        from moviepy import (ImageClip, AudioFileClip, ColorClip,
                             CompositeVideoClip, concatenate_videoclips)
    except ImportError:
        from moviepy.editor import (ImageClip, AudioFileClip, ColorClip,
                                    CompositeVideoClip, concatenate_videoclips)
    try:
        from proglog import ProgressBarLogger
    except ImportError:
        # Very old moviepy bundles its own copy under moviepy.Editor.tools —
        # degrade to no live encode progress rather than failing the render.
        class ProgressBarLogger:
            def __init__(self): self.bars = {}
            def bars_callback(self, *a, **kw): pass
    import numpy as np
    from PIL import Image as _PILImage
    try:
        _LANCZOS = _PILImage.Resampling.LANCZOS
        _BILINEAR = _PILImage.Resampling.BILINEAR
    except AttributeError:
        _LANCZOS = _PILImage.LANCZOS
        _BILINEAR = _PILImage.BILINEAR


# ── compat shims ──────────────────────────────────────────────────────────────
def _with_duration(clip, d):
    return clip.with_duration(d) if hasattr(clip, "with_duration") else clip.set_duration(d)

def _with_audio(clip, a):
    return clip.with_audio(a) if hasattr(clip, "with_audio") else clip.set_audio(a)

def _with_position(clip, p):
    return clip.with_position(p) if hasattr(clip, "with_position") else clip.set_position(p)

def _resized(clip, **kw):
    return clip.resized(**kw) if hasattr(clip, "resized") else clip.resize(**kw)

def _fl_compat(clip, func):
    return clip.transform(func) if hasattr(clip, "transform") else clip.fl(func)


# ── aspect ratio / size ───────────────────────────────────────────────────────
def _parse_aspect(ar):
    if not ar:
        return (9.0, 16.0)
    parts = str(ar).split(":")
    if len(parts) != 2:
        return (9.0, 16.0)
    try:
        w, h = float(parts[0].strip()), float(parts[1].strip())
    except ValueError:
        return (9.0, 16.0)
    return (w, h) if w > 0 and h > 0 else (9.0, 16.0)


def _target_size(ar, short_edge=1080):
    # Visual Quality tier (720/1080/1440) = the "p" number = the SHORT edge, like every other video
    # tool: "1080p" 16:9 -> 1920x1080, "1080p" 9:16 -> 1080x1920. Aspect still comes from the format.
    #
    # B2.18 FIX: B2.5 used this value as the LONG dimension (it replaced a hardcoded 1920), so every
    # render silently dropped to 1080x608 (16:9) / 608x1080 (9:16) — far below the tier's label.
    # MUST stay identical to Get-DimsForAspect (visuals.ps1) so images and video agree exactly.
    try:
        short_edge = int(short_edge)
    except (TypeError, ValueError):
        short_edge = 1080
    if short_edge < 2:
        short_edge = 1080
    rw, rh = _parse_aspect(ar)
    ratio  = rw / rh
    if ratio >= 1:
        th = short_edge; tw = int(round(th * ratio))   # landscape: short edge = height
    else:
        tw = short_edge; th = int(round(tw / ratio))   # portrait:  short edge = width
    if tw % 2: tw += 1
    if th % 2: th += 1
    return (max(2, tw), max(2, th))


# ── brief helpers ─────────────────────────────────────────────────────────────
def _scene_energy(brief, n, role_by_scene=None):
    # B2.3 — energy is inferred from Director 1's role (video_map) when available;
    # a hit/reveal/proof scene reads as high energy, a pause as low. Falls back to a
    # legacy brief's per-scene energy / pacing_map, then 'medium'.
    role_by_scene = role_by_scene or {}
    role = role_by_scene.get(n)
    if role:
        if role in ('hit', 'reveal', 'proof'):
            return 'high'
        if role in ('pause',):
            return 'low'
        return 'medium'
    for sc in brief.get('scenes', []):
        if sc.get('scene') == n:
            return sc.get('energy', 'medium')
    return 'high' if n in brief.get('pacing_map', []) else 'medium'


def _scene_beat(brief, n, beat_by_scene=None):
    # B2.3 — beat comes from Director 1's video_map (beat_by_scene) first, then a
    # legacy brief's per-scene beat / hit_scenes / pause_scenes, then 'none'.
    beat_by_scene = beat_by_scene or {}
    b = beat_by_scene.get(n)
    if b and b != 'NONE':
        return b
    for sc in brief.get('scenes', []):
        if sc.get('scene') == n:
            return sc.get('beat', 'none').upper()
    if n in brief.get('hit_scenes', []):
        return 'HIT'
    if n in brief.get('pause_scenes', []):
        return 'PAUSE'
    return 'none'


# ── safe-zone analysis ────────────────────────────────────────────────────────
_GRID = 3
_ZONE_NAMES = [
    "top-left", "top-center", "top-right",
    "center-left", "center", "center-right",
    "bottom-left", "bottom-center", "bottom-right",
]

def _cell_busyness(cell):
    if cell.size == 0:
        return 0.0
    std  = float(np.std(cell))
    gx   = np.diff(cell, axis=1)
    gy   = np.diff(cell, axis=0)
    edge = (float(np.mean(np.abs(gx))) + float(np.mean(np.abs(gy)))) if gx.size and gy.size else 0.0
    return std + edge * 2.0


def _analyze_safezone(image_path, downscale=64):
    try:
        img = _PILImage.open(image_path).convert("L")
        img = img.resize((downscale, downscale), _LANCZOS)
        arr = np.asarray(img, dtype=np.float32)
    except Exception:
        return {"safe_zone": "bottom-center", "busy_zone": "center", "scores": {}}
    h, w = arr.shape
    ch, cw = h // _GRID, w // _GRID
    scores = {}
    for r in range(_GRID):
        for c in range(_GRID):
            y0, y1 = r * ch, (r + 1) * ch if r < _GRID - 1 else h
            x0, x1 = c * cw, (c + 1) * cw if c < _GRID - 1 else w
            scores[_ZONE_NAMES[r * _GRID + c]] = round(_cell_busyness(arr[y0:y1, x0:x1]), 2)
    return {
        "safe_zone": min(scores, key=scores.get),
        "busy_zone": max(scores, key=scores.get),
        "scores": scores,
    }


# ── Ken Burns ─────────────────────────────────────────────────────────────────
# ── easing curves ─────────────────────────────────────────────────────────────
# Motion that eases reads as intentional cinematography; linear motion reads as a
# mechanical slide. The AI Director picks an ease per shot (B2.0); 'linear' keeps
# the pre-B2.0 behavior exactly for the blind-fallback path.
def _ease(name, t):
    # t in [0,1] -> eased [0,1]
    if name == "linear":
        return t
    if name == "ease_in":
        return t * t
    if name == "ease_out":
        return 1.0 - (1.0 - t) * (1.0 - t)
    if name == "ease_in_out":
        return (2.0 * t * t) if t < 0.5 else (1.0 - ((-2.0 * t + 2.0) ** 2) / 2.0)
    # ease_out_cubic — the default "settle" curve (fast start, gentle landing)
    return 1.0 - (1.0 - t) ** 3


def _apply_ken_burns(clip, scale_from=1.0, scale_to=1.04, zoom_in=True,
                     pan=(0.0, 0.0), ease="linear", focus=(0.0, 0.0)):
    """
    Per-frame move on a still image. Supports:
      - eased scale (ease name; 'linear' == pre-B2.0 behavior),
      - true PAN: pan=(dx,dy) as fractions of the frame the crop window travels
        across the whole shot (e.g. (0.06,0) drifts right by 6% of width),
      - SUBJECT-AWARE FOCUS (B1.15.4): focus=(fx,fy) in [-1,1] biases the crop
        CENTER toward the subject (from scene_safezones.json's busy_zone) so a
        zoom keeps the subject in frame instead of drifting off a centered crop.
    The crop is always at least as large as the target so panning never exposes
    an edge; scale is bumped up if the requested pan would need more headroom.
    Blind-fallback callers pass pan=(0,0), ease='linear', focus=(0,0) ->
    identical old centered output.
    """
    duration = clip.duration
    w, h     = clip.size
    if not zoom_in:
        scale_from, scale_to = scale_to, scale_from
    dx, dy = pan
    fx, fy = focus
    fx = max(-1.0, min(1.0, fx)); fy = max(-1.0, min(1.0, fy))
    # Ensure enough oversize that the pan never runs off the resized image:
    # the crop can shift by |dx|*w / |dy|*h total, so the smallest scale used
    # must leave that much margin on each axis.
    min_scale = max(scale_from, scale_to)
    need_x = 1.0 + abs(dx)
    need_y = 1.0 + abs(dy)
    pan_scale = max(need_x, need_y)
    base_scale = max(min_scale, pan_scale)
    # How far the focus bias may shift the crop toward the subject: a fraction of
    # the available headroom, so it never pushes the crop off the image.
    _FOCUS_STRENGTH = 0.5

    def move_frame(get_frame, t):
        p     = _ease(ease, min(1.0, t / duration)) if duration else 0.0
        scale = (scale_from + (scale_to - scale_from) * p) * (base_scale / min_scale if min_scale else 1.0)
        frame = get_frame(t)
        img   = _PILImage.fromarray(frame)
        nw, nh = int(round(w * scale)), int(round(h * scale))
        # B2.9 — BILINEAR (not LANCZOS) for the per-frame Ken Burns resize: on moving
        # video the quality difference is invisible, but bilinear is markedly cheaper per
        # frame. This is the moviepy fallback path only (the ffmpeg engine uses zoompan).
        img   = img.resize((nw, nh), _BILINEAR)
        # travel the crop window from centered-start toward the pan target
        max_left = nw - w
        max_top  = nh - h
        cx = max_left / 2.0
        cy = max_top / 2.0
        # subject-aware bias: shift the resting center toward the busy zone
        cx += fx * _FOCUS_STRENGTH * (max_left / 2.0)
        cy += fy * _FOCUS_STRENGTH * (max_top / 2.0)
        left = int(round(cx + (dx * w) * (p - 0.5)))
        top  = int(round(cy + (dy * h) * (p - 0.5)))
        left = max(0, min(max_left, left))
        top  = max(0, min(max_top,  top))
        return np.array(img.crop((left, top, left + w, top + h)))
    return _fl_compat(clip, move_frame)


# ── atmosphere / color grade (B2.0) ───────────────────────────────────────────
# A cheap-but-huge perceived-quality lift: a per-video colour LOOK (warm/cool/contrast).
# All local numpy/PIL, no new deps. grade=None -> no-op (blind-fallback path).
# NOTE (B2.15): grade VIGNETTE + GRAIN were removed. B2.16: the app no longer generates
# procedural scene filters at all — filters are external, human-made assets (see the
# "scene FILTERS (B2.16)" block below). The grade is colour LOOK only.
_GRADE_PRESETS = {
    # (r_gain, g_gain, b_gain, contrast, saturation)
    "none":     (1.00, 1.00, 1.00, 1.00, 1.00),
    "warm":     (1.06, 1.01, 0.94, 1.05, 1.06),
    "cool":     (0.95, 1.00, 1.08, 1.05, 1.02),
    "cinematic":(1.03, 0.99, 1.02, 1.12, 0.96),   # teal-orange-ish, punchy
    "noir":     (1.00, 1.00, 1.00, 1.18, 0.30),   # near-mono, high contrast
    "vibrant":  (1.02, 1.02, 1.02, 1.08, 1.22),
    "soft":     (1.02, 1.00, 1.01, 0.94, 1.02),   # gentle, low-contrast
}

def _apply_grade(clip, grade):
    """
    grade: dict {look}. Applies the per-video COLOUR look only (channel gains ->
    contrast -> saturation). B2.15: vignette/grain removed (they're a FILTER thing now).
    """
    look = (grade.get("look") or "none") if isinstance(grade, dict) else "none"
    rg, gg, bg_, contrast, sat = _GRADE_PRESETS.get(look, _GRADE_PRESETS["none"])
    if look == "none":
        return clip

    def grade_frame(get_frame, t):
        f = get_frame(t).astype(np.float32)
        # channel gains
        f[:, :, 0] *= rg; f[:, :, 1] *= gg; f[:, :, 2] *= bg_
        # contrast around mid-grey
        if contrast != 1.0:
            f = (f - 128.0) * contrast + 128.0
        # saturation (toward luma)
        if sat != 1.0:
            luma = (0.299 * f[:, :, 0] + 0.587 * f[:, :, 1] + 0.114 * f[:, :, 2])[:, :, None]
            f = luma + (f - luma) * sat
        return np.clip(f, 0, 255).astype(np.uint8)
    return _fl_compat(clip, grade_frame)


# ── scene FILTERS (B2.16 — external, human-made) ─────────────────────────────────
# The app NO LONGER generates procedural filters. Human-made filters live in
# Assets/Filters/, described by Assets/Filters/Filters.metadata
# (name -> plain-language description [-> asset file]). Director 2 selects filters
# by NAME within the user's bounds (Settings > AI Graphics > Filters:
# intensity + allow/block whitelists).
#
# The on-disk FILTER ASSET format is NOT finalised yet — the chosen direction is an
# ffmpeg "-vf" filter-string snippet per filter. Until assets exist, the resolver
# returns None and scene filters are a clean NO-OP (video renders as if unset).
_FILTER_CATALOG = None   # lazily loaded {name_lower: {"name","desc","vf_file"}}

def _filters_root():
    # Assets/ sits at the app root (parent of python/). Overridable for tests.
    return os.environ.get("AVG_FILTERS_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Assets", "Filters")

def load_filter_catalog(force=False):
    """Parse Assets/Filters/Filters.metadata into {name_lower: {...}}. Tolerant of
    'name | desc | file', 'name: desc', and 'name = desc'. Empty dict if absent."""
    global _FILTER_CATALOG
    if _FILTER_CATALOG is not None and not force:
        return _FILTER_CATALOG
    catalog = {}
    meta = os.path.join(_filters_root(), "Filters.metadata")
    try:
        with open(meta, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                name = desc = vf_file = ""
                if "|" in line:
                    parts = [p.strip() for p in line.split("|")]
                    name = parts[0]
                    if len(parts) > 1: desc = parts[1]
                    if len(parts) > 2: vf_file = parts[2]
                elif ":" in line or "=" in line:
                    sep = ":" if (":" in line and (("=" not in line) or line.index(":") < line.index("="))) else "="
                    name, desc = [p.strip() for p in line.split(sep, 1)]
                else:
                    name = line
                if name:
                    catalog[name.lower()] = {"name": name, "desc": desc, "vf_file": vf_file}
    except FileNotFoundError:
        pass
    except Exception as ex:
        print(f"  [filters] could not read Filters.metadata ({ex})", file=sys.stderr)
    _FILTER_CATALOG = catalog
    return catalog

def scene_filter_vf(name, intensity=100):
    """RETIRED (B2.18.3). Filters are no longer per-scene ffmpeg -vf text snippets baked into the
    base render — they are now ALPHA (ProRes 4444 .mov) VIDEO clips composited as windowed overlays on top
    of the finished video (see compose_filter_overlays). Always returns None so the base-render
    path applies no per-frame filter node (and never tries to read a .mov asset as a text -vf
    snippet, which would crash). The catalog's 3rd column is the overlay clip, read by the pass."""
    return None

def _apply_filter(clip, filt, low_resource=False):
    """B2.16 — moviepy compat path. External filters are ffmpeg '-vf' snippets that
    can't be applied to an in-memory moviepy clip, so scene filters are skipped on
    the moviepy fallback engine (the ffmpeg engine is where filters are applied).
    Kept as a no-op shim to preserve _fit_image_clip's call site. No-op."""
    return clip


# ── scene-boundary transitions (B1.15.1) ──────────────────────────────────────
# A/V SYNC IS SACRED: audio is one fixed track over a chain-concat of clips whose
# durations sum to the audio length. A real OVERLAPPING crossfade would shorten
# total video time and desync every scene after it. So scene transitions here are
# IN-CLIP ENTRANCE effects that play within the clip's OWN first fraction and
# NEVER change its duration — the video still feels edited, sync stays exact.
# 'cut' is a no-op fast path (identical to the old hard-cut behavior).
_TRANSITION_DUR = 0.45   # seconds of entrance, clamped to a fraction of the clip

def _apply_scene_transition(clip, kind, prev_last_frame=None):
    kind = (kind or "cut").lower()
    if kind == "cut":
        return clip
    duration = clip.duration
    if not duration or duration <= 0:
        return clip
    d = min(_TRANSITION_DUR, duration * 0.5)   # never eat more than half the clip
    if d <= 0:
        return clip
    w, h = clip.size

    # dip: briefly come up FROM a color (black) — like a fast dip-to-black in.
    # fade/crossfade: fade up from black over d (a soft in, sync-safe).
    # slide_left/right/up/down + whip: the frame slides into place over d.
    def trans_frame(get_frame, t):
        if t >= d:
            return get_frame(t)
        p = t / d                     # 0..1 across the entrance
        frame = get_frame(t)
        if kind in ("fade", "crossfade", "dip"):
            # ease the fade so it doesn't look linear/cheap
            f = 1.0 - (1.0 - p) ** 2
            return (frame.astype(np.float32) * f).astype(np.uint8)
        # slide/whip: shift the frame in from an edge, filling the gap with black
        out = np.zeros_like(frame)
        if kind in ("slide_left", "whip"):
            off = int(round((1.0 - p) * w))
            if off < w: out[:, :w - off] = frame[:, off:]
        elif kind == "slide_right":
            off = int(round((1.0 - p) * w))
            if off < w: out[:, off:] = frame[:, :w - off]
        elif kind == "slide_up":
            off = int(round((1.0 - p) * h))
            if off < h: out[:h - off, :] = frame[off:, :]
        elif kind == "slide_down":
            off = int(round((1.0 - p) * h))
            if off < h: out[off:, :] = frame[:h - off, :]
        else:
            return frame
        return out
    return _fl_compat(clip, trans_frame)


# ── clip builders ─────────────────────────────────────────────────────────────
_BLACK = None  # initialized in cmd_assemble after numpy import

def _fit_image_clip(image_path, duration, target_size, ken_burns=True,
                    zoom_in=True, kb_from=1.0, kb_to=1.04,
                    pan=(0.0, 0.0), ease="linear", focus=(0.0, 0.0), grade=None,
                    filt=None, low_resource=False):
    global _BLACK
    tw, th = target_size
    # Load as RGB (strip any alpha). An RGBA source makes CompositeVideoClip
    # build a full-frame float64 mask per clip (1.0*img[:,:,3]/255), which on
    # low-RAM machines with several parallel build workers exhausts memory
    # (numpy _ArrayMemoryError). Photos are opaque, so the mask is pure waste.
    _src = _PILImage.open(image_path)
    if _src.mode != "RGB":
        _src = _src.convert("RGB")
    raw  = _with_duration(ImageClip(np.asarray(_src)), duration)
    sw, sh = raw.size
    if (tw / sw) >= (th / sh):
        fitted = _resized(raw, width=tw)
    else:
        fitted = _resized(raw, height=th)
    bg = _with_duration(ColorClip(size=target_size, color=_BLACK), duration)
    composite = _with_duration(
        CompositeVideoClip([bg, _with_position(fitted, "center")], size=target_size),
        duration
    )
    if ken_burns:
        composite = _apply_ken_burns(composite, scale_from=kb_from, scale_to=kb_to,
                                     zoom_in=zoom_in, pan=pan, ease=ease, focus=focus)
    if grade:
        composite = _apply_grade(composite, grade)
    # B3.0/B2.7 — scene filter is the look layer (replaces grade on the default path).
    if filt:
        composite = _apply_filter(composite, filt, low_resource=low_resource)
    return composite


def _black_placeholder(duration, target_size):
    return _with_duration(ColorClip(size=target_size, color=_BLACK), duration)


# ── timeline / audio-map loaders ──────────────────────────────────────────────
def _load_timeline(path, scene_count):
    nodes = _load_json(path, [])
    if not nodes:
        nodes = [{"type": "scene", "scene": i + 1, "part": 0} for i in range(scene_count)]
    return nodes


def _load_audio_map(path):
    data = _load_json(path, {})
    if not data:
        return {}
    return {int(s["scene"]): s for s in data.get("scenes", [])}


# ── direction plan (B2.0 — Director 2 output) ──────────────────────────────────
# render/direction_plan.json is written by the Director 2 phase. Absent =
# blind-fallback (pre-B2.0 behavior), so old projects and failed-plan runs still
# render exactly as before. Shape: { version, grade:{look}, scenes:[{scene,
# motion:{type, intensity, ease}, transition_in}] }  (B2.15: grade is colour-look only;
# vignette/grain moved to the FILTERS feature.)
def _detect_total_ram_gb():
    """Total physical RAM in GB, or None if undetectable. Local only."""
    try:
        return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / (1024 ** 3)
    except (ValueError, AttributeError, OSError):
        pass
    try:
        import ctypes
        class _MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        _st = _MS(); _st.dwLength = ctypes.sizeof(_MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(_st))
        return _st.ullTotalPhys / (1024 ** 3)
    except Exception:
        return None


def _load_direction_plan(project_path):
    for rel in (os.path.join("render", "direction_plan.json"), "direction_plan.json"):
        data = _load_json(rel, None)
        if isinstance(data, dict) and data.get("scenes"):
            return {int(s["scene"]): s for s in data["scenes"] if "scene" in s}
    return {}


# Map a Director motion spec -> the (kb_from, kb_to, zoom_in, pan, ease) the
# compositor consumes. Intensity is normalized 0..1; the kb floor is well above
# the old flat 1.04 so even a "gentle" move actually reads on screen.
_MOTION_MIN_ZOOM = 1.06     # gentlest visible push (was 1.04 = invisible)
_MOTION_MAX_ZOOM = 1.16     # strongest push on a HIT
_MOTION_MAX_PAN  = 0.10     # max crop travel as a fraction of the frame

def _resolve_motion(spec):
    mtype     = (spec.get("type") or "zoom_in").lower()
    intensity = float(spec.get("intensity", 0.5))
    intensity = max(0.0, min(1.0, intensity))
    ease      = (spec.get("ease") or "ease_out_cubic").lower()
    zf = _MOTION_MIN_ZOOM + (_MOTION_MAX_ZOOM - _MOTION_MIN_ZOOM) * intensity
    pan_amt = _MOTION_MAX_PAN * (0.4 + 0.6 * intensity)
    kb_from, kb_to, zoom_in, pan = 1.0, zf, True, (0.0, 0.0)
    if mtype == "zoom_out":
        kb_from, kb_to, zoom_in = zf, 1.0, False
    elif mtype == "still":
        kb_from, kb_to, zoom_in, ease = 1.0, 1.0, True, "linear"
    elif mtype == "pan_left":
        pan = (-pan_amt, 0.0)
    elif mtype == "pan_right":
        pan = (pan_amt, 0.0)
    elif mtype == "pan_up":
        pan = (0.0, -pan_amt)
    elif mtype == "pan_down":
        pan = (0.0, pan_amt)
    # zoom_in is the default (any unrecognized type)
    return kb_from, kb_to, zoom_in, pan, ease


# ── subject-aware framing (B1.15.4, capability C6) ─────────────────────────────
# scene_safezones.json (computed in Phase 2) gives each scene a busy_zone — one of
# the 9 grid cells where the image is most active (i.e. where the subject is). We
# bias the Ken Burns crop CENTER toward that cell so zooms/pans keep the subject
# framed instead of drifting off a dead-centered crop. Pure consumption of data
# the app already produces; absent file => focus=(0,0) => old centered behavior.
_ZONE_FOCUS = {
    "top-left":     (-1.0, -1.0), "top-center":    (0.0, -1.0), "top-right":    (1.0, -1.0),
    "center-left":  (-1.0,  0.0), "center":        (0.0,  0.0), "center-right": (1.0,  0.0),
    "bottom-left":  (-1.0,  1.0), "bottom-center": (0.0,  1.0), "bottom-right": (1.0,  1.0),
}

def _load_safezones_map(project_path):
    for rel in (os.path.join("render", "scene_safezones.json"), "scene_safezones.json"):
        data = _load_json(rel, None)
        if isinstance(data, list) and data:
            out = {}
            for z in data:
                if isinstance(z, dict) and "scene" in z:
                    out[int(z["scene"])] = z
            if out:
                return out
    return {}

def _focus_for_zone(busy_zone):
    return _ZONE_FOCUS.get((busy_zone or "").lower(), (0.0, 0.0))


def cmd_assemble(argv):
    project_path  = argv[0] if len(argv) > 0 else "."
    fps           = int(argv[1])                           if len(argv) > 1 else 24
    codec         = argv[2]                                if len(argv) > 2 else "libx264"
    threads       = int(argv[3])                           if len(argv) > 3 else (os.cpu_count() or 4)
    aspect        = argv[4]                                if len(argv) > 4 else "9:16"
    ken_burns     = argv[5].lower() in ("1","true","yes") if len(argv) > 5 else False
    scene_count   = int(argv[6]) if (len(argv) > 6 and argv[6].strip().isdigit()) else None
    brief_path    = argv[7] if len(argv) > 7 else ''
    timeline_path = argv[8] if len(argv) > 8 else ''
    scene_map_p   = argv[9] if len(argv) > 9 else ''
    short_edge     = int(argv[10]) if (len(argv) > 10 and str(argv[10]).strip().isdigit()) else 1080
    kb_intensity  = argv[11] if len(argv) > 11 else 100          # B2.7 — integer percent 0..150
    kb_mode       = argv[12] if len(argv) > 12 else 'dynamic'    # B2.7 — dynamic|flat
    render_engine = argv[13] if len(argv) > 13 else 'auto'       # B2.9 — auto|ffmpeg|moviepy
    # B2.12 — Smart-Render opts as a JSON blob (argv14) for the child-process fallback path.
    render_opts   = {}
    if len(argv) > 14 and str(argv[14]).strip() not in ("", "-"):
        try:
            render_opts = json.loads(argv[14])
        except (ValueError, TypeError):
            render_opts = {}
    # B2.12 Stage 5 — warm-cache flag as a DEDICATED argv slot (argv15), never inside the
    # JSON blob: a mangled JSON arg (Start-Process quoting) must never silently drop the warm
    # flag and let a background prebuild render a full master_output.mp4 that races the real
    # render. A clean '1' here forces warm regardless of how the JSON survived quoting.
    if len(argv) > 15 and str(argv[15]).strip() == "1":
        render_opts["warm_cache_only"] = True

    render_project(project_path, fps, codec, threads, aspect, ken_burns,
                   scene_count, brief_path, timeline_path, scene_map_p, short_edge,
                   kb_intensity, kb_mode, render_engine, render_opts)
    sys.exit(0)


def _grade_cube_lut(look, out_path, n=33):
    """
    B2.4 Phase 3 — write a .cube 3D LUT reproducing _apply_grade's per-pixel COLOR
    transform EXACTLY (channel gains -> contrast around 128 -> saturation toward luma),
    so ffmpeg's lut3d matches moviepy by construction. Vignette and grain are SPATIAL
    (position-dependent) and are NOT in the LUT — the caller handles them separately
    (or bails). Same math/order as _apply_grade's grade_frame().
    """
    rg, gg, bg_, contrast, sat = _GRADE_PRESETS.get(look, _GRADE_PRESETS["none"])
    denom = n - 1
    lines = [f"LUT_3D_SIZE {n}", ""]
    for bi in range(n):
        for gi in range(n):
            for ri in range(n):          # .cube: RED varies fastest
                r = ri / denom * 255.0; g = gi / denom * 255.0; b = bi / denom * 255.0
                r *= rg; g *= gg; b *= bg_
                if contrast != 1.0:
                    r = (r - 128.0) * contrast + 128.0
                    g = (g - 128.0) * contrast + 128.0
                    b = (b - 128.0) * contrast + 128.0
                if sat != 1.0:
                    luma = 0.299 * r + 0.587 * g + 0.114 * b
                    r = luma + (r - luma) * sat
                    g = luma + (g - luma) * sat
                    b = luma + (b - luma) * sat
                lines.append("%.6f %.6f %.6f" % (
                    min(255.0, max(0.0, r)) / 255.0,
                    min(255.0, max(0.0, g)) / 255.0,
                    min(255.0, max(0.0, b)) / 255.0))
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def _ease_expr(name, t):
    """zoompan expression fragment for eased progress; t is a [0,1] expr string.
    Mirrors avg._ease()."""
    # NOTE: these fragments are always embedded inside a single-quoted zoompan option
    # (z='...'), so commas are protected by the quotes and must NOT be backslash-escaped.
    if name == "linear":     return t
    if name == "ease_in":    return f"(({t})*({t}))"
    if name == "ease_out":   return f"(1-(1-{t})*(1-{t}))"
    if name == "ease_in_out":return f"(if(lt({t},0.5),2*({t})*({t}),1-pow(-2*({t})+2,2)/2))"
    return f"(1-pow(1-{t},3))"   # ease_out_cubic (default)


_IMG_HASH_CACHE = {}   # path -> (mtime, size, sha1) so a segment's image is hashed once

def _file_sha1(path):
    """sha1 of a file's bytes, memoized by (mtime,size). '' if unreadable."""
    try:
        st = os.stat(path)
        key = (st.st_mtime_ns, st.st_size)
        hit = _IMG_HASH_CACHE.get(path)
        if hit and hit[0] == key:
            return hit[1]
        import hashlib
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        dig = h.hexdigest()
        _IMG_HASH_CACHE[path] = (key, dig)
        return dig
    except Exception:
        return ""


def _seg_cache_key(filter_body, fps, codec, img_path):
    """B2.12 Stage 4 — content hash of everything that determines a segment's pixels:
    the full filtergraph body (which already encodes kb/pan/ease/focus/filter/scale/fade/
    frames/base-res), the output fps, the encoder, and the source image bytes. A changed
    input can never collide with a stale segment."""
    import hashlib
    h = hashlib.sha1()
    h.update(str(filter_body).encode("utf-8", "ignore"))
    h.update(f"|fps={fps}|codec={codec}|img=".encode("ascii"))
    h.update((_file_sha1(img_path) if img_path else "noimg").encode("ascii"))
    return h.hexdigest()[:20]


def _sweep_cache(cache_dir, cap_bytes=1_500_000_000):
    """Keep the per-project segment cache under a size cap by deleting the least-recently
    used seg_*.mp4 first. Best-effort — any error is ignored (a full cache just means the
    next render re-encodes)."""
    try:
        entries = []
        total = 0
        for name in os.listdir(cache_dir):
            if not name.startswith("seg_") or not name.endswith(".mp4"):
                continue
            p = os.path.join(cache_dir, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            entries.append((st.st_mtime, st.st_size, p))
            total += st.st_size
        if total <= cap_bytes:
            return
        entries.sort()   # oldest mtime first
        for _mt, sz, p in entries:
            if total <= cap_bytes:
                break
            try:
                os.remove(p); total -= sz
            except OSError:
                pass
    except Exception:
        pass


def _bake_graded_images(project_path, scenes, image_by_scene, grade_lut_rel):
    """
    B2.12 Stage 1 — DO LESS PER FRAME. The colour grade (Director II look) is CONSTANT
    per scene, so bake it onto the source image ONCE (render/_graded/<scene>.png)
    instead of running lut3d on every output frame. We reuse the EXACT SAME grade .cube
    LUT the per-frame path uses, so parity is by construction apart from zoom-
    interpolation (grade(interp(x)) vs interp(grade(x)) — the documented LOW-MED delta).

    B2.16: scene filters are external ffmpeg -vf snippets now, applied per-frame by the
    caller (they're not constant-per-scene colour LUTs), so nothing filter-related is
    baked here — grade only.

    Best-effort: returns a {scene:int -> baked_image_path} dict on full success, or
    None if ANY needed bake fails (caller then keeps the per-frame lut3d path — zero
    regression). cwd is the project root; LUT paths are project-relative.
    """
    import subprocess
    if not grade_lut_rel:
        return {}   # nothing constant to bake — engine uses originals + no lut3d
    baked_dir = os.path.join("render", "_graded")
    os.makedirs(baked_dir, exist_ok=True)
    out = {}
    for e in scenes:
        sn = int(e["scene"])
        src = image_by_scene.get(sn)
        if not src:
            continue
        # grade colour LUT baked onto the source still.
        nodes = [f"lut3d=file='{grade_lut_rel}'"]
        dst = os.path.join(baked_dir, f"{sn:04d}.png")
        # -frames:v 1 -update 1 => single graded still. RGB PNG so zoompan gets the
        # same pixels the original decode would (source is RGB in the moviepy path too).
        cmd = ["ffmpeg", "-y", "-i", src, "-vf", ",".join(nodes),
               "-frames:v", "1", "-update", "1", dst]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
        except Exception as ex:
            print(f"  [render] grade-bake scene {sn} spawn failed ({ex}) — "
                  f"per-frame LUT path.", file=sys.stderr)
            return None
        if r.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) < 128:
            print(f"  [render] grade-bake scene {sn} failed — per-frame LUT path.\n"
                  f"{(r.stderr or '')[-200:]}", file=sys.stderr)
            return None
        out[sn] = dst
    return out


def _render_project_ffmpeg(project_path, scene_timings, image_by_scene, audio_rel,
                           target_sz, fps, codec, total_dur, low_resource,
                           base_fps_opt='auto', base_res_opt='auto', cache_opt=True,
                           planner_opt=True, warm_only=False):
    """
    B2.4 Phase 3 (OPT-IN via env AVG_RENDER_ENGINE=ffmpeg) — build master_output.mp4
    with a SINGLE ffmpeg filtergraph (per-scene zoompan Ken Burns + in-clip fades +
    concat + faithful lut3d grade) and one hardware encode, instead of moviepy's
    per-frame Python build. Driven entirely by the ALREADY-WRITTEN scene_timings
    annotations, so A/V sync (per-scene durations) is identical to the moviepy path.

    CONSERVATIVE: returns "unsupported" (caller -> moviepy) for anything it can't
    reproduce EXACTLY yet — directional pans, slide/whip transitions, and vignette or
    grain (spatial, position-dependent). What it DOES apply is exact-by-construction
    (LUT color grade) or the documented parity-risk (zoompan zoom easing, flagged).
    Returns True on success; raises on hard failure. cwd is the project root.
    """
    import subprocess
    tw, th = target_sz
    scenes = [e for e in scene_timings if e.get("type") == "scene"]
    if not scenes:
        return "unsupported"
    for e in scenes:
        if (e.get("transition_in") or "cut").lower() in (
                "slide_left", "slide_right", "slide_up", "slide_down", "whip"):
            return "unsupported"   # slide/whip entrances still owned by moviepy
        if int(e["scene"]) not in image_by_scene:
            return "unsupported"

    # grade (same dict on every scene entry): colour LOOK via LUT only. B2.15 — grade
    # vignette/grain removed (those effects belong to the B2.7 scene FILTERS now).
    grade = next((e["grade"] for e in scenes if e.get("grade")), None)
    look  = (grade.get("look") if isinstance(grade, dict) else None) or "none"

    render_dir = os.path.join(project_path, "render")
    os.makedirs(render_dir, exist_ok=True)
    # cwd is the project root (os.chdir above), so reference LUTs by RELATIVE path — no
    # Windows drive-colon to escape inside the filtergraph (that escaping was fragile).
    # B2.12 Stage 6 — LUT CACHE. The .cube is deterministic per look/filter name, so key the
    # filename by name and only (re)generate a missing one (the 33^3 write is pure overhead
    # on a re-render / when the look is unchanged). Small helper keeps the skip-if-exists rule.
    def _ensure_lut(rel_name, gen):
        p = os.path.join(render_dir, rel_name)
        if not (os.path.exists(p) and os.path.getsize(p) > 128):
            gen(p)
        return os.path.exists(p) and os.path.getsize(p) > 128

    lut_esc = None
    if look != "none":
        if _ensure_lut(f"_grade_{look}.cube", lambda p: _grade_cube_lut(look, p)):
            lut_esc = f"render/_grade_{look}.cube"

    # B2.16 — scene filters are external ffmpeg -vf snippets (Assets/Filters/), resolved
    # per scene and appended to that scene's chain below. No procedural filter LUTs.
    filt_vf_by_name = {}
    try:
        _filter_intensity = int(os.environ.get("AVG_FILTER_INTENSITY", "100"))
    except ValueError:
        _filter_intensity = 100
    for _fn in {str(e.get("filter")).lower() for e in scenes if e.get("filter")}:
        _vf = scene_filter_vf(_fn, _filter_intensity)
        if _vf:
            filt_vf_by_name[_fn] = _vf

    # B2.12 Stage 1 — bake the CONSTANT-per-scene grade colour onto the source images ONCE,
    # so the per-frame grade lut3d node disappears from the zoompan chain (the big per-frame
    # win). baked = {scene:path} on success; None on any failure (keep per-frame lut3d path —
    # zero regression). External filter -vf snippets stay per-frame nodes below either way.
    baked   = _bake_graded_images(project_path, scenes, image_by_scene, lut_esc)
    bake_ok = baked is not None
    if bake_ok:
        print(f"  [render] grade baked into {len(baked)} image(s) — "
              f"no per-frame lut3d.", file=sys.stderr)

    # B2.10 PERF / B2.12 Stage 2 — BASE-LAYER GENERATION fps. The zoompan Ken Burns is a
    # per-frame CPU filter, so cost scales linearly with frame count. At the user's output
    # fps (often 60) a gentle still-image push wastes half the frames — 60fps buys nothing
    # visible on a near-static slideshow. Build the base at base_fps, then let the ENCODER
    # re-time to the requested output fps (-r fps duplicates frames): the file's nominal fps
    # is unchanged but the filter does ~half the work. Durations are untouched (frames =
    # dur*gfps => segment still lasts `dur`), so A/V sync is identical. render.base_fps:
    # 'auto' (default) = min(fps,30); 'match' = the output fps; an int = that rate (<=fps).
    _bfo = str(base_fps_opt or 'auto').lower()
    if _bfo == 'match':
        gfps = fps
    elif _bfo.isdigit():
        gfps = max(1, min(int(_bfo), fps))
    else:                              # 'auto'
        gfps = fps if fps <= 30 else 30

    # B2.12 Stage 2 — BASE-LAYER RESOLUTION. The sources are tiny (~312px); rendering the
    # zoompan straight to 1080 upscales a small image full-res every frame for nothing. With
    # base_res='auto' (default) we run the zoompan at a source-matched res (min(output_long,
    # max_source_long * headroom)) and scale each segment UP to the output res once, cheaply,
    # AFTER the expensive per-frame work. 'full' = today's behaviour (zoompan at output res).
    # (In Stage 3 the crisp captions/overlays composite over this cheap base at full res/fps.)
    bw, bh = tw, th
    if str(base_res_opt or 'auto').lower() != 'full':
        try:
            _out_long = max(tw, th)
            _src_long = 0
            for _e in scenes:
                _p = image_by_scene.get(int(_e["scene"]))
                if _p and os.path.exists(_p):
                    with _PILImage.open(_p) as _im:
                        _src_long = max(_src_long, max(_im.size))
            if _src_long > 0:
                _HEADROOM = 2                 # keep some detail for the zoom crop
                _base_long = min(_out_long, int(_src_long) * _HEADROOM)
                if _base_long < _out_long:
                    _r = _base_long / float(_out_long)
                    bw = max(2, (int(round(tw * _r)) // 2) * 2)
                    bh = max(2, (int(round(th * _r)) // 2) * 2)
        except Exception as _ex:
            print(f"  [render] base_res auto probe failed ({_ex}) — full-res base.",
                  file=sys.stderr)
            bw, bh = tw, th
    if (bw, bh) != (tw, th):
        print(f"  [render] base layer {bw}x{bh}@{gfps}fps -> output {tw}x{th}@{fps}fps.",
              file=sys.stderr)

    # Build per-segment pieces ONCE, then assemble EITHER a single giant filtergraph
    # (fast; one encode) OR one ffmpeg pass per segment (flat memory; see B2.11 below).
    # Each renderable segment records its own input args (None for a takeover's black
    # source) and its filter `body` — for a scene that's the chain AFTER the input
    # label (so the input index can be substituted per mode), for a takeover it's the
    # whole `color=` source. Same body either way, so per-scene output is identical.
    segs = []   # each: {"input": [...] or None, "body": "<filter chain>"}
    for e in scene_timings:
        dur = float(e["duration"])
        if dur <= 0:
            continue
        frames = max(1, int(round(dur * gfps)))
        if e.get("type") == "takeover":
            segs.append({"input": None,
                         "body": f"color=c=black:s={tw}x{th}:r={gfps}:d={dur:.3f},"
                                 f"format=rgb24,setsar=1"})
        else:
            # B2.11 CORRECTNESS — feed zoompan a SINGLE decoded frame. zoompan emits its
            # `d=` frames PER INPUT FRAME, so the old `-loop 1 -t dur` (which decodes dozens
            # of frames of the same still) multiplied the output ~30-75x: a 3s scene became
            # 90-225s of duplicated zoom. Across 44 scenes that ballooned the filtergraph's
            # frame count into the OOM / "Invalid data found" territory this engine kept
            # hitting. A bare `-i img` decodes one frame; zoompan's d/s/fps set the length
            # (dur = frames/gfps), so the segment lasts exactly `dur` — verified 3.000s.
            _sn = int(e["scene"])
            seg_input = ["-i", (baked.get(_sn) if bake_ok else None) or image_by_scene[_sn]]
            kb_from = float(e.get("kb_from", 1.0)); kb_to = float(e.get("kb_to", 1.04))
            s0, s1 = (kb_from, kb_to) if bool(e.get("zoom_in", True)) else (kb_to, kb_from)
            t = f"(on/{max(1, frames - 1)})"
            zexpr = f"{s0:.5f}+({(s1 - s0):.5f})*{_ease_expr((e.get('ease') or 'ease_out_cubic').lower(), t)}"
            fx = float(e.get("focus_x", 0.0)); fy = float(e.get("focus_y", 0.0))
            dx = float(e.get("pan_x", 0.0)); dy = float(e.get("pan_y", 0.0))   # B2.9 pan
            # B2.10 PERF — supersample 2x (was 4x). 4x turned a 1920-wide output into a
            # 7680x4320 intermediate: that EXCEEDS h264_qsv's max surface (QSV failed and
            # fell through) AND the per-frame zoompan crawled at ~4fps. 2x (3840x2160) stays
            # within encoder limits and is plenty to hide zoompan's zoom-rounding jitter for
            # the gentle flat push, at ~4x less filter work. Never supersample below the
            # source's own size (tiny composite tiles) — that would just blur.
            # B2.12 Stage 2 — do the expensive per-frame zoompan at the BASE resolution
            # (bw x bh), supersampled 2x for clean zoom edges, then scale the finished
            # segment UP to the output res (tw x th) once. When base==output this is a
            # no-op and the graph is byte-identical to B2.11.
            ssw, ssh = bw * 2, bh * 2
            pt = f"(({t})-0.5)"   # -0.5..0.5 across the shot -> linear pan drift
            xexpr = f"iw/2-(iw/zoom/2)+({fx:.4f})*0.25*iw*(1-1/zoom)+({dx:.4f})*(iw/zoom)*{pt}"
            yexpr = f"ih/2-(ih/zoom/2)+({fy:.4f})*0.25*ih*(1-1/zoom)+({dy:.4f})*(ih/zoom)*{pt}"
            chain = (f"scale={ssw}:{ssh}:force_original_aspect_ratio=increase,"
                     f"crop={ssw}:{ssh},"
                     f"zoompan=z='{zexpr}':x='{xexpr}':y='{yexpr}':d={frames}:s={bw}x{bh}:fps={gfps},"
                     f"format=rgb24,setsar=1")
            if (bw, bh) != (tw, th):     # cheap upscale to output res AFTER the zoompan
                chain += f",scale={tw}:{th}:flags=bicubic"
            # B2.16 — scene filter is an EXTERNAL ffmpeg -vf snippet (Assets/Filters/),
            # appended to this scene's chain. filt_vf_by_name is empty until filter assets
            # exist, so with none present this is a clean no-op (renders as if unset).
            _flt = str(e.get("filter")).lower() if e.get("filter") else None
            if _flt and _flt in filt_vf_by_name:
                chain += "," + filt_vf_by_name[_flt]
            if (e.get("transition_in") or "cut").lower() in ("fade", "dip", "crossfade"):
                fd = min(0.45, dur * 0.5)
                chain += f",fade=t=in:st=0:d={max(0.01, fd):.3f}"
            segs.append({"input": seg_input, "body": chain})

    if not segs:
        return "unsupported"

    # Grade colour (LUT) applied uniformly post-concat. B2.15 — grade vignette/grain removed
    # (those are per-scene FILTER effects now, added inside each scene's chain above).
    tail_nodes = []
    if lut_esc and not bake_ok:        # B2.12 Stage 1 — grade colour baked into images
        tail_nodes.append(f"lut3d=file='{lut_esc}'")

    def _run_single(v_codec, v_preset):
        # input 0 = external audio; then one still-image input per SCENE (in order).
        inputs = ["ffmpeg", "-y", "-i", audio_rel]
        seg_filters, seg_labels = [], []
        in_idx = 1   # input 0 is the audio
        for i, s in enumerate(segs):
            lbl = f"[s{i}]"
            if s["input"] is None:
                seg_filters.append(s["body"] + lbl)
            else:
                inputs += s["input"]
                seg_filters.append(f"[{in_idx}:v]{s['body']}{lbl}")
                in_idx += 1
            seg_labels.append(lbl)
        graph = ";".join(seg_filters)
        graph += f";{''.join(seg_labels)}concat=n={len(seg_labels)}:v=1:a=0[cat]"
        graph += f";[cat]{''.join(n + ',' for n in tail_nodes)}format=yuv420p[vout]"
        c = (inputs + ["-filter_complex", graph, "-map", "[vout]", "-map", "0:a",
                       "-c:a", "aac", "-r", str(fps)]
             + _h264_quality_args(v_codec, v_preset) + ["master_output.mp4"])
        return subprocess.run(c, capture_output=True, text=True)

    def _run_segmented(v_codec, v_preset, cache_dir=None, warm_only=False):
        # B2.11 — render ONE ffmpeg pass per segment (peak RAM = one scene's zoompan),
        # then stream-concat and mux the external audio. Mirrors the moviepy low-RAM
        # SEGMENTED path. `-an` per segment; audio is muxed once at the end.
        # B2.12 Stage 4 — CONTENT-ADDRESSED CACHE. When cache_dir is set, each segment's
        # encoded mp4 is stored under a content hash of everything that determines its
        # pixels (filtergraph body + tail + fps + codec + image bytes). A matching file is
        # REUSED (no re-encode), so a re-render only pays for the scenes that actually
        # changed — O(changed scenes) instead of O(whole video). A cache miss just encodes.
        import gc, shutil
        work_dir = os.path.join("render", "_ffseg")   # cwd is the project root
        shutil.rmtree(work_dir, ignore_errors=True)
        os.makedirs(work_dir, exist_ok=True)
        seg_store = cache_dir or work_dir
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        seg_tail = "".join("," + n for n in tail_nodes)   # leading comma to append
        cur_codec, cur_preset = v_codec, v_preset
        seg_files, total = [], len(segs)
        hits = 0
        for i, s in enumerate(segs):
            body = ("[0:v]" + s["body"]) if s["input"] else s["body"]
            fc = f"{body}{seg_tail},format=yuv420p[v]"
            if cache_dir:
                _img = s["input"][1] if s["input"] else None
                key = _seg_cache_key(fc, fps, cur_codec, _img)
                seg_path = os.path.join(cache_dir, f"seg_{key}.mp4")
                if os.path.exists(seg_path) and os.path.getsize(seg_path) >= 1024:
                    seg_files.append(seg_path); hits += 1
                    os.utime(seg_path, None)   # touch — keep hot entries from being swept
                    step = ((40 + int((i + 1) / max(1, total) * 50)) // 5) * 5
                    _write_progress(project_path, min(90, step),
                                    f"Reusing scene {i + 1}/{total}")
                    continue
            else:
                seg_path = os.path.join(seg_store, f"seg_{i:04d}.mp4")

            def _one(vc, vp, _sp=seg_path, _fc=fc, _s=s):
                cmd = ["ffmpeg", "-y"]
                if _s["input"]:
                    cmd += _s["input"]
                cmd += (["-filter_complex", _fc, "-map", "[v]", "-r", str(fps), "-an"]
                        + _h264_quality_args(vc, vp) + [_sp])
                return subprocess.run(cmd, capture_output=True, text=True)

            r = _one(cur_codec, cur_preset)
            if r.returncode != 0 and cur_codec != "libx264":
                print(f"  [render] segment {i}: {cur_codec} failed — libx264 for the "
                      f"remaining segments.\n{r.stderr[-300:]}", file=sys.stderr)
                cur_codec, cur_preset = "libx264", "veryfast"
                if cache_dir:      # re-key under the codec that actually encodes it
                    _img = s["input"][1] if s["input"] else None
                    seg_path = os.path.join(cache_dir,
                                            f"seg_{_seg_cache_key(fc, fps, cur_codec, _img)}.mp4")
                r = _one(cur_codec, cur_preset, _sp=seg_path)
            if r.returncode != 0:
                return r
            seg_files.append(seg_path)
            gc.collect()
            step = ((40 + int((i + 1) / max(1, total) * 50)) // 5) * 5
            _write_progress(project_path, min(90, step), f"Encoding scene {i + 1}/{total}")

        if cache_dir:
            print(f"  [render] segment cache: {hits}/{total} reused, "
                  f"{total - hits} encoded.", file=sys.stderr)
            _sweep_cache(cache_dir)

        # B2.12 Stage 5 — WARM-ONLY: segments are now encoded + cached; the real render
        # (which hits this cache) does the concat/mux. Return a synthetic success without
        # producing master_output.mp4 so a background prebuild never races the final render.
        if warm_only:
            class _Ok:  # mimic subprocess.CompletedProcess enough for the caller's checks
                returncode = 0
                stderr = ""
            return _Ok()

        seg_dir = work_dir
        list_path = os.path.join(seg_dir, "concat.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            for sp in seg_files:
                f.write("file '" + os.path.abspath(sp).replace("\\", "/") + "'\n")
        _write_progress(project_path, 92, "Joining scenes...")
        concat_v = os.path.join(seg_dir, "concat.mp4")
        r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
                            "-c", "copy", concat_v], capture_output=True, text=True)
        if r.returncode != 0:   # params mismatch — re-encode the join once
            print(f"  [render] stream-copy concat failed — re-encoding join.\n{r.stderr[-300:]}",
                  file=sys.stderr)
            r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path]
                               + _h264_quality_args(cur_codec, cur_preset) + [concat_v],
                               capture_output=True, text=True)
            if r.returncode != 0:
                return r
        _write_progress(project_path, 96, "Muxing audio...")
        r = subprocess.run(["ffmpeg", "-y", "-i", concat_v, "-i", audio_rel,
                            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                            "-c:a", "aac", "-shortest", "master_output.mp4"],
                           capture_output=True, text=True)
        shutil.rmtree(seg_dir, ignore_errors=True)
        return r

    global _HW_ENCODER_CACHE
    _write_progress(project_path, 40, "Encoding video...")
    v_codec, v_preset = _detect_hw_encoder(codec)

    # B2.12 Stage 4 — when the segment cache is on (default), the CACHED SEGMENTED path is
    # primary: it reuses unchanged scenes across re-renders (O(changed scenes)) and keeps
    # peak RAM at one scene (right for the 8GB box, where the giant single-pass filtergraph
    # OOMs anyway). Cold cache pays a normal segmented render once. On failure it falls to
    # single-pass, then the caller drops to moviepy. cache off => the B2.11 single-pass-first
    # strategy (fastest cold render on RAM-rich machines), unchanged.
    cache_dir = os.path.join("render", "_cache") if cache_opt else None

    # B2.12 Stage 7 — RENDER PLANNER / MANIFEST. One line, before any pixels, tying the
    # Stage 0-6 decisions together (encoder x per-layer fps/res x grade-bake x predicted
    # cache reuse) so the render's choices are visible. Predicts reuse by counting how many
    # segment keys already exist in the cache — the same keys _run_segmented will look up.
    if planner_opt:
        reuse = 0
        seg_tail_pred = "".join("," + n for n in tail_nodes)
        if cache_dir and os.path.isdir(cache_dir):
            for s in segs:
                body = ("[0:v]" + s["body"]) if s["input"] else s["body"]
                fc = f"{body}{seg_tail_pred},format=yuv420p[v]"
                _img = s["input"][1] if s["input"] else None
                kp = os.path.join(cache_dir, f"seg_{_seg_cache_key(fc, fps, v_codec, _img)}.mp4")
                if os.path.exists(kp) and os.path.getsize(kp) >= 1024:
                    reuse += 1
        _base_str = (f"{bw}x{bh}@{gfps}" if (bw, bh) != (tw, th) or gfps != fps
                     else "output-res")
        _grade_str = "baked" if bake_ok else ("per-frame" if lut_esc else "none")
        _cache_str = (f"{reuse}/{len(segs)} cached" if cache_dir else "off")
        print(f"  [render] plan: {v_codec} {tw}x{th}@{fps} | base {_base_str} | "
              f"grade {_grade_str} | cache {_cache_str} | {len(segs)} segs", file=sys.stderr)

    # B2.12 Stage 5 — WARM-ONLY prebuild: just populate the segment cache (encode each
    # scene into render/_cache) and return, so a background worker can pre-pay the render
    # during the TTS/transcription network waits. Never writes master_output.mp4.
    if warm_only:
        if not cache_dir:
            return True   # nothing to warm without a cache
        result = _run_segmented(v_codec, v_preset, cache_dir=cache_dir, warm_only=True)
        if result.returncode != 0 and v_codec != "libx264":
            result = _run_segmented("libx264", "veryfast", cache_dir=cache_dir, warm_only=True)
        return True   # best-effort: any miss just means the real render encodes it

    if cache_dir:
        print(f"  [render] ffmpeg engine: {len(segs)} segment(s), grade={look} [{v_codec}], "
              f"cached-segmented...", file=sys.stderr)
        result = _run_segmented(v_codec, v_preset, cache_dir=cache_dir)
        if result.returncode != 0 and v_codec != "libx264":
            print(f"  [render] {v_codec} failed — libx264 cached-segmented.\n"
                  f"{result.stderr[-400:]}", file=sys.stderr)
            _HW_ENCODER_CACHE = ("libx264", "veryfast")
            v_codec, v_preset = "libx264", "veryfast"
            result = _run_segmented(v_codec, v_preset, cache_dir=cache_dir)
        if result.returncode != 0:
            print(f"  [render] cached-segmented failed — trying single-pass.\n"
                  f"{result.stderr[-400:]}", file=sys.stderr)
            result = _run_single(v_codec, v_preset)
    else:
        # Single pass first (one encode = fastest). If it fails — e.g. a filtergraph edge
        # case or memory pressure from all N zoompan chains at once on a low-RAM box —
        # retry SEGMENTED (one ffmpeg pass per scene), the same guard the moviepy path has.
        print(f"  [render] ffmpeg engine: {len(segs)} segment(s), grade={look} [{v_codec}], "
              f"single-pass...", file=sys.stderr)
        result = _run_single(v_codec, v_preset)
        if result.returncode != 0 and v_codec != "libx264":
            print(f"  [render] {v_codec} failed — libx264 fallback.\n{result.stderr[-400:]}",
                  file=sys.stderr)
            _HW_ENCODER_CACHE = ("libx264", "veryfast")
            v_codec, v_preset = "libx264", "veryfast"
            result = _run_single(v_codec, v_preset)
        if result.returncode != 0:
            print(f"  [render] single-pass failed — retrying segmented (flat memory).\n"
                  f"{result.stderr[-400:]}", file=sys.stderr)
            result = _run_segmented(v_codec, v_preset)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg render failed:\n{result.stderr[-800:]}")
    if not os.path.exists("master_output.mp4") or os.path.getsize("master_output.mp4") < 1024:
        raise RuntimeError("ffmpeg render produced no output file")
    return True


class _SceneTimingsError(Exception):
    """Fatal timings condition (e.g. every timeline scene dropped) — caller decides
    whether to sys.exit (render) or return a job failure (standalone timings phase)."""


def compute_scene_timings(timeline, audio_map, total_dur, brief,
                          beat_by_scene, role_by_scene, filter_by_scene,
                          plan_by_scene, safezone_by_scene, kb_intensity, kb_mode):
    """
    B2.12 Stage 0 — PURE per-scene/takeover timing + Ken-Burns/grade/filter annotation.
    Extracted verbatim from render_project so it can run in a lightweight phase BEFORE
    Render (the reorder that lets AI Graphics + Captions plan without waiting on a frame).
    Returns (scene_timings, takeover_count, skipped_scenes). Raises _SceneTimingsError if
    every timeline scene dropped (the empty-render class of failure). Warnings go to stderr.
    """
    scene_timings  = []
    t              = 0.0
    takeover_count = 0
    skipped_scenes = []
    for node in timeline:
        if node.get("type") == "scene":
            sn = int(node.get("scene", 0))
            if sn not in audio_map:
                skipped_scenes.append(sn)
                continue
            m   = audio_map[sn]
            dur = float(m["duration"])
            scene_timings.append({
                "type": "scene", "scene": sn,
                "start": round(t, 3), "end": round(t + dur, 3), "duration": round(dur, 3),
            })
            t += dur
        elif node.get("type") == "takeover":
            dur = float(node.get("duration_sec", 1.8))
            scene_timings.append({
                "type": "takeover", "id": node.get("id", ""),
                "headline": node.get("headline", ""), "sub": node.get("sub", ""),
                "start": round(t, 3), "end": round(t + dur, 3), "duration": round(dur, 3),
            })
            t += dur
            takeover_count += 1

    scene_node_count = sum(1 for nd in timeline if nd.get("type") == "scene")
    kept_scene_count = sum(1 for e in scene_timings if e["type"] == "scene")
    if skipped_scenes:
        print(f"  Warning: {len(skipped_scenes)} of {scene_node_count} timeline "
              f"scene(s) have no scene_audio_map.json entry and were dropped "
              f"(scenes {skipped_scenes[:10]}{'...' if len(skipped_scenes) > 10 else ''}). "
              f"audio-map scenes present: {sorted(audio_map.keys())[:10]}"
              f"{'...' if len(audio_map) > 10 else ''}.", file=sys.stderr)
    if scene_node_count > 0 and kept_scene_count == 0:
        raise _SceneTimingsError(
            "every timeline scene was dropped — timeline scene numbers do not match "
            "scene_audio_map.json. Nothing to render. (Re-run Voice & Audio, or start a "
            "fresh video if this is a resumed/legacy project.)")

    timeline_total = t
    if timeline_total > 0 and abs(timeline_total - total_dur) > 0.05:
        scale = total_dur / timeline_total
        print(f"  Warning: timeline total ({timeline_total:.2f}s) != audio duration "
              f"({total_dur:.2f}s) — rescaling by {scale:.4f}x.", file=sys.stderr)
        t = 0.0
        for entry in scene_timings:
            dur = entry["duration"] * scale
            entry["start"] = round(t, 3)
            entry["duration"] = round(dur, 3)
            t += dur
            entry["end"] = round(t, 3)

    # Annotate Ken Burns / grade / filter decisions.
    for entry in scene_timings:
        if entry["type"] != "scene":
            continue
        i      = entry["scene"]
        energy = _scene_energy(brief, i, role_by_scene)
        beat   = _scene_beat(brief, i, beat_by_scene)
        zoom_in = beat not in ('PAUSE', 'SLOWDOWN') and energy != 'low'
        if beat == 'HIT':
            kb_from, kb_to = 1.0, 1.08
        elif energy == 'high':
            kb_from, kb_to = 1.0, 1.05
        elif beat in ('PAUSE', 'SLOWDOWN') or energy == 'low':
            kb_from, kb_to = 1.04, 1.0; zoom_in = False
        else:
            kb_from, kb_to = 1.0, 1.04
        pan, ease, grade, transition_in = (0.0, 0.0), "linear", None, "cut"
        p = plan_by_scene.get(i)
        if p:
            if p.get("motion"):
                kb_from, kb_to, zoom_in, pan, ease = _resolve_motion(p["motion"])
            if p.get("grade"):
                grade = p["grade"]
            transition_in = (p.get("transition_in") or "cut").lower()
        if kb_mode == 'flat':
            kb_from, kb_to, zoom_in, pan, ease = 1.0, 1.03, True, (0.0, 0.0), 'ease_out'
        if kb_intensity != 1.0:
            kb_from = 1.0 + (kb_from - 1.0) * kb_intensity
            kb_to   = 1.0 + (kb_to   - 1.0) * kb_intensity
        focus = (0.0, 0.0)
        sz = safezone_by_scene.get(i)
        if sz and (pan == (0.0, 0.0)):
            focus = _focus_for_zone(sz.get("busy_zone", ""))
        entry.update({"beat": beat, "energy": energy, "zoom_in": zoom_in,
                      "kb_from": kb_from, "kb_to": kb_to,
                      "pan_x": pan[0], "pan_y": pan[1], "ease": ease,
                      "focus_x": focus[0], "focus_y": focus[1],
                      "grade": grade, "transition_in": transition_in,
                      "filter": filter_by_scene.get(i)})
    return scene_timings, takeover_count, skipped_scenes


def _read_valid_scene_timings(project_path, timeline, audio_map, total_dur):
    """B2.12 Stage 0 — idempotent read. If a scene_timings.json already exists (written
    by the standalone timings phase for THIS render) and is consistent with the current
    audio (same kept-scene count + total duration within tolerance), reuse it verbatim so
    Render matches exactly what AI Graphics/Captions already planned against. Returns the
    list on a good match, else None (recompute). Never raises."""
    path = os.path.join(project_path, "render", "scene_timings.json")
    try:
        if not os.path.exists(path):
            return None
        data = _load_json(path, None)
        if not isinstance(data, list) or not data:
            return None
        want_scenes = sum(1 for nd in timeline if nd.get("type") == "scene"
                          and int(nd.get("scene", 0)) in audio_map)
        got_scenes  = sum(1 for e in data if e.get("type") == "scene")
        if got_scenes != want_scenes:
            return None
        end = max((float(e.get("end", 0)) for e in data), default=0.0)
        if total_dur > 0 and abs(end - total_dur) > 0.15:
            return None
        # require the annotations render depends on
        if any(e.get("type") == "scene" and "kb_from" not in e for e in data):
            return None
        return data
    except Exception:
        return None


def _ffprobe_duration(path):
    """Media duration in seconds via ffprobe, or 0.0 if unavailable. No moviepy needed."""
    import subprocess
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of",
                            "default=noprint_wrappers=1:nokey=1", path],
                           capture_output=True, text=True)
        return float((r.stdout or "").strip())
    except Exception:
        return 0.0


def build_scene_timings_file(project_path, aspect_ratio="9:16", timeline_path='',
                             scene_map_path='', brief_path='', kb_intensity=100,
                             kb_mode='flat', short_edge=1080):
    """
    B2.12 Stage 0 — write render/scene_timings.json WITHOUT rendering a frame (no moviepy).
    Mirrors render_project's input loading, gets total duration from scene_audio_map's
    total_duration (or ffprobe of optimized_voice.mp3), and calls compute_scene_timings.
    Runs in the lightweight phase right after Director II so AI Graphics + Captions can
    plan before Render. Returns the entry count. Raises on a fatal timings condition.
    """
    prev = os.getcwd()
    os.chdir(project_path)
    try:
        index_rel = _resolve_input(os.path.join("render", "index.json"), "index.json")
        images = _load_json(index_rel, [])
        image_by_scene = {i + 1: img for i, img in enumerate(images) if os.path.exists(img)}
        n = len(image_by_scene)

        brief = _load_json(brief_path, {})
        _video_map = _load_json(os.path.join("script", "video_map.json"), {})
        beat_by_scene, role_by_scene, filter_by_scene = {}, {}, {}
        for _s in (_video_map.get("scenes", []) if isinstance(_video_map, dict) else []):
            try:
                _sn = int(_s.get("scene"))
            except (TypeError, ValueError):
                continue
            beat_by_scene[_sn] = str(_s.get("beat", "none")).upper()
            role_by_scene[_sn] = str(_s.get("role", "")).lower()
            _flt = _s.get("filter")
            if _flt and str(_flt).lower() in load_filter_catalog():
                filter_by_scene[_sn] = str(_flt).lower()

        timeline  = _load_timeline(timeline_path, n)
        audio_map = _load_audio_map(scene_map_path)
        plan_by_scene = _load_direction_plan(".")
        safezone_by_scene = _load_safezones_map(".")

        # total duration: prefer the audio map's own total, else ffprobe the strip output.
        total_dur = 0.0
        _raw_map = _load_json(scene_map_path or os.path.join("audio", "scene_audio_map.json"), {})
        if isinstance(_raw_map, dict):
            try:
                total_dur = float(_raw_map.get("total_duration", 0)) or 0.0
            except (TypeError, ValueError):
                total_dur = 0.0
        if total_dur <= 0:
            audio_rel = _resolve_input(os.path.join("audio", "optimized_voice.mp3"),
                                       "optimized_voice.mp3")
            total_dur = _ffprobe_duration(audio_rel)

        if not audio_map and n > 0 and total_dur > 0:
            base_dur = total_dur / n
            t = 0.0
            for i in range(1, n + 1):
                dur = base_dur if i < n else (total_dur - t)
                audio_map[i] = {"start": round(t, 3), "end": round(t + dur, 3),
                                "duration": round(dur, 3)}
                t += dur

        try:
            kbf = max(0.0, min(1.5, float(kb_intensity) / 100.0))
        except (TypeError, ValueError):
            kbf = 1.0
        kb_mode = 'flat'   # B2.10 — one motion; matches render_project's coercion

        scene_timings, _tc, _sk = compute_scene_timings(
            timeline, audio_map, total_dur, brief, beat_by_scene, role_by_scene,
            filter_by_scene, plan_by_scene, safezone_by_scene, kbf, kb_mode)
        os.makedirs("render", exist_ok=True)
        with open(os.path.join("render", "scene_timings.json"), "w") as f:
            json.dump(scene_timings, f, indent=2)
        return len(scene_timings)
    finally:
        os.chdir(prev)


def render_project(project_path=".", fps=24, codec="libx264", threads=None,
                   aspect_ratio="9:16", ken_burns=False, scene_count=None,
                   brief_path='', timeline_path='', scene_map_path='', short_edge=1080,
                   kb_intensity=100, kb_mode='dynamic', render_engine='auto',
                   render_opts=None):
    """
    Pure callable entry point (used by server.py's persistent process).
    Mirrors original assemble_video.py's render_project() signature exactly.
    May call sys.exit(1) on hard errors — server.py wraps the call in
    try/except SystemExit, same contract as the original.

    render_opts (B2.12): optional dict of Smart-Render knobs threaded from config.render
    ({base_fps, base_res, cache, prebuild, planner_log}). None/empty => plan defaults.
    """
    _assemble_imports()
    render_opts = render_opts or {}
    global _BLACK, _HW_ENCODER_CACHE
    _BLACK = np.array([0, 0, 0], dtype=np.uint8)

    if threads is None:
        threads = os.cpu_count() or 4

    # B2.7 — Ken Burns controls. kb_intensity arrives as an INTEGER PERCENT (0..150)
    # to dodge locale decimal-separator issues; convert to a fraction here. Unknown
    # mode => dynamic (the B2.0 beat-matched motion).
    try:
        kb_intensity = float(kb_intensity) / 100.0
    except (TypeError, ValueError):
        kb_intensity = 1.0
    kb_intensity = max(0.0, min(1.5, kb_intensity))
    # B2.10 — Ken Burns 'modes' removed. There is now ONE motion: the flat, gentle,
    # uniform push (the old 'dynamic' beat-matched motion felt exaggerated/messy). The
    # kb_mode arg is kept for arg-shape/back-compat but is always coerced to 'flat'.
    kb_mode = 'flat'

    os.chdir(project_path)

    # New subfolder layout, falling back to legacy root paths for pre-reorg
    # projects (mirrors PowerShell's Invoke-VideoRender resolution).
    audio_rel = _resolve_input(os.path.join("audio", "optimized_voice.mp3"), "optimized_voice.mp3")
    index_rel = _resolve_input(os.path.join("render", "index.json"), "index.json")
    for req in (audio_rel, index_rel):
        if not os.path.exists(req):
            print(f"Error: {req} not found.", file=sys.stderr)
            sys.exit(1)

    audio = AudioFileClip(audio_rel)
    images = _load_json(index_rel, [])
    image_by_scene = {i + 1: img for i, img in enumerate(images) if os.path.exists(img)}
    if not image_by_scene:
        print("Error: No valid images found in index.json.", file=sys.stderr)
        sys.exit(1)

    brief     = _load_json(brief_path, {})
    has_brief = bool(brief)
    # B2.3 — per-scene beat/role now live in video_map.json (Director 1), not the
    # brief. Build lookup dicts; the blind Ken Burns fallback (_scene_beat/energy)
    # prefers these, then a legacy brief, then defaults. Empty when absent.
    _video_map = _load_json(os.path.join(project_path, "script", "video_map.json"), {})
    beat_by_scene = {}
    role_by_scene = {}
    filter_by_scene = {}   # B2.7 — Director 1's per-scene filter (None when unset)
    for _s in (_video_map.get("scenes", []) if isinstance(_video_map, dict) else []):
        try:
            _sn = int(_s.get("scene"))
        except (TypeError, ValueError):
            continue
        beat_by_scene[_sn] = str(_s.get("beat", "none")).upper()
        role_by_scene[_sn] = str(_s.get("role", "")).lower()
        _flt = _s.get("filter")
        if _flt and str(_flt).lower() in load_filter_catalog():
            filter_by_scene[_sn] = str(_flt).lower()
    n         = len(image_by_scene)
    target_sz = _target_size(aspect_ratio, short_edge)
    total_dur = audio.duration

    timeline  = _load_timeline(timeline_path, n)
    audio_map = _load_audio_map(scene_map_path)
    plan_by_scene = _load_direction_plan(project_path)   # B2.0 — empty = blind fallback
    has_plan  = bool(plan_by_scene)
    safezone_by_scene = _load_safezones_map(project_path)  # C6 — empty = centered crop
    # Resource flag shared by the grain cost-guard (in _build_one) and the
    # build-worker cap below. Computed once here so _build_one can read it.
    _ram_gb = _detect_total_ram_gb()
    _low_resource = (_ram_gb is not None and _ram_gb <= 8.5)

    if not audio_map:
        base_dur = total_dur / n
        t = 0.0
        for i in range(1, n + 1):
            dur = base_dur if i < n else (total_dur - t)
            audio_map[i] = {"start": round(t, 3), "end": round(t + dur, 3), "duration": round(dur, 3)}
            t += dur
        print("  Warning: scene_audio_map.json not found — falling back to evenly-divided timing.", file=sys.stderr)

    # ── scene_timings.json ────────────────────────────────────────────────────
    # B2.12 Stage 0 — the timing/annotation math is now the pure compute_scene_timings;
    # if the standalone timings phase already wrote a consistent file for THIS render
    # (same kept-scene count + total duration), reuse it verbatim so Render matches what
    # AI Graphics/Captions planned against. Otherwise compute + write fresh.
    scene_timings = _read_valid_scene_timings(project_path, timeline, audio_map, total_dur)
    if scene_timings is not None:
        takeover_count = sum(1 for e in scene_timings if e.get("type") == "takeover")
        print("  [render] reusing existing render/scene_timings.json (Stage 0).",
              file=sys.stderr)
    else:
        try:
            scene_timings, takeover_count, _skipped = compute_scene_timings(
                timeline, audio_map, total_dur, brief, beat_by_scene, role_by_scene,
                filter_by_scene, plan_by_scene, safezone_by_scene, kb_intensity, kb_mode)
        except _SceneTimingsError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        # cwd is the project root (os.chdir above); ensure render/ exists — a
        # pre-reorg project resumed at Render has no render/ subfolder yet.
        os.makedirs("render", exist_ok=True)
        with open(os.path.join("render", "scene_timings.json"), "w") as f:
            json.dump(scene_timings, f, indent=2)

    # B2.4 Phase 3 (OPT-IN, env AVG_RENDER_ENGINE=ffmpeg) — native single-pass ffmpeg
    # render (zoompan Ken Burns + lut3d grade + in-clip fades + concat, one hw encode)
    # driven by the scene_timings just written (so A/V sync is identical). Bails to the
    # moviepy build below for pans / slide|whip / vignette|grain it can't match yet.
    # B2.9 — ON BY DEFAULT (render_engine 'auto'); any 'unsupported'/error => the
    # unchanged moviepy build runs. render_engine='moviepy' (or env) skips this entirely.
    # B2.12 Stage 5 — WARM-ONLY prebuild: populate the segment cache (no master written),
    # so a background worker can pre-pay the render during TTS/transcription. Only the ffmpeg
    # engine can warm; moviepy has no per-segment cache, so warm is a no-op there. Best-effort.
    _warm = bool(render_opts.get('warm_cache_only', False))
    if _warm:
        try:
            if _use_ffmpeg_engine(render_engine):
                _render_project_ffmpeg(project_path, scene_timings, image_by_scene,
                                       audio_rel, target_sz, fps, codec, total_dur,
                                       _low_resource,
                                       base_fps_opt=render_opts.get('base_fps', 'auto'),
                                       base_res_opt=render_opts.get('base_res', 'auto'),
                                       cache_opt=bool(render_opts.get('cache', True)),
                                       planner_opt=False, warm_only=True)
                print("  [render] prebuild warm-cache complete.", file=sys.stderr)
        except Exception as e:
            print(f"  [render] prebuild warm-cache skipped ({e}).", file=sys.stderr)
        return

    if _use_ffmpeg_engine(render_engine):
        try:
            if _render_project_ffmpeg(project_path, scene_timings, image_by_scene,
                                      audio_rel, target_sz, fps, codec, total_dur,
                                      _low_resource,
                                      base_fps_opt=render_opts.get('base_fps', 'auto'),
                                      base_res_opt=render_opts.get('base_res', 'auto'),
                                      cache_opt=bool(render_opts.get('cache', True)),
                                      planner_opt=bool(render_opts.get('planner_log', True))) is True:
                _write_progress(project_path, 100, "Render complete")
                print("Render complete: master_output.mp4 (ffmpeg engine).", file=sys.stderr)
                return
            print("  [render] ffmpeg engine unsupported for this plan "
                  "(slide|whip transition) — using moviepy.", file=sys.stderr)
        except Exception as e:
            print(f"  [render] ffmpeg engine failed ({e}) — falling back to moviepy.",
                  file=sys.stderr)

    # scene_safezones.json moved to Phase 2 (cmd_safezones) — Edit Plan needs
    # it before Render even runs now, so it's computed right after images are
    # generated instead of incidentally here.

    print(f"Render: {n} scene(s), {takeover_count} takeover(s)  |  "
          f"{target_sz[0]}x{target_sz[1]}  |  {total_dur:.1f}s  |  {fps}fps  |  {codec}  |  "
          f"threads={threads}  |  ken-burns={'on' if ken_burns else 'off'}  |  "
          f"brief={'yes' if has_brief else 'no'}  |  "
          f"director={'on' if has_plan else 'off (blind)'}", file=sys.stderr)

    # ── clip build ────────────────────────────────────────────────────────────
    # Prints here used to go to stdout, which corrupts server.py's one-JSON-
    # line-per-job protocol when running under the persistent server (stdout
    # is shared with the job result line). Progress now goes to progress.json
    # instead — PowerShell polls it for the live progress line. Build gets a
    # 0-40% budget, encode (moviepy's own callback below) gets 40-100%.
    #
    # Each entry's clip is independent (no shared state) — PIL resize/paste
    # releases the GIL during the heavy C code, so a thread pool sized to
    # `threads` gives a real speedup here, not just I/O-bound overlap.
    # ThreadPoolExecutor.map preserves input order, required since
    # concatenate_videoclips depends on clips being in scene_timings order.
    from concurrent.futures import ThreadPoolExecutor

    def _build_one(entry):
        dur = entry["duration"]
        if dur <= 0:
            return None
        if entry["type"] == "takeover":
            return _black_placeholder(dur, target_sz)
        i   = entry["scene"]
        img = image_by_scene.get(i)
        if not img:
            print(f"  Warning: scene {i} has no image — using black placeholder ({dur:.2f}s).", file=sys.stderr)
            return _black_placeholder(dur, target_sz)
        zoom_in = entry.get("zoom_in", True)
        kb_from = entry.get("kb_from", 1.0)
        kb_to   = entry.get("kb_to",   1.04)
        pan     = (entry.get("pan_x", 0.0), entry.get("pan_y", 0.0))
        ease    = entry.get("ease", "linear")
        focus   = (entry.get("focus_x", 0.0), entry.get("focus_y", 0.0))
        grade   = entry.get("grade")
        filt    = entry.get("filter")   # B2.7 — Director 1's scene filter (or None)
        # B2.0/B2.7 effects (motion/grade/filter) are per-clip and must NEVER be able
        # to fail the whole render — a bad value for ONE scene degrades that scene to
        # plain Ken Burns, it doesn't empty the clip list. Same skip-on-error
        # posture the overlay path already uses.
        try:
            built = _fit_image_clip(img, dur, target_sz, ken_burns=ken_burns,
                                    zoom_in=zoom_in, kb_from=kb_from, kb_to=kb_to,
                                    pan=pan, ease=ease, focus=focus, grade=grade,
                                    filt=filt, low_resource=_low_resource)
        except Exception as e:
            print(f"  Warning: scene {i} effects failed ({e}) — plain fit.", file=sys.stderr)
            built = _fit_image_clip(img, dur, target_sz, ken_burns=ken_burns,
                                    filt=filt, low_resource=_low_resource)
        # Scene-boundary entrance transition (B1.15.1). In-clip only — never
        # changes duration, so chain-concat + audio stay perfectly in sync.
        tin = entry.get("transition_in", "cut")
        if tin and tin != "cut":
            try:
                built = _apply_scene_transition(built, tin)
            except Exception as e:
                print(f"  Warning: scene {i} transition '{tin}' failed ({e}).", file=sys.stderr)
        return built

    _t0 = _time.time()

    # ── Memory-frugal SEGMENTED render (auto on low RAM) ──────────────────────
    # B2.5.1 — the standard path below builds EVERY scene clip into a list that
    # concatenate/write_videofile keeps alive at once (moviepy holds all N scene
    # images + per-frame numpy buffers → the multi-GB peak on low-RAM machines).
    # Segmented mode renders each timeline segment to its own temp file, RELEASES
    # it (close + gc) before the next, then stream-concats the segments and muxes
    # the audio — peak RAM = ONE segment instead of all N. Slower (one encode per
    # segment) but flat on memory. Best-effort: ANY failure falls through to the
    # standard path, so a low-RAM render can never be worse off than before.
    if _low_resource:
        try:
            import gc, shutil, subprocess
            seg_dir = os.path.join("_tmp", "segments")   # cwd is the project root
            shutil.rmtree(seg_dir, ignore_errors=True)
            os.makedirs(seg_dir, exist_ok=True)
            seg_codec, seg_preset = _detect_hw_encoder(codec)
            _gb_str = f"{_ram_gb:.1f} GB" if _ram_gb is not None else "low"
            print(f"  Low RAM ({_gb_str}) — segmented render (one scene at a time, flat memory).",
                  file=sys.stderr)
            seg_files = []
            total_segs = len(scene_timings)
            for si, entry in enumerate(scene_timings):
                if float(entry.get("duration", 0)) <= 0:
                    continue
                clip = _build_one(entry)
                if clip is None:
                    continue
                seg_path = os.path.join(seg_dir, f"seg_{si:04d}.mp4")

                def _seg_write(v_codec, v_preset):
                    kw = dict(fps=fps, codec=v_codec, preset=v_preset, audio=False, logger=None)
                    if v_codec == "libx264":
                        kw["threads"] = threads
                    clip.write_videofile(seg_path, **kw)

                try:
                    _seg_write(seg_codec, seg_preset)
                except Exception as e:
                    if seg_codec != "libx264":
                        print(f"  Segment {si}: {seg_codec} failed ({e}) — libx264.", file=sys.stderr)
                        seg_codec, seg_preset = "libx264", "veryfast"
                        _seg_write(seg_codec, seg_preset)
                    else:
                        try: clip.close()
                        except Exception: pass
                        raise
                try: clip.close()
                except Exception: pass
                del clip
                gc.collect()
                seg_files.append(seg_path)
                step = ((int(((si + 1) / max(1, total_segs)) * 90)) // 5) * 5
                _write_progress(project_path, step, f"Rendering scene {si + 1}/{total_segs}")

            if not seg_files:
                raise RuntimeError("no renderable segments")

            list_path = os.path.join(seg_dir, "concat.txt")
            with open(list_path, "w", encoding="utf-8") as f:
                for sp in seg_files:
                    f.write("file '" + os.path.abspath(sp).replace("\\", "/") + "'\n")

            _write_progress(project_path, 95, "Joining scenes...")
            concat_v = os.path.join(seg_dir, "concat.mp4")
            r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
                                "-c", "copy", concat_v], capture_output=True, text=True)
            if r.returncode != 0:
                # params mismatch across segments — re-encode the join once.
                print(f"  Stream-copy concat failed — re-encoding join.\n{r.stderr[-300:]}", file=sys.stderr)
                cc, cp = _detect_hw_encoder(codec)
                r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path]
                                   + _h264_quality_args(cc, cp) + [concat_v],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    raise RuntimeError(f"segment concat failed:\n{r.stderr[-500:]}")

            r = subprocess.run(["ffmpeg", "-y", "-i", concat_v, "-i", audio_rel,
                                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                                "-c:a", "aac", "-shortest", "master_output.mp4"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"segment audio mux failed:\n{r.stderr[-500:]}")

            try: audio.close()
            except Exception: pass
            shutil.rmtree(seg_dir, ignore_errors=True)
            _write_progress(project_path, 100, "Render complete")
            print(f"Render complete: master_output.mp4 (segmented, low-RAM)  "
                  f"(total: {int(_time.time() - _t0)}s)", file=sys.stderr)
            return
        except Exception as e:
            print(f"  Segmented render failed ({e}) — using the standard render instead.",
                  file=sys.stderr)
            # fall through to the standard build+encode path below (audio still open)

    build_workers = max(1, min(threads, len(scene_timings)))
    # Low-RAM guard: each in-flight build holds a full-frame RGB source plus
    # Ken Burns working buffers. On machines with little free memory, 4 parallel
    # 1080p builds can still OOM — throttle workers when total RAM is small.
    # Reuses _ram_gb/_low_resource computed once near the top of render_project.
    if _low_resource and build_workers > 2:
        build_workers = 2
        _gb_str = f"{_ram_gb:.1f} GB" if _ram_gb is not None else "low"
        print(f"  Low RAM ({_gb_str}) — capping build workers at 2.", file=sys.stderr)
    clips = []
    done  = 0
    last_pct_step = -1
    with ThreadPoolExecutor(max_workers=build_workers) as pool:
        for clip in pool.map(_build_one, scene_timings):
            if clip is not None:
                clips.append(clip)
            done += 1
            build_pct = int((done / n) * 40) if n else 40
            step = (build_pct // 5) * 5
            if step != last_pct_step:
                last_pct_step = step
                _write_progress(project_path, step, f"Building scenes ({done}/{n})")

    _write_progress(project_path, 40, "Encoding video...")
    # Guard: an empty clip list would make concatenate/encode produce a zero-
    # length or missing file in ~0s and look like a bogus "success" to the
    # server. Fail loudly instead so the real cause surfaces (e.g. all scene
    # durations <= 0, or timeline/audio-map scene numbers not matching images).
    if not clips:
        print(f"Error: no renderable clips were built from {len(scene_timings)} "
              f"timeline entr(ies) — check that scene durations > 0 and that "
              f"timeline scene numbers match generated images.", file=sys.stderr)
        sys.exit(1)
    video = _with_audio(concatenate_videoclips(clips, method="chain"), audio)
    enc_t0 = _time.time()

    class _EncodeProgressLogger(ProgressBarLogger):
        # Proglog hook — moviepy's real per-frame encode progress, not an
        # estimate. bars['t'] is the ffmpeg time-position bar write_videofile
        # registers internally.
        def __init__(self):
            super().__init__()
            self._last_step = 40

        def bars_callback(self, bar, attr, value, old_value=None):
            if bar != 't' or attr != 'index':
                return
            total = self.bars.get(bar, {}).get('total', 0)
            if not total:
                return
            frac = min(1.0, value / total)
            pct  = 40 + int(frac * 60)
            step = (pct // 5) * 5
            if step != self._last_step:
                self._last_step = step
                _write_progress(project_path, step, "Encoding video...")

    actual_codec, actual_preset = _detect_hw_encoder(codec)
    if actual_codec != codec:
        print(f"  Hardware encoder detected: {actual_codec} (preset={actual_preset}) — "
              f"replacing software {codec}", file=sys.stderr)

    write_kwargs = dict(fps=fps, codec=actual_codec, audio_codec="aac",
                        preset=actual_preset, logger=_EncodeProgressLogger())
    if actual_codec == "libx264":
        write_kwargs["threads"] = threads   # thread param only applies to software x264

    try:
        video.write_videofile("master_output.mp4", **write_kwargs)
    except Exception as e:
        if actual_codec == "libx264":
            raise
        # Hardware encoder was detected as present but failed at actual encode
        # time (GPU/driver issue, VM, headless) — fall back to software once,
        # and poison the cache so later renders this session skip straight to
        # libx264 instead of re-failing the same way every time.
        print(f"  Hardware encoder {actual_codec} failed ({e}) — falling back to libx264.",
              file=sys.stderr)
        _HW_ENCODER_CACHE = ("libx264", "faster")
        write_kwargs["codec"]  = "libx264"
        write_kwargs["preset"] = "faster"
        write_kwargs["threads"] = threads
        video.write_videofile("master_output.mp4", **write_kwargs)
    _write_progress(project_path, 100, "Render complete")
    print(f"Render complete: master_output.mp4  "
          f"(encode: {int(_time.time()-enc_t0)}s, total: {int(_time.time()-_t0)}s)",
          file=sys.stderr)


# ══════════════════════════════════════════════════════════════════════════════
#  SUBCOMMAND: overlay_render
#  (was overlay_render.py)
#  Composites AI-planned overlays + chapters chapters_takeover_cardss onto master_output.mp4
# ══════════════════════════════════════════════════════════════════════════════

FADE_SEC           = 0.4
ANIMATION_DURATION = 0.55
ANIMATION_FPS      = 12


def _or_imports():
    global VideoFileClip, VideoClip, ImageClip, CompositeVideoClip, np, _PILImage
    try:
        from moviepy import VideoFileClip, ImageClip, VideoClip, CompositeVideoClip
    except ImportError:
        from moviepy.editor import VideoFileClip, ImageClip, VideoClip, CompositeVideoClip
    import numpy as np
    from PIL import Image as _PILImage


def _apply_transition(clip, fade_in, fade_out, duration,
                       transition_in='fade', transition_out='fade'):
    if fade_in <= 0 and fade_out <= 0:
        return clip
    W, H = clip.size
    mask = clip.mask
    if mask is not None:
        def fade_factor(t):
            f = 1.0
            if fade_in > 0 and t < fade_in:
                f = min(f, t / fade_in if transition_in == 'fade'
                        else (t / fade_in) * 0.5 + 0.5)
            if fade_out > 0 and t > duration - fade_out:
                f = min(f, max(0.0, (duration - t) / fade_out if transition_out == 'fade'
                               else ((duration - t) / fade_out) * 0.5 + 0.5))
            return max(0.0, min(1.0, f))

        def mask_frame(get_frame, t):
            frame  = get_frame(t)
            factor = fade_factor(t)
            return frame if factor >= 1.0 else (frame * factor).astype(frame.dtype)

        new_mask = _fl_compat(mask, mask_frame)
        clip = clip.with_mask(new_mask) if hasattr(clip, "with_mask") else clip.set_mask(new_mask)

    needs_spatial = (transition_in  in ('pop', 'slide_left', 'slide_right') or
                     transition_out in ('shrink', 'slide_left', 'slide_right'))
    if needs_spatial:
        def spatial(get_frame, t):
            frame = get_frame(t)
            img   = _PILImage.fromarray(frame, 'RGBA' if frame.shape[2] == 4 else 'RGB')
            scale, x_off = 1.0, 0
            if fade_in > 0 and t < fade_in:
                p = t / fade_in; ease = 1 - (1 - p) ** 3
                if transition_in == 'pop':
                    scale = 0.85 + 0.15 * ease
                elif transition_in == 'slide_left':
                    x_off = int(-W * 0.15 * (1 - ease))
                elif transition_in == 'slide_right':
                    x_off = int(W * 0.15 * (1 - ease))
            elif fade_out > 0 and t > duration - fade_out:
                p = (t - (duration - fade_out)) / fade_out; ease = p ** 3
                if transition_out == 'shrink':
                    scale = 1.0 - 0.15 * ease
                elif transition_out == 'slide_left':
                    x_off = int(-W * 0.15 * ease)
                elif transition_out == 'slide_right':
                    x_off = int(W * 0.15 * ease)
            if scale != 1.0 or x_off != 0:
                mode = 'RGBA' if frame.shape[2] == 4 else 'RGB'
                if scale != 1.0:
                    ns = (int(W * scale), int(H * scale))
                    rs = img.resize(ns, _PILImage.Resampling.LANCZOS)
                    canvas = _PILImage.new(mode, (W, H), (0, 0, 0, 0))
                    px, py = (W - ns[0]) // 2, (H - ns[1]) // 2
                    canvas.paste(rs, (px, py), rs) if frame.shape[2] == 4 else canvas.paste(rs, (px, py))
                    img = canvas
                if x_off != 0:
                    canvas = _PILImage.new(mode, (W, H), (0, 0, 0, 0))
                    canvas.paste(img, (x_off, 0), img) if frame.shape[2] == 4 else canvas.paste(img, (x_off, 0))
                    img = canvas
            return np.array(img)
        clip = _fl_compat(clip, spatial)
    return clip


def _build_overlay_clip(spec, W, H, brand, overlay_dur, od):
    element = spec.get("element", "")
    if element not in od.ANIMATED_ELEMENTS:
        img = od.render_overlay(spec, W, H, brand)
        return img, None

    # Kinetic typography (B1.15.2): a "kinetic" spec spreads its word-by-word
    # reveal across MOST of the overlay's on-screen time instead of a fixed
    # ~0.55s entrance burst — so words appear roughly as they're spoken, matching
    # the narration instead of flying in fast then freezing. Reuses the exact
    # word-reveal renderer (render_text_emphasis); only the TIME WINDOW changes.
    # Non-kinetic overlays keep the snappy fixed entrance (unchanged behavior).
    if spec.get("kinetic"):
        # reveal over ~85% of the window, leaving a short held tail so the full
        # phrase sits complete before the overlay leaves.
        anim_dur = max(ANIMATION_DURATION, overlay_dur * 0.85)
    else:
        anim_dur = min(ANIMATION_DURATION, overlay_dur)
    final_img    = od.render_overlay(spec, W, H, brand, progress=1.0)
    final_arr    = np.array(final_img.convert("RGB"))
    final_mask   = np.array(final_img.convert("RGBA"))[:, :, 3] / 255.0
    _cache       = {"t": None, "img": None}

    def _at(t):
        if t >= anim_dur:
            return None
        if _cache["t"] != t:
            progress  = t / anim_dur if anim_dur > 0 else 1.0
            _cache["img"] = od.render_overlay(spec, W, H, brand, progress=progress)
            _cache["t"]   = t
        return _cache["img"]

    def make_frame(t):
        fi = _at(t)
        return final_arr if fi is None else np.array(fi.convert("RGB"))

    def make_mask(t):
        fi = _at(t)
        return final_mask if fi is None else np.array(fi.convert("RGBA"))[:, :, 3] / 255.0

    vc = VideoClip(make_frame, duration=overlay_dur)
    mc = VideoClip(make_mask,  duration=overlay_dur, is_mask=True)
    vc = vc.with_mask(mc) if hasattr(vc, "with_mask") else vc.set_mask(mc)
    vc.fps = ANIMATION_FPS
    return None, vc


def _parse_dynamic_options(path):
    sections, current = {}, None
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            m = re.match(r"^##\s*CATEGORY:\s*(.+)$", line)
            if m:
                current = m.group(1).strip().lower(); sections.setdefault(current, []); continue
            m = re.match(r"^##\s*OPTION:\s*(.+)$", line)
            if m and current:
                sections[current].append(m.group(1).strip())
    return sections


def _load_brand(dynamic_path, preset_name, od):
    brand = dict(od.DEFAULT_BRAND)
    if not os.path.exists(dynamic_path):
        return brand
    try:
        sections = _parse_dynamic_options(dynamic_path)
        options  = sections.get("brand_presets", [])
        chosen   = None
        for opt in options:
            parts = [p.strip() for p in opt.split("|", 2)]
            if len(parts) < 3:
                continue
            if not preset_name and chosen is None:
                chosen = parts[2]
            if parts[0] == preset_name:
                chosen = parts[2]; break
        if chosen:
            for kv in chosen.split(";"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    brand[k.strip()] = v.strip()
    except Exception as e:
        print(f"  [edit] Warning: brand load failed ({e}) — using defaults.", file=sys.stderr)
    return brand


def _apply_global_font(brand, global_font, od):
    """B2.19.3 — push the user's GLOBAL font (config.edit.font) into the brand dict so
    Takeover Cards use it.

    This was the missing half of B2.16: the global font was passed to the CAPTION path
    (make_ass_header's Fontname) but never to the card path, which read font_path straight
    off the brand preset in dynamic_options.txt. So picking a font changed the captions and
    left the cards on the preset default.

    The global font WINS over the preset: it is an explicit, per-user choice, while the
    preset's font_path is a packaged default. No-ops when unset or unresolvable, leaving
    today's preset behaviour exactly as it was.
    """
    if not global_font:
        return brand
    regular = global_font if os.path.exists(global_font) else od._resolve_font_name(global_font)
    if not regular:
        print(f"  [edit] Global font '{global_font}' not found — keeping the preset font.", file=sys.stderr)
        return brand
    brand["font_path"] = regular
    # Try for a real bold cut; PIL cannot synthesise one, so falling back to the regular
    # face is correct — a slightly light headline beats a completely different typeface.
    bold = None
    if not os.path.exists(global_font):
        for suffix in (" Bold", "-Bold", " bd", "bd", " Black", "-Black"):
            bold = od._resolve_font_name(global_font + suffix)
            if bold and bold != regular:
                break
            bold = None
    brand["font_bold_path"] = bold or regular
    print(f"  [edit] Global font: {os.path.basename(regular)}"
          f"{'' if bold else ' (no bold cut — using regular)'}", file=sys.stderr)
    return brand


def _load_safezones(project_path):
    data = _load_json(os.path.join(project_path, "render", "scene_safezones.json"), [])
    if not data:
        return {}
    return {int(z["scene"]): z for z in data}


_DEFAULT_PLACEMENT = "top-right"

def _resolve_placement(spec, scene_num, safezones):
    placement = (spec.get("placement") or "auto").strip().lower()
    if placement != "auto":
        return placement
    z = safezones.get(scene_num)
    if not z:
        return _DEFAULT_PLACEMENT
    mapping = {
        "top-left": "top-left",    "top-center": "top-left",    "top-right": "top-right",
        "center-left": "top-left", "center": _DEFAULT_PLACEMENT, "center-right": "top-right",
        "bottom-left": "bottom-left", "bottom-center": "bottom-left", "bottom-right": "bottom-right",
    }
    return mapping.get(z.get("safe_zone", ""), _DEFAULT_PLACEMENT)


def _ffprobe_video(video_path):
    """(width, height, duration_seconds) via ffprobe, or None on failure. B2.4."""
    import subprocess
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:format=duration",
             "-of", "json", video_path],
            capture_output=True, text=True, timeout=30)
        d = json.loads(r.stdout)
        st = d["streams"][0]
        return (int(st["width"]), int(st["height"]), float(d["format"]["duration"]))
    except Exception:
        return None


def _overlay_render_ffmpeg(project_path, overlays_spec, timings_by_scene,
                           takeover_timings, dynamic_path, brand_preset,
                           video_in, video_tmp, overlays_dir, defer=False, dims=None,
                           global_font=""):
    """
    B2.4 Phase 2 (OPT-IN via env AVG_RENDER_ENGINE=ffmpeg) — composite STATIC
    overlays + chapters_takeover_cards takeover cards via ONE ffmpeg overlay filtergraph + hardware
    encode, instead of moviepy decoding the whole base video frame-by-frame.

    CONSERVATIVE BY DESIGN: returns "unsupported" for any animated element, ride_motion,
    or non-fade transition, so the moviepy path stays the EXACT-parity reference for
    those. Returns True on success; raises on hard failure (caller falls back to
    moviepy). Default (env unset) never calls this — moviepy is byte-identical to
    pre-B2.4.
    """
    import subprocess
    import overlay_design as od

    # Bail to moviepy for anything we can't reproduce EXACTLY as a static fade overlay.
    for spec in overlays_spec:
        element = (spec.get("element") or "").strip().lower()
        if element in od.ANIMATED_ELEMENTS:
            return "unsupported"
        if spec.get("ride_motion"):
            return "unsupported"
        if (spec.get("transition_in") or "fade").lower() != "fade" or \
           (spec.get("transition_out") or "fade").lower() != "fade":
            return "unsupported"

    # B2.16 — windowed Takeover Cards (explainer/focused) also count as compositable content.
    _cp = _load_json(os.path.join(project_path, "render", "card_plan.json"), {})
    _has_cards = any(("start" in c and "end" in c)
                     for c in (_cp.get("cards", []) if isinstance(_cp, dict) else []))
    if not overlays_spec and not takeover_timings and not _has_cards:
        return "unsupported"   # empty plan — let moviepy own the skip/exit-2 path

    # B2.12 Stage 3 — dims may be injected (W,H,duration) so overlay ASSETS can be
    # rendered BEFORE the base video exists (reordered pipeline). Otherwise probe master.
    dims = dims or _ffprobe_video(video_in)
    if not dims:
        raise RuntimeError("ffprobe could not read master_output.mp4 dimensions")
    W, H, video_dur = dims

    brand     = _apply_global_font(_load_brand(dynamic_path, brand_preset, od), global_font, od)
    safezones = _load_safezones(project_path)
    os.makedirs(overlays_dir, exist_ok=True)

    # (png_path, start, dur, fade_in, fade_out) per layer, in draw order.
    layers = []

    for idx, spec in enumerate(overlays_spec, 1):
        scene = spec.get("scene")
        timing = timings_by_scene.get(int(scene)) if scene is not None else None
        if timing is None:
            print(f"  [edit] Overlay {idx}: scene {scene} not in timings — skipping.", file=sys.stderr)
            continue
        start = float(timing["start"])
        scene_dur = float(timing["duration"])
        overlay_dur = max(0.5, min(float(spec.get("duration", 5.0)), scene_dur, video_dur - start))
        if overlay_dur <= 0:
            continue
        rspec = dict(spec)
        rspec["placement"] = _resolve_placement(spec, int(scene), safezones)
        # full-frame RGBA, placement baked into the PNG (same as the moviepy path)
        od.render_overlay(rspec, W, H, brand).save(
            os.path.join(overlays_dir, f"overlay_{idx:02d}.png"))
        fade = min(FADE_SEC, overlay_dur / 3)
        layers.append((os.path.join(overlays_dir, f"overlay_{idx:02d}.png"),
                       start, overlay_dur, fade, fade))

    for t in takeover_timings:
        spec = {"element": "chapters_takeover_cards", "headline": t.get("headline", ""), "sub": t.get("sub", "")}
        try:
            img = od.render_overlay(spec, W, H, brand)
        except Exception as e:
            print(f"  [edit] Takeover {t.get('id','')}: render error ({e}) — skipping.", file=sys.stderr)
            continue
        start = float(t["start"]); dur = float(t["duration"])
        if dur <= 0 or start >= video_dur:
            continue
        dur = min(dur, video_dur - start)
        png = os.path.join(overlays_dir, f"takeover_{t.get('id','')}.png")
        img.save(png)
        fade = min(0.3, dur / 4)
        layers.append((png, start, dur, fade, fade))

    # B2.16 — full-screen Explainer/Focused Takeover Cards. The Captions phase refined each
    # card's window (start/end) + wrote its synced text into captions.ass, so here we only
    # composite the card BACKGROUND (text_via_ass => bg only) over that window. Instant cut
    # in/out (near-zero fade) per spec. Cards without a window haven't been refined yet
    # (Phase-5 asset pre-render pass) — skip them; Render composites once windows exist.
    card_plan = _load_json(os.path.join(project_path, "render", "card_plan.json"), {})
    for ci, card in enumerate((card_plan.get("cards", []) if isinstance(card_plan, dict) else [])):
        if "start" not in card or "end" not in card:
            continue
        cstart = float(card["start"]); cend = float(card["end"])
        cdur = cend - cstart
        if cdur <= 0 or cstart >= video_dur:
            continue
        cdur = min(cdur, video_dur - cstart)
        cspec = {"element": str(card.get("element", "focused")), "text_via_ass": True,
                 "accent": card.get("accent", "accent1")}
        try:
            cimg = od.render_overlay(cspec, W, H, brand)
        except Exception as e:
            print(f"  [edit] Card {ci}: render error ({e}) — skipping.", file=sys.stderr)
            continue
        cpng = os.path.join(overlays_dir, f"card_{ci:02d}.png")
        cimg.save(cpng)
        layers.append((cpng, cstart, cdur, 0.02, 0.02))   # instant cut in/out

    if not layers:
        return "unsupported"   # nothing composited — defer to moviepy for parity

    if defer:
        # B2.9 Stage 2 — SINGLE-PASS FINAL: don't encode now. Hand the rendered layer
        # PNGs + timings to the caption phase, which bakes overlays + captions in ONE
        # ffmpeg pass (saves a whole full-video re-encode). The PNGs are already on disk.
        spec = [{"png": p, "start": s, "dur": d, "fade_in": fi, "fade_out": fo}
                for (p, s, d, fi, fo) in layers]
        with open(os.path.join(project_path, "render", "_final_layers.json"), "w", encoding="utf-8") as f:
            json.dump(spec, f)
        print(f"  [edit] Deferred {len(layers)} overlay layer(s) to the caption pass "
              f"(single-pass final).", file=sys.stderr)
        return "deferred"

    # base video + one looped-still input per layer.
    cmd = ["ffmpeg", "-y", "-i", video_in]
    for (png, _s, _d, _fi, _fo) in layers:
        cmd += ["-loop", "1", "-t", f"{video_dur:.3f}", "-i", png]

    # each still is alpha-faded to its window (transparent outside it) then overlaid;
    # enable=between(...) skips compositing outside the window (cheap + matches look).
    filt = []
    cur = "[0:v]"
    for i, (png, start, dur, fi, fo) in enumerate(layers, start=1):
        end = start + dur
        fo_st = max(start, end - fo)
        filt.append(f"[{i}:v]format=rgba,"
                    f"fade=t=in:st={start:.3f}:d={max(0.001, fi):.3f}:alpha=1,"
                    f"fade=t=out:st={fo_st:.3f}:d={max(0.001, fo):.3f}:alpha=1[ov{i}]")
        nxt = f"[v{i}]"
        filt.append(f"{cur}[ov{i}]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})'{nxt}")
        cur = nxt
    filtergraph = ";".join(filt)

    def _run(v_codec, v_preset):
        c = (cmd + ["-filter_complex", filtergraph, "-map", cur, "-map", "0:a?",
                    "-c:a", "copy"] + _h264_quality_args(v_codec, v_preset) + [video_tmp])
        return subprocess.run(c, capture_output=True, text=True)

    global _HW_ENCODER_CACHE
    ov_codec, ov_preset = _detect_hw_encoder("libx264")
    print(f"  [edit] Compositing {len(layers)} overlay(s) via ffmpeg [{ov_codec}]...", file=sys.stderr)
    result = _run(ov_codec, ov_preset)
    if result.returncode != 0 and ov_codec != "libx264":
        print(f"  [edit] {ov_codec} encode failed — falling back to libx264.\n"
              f"{result.stderr[-400:]}", file=sys.stderr)
        _HW_ENCODER_CACHE = ("libx264", "veryfast")
        if os.path.exists(video_tmp): os.remove(video_tmp)
        result = _run("libx264", "veryfast")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg overlay encode failed:\n{result.stderr[-800:]}")

    tmp_sz = os.path.getsize(video_tmp) if os.path.exists(video_tmp) else 0
    src_sz = os.path.getsize(video_in)
    if tmp_sz < src_sz * 0.5:
        if os.path.exists(video_tmp): os.remove(video_tmp)
        raise RuntimeError(f"ffmpeg overlay output suspiciously small ({tmp_sz} vs {src_sz})")

    _replace_with_retry(video_tmp, video_in)
    print(f"  [edit] Done — master_output.mp4 updated via ffmpeg "
          f"({os.path.getsize(video_in) / 1048576:.1f} MB)", file=sys.stderr)
    return True


def cmd_overlay_assets(argv):
    """
    B2.12 Stage 3 — render STATIC overlay assets (PNGs + render/_final_layers.json) BEFORE
    the base video exists, so the reordered pipeline can compose overlays + captions in the
    Render phase. W/H come from aspect_ratio + short_edge (no master to probe); duration from
    scene_timings. REFUSES the animated case: if the plan has any animated element or
    ride_motion, exit 3 so the caller keeps today's render-first flow (animated overlays are
    composited by moviepy onto the finished base). Exit 0 = statics deferred; 2 = nothing to do.
    argv: project_path, edit_plan.json, scene_timings.json, dynamic_options.txt,
          brand_preset, aspect_ratio, short_edge, global_font.
    """
    import overlay_design as od
    project_path = argv[0]
    plan_path    = argv[1]
    timings_path = argv[2]
    dynamic_path = argv[3]
    brand_preset = argv[4] if len(argv) > 4 else ""
    aspect       = argv[5] if len(argv) > 5 else "9:16"
    short_edge    = int(argv[6]) if (len(argv) > 6 and str(argv[6]).strip().isdigit()) else 1080
    global_font  = str(argv[7]).strip() if len(argv) > 7 else ""   # B2.19.3

    # Clear any stale defer manifest FIRST so its presence always reflects THIS run — Render
    # keys the "static overlays were deferred" branch on the file existing.
    _fl_stale = os.path.join(project_path, "render", "_final_layers.json")
    try:
        if os.path.exists(_fl_stale):
            os.remove(_fl_stale)
    except OSError:
        pass

    plan = _load_json(plan_path, {})
    overlays_spec = plan.get("overlays", []) if isinstance(plan, dict) else []
    # Animated / motion overlays cannot be pre-rendered without the base — defer to old flow.
    for spec in overlays_spec:
        if (spec.get("element") or "").strip().lower() in od.ANIMATED_ELEMENTS or spec.get("ride_motion"):
            print("  [edit] plan has animated overlays — composited in Render (render-first).",
                  file=sys.stderr)
            sys.exit(3)

    timings = _load_json(timings_path, [])
    if not timings:
        print("  [edit] no scene_timings — nothing to pre-render.", file=sys.stderr)
        sys.exit(2)
    timings_by_scene = {int(t["scene"]): t for t in timings if t.get("type", "scene") == "scene"}
    takeover_timings = [t for t in timings if t.get("type") == "takeover"]
    if not overlays_spec and not takeover_timings:
        sys.exit(2)

    W, H = _target_size(aspect, short_edge)
    video_dur = max((float(t.get("end", 0)) for t in timings), default=0.0)
    overlays_dir = os.path.join(project_path, "overlays")
    video_in  = os.path.join(project_path, "master_output.mp4")   # may not exist (dims injected)
    video_tmp = os.path.join(project_path, "_tmp", "master_output_edit_tmp.mp4")
    try:
        handled = _overlay_render_ffmpeg(project_path, overlays_spec, timings_by_scene,
                                         takeover_timings, dynamic_path, brand_preset,
                                         video_in, video_tmp, overlays_dir,
                                         defer=True, dims=(W, H, video_dur),
                                         global_font=global_font)
    except Exception as e:
        print(f"  [edit] overlay asset pre-render failed ({e}) — render-first flow.",
              file=sys.stderr)
        sys.exit(3)
    if handled == "deferred":
        sys.exit(0)
    # "unsupported" here means no renderable layers — nothing to defer.
    sys.exit(2)


def cmd_overlay_render(argv):
    if len(argv) < 4:
        print("Usage: avg.py overlay_render <project_path> <edit_plan.json> "
              "<scene_timings.json> <dynamic_options.txt> [brand_preset] [beat_map]",
              file=sys.stderr)
        sys.exit(1)

    project_path   = argv[0]
    plan_path      = argv[1]
    timings_path   = argv[2]
    dynamic_path   = argv[3]
    brand_preset   = argv[4] if len(argv) > 4 else ""
    # argv[5] = beat_map (vestigial here, unused); argv[6] = render engine (B2.9)
    render_engine  = argv[6] if len(argv) > 6 else "auto"
    defer_final    = str(argv[7]).strip() == "1" if len(argv) > 7 else False   # B2.9 Stage 2
    global_font    = str(argv[8]).strip() if len(argv) > 8 else ""             # B2.19.3

    video_in     = os.path.join(project_path, "master_output.mp4")
    video_tmp    = os.path.join(project_path, "_tmp", "master_output_edit_tmp.mp4")
    overlays_dir = os.path.join(project_path, "overlays")

    if not os.path.exists(video_in):
        print(f"Error: {video_in} not found.", file=sys.stderr)
        sys.exit(1)

    plan = _load_json(plan_path)
    if plan is None:
        print(f"  [edit] Could not read edit_plan.json — skipping.", file=sys.stderr)
        sys.exit(2)

    overlays_spec = plan.get("overlays", [])

    timings = _load_json(timings_path)
    if timings is None:
        print(f"  [edit] Could not read scene_timings.json — skipping.", file=sys.stderr)
        sys.exit(2)

    timings_by_scene = {int(t["scene"]): t for t in timings if t.get("type", "scene") == "scene"}
    takeover_timings = [t for t in timings if t.get("type") == "takeover"]

    # B2.9 — native ffmpeg overlay compositor, now ON BY DEFAULT (render_engine 'auto').
    # It handles ONLY the simple static/fade case and returns "unsupported" for anything
    # animated/motion, so the moviepy path below stays the exact-parity reference. Any
    # error falls through to moviepy too. render_engine='moviepy' (or env) skips it.
    if _use_ffmpeg_engine(render_engine):
        try:
            handled = _overlay_render_ffmpeg(project_path, overlays_spec,
                                             timings_by_scene, takeover_timings,
                                             dynamic_path, brand_preset,
                                             video_in, video_tmp, overlays_dir,
                                             defer=defer_final,
                                             global_font=global_font)
            if handled is True or handled == "deferred":
                sys.exit(0)   # 'deferred' => layers handed to the caption pass (no encode)
            print("  [edit] ffmpeg overlay path unsupported for this plan "
                  "(animated/motion overlays) — using moviepy.", file=sys.stderr)
        except SystemExit:
            raise
        except Exception as e:
            print(f"  [edit] ffmpeg overlay path failed ({e}) — falling back to moviepy.",
                  file=sys.stderr)

    # Lazy imports
    _or_imports()
    import overlay_design as od

    brand     = _apply_global_font(_load_brand(dynamic_path, brand_preset, od), global_font, od)
    safezones = _load_safezones(project_path)

    try:
        base = VideoFileClip(video_in)
    except Exception as e:
        print(f"  [edit] Could not open master_output.mp4 ({e}) — skipping.", file=sys.stderr)
        sys.exit(2)

    W, H       = base.size
    video_dur  = base.duration
    os.makedirs(overlays_dir, exist_ok=True)
    overlay_clips = []
    applied = 0

    # ── AI-planned overlays ───────────────────────────────────────────────────
    for idx, spec in enumerate(overlays_spec, 1):
        scene  = spec.get("scene")
        timing = timings_by_scene.get(int(scene)) if scene is not None else None
        if timing is None:
            print(f"  [edit] Overlay {idx}: scene {scene} not in timings — skipping.", file=sys.stderr)
            continue

        start       = float(timing["start"])
        scene_dur   = float(timing["duration"])
        overlay_dur = float(spec.get("duration", 5.0))
        overlay_dur = max(0.5, min(overlay_dur, scene_dur, video_dur - start))
        if overlay_dur <= 0:
            continue

        try:
            rspec = dict(spec)
            rspec["placement"] = _resolve_placement(spec, int(scene), safezones)
            static_img, anim_clip = _build_overlay_clip(rspec, W, H, brand, overlay_dur, od)
        except Exception as e:
            print(f"  [edit] Overlay {idx} ({spec.get('element')}): render error ({e}) — skipping.",
                  file=sys.stderr)
            continue

        element = spec.get("element", "")
        if anim_clip is not None:
            clip = anim_clip
        else:
            png = os.path.join(overlays_dir, f"overlay_{idx:02d}.png")
            static_img.save(png)
            clip = _with_duration(ImageClip(png), overlay_dur)

        clip = _with_position(_with_duration(clip, overlay_dur), (0, 0))

        # Set start using with_start / set_start
        clip = (clip.with_start(start) if hasattr(clip, "with_start") else clip.set_start(start))

        fi = min(FADE_SEC, overlay_dur / 3)
        fo = min(FADE_SEC, overlay_dur / 3)
        ti = spec.get('transition_in', 'fade')
        to = spec.get('transition_out', 'fade')
        if element in od.ANIMATED_ELEMENTS and ti != 'fade' and not spec.get('ride_motion'):
            ti = 'fade'
        if spec.get('ride_motion') and timing.get('zoom_in', True):
            ti = 'pop'; fi = min(FADE_SEC * 1.6, overlay_dur / 3)

        clip = _apply_transition(clip, fi, fo, overlay_dur, ti, to)
        overlay_clips.append(clip)
        applied += 1

    if not overlay_clips and not takeover_timings:
        print("  [edit] No overlays could be applied — skipping.", file=sys.stderr)
        base.close(); sys.exit(2)

    # ── chapters_takeover_cards (chapters takeovers) ──────────────────────────────────
    for t in takeover_timings:
        spec = {"element": "chapters_takeover_cards", "headline": t.get("headline", ""), "sub": t.get("sub", "")}
        try:
            img = od.render_overlay(spec, W, H, brand)
        except Exception as e:
            print(f"  [edit] Takeover {t.get('id','')}: render error ({e}) — skipping.", file=sys.stderr)
            continue

        png = os.path.join(overlays_dir, f"takeover_{t.get('id','')}.png")
        img.save(png)

        start = float(t["start"]); dur = float(t["duration"])
        if dur <= 0 or start >= video_dur:
            continue
        dur = min(dur, video_dur - start)

        clip = _with_duration(ImageClip(png), dur)
        clip = _with_position(clip, (0, 0))
        clip = (clip.with_start(start) if hasattr(clip, "with_start") else clip.set_start(start))
        fade = min(0.3, dur / 4)
        clip = _apply_transition(clip, fade, fade, dur, 'fade', 'fade')
        overlay_clips.append(clip)
        applied += 1

    if not overlay_clips:
        print("  [edit] All overlay/takeover renders failed — skipping.", file=sys.stderr)
        base.close(); sys.exit(2)

    # ── composite + re-encode ─────────────────────────────────────────────────
    print(f"  [edit] Compositing {applied} overlay(s) onto master_output.mp4...", file=sys.stderr)
    video = None
    try:
        video = _with_duration(CompositeVideoClip([base] + overlay_clips, size=(W, H)), video_dur)
        actual_codec, actual_preset = _detect_hw_encoder("libx264")
        video.write_videofile(video_tmp, fps=base.fps, codec=actual_codec,
                               audio_codec="aac", preset=actual_preset, logger=None)
    except Exception as e:
        print(f"  [edit] Render error ({e}) — skipping.", file=sys.stderr)
        if os.path.exists(video_tmp): os.remove(video_tmp)
        sys.exit(2)
    finally:
        # Close every clip that could be holding an ffmpeg-reader subprocess
        # handle on video_in — base alone isn't enough; a lingering handle
        # here is what caused the next server job (caption_burn) to fail
        # os.replace with WinError 5 on this same file.
        if video is not None:
            try: video.close()
            except Exception: pass
        for c in overlay_clips:
            try: c.close()
            except Exception: pass
        base.close()

    tmp_sz = os.path.getsize(video_tmp) if os.path.exists(video_tmp) else 0
    src_sz = os.path.getsize(video_in)
    if tmp_sz < src_sz * 0.5:
        print(f"  [edit] Output suspiciously small ({tmp_sz} vs {src_sz}) — skipping replace.",
              file=sys.stderr)
        if os.path.exists(video_tmp): os.remove(video_tmp)
        sys.exit(2)

    _replace_with_retry(video_tmp, video_in)
    print(f"  [edit] Done — master_output.mp4 updated ({os.path.getsize(video_in)/1048576:.1f} MB)", file=sys.stderr)
    sys.exit(0)


# ══════════════════════════════════════════════════════════════════════════════
#  SUBCOMMAND: caption_burn
#  (was caption_burner.py)
#  Transcribes audio → animated ASS captions → burns into master_output.mp4
# ══════════════════════════════════════════════════════════════════════════════

# B2.5.3 — caption animations rebuilt from scratch. The old set had ~10 styles, several
# of which were duplicates or half-working (typewriter and karaoke were literally the same
# code; pop grew from scale 0 over a slow linear 250ms so it "exploded" and got cut off).
# Pared to 5 distinct, tuned, production-grade styles + plain. Each uses a DIFFERENT
# mechanism so none feel the same: pop = scale, fade = opacity, rise = move+opacity,
# highlight = per-word colour fill, typewriter = per-word reveal.
_VALID_ANIMATIONS = {
    'none', 'pop', 'fade', 'rise', 'highlight', 'typewriter',
}
# Old style names migrate to the closest survivor so saved configs keep working.
_ANIM_ALIASES = {
    'fade_up': 'rise', 'slide_left': 'rise', 'karaoke': 'highlight',
    'bounce': 'pop', 'glow': 'fade', 'spin': 'pop', 'heartbeat': 'pop',
}
_COLOR_MAP = {
    'white': '&H00FFFFFF', 'black': '&H00000000', 'red': '&H000000FF',
    'green': '&H0000FF00', 'blue': '&H00FF0000', 'yellow': '&H0000FFFF',
    'cyan': '&H00FFFF00', 'magenta': '&H00FF00FF', 'orange': '&H0000A5FF',
    'gray': '&H00808080', 'grey': '&H00808080',
}
# Highlight fill accent (ASS is &HBBGGRR) — the "spoken word lights up" colour.
_HIGHLIGHT_ACCENT = '&H0000FFFF'   # bright yellow (R255 G255 B0)
_POS_AWARE_STYLES = {'rise'}


def cmd_caption_burn(argv):
    import subprocess

    if len(argv) < 10:
        print("Usage: avg.py caption_burn <project_path> <provider> <assemblyai_key> "
              "<openai_key> <font_size> <font_color> <outline_width> <position> "
              "<max_words> <timeout> <animation_style> [animation_speed]",
              file=sys.stderr)
        sys.exit(1)

    project_path   = argv[0]
    provider       = argv[1].lower()
    assemblyai_key = argv[2].strip()
    openai_key     = argv[3].strip()
    font_size_str  = argv[4]
    font_color     = argv[5]
    outline_width  = argv[6]
    position       = argv[7].lower()
    max_words      = int(argv[8])
    timeout_sec    = int(argv[9])
    _anim_raw      = argv[10].lower().strip() if len(argv) > 10 else 'pop'
    _anim_raw      = _ANIM_ALIASES.get(_anim_raw, _anim_raw)   # migrate retired style names
    anim_style     = _anim_raw if _anim_raw in _VALID_ANIMATIONS else 'pop'
    _speed_raw     = argv[11].lower().strip() if len(argv) > 11 else 'normal'
    speed_factor   = {'slow': 1.5, 'normal': 1.0, 'fast': 0.6, 'very_fast': 0.3}.get(_speed_raw, 1.0)
    # B2.8 Smart Captions — 'typewriter' and 'highlight' are timed to the VOICE (each
    # word reveals / lights up as it's spoken). Scaling their timing by animation_speed
    # breaks that sync, so speed is FORCED to normal for them (and 'none', which is
    # static). Speed only affects the entrance styles pop / fade / rise.
    if anim_style in ('typewriter', 'highlight', 'none'):
        speed_factor = 1.0
        _speed_raw   = 'normal'
    font_size      = int(font_size_str) if font_size_str.isdigit() and int(font_size_str) > 0 else 56
    # B2.10 — trailing "-" on a sentence cut mid-way by the word limit is now optional
    # (config video.captions.cut_dash). '0' => no dash, the line just ends. Default on.
    _cut_dash      = (str(argv[12]).strip() != '0') if len(argv) > 12 else True
    def _dash(cont): return " -" if (cont and _cut_dash) else ""
    # B2.12 Stage 3 — 'ass_only' (argv13='1'): transcribe + write captions.ass, then exit
    # WITHOUT burning. Used by the reordered Captions phase (Render composes captions later).
    ass_only       = (len(argv) > 13 and str(argv[13]).strip() == '1')
    # B2.16 — GLOBAL font (family name or .ttf/.otf path; applies to captions AND Takeover
    # Cards) and active-word HIGHLIGHT style ('underline' | 'outline_fill'). Empty font =
    # keep the system default. highlight_style is consumed by the Takeover-Card text layer.
    global_font     = str(argv[14]).strip() if len(argv) > 14 else ''
    highlight_style = str(argv[15]).strip().lower() if len(argv) > 15 else 'underline'
    if highlight_style not in ('underline', 'outline_fill'):
        highlight_style = 'underline'

    audio_file = _resolve_input(os.path.join(project_path, "audio", "optimized_voice.mp3"),
                                os.path.join(project_path, "optimized_voice.mp3"))
    video_in   = os.path.join(project_path, "master_output.mp4")
    srt_file   = os.path.join(project_path, "render", "captions.srt")
    ass_file   = os.path.join(project_path, "render", "captions.ass")
    video_tmp  = os.path.join(project_path, "_tmp", "master_output_cap_tmp.mp4")
    # Output subfolders may not exist yet on a pre-reorg project.
    os.makedirs(os.path.join(project_path, "render"), exist_ok=True)
    os.makedirs(os.path.join(project_path, "_tmp"), exist_ok=True)

    # B2.16 fix — in ASS-ONLY mode the Captions phase runs BEFORE Render (the reorder), so
    # master_output.mp4 does not exist yet; it is only required for the actual burn. Requiring
    # it here made the ass-only pass exit 1 on every fresh project (no leftover master), which
    # skipped warming the transcription cache and surfaced a scary "caption_burn exited 1".
    _required = [(audio_file, "optimized_voice.mp3")]
    if not ass_only:
        _required.append((video_in, "master_output.mp4"))
    for req, label in _required:
        if not os.path.exists(req):
            print(f"Error: {label} not found.", file=sys.stderr)
            sys.exit(1)

    # B2.9 Stage 2 — SINGLE-PASS FINAL. If the Graphics phase DEFERRED its overlays
    # (wrote render/_final_layers.json instead of encoding), bake them in the SAME ffmpeg
    # pass as the captions — one encode instead of two. Absent => normal caption-only burn.
    # Guaranteed no overlay loss: if the combined pass fails, the overlays are composited
    # in a separate fallback pass before the caption-only burn (see below).
    _final_layers = []
    _final_layers_json = os.path.join(project_path, "render", "_final_layers.json")
    _fl_dur = None
    _lj = _load_json(_final_layers_json, None)
    if isinstance(_lj, list) and _lj:
        _dims = _ffprobe_video(video_in)
        _fl_dur = _dims[2] if _dims else None
        if _fl_dur:
            _final_layers = [l for l in _lj if isinstance(l, dict) and l.get("png") and os.path.exists(l["png"])]

    def _bake_deferred_overlays():
        # No-loss safety: if captions are about to be SKIPPED (transcription failed) while
        # Graphics deferred its overlays, bake them into master now so they're never lost.
        if not (_final_layers and _fl_dur):
            return
        try:
            import subprocess as _sp
            cc, cp = _detect_hw_encoder("libx264")
            ov_tmp = os.path.join(project_path, "_tmp", "master_ovskip_tmp.mp4")
            base = ["ffmpeg", "-y", "-i", video_in]
            filt = []; cur = "[0:v]"
            for i, lyr in enumerate(_final_layers, start=1):
                base += ["-loop", "1", "-t", f"{_fl_dur:.3f}", "-i", lyr["png"]]
                s = float(lyr.get("start", 0.0)); d = float(lyr.get("dur", 0.0))
                fi = float(lyr.get("fade_in", 0.3)); fo = float(lyr.get("fade_out", 0.3))
                e = s + d; fo_st = max(s, e - fo)
                filt.append(f"[{i}:v]format=rgba,fade=t=in:st={s:.3f}:d={max(0.001, fi):.3f}:alpha=1,"
                            f"fade=t=out:st={fo_st:.3f}:d={max(0.001, fo):.3f}:alpha=1[ov{i}]")
                nxt = f"[v{i}]" if i < len(_final_layers) else "[vout]"
                filt.append(f"{cur}[ov{i}]overlay=0:0:enable='between(t,{s:.3f},{e:.3f})'{nxt}")
                cur = nxt
            base += ["-filter_complex", ";".join(filt), "-map", "[vout]", "-map", "0:a?", "-c:a", "copy"]
            r = _sp.run(base + _h264_quality_args(cc, cp) + [ov_tmp], capture_output=True, text=True)
            if r.returncode != 0 and cc != "libx264":
                r = _sp.run(base + _h264_quality_args("libx264", "veryfast") + [ov_tmp], capture_output=True, text=True)
            if r.returncode == 0 and os.path.exists(ov_tmp) and os.path.getsize(ov_tmp) > 1024:
                _replace_with_retry(ov_tmp, video_in)
                try:
                    if os.path.exists(_final_layers_json): os.remove(_final_layers_json)
                except Exception:
                    pass
                print("  [captions] captions skipped — deferred overlays baked separately.", file=sys.stderr)
        except Exception:
            pass

    def scale_ms(ms): return max(1, int(round(ms * speed_factor)))

    # ── overlay windows for caption-avoidance ─────────────────────────────────
    def load_overlay_windows():
        windows = []
        timings = _load_json(_resolve_input(os.path.join(project_path, "render", "scene_timings.json"),
                                            os.path.join(project_path, "scene_timings.json")), [])
        timings_by_scene = {}
        for t in timings:
            if t.get('type') == 'takeover':
                windows.append((float(t['start']), float(t['end'])))
            elif t.get('type', 'scene') == 'scene':
                timings_by_scene[int(t['scene'])] = (float(t['start']), float(t['end']))
        plan = _load_json(os.path.join(project_path, "render", "edit_plan.json"), {})
        if plan and timings_by_scene:
            for ov in plan.get('overlays', []):
                scene = ov.get('scene')
                if scene is None or int(scene) not in timings_by_scene:
                    continue
                ss, se = timings_by_scene[int(scene)]
                dur    = float(ov.get('duration', 5.0))
                windows.append((ss, min(ss + dur, se)))
        return windows

    def load_card_windows():
        # B2.16 CAPTION INTERLOCK — every Takeover Card (chapters/explainer/focused = a
        # 'takeover' entry in scene_timings) turns standard captions OFF while it is on
        # screen; captions resume the instant the card ends. Returns those (start,end)s.
        timings = _load_json(_resolve_input(os.path.join(project_path, "render", "scene_timings.json"),
                                            os.path.join(project_path, "scene_timings.json")), [])
        return [(float(t['start']), float(t['end'])) for t in timings if t.get('type') == 'takeover']

    def cue_overlaps(cs, ce, windows):
        return any(cs < we and ce > ws for ws, we in windows)

    # ── transcription ─────────────────────────────────────────────────────────
    def transcribe_assemblyai(key):
        import urllib.request
        base    = "https://api.assemblyai.com/v2"
        headers = {"authorization": key, "content-type": "application/json"}

        print("  [captions] Uploading audio to AssemblyAI...", file=sys.stderr)
        file_size = os.path.getsize(audio_file)
        class _CF:
            def __init__(self): self._f = open(audio_file, 'rb'); self.length = file_size
            def read(self, n=-1): return self._f.read(n)
            def __len__(self): return self.length
            def close(self): self._f.close()
        cf = _CF()
        try:
            req = urllib.request.Request(
                f"{base}/upload", data=cf,
                headers={"authorization": key, "content-length": str(file_size)})
            with urllib.request.urlopen(req, timeout=120) as r:
                upload_url = json.loads(r.read())['upload_url']
        finally:
            cf.close()

        payload = json.dumps({"audio_url": upload_url, "word_boost": [], "language_code": "en"}).encode()
        req     = urllib.request.Request(f"{base}/transcript", data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            job_id = json.loads(r.read())['id']

        print(f"  [captions] Transcribing (id={job_id})...", file=sys.stderr)
        deadline, poll_wait = _time.time() + timeout_sec, 3.0
        while _time.time() < deadline:
            req = urllib.request.Request(f"{base}/transcript/{job_id}", headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
            status = data['status']
            if status == 'completed': return data.get('words', [])
            if status == 'error':     raise RuntimeError(f"AssemblyAI error: {data.get('error','unknown')}")
            _time.sleep(poll_wait); poll_wait = min(poll_wait * 1.5, 10.0)
        raise TimeoutError(f"AssemblyAI did not complete within {timeout_sec}s")

    def transcribe_whisper(key):
        import urllib.request, uuid
        boundary = uuid.uuid4().hex
        with open(audio_file, 'rb') as f: audio_data = f.read()
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\nwhisper-1\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="response_format"\r\n\r\nverbose_json\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="timestamp_granularities[]"\r\n\r\nword\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.mp3"\r\n'
            f'Content-Type: audio/mpeg\r\n\r\n'
        ).encode() + audio_data + f'\r\n--{boundary}--\r\n'.encode()
        print("  [captions] Transcribing via OpenAI Whisper...", file=sys.stderr)
        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/transcriptions", data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        words = []
        for seg in data.get('segments', []):
            for w in seg.get('words', []):
                words.append({'text': w['word'], 'start': int(w['start']*1000), 'end': int(w['end']*1000)})
        return words

    # B2.12 Stage 6 — TRANSCRIPTION CACHE. The word list is a pure function of the audio
    # bytes + provider, but caption re-runs (style tweaks, Preview-a-Phase, single-pass
    # re-render) used to re-upload and RE-PAY AssemblyAI/Whisper every time. Cache the raw
    # word list under a hash of the audio + provider so a repeat is instant and free. A miss
    # (or any cache error) just transcribes as before.
    _tc_dir  = os.path.join(project_path, "render", "_cache")
    _tc_key  = ""
    _tc_path = ""
    try:
        _tc_key  = _seg_cache_key("transcript", 0, provider, audio_file)
        _tc_path = os.path.join(_tc_dir, f"transcript_{_tc_key}.json")
    except Exception:
        _tc_path = ""

    words = []
    if _tc_path and os.path.exists(_tc_path):
        _cached = _load_json(_tc_path, None)
        if isinstance(_cached, list) and _cached:
            words = _cached
            print(f"  [captions] reusing cached transcription ({len(words)} words, "
                  f"no re-upload).", file=sys.stderr)
    try:
        if words:
            pass   # cache hit — no API call
        elif provider == 'assemblyai' and assemblyai_key:
            words = transcribe_assemblyai(assemblyai_key)
        elif openai_key:
            words = transcribe_whisper(openai_key)
        else:
            print("  [captions] No valid API key — skipping captions.", file=sys.stderr)
            _bake_deferred_overlays(); sys.exit(2)
    except Exception as e:
        print(f"  [captions] Transcription failed ({e}) — skipping.", file=sys.stderr)
        if provider == 'assemblyai' and openai_key:
            print("  [captions] Falling back to OpenAI Whisper...", file=sys.stderr)
            try:
                words = transcribe_whisper(openai_key)
            except Exception as e2:
                print(f"  [captions] Whisper fallback failed ({e2}) — skipping.", file=sys.stderr)
                _bake_deferred_overlays(); sys.exit(2)
        else:
            _bake_deferred_overlays(); sys.exit(2)

    if not words:
        print("  [captions] No words returned — skipping.", file=sys.stderr)
        _bake_deferred_overlays(); sys.exit(2)

    # B2.12 Stage 6 — persist the raw word list (pre-takeover-drop) for free re-runs.
    if _tc_path:
        try:
            os.makedirs(_tc_dir, exist_ok=True)
            with open(_tc_path, "w", encoding="utf-8") as _f:
                json.dump(words, _f)
        except Exception:
            pass

    # B2.10 — captions must NOT paint over takeover chapter cards. Drop any transcribed
    # word whose spoken midpoint falls inside a takeover window (exact positions from
    # scene_timings.json), so the caption line vanishes for the card and reappears after.
    # Breaths are already handled by the >=500ms cue-split in _smart_group (silence => the
    # line ends at the last spoken word, so no caption lingers over the pause).
    def _drop_words_in_takeovers(ws):
        st = _load_json(_resolve_input(os.path.join(project_path, "render", "scene_timings.json"),
                                       os.path.join(project_path, "scene_timings.json")), [])
        tk = [(float(t['start']) * 1000.0, float(t['end']) * 1000.0)
              for t in (st or []) if t.get('type') == 'takeover']
        if not tk:
            return ws
        kept = [w for w in ws
                if not any(a <= (float(w['start']) + float(w['end'])) / 2.0 < b for a, b in tk)]
        if len(kept) != len(ws):
            print(f"  [captions] {len(ws) - len(kept)} word(s) suppressed under takeover card(s).",
                  file=sys.stderr)
        return kept
    words = _drop_words_in_takeovers(words)
    if not words:
        print("  [captions] No words outside takeover windows — skipping.", file=sys.stderr)
        _bake_deferred_overlays(); sys.exit(2)

    # ── SRT ───────────────────────────────────────────────────────────────────
    def srt_ts(s):
        ms=int((s%1)*1000); sec=int(s)%60; m=(int(s)//60)%60; h=int(s)//3600
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

    def _smart_group(ws):
        # B2.8 Smart Captions — group words by SENTENCE, with max_words as a LIMIT (a
        # ceiling), not a fixed count. A line ends when: (a) a word ends a sentence
        # (. ! ?), (b) there's a real speech PAUSE before the next word (so a caption
        # never lingers over silence — keeps it locked to the voice), or (c) the word
        # limit is hit MID-sentence, in which case the line is marked cont=True so the
        # renderer appends a trailing "-" showing the sentence isn't finished.
        PAUSE_MS = 500
        limit = max(1, int(max_words))
        out, bucket = [], []
        def flush(cont):
            if bucket:
                out.append((list(bucket), cont)); bucket.clear()
        for w in ws:
            if bucket and (float(w['start']) - float(bucket[-1]['end'])) >= PAUSE_MS:
                flush(False)          # silence gap — end the line here, no dash
            bucket.append(w)
            txt = (w.get('text') or '').strip()
            # sentence ends on . ! ? (allowing a trailing closing quote/bracket)
            ends_sentence = txt.rstrip('"\')]').endswith(('.', '!', '?'))
            if ends_sentence:
                flush(False)          # sentence finished — no dash
            elif len(bucket) >= limit:
                flush(True)           # cut mid-sentence at the limit — dash
        flush(False)
        return out

    def words_to_srt(ws):
        lines = []
        for i, (cue, cont) in enumerate(_smart_group(ws), 1):
            txt = ' '.join(w['text'].strip() for w in cue) + _dash(cont)
            lines.append(f"{i}\n{srt_ts(cue[0]['start']/1000)} --> {srt_ts(cue[-1]['end']/1000)}\n{txt}\n")
        return '\n'.join(lines)

    with open(srt_file, 'w', encoding='utf-8') as f:
        f.write(words_to_srt(words))
    print(f"  [captions] SRT written ({len(words)} words)", file=sys.stderr)

    # ── ASS helpers ───────────────────────────────────────────────────────────
    def ass_color(name):
        return _COLOR_MAP.get(name.lower().strip(), '&H00FFFFFF')

    def ass_escape(t):
        return t.replace('\\', '\\\\').replace('{', '\\{').replace('}', '\\}')

    def ass_time(s):
        h=int(s//3600); m=int((s%3600)//60); sec=int(s%60); cs=int((s%1)*100)
        return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"

    def get_video_dims(vp):
        try:
            cmd=['ffprobe','-v','error','-select_streams','v:0',
                 '-show_entries','stream=width,height','-of','json', vp]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            d = json.loads(r.stdout)
            return int(d['streams'][0]['width']), int(d['streams'][0]['height'])
        except Exception:
            return 1920, 1080

    def find_system_font():
        for p in [r"C:\Windows\Fonts\Arial.ttf", r"C:\Windows\Fonts\ariblk.ttf",
                  r"C:\Windows\Fonts\calibri.ttf", r"C:\Windows\Fonts\segoeui.ttf"]:
            if os.path.exists(p): return p
        return ""

    def group_cues(ws):
        # B2.8 — sentence-aware; returns [(words, continues), ...] (see _smart_group).
        return _smart_group(ws)

    def make_ass_header(w, h, fname):
        margin_v  = int(round({'bottom': 40, 'top': 40, 'center': 0}.get(position, 40) * _res_scale))
        alignment = {'bottom': 2,  'top': 8,   'center': 5}.get(position, 2)
        # 'highlight' fills each word from the base colour (Secondary) to the accent
        # (Primary) as it's spoken. Every other style paints in the user's colour.
        if anim_style == 'highlight':
            primary   = _HIGHLIGHT_ACCENT
            secondary = ass_color(font_color)
        else:
            primary   = ass_color(font_color)
            secondary = '&H000000FF'
        return (f"[Script Info]\nTitle: AVG Captions\nScriptType: v4.00+\n"
                f"PlayResX: {w}\nPlayResY: {h}\nScaledBorderAndShadow: yes\n\n"
                f"[V4+ Styles]\n"
                f"Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                f"OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
                f"ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                f"Alignment, MarginL, MarginR, MarginV, Encoding\n"
                f"Style: Default,{fname},{font_size},{primary},{secondary},"
                f"&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,{outline_width},0,"
                f"{alignment},10,10,{margin_v},1\n\n"
                f"[Events]\n"
                f"Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

    def cue_pos(w, h, pos=None):
        p = pos if pos is not None else position
        x = w // 2
        _pad = int(round(40 * _res_scale))
        if p == 'bottom':   y = h - _pad - font_size // 2
        elif p == 'top':    y = _pad + font_size // 2
        else:               y = h // 2
        return x, y

    def _word_rel_ms(cue, w):
        # a word's spoken start relative to the cue's start, speed-scaled, clamped >= 0
        return max(0, int(round((w['start'] - cue[0]['start']) * speed_factor)))

    # ── CAPTION WORD STRING ─────────────────────────────────────────────────────
    # B2.16's focus [[..]] / question ((..)) markers are catalogued by Director 1 to
    # drive the Focused Takeover Cards ONLY. Standard captions render the user's chosen
    # animation with NO extra highlight/accent overlay: painting focus words onto every
    # caption (bold + accent fill) was never part of the B2.16 spec and it fought the
    # clean entrance styles — e.g. 'pop' is a pure scale and must have no coloured words.
    def _cue_words(cue):
        return ' '.join(ass_escape(ww['text']) for ww in cue)

    def reveal_text(cue, cont=False):
        # TYPEWRITER (B2.11.1 rewrite) — every word is visible; a single WHOLE-WORD highlight
        # MOVES with the voice: each word snaps to the accent colour for exactly the moment it
        # is spoken, then snaps back to base. The highlight is NOT left behind (that is what
        # makes it distinct from 'highlight', which fills each word cumulatively and keeps it).
        # \r before every word scopes its colour transforms so only the currently-spoken word
        # is lit at any instant; \t(t,t,...) gives an INSTANT (whole-word) switch, not a fade.
        def _c(cv): return r"\c&H" + cv[4:] + "&"        # &HAABBGGRR -> \c&HBBGGRR&
        base_c   = _c(ass_color(font_color))
        accent_c = _c(_HIGHLIGHT_ACCENT)
        parts = []
        for w in cue:
            # ws must be >=1: an accent \t at exactly t=0 collides with the \r reset (also at
            # t=0) and \r wins, so the FIRST word would never light. 1ms in is imperceptible.
            ws = max(1, _word_rel_ms(cue, w))
            we = max(ws + 1, int(round((float(w['end']) - float(cue[0]['start'])) * speed_factor)))
            parts.append(r"{\r\t(" + str(ws) + "," + str(ws) + "," + accent_c + r")"
                         r"\t(" + str(we) + "," + str(we) + "," + base_c + r")}" + ass_escape(w['text']))
        return ' '.join(parts) + _dash(cont)

    def highlight_text(cue, cont=False):
        # HIGHLIGHT — every word is visible; each fills from the base colour to the accent
        # as it's spoken (\kf sweep, Secondary -> Primary). The classic "word lights up".
        parts = []
        for w in cue:
            dur_cs = max(1, int(round((w['end'] - w['start']) / 10.0 * speed_factor)))  # centiseconds
            parts.append(r"{\kf" + str(dur_cs) + r"}" + ass_escape(w['text']) + " ")
        return ''.join(parts).rstrip() + _dash(cont)

    def animated_text(cue, style, w, h, eff_pos=None, cont=False):
        # 5 tuned styles, each a DIFFERENT mechanism so none feel like a re-skin of another:
        #   pop = scale · fade = opacity · rise = move+opacity · highlight = cumulative colour
        #   fill (left behind) · typewriter = a single whole-word highlight that MOVES with the
        #   voice (B2.11.1, not left behind).  B2.8: a "-" is appended when the sentence is
        #   cut mid-way by the word limit (cont=True). Captions carry no focus-word overlay —
        #   focus markers drive the Focused Takeover Cards only (see _cue_words).
        text = _cue_words(cue) + _dash(cont)
        if style == 'typewriter':
            return reveal_text(cue, cont)
        if style == 'highlight':
            return highlight_text(cue, cont)
        if style == 'none':
            return text
        if style == 'pop':
            # POP — the YouTuber signature. Snappy scale from 65% with a tight 108% overshoot
            # (ease-out via accel 0.7), then a quick settle to 100%. Starts near full size so
            # it reads instantly instead of exploding from a dot, and finishes in <200ms so it
            # can't get clipped by the next cue — fixes the old slow/oversized/cut-off pop.
            t1 = scale_ms(90); t2 = scale_ms(190)
            return (r"{\fscx65\fscy65\t(0," + str(t1) + r",0.7,\fscx108\fscy108)"
                    r"\t(" + str(t1) + "," + str(t2) + r",\fscx100\fscy100)}" + text)
        if style == 'fade':
            # FADE — clean cinematic opacity fade-in, no movement.
            return r"{\alpha&HFF&\t(0," + str(scale_ms(180)) + r",\alpha&H00&)}" + text
        if style == 'rise':
            # RISE — glides up a short distance while fading in. Modern and smooth.
            x, y = cue_pos(w, h, eff_pos)
            dist = max(6, int(round(34 * _res_scale)))
            t    = scale_ms(220)
            return (r"{\alpha&HFF&\move(" + f"{x},{y + dist},{x},{y},0,{t}" + r")"
                    r"\t(0," + str(t) + r",\alpha&H00&)}" + text)
        return text

    def cue_text(cue, w, h, windows, cont=False):
        cs, ce = cue[0]['start'] / 1000.0, cue[-1]['end'] / 1000.0
        needs_shift = position in ('top', 'center') and cue_overlaps(cs, ce, windows)
        if anim_style in _POS_AWARE_STYLES:
            return animated_text(cue, anim_style, w, h, 'bottom' if needs_shift else None, cont)
        body = animated_text(cue, anim_style, w, h, cont=cont)
        if not needs_shift:
            return body
        bx, by   = cue_pos(w, h, 'bottom')
        override = r"{\pos(" + f"{bx},{by}" + r")\an2}"
        if body.startswith('{'):
            close = body.index('}')
            return override[:-1] + body[1:close+1] + body[close+1:]
        return override + body

    # ── burn-in ───────────────────────────────────────────────────────────────
    width, height = get_video_dims(video_in)
    # B2.5.3 — caption sizing is now resolution-relative. font_size / outline_width /
    # margins are tuned against the pre-Visual-Quality render (long edge 1920). Visual
    # Quality renders at 720/1080/1440, and ASS Fontsize lives in PlayResY (= actual
    # height), so a fixed font_size ballooned at low res and shrank at high res. Scale
    # everything by actual_long_edge / 1920 so captions keep the SAME proportion of the
    # frame regardless of the quality tier (nested make_ass_header / cue_pos close over
    # _res_scale, which is set here before they are first called).
    _CAP_REF_LONG_EDGE = 1920.0
    _res_scale = (max(int(width), int(height)) / _CAP_REF_LONG_EDGE) if (width and height) else 1.0
    if _res_scale <= 0:
        _res_scale = 1.0
    font_size = max(12, int(round(font_size * _res_scale)))
    try:
        outline_width = f"{max(0.0, float(outline_width) * _res_scale):.2f}"
    except (TypeError, ValueError):
        pass
    # B2.16 — GLOBAL font first (family name or path), else a system fallback. libass matches
    # the Style Fontname by family NAME, so pass the family for a name; for a path, use its stem.
    font_name = ''
    if global_font:
        if os.path.exists(global_font):
            font_name = os.path.splitext(os.path.basename(global_font))[0]
        else:
            font_name = global_font   # treat as an installed family name
    if not font_name:
        font_path = find_system_font()
        font_name = os.path.splitext(os.path.basename(font_path))[0] if font_path else 'Arial'
    header    = make_ass_header(width, height, font_name)
    cues      = group_cues(words)
    windows   = load_overlay_windows()
    if windows and position in ('top', 'center'):
        print(f"  [captions] {len(windows)} overlay/takeover window(s) — captions will shift during overlaps.", file=sys.stderr)

    card_windows = load_card_windows()   # chapters takeovers (silent gaps)

    # ── B2.16 Takeover Cards (Explainer / Focused) — TEXT SYNC ENGINE ───────────
    # Each planned card (render/card_plan.json) is matched to the transcript to find the exact
    # window its words are spoken, then rendered as CENTRED, voice-synced karaoke text using the
    # user's highlight style. The refined window is written back so Render composites the card
    # background over the same span, and it joins the interlock set (captions OFF there).
    def load_card_plan():
        cp = _load_json(os.path.join(project_path, "render", "card_plan.json"), {})
        return cp.get("cards", []) if isinstance(cp, dict) else []

    def load_scene_windows():
        timings = _load_json(_resolve_input(os.path.join(project_path, "render", "scene_timings.json"),
                                            os.path.join(project_path, "scene_timings.json")), [])
        out = {}
        for t in timings:
            if t.get("type", "scene") == "scene" and "scene" in t:
                out[int(t["scene"])] = (float(t.get("start", 0)), float(t.get("end", 0)))
        return out

    def _norm_tok(s):
        return [tok for tok in ''.join(ch if (ch.isalnum() or ch.isspace()) else ' '
                                       for ch in str(s).lower()).split() if tok]

    def _first_tok(w):
        t = _norm_tok(w.get('text', ''))
        return t[0] if t else ''

    def resolve_card_window(card):
        """Match the card's text against the transcript within its scene window; return
        (start_ms, end_ms, run_words) or None. Falls back to the whole scene window."""
        toks = _norm_tok(card.get('text', ''))
        if not toks:
            return None
        sw = scene_windows.get(int(card.get('scene', 0) or 0))
        lo, hi = (sw[0] * 1000.0, sw[1] * 1000.0) if sw else (0.0, float('inf'))
        pool = [w for w in words if lo <= w['start'] < hi] or list(words)
        if not pool:
            return None
        wtok = [_first_tok(w) for w in pool]
        for i, t in enumerate(wtok):
            if t and t == toks[0]:
                run = pool[i:i + len(toks)]
                if run:
                    return (run[0]['start'], run[-1]['end'], run)
        return (pool[0]['start'], pool[-1]['end'], pool)   # fallback: whole scene window

    def _is_rtl_text(s):
        # True if the string contains any right-to-left script char (Arabic / Hebrew ranges,
        # incl. presentation forms). Captions on 'pop' render the whole line as one run so bidi
        # works, but the CARD karaoke splits per word, which breaks Arabic shaping + word order.
        for ch in str(s):
            o = ord(ch)
            if (0x0590 <= o <= 0x05FF or 0x0600 <= o <= 0x06FF or 0x0700 <= o <= 0x074F or
                    0x0750 <= o <= 0x077F or 0x08A0 <= o <= 0x08FF or
                    0xFB1D <= o <= 0xFDFF or 0xFE70 <= o <= 0xFEFF):
                return True
        return False

    def card_karaoke_text(cwords, style):
        # Per-word active highlight timed to the voice (renderer-safe \t transforms):
        #   underline    — active word lifts to the accent colour (card text underlined via \u1)
        #   outline_fill — active word gets an accent FILL + a thick dark OUTLINE while spoken
        full_txt = ' '.join(str(w.get('text', '')) for w in cwords)
        if _is_rtl_text(full_txt):
            # RTL (Arabic/Hebrew): per-word {\r}word segments break bidi shaping + reverse word
            # order. Render the WHOLE phrase as ONE run (correct shaping/direction) and wrap it in
            # RLE…PDF bidi controls so libass lays it out right-to-left even with mixed digits/latin.
            # The voice-synced per-word highlight is dropped for RTL — correct text wins.
            return "‫" + ass_escape(full_txt) + "‬"   # RLE … PDF
        def _c(cv):
            return r"\c&H" + cv[4:] + "&"
        base_c = _c(ass_color(font_color))
        acc_c  = _c(_HIGHLIGHT_ACCENT)
        t0 = cwords[0]['start']
        parts = []
        for w in cwords:
            ws = max(1, int(round(w['start'] - t0)))
            we = max(ws + 1, int(round(w['end'] - t0)))
            wt = ass_escape(w.get('text', ''))
            if style == 'outline_fill':
                on  = r"\t(%d,%d,%s\bord6\3c&H101010&)" % (ws, ws, acc_c)
                off = r"\t(%d,%d,%s\bord2\3c&H000000&)" % (we, we, base_c)
            else:  # 'underline'
                on  = r"\t(%d,%d,%s)" % (ws, ws, acc_c)
                off = r"\t(%d,%d,%s)" % (we, we, base_c)
            parts.append("{\\r" + on + off + "}" + wt)
        return ' '.join(parts)

    cards         = load_card_plan()
    scene_windows = load_scene_windows()
    card_dialogues = []
    card_text_windows = []
    updated_cards = []
    for card in cards:
        rw = resolve_card_window(card)
        if not rw:
            updated_cards.append(card)
            continue
        cs_ms, ce_ms, cwords = rw
        cs, ce = cs_ms / 1000.0, ce_ms / 1000.0
        card_text_windows.append((cs, ce))
        c2 = dict(card); c2['start'] = round(cs, 3); c2['end'] = round(ce, 3)
        updated_cards.append(c2)
        nw = len(cwords)
        card_fs = int(height * (0.11 if nw <= 3 else (0.08 if nw <= 6 else 0.06)))
        cx, cy = width // 2, height // 2
        style_prefix = r"\u1" if highlight_style == 'underline' else ""
        body = card_karaoke_text(cwords, highlight_style)
        override = r"{\an5\pos(%d,%d)\fs%d\bord2%s}" % (cx, cy, card_fs, style_prefix)
        merged = (override[:-1] + body[1:]) if body.startswith('{') else (override + body)
        card_dialogues.append((cs, ce, merged))
    if cards:
        try:
            with open(os.path.join(project_path, "render", "card_plan.json"), "w", encoding="utf-8") as f:
                json.dump({"cards": updated_cards}, f)
        except OSError:
            pass
        print(f"  [captions] {len(card_dialogues)} Takeover Card(s) synced to the voice "
              f"(style={highlight_style}).", file=sys.stderr)

    # Interlock set — captions turn OFF under ANY card (chapters takeovers + explainer/focused).
    suppress_windows = list(card_windows) + list(card_text_windows)
    if suppress_windows:
        print(f"  [captions] {len(suppress_windows)} Takeover Card window(s) — captions OFF while a card is active (interlock).", file=sys.stderr)

    lines = [header]
    _suppressed = 0
    for cue, cont in cues:
        start = cue[0]['start'] / 1000.0; end = cue[-1]['end'] / 1000.0
        # B2.16 interlock — a Takeover Card owns the full screen (its own text is the card's
        # synced karaoke), so standard captions turn OFF for any cue overlapping a card window.
        if suppress_windows and cue_overlaps(start, end, suppress_windows):
            _suppressed += 1
            continue
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{cue_text(cue, width, height, windows, cont)}")
    if _suppressed:
        print(f"  [captions] {_suppressed} caption cue(s) suppressed under Takeover Cards.", file=sys.stderr)

    # Card text on Layer 1 (above captions; captions are suppressed in these windows anyway).
    for (cs, ce, body) in card_dialogues:
        lines.append(f"Dialogue: 1,{ass_time(cs)},{ass_time(ce)},Default,,0,0,0,,{body}")

    with open(ass_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    # B2.12 Stage 3 — ASS-ONLY mode. In the reordered pipeline the Captions PHASE runs
    # BEFORE Render and only needs to produce render/captions.ass (+ warm the transcription
    # cache); Render then composes overlays + captions in its own pass. Stop here — no burn,
    # no video touched. (Legacy/preview callers omit the flag and get the full burn below.)
    if ass_only:
        print(f"  [captions] ASS written ({len(cues)} cue(s)) — burn deferred to Render.",
              file=sys.stderr)
        sys.exit(0)

    ass_esc = ass_file.replace('\\', '/').replace(':', '\\:')
    # B2.4 — hardware-accelerated caption burn. Was hardcoded software libx264 /
    # preset fast / crf18, the SLOWEST-possible final pass on every captioned video.
    # Use the same hw auto-detect as render/overlays; any hw failure re-runs with
    # libx264 (poisoning the cache), so a bad hw flag degrades to software, never
    # breaks the burn.
    global _HW_ENCODER_CACHE

    def _overlay_chain(final_label):
        # fade-in/out alpha layers overlaid in order onto [0:v], ending at final_label.
        filt = []
        cur = "[0:v]"
        for i, lyr in enumerate(_final_layers, start=1):
            s = float(lyr.get("start", 0.0)); d = float(lyr.get("dur", 0.0))
            fi = float(lyr.get("fade_in", 0.3)); fo = float(lyr.get("fade_out", 0.3))
            e = s + d; fo_st = max(s, e - fo)
            filt.append(f"[{i}:v]format=rgba,"
                        f"fade=t=in:st={s:.3f}:d={max(0.001, fi):.3f}:alpha=1,"
                        f"fade=t=out:st={fo_st:.3f}:d={max(0.001, fo):.3f}:alpha=1[ov{i}]")
            nxt = f"[v{i}]" if i < len(_final_layers) else final_label
            filt.append(f"{cur}[ov{i}]overlay=0:0:enable='between(t,{s:.3f},{e:.3f})'{nxt}")
            cur = nxt
        return filt, cur

    def _run_caption_burn(v_codec, v_preset, with_overlays=False):
        cmd = ["ffmpeg", "-y", "-i", video_in]
        if with_overlays and _final_layers and _fl_dur:
            for lyr in _final_layers:
                cmd += ["-loop", "1", "-t", f"{_fl_dur:.3f}", "-i", lyr["png"]]
            chain, last = _overlay_chain("[vov]")
            chain.append(f"{last}ass='{ass_esc}'[vout]")
            cmd += ["-filter_complex", ";".join(chain), "-map", "[vout]", "-map", "0:a?", "-c:a", "copy"]
        else:
            cmd += ["-vf", f"ass='{ass_esc}'", "-c:a", "copy"]
        cmd += _h264_quality_args(v_codec, v_preset) + [video_tmp]
        return subprocess.run(cmd, capture_output=True, text=True)

    def _composite_overlays_only():
        # Never lose overlays: if the combined pass fails, bake the deferred overlays into
        # master on their own (no captions), best-effort, before the caption-only burn.
        try:
            cc, cp = _detect_hw_encoder("libx264")
            ov_tmp = os.path.join(project_path, "_tmp", "master_ovonly_tmp.mp4")
            chain, last = _overlay_chain("[vout]")
            base = ["ffmpeg", "-y", "-i", video_in]
            for lyr in _final_layers:
                base += ["-loop", "1", "-t", f"{_fl_dur:.3f}", "-i", lyr["png"]]
            base += ["-filter_complex", ";".join(chain), "-map", "[vout]", "-map", "0:a?", "-c:a", "copy"]
            r = subprocess.run(base + _h264_quality_args(cc, cp) + [ov_tmp], capture_output=True, text=True)
            if r.returncode != 0 and cc != "libx264":
                r = subprocess.run(base + _h264_quality_args("libx264", "veryfast") + [ov_tmp], capture_output=True, text=True)
            if r.returncode == 0 and os.path.exists(ov_tmp) and os.path.getsize(ov_tmp) > 1024:
                _replace_with_retry(ov_tmp, video_in)
                return True
        except Exception:
            pass
        return False

    cap_codec, cap_preset = _detect_hw_encoder("libx264")
    _with_ov = bool(_final_layers and _fl_dur)
    print(f"  [captions] Burning{' + overlays' if _with_ov else ''} in captions "
          f"({anim_style}, {_speed_raw}) via FFmpeg [{cap_codec}]...", file=sys.stderr)
    try:
        result = _run_caption_burn(cap_codec, cap_preset, _with_ov)
        if result.returncode != 0 and cap_codec != "libx264":
            print(f"  [captions] {cap_codec} encode failed — falling back to libx264.\n"
                  f"{result.stderr[-400:]}", file=sys.stderr)
            _HW_ENCODER_CACHE = ("libx264", "veryfast")   # poison so this session skips hw
            if os.path.exists(video_tmp): os.remove(video_tmp)
            result = _run_caption_burn("libx264", "veryfast", _with_ov)
        if result.returncode != 0 and _with_ov:
            # Combined overlay+caption pass failed — DON'T lose the overlays. Composite
            # them separately, then do a plain caption-only burn.
            print(f"  [captions] combined overlay+caption pass failed — compositing overlays "
                  f"separately then captions.\n{result.stderr[-300:]}", file=sys.stderr)
            if os.path.exists(video_tmp): os.remove(video_tmp)
            _composite_overlays_only()
            _with_ov = False
            result = _run_caption_burn("libx264", "veryfast", False)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed:\n{result.stderr[-800:]}")
    except Exception as e:
        print(f"  [captions] Burn-in failed ({e}) — skipping.", file=sys.stderr)
        if os.path.exists(video_tmp): os.remove(video_tmp)
        sys.exit(2)

    tmp_sz = os.path.getsize(video_tmp) if os.path.exists(video_tmp) else 0
    src_sz = os.path.getsize(video_in)
    if tmp_sz < src_sz * 0.5:
        print(f"  [captions] Output too small ({tmp_sz} vs {src_sz}) — skipping replace.", file=sys.stderr)
        if os.path.exists(video_tmp): os.remove(video_tmp)
        sys.exit(2)

    _replace_with_retry(video_tmp, video_in)
    # Deferred overlays are now baked in — clear the handshake file so nothing re-applies.
    if _final_layers or _lj:
        try:
            if os.path.exists(_final_layers_json):
                os.remove(_final_layers_json)
        except Exception:
            pass
    print(f"  [captions] Done — master_output.mp4 updated ({os.path.getsize(video_in)/1048576:.1f} MB)", file=sys.stderr)
    sys.exit(0)


# ══════════════════════════════════════════════════════════════════════════════
#  DISPATCH
# ══════════════════════════════════════════════════════════════════════════════

def cmd_scene_timings(argv):
    """B2.12 Stage 0 — child-process entry for the lightweight scene-timings phase.
    argv: project_path, aspect_ratio, timeline_path, scene_map_path, brief_path,
          kb_intensity, short_edge."""
    project_path  = argv[0] if len(argv) > 0 else "."
    aspect        = argv[1] if len(argv) > 1 else "9:16"
    timeline_path = argv[2] if len(argv) > 2 else ''
    scene_map_p   = argv[3] if len(argv) > 3 else ''
    brief_path    = argv[4] if len(argv) > 4 else ''
    kb_intensity  = argv[5] if len(argv) > 5 else 100
    short_edge     = int(argv[6]) if (len(argv) > 6 and str(argv[6]).strip().isdigit()) else 1080
    try:
        cnt = build_scene_timings_file(project_path, aspect, timeline_path,
                                       scene_map_p, brief_path, kb_intensity,
                                       'flat', short_edge)
    except _SceneTimingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"scene_timings.json written ({cnt} node(s)).", file=sys.stderr)
    sys.exit(0)


def compose_filter_overlays(project_path, intensity=50, overrides=None, video_in=None):
    """B2.18.3 — composite ALPHA (ProRes 4444 .mov) FILTER effect clips as windowed overlays onto the
    finished master_output.mp4. For each scene in scene_timings.json whose 'filter' resolves to a
    catalog asset (name -> 3rd metadata column -> Assets/Filters/<file>), the clip is looped +
    cover-scaled + alpha-composited ONLY within that scene's [start, start+dur] window, at
    opacity = intensity/100. Manual `overrides` ({scene#: name}) win over the AI's per-scene pick
    ('none'/'' removes one). Returns 'ok' | 'skipped'; raises only on a genuine encode failure.
    Pure (no sys.exit) so the render server can call it directly."""
    import subprocess
    intensity = max(0, min(100, int(intensity)))
    if intensity <= 0:
        return "skipped"
    video_in = video_in or os.path.join(project_path, "master_output.mp4")
    if not os.path.exists(video_in):
        return "skipped"
    st = _load_json(os.path.join(project_path, "render", "scene_timings.json"), None)
    if not isinstance(st, list):
        return "skipped"

    overrides = overrides or {}
    catalog   = load_filter_catalog(force=True)
    fdir      = _filters_root()
    MAX_FX    = 12   # perf ceiling — each overlay is a looped video input decoded alongside the base

    events = []
    for e in st:
        if not isinstance(e, dict) or e.get("type") != "scene":
            continue
        sn = e.get("scene")
        name = overrides.get(str(sn), overrides.get(sn, e.get("filter")))
        if not name or str(name).strip().lower() in ("none", ""):
            continue
        entry = catalog.get(str(name).strip().lower())
        vf = entry.get("vf_file") if entry else None
        if not vf:
            print(f"  [filters] scene {sn}: '{name}' not in catalog / no asset file — skipped.", file=sys.stderr)
            continue
        path = vf if os.path.isabs(vf) else os.path.join(fdir, vf)
        if not os.path.exists(path):
            print(f"  [filters] scene {sn}: asset not found: {path}", file=sys.stderr)
            continue
        try:
            start = float(e.get("start", 0.0)); dur = float(e.get("duration", 0.0))
        except (TypeError, ValueError):
            continue
        if dur <= 0:
            continue
        events.append({"path": path, "start": round(start, 3), "dur": round(dur, 3), "name": name})
        if len(events) >= MAX_FX:
            print(f"  [filters] capped at {MAX_FX} overlay(s) for this render.", file=sys.stderr)
            break
    if not events:
        return "skipped"

    dims = _ffprobe_video(video_in)
    if not dims:
        return "skipped"
    W, H, video_dur = dims
    op = round(intensity / 100.0, 3)

    # scale to COVER (crop overflow), keep alpha, scale opacity (colorchannelmixer aa), then
    # setpts so the clip PLAYS FROM ITS OWN START at the scene start; -stream_loop -1 fills any
    # window length; overlay honors the ProRes 4444 alpha and enable=between clips it to the window.
    filt = []
    cur = "[0:v]"
    used = 0
    for i, ev in enumerate(events, start=1):
        s = ev["start"]; e = min(ev["start"] + ev["dur"], video_dur)
        if e <= s:
            continue
        used += 1
        filt.append(
            f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"format=rgba,colorchannelmixer=aa={op},setpts=PTS-STARTPTS+{s:.3f}/TB[fx{i}]")
        nxt = f"[v{i}]"
        filt.append(f"{cur}[fx{i}]overlay=0:0:enable='between(t,{s:.3f},{e:.3f})'{nxt}")
        cur = nxt
    if used == 0:
        return "skipped"
    filtergraph = ";".join(filt)

    tmp = os.path.join(project_path, "_tmp", "master_filters_tmp.mp4")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)

    def _run(codec, preset):
        c = ["ffmpeg", "-y", "-i", video_in]
        for ev in events:
            c += ["-stream_loop", "-1", "-i", ev["path"]]
        # -t caps the output to the base duration (the looped fx inputs are otherwise infinite).
        c += ["-filter_complex", filtergraph, "-map", cur, "-map", "0:a?", "-c:a", "copy",
              "-t", f"{video_dur:.3f}"] + _h264_quality_args(codec, preset) + [tmp]
        return subprocess.run(c, capture_output=True, text=True)

    global _HW_ENCODER_CACHE
    codec, preset = _detect_hw_encoder("libx264")
    print(f"  [filters] compositing {used} effect overlay(s) via ffmpeg [{codec}]...", file=sys.stderr)
    r = _run(codec, preset)
    if r.returncode != 0 and codec != "libx264":
        print(f"  [filters] {codec} failed — retrying libx264.\n{r.stderr[-300:]}", file=sys.stderr)
        _HW_ENCODER_CACHE = ("libx264", "veryfast")
        if os.path.exists(tmp):
            os.remove(tmp)
        r = _run("libx264", "veryfast")
    if r.returncode != 0:
        raise RuntimeError(f"filter overlay encode failed:\n{r.stderr[-700:]}")
    if not (os.path.exists(tmp) and os.path.getsize(tmp) > os.path.getsize(video_in) * 0.4):
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError("filter overlay output suspiciously small — kept the un-filtered master")
    _replace_with_retry(tmp, video_in)
    print(f"  [filters] done — {used} effect overlay(s) composited "
          f"({os.path.getsize(video_in) / 1048576:.1f} MB).", file=sys.stderr)
    return "ok"


def cmd_filter_overlay(argv):
    """avg.py filter_overlay <project_path> [intensity]   (fallback path for the render server)."""
    if len(argv) < 1:
        print("Usage: avg.py filter_overlay <project_path> [intensity]", file=sys.stderr)
        sys.exit(1)
    project_path = argv[0]
    intensity = int(argv[1]) if (len(argv) > 1 and str(argv[1]).strip().lstrip("-").isdigit()) else 50
    overrides = _load_json(os.path.join(project_path, "render", "filter_overrides.json"), {}) or {}
    try:
        compose_filter_overlays(project_path, intensity=intensity, overrides=overrides)
    except Exception as e:
        # Non-fatal: a filter-pass failure must never lose the finished video.
        print(f"  [filters] error (non-fatal): {e}", file=sys.stderr)
        sys.exit(0)
    sys.exit(0)


_COMMANDS = {
    "safezones":       cmd_safezones,
    "splice":          cmd_splice,
    "splice_flat":     cmd_splice_flat,
    "remove_silences": cmd_remove_silences,
    "scene_timings":   cmd_scene_timings,
    "assemble":        cmd_assemble,
    "overlay_assets":  cmd_overlay_assets,
    "overlay_render":  cmd_overlay_render,
    "caption_burn":    cmd_caption_burn,
    "filter_overlay":  cmd_filter_overlay,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in _COMMANDS:
        print("Usage: python avg.py <command> [args...]", file=sys.stderr)
        print(f"Commands: {', '.join(_COMMANDS)}", file=sys.stderr)
        sys.exit(1)
    _COMMANDS[sys.argv[1]](sys.argv[2:])
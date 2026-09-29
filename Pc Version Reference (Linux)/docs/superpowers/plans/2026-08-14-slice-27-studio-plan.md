# Slice 27 — Studio screen: Implementation Plan

Implements `docs/superpowers/specs/2026-08-14-slice-27-studio-design.md`.
Read that first.

Verify: `.venv-build\Scripts\python.exe -m pytest tests -q`,
`.venv-build\Scripts\python.exe -m mypy avg`, `cd web && npm run build
&& npm run lint`. No frontend test runner exists (confirmed Slice 22) -
manual checks are called out per task.

## Files touched

| File | Change |
|---|---|
| `web/src/App.tsx` | `openProject` persists the id; new `onStudio` |
| `web/src/Rail.tsx` | Studio item becomes a real button |
| `avg/pipeline/phases/render.py` | `_BASE_PERCENT_SPAN` stays, nothing else changes here |
| `avg/api/routes_takes.py` | `GET .../takes` gains a `substates` field |
| `web/src/api.ts` | `TakesResponse` gains `substates`; `getTakes` unchanged |
| `tests/api/test_routes_takes.py` | +2 tests |
| `web/src/useMediaPlayer.ts` | New - the shared player state/controls hook |
| `web/src/Player.tsx` | Rewritten on the hook, custom controls, same `{ src }` API |
| `web/src/VoicePlayer.tsx` | New - the audio counterpart |
| `web/src/Icons.tsx` | `IconPause`, `IconVolume`, `IconMute` |
| `web/src/tokens.css` | `.media-player` family (shared by both wrappers), seek-bar styling |
| `web/src/artifacts/bodies.tsx` | `VoiceBody` uses `VoicePlayer`; `ScriptBody` gets a partial state |
| `web/src/PreviewPanel.tsx` | `VoicePreview` uses `VoicePlayer` |
| `web/src/Studio.tsx` | `ArtifactDetail`'s render branches use `substates`; base/composite messaging |

Seven tasks. Order: nav (self-contained) → backend substates (needed by
Task 6) → the shared player hook → the two player wrappers → Script's
partial state (independent of everything else) → Render's substate UI
(needs Task 2's backend field) → regression.

---

## Task 1 — Studio nav opens the latest opened project (#5)

**Files:** `web/src/App.tsx`, `web/src/Rail.tsx`

### Step 1.1 — App.tsx

Replace:

```typescript
  function openProject(id: string) {
    setStartError(null);
    setScreen({ name: "project", id });
  }
```

with:

```typescript
  // "Latest opened", not latest created and not the current take within a
  // project (spec §1's explicit distinction) - every navigation-to-Studio
  // path already goes through this one function, so recording it here
  // covers Home's card click, Create's onCreated, and Run again/Resume
  // (openAndRender, below) without instrumenting each separately.
  const LAST_PROJECT_KEY = "avg.studio.lastProjectId";

  function openProject(id: string) {
    setStartError(null);
    setScreen({ name: "project", id });
    try {
      localStorage.setItem(LAST_PROJECT_KEY, id);
    } catch {
      // Best-effort - a UI convenience, never worth failing navigation over.
    }
  }

  /** The Studio rail item's own handler - opens whichever project was last
   * opened, confirming it still exists first (archived counts, since
   * archiving only hides a project from Home's grid, it doesn't remove
   * it - only a genuinely deleted or never-yet-opened id falls through).
   * No stored id, or a stale one, goes Home instead of a dead click. */
  function openStudio() {
    let lastId: string | null = null;
    try {
      lastId = localStorage.getItem(LAST_PROJECT_KEY);
    } catch {
      lastId = null;
    }
    if (lastId && projects?.some((p) => p.id === lastId)) {
      openProject(lastId);
    } else {
      setScreen({ name: "home" });
    }
  }
```

Wire it into `Rail`. Replace:

```tsx
        onCreate={() => setScreen({ name: "create" })}
        onChannels={() => setScreen({ name: "channels" })}
        onSettings={() => setScreen({ name: "settings" })}
        onHealth={() => setScreen({ name: "health" })}
```

with:

```tsx
        onCreate={() => setScreen({ name: "create" })}
        onStudio={openStudio}
        onChannels={() => setScreen({ name: "channels" })}
        onSettings={() => setScreen({ name: "settings" })}
        onHealth={() => setScreen({ name: "health" })}
```

### Step 1.2 — Rail.tsx

Add the prop. Replace:

```typescript
  active: RailSection;
  onHome: () => void;
  onCreate: () => void;
  onChannels: () => void;
```

with:

```typescript
  active: RailSection;
  onHome: () => void;
  onCreate: () => void;
  /** Opens the latest opened project - App.tsx's own `openStudio`, which
   * already confirms the id still exists and falls back to Home. This
   * component doesn't make that decision, only triggers it. */
  onStudio: () => void;
  onChannels: () => void;
```

Add it to the destructured params. Replace:

```typescript
export default function Rail({
  active,
  onHome,
  onCreate,
  onChannels,
```

with:

```typescript
export default function Rail({
  active,
  onHome,
  onCreate,
  onStudio,
  onChannels,
```

Turn the item into a real button. Replace:

```tsx
      <span className={`rail__item${active === "studio" ? " rail__item--on" : ""}`} aria-hidden="true">
        <IconFilm />
      </span>
```

with:

```tsx
      <button
        type="button"
        className={`rail__item${active === "studio" ? " rail__item--on" : ""}`}
        onClick={onStudio}
        title="Studio"
        aria-label="Studio — your latest opened video"
        aria-current={active === "studio" ? "page" : undefined}
      >
        <IconFilm />
      </button>
```

### Step 1.3 — verify

`cd web && npm run build && npm run lint`. **Manual check:** open a
project, navigate to Home, click the Studio rail icon - lands back on
that same project. With no project ever opened (fresh profile, or
`localStorage` cleared), clicking Studio goes Home instead of doing
nothing.

### Step 1.4 — commit

`feat: Studio rail opens the latest opened project — Slice 27 Task 1`

---

## Task 2 — Backend: per-take substates

**Files:** `avg/api/routes_takes.py`, `tests/api/test_routes_takes.py`

### Step 2.1 — failing test

`tests/api/test_routes_takes.py` already has `_project`/`_seed_take1`/
`_add_second_take` helpers this test reuses - but note `_add_second_take`
writes a 14-byte fake `master_output.mp4`, well under
`avg.pipeline.phases.render._MIN_OUTPUT_BYTES` (1024) - too small to
register as anything but `"none"`, so it's not useful for exercising the
`"base"` state here. Append a purpose-built test instead of reusing that
helper for this specific case:

```python
def test_takes_response_includes_a_substate_per_take(client, paths):
    """Mirrors GET /api/projects' own per-project script_substate/
    render_substate computation (unrelated to Slice 20's UserAssets work),
    just looped over every take instead of only the project's current one
    - Studio can view any take, not only the current one.
    """
    project = _project(paths)
    _seed_take1(project)  # script: both files written -> "done". render: no master_output.mp4 -> "none".
    take2 = take_folder(project, 2)
    take2.mkdir(parents=True, exist_ok=True)
    # >= _MIN_OUTPUT_BYTES (1024), unlike _add_second_take's own 14-byte
    # fake - real enough to register as the "base" substate.
    (take2 / "master_output.mp4").write_bytes(b"x" * 2048)
    project.takes.append(Take(id=2, label="Take 2", created=project.created, phases={}, master_output=None))
    client.app.state.projects[project.id] = project

    r = client.get(f"/api/projects/{project.id}/takes", headers=AUTH)
    assert r.status_code == 200
    substates = r.json()["substates"]

    assert substates["1"] == {"script": "done", "render": "none"}
    # script_substate returns "pending", not "none", when neither file
    # exists - confirmed against its own return type
    # (Literal["pending", "partial", "done"]) rather than assumed.
    assert substates["2"] == {"script": "pending", "render": "base"}
```

(`take_folder` needs to already be imported in this test file - it is,
used by the existing helpers above. `Take` likewise.)

Run: `.venv-build\Scripts\python.exe -m pytest tests/api/test_routes_takes.py -q`
— fails, `substates` isn't in the response yet.

### Step 2.2 — implement

In `avg/api/routes_takes.py`, add the imports (check the file's current
import block first - `render_substate`/`script_substate` are imported
locally inside a function in `app.py`, not at module level, matching that
file's own "reaches outside pure I/O" convention for this exact import;
mirror that here rather than a top-level import). Replace:

```python
@router.get("/api/projects/{project_id}/takes")
async def list_takes(project_id: str, request: Request) -> dict:
    project = _get_project(request.app.state, project_id)
    # Safe on every call (idempotent, a no-op once takes exist) - see
    # `avg.core.takes.migrate_to_takes`'s own docstring. A legacy project
    # that has never touched this API still shows a "Take 1" - and, since
    # Task 3.5, that take 1 is persisted immediately (`_migrate_and_persist`)
    # rather than living only in this process's memory until some later
    # mutation happens to save it.
    _migrate_and_persist(project)
    return {
        "takes": list(project.takes),
        "current_take_id": project.current_take_id,
        # Task 4: the Studio header's disk-usage figure - see
        # `_folder_size_bytes`. The WHOLE project folder (every take's own
        # subfolder plus any legacy top-level artifacts), not one take's
        # slice of it - "how much is this project costing me on disk" is a
        # project-level question, matching the spec's own header placement.
        "disk_bytes": _folder_size_bytes(Path(project.folder)),
    }
```

with:

```python
@router.get("/api/projects/{project_id}/takes")
async def list_takes(project_id: str, request: Request) -> dict:
    # Imported here, not at module level, matching avg/api/app.py's own
    # handling of these two - "reaches outside pure I/O" is worth keeping
    # visible at the call site for a route file this thin.
    from avg.pipeline.phases.render import render_substate  # noqa: PLC0415
    from avg.pipeline.phases.writing import script_substate  # noqa: PLC0415

    project = _get_project(request.app.state, project_id)
    # Safe on every call (idempotent, a no-op once takes exist) - see
    # `avg.core.takes.migrate_to_takes`'s own docstring. A legacy project
    # that has never touched this API still shows a "Take 1" - and, since
    # Task 3.5, that take 1 is persisted immediately (`_migrate_and_persist`)
    # rather than living only in this process's memory until some later
    # mutation happens to save it.
    _migrate_and_persist(project)
    return {
        "takes": list(project.takes),
        "current_take_id": project.current_take_id,
        # Task 4: the Studio header's disk-usage figure - see
        # `_folder_size_bytes`. The WHOLE project folder (every take's own
        # subfolder plus any legacy top-level artifacts), not one take's
        # slice of it - "how much is this project costing me on disk" is a
        # project-level question, matching the spec's own header placement.
        "disk_bytes": _folder_size_bytes(Path(project.folder)),
        # Slice 27: the SAME two functions `GET /api/projects` already
        # calls for a project's current take, looped over every take here -
        # Studio can view any take, not only the current one, and the
        # "why has my video no captions" question `render_substate`'s own
        # docstring exists to answer applies to whichever take is open.
        "substates": {
            str(take.id): {
                "script": script_substate(take_root(project, take.id) / "script"),
                "render": render_substate(take_root(project, take.id)),
            }
            for take in project.takes
        },
    }
```

(`take_root` needs to already be imported in this file - check first;
it almost certainly is, since `_folder_size_bytes` and the rest of this
router already work with take folders throughout.)

### Step 2.3 — verify

`.venv-build\Scripts\python.exe -m pytest tests/api/test_routes_takes.py -q`
then the full suite and mypy.

### Step 2.4 — commit

`feat: per-take script/render substates in the takes response — Slice 27 Task 2`

---

## Task 3 — The shared media player hook

**Files:** `web/src/useMediaPlayer.ts` (new)

### Step 3.1 — implement

Create `web/src/useMediaPlayer.ts`:

```typescript
import { useEffect, useRef, useState } from "react";

export interface MediaPlayerState {
  playing: boolean;
  currentTime: number;
  duration: number;
  buffering: boolean;
  muted: boolean;
  /** `null` until metadata has loaded, or a real message once the element
   * fires its own `error` event - never a guess at what went wrong, just
   * whatever the browser itself reports. */
  error: string | null;
}

export interface MediaPlayerControls {
  togglePlay: () => void;
  seek: (seconds: number) => void;
  toggleMute: () => void;
}

/**
 * Every bit of state and every control both `Player` (video) and
 * `VoicePlayer` (audio) need - `HTMLVideoElement`/`HTMLAudioElement` are
 * both `HTMLMediaElement`, so this hook works unmodified against either,
 * given a ref to one. One state machine, two thin presentational
 * wrappers on top of it - not two separate players that happen to look
 * similar (Slice 27 spec §2).
 */
export function useMediaPlayer(ref: React.RefObject<HTMLMediaElement | null>): [MediaPlayerState, MediaPlayerControls] {
  const [state, setState] = useState<MediaPlayerState>({
    playing: false,
    currentTime: 0,
    duration: 0,
    buffering: false,
    muted: false,
    error: null,
  });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const onPlay = () => setState((s) => ({ ...s, playing: true }));
    const onPause = () => setState((s) => ({ ...s, playing: false }));
    const onTimeUpdate = () => setState((s) => ({ ...s, currentTime: el.currentTime }));
    const onDurationChange = () => setState((s) => ({ ...s, duration: Number.isFinite(el.duration) ? el.duration : 0 }));
    const onWaiting = () => setState((s) => ({ ...s, buffering: true }));
    const onCanPlay = () => setState((s) => ({ ...s, buffering: false }));
    const onVolumeChange = () => setState((s) => ({ ...s, muted: el.muted }));
    const onError = () => setState((s) => ({ ...s, error: el.error?.message || "This media could not be played." }));
    const onEnded = () => setState((s) => ({ ...s, playing: false }));

    el.addEventListener("play", onPlay);
    el.addEventListener("pause", onPause);
    el.addEventListener("timeupdate", onTimeUpdate);
    el.addEventListener("durationchange", onDurationChange);
    el.addEventListener("waiting", onWaiting);
    el.addEventListener("canplay", onCanPlay);
    el.addEventListener("volumechange", onVolumeChange);
    el.addEventListener("error", onError);
    el.addEventListener("ended", onEnded);
    return () => {
      el.removeEventListener("play", onPlay);
      el.removeEventListener("pause", onPause);
      el.removeEventListener("timeupdate", onTimeUpdate);
      el.removeEventListener("durationchange", onDurationChange);
      el.removeEventListener("waiting", onWaiting);
      el.removeEventListener("canplay", onCanPlay);
      el.removeEventListener("volumechange", onVolumeChange);
      el.removeEventListener("error", onError);
      el.removeEventListener("ended", onEnded);
    };
    // `ref` is a stable object identity across renders (a React ref), so
    // this only re-runs when the ELEMENT it points at actually changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ref.current]);

  const controls: MediaPlayerControls = {
    togglePlay: () => {
      const el = ref.current;
      if (!el) return;
      if (el.paused) void el.play();
      else el.pause();
    },
    seek: (seconds: number) => {
      const el = ref.current;
      if (!el) return;
      el.currentTime = Math.min(Math.max(seconds, 0), state.duration || seconds);
    },
    toggleMute: () => {
      const el = ref.current;
      if (!el) return;
      el.muted = !el.muted;
    },
  };

  return [state, controls];
}

/** Seconds of media playback → `M:SS`. A THIRD small time formatter in
 * this codebase (`Studio.tsx` and `TakeStrip.tsx` each already have their
 * own `formatDuration`, for milliseconds, in a phase-duration context) -
 * deliberately not a consolidation of those two: this one is about media
 * playback position, a different concern that happens to share a format,
 * and untangling the existing pair's own small differences is unrelated
 * to this slice.
 */
export function formatPlaybackTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
```

### Step 3.2 — verify

`cd web && npm run build && npm run lint`. Nothing consumes this yet -
this step only confirms it compiles cleanly on its own.

### Step 3.3 — commit

`feat: useMediaPlayer — shared state for the video/voice players — Slice 27 Task 3`

---

## Task 4 — `Player` (video) and `VoicePlayer` (audio), custom controls

**Files:** `web/src/Player.tsx`, `web/src/VoicePlayer.tsx` (new),
`web/src/Icons.tsx`, `web/src/tokens.css`, `web/src/artifacts/bodies.tsx`,
`web/src/PreviewPanel.tsx`

### Step 4.1 — icons

In `web/src/Icons.tsx`, add after `IconStop`:

```tsx
export function IconPause({ size = 14 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <rect x="6" y="5" width="4" height="14" rx="1.2" />
      <rect x="14" y="5" width="4" height="14" rx="1.2" />
    </svg>
  );
}

export function IconVolume({ size = 17 }: IconProps) {
  return (
    <svg width={size} height={size} {...base} aria-hidden="true">
      <path d="M4 9v6h4l5 4V5L8 9H4Z" strokeLinejoin="round" />
      <path d="M16.5 9a4.5 4.5 0 0 1 0 6M19 6.5a8 8 0 0 1 0 11" />
    </svg>
  );
}

export function IconMute({ size = 17 }: IconProps) {
  return (
    <svg width={size} height={size} {...base} aria-hidden="true">
      <path d="M4 9v6h4l5 4V5L8 9H4Z" strokeLinejoin="round" />
      <path d="M16 9l5 6M21 9l-5 6" />
    </svg>
  );
}
```

### Step 4.2 — CSS

In `web/src/tokens.css`, replace the `.player` rule:

```css
.player {
  background: #0d0b09;
  border-radius: var(--radius-lg);
  overflow: hidden;
  border: 1px solid var(--line-2);
  box-shadow: var(--shadow-md);
  max-width: 300px;
}
```

with (keeps the video-specific frame; adds the shared control-row shell
both wrappers use):

```css
.player {
  background: #0d0b09;
  border-radius: var(--radius-lg);
  overflow: hidden;
  border: 1px solid var(--line-2);
  box-shadow: var(--shadow-md);
  max-width: 300px;
}

.media-player {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  background: var(--surface-2);
  border-radius: var(--radius-md);
}

.media-player--voice {
  max-width: 520px;
}

.media-player__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 30px;
  height: 30px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--text);
  cursor: pointer;
}

.media-player__btn:hover {
  background: var(--surface-3);
}

.media-player__time {
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
  font-size: 12px;
  color: var(--muted);
  min-width: 34px;
}

.media-player__seek {
  flex: 1;
  appearance: none;
  height: 4px;
  border-radius: 2px;
  background: var(--line-2);
  outline-offset: 4px;
}

.media-player__seek::-webkit-slider-thumb {
  appearance: none;
  width: 13px;
  height: 13px;
  border-radius: 50%;
  background: var(--accent);
  cursor: pointer;
}

.media-player__seek::-moz-range-thumb {
  width: 13px;
  height: 13px;
  border: none;
  border-radius: 50%;
  background: var(--accent);
  cursor: pointer;
}

.media-player__error {
  font-size: 12px;
  color: var(--danger);
}
```

(Confirm `--surface-2`/`--surface-3`/`--danger` are the actual token
names this file uses elsewhere - adjust to match if a nearby rule uses
different names; the intent is "reuse existing tokens," not these exact
strings.)

Replace `.artifact-audio audio`'s rule, since the raw element it targeted
is going away:

```css
.artifact-audio audio {
  width: 100%;
  max-width: 520px;
  margin-top: 8px;
}
```

with:

```css
.artifact-audio .media-player {
  margin-top: 8px;
}
```

### Step 4.3 — `Player.tsx`

Replace the whole file:

```tsx
import { useRef } from "react";
import { IconAlert, IconPause, IconPlay } from "./Icons";
import { formatPlaybackTime, useMediaPlayer } from "./useMediaPlayer";

export interface PlayerProps {
  /** Full URL to `GET /api/projects/{id}/video`, already carrying the
   * session token as a `?token=` query param — a plain `<video>` element
   * issues its own request and can't attach the `X-AVG-Token` header
   * `api.ts` uses everywhere else (see `avg/api/deps.py`'s docstring). */
  src: string;
}

/**
 * The inline video player, on `useMediaPlayer`'s shared state - a real
 * reversal of the previous build's deliberate choice to keep the
 * browser's own `<video controls>` (Slice 27; see the design spec §2 for
 * why the earlier reasoning no longer applies). `.player` frames the
 * video itself; `.media-player` below it is the same control row
 * `VoicePlayer` uses, not a video-specific one.
 */
export default function Player({ src }: PlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [state, controls] = useMediaPlayer(videoRef);

  return (
    <div className="player">
      {/* eslint-disable-next-line jsx-a11y/media-has-caption -- legacy renders carry no caption track to attach */}
      <video ref={videoRef} preload="metadata" src={src} onClick={controls.togglePlay} />
      <div className="media-player">
        <button
          type="button"
          className="media-player__btn"
          onClick={controls.togglePlay}
          aria-label={state.playing ? "Pause" : "Play"}
        >
          {state.playing ? <IconPause /> : <IconPlay />}
        </button>
        <span className="media-player__time">{formatPlaybackTime(state.currentTime)}</span>
        <input
          type="range"
          className="media-player__seek"
          min={0}
          max={state.duration || 0}
          step={0.1}
          value={state.currentTime}
          onChange={(e) => controls.seek(Number(e.target.value))}
          aria-label="Seek"
        />
        <span className="media-player__time">{formatPlaybackTime(state.duration)}</span>
      </div>
      {state.error && (
        <p className="media-player__error">
          <IconAlert /> {state.error}
        </p>
      )}
    </div>
  );
}
```

(Mute isn't in the video player's row - the render preview plays with
its own audio track at full volume by default and this app has no prior
per-player volume preference to restore; `VoicePlayer` below does need
it, since the whole point of several voice takes is comparing them
without earbud-blast surprises between narration and a UI sound.
Reconsider if that reasoning doesn't hold once this is actually
running.)

### Step 4.4 — `VoicePlayer.tsx`

Create `web/src/VoicePlayer.tsx`:

```tsx
import { useRef } from "react";
import { IconMute, IconPause, IconPlay, IconVolume } from "./Icons";
import { formatPlaybackTime, useMediaPlayer } from "./useMediaPlayer";

export interface VoicePlayerProps {
  src: string;
  /** A short label for the aria-label ("Raw narration", "Optimized
   * narration", "Sample") - three call sites (VoiceBody's two tracks,
   * PreviewPanel's one sample) share this component and would otherwise
   * all announce identically to a screen reader. */
  label?: string;
}

/** The audio counterpart to `Player` - same `useMediaPlayer` state, same
 * `.media-player` row, no video frame around it. Replaces every
 * `<audio controls>` in the app (Slice 27 spec §2): `VoiceBody`'s two
 * narration tracks and `PreviewPanel.tsx`'s paid-sample preview.
 */
export default function VoicePlayer({ src, label = "Audio" }: VoicePlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [state, controls] = useMediaPlayer(audioRef);

  return (
    <div className="media-player media-player--voice">
      {/* eslint-disable-next-line jsx-a11y/media-has-caption -- narration has no separate transcript track to attach here */}
      <audio ref={audioRef} preload="none" src={src} />
      <button
        type="button"
        className="media-player__btn"
        onClick={controls.togglePlay}
        aria-label={`${state.playing ? "Pause" : "Play"} ${label}`}
      >
        {state.playing ? <IconPause /> : <IconPlay />}
      </button>
      <span className="media-player__time">{formatPlaybackTime(state.currentTime)}</span>
      <input
        type="range"
        className="media-player__seek"
        min={0}
        max={state.duration || 0}
        step={0.1}
        value={state.currentTime}
        onChange={(e) => controls.seek(Number(e.target.value))}
        aria-label={`Seek ${label}`}
      />
      <span className="media-player__time">{formatPlaybackTime(state.duration)}</span>
      <button
        type="button"
        className="media-player__btn"
        onClick={controls.toggleMute}
        aria-label={state.muted ? "Unmute" : "Mute"}
      >
        {state.muted ? <IconMute /> : <IconVolume />}
      </button>
      {state.error && <span className="media-player__error">{state.error}</span>}
    </div>
  );
}
```

### Step 4.5 — wire it into `VoiceBody`

In `web/src/artifacts/bodies.tsx`, add the import near the top (check the
current import block first) and replace:

```tsx
      {has("raw_voice") && (
        <section className="artifact-audio">
          <p className="lbl">Raw narration</p>
          <audio controls preload="none" src={artifactUrl(projectId, takeId, "raw_voice")} />
        </section>
      )}
      {has("optimized_voice") && (
        <section className="artifact-audio">
          <p className="lbl">Optimized narration — this is what the render uses</p>
          <audio controls preload="none" src={artifactUrl(projectId, takeId, "optimized_voice")} />
        </section>
      )}
```

with:

```tsx
      {has("raw_voice") && (
        <section className="artifact-audio">
          <p className="lbl">Raw narration</p>
          <VoicePlayer src={artifactUrl(projectId, takeId, "raw_voice")} label="raw narration" />
        </section>
      )}
      {has("optimized_voice") && (
        <section className="artifact-audio">
          <p className="lbl">Optimized narration — this is what the render uses</p>
          <VoicePlayer src={artifactUrl(projectId, takeId, "optimized_voice")} label="optimized narration" />
        </section>
      )}
```

### Step 4.6 — wire it into `PreviewPanel.tsx`

Add the import, then replace:

```tsx
        {url ? (
          <audio controls src={url} />
        ) : (
```

with:

```tsx
        {url ? (
          <VoicePlayer src={url} label="voice sample" />
        ) : (
```

### Step 4.7 — verify

```
cd web && npm run build && npm run lint
```

**Manual check:** Render's finished video plays with the new custom
controls (play/pause, drag the seek bar, time updates). `VoiceBody`'s two
narration tracks each get their own custom player, independently
playable, one muting/pausing the other correctly (two separate
`<audio>` elements, browser's own default behavior, nothing special
needed). Quick Panel's voice sample preview - same. Tab to a player's
play button, press Space - toggles play. Tab to the seek bar, press
Left/Right arrows - nudges position.

### Step 4.8 — commit

`feat: custom video/voice players replace native controls — Slice 27 Task 4`

---

## Task 5 — Script's partial state (#16, Script half)

**Files:** `web/src/artifacts/bodies.tsx`

### Step 5.1 — implement

Replace:

```typescript
export function ScriptBody({ projectId, takeId, files }: BodyProps) {
  const narration = useArtifactText(projectId, takeId, "narration", "narration" in files);
  const scenes = useArtifactJson<Scene[]>(projectId, takeId, "scenes", "scenes" in files);

  if (!("narration" in files) && !("scenes" in files)) return <Missing what="script" />;

  return (
```

with:

```typescript
export function ScriptBody({ projectId, takeId, files }: BodyProps) {
  const narration = useArtifactText(projectId, takeId, "narration", "narration" in files);
  const scenes = useArtifactJson<Scene[]>(projectId, takeId, "scenes", "scenes" in files);

  if (!("narration" in files) && !("scenes" in files)) return <Missing what="script" />;

  // Mirrors avg.pipeline.phases.writing.script_substate's own three
  // states (pending/partial/done), derived the same way it is server-side
  // - from which of these two files exist - rather than a new field: this
  // component already has `files`, and Pass 2 (segmentation) can genuinely
  // take a while, so "narration exists, scenes doesn't yet" deserves its
  // own message instead of silently showing an empty scenes section.
  const partial = ("narration" in files) && !("scenes" in files);

  return (
```

Add the partial-state message. Replace:

```tsx
  return (
    <div className="artifact-stack">
      {Array.isArray(scenes) && scenes.length > 0 && (
```

with:

```tsx
  return (
    <div className="artifact-stack">
      {partial && (
        <p className="lbl">
          <span className="dot pulse" style={{ background: "var(--accent)" }} aria-hidden="true" />
          Draft written — segmenting into scenes…
        </p>
      )}
      {Array.isArray(scenes) && scenes.length > 0 && (
```

(`.dot.pulse` and the inline accent-background style match
`Studio.tsx`'s own `status === "running"` message exactly - the same
"running" affordance, not a new one, for a sub-step that's genuinely
running even though the phase-level `status` may not have flipped to
`"running"` in exactly this window.)

### Step 5.2 — verify

`cd web && npm run build && npm run lint`. **Manual check:** during a
real run, open the Script phase in Studio while Pass 2 is still working
- the "segmenting into scenes" message shows instead of an empty panel.

### Step 5.3 — commit

`feat: Script phase preview shows its partial (segmenting) state — Slice 27 Task 5`

---

## Task 6 — Render's proper preview (#16, Render half)

**Files:** `web/src/Studio.tsx`, `web/src/api.ts`

### Step 6.1 — api.ts

Replace:

```typescript
export interface TakesResponse {
  takes: Take[];
  current_take_id: number | null;
  /** The whole project's size on disk, in bytes - server-computed (see
   * `routes_takes._folder_size_bytes`), never walked from the browser. */
  disk_bytes: number;
}
```

with:

```typescript
export interface TakesResponse {
  takes: Take[];
  current_take_id: number | null;
  /** The whole project's size on disk, in bytes - server-computed (see
   * `routes_takes._folder_size_bytes`), never walked from the browser. */
  disk_bytes: number;
  /** Per take id (as a string key, matching every other JSON-object-keyed-
   * by-id shape this API already returns): where Script and Render each
   * got to, derived server-side off the take's own folder - see
   * `avg.pipeline.phases.writing.script_substate` and
   * `avg.pipeline.phases.render.render_substate`. */
  substates: Record<string, { script: "pending" | "partial" | "done"; render: "none" | "base" | "done" }>;
}
```

### Step 6.2 — Studio.tsx: thread `substates` through

Replace:

```typescript
  function refreshTakes() {
    return getTakes(project.id).then((res) => {
      setTakes(res.takes);
      setCurrentTakeId(res.current_take_id);
      setDiskBytes(res.disk_bytes);
      setTakesError(null);
      return res;
    });
  }
```

with:

```typescript
  function refreshTakes() {
    return getTakes(project.id).then((res) => {
      setTakes(res.takes);
      setCurrentTakeId(res.current_take_id);
      setDiskBytes(res.disk_bytes);
      setSubstates(res.substates);
      setTakesError(null);
      return res;
    });
  }
```

Add the state, near the other take-related `useState` calls. Replace:

```typescript
  const [takesError, setTakesError] = useState<string | null>(null);
```

with:

```typescript
  const [takesError, setTakesError] = useState<string | null>(null);
  const [substates, setSubstates] = useState<TakesResponse["substates"]>({});
```

(Import `TakesResponse` as a type alongside whatever's already imported
from `./api` at the top of this file.)

### Step 6.3 — the mirrored percent threshold and render-branch messaging

Add the mirrored constant near `formatDuration` at the top of the file.
Replace:

```typescript
function formatDuration(ms?: number | null): string {
```

with:

```typescript
// Mirrors avg.pipeline.phases.render._BASE_PERCENT_SPAN - keep in sync by
// hand, the same convention phases.ts already uses for the backend phase
// registry. The base render pass is scaled into 0-70% of overall
// progress specifically so the composite pass's own 0-100% doesn't read
// as the render restarting; this is what lets the frontend say which of
// the two it's currently in without a new event type.
const RENDER_BASE_PERCENT_SPAN = 70;

function formatDuration(ms?: number | null): string {
```

Update `ArtifactDetail`'s render-progress branch to name the active
sub-step. Replace:

```tsx
  if (phaseKey === "render" && progress) {
    return (
      <div className="artifact-progress">
        <div style={{ display: "flex", alignItems: "baseline", gap: 16 }}>
          <span className="artifact-progress__pct">{progress.percent}%</span>
          <span style={{ fontSize: 15, color: "var(--muted)" }}>{progress.message}</span>
        </div>
```

with:

```tsx
  if (phaseKey === "render" && progress) {
    const step = progress.percent < RENDER_BASE_PERCENT_SPAN ? "Step 1 of 2 — base video" : "Step 2 of 2 — overlays & captions";
    return (
      <div className="artifact-progress">
        <p className="lbl" style={{ marginBottom: 4 }}>{step}</p>
        <div style={{ display: "flex", alignItems: "baseline", gap: 16 }}>
          <span className="artifact-progress__pct">{progress.percent}%</span>
          <span style={{ fontSize: 15, color: "var(--muted)" }}>{progress.message}</span>
        </div>
```

Give the pending state (nothing rendered yet) an outline of what's
coming, and the finished-but-base-only state its own honest message.
`status === "pending"` currently short-circuits before `phaseKey ===
"render"` is ever considered specially - reorder so Render's pending
case gets its own message. Replace:

```tsx
  // Every non-render phase shows its own real output. A phase that has
  // not run yet still says so - there is nothing on disk to show - but
  // "Done." on its own, which is what six of the eight used to render, is
  // gone.
  if (status === "pending") return <p className="artifact-empty">Not started yet.</p>;
```

with:

```tsx
  // Every non-render phase shows its own real output. A phase that has
  // not run yet still says so - there is nothing on disk to show - but
  // "Done." on its own, which is what six of the eight used to render, is
  // gone.
  if (status === "pending" && phaseKey === "render") {
    return (
      <p className="artifact-empty">
        Two steps when this runs: the base video first, then overlays and captions.
      </p>
    );
  }
  if (status === "pending") return <p className="artifact-empty">Not started yet.</p>;
```

Finally, the base-only-finished case - `render_substate === "base"` means
`master_output.mp4` exists but `render/_composite.json` doesn't, so the
video plays fine but has no overlays/captions baked in, which nothing
about the file itself can say. Add a `renderSubstate` prop and use it in
the two `phaseKey === "render" && status === "done"` branches. Replace
the props destructuring:

```typescript
function ArtifactDetail({
  phaseKey,
  record,
  progress,
  failure,
  finished,
  project,
  take,
  isCurrentTake,
  onCancel,
  takeId,
  duration,
  cost,
}: {
  phaseKey: string;
  record: PhaseRecord | undefined;
  progress: ProgressEvent | null;
  failure: PhaseFailedEvent | null;
  finished: RenderFinishedEvent | null;
  project: ProjectDoc;
```

with:

```typescript
function ArtifactDetail({
  phaseKey,
  record,
  progress,
  failure,
  finished,
  project,
  take,
  isCurrentTake,
  onCancel,
  takeId,
  duration,
  cost,
  renderSubstate,
}: {
  phaseKey: string;
  record: PhaseRecord | undefined;
  progress: ProgressEvent | null;
  failure: PhaseFailedEvent | null;
  finished: RenderFinishedEvent | null;
  project: ProjectDoc;
  /** From the takes response's new `substates` map (Slice 27 Task 2) -
   * `undefined` only in the brief window before the first `GET .../takes`
   * response lands, same as `take` itself. */
  renderSubstate: "none" | "base" | "done" | undefined;
```

Replace the finished-render summary block (both the not-current-take and
the normal cases) to note a base-only result. Replace:

```tsx
  if (phaseKey === "render" && status === "done") {
    const output = finished?.output ?? project.master_output;
    const size = finished ? formatBytes(finished.size_bytes) : null;
    return (
      <div className="artifact-done">
        <Player src={videoUrl(project.id)} />
        <div className="artifact-summary">
          <div className="artifact-summary__head">
            <span className="artifact-summary__icon">
              <IconCheck />
            </span>
            Render finished
          </div>
          <dl className="artifact-summary__grid">
```

with:

```tsx
  if (phaseKey === "render" && status === "done") {
    const output = finished?.output ?? project.master_output;
    const size = finished ? formatBytes(finished.size_bytes) : null;
    return (
      <div className="artifact-done">
        <Player src={videoUrl(project.id)} />
        <div className="artifact-summary">
          <div className="artifact-summary__head">
            <span className="artifact-summary__icon">
              <IconCheck />
            </span>
            Render finished
          </div>
          {renderSubstate === "base" && (
            <p className="artifact-empty" style={{ marginTop: -4, marginBottom: 8 }}>
              Overlays and captions weren't baked into this video — only the base render completed.
            </p>
          )}
          <dl className="artifact-summary__grid">
```

### Step 6.4 — pass the prop at the call site

Replace:

```tsx
            <ArtifactDetail
              phaseKey={selected}
              record={record}
              progress={selected === "render" && isCurrentTake ? progress : null}
              failure={selected === "render" ? failure : null}
              finished={selected === "render" && isCurrentTake ? finished : null}
              project={project}
              take={selectedTake}
              isCurrentTake={isCurrentTake}
              onCancel={onCancel}
              takeId={selectedTakeId}
              duration={formatDuration(record?.duration_ms)}
```

with:

```tsx
            <ArtifactDetail
              phaseKey={selected}
              record={record}
              progress={selected === "render" && isCurrentTake ? progress : null}
              failure={selected === "render" ? failure : null}
              finished={selected === "render" && isCurrentTake ? finished : null}
              project={project}
              take={selectedTake}
              isCurrentTake={isCurrentTake}
              onCancel={onCancel}
              takeId={selectedTakeId}
              renderSubstate={selectedTakeId !== null ? substates[String(selectedTakeId)]?.render : undefined}
              duration={formatDuration(record?.duration_ms)}
```

### Step 6.5 — verify

```
cd web && npm run build && npm run lint
```

**Manual check:** open Render before it's ever run - the two-step outline
shows. Start a render, watch it cross from base into compositing - the
"Step 1 of 2"/"Step 2 of 2" label switches at the same point the overall
percent crosses 70%. If a take's composite pass was skipped (or hasn't
run) but the base video exists, the finished summary says so rather than
looking identical to a fully composited render.

### Step 6.6 — commit

`feat: Render phase gets a real preview — base/composite substate — Slice 27 Task 6`

---

## Task 7 — Full regression, update progress ledger

### Step 7.1

```
.venv-build\Scripts\python.exe -m pytest tests -q
.venv-build\Scripts\python.exe -m mypy avg
cd web && npm run build && npm run lint
```

### Step 7.2

Update `.superpowers/sdd/progress.md`: Slice 27 complete, all four items
noted - the shared player architecture, the per-take substates addition,
and that `VoicePreview`'s native controls (found during the players audit
but originally scoped to a different slice) were fixed here rather than
left as a fourth native-controls spot.

### Step 7.3 — commit

`docs: Slice 27 complete — Studio screen (nav, players, previews)`

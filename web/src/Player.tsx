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

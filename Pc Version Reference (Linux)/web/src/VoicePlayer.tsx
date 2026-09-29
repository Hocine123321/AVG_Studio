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

import { useEffect, useState } from "react";

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

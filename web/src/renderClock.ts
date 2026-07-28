import { useEffect, useState } from "react";
import type { ProgressEvent } from "./useEvents";

/**
 * Elapsed time and ETA for a running job, kept OUTSIDE React's tree.
 *
 * The bug this exists to fix: the clock used to be a `useRef` inside
 * `Home.tsx`, so its start time was born when that component mounted and
 * died when it unmounted. Navigating to Studio and back - or to Settings and
 * back - restarted the ref from zero, and a render that was eight minutes in
 * would proudly report `00:03` elapsed with a nonsense ETA to match.
 *
 * The start time belongs to the RENDER, not to whichever screen happens to
 * be showing it, so it lives in this module-level map. Module state survives
 * every unmount and every route change in a single-page app; only a reload
 * clears it, and a reload is also when the render's own liveness has to be
 * re-established anyway.
 *
 * Keyed by project id so two projects rendered in one session never inherit
 * each other's start time. (The app runs one job at a time - see the 409 in
 * `avg/api/app.py` - but the map costs nothing and removes the question.)
 *
 * The backend's `ProgressEvent` carries no timestamps, so elapsed is measured
 * from the first event this module saw and remaining is a straight-line
 * projection from the percentage. Both are honest about being estimates: a
 * percentage that stalls makes the ETA grow, which is correct.
 */

const startedAt = new Map<string, number>();

/** Record (once) when work on `projectId` was first seen. */
export function markStarted(projectId: string): void {
  if (!startedAt.has(projectId)) startedAt.set(projectId, Date.now());
}

/** Forget a project's clock. Called on any terminal event - finished,
 * failed, cancelled - so the next run measures from its own start rather
 * than inheriting the previous one's. */
export function clearRenderClock(projectId: string): void {
  startedAt.delete(projectId);
}

export interface RenderClock {
  elapsedMs: number;
  remainingMs: number | null;
}

/**
 * Live elapsed/remaining for `progress`, ticking once a second.
 *
 * Safe to call from more than one component at once: they all read the same
 * module-level start time, so the header and the hero agree rather than each
 * counting from its own mount.
 */
export function useRenderClock(progress: ProgressEvent | null): RenderClock {
  const [, tick] = useState(0);

  useEffect(() => {
    if (!progress) return;
    markStarted(progress.project_id);
  }, [progress]);

  useEffect(() => {
    if (!progress) return;
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [progress?.project_id, progress]);

  if (!progress) return { elapsedMs: 0, remainingMs: null };
  const start = startedAt.get(progress.project_id);
  if (start === undefined) return { elapsedMs: 0, remainingMs: null };

  const elapsedMs = Date.now() - start;
  if (progress.percent <= 0) return { elapsedMs, remainingMs: null };
  const total = (elapsedMs / progress.percent) * 100;
  return { elapsedMs, remainingMs: Math.max(total - elapsedMs, 0) };
}

import { useEffect, useState } from "react";

/**
 * What the backend is being asked to do right now, for the global status bar.
 *
 * Every call through `api.ts`'s one `request()` wrapper registers here for
 * its lifetime, so nothing has to be instrumented per-screen and no call can
 * be silently missing from the bar. Non-HTTP work (the startup library scan,
 * an App Health pass) registers by calling `beginActivity` directly.
 *
 * Module-level, deliberately, for the same reason `renderClock.ts` is: the
 * bar has to survive route changes, and an in-flight request does not care
 * which screen was mounted when it started.
 *
 * The whole point is that a slow call LOOKS slow instead of looking like a
 * frozen app - hence the per-activity elapsed clock the bar renders as
 * "connecting 00:07".
 */

export interface Activity {
  id: number;
  /** What the user should understand is happening: "Loading", "Checking",
   * "Saving", "Connecting". Sentence case, no ellipsis - the bar adds it. */
  label: string;
  startedAt: number;
}

type Listener = (activities: Activity[]) => void;

let nextId = 1;
const active = new Map<number, Activity>();
const listeners = new Set<Listener>();

function emit(): void {
  const snapshot = [...active.values()];
  for (const listener of listeners) listener(snapshot);
}

/** Register work in progress. Returns the function that ends it - always
 * call it, in a `finally`, or the bar will claim the work never finished. */
export function beginActivity(label: string): () => void {
  const id = nextId++;
  active.set(id, { id, label, startedAt: Date.now() });
  emit();
  let done = false;
  return () => {
    if (done) return;
    done = true;
    active.delete(id);
    emit();
  };
}

/** A verb for a request, derived from method and path.
 *
 * Deliberately coarse: the bar is ambient, and a user reading "Checking"
 * while the health page loads learns more than they would from a URL. Only
 * the handful of paths with a genuinely different meaning get their own
 * word. */
export function labelForRequest(method: string, path: string): string {
  if (path.startsWith("/api/health")) return "Checking";
  if (path.startsWith("/api/keys")) return "Saving key";
  if (method !== "GET") return "Saving";
  if (path.startsWith("/api/projects") && !path.includes("/")) return "Loading library";
  return "Loading";
}

export function useActivities(): Activity[] {
  const [activities, setActivities] = useState<Activity[]>(() => [...active.values()]);

  useEffect(() => {
    listeners.add(setActivities);
    // Re-read on mount: an activity may have started before this component
    // existed, which is exactly the case the bar is for.
    setActivities([...active.values()]);
    return () => {
      listeners.delete(setActivities);
    };
  }, []);

  return activities;
}

/** mm:ss since `startedAt`, the format the status bar shows next to a label. */
export function elapsedClock(startedAt: number, now: number): string {
  const total = Math.max(0, Math.round((now - startedAt) / 1000));
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

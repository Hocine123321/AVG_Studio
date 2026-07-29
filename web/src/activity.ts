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

/**
 * How much the bar should care about one activity.
 *
 * `high` is a running job - a render or a pipeline. It is registered from
 * the event socket, never from an HTTP call, because the call that STARTS
 * a job returns 202 in milliseconds and its activity ends before anyone
 * can read it. That is why the pill said "Ready!" through an entire
 * render.
 *
 * The bar names the highest priority present, oldest first within it, and
 * a lower priority can never replace a higher one. That is what removes
 * the flicker: two concurrent reads used to swap the label as each
 * resolved.
 */
export type Priority = "high" | "medium" | "low";

export interface Activity {
  id: number;
  /** What the user should understand is happening: "Loading", "Checking",
   * "Saving", "Connecting". Sentence case, no ellipsis - the bar adds it. */
  label: string;
  startedAt: number;
  priority: Priority;
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
export function beginActivity(label: string, priority: Priority = "low"): () => void {
  const id = nextId++;
  active.set(id, { id, label, startedAt: Date.now(), priority });
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

const RANK: Record<Priority, number> = { high: 0, medium: 1, low: 2 };

/** The activity the bar should name: highest priority, oldest within it. */
export function leadActivity(activities: Activity[]): Activity | null {
  return activities.reduce<Activity | null>((lead, a) => {
    if (lead === null) return a;
    if (RANK[a.priority] !== RANK[lead.priority]) {
      return RANK[a.priority] < RANK[lead.priority] ? a : lead;
    }
    return a.startedAt < lead.startedAt ? a : lead;
  }, null);
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

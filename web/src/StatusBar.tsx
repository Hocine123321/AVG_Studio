import { useEffect, useState } from "react";
import { elapsedClock, leadActivity, useActivities } from "./activity";

/**
 * The floating status pill: what the backend is doing, top-right of the
 * content area.
 *
 * Every HTTP call registers itself in `activity.ts` (from the one wrapper in
 * `api.ts`, so nothing can be missing), and this names whatever is in flight
 * with a live mm:ss clock. A slow call therefore LOOKS slow, which is the
 * entire point - a UI that sits perfectly still is indistinguishable from a
 * hung one.
 *
 * Shape and placement come from the 3rd redesign (`docs/reference/
 * 3rd-redesign`): a pill that floats over the content rather than a bar
 * pinned to the bottom, its border and tint taking the state's colour.
 * The redesign's pill carries no timer; the clock is kept here deliberately,
 * because "this call is 40 seconds deep" is the thing that made a stall
 * legible in the first place.
 *
 * Idle is not blank - it says "Ready!", so the pill is something the user
 * learns to trust rather than a thing that only appears when it feels like
 * it. A dropped events socket outranks any request: while it is down, the
 * app is not being told about the very work this pill exists to report.
 */
export default function StatusBar({ connected }: { connected: boolean }) {
  const activities = useActivities();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (activities.length === 0) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [activities.length]);

  // Highest priority first, oldest within it. A running job outranks every
  // request, so the pill can no longer read "Ready!" through a render -
  // and a background read can no longer replace the label of something
  // more important that is still in flight, which is what made it flicker.
  const lead = leadActivity(activities);

  const kind = !connected ? "error" : lead !== null ? "busy" : "ready";
  const label = kind === "error" ? "Reconnecting" : lead !== null ? lead.label : "Ready!";

  return (
    <div className={`statuspill statuspill--${kind}`} role="status" aria-live="polite">
      <span className="statuspill__dot" aria-hidden="true" />
      <span className="statuspill__label">
        {label}
        {lead !== null && "…"}
      </span>
      {lead !== null && <span className="statuspill__clock mono">{elapsedClock(lead.startedAt, now)}</span>}
      {activities.length > 1 && <span className="statuspill__more">+{activities.length - 1}</span>}
    </div>
  );
}

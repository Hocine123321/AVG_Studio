import { useEffect, useState } from "react";
import { elapsedClock, useActivities } from "./activity";

/**
 * The always-present line saying what the backend is doing.
 *
 * Every HTTP call registers itself in `activity.ts` (from the one wrapper in
 * `api.ts`, so nothing can be missing), and this renders whatever is in
 * flight with a running clock: "Loading library 00:03". A call that is slow
 * therefore LOOKS slow, which is the entire point - the previous behaviour
 * was a UI that sat perfectly still and was indistinguishable from a hang.
 *
 * Idle is not blank: it says "Ready", so the bar is a thing the user learns
 * to trust rather than something that only appears when it feels like it.
 *
 * `connected` comes from the events socket. A dropped socket outranks any
 * request, because while it is down the app is not being told about the very
 * work this bar exists to report.
 */
export default function StatusBar({ connected }: { connected: boolean }) {
  const activities = useActivities();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (activities.length === 0) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [activities.length]);

  // The oldest wins: with several calls in flight, the one that has been
  // waiting longest is the one worth naming.
  const oldest = activities.reduce<(typeof activities)[number] | null>(
    (slowest, a) => (slowest === null || a.startedAt < slowest.startedAt ? a : slowest),
    null,
  );

  const busy = oldest !== null;
  const label = !connected ? "Reconnecting" : busy ? oldest.label : "Ready";
  const clock = busy ? elapsedClock(oldest.startedAt, now) : null;

  return (
    <div
      className={`statusbar${busy || !connected ? " statusbar--busy" : ""}`}
      role="status"
      aria-live="polite"
    >
      <span className={`statusbar__dot${busy || !connected ? " pulse" : ""}`} aria-hidden="true" />
      <span className="statusbar__label">
        {label}
        {busy && "…"}
      </span>
      {clock && <span className="statusbar__clock mono">{clock}</span>}
      {activities.length > 1 && (
        <span className="statusbar__more">+{activities.length - 1} more</span>
      )}
    </div>
  );
}

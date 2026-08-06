import { useEffect, useState } from "react";
import { elapsedClock, leadActivity, useActivities } from "./activity";
import { IconCheck } from "./Icons";

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
 * Idle is not blank - it shows a drawn checkmark, so the pill is something
 * the user learns to trust rather than a thing that only appears when it
 * feels like it. A dropped events socket outranks any request: while it is
 * down, the app is not being told about the very work this pill exists to
 * report.
 */

/** How long an activity must be in flight before the pill shows a ring.
 * Below this the work is over before a spinner would mean anything, and
 * showing one makes the pill cry wolf. */
const RING_DELAY_MS = 150;

export default function StatusBar({ connected, loading = false }: { connected: boolean; loading?: boolean }) {
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

  // The ring is gated on TIME, not on priority. Slice 10 gated it on
  // `priority === "high"` so a 40ms GET would not flash a spinner - but that
  // reasoning was about flicker, and the fix for flicker is a delay. A slow
  // screen change deserves the ring exactly as much as a render does; a fast
  // one still shows nothing.
  const [settled, setSettled] = useState(false);
  useEffect(() => {
    if (lead === null) {
      setSettled(false);
      return;
    }
    const id = window.setTimeout(() => setSettled(true), RING_DELAY_MS);
    return () => window.clearTimeout(id);
  }, [lead?.id]);

  const kind = !connected ? "error" : lead !== null ? "busy" : "ready";
  const working = lead !== null && settled;
  const label = kind === "error" ? "Reconnecting" : lead !== null ? lead.label : "";

  // Until the library has ever answered, the pill has nothing true to say -
  // the server is up, so it would read "ready" while the screen is still
  // empty. A bar in the pill's own slot says "still counting" instead.
  // `avg/shell.py`'s splash covers the earlier moment, before the server
  // exists at all; this is the one after it.
  if (loading) {
    return (
      <div
        className="statuspill statuspill--loading"
        role="status"
        aria-live="polite"
        aria-label="Loading your library"
      >
        <span className="statusbar-load" aria-hidden="true">
          <span className="statusbar-load__fill" />
        </span>
      </div>
    );
  }

  return (
    <div
      className={`statuspill statuspill--${kind}`}
      role="status"
      aria-live="polite"
      // Idle drops the visible word, and the mark itself is `aria-hidden`,
      // so without this the pill would announce nothing at all to a screen
      // reader at rest. The visible text carries it in every other state.
      aria-label={kind === "ready" && !working ? "Ready" : undefined}
    >
      {working ? (
        <span className="statuspill__ring" aria-hidden="true" />
      ) : kind === "ready" ? (
        // A drawn mark, never the ✅ character: an emoji is rendered by the
        // OS in its own font and colours and is incapable of obeying the
        // theme - the same objection that bans the native <select>.
        <span className="statuspill__check" aria-hidden="true">
          <IconCheck size={12} />
        </span>
      ) : (
        <span className="statuspill__dot" aria-hidden="true" />
      )}
      {label !== "" && (
        // Keyed so the label replays its entry each time the pill changes
        // between resting and working, rather than swapping text in place.
        <span className="statuspill__label" key={working ? "working" : "resting"}>
          {label}
          {lead !== null && "…"}
        </span>
      )}
      {lead !== null && <span className="statuspill__clock mono">{elapsedClock(lead.startedAt, now)}</span>}
      {activities.length > 1 && <span className="statuspill__more">+{activities.length - 1}</span>}
    </div>
  );
}

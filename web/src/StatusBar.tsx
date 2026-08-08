import { useEffect, useRef, useState } from "react";
import { elapsedClock, leadActivity, useActivities } from "./activity";
import { IconAlert, IconCheck, IconChevron, IconX } from "./Icons";
import type { ActivityEvent } from "./useEvents";

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
 *
 * Slice 17 adds a second feed on top of all of the above: a chevron-toggled
 * detail panel showing every currently running `ActivityEvent` (the
 * WS-scoped "what long operations exist" feed, keyed by `activity_id`),
 * plus a sticky acknowledgement queue for outcomes the user must not miss
 * (a failed render, a completed video, a provider-key failure).
 * `kind`/`working` now also go busy when a WS-tracked operation is running
 * with no HTTP call in flight - see `working`'s own comment below for why
 * that fold was necessary, not merely convenient. The sticky queue is a
 * separate concern from `kind`: a non-empty queue keeps the pill in its
 * expanded (undocked) state regardless of `kind`, so a finished-but-
 * unacknowledged outcome does not collapse back to idle. See this slice's
 * plan for the fuller reasoning.
 */

/** How long an activity must be in flight before the pill shows a ring.
 * Below this the work is over before a spinner would mean anything, and
 * showing one makes the pill cry wolf. */
const RING_DELAY_MS = 150;

export default function StatusBar({
  connected,
  loading = false,
  activity = {},
  stickyQueue = [],
  onAcknowledge,
}: {
  connected: boolean;
  loading?: boolean;
  /** Slice 17: `events.activity` - every currently running operation. */
  activity?: Record<string, ActivityEvent>;
  /** Slice 17: `events.stickyQueue` - unacknowledged outcomes, oldest first. */
  stickyQueue?: ActivityEvent[];
  /** Slice 17: `events.acknowledgeSticky` - dismisses one queued event by id. */
  onAcknowledge?: (activityId: string) => void;
}) {
  const activities = useActivities();
  const [now, setNow] = useState(() => Date.now());
  const [detailOpen, setDetailOpen] = useState(false);

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
    // A new lead - even one that starts in the same tick a previous one
    // finishes - must repeat the quiet period rather than inherit
    // `settled === true` from whatever just ended. Without this reset, a
    // fast request beginning back-to-back with a slow one skipped the gate
    // entirely and rang immediately.
    setSettled(false);
    const id = window.setTimeout(() => setSettled(true), RING_DELAY_MS);
    return () => window.clearTimeout(id);
    // `lead` is a fresh object every render; only its identity (`lead.id`)
    // should restart this timer, so it is deliberately left out of the
    // dependency array rather than satisfied by re-running on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lead?.id]);

  // Everything the pill shows about an activity - not just the ring - waits
  // for the same quiet period. Gating the ring alone (as this first read)
  // still let a 40ms request swap the resting check for a dot, pop a label
  // and a 00:00 clock into existence, and collapse again a frame later:
  // exactly the "label that snaps between words" this slice exists to
  // remove, just relocated from the label to the whole pill. `activeLead`
  // is `lead` once it has been in flight for `RING_DELAY_MS`, `null` before
  // that, so nothing downstream needs its own timing logic.
  const activeLead = settled ? lead : null;
  // Slice 17: `kind`/`working` used to answer only "is an HTTP call in
  // flight" (the pre-existing `activity.ts` axis). A running WS-tracked
  // operation with no HTTP call in flight - the exact case this slice
  // exists for, "a long AI phase between requests" - must also count as
  // busy, or the pill sits there green-and-idle while a render is
  // actually going. `activityRows` (Slice 17's own `activity` map) is
  // folded into `working` for that reason; `activeLead`'s label still wins
  // when both are present, since an HTTP call in flight is usually the
  // more immediate thing happening.
  const activityRows = Object.values(activity);
  const wsLead = activityRows[0] ?? null;
  const working = activeLead !== null || wsLead !== null;
  const kind = !connected ? "error" : working ? "busy" : "ready";
  const label =
    kind === "error" ? "Reconnecting" : activeLead !== null ? activeLead.label : wsLead !== null ? wsLead.label : "";
  // Slice 17: the sticky queue and the running-activity map are an
  // independent axis from `kind` above (see this component's docstring).
  // A non-empty queue means there is something the user has not yet
  // acknowledged, so the pill stays visually "there" even if `kind` alone
  // would say idle - closing the chevron never discards the queue, only
  // the `x` on each item does.
  const hasSticky = stickyQueue.length > 0;
  const stickyHead = stickyQueue[0] ?? null;
  const detailAvailable = activityRows.length > 0 || hasSticky;
  // Auto-open the panel the moment there is something sticky to see, so a
  // failure or a completion cannot land silently behind a closed chevron -
  // the whole point of the sticky queue is that it is impossible to miss.
  const wasStickyRef = useRef(false);
  useEffect(() => {
    if (hasSticky && !wasStickyRef.current) setDetailOpen(true);
    wasStickyRef.current = hasSticky;
  }, [hasSticky]);
  const panelOpen = detailOpen && detailAvailable;

  // Spec §4's three pill states. "Docked (idle)" collapses to a thin,
  // glyph-free sliver flush with the right edge - genuinely idle AND
  // nothing queued AND the panel isn't open. "Active" is everything else:
  // busy, an error, or - the one place these two independent axes meet -
  // idle but with something sticky still unacknowledged, which keeps the
  // pill in its Active shape rather than collapsing (spec §4:
  // "Finishing... unless a sticky state is waiting, in which case it
  // stays out"). `panelOpen` also keeps it undocked: a sliver the user
  // just clicked open cannot re-collapse out from under the click.
  const docked = kind === "ready" && !working && !hasSticky && !panelOpen;

  // Until the library has ever answered, the pill has nothing true to say -
  // the server is up, so it would read "ready" while the screen is still
  // empty. A bar in the pill's own slot says "still counting" instead.
  // `avg/shell.py`'s splash covers the earlier moment, before the server
  // exists at all; this is the one after it.
  //
  // This wait obeys the same quiet period as an activity's ring: Task 2's
  // whole argument is that an indicator in flight under `RING_DELAY_MS` is
  // noise, and the first version of this bar broke that rule against
  // itself, appearing and disappearing within 24ms on the machine it was
  // measured on. Reusing the constant here, rather than declaring a second
  // one, keeps the two indicators speaking the same vocabulary.
  const [loadingSettled, setLoadingSettled] = useState(false);
  useEffect(() => {
    if (!loading) {
      setLoadingSettled(false);
      return;
    }
    const id = window.setTimeout(() => setLoadingSettled(true), RING_DELAY_MS);
    return () => window.clearTimeout(id);
  }, [loading]);

  if (loading) {
    // Still within the quiet period: render nothing rather than a bar that
    // would only flash and vanish.
    if (!loadingSettled) return null;
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

  // The part that grows and shrinks: the label, the clock, and the "+N"
  // overflow count. Computed once so the JSX below and the width-animating
  // wrapper agree about when there is anything in it to show.
  const expanded = label !== "" || activeLead !== null;

  return (
    <div
      className={`statuspill statuspill--${kind}${docked ? " statuspill--docked" : ""}`}
      role="status"
      aria-live="polite"
      // Idle drops the visible word, and the mark itself is `aria-hidden`,
      // so without this the pill would announce nothing at all to a screen
      // reader at rest. The visible text carries it in every other state.
      // Docked has neither glyph nor label (spec §4), so it gets its own
      // wording rather than the bare "Ready".
      aria-label={docked ? "Ready" : kind === "ready" && !working ? "Ready" : undefined}
    >
      {/* Check, dot and ring are all mounted together, stacked in one slot,
          and only opacity/transform say which one is showing. Conditionally
          mounting just one of them (the previous approach) has no way to
          animate the outgoing element - it is simply gone on the frame the
          state changes, which is the "pop" this slice exists to remove.
          Docked hides this whole slot via CSS rather than unmounting it,
          same reasoning: the slide from docked to active is a transform on
          already-mounted content, not a mount. */}
      <span className="statuspill__icon" aria-hidden="true">
        <span className={`statuspill__check${!working && kind === "ready" && !hasSticky ? " is-active" : ""}`}>
          {/* No explicit `size`: the component's default (13) is also the
              box's own size and the ring's optical weight, so there is one
              number instead of two that happen to agree. */}
          <IconCheck />
        </span>
        <span className={`statuspill__dot${!working && kind !== "ready" && !hasSticky ? " is-active" : ""}`} />
        <span className={`statuspill__ring${working ? " is-active" : ""}`} />
      </span>
      <span className={`statuspill__expand${expanded ? " is-expanded" : ""}`}>
        {label !== "" && (
          // Keyed so the label replays its entry each time the pill changes
          // between resting and working, rather than swapping text in place.
          <span className="statuspill__label" key={working ? "working" : "resting"}>
            {label}
            {activeLead !== null && "…"}
          </span>
        )}
        {activeLead !== null && (
          <span className="statuspill__clock mono">{elapsedClock(activeLead.startedAt, now)}</span>
        )}
        {activeLead !== null && activities.length > 1 && (
          <span className="statuspill__more">+{activities.length - 1}</span>
        )}
      </span>

      {/* The obvious click target spec §4 asks for: covers the WHOLE pill
          (including the docked sliver, which has no other visible
          content) rather than sitting beside it, so docked - despite
          having no glyph or label - is still "wide enough to be an
          obvious click target". Rendered whenever there is something to
          reveal; a truly idle pill with nothing running and nothing
          queued has nothing for it to open, so it is absent rather than a
          button that opens an empty panel every time. */}
      {detailAvailable && (
        <button
          type="button"
          className={`statuspill__chevron${panelOpen ? " is-open" : ""}${docked ? " statuspill__chevron--fill" : ""}`}
          aria-expanded={panelOpen}
          aria-label={panelOpen ? "Hide activity details" : "Show activity details"}
          onClick={() => setDetailOpen((v) => !v)}
        >
          <IconChevron />
        </button>
      )}

      {panelOpen && (
        <div className="statuspill-detail" role="group" aria-label="Activity details">
          {stickyHead && (
            <div className={`statuspill-detail__row statuspill-detail__row--sticky is-${stickyHead.state}`}>
              <span className="statuspill-detail__icon" aria-hidden="true">
                {stickyHead.state === "failed" ? <IconAlert /> : <IconCheck />}
              </span>
              <span className="statuspill-detail__text">
                <span className="statuspill-detail__label">{stickyHead.label}</span>
                {stickyHead.detail && <span className="statuspill-detail__meta">{stickyHead.detail}</span>}
              </span>
              {stickyQueue.length > 1 && (
                <span className="statuspill-detail__count">1 of {stickyQueue.length}</span>
              )}
              <button
                type="button"
                className="statuspill-detail__ack"
                aria-label="Dismiss"
                onClick={() => onAcknowledge?.(stickyHead.activity_id)}
              >
                <IconX />
              </button>
            </div>
          )}
          {activityRows.map((row) => (
            <div key={row.activity_id} className="statuspill-detail__row">
              <span className="statuspill-detail__text">
                <span className="statuspill-detail__label">{row.label}</span>
                {row.detail && <span className="statuspill-detail__meta">{row.detail}</span>}
              </span>
              {row.percent !== null && <span className="statuspill-detail__percent">{row.percent}%</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

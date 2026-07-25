import { useEffect, useState } from "react";
import { wsUrl } from "./api";

/**
 * Mirrors `avg.core.events` on the Python side (see `avg/core/events.py`).
 * `kind` is left as `string` rather than a literal union of
 * `avg.core.errors.ErrorKind` values on purpose - the frontend only ever
 * displays it, so a new kind added on the backend must never require a
 * matching frontend change to keep compiling.
 */
export interface ProgressEvent {
  type: "progress";
  project_id: string;
  percent: number;
  message: string;
}

export interface PhaseFailedEvent {
  type: "phase_failed";
  project_id: string;
  phase: string;
  kind: string;
  message: string;
  detail: string;
}

export interface RenderCancelledEvent {
  type: "render_cancelled";
  project_id: string;
}

export interface RenderFinishedEvent {
  type: "render_finished";
  project_id: string;
  output: string;
  size_bytes: number;
}

export type EngineEvent = ProgressEvent | PhaseFailedEvent | RenderCancelledEvent | RenderFinishedEvent;

export interface EventsState {
  /** The most recently seen event of each kind - not a history, and not
   * keyed per-project. `avg.api.app` enforces one render at a time for the
   * whole process (a second `POST .../render` gets a 409), so "the last
   * progress event anyone saw" and "the currently running render" are
   * always the same thing; callers that care about a specific project
   * filter these by `project_id` themselves (see `App.tsx`). */
  progress: ProgressEvent | null;
  failure: PhaseFailedEvent | null;
  cancelled: RenderCancelledEvent | null;
  finished: RenderFinishedEvent | null;
  /** False while the socket is down and a reconnect is pending - surfaced
   * so a dropped connection is something the user can see (Transport),
   * never something that fails silently. */
  connected: boolean;
}

const INITIAL_STATE: EventsState = {
  progress: null,
  failure: null,
  cancelled: null,
  finished: null,
  connected: false,
};

/** Every event resets the other three fields: state always reflects the
 * single most recent thing that happened, terminal or not, rather than
 * accumulating stale flags from a previous render. */
function reduce(payload: { type: string } & Record<string, unknown>): EventsState {
  switch (payload.type) {
    case "progress":
      return { ...INITIAL_STATE, connected: true, progress: payload as unknown as ProgressEvent };
    case "phase_failed":
      return { ...INITIAL_STATE, connected: true, failure: payload as unknown as PhaseFailedEvent };
    case "render_cancelled":
      return { ...INITIAL_STATE, connected: true, cancelled: payload as unknown as RenderCancelledEvent };
    case "render_finished":
      return { ...INITIAL_STATE, connected: true, finished: payload as unknown as RenderFinishedEvent };
    default:
      // "hello" (the socket's own greeting on connect) and anything future
      // and unrecognised both land here - connectedness changes, nothing
      // else does.
      return { ...INITIAL_STATE, connected: true };
  }
}

const MAX_RETRY_DELAY_MS = 15000;

/**
 * Owns the one `/ws/events` WebSocket for the whole app and reduces its
 * messages into state. Called exactly once, at the top of `App`, so every
 * screen (and the always-visible Transport bar) shares a single socket
 * instead of each opening its own.
 *
 * Reconnects with capped exponential backoff on drop - a render that
 * outlives a flaky socket must not silently vanish from view - and reports
 * `connected: false` for the whole time it's down so a consumer can say so
 * honestly instead of just going quiet.
 */
export function useEvents(): EventsState {
  const [state, setState] = useState<EventsState>(INITIAL_STATE);

  useEffect(() => {
    let stopped = false;
    let socket: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    function connect() {
      if (stopped) return;
      socket = new WebSocket(wsUrl("/ws/events"));

      socket.onopen = () => {
        attempt = 0;
      };

      socket.onmessage = (ev) => {
        let payload: unknown;
        try {
          payload = JSON.parse(ev.data as string);
        } catch {
          return;
        }
        if (typeof payload !== "object" || payload === null || !("type" in payload)) return;
        setState(reduce(payload as { type: string } & Record<string, unknown>));
      };

      socket.onclose = () => {
        if (stopped) return;
        setState((s) => ({ ...s, connected: false }));
        attempt += 1;
        const delay = Math.min(1000 * 2 ** attempt, MAX_RETRY_DELAY_MS);
        retryTimer = setTimeout(connect, delay);
      };

      socket.onerror = () => {
        // onclose always follows onerror for a WebSocket - the retry is
        // scheduled there, this just stops the dying socket promptly.
        socket?.close();
      };
    }

    connect();

    return () => {
      stopped = true;
      if (retryTimer) clearTimeout(retryTimer);
      socket?.close();
    };
  }, []);

  return state;
}

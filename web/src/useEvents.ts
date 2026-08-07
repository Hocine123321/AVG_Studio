import { useEffect, useState } from "react";
import { wsUrl } from "./api";
import { clearRenderClock } from "./renderClock";

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

export interface PhaseStartedEvent {
  type: "phase_started";
  project_id: string;
  phase: string;
}

export interface PhaseFinishedEvent {
  type: "phase_finished";
  project_id: string;
  phase: string;
  duration_ms: number;
}

export interface PipelineCancelledEvent {
  type: "pipeline_cancelled";
  project_id: string;
  phase: string;
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

export interface JobStartedEvent {
  type: "job_started";
  project_id: string;
  kind: "render" | "pipeline";
}

export interface JobStepEvent {
  type: "job_step";
  project_id: string;
  step: string;
  detail: string;
}

export type EngineEvent =
  | ProgressEvent
  | PhaseFailedEvent
  | PhaseStartedEvent
  | PhaseFinishedEvent
  | PipelineCancelledEvent
  | RenderCancelledEvent
  | RenderFinishedEvent
  | JobStartedEvent
  | JobStepEvent;

/** One phase's live state, in the same shape `ProjectDoc.phases` uses. */
export interface LivePhase {
  status: "pending" | "running" | "done" | "failed";
  duration_ms?: number;
  error?: string | null;
}

export interface EventsState {
  /** The most recently seen event of each kind - not a history, and not
   * keyed per-project. `avg.api.app` enforces one render at a time for the
   * whole process (a second `POST .../render` gets a 409), so "the last
   * progress event anyone saw" and "the currently running render" are
   * always the same thing; callers that care about a specific project
   * filter these by `project_id` themselves (see `App.tsx`). */
  progress: ProgressEvent | null;
  failure: PhaseFailedEvent | null;
  phaseStarted: PhaseStartedEvent | null;
  phaseFinished: PhaseFinishedEvent | null;
  pipelineCancelled: PipelineCancelledEvent | null;
  cancelled: RenderCancelledEvent | null;
  finished: RenderFinishedEvent | null;
  /** The job that is running, from its own announcement rather than from
   * the HTTP call that started it - that call is over in milliseconds.
   * Cleared by any terminal event. */
  jobStarted: JobStartedEvent | null;
  /** Named work with no measurable percentage, e.g. preparing the render
   * workspace. Shown as the Transport bar's detail line. */
  jobStep: JobStepEvent | null;
  /** Every live job's latest progress, keyed by project id. Events have
   * always carried `project_id`; until now nothing kept more than one.
   * The single-job fields above are derived from this and stay for
   * consumers that genuinely only care about one project (Studio). */
  byProject: Record<string, ProgressEvent>;
  /** False while the socket is down and a reconnect is pending - surfaced
   * so a dropped connection is something the user can see (Transport),
   * never something that fails silently. */
  connected: boolean;
  /**
   * Every phase transition seen for each project this session, folded
   * forward. Keyed project id, then phase key.
   *
   * The single-event fields above are deliberately last-write-wins: they
   * answer "what just happened". They cannot answer "where has this run
   * got to", because every branch of `reduce` returns `{...INITIAL_STATE,
   * <one field>}` - a `phase_finished` is erased by the next
   * `phase_started`, so finished phases never accumulated anywhere on the
   * client and the rail was only ever right about the most recent
   * transition.
   */
  phases: Record<string, Record<string, LivePhase>>;
}

const INITIAL_STATE: EventsState = {
  progress: null,
  failure: null,
  phaseStarted: null,
  phaseFinished: null,
  pipelineCancelled: null,
  cancelled: null,
  finished: null,
  jobStarted: null,
  jobStep: null,
  byProject: {},
  connected: false,
  phases: {},
};

/**
 * Fold one event into the accumulated phase map.
 *
 * Returns the previous map unchanged for anything that says nothing about
 * a phase (the socket's own "hello", a future event type), so a render's
 * progress ticks do not allocate a new object thirty times a second.
 */
function foldPhases(
  previous: EventsState["phases"],
  payload: { type: string } & Record<string, unknown>,
): EventsState["phases"] {
  const id = typeof payload.project_id === "string" ? payload.project_id : null;
  const phase = typeof payload.phase === "string" ? payload.phase : null;
  if (id === null) return previous;

  const forProject = { ...(previous[id] ?? {}) };
  switch (payload.type) {
    case "phase_started":
      if (!phase) return previous;
      forProject[phase] = { status: "running" };
      break;
    case "phase_finished":
      if (!phase) return previous;
      forProject[phase] = {
        status: "done",
        duration_ms: typeof payload.duration_ms === "number" ? payload.duration_ms : undefined,
      };
      break;
    case "phase_failed":
      if (!phase) return previous;
      forProject[phase] = {
        status: "failed",
        error: typeof payload.message === "string" ? payload.message : null,
      };
      break;
    case "pipeline_cancelled":
      // Back to pending, never a "cancelled" status - the backend does not
      // invent one either (`_apply_terminal_event`), and a status the
      // server cannot produce would be a second vocabulary.
      if (!phase) return previous;
      forProject[phase] = { status: "pending" };
      break;
    case "render_finished":
      forProject.render = { status: "done" };
      break;
    case "render_cancelled":
      forProject.render = { status: "pending" };
      break;
    default:
      return previous;
  }
  return { ...previous, [id]: forProject };
}

/**
 * Fold one event into the accumulated per-project progress map.
 *
 * A `progress` event writes (or overwrites) that project's entry. A terminal
 * event - the job finished, failed or was cancelled - deletes it, because a
 * finished job has no "latest progress" a consumer should keep showing; the
 * finished/cancelled/failed single-event fields are where that outcome lives
 * instead. Anything else (a phase transition, the socket's own "hello")
 * leaves the map untouched, for the same reason `foldPhases` does: several
 * jobs may be ticking at once and only their own `progress` events should
 * cause an allocation.
 */
function foldByProject(
  previous: EventsState["byProject"],
  payload: { type: string } & Record<string, unknown>,
): EventsState["byProject"] {
  const id = typeof payload.project_id === "string" ? payload.project_id : null;
  if (id === null) return previous;

  if (payload.type === "progress") {
    return { ...previous, [id]: payload as unknown as ProgressEvent };
  }
  if (
    payload.type === "render_finished" ||
    payload.type === "render_cancelled" ||
    payload.type === "pipeline_cancelled" ||
    payload.type === "phase_failed"
  ) {
    if (!(id in previous)) return previous;
    const next = { ...previous };
    delete next[id];
    return next;
  }
  return previous;
}

/** Every event replaces the prior terminal/progress state so the UI reflects
 * the most recent event, rather than accumulating stale outcomes. A progress
 * event deliberately retains its preceding phase_started association because
 * the backend's progress payload itself has no phase key. */
function reduce(previous: EventsState, payload: { type: string } & Record<string, unknown>): EventsState {
  // The render clock lives outside React (see `renderClock.ts`, which is
  // what stops a route change from resetting it). Nothing unmounts to tell
  // it a job ended, so the terminal events do - otherwise the next run on
  // the same project would measure from the previous run's start.
  if (
    payload.type === "render_finished" ||
    payload.type === "render_cancelled" ||
    payload.type === "pipeline_cancelled" ||
    payload.type === "phase_failed"
  ) {
    const id = payload.project_id;
    if (typeof id === "string") clearRenderClock(id);
  }

  // Folded once, then carried through every branch: the map is the one
  // piece of state that must survive the next event rather than be
  // replaced by it.
  const phases = foldPhases(previous.phases, payload);

  // Carried forward until a terminal event, like the phase map: the job is
  // still running while phases start and finish under it, and the status
  // pill's "a job is running" state is exactly this field being set.
  const terminal =
    payload.type === "render_finished" ||
    payload.type === "render_cancelled" ||
    payload.type === "pipeline_cancelled" ||
    payload.type === "phase_failed";
  const jobStarted =
    payload.type === "job_started"
      ? (payload as unknown as JobStartedEvent)
      : terminal
        ? null
        : previous.jobStarted;
  const jobStep =
    payload.type === "job_step" ? (payload as unknown as JobStepEvent) : terminal ? null : previous.jobStep;
  const byProject = foldByProject(previous.byProject, payload);
  const carried = { phases, jobStarted, jobStep, byProject, connected: true };

  switch (payload.type) {
    case "phase_started":
      return { ...INITIAL_STATE, ...carried, phaseStarted: payload as unknown as PhaseStartedEvent };
    case "progress":
      // Pipeline progress has no phase field. The immediately preceding
      // phase_started event supplies that association; direct media renders
      // have none and continue to be treated as render progress by App.
      return {
        ...INITIAL_STATE,
        ...carried,
        progress: payload as unknown as ProgressEvent,
        phaseStarted: previous.phaseStarted,
      };
    case "phase_finished":
      return { ...INITIAL_STATE, ...carried, phaseFinished: payload as unknown as PhaseFinishedEvent };
    case "phase_failed":
      return { ...INITIAL_STATE, ...carried, failure: payload as unknown as PhaseFailedEvent };
    case "pipeline_cancelled":
      return {
        ...INITIAL_STATE,
        ...carried,
        pipelineCancelled: payload as unknown as PipelineCancelledEvent,
      };
    case "render_cancelled":
      return { ...INITIAL_STATE, ...carried, cancelled: payload as unknown as RenderCancelledEvent };
    case "render_finished":
      return { ...INITIAL_STATE, ...carried, finished: payload as unknown as RenderFinishedEvent };
    default:
      // "hello" (the socket's own greeting on connect) and anything future
      // and unrecognised both land here - connectedness changes, nothing
      // else does.
      return { ...INITIAL_STATE, ...carried };
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
        setState((previous) => reduce(previous, payload as { type: string } & Record<string, unknown>));
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

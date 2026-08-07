import { useState } from "react";
import type { ProjectDoc } from "./api";
import { thumbnailUrl } from "./api";
import type { EventsState } from "./useEvents";
import { IconStop } from "./Icons";

function titleFor(projects: ProjectDoc[], id: string): string {
  return projects.find((p) => p.id === id)?.title ?? id;
}

export interface TransportProps {
  events: EventsState;
  projects: ProjectDoc[];
  onCancel: (projectId: string) => void;
  onOpen: (projectId: string) => void;
}

function TransportThumb({ projectId }: { projectId: string }) {
  const [broken, setBroken] = useState(false);
  if (broken) return <span className="transport__thumb" aria-hidden="true" />;
  return (
    <span className="transport__thumb">
      <img className="washed" src={thumbnailUrl(projectId)} alt="" onError={() => setBroken(true)} />
    </span>
  );
}

/**
 * The transport bar — mounted once in `App.tsx` below the router outlet so a
 * running render stays visible from every screen. That principle is
 * unchanged; it is the most important element in the app.
 *
 * Restated in Organic: a status dot leads every state (so state is never
 * carried by colour alone), the label sits over a quieter detail line, and
 * the bar is a rounded 6px track with a terracotta gradient fill. Cancelled
 * stays neutral — the user's choice, not a crash.
 *
 * Purely presentational; every action is a callback prop.
 */
export default function Transport({ events, projects, onCancel, onOpen }: TransportProps) {
  const { progress, failure, cancelled, finished, connected, jobStarted, jobStep } = events;

  if (!connected) {
    return (
      <div className="transport transport--offline">
        <span className="dot pulse" style={{ background: "var(--dim)" }} aria-hidden="true" />
        <span className="transport__label">Reconnecting to the render service…</span>
      </div>
    );
  }

  // Several videos can be in flight at once (Slice 16), but this bar is one
  // line on every screen, so it leads with one of them and says how many
  // others there are. Home's job stack is where each one is shown in full.
  //
  // The lead is picked by project id rather than by "whichever event arrived
  // last" - `events.progress` is last-write-wins, so with two jobs running it
  // would swap identity on every tick and the bar would flicker between two
  // titles, two percentages and two Cancel buttons. Sorting is arbitrary but
  // stable, which is the property that matters.
  const live = Object.values(events.byProject).sort((a, b) => a.project_id.localeCompare(b.project_id));
  const lead = live[0] ?? progress;
  if (lead) {
    const title = titleFor(projects, lead.project_id);
    const others = Math.max(live.length - 1, 0);
    return (
      <div className="transport transport--running">
        <TransportThumb projectId={lead.project_id} />
        <span className="dot pulse" style={{ background: "var(--accent)" }} aria-hidden="true" />
        <button type="button" className="transport__open" onClick={() => onOpen(lead.project_id)}>
          <span className="transport__label">Rendering “{title}”</span>
          <span className="transport__detail">{lead.message}</span>
        </button>
        {others > 0 && (
          // Not a link and not a button: everything it would navigate to is
          // already one click away on Home, and a second control here would
          // compete with Cancel for the same glance.
          <span className="chip transport__more">+{others} more</span>
        )}
        <span className="transport__spacer" />
        <div className="transport__bar">
          <div className="transport__bar-fill" style={{ width: `${lead.percent}%` }} />
        </div>
        <span className="transport__percent">{lead.percent}%</span>
        <button type="button" className="btn btn--sm" onClick={() => onCancel(lead.project_id)}>
          <IconStop size={12} />
          Cancel
        </button>
      </div>
    );
  }

  // Started, but nothing measurable to report yet. Before this the bar was
  // empty for as long as the workspace took to prepare - minutes on a real
  // project - which is indistinguishable from the app having done nothing.
  if (jobStarted) {
    const title = titleFor(projects, jobStarted.project_id);
    return (
      <div className="transport transport--running">
        <TransportThumb projectId={jobStarted.project_id} />
        <span className="dot pulse" style={{ background: "var(--accent)" }} aria-hidden="true" />
        <button type="button" className="transport__open" onClick={() => onOpen(jobStarted.project_id)}>
          <span className="transport__label">
            {jobStarted.kind === "render" ? "Starting render" : "Working"} “{title}”
          </span>
          <span className="transport__detail">{jobStep?.detail || "Getting ready…"}</span>
        </button>
        <span className="transport__spacer" />
        <button type="button" className="btn btn--sm" onClick={() => onCancel(jobStarted.project_id)}>
          <IconStop size={12} />
          Cancel
        </button>
      </div>
    );
  }

  if (failure) {
    const title = titleFor(projects, failure.project_id);
    return (
      <div className="transport transport--failed">
        <TransportThumb projectId={failure.project_id} />
        <span className="dot" style={{ background: "var(--bad)" }} aria-hidden="true" />
        <button type="button" className="transport__open" onClick={() => onOpen(failure.project_id)}>
          <span className="transport__label">Render failed — “{title}”</span>
          <span className="transport__detail">{failure.message}</span>
        </button>
      </div>
    );
  }

  if (cancelled) {
    const title = titleFor(projects, cancelled.project_id);
    return (
      <div className="transport transport--cancelled">
        <TransportThumb projectId={cancelled.project_id} />
        <span className="dot" style={{ background: "var(--dim)" }} aria-hidden="true" />
        <button type="button" className="transport__open" onClick={() => onOpen(cancelled.project_id)}>
          <span className="transport__label">Render cancelled — “{title}”</span>
          <span className="transport__detail">Nothing was overwritten.</span>
        </button>
      </div>
    );
  }

  if (finished) {
    const title = titleFor(projects, finished.project_id);
    return (
      <div className="transport transport--finished">
        <TransportThumb projectId={finished.project_id} />
        <span className="dot" style={{ background: "var(--sage)" }} aria-hidden="true" />
        <button type="button" className="transport__open" onClick={() => onOpen(finished.project_id)}>
          <span className="transport__label">Render finished — “{title}”</span>
          <span className="transport__detail">{finished.output}</span>
        </button>
      </div>
    );
  }

  // Nothing running: render nothing. The bar used to sit here permanently
  // saying "Nothing rendering.", which the 3rd redesign drops - and the
  // status pill now answers "is anything happening" everywhere, all the
  // time, so the idle bar was a second voice saying less.
  return null;
}

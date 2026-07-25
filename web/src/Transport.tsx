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
  const { progress, failure, cancelled, finished, connected } = events;

  if (!connected) {
    return (
      <div className="transport transport--offline">
        <span className="dot pulse" style={{ background: "var(--dim)" }} aria-hidden="true" />
        <span className="transport__label">Reconnecting to the render service…</span>
      </div>
    );
  }

  if (progress) {
    const title = titleFor(projects, progress.project_id);
    return (
      <div className="transport transport--running">
        <TransportThumb projectId={progress.project_id} />
        <span className="dot pulse" style={{ background: "var(--accent)" }} aria-hidden="true" />
        <button type="button" className="transport__open" onClick={() => onOpen(progress.project_id)}>
          <span className="transport__label">Rendering “{title}”</span>
          <span className="transport__detail">{progress.message}</span>
        </button>
        <span className="transport__spacer" />
        <div className="transport__bar">
          <div className="transport__bar-fill" style={{ width: `${progress.percent}%` }} />
        </div>
        <span className="transport__percent">{progress.percent}%</span>
        <button type="button" className="btn btn--sm" onClick={() => onCancel(progress.project_id)}>
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

  return (
    <div className="transport transport--idle">
      <span className="dot" style={{ background: "var(--dim)" }} aria-hidden="true" />
      <span className="transport__label">Nothing rendering.</span>
    </div>
  );
}

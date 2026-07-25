import type { ProjectDoc } from "./api";
import type { EventsState } from "./useEvents";

function titleFor(projects: ProjectDoc[], id: string): string {
  return projects.find((p) => p.id === id)?.title ?? id;
}

export interface TransportProps {
  events: EventsState;
  projects: ProjectDoc[];
  onCancel: (projectId: string) => void;
  onOpen: (projectId: string) => void;
}

/**
 * The transport bar - lifted from video editors, mounted once in `App.tsx`
 * below the router outlet so a running render stays visible from every
 * screen, not just the one that started it. That is the entire point of
 * it: a render must be ambient, never something the user navigates back to
 * check on.
 *
 * Purely presentational - every action is a callback prop, nothing here
 * calls the API directly (`App.tsx` owns that, and reuses the same
 * functions Home's card actions use).
 */
export default function Transport({ events, projects, onCancel, onOpen }: TransportProps) {
  const { progress, failure, cancelled, finished, connected } = events;

  if (!connected) {
    // A dropped socket must fail visibly, not just go quiet - this is the
    // one state that pre-empts everything else, since without the socket
    // none of the other fields can be trusted to still be current.
    return (
      <div className="transport transport--offline">
        <span className="transport__label">Reconnecting to the render service…</span>
      </div>
    );
  }

  if (progress) {
    const title = titleFor(projects, progress.project_id);
    return (
      <div className="transport transport--running">
        <button type="button" className="transport__open" onClick={() => onOpen(progress.project_id)}>
          <span className="transport__label">Rendering "{title}"</span>
          <span className="transport__detail mono">{progress.message}</span>
        </button>
        <div className="transport__bar">
          <div className="transport__bar-fill" style={{ width: `${progress.percent}%` }} />
        </div>
        <span className="transport__percent mono">{progress.percent}%</span>
        <button type="button" className="btn" onClick={() => onCancel(progress.project_id)}>
          Cancel
        </button>
      </div>
    );
  }

  if (failure) {
    const title = titleFor(projects, failure.project_id);
    return (
      <div className="transport transport--failed">
        <button type="button" className="transport__open" onClick={() => onOpen(failure.project_id)}>
          <span className="transport__label">Render failed — "{title}"</span>
          <span className="transport__detail mono">{failure.message}</span>
        </button>
      </div>
    );
  }

  if (cancelled) {
    // Deliberately its own neutral state, never red - a cancel was the
    // user's choice, not a crash, and must not read as a failure.
    const title = titleFor(projects, cancelled.project_id);
    return (
      <div className="transport transport--cancelled">
        <button type="button" className="transport__open" onClick={() => onOpen(cancelled.project_id)}>
          <span className="transport__label">Render cancelled — "{title}"</span>
        </button>
      </div>
    );
  }

  if (finished) {
    const title = titleFor(projects, finished.project_id);
    return (
      <div className="transport transport--finished">
        <button type="button" className="transport__open" onClick={() => onOpen(finished.project_id)}>
          <span className="transport__label">Render finished — "{title}"</span>
        </button>
      </div>
    );
  }

  return (
    <div className="transport transport--idle">
      <span className="transport__label">No render in progress.</span>
    </div>
  );
}

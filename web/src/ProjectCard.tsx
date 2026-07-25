import type { ProjectDoc } from "./api";

type CardState = "done" | "failed" | "running" | "incomplete";

interface Resolved {
  state: CardState;
  failedPhase?: string;
}

/**
 * Derives the card's state from the project's phases, per the product rule:
 *
 *   - every phase done + master_output present  -> done
 *   - any phase failed                           -> failed (name it)
 *   - a phase running                            -> running (amber)
 *   - otherwise                                  -> incomplete
 *
 * Failed takes priority over running: a pipeline that died mid-phase can
 * still show an earlier phase's stale "running" record (nothing ever set it
 * back to pending), so failed must win or a dead render would read as live.
 */
function resolveState(project: ProjectDoc): Resolved {
  const phases = Object.entries(project.phases ?? {});

  const failed = phases.find(([, p]) => p.status === "failed");
  if (failed) {
    return { state: "failed", failedPhase: failed[0] };
  }

  const running = phases.find(([, p]) => p.status === "running");
  if (running) {
    return { state: "running" };
  }

  const allDone = phases.length > 0 && phases.every(([, p]) => p.status === "done");
  if (allDone && project.master_output) {
    return { state: "done" };
  }

  return { state: "incomplete" };
}

function formatCreated(iso?: string): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export interface ProjectCardProps {
  project: ProjectDoc;
  /** Navigate to the Studio view for this project - no side effect beyond
   * that (Play on a done project, Open on an incomplete/failed one, Open
   * studio on a running one). */
  onOpen: (id: string) => void;
  /** Navigate to Studio AND kick off a fresh render - what "Run again" (a
   * done project) and "Resume" (a failed one) actually do, now that Task 10
   * gives the render endpoint a UI to call it from. Only "render" is a real
   * phase this build can (re)run, so both buttons drive the same action;
   * their distinct labels communicate distinct intent to the user even
   * though the request they fire is identical. */
  onRender: (id: string) => void;
}

/** One project's card in the home grid. */
export default function ProjectCard({ project, onOpen, onRender }: ProjectCardProps) {
  const { state, failedPhase } = resolveState(project);
  const created = formatCreated(project.created);
  const open = () => onOpen(project.id);
  const render = () => onRender(project.id);

  let chipClass = "chip";
  let chipLabel = "Incomplete";
  if (state === "done") {
    chipClass = "chip chip--ok";
    chipLabel = "Done";
  } else if (state === "failed") {
    chipClass = "chip chip--bad";
    chipLabel = failedPhase ? `Failed · ${failedPhase}` : "Failed";
  } else if (state === "running") {
    // The one place amber is allowed on this card: this status IS the thing
    // that is happening right now.
    chipClass = "chip chip--amber";
    chipLabel = "Running";
  }

  return (
    <article className="project-card">
      <div className="project-card__thumb">
        <span className={chipClass}>{chipLabel}</span>
        <span className="chip mono">{project.aspect_ratio}</span>
      </div>
      <div className="project-card__body">
        <h3 className="project-card__title" dir="auto" title={project.title}>
          {project.title}
        </h3>
        {created && <p className="project-card__meta mono">{created}</p>}
        <div className="project-card__actions">
          {state === "done" && (
            <>
              <button type="button" className="btn" onClick={open}>
                Play
              </button>
              <button type="button" className="btn" onClick={render}>
                Run again
              </button>
            </>
          )}
          {state === "failed" && (
            <>
              <button type="button" className="btn" onClick={render}>
                Resume
              </button>
              <button type="button" className="btn" onClick={open}>
                Open
              </button>
            </>
          )}
          {state === "running" && (
            <button type="button" className="btn" onClick={open}>
              Open studio
            </button>
          )}
          {state === "incomplete" && (
            <button type="button" className="btn" onClick={open}>
              Open
            </button>
          )}
        </div>
      </div>
    </article>
  );
}

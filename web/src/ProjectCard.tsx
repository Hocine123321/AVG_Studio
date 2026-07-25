import { useState } from "react";
import type { ProjectDoc } from "./api";
import { thumbnailUrl } from "./api";
import { IconFrame } from "./Icons";

type CardState = "done" | "failed" | "running" | "incomplete";

interface Resolved {
  state: CardState;
  failedPhase?: string;
}

/**
 * Derives the card's state from the project's phases. Unchanged logic —
 * failed still takes priority over running, because a pipeline that died
 * mid-phase can leave a stale "running" record behind and a dead render
 * must never read as live.
 */
function resolveState(project: ProjectDoc): Resolved {
  const phases = Object.entries(project.phases ?? {});

  const failed = phases.find(([, p]) => p.status === "failed");
  if (failed) return { state: "failed", failedPhase: failed[0] };

  const running = phases.find(([, p]) => p.status === "running");
  if (running) return { state: "running" };

  const allDone = phases.length > 0 && phases.every(([, p]) => p.status === "done");
  if (allDone && project.master_output) return { state: "done" };

  return { state: "incomplete" };
}

function formatCreated(iso?: string): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const pad = (n: number) => String(n).padStart(2, "0");
  const month = d.toLocaleString("en-US", { month: "short" });
  return `${d.getDate()} ${month} · ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export interface ProjectCardProps {
  project: ProjectDoc;
  onOpen: (id: string) => void;
  onRender: (id: string) => void;
}

/**
 * One project's card. The card IS the frame — that principle is unchanged.
 * What changed:
 *
 *   - The action strip no longer sits in a grey block under the still. The
 *     buttons overlay the frame on hover/focus and the title fades out
 *     under them, so at rest the grid is nothing but stills.
 *   - Radii went to 28px and the whole card lifts 2px on hover.
 *   - "Done" is sage, not green-ish grey; terracotta is reserved for the
 *     card that is actually rendering.
 *
 * The empty state is a drawn frame icon rather than the `▤` glyph.
 */
export default function ProjectCard({ project, onOpen, onRender }: ProjectCardProps) {
  const { state, failedPhase } = resolveState(project);
  const created = formatCreated(project.created);
  const [thumbBroken, setThumbBroken] = useState(false);

  const open = () => onOpen(project.id);
  const render = () => onRender(project.id);

  let statusPillClass = "pill";
  let statusLabel = "Incomplete";
  if (state === "done") {
    statusPillClass = "pill pill--ok";
    statusLabel = "Done";
  } else if (state === "failed") {
    statusPillClass = "pill pill--bad";
    statusLabel = failedPhase ? `Failed · ${failedPhase}` : "Failed";
  } else if (state === "running") {
    statusPillClass = "pill pill--amber";
    statusLabel = "Rendering";
  }

  return (
    <article className="project-card" onClick={open}>
      <div className={`project-card__thumb${thumbBroken ? " project-card__thumb--empty" : ""}`}>
        {thumbBroken ? (
          <IconFrame size={30} />
        ) : (
          <img
            className="washed"
            src={thumbnailUrl(project.id)}
            alt=""
            loading="lazy"
            onError={() => setThumbBroken(true)}
          />
        )}
      </div>

      <div className="project-card__scrim" />

      <div className="project-card__badges">
        <span className={statusPillClass}>{statusLabel}</span>
        <span className="pill pill--ar">{project.aspect_ratio}</span>
      </div>

      <div className="project-card__bottom">
        <h3 className="project-card__title" dir="auto" title={project.title}>
          {project.title}
        </h3>
        {created && <p className="project-card__date">{created}</p>}
      </div>

      <div className="project-card__actions" onClick={(e) => e.stopPropagation()}>
        {state === "done" && (
          <>
            <button type="button" className="btn btn--primary" onClick={open}>
              Play
            </button>
            <button type="button" className="btn" onClick={render}>
              Run again
            </button>
          </>
        )}
        {state === "failed" && (
          <>
            <button type="button" className="btn btn--primary" onClick={render}>
              Resume
            </button>
            <button type="button" className="btn" onClick={open}>
              Open
            </button>
          </>
        )}
        {(state === "running" || state === "incomplete") && (
          <button type="button" className="btn btn--primary" onClick={open}>
            {state === "running" ? "Open studio" : "Open"}
          </button>
        )}
      </div>
    </article>
  );
}

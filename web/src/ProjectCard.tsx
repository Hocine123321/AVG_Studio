import { useState } from "react";
import type { ProjectDoc } from "./api";
import { thumbnailUrl } from "./api";

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
  // A project that predates Slice 5 (or has never been touched via the
  // takes routes yet) reports `takes: []` from `GET /api/projects` - it is
  // still, conceptually, exactly one take, so this never reads as "0
  // takes" (see `avg.core.takes.migrate_to_takes`, which only actually
  // creates that take once Studio's own `GET .../takes` call runs).
  const takeCount = Math.max(project.takes?.length ?? 0, 1);

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

  // The project's own ratio, as a CSS `aspect-ratio` value. `aspect_ratio`
  // arrives from `GET /api/projects` as "9:16" or "16:9"; anything else
  // falls back to portrait rather than producing an invalid declaration.
  const aspect = /^\d+:\d+$/.test(project.aspect_ratio)
    ? project.aspect_ratio.replace(":", " / ")
    : "9 / 16";

  return (
    <article className="project-card" onClick={open}>
      <div
        className={`project-card__thumb${thumbBroken ? " project-card__thumb--empty" : ""}`}
        style={{ aspectRatio: aspect }}
      >
        {thumbBroken ? (
          // `GET .../thumbnail` 404s honestly for a project that never
          // generated an image. A grey frame icon reads as a broken image;
          // a sentence says which of the two it actually is.
          <p className="project-card__noimg">No images yet</p>
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

      {/* The status chip owns the left, the aspect chip the right. A long
          "Failed · render" and the aspect chip used to share a corner. */}
      <div className="project-card__badges">
        <span className={statusPillClass}>{statusLabel}</span>
        <span className="pill pill--ar">{project.aspect_ratio}</span>
      </div>

      <div className="project-card__bottom">
        <h3 className="project-card__title" dir="auto" title={project.title}>
          {project.title}
        </h3>
        <p className="project-card__date">
          {created && <span>{created}</span>}
          <span>
            {takeCount} take{takeCount === 1 ? "" : "s"}
          </span>
        </p>
      </div>

      {/* No `.btn--primary` anywhere on a card. Home spends its one filled
          terracotta action on "New video"; thirteen filled buttons in a
          grid taught the user nothing about which one to press. The card's
          state is already carried by its chip - sage for done, amber for
          running, red for failed - which is colour the state earned and
          which no longer competes with a button for the same meaning. */}
      <div className="project-card__actions" onClick={(e) => e.stopPropagation()}>
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
        {(state === "running" || state === "incomplete") && (
          <button type="button" className="btn" onClick={open}>
            {state === "running" ? "Open studio" : "Open"}
          </button>
        )}
      </div>
    </article>
  );
}

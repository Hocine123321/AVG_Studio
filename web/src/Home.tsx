import { useEffect, useMemo, useRef, useState } from "react";
import type { ProjectDoc } from "./api";
import { thumbnailUrl } from "./api";
import type { ProgressEvent } from "./useEvents";
import { IconFrame, IconStop } from "./Icons";
import ProjectCard from "./ProjectCard";

export interface HomeProps {
  projects: ProjectDoc[];
  /** The single render this whole process ever runs at once (see
   * `avg/api/app.py` — a second `POST .../render` gets a 409), or `null`
   * when nothing is in flight. */
  progress: ProgressEvent | null;
  onOpen: (id: string) => void;
  onRender: (id: string) => void;
  onCancel: (id: string) => void;
}

function formatClock(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function formatRemaining(ms: number): string {
  const minutes = Math.round(ms / 60000);
  return minutes < 1 ? "<1 min left" : `~${minutes} min left`;
}

/** Unchanged from the previous build. The engine's `ProgressEvent` carries
 * no timestamps, so elapsed is measured from the first progress event this
 * hook saw for the current project and remaining is a straight-line
 * projection. Resets when a different project starts rendering. */
function useRenderClock(progress: ProgressEvent | null): { elapsedMs: number; remainingMs: number | null } {
  const startRef = useRef<{ id: string; at: number } | null>(null);
  const [, tick] = useState(0);

  useEffect(() => {
    if (!progress) {
      startRef.current = null;
      return;
    }
    if (!startRef.current || startRef.current.id !== progress.project_id) {
      startRef.current = { id: progress.project_id, at: Date.now() };
    }
  }, [progress]);

  useEffect(() => {
    if (!progress) return;
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progress?.project_id]);

  if (!progress || !startRef.current) return { elapsedMs: 0, remainingMs: null };
  const elapsedMs = Date.now() - startRef.current.at;
  if (progress.percent <= 0) return { elapsedMs, remainingMs: null };
  const total = (elapsedMs / progress.percent) * 100;
  return { elapsedMs, remainingMs: Math.max(total - elapsedMs, 0) };
}

interface HeroProps {
  project: ProjectDoc;
  progress: ProgressEvent;
  onOpen: (id: string) => void;
  onCancel: (id: string) => void;
}

/**
 * A render in progress takes the top of Home. Same idea as before, restated
 * in Organic: the percentage is now the headline, set in Caprasimo at 52px,
 * with the message reading as a caption beside it rather than the other way
 * round. You can tell across the room how far along it is.
 */
function Hero({ project, progress, onOpen, onCancel }: HeroProps) {
  const [thumbBroken, setThumbBroken] = useState(false);
  const { elapsedMs, remainingMs } = useRenderClock(progress);
  const pct = Math.min(Math.max(progress.percent, 0), 100);

  return (
    <div style={{ position: "relative", marginBottom: "calc(var(--gap) + 10px)" }}>
      <p className="lbl" style={{ marginBottom: 11, color: "var(--accent)" }}>
        <span className="dot pulse" style={{ background: "var(--accent)" }} aria-hidden="true" />
        Rendering now
      </p>

      <div className="hero">
        <div className={`hero__frame${thumbBroken ? " hero__frame--empty" : ""}`}>
          {thumbBroken ? (
            <IconFrame size={26} />
          ) : (
            <img className="washed" src={thumbnailUrl(project.id)} alt="" onError={() => setThumbBroken(true)} />
          )}
        </div>

        <div className="hero__info">
          <h3 className="hero__title" dir="auto" title={project.title}>
            {project.title}
          </h3>

          <p className="hero__meta">
            <span>{project.aspect_ratio}</span>
            {project.short_edge ? <span>{project.short_edge}p</span> : null}
            <span>
              {Math.max(project.takes?.length ?? 0, 1)} take
              {Math.max(project.takes?.length ?? 0, 1) === 1 ? "" : "s"}
            </span>
          </p>

          <div className="hero__stat">
            <span className="hero__pct">{progress.percent}%</span>
            <span className="hero__msg">{progress.message}</span>
          </div>

          <div className="track">
            <span className="track__fill" style={{ width: `${pct}%` }} />
          </div>

          <p className="hero__meta">
            <span>{formatClock(elapsedMs)} elapsed</span>
            {remainingMs !== null && <span>{formatRemaining(remainingMs)}</span>}
          </p>

          <div className="hero__actions">
            <button type="button" className="btn btn--primary" onClick={() => onOpen(project.id)}>
              Open studio
            </button>
            <button type="button" className="btn btn--ghost" onClick={() => onCancel(project.id)}>
              <IconStop />
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * The project grid.
 *
 * New here: a client-side title filter (no backend change needed — it reads
 * the list `App` already holds) and the section rule between the hero and
 * the library. Filtering by CHANNEL is deliberately absent: `ProjectDoc`
 * carries no channel field yet, and a filter that can't filter is worse
 * than none. Add `channel` to `avg/core/models.py` and the importer, and
 * the chips drop straight in — see PORTING.md.
 */
export default function Home({ projects, progress, onOpen, onRender, onCancel }: HomeProps) {
  const [query, setQuery] = useState("");

  const renderingProject = progress ? (projects.find((p) => p.id === progress.project_id) ?? null) : null;

  const gridProjects = useMemo(() => {
    const q = query.trim().toLowerCase();
    return projects
      .filter((p) => !(renderingProject && p.id === renderingProject.id))
      .filter((p) => (q ? p.title.toLowerCase().includes(q) : true));
  }, [projects, renderingProject, query]);

  return (
    <div className="home">
      <span className="blob blob--accent drift" style={{ right: -120, top: -140, width: 420, height: 420 }} />
      <span className="blob blob--sage" style={{ left: "38%", top: 220, width: 300, height: 300 }} />

      <header className="home__header">
        <div style={{ minWidth: 0 }}>
          <div className="home__kicker">Library</div>
          <h1 className="home__title">Your videos</h1>
        </div>
        <span className="home__spacer" />
        <input
          className="home__search"
          placeholder="Search topics"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search projects by title"
        />
      </header>

      {renderingProject && progress && (
        <Hero project={renderingProject} progress={progress} onOpen={onOpen} onCancel={onCancel} />
      )}

      <div className="home__section">
        <span className="lbl">{renderingProject ? "Everything else" : "Library"}</span>
        <span className="home__rule" />
        <span className="home__count">
          {gridProjects.length} {gridProjects.length === 1 ? "video" : "videos"}
        </span>
      </div>

      {gridProjects.length === 0 ? (
        <p className="home__empty">
          {projects.length === 0 ? "No projects yet." : query ? `Nothing matches "${query}".` : "Nothing else to show."}
        </p>
      ) : (
        <div className="project-grid">
          {gridProjects.map((project) => (
            <ProjectCard key={project.id} project={project} onOpen={onOpen} onRender={onRender} />
          ))}
        </div>
      )}
    </div>
  );
}

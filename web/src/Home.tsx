import { useMemo, useState } from "react";
import type { ProjectDoc } from "./api";
import { archiveProjects, thumbnailUrl } from "./api";
import type { ProgressEvent } from "./useEvents";
import { IconFrame, IconStop } from "./Icons";
import Overview from "./Overview";
import ProjectCard from "./ProjectCard";
import { useRenderClock } from "./renderClock";

export interface HomeProps {
  projects: ProjectDoc[];
  /** Every running job's progress, keyed by project id (Slice 16 - several
   * may run at once, up to `max_parallel_jobs`). A project with no entry
   * here but a `queue_position` set is accepted and waiting its turn; one
   * with neither is not running at all. */
  progress: Record<string, ProgressEvent>;
  onOpen: (id: string) => void;
  onRender: (id: string) => void;
  onCancel: (id: string) => void;
  /** Reload the project list after a bulk archive/restore. Home holds no
   * copy of its own - `App` owns the list, so the only honest way to show
   * the result is to ask for it again. */
  onArchived: () => Promise<void>;
  /** Total library bytes from `GET /api/projects`; `null` until the startup
   * scan has summed it. Feeds the Overview row's Storage card. */
  storageBytes: number | null;
  onGoSettings: () => void;
  onGoHealth: () => void;
  onGoChannels: () => void;
  /** Home's single filled terracotta action. Every card action is plain
   * `.btn`; this is the one thing on the screen the eye should land on. */
  onCreate: () => void;
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
            <button type="button" className="btn" onClick={() => onOpen(project.id)}>
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
 * One compact row: a running job's progress, or a queued job's position.
 * Used once several jobs are live at once (Slice 16) - the full `Hero`
 * above is reserved for the single-job case, where there's room to spend.
 */
function JobRow({
  project,
  progress,
  queuePosition,
  onOpen,
  onCancel,
}: {
  project: ProjectDoc;
  progress: ProgressEvent | null;
  queuePosition: number | null;
  onOpen: (id: string) => void;
  onCancel: (id: string) => void;
}) {
  const pct = progress ? Math.min(Math.max(progress.percent, 0), 100) : 0;
  return (
    <div className="job-row">
      <div className="job-row__info">
        <p className="job-row__title" dir="auto" title={project.title}>
          {project.title}
        </p>
        {progress ? (
          <>
            <p className="hero__meta">
              <span>{progress.percent}%</span>
              <span>{progress.message}</span>
            </p>
            <div className="track">
              <span className="track__fill" style={{ width: `${pct}%` }} />
            </div>
          </>
        ) : (
          <p className="hero__meta">
            <span>Queued · {ordinal(queuePosition ?? 1)}</span>
          </p>
        )}
      </div>
      <div className="job-row__actions">
        <button type="button" className="btn btn--sm" onClick={() => onOpen(project.id)}>
          Open
        </button>
        {progress && (
          <button type="button" className="btn btn--sm btn--ghost" onClick={() => onCancel(project.id)}>
            <IconStop />
          </button>
        )}
      </div>
    </div>
  );
}

function ordinal(n: number): string {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return `${n}${s[(v - 20) % 10] ?? s[v] ?? s[0]}`;
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
export default function Home({
  projects,
  progress,
  onOpen,
  onRender,
  onCancel,
  onArchived,
  storageBytes,
  onGoSettings,
  onGoHealth,
  onGoChannels,
  onCreate,
}: HomeProps) {
  const [query, setQuery] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [busy, setBusy] = useState(false);

  // Running: has a live progress entry. Queued: no progress yet, but the
  // backend says it's waiting its turn (`queue_position`, added because
  // there was otherwise no way to tell "queued" apart from "not running at
  // all" - see `GET /api/projects`). Order matches arrival: running jobs by
  // whichever `App.tsx` happened to fold last, queued jobs by position.
  const runningProjects = projects.filter((p) => progress[p.id] !== undefined);
  const queuedProjects = projects
    .filter((p) => progress[p.id] === undefined && p.queue_position != null)
    .sort((a, b) => (a.queue_position ?? 0) - (b.queue_position ?? 0));
  const liveIds = new Set([...runningProjects.map((p) => p.id), ...queuedProjects.map((p) => p.id)]);

  const archivedCount = projects.filter((p) => p.archived).length;

  const gridProjects = useMemo(() => {
    const q = query.trim().toLowerCase();
    return projects
      .filter((p) => !liveIds.has(p.id))
      // Archived projects are hidden, not gone - `showArchived` is the only
      // way to see them, and it shows ONLY them so "Restore" has an obvious
      // place to act.
      .filter((p) => Boolean(p.archived) === showArchived)
      .filter((p) => (q ? p.title.toLowerCase().includes(q) : true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projects, query, showArchived, runningProjects.length, queuedProjects.length]);

  // A selection only ever means something for rows currently on screen;
  // dropping the rest avoids archiving something the user cannot see.
  const visibleSelected = gridProjects.filter((p) => selected.has(p.id)).map((p) => p.id);

  function toggleSelected(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }

  async function applyArchive(archived: boolean) {
    if (visibleSelected.length === 0) return;
    setBusy(true);
    try {
      await archiveProjects(visibleSelected, archived);
      setSelected(new Set());
      await onArchived();
    } catch {
      // The list is reloaded either way, so a failure simply leaves
      // everything as it was rather than showing a state that never landed.
      await onArchived();
    } finally {
      setBusy(false);
    }
  }

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
        {(archivedCount > 0 || showArchived) && (
          <button
            type="button"
            className={`btn${showArchived ? " btn--on" : ""}`}
            onClick={() => {
              setShowArchived((on) => !on);
              setSelected(new Set());
            }}
          >
            {showArchived ? "Back to library" : `Archived (${archivedCount})`}
          </button>
        )}
        <input
          className="home__search"
          placeholder="Search topics"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search projects by title"
        />
        <button type="button" className="btn btn--primary" onClick={onCreate}>
          New video
        </button>
      </header>

      <Overview
        projects={projects}
        storageBytes={storageBytes}
        onGoSettings={onGoSettings}
        onGoHealth={onGoHealth}
        onGoChannels={onGoChannels}
      />

      {runningProjects.length === 1 && queuedProjects.length === 0 && (
        <Hero project={runningProjects[0]} progress={progress[runningProjects[0].id]} onOpen={onOpen} onCancel={onCancel} />
      )}

      {(runningProjects.length > 1 || queuedProjects.length > 0) && (
        <div className="job-stack" aria-label="Running videos">
          {runningProjects.map((p) => (
            <JobRow key={p.id} project={p} progress={progress[p.id]} queuePosition={null} onOpen={onOpen} onCancel={onCancel} />
          ))}
          {queuedProjects.map((p) => (
            <JobRow
              key={p.id}
              project={p}
              progress={null}
              queuePosition={p.queue_position ?? null}
              onOpen={onOpen}
              onCancel={onCancel}
            />
          ))}
        </div>
      )}

      <div className="home__section">
        <span className="lbl">{liveIds.size > 0 ? "Everything else" : "Library"}</span>
        <span className="home__rule" />
        <span className="home__count">
          {gridProjects.length} {gridProjects.length === 1 ? "video" : "videos"}
        </span>
      </div>

      {gridProjects.length === 0 ? (
        <p className="home__empty">
          {projects.length === 0
            ? "No projects yet."
            : query
              ? `Nothing matches "${query}".`
              : showArchived
                ? "Nothing archived."
                : "Nothing else to show."}
        </p>
      ) : (
        <div className="project-grid">
          {gridProjects.map((project) => (
            <div key={project.id} className="project-cell">
              <ProjectCard project={project} onOpen={onOpen} onRender={onRender} />
              <label
                className={`select-dot${selected.has(project.id) ? " select-dot--on" : ""}`}
                title={showArchived ? "Select to restore" : "Select to archive"}
              >
                <input
                  type="checkbox"
                  checked={selected.has(project.id)}
                  onChange={() => toggleSelected(project.id)}
                  aria-label={`Select ${project.title}`}
                />
              </label>
            </div>
          ))}
        </div>
      )}

      {visibleSelected.length > 0 && (
        <div className="select-bar" role="region" aria-label="Bulk actions">
          <span>
            {visibleSelected.length} selected
          </span>
          <button type="button" className="btn" onClick={() => setSelected(new Set())} disabled={busy}>
            Clear
          </button>
          {/* Archiving hides; it never deletes or moves a folder, so it needs
              no confirmation and stays a plain action. */}
          <button type="button" className="btn" onClick={() => applyArchive(!showArchived)} disabled={busy}>
            {busy ? "Working…" : showArchived ? "Restore" : "Archive"}
          </button>
        </div>
      )}
    </div>
  );
}

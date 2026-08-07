import { useMemo, useState } from "react";
import type { JobInfo, ProjectDoc } from "./api";
import { archiveProjects, thumbnailUrl } from "./api";
import type { ProgressEvent } from "./useEvents";
import { IconFrame, IconStop } from "./Icons";
import Overview from "./Overview";
import ProjectCard from "./ProjectCard";
import { useRenderClock } from "./renderClock";

export interface HomeProps {
  projects: ProjectDoc[];
  /** Every live job's latest progress, keyed by project id (`useEvents`'s
   * `byProject`). Populated once a running job has ticked at least once -
   * several can run at once as of Slice 16, which is why this replaced the
   * old single `progress: ProgressEvent | null`. */
  byProject: Record<string, ProgressEvent>;
  /** Every in-flight job, running or queued (`GET /api/jobs`, polled in
   * `App.tsx`). The only source for a queued job's existence and position:
   * a queued job holds no thread yet, so it never announces itself over
   * `/ws/events` the way `byProject` above learns about a running one (see
   * `avg.core.jobs.JobRegistry`'s own docstring). */
  jobs: JobInfo[];
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

/** "2nd", "3rd", "11th" - `Queued · 2nd` is the exact copy the row below uses.
 * 1-based, matching `JobInfo.position` (`avg.core.jobs.JobRegistry.snapshot`). */
function ordinal(n: number): string {
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${n}th`;
  switch (n % 10) {
    case 1:
      return `${n}st`;
    case 2:
      return `${n}nd`;
    case 3:
      return `${n}rd`;
    default:
      return `${n}th`;
  }
}

/** A small thumbnail for one row of the job stack - the same broken-image
 * fallback `Hero` and `Transport.tsx`'s `TransportThumb` both use, just
 * sized for a compact row instead of a hero or the transport bar. */
function JobThumb({ projectId }: { projectId: string }) {
  const [broken, setBroken] = useState(false);
  if (broken) {
    return (
      <span className="job-row__thumb job-row__thumb--empty">
        <IconFrame size={16} />
      </span>
    );
  }
  return (
    <span className="job-row__thumb">
      <img className="washed" src={thumbnailUrl(projectId)} alt="" onError={() => setBroken(true)} />
    </span>
  );
}

interface JobRowProps {
  project: ProjectDoc;
  progress: ProgressEvent;
  onOpen: (id: string) => void;
  onCancel: (id: string) => void;
}

/**
 * One running job's row inside the stack, once it has run alone (see `Hero`
 * above) is no longer true. Same numbers the hero shows - percent, message,
 * a fillable track - laid out compactly because several of these may sit on
 * screen together. Built from `.hero__meta`, `.track` and `.track__fill`
 * rather than new styling, per this file's own rule against two controls
 * doing the same job with different values.
 */
function JobRow({ project, progress, onOpen, onCancel }: JobRowProps) {
  const pct = Math.min(Math.max(progress.percent, 0), 100);
  return (
    <div className="job-row">
      <JobThumb projectId={project.id} />
      <button type="button" className="job-row__open" onClick={() => onOpen(project.id)}>
        <span className="job-row__title" dir="auto" title={project.title}>
          {project.title}
        </span>
        <p className="hero__meta job-row__meta">
          <span>{progress.percent}%</span>
          <span>{progress.message}</span>
        </p>
        <div className="track">
          <span className="track__fill" style={{ width: `${pct}%` }} />
        </div>
      </button>
      <button type="button" className="btn btn--sm btn--ghost" onClick={() => onCancel(project.id)}>
        <IconStop size={12} />
        Cancel
      </button>
    </div>
  );
}

interface QueuedRowProps {
  project: ProjectDoc;
  position: number;
  onOpen: (id: string) => void;
  onCancel: (id: string) => void;
}

/**
 * A queued job's row. It has no percentage to show - its thread has not
 * started, so nothing is ticking - which is exactly why it never gets a
 * `.track` it cannot fill (a stalled-looking empty bar would read as
 * broken, not waiting). Its position in the FIFO queue
 * (`GET /api/jobs`, `avg.core.jobs.JobRegistry.snapshot`) is the one honest
 * thing to say about it instead. Cancellable like a running job - the
 * registry accepts a cancel on a job that has not started yet and simply
 * never starts it.
 */
function QueuedRow({ project, position, onOpen, onCancel }: QueuedRowProps) {
  return (
    <div className="job-row job-row--queued">
      <JobThumb projectId={project.id} />
      <button type="button" className="job-row__open" onClick={() => onOpen(project.id)}>
        <span className="job-row__title" dir="auto" title={project.title}>
          {project.title}
        </span>
        <p className="hero__meta job-row__meta">
          <span className="chip chip--amber">Queued · {ordinal(position)}</span>
        </p>
      </button>
      <button type="button" className="btn btn--sm btn--ghost" onClick={() => onCancel(project.id)}>
        <IconStop size={12} />
        Cancel
      </button>
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
export default function Home({
  projects,
  byProject,
  jobs,
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

  // Running rows only ever come from `byProject`, never from `jobs` alone:
  // `jobs` says a job's thread has started, but the row needs an actual
  // percentage and message to show, and those arrive over the socket, not
  // the poll. The gap between "thread started" and "first tick" is the same
  // one the single-job Hero always had (it showed nothing until the first
  // `progress` event either) - not a new hole Slice 16 opened.
  const runningRows = useMemo(
    () =>
      jobs
        .filter((job) => !job.queued)
        .map((job) => {
          const project = projects.find((p) => p.id === job.project_id);
          const progress = byProject[job.project_id];
          return project && progress ? { project, progress } : null;
        })
        .filter((row): row is { project: ProjectDoc; progress: ProgressEvent } => row !== null),
    [jobs, byProject, projects],
  );

  const queuedRows = useMemo(
    () =>
      jobs
        .filter((job) => job.queued)
        .sort((a, b) => (a.position ?? 0) - (b.position ?? 0))
        .map((job) => {
          const project = projects.find((p) => p.id === job.project_id);
          return project && job.position !== null ? { project, position: job.position } : null;
        })
        .filter((row): row is { project: ProjectDoc; position: number } => row !== null),
    [jobs, projects],
  );

  // A job in flight - running or queued - lives in the stack above, never
  // also in the plain library grid below it.
  const liveIds = useMemo(() => new Set(jobs.map((j) => j.project_id)), [jobs]);

  // The single-job hero is reserved for exactly that case: one job running,
  // nothing queued behind it. Anything else - a second running job, or one
  // running with company waiting - moves everything into the compact stack,
  // so the screen never has one giant hero sitting above a second job with
  // nowhere to go.
  const solo = runningRows.length === 1 && queuedRows.length === 0 ? runningRows[0] : null;
  const anyLive = runningRows.length > 0 || queuedRows.length > 0;

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
  }, [projects, liveIds, query, showArchived]);

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

      {solo ? (
        <Hero project={solo.project} progress={solo.progress} onOpen={onOpen} onCancel={onCancel} />
      ) : anyLive ? (
        <div className="job-stack">
          <p className="lbl" style={{ marginBottom: 11, color: "var(--accent)" }}>
            <span className="dot pulse" style={{ background: "var(--accent)" }} aria-hidden="true" />
            {runningRows.length > 0 ? "Rendering now" : "Queued"}
          </p>
          {runningRows.map(({ project, progress }) => (
            <JobRow key={project.id} project={project} progress={progress} onOpen={onOpen} onCancel={onCancel} />
          ))}
          {queuedRows.map(({ project, position }) => (
            <QueuedRow
              key={project.id}
              project={project}
              position={position}
              onOpen={onOpen}
              onCancel={onCancel}
            />
          ))}
        </div>
      ) : null}

      <div className="home__section">
        <span className="lbl">{anyLive ? "Everything else" : "Library"}</span>
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

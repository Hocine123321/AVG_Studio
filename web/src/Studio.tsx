import { useEffect, useState } from "react";
import { createTake, deleteTake, getCosts, getTakes, renameTake, videoUrl } from "./api";
import type { CostTable, PhaseRecord, PhaseStatus, ProjectDoc, ScriptSubstate, Take } from "./api";
import { formatUsd, projectSpend } from "./costs";
import type { LivePhase, PhaseFailedEvent, ProgressEvent, RenderCancelledEvent, RenderFinishedEvent } from "./useEvents";
import { IconAlert, IconArrowLeft, IconCheck, IconPlay, IconStop } from "./Icons";
import NewTakePanel from "./NewTakePanel";
import { groupStatus, PHASE_DEFS, PHASE_ORDER, type DisplayPhaseStatus } from "./phases";
import PhaseArtifact from "./artifacts/PhaseArtifact";
import Player from "./Player";
import TakeStrip from "./TakeStrip";

function formatDuration(ms?: number | null): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  const total = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function formatBytes(n?: number | null): string {
  if (!n || n <= 0) return "—";
  const mb = n / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(1)} MB`;
}

/** Same trick every other screen uses: show the server's own refusal
 * sentence, not the "POST ... failed: 400 - {...}" wrapper around it. */
function extractDetail(message: string): string {
  const match = message.match(/"detail"\s*:\s*"([^"]*)"/);
  return match ? match[1] : message;
}

function statusLabel(status: DisplayPhaseStatus): string {
  if (status === "done") return "Done";
  if (status === "failed") return "Failed";
  if (status === "running") return "Running";
  if (status === "partial") return "1 of 2 done";
  return "Not started";
}

function statusChipClass(status: DisplayPhaseStatus): string {
  if (status === "done") return "chip chip--ok";
  if (status === "failed") return "chip chip--bad";
  if (status === "running" || status === "partial") return "chip chip--amber";
  return "chip";
}

/** Which row the panel opens on: the failed one, else the running one, else
 * the last completed one, else the first. Takes a plain phases dict now
 * (a selected TAKE's own `phases`, not necessarily `ProjectDoc.phases` -
 * see this file's own header comment on why those two can differ once a
 * project has more than one take) rather than a whole `ProjectDoc`. */
function defaultPhase(phases: Record<string, PhaseRecord>): string {
  const failed = PHASE_ORDER.find(({ key }) => phases[key]?.status === "failed");
  if (failed) return failed.key;
  const running = PHASE_ORDER.find(({ key }) => phases[key]?.status === "running");
  if (running) return running.key;
  for (let i = PHASE_ORDER.length - 1; i >= 0; i--) {
    if (phases[PHASE_ORDER[i].key]?.status === "done") return PHASE_ORDER[i].key;
  }
  return PHASE_ORDER[0].key;
}

/** What the primary action should do, given where this take actually got to.
 *
 * The bug this replaces: the CTA read `project.phases.render` and nothing
 * else, so a project that stopped at Captions - or at Brief - offered
 * "Start render", because `render` was `pending` in all three cases. It
 * ignored both the other seven phases and the take being viewed.
 *
 * The rule now is the first phase in REGISTRY order that is not done:
 *
 *   - nothing left        -> "Run again", a straight re-render
 *   - only `render` left  -> "Start render", the same re-render
 *   - anything else left  -> resume THERE, by creating a take that re-rolls
 *     from that phase (`reroll_from` re-runs it and everything after it -
 *     which for the first unfinished phase is exactly "carry on").
 *
 * A failed phase counts as unfinished, so a run that died at Visuals offers
 * to resume at Visuals rather than pretending the video is renderable.
 */
function resumeTarget(phases: Record<string, PhaseRecord>): { key: string; label: string } | null {
  const next = PHASE_ORDER.find(({ key }) => phases[key]?.status !== "done");
  return next ? { key: next.key, label: next.label } : null;
}

function totalDurationMs(phases: Record<string, PhaseRecord>): number {
  return Object.values(phases).reduce((sum, p) => sum + (p.duration_ms ?? 0), 0);
}

/** Add the display-only Script leaves to the backend's eight phase records.
 * The list endpoint supplies current-take artifact truth while idle. During
 * a fresh client-side run it has not yet been refreshed, so a completed
 * script PhaseRecord is the short-lived fallback. */
function displayStatuses(
  phases: Record<string, PhaseRecord>,
  scriptSubstate: ScriptSubstate | undefined,
  activePhase: string | null,
): Record<string, PhaseStatus> {
  const statuses: Record<string, PhaseStatus> = {};
  for (const { key } of PHASE_ORDER) statuses[key] = phases[key]?.status ?? "pending";

  if (activePhase === "script") {
    // The runner buffers Script's 0/50/100 progress until it has returned,
    // so phase_started is the only live signal. Do not pretend to know that
    // segmentation has begun before the backend can tell us.
    statuses.draft = "running";
    statuses.segmentation = "pending";
  } else if (statuses.script === "failed") {
    statuses.draft = "failed";
    statuses.segmentation = "failed";
  } else {
    const resolved = scriptSubstate ?? (statuses.script === "done" ? "done" : "pending");
    statuses.draft = resolved === "pending" ? "pending" : "done";
    statuses.segmentation = resolved === "done" ? "done" : "pending";
  }
  return statuses;
}

function ArtifactDetail({
  phaseKey,
  record,
  progress,
  failure,
  finished,
  project,
  take,
  isCurrentTake,
  onStartRender,
  onCancel,
  takeId,
  duration,
  cost,
}: {
  phaseKey: string;
  record: PhaseRecord | undefined;
  progress: ProgressEvent | null;
  failure: PhaseFailedEvent | null;
  finished: RenderFinishedEvent | null;
  project: ProjectDoc;
  /** The selected take, when takes have loaded - `undefined` only in the
   * brief window before the first `GET .../takes` response lands. */
  take: Take | undefined;
  /** Whether the SELECTED take is also the CURRENT one - `/video`,
   * `/images` and `/thumbnail` only ever serve the current take's own
   * files (see `avg/api/app.py`), so a selected take that isn't current
   * can't actually play back its media through those routes yet. This
   * panel says so honestly rather than silently showing the wrong take's
   * video under the right take's label. */
  isCurrentTake: boolean;
  onStartRender: () => Promise<void>;
  onCancel: () => void;
  /** Which take's files the panel should read. `null` only in the window
   * before the first `GET .../takes` lands. */
  takeId: number | null;
  duration: string;
  cost: string | null;
}) {
  const status: PhaseStatus = record?.status ?? "pending";

  // A running phase streams into the same view it is later inspected in.
  if (phaseKey === "render" && progress) {
    return (
      <div className="artifact-progress">
        <div style={{ display: "flex", alignItems: "baseline", gap: 16 }}>
          <span className="artifact-progress__pct">{progress.percent}%</span>
          <span style={{ fontSize: 15, color: "var(--muted)" }}>{progress.message}</span>
        </div>
        <div className="artifact-progress__bar">
          <div className="artifact-progress__fill" style={{ width: `${progress.percent}%` }} />
        </div>
        <button type="button" className="btn" onClick={onCancel}>
          <IconStop />
          Cancel render
        </button>
      </div>
    );
  }

  if (status === "failed") {
    // The sentence in plain words, the raw detail underneath, then the
    // actions. Which actions are valid is the taxonomy's call, not this
    // component's — see `avg/core/errors.py`.
    const message = failure?.message ?? record?.error ?? "This phase failed.";
    const detail = failure?.detail ?? "";
    const kind = failure?.kind;

    return (
      <div className="artifact-failed">
        <div className="artifact-failed__head">
          <span className="artifact-failed__icon">
            <IconAlert />
          </span>
          {statusLabel(status)} — {PHASE_ORDER.find((p) => p.key === phaseKey)?.label ?? phaseKey}
        </div>
        <p>{message}</p>
        {detail && <div className="artifact-failed__detail">{detail}</div>}
        <div className="artifact-failed__actions">
          {/* Secondary here on purpose - the header CTA is already the one
           * filled `.btn--primary` action for this same "Run again", and two
           * filled buttons on one screen is the exact defect the design
           * tokens warn against. */}
          <button type="button" className="btn" onClick={() => void onStartRender()}>
            Run again
          </button>
          {kind && <span className="chip chip--bad">{kind}</span>}
        </div>
      </div>
    );
  }

  if (phaseKey === "render" && status === "done" && !isCurrentTake) {
    // The video/thumbnail routes only ever serve the CURRENT take's file
    // (see this component's own prop comment) - a selected take that isn't
    // current gets an honest summary instead of a video player quietly
    // showing the wrong take's output under the right take's label.
    return (
      <div className="artifact-summary" style={{ maxWidth: 480 }}>
        <div className="artifact-summary__head">
          <span className="artifact-summary__icon">
            <IconCheck />
          </span>
          Render finished
        </div>
        <dl className="artifact-summary__grid">
          {take?.master_output && (
            <>
              <dt>Output</dt>
              <dd>{take.master_output}</dd>
            </>
          )}
          <dt>Total time</dt>
          <dd>{formatDuration(totalDurationMs(take?.phases ?? {}))}</dd>
        </dl>
        <p className="artifact-empty" style={{ marginTop: 12 }}>
          Video preview isn't available for this take yet — only the current take can play back here.
        </p>
      </div>
    );
  }

  if (phaseKey === "render" && status === "done") {
    const output = finished?.output ?? project.master_output;
    const size = finished ? formatBytes(finished.size_bytes) : null;
    return (
      <div className="artifact-done">
        <Player src={videoUrl(project.id)} />
        <div className="artifact-summary">
          <div className="artifact-summary__head">
            <span className="artifact-summary__icon">
              <IconCheck />
            </span>
            Render finished
          </div>
          <dl className="artifact-summary__grid">
            {output && (
              <>
                <dt>Output</dt>
                <dd>{output}</dd>
              </>
            )}
            {size && (
              <>
                <dt>Size</dt>
                <dd>{size}</dd>
              </>
            )}
            <dt>Aspect</dt>
            <dd>{project.aspect_ratio}</dd>
          </dl>
          <div className="artifact-failed__actions">
            {/* Secondary here too, for the same reason as the failed-state
             * button above - the header already carries the one filled CTA. */}
            <button type="button" className="btn" onClick={() => void onStartRender()}>
              <IconPlay />
              Run again
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Every non-render phase shows its own real output. A phase that has
  // not run yet still says so - there is nothing on disk to show - but
  // "Done." on its own, which is what six of the eight used to render, is
  // gone.
  if (status === "pending") return <p className="artifact-empty">Not started yet.</p>;

  return (
    <>
      {status === "running" && (
        <p className="lbl" style={{ marginBottom: 12 }}>
          <span className="dot pulse" style={{ background: "var(--accent)" }} aria-hidden="true" />
          Running — showing what has landed so far
        </p>
      )}
      <PhaseArtifact
        projectId={project.id}
        takeId={takeId}
        phaseKey={phaseKey}
        duration={duration}
        cost={cost}
      />
    </>
  );
}

export interface StudioProps {
  project: ProjectDoc;
  progress: ProgressEvent | null;
  /** The current pipeline phase, from its existing phase_started event.
   * `null` for direct `/render` jobs, which have no pipeline phase event. */
  activePhase?: string | null;
  failure?: PhaseFailedEvent | null;
  cancelled?: RenderCancelledEvent | null;
  finished?: RenderFinishedEvent | null;
  startError?: string | null;
  onStartRender: () => Promise<void>;
  onCancel: () => void;
  /** New: the back arrow in the header. The icon rail can't navigate out of
   * Studio (it's only reached from a card), so this is the way home. */
  onBack?: () => void;
  /** Live phase state for THIS project, folded from the event socket
   * (`useEvents`). Overlays the persisted records so the rail is right
   * between refetches and across a reconnect. */
  livePhases?: Record<string, LivePhase>;
}

/**
 * The screen the app exists for.
 *
 * The phase rail is now a SPINE: eight nodes threaded on one continuous
 * line, filling sage as they complete, with a pulsing terracotta halo on
 * whichever one is running. The previous build drew eight independent rows
 * with a coloured dot each — correct, but it read as a status list rather
 * than as a pipeline with a direction.
 *
 * Slice 5 (Task 4) adds the other axis: takes run ACROSS the top, under the
 * header, via `TakeStrip`. No new screen - selecting a take just repoints
 * this same rail/panel at that take's own `phases` dict instead of
 * `project.phases` (which, once a project has more than one take, only
 * ever reflects a legacy pre-takes project or goes stale - see
 * `avg.api.routes_takes`'s own docstring on why a take's re-run mutates
 * `Take.phases`, never `ProjectDoc.phases`).
 */
export default function Studio({
  project,
  progress,
  activePhase = null,
  failure = null,
  cancelled = null,
  finished = null,
  startError = null,
  onStartRender,
  onCancel,
  onBack,
  livePhases,
}: StudioProps) {
  const [selected, setSelected] = useState<string>(() => defaultPhase(project.phases));

  // ---- takes (Slice 5, Task 4) ----
  const [takes, setTakes] = useState<Take[] | null>(null);
  const [currentTakeId, setCurrentTakeId] = useState<number | null>(null);
  const [diskBytes, setDiskBytes] = useState<number>(0);
  const [selectedTakeId, setSelectedTakeId] = useState<number | null>(null);
  const [takesError, setTakesError] = useState<string | null>(null);
  // Set the instant `NewTakePanel` reports a create succeeded, cleared on
  // the first terminal event for this project - bridges the gap before the
  // new take's own `phases` dict has recorded a single transition yet (see
  // `TakeStrip.tsx`'s `resolveStatus`).
  const [runningTakeId, setRunningTakeId] = useState<number | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  // Which phase the new-take panel should open pre-set to re-roll from.
  // `null` when the panel was opened from the take strip instead - always a
  // backend phase key, never a Script display leaf, since the route 400s on
  // anything outside `REGISTRY` (see `phases.ts`).
  const [rerollTarget, setRerollTarget] = useState<string | null>(null);
  // The primary action's own in-flight/error state. Separate from the
  // render start error above: resuming creates a take, which can be refused
  // for reasons a render cannot (no active channel, for one).
  const [resuming, setResuming] = useState(false);
  const [resumeError, setResumeError] = useState<string | null>(null);
  // The maintained cost table. Fetched once; a failure just hides the spend
  // chip rather than taking the screen down with it.
  const [costTable, setCostTable] = useState<CostTable | null>(null);

  useEffect(() => {
    getCosts().then(setCostTable).catch(() => setCostTable(null));
  }, []);

  function refreshTakes() {
    return getTakes(project.id).then((res) => {
      setTakes(res.takes);
      setCurrentTakeId(res.current_take_id);
      setDiskBytes(res.disk_bytes);
      setTakesError(null);
      return res;
    });
  }

  // (Re)load whenever the project changes - and reset every other bit of
  // take state first, so a stale selection from a previously viewed project
  // never briefly shows through.
  useEffect(() => {
    setTakes(null);
    setSelectedTakeId(null);
    setRunningTakeId(null);
    refreshTakes()
      .then((res) => setSelectedTakeId(res.current_take_id ?? res.takes[0]?.id ?? null))
      .catch((err) => setTakesError(err instanceof Error ? err.message : String(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id]);

  // While a take is running, poll its persisted records as a recovery path
  // for a dropped socket. Existing phase-start/finish events update the
  // current UI immediately; this keeps it correct across a reconnect.
  useEffect(() => {
    if (runningTakeId === null) return;
    const id = setInterval(() => {
      refreshTakes().catch(() => {});
    }, 2500);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runningTakeId]);

  // Any event for this project means take data is worth refreshing (a
  // render just failed/finished/was cancelled, or is progressing). A live
  // active pipeline phase also snaps both the selected TAKE (to whichever
  // one is actually running) and the selected PHASE there.
  useEffect(() => {
    const forThisProject =
      progress?.project_id === project.id ||
      failure?.project_id === project.id ||
      cancelled?.project_id === project.id ||
      finished?.project_id === project.id ||
      activePhase !== null;
    if (!forThisProject) return;

    refreshTakes().catch(() => {});
    if (failure || cancelled || finished) setRunningTakeId(null);
    if (activePhase !== null) {
      setSelected(activePhase);
      if (runningTakeId !== null) setSelectedTakeId(runningTakeId);
    } else if (progress?.project_id === project.id) {
      setSelected("render");
      if (runningTakeId !== null) setSelectedTakeId(runningTakeId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progress, activePhase, failure, cancelled, finished]);

  const selectedTake = takes?.find((t) => t.id === selectedTakeId);
  const isCurrentTake = selectedTakeId !== null && selectedTakeId === currentTakeId;

  // Jump to the sensible default phase for whichever take just became
  // selected - the same "failed, else running, else last done" rule
  // `defaultPhase` already applies, just re-run per take instead of once.
  useEffect(() => {
    if (selectedTake) setSelected(defaultPhase(selectedTake.phases));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTakeId]);

  async function handleDeleteTake(id: number) {
    await deleteTake(project.id, id);
    const res = await refreshTakes();
    if (selectedTakeId === id) {
      setSelectedTakeId(res.current_take_id ?? res.takes[0]?.id ?? null);
    }
  }

  async function handleRenameTake(id: number, label: string) {
    await renameTake(project.id, id, label);
    await refreshTakes();
  }

  function handleTakeCreated(take: Take) {
    setPanelOpen(false);
    setRunningTakeId(take.id);
    setSelectedTakeId(take.id);
    refreshTakes().catch(() => {});
  }

  // Live state overlays the persisted records, and ONLY for the current
  // take: a historical take must never inherit the running take's
  // progress. The persisted records are still the base, so a phase that
  // finished before this session started is not lost.
  const persistedPhases: Record<string, PhaseRecord> = selectedTake?.phases ?? project.phases;
  const phasesForSelected: Record<string, PhaseRecord> =
    isCurrentTake && livePhases
      ? { ...persistedPhases, ...(livePhases as Record<string, PhaseRecord>) }
      : persistedPhases;
  // `/api/projects` derives this only for the current take. A historical
  // take must not inherit that current take's artifact state, so its own
  // persisted Script record remains the fallback in `displayStatuses`.
  const selectedScriptSubstate = isCurrentTake ? project.script_substate : undefined;
  const statuses = displayStatuses(phasesForSelected, selectedScriptSubstate, activePhase);

  // Anything in this take mid-flight counts as running, not just `render` -
  // a project generating images is as busy as one encoding video, and the
  // CTA has to say Cancel for both.
  const isRunning =
    runningTakeId === selectedTakeId ||
    PHASE_ORDER.some(({ key }) => phasesForSelected[key]?.status === "running");
  const resume = resumeTarget(phasesForSelected);
  const actionLabel = isRunning
    ? "Cancel"
    : resume === null
      ? "Run again"
      : resume.key === "render"
        ? "Start render"
        : `Continue · ${resume.label}`;

  /** The CTA. Only the two render-shaped cases hit `/render`; every other
   * unfinished phase resumes the pipeline from there instead, which is what
   * the old "Start render" label was wrong about. */
  async function handlePrimaryAction() {
    if (isRunning) {
      onCancel();
      return;
    }
    setResumeError(null);
    setResuming(true);
    try {
      if (resume === null || resume.key === "render") {
        await onStartRender();
        return;
      }
      const take = await createTake(project.id, { overrides: {}, reroll_from: resume.key });
      handleTakeCreated(take);
    } catch (err) {
      setResumeError(extractDetail(err instanceof Error ? err.message : String(err)));
    } finally {
      setResuming(false);
    }
  }

  const record = phasesForSelected[selected];
  const selectedDef = PHASE_DEFS.find((def) => def.key === selected);
  const selectedStatus = selectedDef ? groupStatus(selectedDef, statuses) : "pending";
  const label = selectedDef?.label ?? selected;

  return (
    <div className="studio">
      <header className="studio__header">
        {onBack && (
          <button type="button" className="studio__back" onClick={onBack} aria-label="Back to your videos">
            <IconArrowLeft />
          </button>
        )}
        <div style={{ minWidth: 0 }}>
          <div className="studio__kicker">Project</div>
          <h1 className="studio__title" dir="auto" title={project.title}>
            {project.title}
          </h1>
        </div>
        <span className="home__spacer" />
        <span className="chip">{project.aspect_ratio}</span>
        <span className="chip" title="Total size on disk — takes are never auto-pruned">
          {formatBytes(diskBytes)}
        </span>
        {costTable && (
          <span
            className="chip mono"
            title="Estimated from a maintained price table, not metered usage. Counts phases that are done or running in the take you are looking at."
          >
            ~{formatUsd(projectSpend(phasesForSelected, costTable, selectedScriptSubstate))}
          </span>
        )}
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => void handlePrimaryAction()}
          disabled={resuming}
        >
          {isRunning ? <IconStop /> : <IconPlay />}
          {resuming ? "Starting…" : actionLabel}
        </button>
      </header>

      <TakeStrip
        takes={takes ?? []}
        loading={takes === null}
        currentTakeId={currentTakeId}
        selectedTakeId={selectedTakeId}
        runningTakeId={runningTakeId}
        onSelect={setSelectedTakeId}
        onDelete={handleDeleteTake}
        onRename={handleRenameTake}
        onNewTake={() => {
          setRerollTarget(null);
          setPanelOpen(true);
        }}
      />

      {takesError && (
        <div className="studio__banner studio__banner--bad">
          <p>Couldn't load takes.</p>
          <p>{takesError}</p>
        </div>
      )}

      {startError && (
        <div className="studio__banner studio__banner--bad">
          <p>Couldn't start the render.</p>
          <p>{startError}</p>
        </div>
      )}

      {resumeError && (
        <div className="studio__banner studio__banner--bad">
          <p>Couldn't resume this project.</p>
          <p>{resumeError}</p>
        </div>
      )}

      {cancelled && !isRunning && !startError && (
        <div className="studio__banner studio__banner--muted">
          <p>Render cancelled. Nothing in the project folder was overwritten.</p>
        </div>
      )}

      <div className="studio__body">
        <div className="phase-rail">
          <p className="lbl" style={{ marginBottom: 14 }}>
            Pipeline
          </p>
          <div className="phase-rail__inner">
            <span className="phase-rail__thread" aria-hidden="true" />
            {PHASE_DEFS.map((def) => {
              const { key, label: rowLabel } = def;
              const rowRecord = phasesForSelected[key];
              const status = groupStatus(def, statuses);
              const live = key === "render" && activePhase === null && isCurrentTake && progress;
              return (
                <button
                  key={key}
                  type="button"
                  className={`phase-row phase-row--${status}${selected === key ? " phase-row--selected" : ""}`}
                  onClick={() => setSelected(key)}
                  title={statusLabel(status)}
                >
                  <span className={`phase-row__node${status === "running" ? " halo" : ""}`} aria-hidden="true" />
                  <span className="phase-row__name">
                    {rowLabel}
                    {def.sub && <span className="phase-row__sub">{def.sub.length}</span>}
                  </span>
                  <span className="phase-row__dur">
                    {live ? `${progress.percent}%` : status === "partial" ? "1/2" : formatDuration(rowRecord?.duration_ms)}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="artifact-panel">
          <div className="artifact-panel__head">
            <h2 className="artifact-panel__title">{label}</h2>
            <span className={statusChipClass(selectedStatus)}>{statusLabel(selectedStatus)}</span>
            <span className="home__spacer" />
            {/* The mockup's per-phase "Run again". It opens the new-take
                panel with this phase pre-picked rather than adding a second
                mechanism: `reroll_from` already re-runs the chosen phase and
                everything downstream, in a new take, so the only thing
                missing was a way to say it from the phase you are looking
                at. Unaccented - the panel's own Create take is the
                terracotta action once it opens. */}
            <button
              type="button"
              className="btn"
              disabled={isRunning || !selectedTake}
              title={
                isRunning
                  ? "A job is already running"
                  : `Re-run ${label} and everything after it, in a new take`
              }
              onClick={() => {
                setRerollTarget(selected);
                setPanelOpen(true);
              }}
            >
              Run again
            </button>
          </div>
          {selectedDef?.sub && (
            <div className="subphase" aria-label="Script sub-phases">
              {selectedDef.sub.map(({ key, label: subLabel }) => {
                const status = statuses[key];
                return (
                  <div key={key} className="subphase__row">
                    <span className={`dot dot--${status}`} aria-hidden="true" />
                    <b>{subLabel}</b>
                    <span className="mut">{statusLabel(status)}</span>
                  </div>
                );
              })}
            </div>
          )}
          {(!selectedDef?.sub || selectedStatus === "failed") && (
            <ArtifactDetail
              phaseKey={selected}
              record={record}
              progress={selected === "render" && isCurrentTake ? progress : null}
              failure={selected === "render" ? failure : null}
              finished={selected === "render" && isCurrentTake ? finished : null}
              project={project}
              take={selectedTake}
              isCurrentTake={isCurrentTake}
              onStartRender={onStartRender}
              onCancel={onCancel}
              takeId={selectedTakeId}
              duration={formatDuration(record?.duration_ms)}
              cost={
                costTable
                  ? formatUsd(projectSpend({ [selected]: { status: "done" } }, costTable, undefined))
                  : null
              }
            />
          )}
        </div>
      </div>

      {panelOpen && selectedTake && (
        <NewTakePanel
          projectId={project.id}
          branchFromTake={selectedTake}
          initialRerollFrom={rerollTarget}
          onClose={() => {
            setPanelOpen(false);
            setRerollTarget(null);
          }}
          onCreated={handleTakeCreated}
        />
      )}
    </div>
  );
}

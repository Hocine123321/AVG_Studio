import { useEffect, useState } from "react";
import { apiGet, imageUrl, videoUrl } from "./api";
import type { PhaseRecord, PhaseStatus, ProjectDoc, ProjectImagesResponse } from "./api";
import type { PhaseFailedEvent, ProgressEvent, RenderCancelledEvent, RenderFinishedEvent } from "./useEvents";
import { IconAlert, IconArrowLeft, IconCheck, IconPlay, IconStop } from "./Icons";
import Player from "./Player";

/** Mirrors `avg.config.legacy_import.PHASE_ORDER`. Sentence case, per the
 * design rules, not the legacy app's title case. */
const PHASE_ORDER: { key: string; label: string }[] = [
  { key: "brief", label: "Brief" },
  { key: "script", label: "Script" },
  { key: "director1", label: "Director I" },
  { key: "visuals", label: "Visuals" },
  { key: "voice", label: "Voice & audio" },
  { key: "director2", label: "Director II" },
  { key: "captions", label: "Captions" },
  { key: "render", label: "Render" },
];

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

function statusLabel(status: PhaseStatus): string {
  if (status === "done") return "Done";
  if (status === "failed") return "Failed";
  if (status === "running") return "Running";
  return "Not started";
}

function statusChipClass(status: PhaseStatus): string {
  if (status === "done") return "chip chip--ok";
  if (status === "failed") return "chip chip--bad";
  if (status === "running") return "chip chip--amber";
  return "chip";
}

/** Which row the panel opens on: the failed one, else the running one, else
 * the last completed one, else the first. Unchanged. */
function defaultPhase(project: ProjectDoc): string {
  const failed = PHASE_ORDER.find(({ key }) => project.phases[key]?.status === "failed");
  if (failed) return failed.key;
  const running = PHASE_ORDER.find(({ key }) => project.phases[key]?.status === "running");
  if (running) return running.key;
  for (let i = PHASE_ORDER.length - 1; i >= 0; i--) {
    if (project.phases[PHASE_ORDER[i].key]?.status === "done") return PHASE_ORDER[i].key;
  }
  return PHASE_ORDER[0].key;
}

function ArtifactDetail({
  phaseKey,
  record,
  progress,
  failure,
  finished,
  project,
  onStartRender,
  onCancel,
}: {
  phaseKey: string;
  record: PhaseRecord | undefined;
  progress: ProgressEvent | null;
  failure: PhaseFailedEvent | null;
  finished: RenderFinishedEvent | null;
  project: ProjectDoc;
  onStartRender: () => void;
  onCancel: () => void;
}) {
  const status: PhaseStatus = record?.status ?? "pending";

  const [images, setImages] = useState<string[]>([]);
  useEffect(() => {
    if (phaseKey !== "visuals") return;
    let cancelled = false;
    apiGet<ProjectImagesResponse>(`/api/projects/${encodeURIComponent(project.id)}/images`)
      .then((res) => {
        if (!cancelled) setImages(res.images);
      })
      .catch(() => {
        if (!cancelled) setImages([]);
      });
    return () => {
      cancelled = true;
    };
  }, [phaseKey, project.id]);

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
          <button type="button" className="btn btn--primary" onClick={onStartRender}>
            Run again
          </button>
          {kind && <span className="chip chip--bad">{kind}</span>}
        </div>
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
            <button type="button" className="btn btn--primary" onClick={onStartRender}>
              <IconPlay />
              Run again
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (phaseKey === "visuals" && images.length > 0) {
    return (
      <div>
        <p className="lbl" style={{ marginBottom: 12 }}>
          {status === "running" && <span className="dot pulse" style={{ background: "var(--accent)" }} aria-hidden="true" />}
          {images.length} image{images.length === 1 ? "" : "s"}
        </p>
        <div className="shots">
          {images.map((name) => (
            <div key={name}>
              <div className="shot">
                <img className="washed" src={imageUrl(project.id, name)} alt="" loading="lazy" />
              </div>
              <div className="shot__caption">{name}</div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (status === "done") return <p className="artifact-empty">Done.</p>;
  if (status === "running") return <p className="artifact-empty">Running…</p>;
  return <p className="artifact-empty">Not started yet.</p>;
}

export interface StudioProps {
  project: ProjectDoc;
  progress: ProgressEvent | null;
  failure?: PhaseFailedEvent | null;
  cancelled?: RenderCancelledEvent | null;
  finished?: RenderFinishedEvent | null;
  startError?: string | null;
  onStartRender: () => void;
  onCancel: () => void;
  /** New: the back arrow in the header. The icon rail can't navigate out of
   * Studio (it's only reached from a card), so this is the way home. */
  onBack?: () => void;
}

/**
 * The screen the app exists for.
 *
 * The phase rail is now a SPINE: eight nodes threaded on one continuous
 * line, filling sage as they complete, with a pulsing terracotta halo on
 * whichever one is running. The previous build drew eight independent rows
 * with a coloured dot each — correct, but it read as a status list rather
 * than as a pipeline with a direction.
 */
export default function Studio({
  project,
  progress,
  failure = null,
  cancelled = null,
  finished = null,
  startError = null,
  onStartRender,
  onCancel,
  onBack,
}: StudioProps) {
  const [selected, setSelected] = useState<string>(() => defaultPhase(project));

  useEffect(() => {
    if (progress) setSelected("render");
  }, [progress]);

  const renderStatus: PhaseStatus = project.phases.render?.status ?? "pending";
  const isRunning = renderStatus === "running";
  const actionLabel = isRunning ? "Cancel" : renderStatus === "pending" ? "Start render" : "Run again";

  const record = project.phases[selected];
  const selectedStatus: PhaseStatus = record?.status ?? "pending";
  const label = PHASE_ORDER.find((p) => p.key === selected)?.label ?? selected;

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
        <button type="button" className="btn btn--primary" onClick={isRunning ? onCancel : onStartRender}>
          {isRunning ? <IconStop /> : <IconPlay />}
          {actionLabel}
        </button>
      </header>

      {startError && (
        <div className="studio__banner studio__banner--bad">
          <p>Couldn't start the render.</p>
          <p>{startError}</p>
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
            {PHASE_ORDER.map(({ key, label: rowLabel }) => {
              const rowRecord = project.phases[key];
              const status: PhaseStatus = rowRecord?.status ?? "pending";
              const live = key === "render" && progress;
              return (
                <button
                  key={key}
                  type="button"
                  className={`phase-row phase-row--${status}${selected === key ? " phase-row--selected" : ""}`}
                  onClick={() => setSelected(key)}
                  title={statusLabel(status)}
                >
                  <span className={`phase-row__node${status === "running" ? " halo" : ""}`} aria-hidden="true" />
                  <span className="phase-row__name">{rowLabel}</span>
                  <span className="phase-row__dur">
                    {live ? `${progress.percent}%` : formatDuration(rowRecord?.duration_ms)}
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
          </div>
          <ArtifactDetail
            phaseKey={selected}
            record={record}
            progress={selected === "render" ? progress : null}
            failure={selected === "render" ? failure : null}
            finished={selected === "render" ? finished : null}
            project={project}
            onStartRender={onStartRender}
            onCancel={onCancel}
          />
        </div>
      </div>
    </div>
  );
}

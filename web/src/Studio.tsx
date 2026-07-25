import { useEffect, useState } from "react";
import type { PhaseRecord, PhaseStatus, ProjectDoc } from "./api";
import type { PhaseFailedEvent, ProgressEvent, RenderCancelledEvent, RenderFinishedEvent } from "./useEvents";

/** New pipeline's phase order and display labels - mirrors
 * `avg.config.legacy_import.PHASE_ORDER` and the phase names from the
 * legacy app (see the project's CLAUDE.md: Brief -> Script -> Director I ->
 * Visuals -> Voice & Audio -> Director II -> Captions -> Render). Sentence
 * case per the design rules, not the legacy app's title case. */
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

/** `duration_ms` -> `m:ss`, e.g. 12000 -> "0:12". `null`/`undefined`/`NaN`
 * (every phase besides render, currently - the legacy importer never
 * populates `duration_ms` for the other seven) render as an em dash rather
 * than "NaN:NaN". */
function formatDuration(ms?: number | null): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  const totalSeconds = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function formatBytes(n?: number | null): string {
  if (!n || n <= 0) return "—";
  const mb = n / (1024 * 1024);
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} GB`;
  return `${mb.toFixed(1)} MB`;
}

function statusChipClass(status: PhaseStatus): string {
  if (status === "done") return "chip chip--ok";
  if (status === "failed") return "chip chip--bad";
  // "running" is the one place amber is allowed on this row: it IS the
  // phase happening right now.
  if (status === "running") return "chip chip--amber";
  return "chip";
}

function statusLabel(status: PhaseStatus): string {
  if (status === "done") return "Done";
  if (status === "failed") return "Failed";
  if (status === "running") return "Running";
  return "Pending";
}

/** Which row the panel opens on: the failed one, else the running one,
 * else the last completed one, else the first. Whatever the user would
 * actually want to look at first, rather than always defaulting to "Brief". */
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
}: {
  phaseKey: string;
  record: PhaseRecord | undefined;
  progress: ProgressEvent | null;
  failure: PhaseFailedEvent | null;
  finished: RenderFinishedEvent | null;
  project: ProjectDoc;
}) {
  const status: PhaseStatus = record?.status ?? "pending";

  // A running phase streams progress straight into the panel that will
  // later show what it produced - there is no separate progress screen.
  if (phaseKey === "render" && progress) {
    return (
      <div className="artifact-progress">
        <div className="artifact-progress__bar">
          <div className="artifact-progress__fill" style={{ width: `${progress.percent}%` }} />
        </div>
        <p className="mono">
          {progress.percent}% — {progress.message}
        </p>
      </div>
    );
  }

  if (status === "failed") {
    // The message is plain words, the detail is the raw mono-face text -
    // the promise of this design is you never go log-spelunking for either.
    const message = failure?.message ?? record?.error ?? "This phase failed.";
    const detail = failure?.detail ?? "";
    return (
      <div className="artifact-failed">
        <p>{message}</p>
        {detail && <p className="mono">{detail}</p>}
      </div>
    );
  }

  if (phaseKey === "render" && status === "done") {
    const output = finished?.output ?? project.master_output;
    const size = finished ? formatBytes(finished.size_bytes) : null;
    return (
      <div className="artifact-done">
        <p>Render finished.</p>
        {output && <p className="mono">{output}</p>}
        {size && <p className="mono">{size}</p>}
      </div>
    );
  }

  if (status === "done") {
    return <p className="artifact-empty">Done.</p>;
  }
  if (status === "running") {
    return <p className="artifact-empty">Running…</p>;
  }
  return <p className="artifact-empty">Not started yet.</p>;
}

export interface StudioProps {
  project: ProjectDoc;
  progress: ProgressEvent | null;
  failure?: PhaseFailedEvent | null;
  cancelled?: RenderCancelledEvent | null;
  finished?: RenderFinishedEvent | null;
  /** Set when this project's own `POST .../render` was refused (currently
   * only the 409 "something else is already rendering" case) - surfaced
   * honestly rather than swallowed. */
  startError?: string | null;
  onBack: () => void;
  onStartRender: () => void;
  onCancel: () => void;
}

/** The screen the app exists for: an eight-row phase rail (the spine) next
 * to an artifact panel showing whatever the selected phase produced. A
 * running phase is shown in the same view it's later inspected in - opening
 * Studio never lands on a separate "progress screen". */
export default function Studio({
  project,
  progress,
  failure = null,
  cancelled = null,
  finished = null,
  startError = null,
  onBack,
  onStartRender,
  onCancel,
}: StudioProps) {
  const [selected, setSelected] = useState<string>(() => defaultPhase(project));

  useEffect(() => {
    if (progress) setSelected("render");
  }, [progress]);

  const renderStatus: PhaseStatus = project.phases.render?.status ?? "pending";
  const isRunning = renderStatus === "running";
  const actionLabel = isRunning ? "Cancel" : renderStatus === "pending" ? "Start render" : "Run again";

  const record = project.phases[selected];
  const label = PHASE_ORDER.find((p) => p.key === selected)?.label ?? selected;

  return (
    <div className="studio">
      <div className="studio__header">
        <button type="button" className="btn" onClick={onBack}>
          Back
        </button>
        <h2 className="studio__title" dir="auto" title={project.title}>
          {project.title}
        </h2>
        <span className="chip mono">{project.aspect_ratio}</span>
        <button type="button" className="btn" onClick={isRunning ? onCancel : onStartRender}>
          {actionLabel}
        </button>
      </div>

      {startError && (
        <div className="studio__banner studio__banner--bad">
          <p>Couldn't start the render.</p>
          <p className="mono">{startError}</p>
        </div>
      )}

      {cancelled && !isRunning && !startError && (
        <div className="studio__banner studio__banner--muted">
          <p>Render cancelled.</p>
        </div>
      )}

      <div className="studio__body">
        <div className="phase-rail">
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
              >
                <span className="phase-row__top">
                  <span className="phase-row__name">{rowLabel}</span>
                  <span className={statusChipClass(status)}>{statusLabel(status)}</span>
                </span>
                <span className="phase-row__meta mono">
                  {live ? `${progress.percent}%` : formatDuration(rowRecord?.duration_ms)}
                </span>
              </button>
            );
          })}
        </div>

        <div className="artifact-panel">
          <h3 className="artifact-panel__title">{label}</h3>
          <ArtifactDetail
            phaseKey={selected}
            record={record}
            progress={selected === "render" ? progress : null}
            failure={selected === "render" ? failure : null}
            finished={selected === "render" ? finished : null}
            project={project}
          />
        </div>
      </div>
    </div>
  );
}

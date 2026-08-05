import { useEffect, useState } from "react";
import { getPhases, getTakeArtifacts, getTakeLog, type ApiPhaseDef, type TakeArtifacts, type TakeLogTail } from "../api";
import {
  BriefBody,
  CaptionsBody,
  Director1Body,
  Director2Body,
  ScriptBody,
  VisualsBody,
  VoiceBody,
} from "./bodies";

/**
 * The artifact panel's shell: one header strip, then the body for whichever
 * phase is selected.
 *
 * Which artifacts a phase owns lives on the backend
 * (`avg/core/artifacts.py`) and arrives through `GET .../artifacts`, so
 * this file never restates the file list - it only maps a phase key to the
 * component that knows how to render that phase's output.
 *
 * Render is deliberately absent: it already has a player, a summary and a
 * progress view in `Studio.tsx`, all of which predate this panel and none
 * of which are improved by being moved.
 */
const BODIES: Record<string, typeof BriefBody> = {
  brief: BriefBody,
  script: ScriptBody,
  director1: Director1Body,
  visuals: VisualsBody,
  voice: VoiceBody,
  director2: Director2Body,
  // Keyed by PHASE KEY, so this moved with the Slice 9 rename. The component
  // keeps its own name: it renders `captions.ass` and the timed cards, which
  // is what the file is still called.
  screen_text: CaptionsBody,
};

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const kb = n / 1024;
  if (kb < 1024) return `${kb.toFixed(0)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

export interface PhaseArtifactProps {
  projectId: string;
  takeId: number | null;
  phaseKey: string;
  /** Total duration for this phase, already formatted by Studio. */
  duration: string;
  /** Estimated cost for this phase, already formatted, or null when the
   * cost table has not loaded. */
  cost: string | null;
}

export default function PhaseArtifact({ projectId, takeId, phaseKey, duration, cost }: PhaseArtifactProps) {
  const [artifacts, setArtifacts] = useState<TakeArtifacts | null>(null);
  /** The take's run log, fetched only once asked for: it is per TAKE, not per
   * phase, so opening it on every row click would re-download the same file
   * eight times over. `null` means "not asked for yet", `[]` means "asked and
   * there is none" - a take that has not run yet 404s, which is a fact worth
   * showing rather than an error. */
  const [log, setLog] = useState<TakeLogTail | null>(null);
  const [logOpen, setLogOpen] = useState(false);
  const [phaseDefs, setPhaseDefs] = useState<ApiPhaseDef[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    getPhases()
      .then((r) => {
        if (!cancelled) setPhaseDefs(r.phases);
      })
      .catch(() => {
        if (!cancelled) setPhaseDefs([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (takeId === null) {
      setArtifacts(null);
      return;
    }
    let cancelled = false;
    getTakeArtifacts(projectId, takeId)
      .then((r) => {
        if (!cancelled) setArtifacts(r);
      })
      .catch(() => {
        if (!cancelled) setArtifacts({ files: {}, by_phase: {} });
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, takeId, phaseKey]);

  const Body = BODIES[phaseKey];
  if (takeId === null) return null;

  const definition = phaseDefs?.find((p) => p.key === phaseKey) ?? null;

  // An un-started phase has no artifacts, so every body renders "no X in
  // this take" and the panel collapses to nothing. Saying what the phase is
  // FOR is more use than an empty box - and it is the same information the
  // pipeline rail is already showing a grey dot for.
  const nothingYet = artifacts !== null && (artifacts.by_phase[phaseKey] ?? []).length === 0;
  if (nothingYet && definition) {
    return (
      <div className="artifact-stack">
        <p className="artifact-idle">{definition.description}</p>
      </div>
    );
  }

  if (!Body) return null;
  if (artifacts === null) return <p className="artifact-empty">Loading…</p>;

  const { files } = artifacts;
  // This phase's own artifacts, not the take's total: the strip describes
  // the row you are looking at.
  const own = (artifacts.by_phase[phaseKey] ?? []).map((key) => [key, files[key] ?? 0] as const);

  return (
    <div className="artifact-stack">
      <div className="artifact-strip">
        <span className="artifact-strip__item">
          Took <b>{duration}</b>
        </span>
        {cost && (
          <span className="artifact-strip__item">
            Cost <b className="mono">{cost}</b>
          </span>
        )}
        <span className="artifact-strip__item">
          <b>{own.length}</b> file{own.length === 1 ? "" : "s"}
        </span>
        <span className="home__spacer" />
        <span className="artifact-strip__item mono">
          {formatBytes(own.reduce((sum, [, size]) => sum + size, 0))}
        </span>
        {/* Unaccented: the filled terracotta action on this screen is
            Studio's own CTA, and a log toggle is not competing with it. */}
        <button
          type="button"
          className="btn btn--sm"
          onClick={() => {
            setLogOpen((open) => !open);
            if (log === null) {
              getTakeLog(projectId, takeId)
                .then(setLog)
                .catch(() => setLog({ lines: [], truncated: false, total: 0 }));
            }
          }}
        >
          {logOpen ? "Hide log" : "Log"}
        </button>
      </div>
      <Body projectId={projectId} takeId={takeId} files={files} />
      {logOpen &&
        (log === null ? (
          <p className="artifact-empty">Loading the log…</p>
        ) : log.lines.length === 0 ? (
          <p className="artifact-empty">No log for this take yet — nothing has run in it.</p>
        ) : (
          <div className="artifact-log">
            {log.truncated && (
              <p className="artifact-log__note">
                Showing the last {log.lines.length} of {log.total} lines.
              </p>
            )}
            <pre className="artifact-log__body">{log.lines.join("\n")}</pre>
          </div>
        ))}
    </div>
  );
}

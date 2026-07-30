import { useEffect, useState } from "react";
import { getTakeArtifacts, type TakeArtifacts } from "../api";
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
  if (!Body || takeId === null) return null;
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
      </div>
      <Body projectId={projectId} takeId={takeId} files={files} />
    </div>
  );
}

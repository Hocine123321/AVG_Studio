import { useCallback, useEffect, useState } from "react";
import { getHealthReport, type HealthReport } from "./api";
import ErrorLog from "./ErrorLog";
import { IconChevron } from "./Icons";
import ModelCacheCard from "./ModelCacheCard";

/**
 * The App Health screen: the legacy app's startup diagnostic, rebuilt.
 *
 * Three stages in order - Dependency check, App warmup, Safeguards - because
 * each assumes the one before it passed, and when a launch goes wrong
 * "which stage failed" is the whole diagnosis. It runs on mount, so opening
 * the page IS the check; Re-run everything pays for the ffmpeg subprocess
 * again rather than reading the five-minute cache.
 *
 * A failed check is never an error state for the screen - it is the content.
 * The whole point is to show a broken FFmpeg or an unwritable library folder
 * clearly, before a render finds them the expensive way.
 *
 * A fourth stage, Deep Check, is opt-in: it does the thing rather than
 * merely describing it (a real settings and DPAPI round-trip, a real
 * one-second ffmpeg encode), which costs real disk and a subprocess, so it
 * never runs on mount - only Deep check requests it.
 *
 * One filled terracotta action here: Re-run everything. Deep check is a
 * plain button beside it, not a second primary.
 */
export default function AppHealth() {
  const [report, setReport] = useState<HealthReport | null>(null);
  // WHICH action is running, not just whether one is - a single shared
  // `busy` flag was the Slice 12 review's Minor: pressing "Re-run
  // everything" flipped the OTHER button's label to "Checking…" too,
  // implying a Deep Check was running when it never started. Both buttons
  // still disable together (they hit the same in-flight request), but each
  // only claims its own label.
  const [busyAction, setBusyAction] = useState<"rerun" | "deep" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (refresh: boolean, deep = false) => {
    setBusyAction(deep ? "deep" : "rerun");
    setError(null);
    try {
      setReport(await getHealthReport(refresh, deep));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAction(null);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  const failing = report?.stages.filter((s) => !s.ok) ?? [];

  // Passing stages start collapsed - "Passed" in the header chip is
  // usually the whole answer, which is the entire reason this exists
  // (the sections "currently consume too much space"). Failing stages
  // start open - a failed check is this screen's content, not an error
  // state to click past. A user's own toggle, once made, is never
  // overridden by a later report unless it's a stage key seen for the
  // first time.
  const [openStages, setOpenStages] = useState<Record<string, boolean>>({});
  useEffect(() => {
    if (!report) return;
    setOpenStages((prev) => {
      const next = { ...prev };
      for (const stage of report.stages) {
        if (!(stage.key in next)) next[stage.key] = !stage.ok;
      }
      return next;
    });
  }, [report]);
  const toggleStage = (key: string) => setOpenStages((prev) => ({ ...prev, [key]: !prev[key] }));

  return (
    <div className="settings">
      <header className="settings__header">
        <div>
          <div className="home__kicker">Diagnostics</div>
          <h1 className="home__title">App health</h1>
        </div>
        <span className="home__spacer" />
        {report && (
          <span className={report.ok ? "chip chip--ok" : "chip chip--bad"}>
            {report.ok ? "All clear" : `${failing.length} stage${failing.length === 1 ? "" : "s"} need attention`}
          </span>
        )}
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => void load(true)}
          disabled={busyAction !== null}
        >
          {busyAction === "rerun" ? "Running…" : "Re-run everything"}
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => void load(true, true)}
          disabled={busyAction !== null}
        >
          {busyAction === "deep" ? "Checking…" : "Deep check"}
        </button>
      </header>

      {error && <p className="settings__save-error">Could not run the checks: {error}</p>}

      {!report && !error && <p className="home__empty">Running checks…</p>}

      <div className="health-stages">
        {report?.stages.map((stage, index) => {
          const isOpen = openStages[stage.key] ?? !stage.ok;
          return (
            <section key={stage.key} className={`health-stage${stage.ok ? "" : " health-stage--bad"}`}>
              <button
                type="button"
                className="health-stage__head"
                onClick={() => toggleStage(stage.key)}
                aria-expanded={isOpen}
              >
                {/* The step number carries the ordering the stages actually
                    have - this is a pipeline, not three independent panels. */}
                <span className="health-stage__num" aria-hidden="true">
                  {index + 1}
                </span>
                <div style={{ minWidth: 0 }}>
                  {/* `<button>`'s content model is phrasing content only -
                      `<h2>`/`<p>` (both flow/block-level, not phrasing)
                      inside one is invalid HTML regardless of how browsers
                      happen to render it. `<span>` with `display: block`
                      (tokens.css) keeps the exact same visual stacking
                      with none of that. */}
                  <span className="health-stage__title" role="heading" aria-level={2}>
                    {stage.name}
                  </span>
                  <span className="health-stage__detail">{stage.detail}</span>
                </div>
                <span className="home__spacer" />
                <span className={stage.ok ? "chip chip--ok" : "chip chip--bad"}>
                  {stage.ok ? "Passed" : "Failed"}
                </span>
                <span className={`health-stage__chevron${isOpen ? " is-open" : ""}`}>
                  <IconChevron />
                </span>
              </button>

              <ul
                className={`health-checks${isOpen ? "" : " health-checks--collapsed"}`}
                aria-hidden={!isOpen}
              >
                {stage.checks.map((check) => (
                  <li key={check.name} className="health-check">
                    <span className={`health-check__dot${check.ok ? " health-check__dot--ok" : " health-check__dot--bad"}`} aria-hidden="true" />
                    <span className="health-check__name">{check.name}</span>
                    {check.required === false && (
                      <span className="health-check__optional">Optional</span>
                    )}
                    {check.version && <span className="health-check__version mono">{check.version}</span>}
                    <span className="health-check__detail" title={check.detail}>
                      {check.detail}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </div>

      <ModelCacheCard />

      <ErrorLog />
    </div>
  );
}

import { useCallback, useEffect, useState } from "react";
import { getHealthReport, type HealthReport } from "./api";
import ErrorLog from "./ErrorLog";

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
 * One filled terracotta action here: Re-run everything.
 */
export default function AppHealth() {
  const [report, setReport] = useState<HealthReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (refresh: boolean) => {
    setBusy(true);
    setError(null);
    try {
      setReport(await getHealthReport(refresh));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  const failing = report?.stages.filter((s) => !s.ok) ?? [];

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
        <button type="button" className="btn btn--primary" onClick={() => void load(true)} disabled={busy}>
          {busy ? "Running…" : "Re-run everything"}
        </button>
      </header>

      {error && <p className="settings__save-error">Could not run the checks: {error}</p>}

      {!report && !error && <p className="home__empty">Running checks…</p>}

      <div className="health-stages">
        {report?.stages.map((stage, index) => (
          <section key={stage.key} className={`health-stage${stage.ok ? "" : " health-stage--bad"}`}>
            <header className="health-stage__head">
              {/* The step number carries the ordering the stages actually
                  have - this is a pipeline, not three independent panels. */}
              <span className="health-stage__num" aria-hidden="true">
                {index + 1}
              </span>
              <div style={{ minWidth: 0 }}>
                <h2 className="health-stage__title">{stage.name}</h2>
                <p className="health-stage__detail">{stage.detail}</p>
              </div>
              <span className="home__spacer" />
              <span className={stage.ok ? "chip chip--ok" : "chip chip--bad"}>
                {stage.ok ? "Passed" : "Failed"}
              </span>
            </header>

            <ul className="health-checks">
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
        ))}
      </div>

      <ErrorLog />
    </div>
  );
}

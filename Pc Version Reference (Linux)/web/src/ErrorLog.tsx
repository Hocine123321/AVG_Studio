import { useCallback, useEffect, useState } from "react";
import { clearErrors, getErrors, openLogsFolder, type AppError } from "./api";

const SEVERITY_LABEL: Record<AppError["severity"], string> = {
  critical: "Critical",
  needs_review: "Needs review",
  low: "Low",
};

const SEVERITY_CLASS: Record<AppError["severity"], string> = {
  critical: "chip chip--bad",
  needs_review: "chip chip--amber",
  low: "chip",
};

/**
 * Turns one recorded error into a paste an assistant can act on with no
 * further back-and-forth.
 *
 * The prompt is part of the payload on purpose. A bare traceback makes the
 * assistant ask which app, which version, what the user was doing - and
 * every one of those round trips is the delay this feature exists to
 * remove. The detail is already redacted server-side, so it is safe to
 * put on the clipboard as-is.
 */
/** The same formatting `promptFor` has always used for the clipboard
 * prompt's Context section - reused by the on-screen expansion (Slice 23)
 * so an error with no `detail` (every `low`-severity degradation - see
 * `avg.core.error_store.ErrorStore.degraded`) still shows the one thing
 * this app already knows about it, instead of nothing. */
function contextText(error: AppError): string {
  return Object.entries(error.context)
    .map(([key, value]) => `- ${key}: ${value}`)
    .join("\n");
}

/** What a click on a row reveals: real detail (a traceback, most often)
 * if there is one, otherwise the same context `promptFor` already puts on
 * the clipboard, otherwise a plain admission there's nothing further -
 * never nothing at all, which read as the click doing nothing. */
function expandedContent(error: AppError): string {
  return error.detail || contextText(error) || "No further detail recorded for this error.";
}

function promptFor(error: AppError): string {
  const context = contextText(error);

  return [
    "I hit an error in AVG Studio (Python + FastAPI backend, React 19 in a",
    "WebView2 window, Windows desktop app). Please find the cause and propose a fix.",
    "",
    `Severity: ${SEVERITY_LABEL[error.severity]}`,
    `Where: ${error.source}`,
    `When: ${error.when}`,
    `Summary: ${error.summary}`,
    context ? `\nContext:\n${context}` : "",
    error.detail ? `\nDetail:\n\`\`\`\n${error.detail}\n\`\`\`` : "",
  ]
    .filter(Boolean)
    .join("\n");
}

/**
 * The latest failures, newest first, at the bottom of App Health.
 *
 * Every row carries its severity and a Copy button that puts a
 * ready-to-send prompt on the clipboard. Deliberately not a link to a log
 * file: the user should never have to find a file to report a bug, which
 * is exactly what the packaged build used to require - it has no console,
 * so a traceback went nowhere at all.
 */
export default function ErrorLog() {
  const [errors, setErrors] = useState<AppError[] | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  /** Which severities to show. Degradations are recorded at `low` (Slice 9)
   * - a fallback that rescued a run is not a failure, but it IS why a video
   * came out different from the settings - and this is what keeps them from
   * crowding out the things that actually broke. */
  const [showLow, setShowLow] = useState(true);

  const load = useCallback(() => {
    getErrors()
      .then((res) => setErrors(res.errors))
      .catch(() => setErrors([]));
  }, []);

  useEffect(load, [load]);

  async function copy(error: AppError) {
    try {
      await navigator.clipboard.writeText(promptFor(error));
      setCopied(error.id);
      setTimeout(() => setCopied(null), 2000);
    } catch {
      // The clipboard can be refused. Stay silent rather than raise an
      // error about the error reporter.
    }
  }

  const lowCount = (errors ?? []).filter((error) => error.severity === "low").length;
  const visible = (errors ?? []).filter((error) => showLow || error.severity !== "low");

  return (
    <section className="errorlog" aria-label="Recent errors">
      <header className="errorlog__head">
        <div style={{ minWidth: 0 }}>
          <h2 className="health-stage__title">Recent errors</h2>
          <p className="health-stage__detail">
            Newest first. Copy sends the whole thing, with a prompt, ready to paste.
          </p>
        </div>
        <span className="home__spacer" />
        {lowCount > 0 && (
          <button
            type="button"
            // `.btn--on` is the app's existing pressed-toggle look (Home's
            // Archived pill uses it); a new class for the same gesture is
            // the quiet kind of inconsistency tokens.css warns about.
            className={`btn btn--sm${showLow ? " btn--on" : ""}`}
            aria-pressed={showLow}
            onClick={() => setShowLow((on) => !on)}
            title="Recorded degradations: a run that worked, worse than asked"
          >
            {lowCount} minor
          </button>
        )}
        <button type="button" className="btn btn--sm" onClick={() => void openLogsFolder()}>
          Logs
        </button>
        <button
          type="button"
          className="btn"
          disabled={!errors || errors.length === 0}
          onClick={() => void clearErrors().then(load)}
        >
          Clear
        </button>
      </header>

      {errors !== null && errors.length === 0 && (
        <p className="home__empty">Nothing has gone wrong yet.</p>
      )}

      <ul className="errorlog__list">
        {visible.map((error) => (
          <li key={error.id} className="errorlog__row">
            <span className={SEVERITY_CLASS[error.severity]}>{SEVERITY_LABEL[error.severity]}</span>
            <button
              type="button"
              className="errorlog__summary"
              onClick={() => setExpanded(expanded === error.id ? null : error.id)}
              title={error.detail || Object.keys(error.context).length > 0 ? "Show the full detail" : error.summary}
            >
              {error.summary}
            </button>
            <span className="errorlog__meta mono">{new Date(error.when).toLocaleString()}</span>
            {error.context.phase && (
              <span className="errorlog__meta">
                {error.context.phase}
                {error.context.take_id ? ` · take ${error.context.take_id}` : ""}
              </span>
            )}
            <button type="button" className="btn btn--sm" onClick={() => void copy(error)}>
              {copied === error.id ? "Copied" : "Copy"}
            </button>
            {expanded === error.id && (
              <pre className="errorlog__detail">{expandedContent(error)}</pre>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

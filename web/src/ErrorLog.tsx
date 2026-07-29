import { useCallback, useEffect, useState } from "react";
import { clearErrors, getErrors, type AppError } from "./api";

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
function promptFor(error: AppError): string {
  const context = Object.entries(error.context)
    .map(([key, value]) => `- ${key}: ${value}`)
    .join("\n");

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
        {(errors ?? []).map((error) => (
          <li key={error.id} className="errorlog__row">
            <span className={SEVERITY_CLASS[error.severity]}>{SEVERITY_LABEL[error.severity]}</span>
            <button
              type="button"
              className="errorlog__summary"
              onClick={() => setExpanded(expanded === error.id ? null : error.id)}
              title={error.detail ? "Show the full detail" : error.summary}
            >
              {error.summary}
            </button>
            <span className="errorlog__meta mono">{new Date(error.when).toLocaleString()}</span>
            <button type="button" className="btn btn--sm" onClick={() => void copy(error)}>
              {copied === error.id ? "Copied" : "Copy"}
            </button>
            {expanded === error.id && error.detail && (
              <pre className="errorlog__detail">{error.detail}</pre>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

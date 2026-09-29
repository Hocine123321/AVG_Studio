import { useEffect, useState } from "react";
import { clearErrors, getErrors, type AppError } from "./api";

const SEVERITY_LABEL: Record<AppError["severity"], string> = {
  critical: "Critical",
  needs_review: "Needs review",
  low: "Low",
};

/**
 * The Quick panel's Recent Errors tab (spec §3.4) - purely additive: the
 * backend route already does exactly this (`avg/api/routes_settings.py`'s
 * `list_errors`/`clear_errors`), so this component is just a newest-first
 * list with `detail` behind a disclosure, nothing more.
 */
export default function RecentErrorsTab() {
  const [errors, setErrors] = useState<AppError[] | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  useEffect(() => {
    void getErrors().then((r) => setErrors(r.errors));
  }, []);

  async function handleClear() {
    await clearErrors();
    setErrors([]);
  }

  if (errors === null) return <p className="home__empty">Loading…</p>;
  if (errors.length === 0) return <p className="home__empty">No recent failures.</p>;

  return (
    <div className="quickpanel-errors">
      <button type="button" className="btn btn--sm" onClick={() => void handleClear()}>
        Clear
      </button>
      <ul className="quickpanel-errors__list">
        {errors.map((e) => (
          <li key={e.id} className="quickpanel-errors__row">
            <button
              type="button"
              className="quickpanel-errors__summary"
              aria-expanded={openId === e.id}
              onClick={() => setOpenId(openId === e.id ? null : e.id)}
            >
              <span
                className={`chip chip--${e.severity === "critical" ? "bad" : e.severity === "low" ? "" : "amber"}`}
              >
                {SEVERITY_LABEL[e.severity]}
              </span>
              <span className="quickpanel-errors__source">{e.source}</span>
              <span className="quickpanel-errors__text">{e.summary}</span>
              <span className="quickpanel-errors__time">{new Date(e.when).toLocaleTimeString()}</span>
            </button>
            {openId === e.id && e.detail && <pre className="quickpanel-errors__detail">{e.detail}</pre>}
          </li>
        ))}
      </ul>
    </div>
  );
}

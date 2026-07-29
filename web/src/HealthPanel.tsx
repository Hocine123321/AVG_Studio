import { useCallback, useEffect, useState } from "react";
import { getHealth, type HealthResponse } from "./api";

/**
 * The Components table: is every render dependency actually here, and does
 * it run. The old PowerShell app's Check Health, rebuilt as a table.
 *
 * Worth its place on this screen because every dependency failure in this
 * project's history was discovered by a video failing partway through - an
 * ffmpeg missing from a stripped PATH would show up here first, before a
 * run started and before any money was spent.
 *
 * Recheck skips the cache (`avg/core/health.py` holds results for five
 * minutes, since the ffmpeg check is a real subprocess). It is a plain
 * button, not the accented one: this screen's single terracotta action is
 * "Save changes" in the save bar.
 */
export default function HealthPanel() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (refresh: boolean) => {
    setBusy(true);
    setError(null);
    try {
      setHealth(await getHealth(refresh));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  return (
    <section className="models-panel" aria-label="Components">
      <div className="spend-card">
        <div>
          <p className="lbl">Components</p>
          <b className={health && !health.ok ? "health--bad" : undefined}>
            {health ? (health.ok ? "All present" : "Needs attention") : "—"}
          </b>
        </div>
        <span className="spend-card__note">
          Checked on this machine. FFmpeg is run, not just looked for — a file that exists can still
          fail to start.
        </span>
        <button type="button" className="btn" onClick={() => void load(true)} disabled={busy}>
          {busy ? "Checking…" : "Recheck"}
        </button>
      </div>

      {error && <p className="settings__save-error">Could not check components: {error}</p>}

      {health && (
        <div className="models-scroll">
          <table className="models">
            <thead>
              <tr>
                <th>Component</th>
                <th>State</th>
                <th>Version</th>
              </tr>
            </thead>
            <tbody>
              {health.components.map((component) => (
                <tr key={component.name}>
                  <td>{component.name}</td>
                  <td>
                    <span className={component.ok ? "chip chip--ok" : "chip chip--bad"}>
                      {component.ok ? "Present" : "Missing"}
                    </span>
                  </td>
                  {/* No Detail column here, per the 3rd redesign: this is
                      the at-a-glance summary and the full reason for a
                      failure lives on the App Health screen. The detail is
                      still reachable as the row's tooltip. */}
                  <td className="mono" title={component.detail}>
                    {component.version || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

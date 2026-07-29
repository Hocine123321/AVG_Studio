import { useCallback, useEffect, useRef, useState } from "react";
import { buildModelCache, getModelCache, type ModelCacheResponse } from "./api";

/**
 * Build and report the working-models cache, on App Health.
 *
 * Probing is one generation call per model on every keyed provider, so it
 * is a deliberate action with a visible cost, not something that happens on
 * a screen load. The button is plain, not terracotta: App Health already
 * spends its one filled action on "Re-run everything".
 *
 * The count per provider is what makes the cache legible - "groq · 4
 * verified" answers the question the model pickers raise ("why is this list
 * short?") without opening a file.
 */
const POLL_MS = 3000;

export default function ModelCacheCard() {
  const [cache, setCache] = useState<ModelCacheResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      setCache(await getModelCache());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Polled while a sweep runs, and only then: the build takes tens of
  // seconds on a real account and has no event of its own to listen for.
  useEffect(() => {
    if (!cache?.building) return;
    timer.current = window.setTimeout(() => void load(), POLL_MS);
    return () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, [cache, load]);

  async function build() {
    setError(null);
    try {
      await buildModelCache();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const providers = Object.entries(cache?.providers ?? {});
  const building = Boolean(cache?.building);

  return (
    <section className="errorlog" aria-label="Model cache">
      <header className="errorlog__head">
        <div>
          <h2 className="health-stage__title">Model cache</h2>
          <p className="health-stage__detail">
            {cache?.updated
              ? `Last checked ${new Date(cache.updated).toLocaleString()}.`
              : "Never checked. Models are typed by hand until this runs."}
          </p>
        </div>
        <span className="home__spacer" />
        <button type="button" className="btn" onClick={() => void build()} disabled={building}>
          {building ? "Probing…" : "Build model cache"}
        </button>
      </header>

      {error && <p className="settings__save-error">Could not build the cache: {error}</p>}

      <ul className="errorlog__list">
        {providers.map(([provider, models]) => (
          <li key={provider} className="errorlog__row">
            <span className={models.length > 0 ? "chip chip--ok" : "chip"}>{provider}</span>
            <span className="errorlog__summary" style={{ cursor: "default" }}>
              {models.length > 0 ? models.join(", ") : "nothing answered"}
            </span>
            <span className="errorlog__meta mono">{models.length} verified</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

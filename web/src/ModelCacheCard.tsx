import { useCallback, useEffect, useRef, useState } from "react";
import { buildModelCache, getModelCache, type ModelCacheResponse } from "./api";

/**
 * Test Providers, on App Health.
 *
 * What it runs, and what each result is allowed to claim:
 *
 *   - **Working** — a real minimal completion succeeded on this text model.
 *     Text only; it is the one verdict that costs anything, and only ticked
 *     models are ever tested.
 *   - **Reachable** — the provider answered its own catalogue with this key.
 *     THIS MODEL is unproven. Image and voice providers get this and nothing
 *     more: an image probe is a paid image and an ElevenLabs probe is paid
 *     characters, which is why Slice 8 made them listed rather than probed.
 *   - **Untested** — never checked. The absence of a verdict, not a verdict.
 *
 * The button is plain, not terracotta: App Health already spends its one
 * filled action on "Re-run everything".
 */
const POLL_MS = 3000;

interface Row {
  key: string;
  label: string;
  verdict: "working" | "failed" | "reachable" | "unreachable";
  detail: string;
}

function chipFor(verdict: Row["verdict"]): string {
  if (verdict === "working" || verdict === "reachable") return "chip chip--ok";
  return "chip chip--bad";
}

const VERDICT_LABEL: Record<Row["verdict"], string> = {
  working: "Working",
  failed: "Failed",
  reachable: "Reachable",
  unreachable: "Unreachable",
};

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

  // Polled while a test runs, and only then: it takes tens of seconds on a
  // real account and has no event of its own to listen for.
  useEffect(() => {
    if (!cache?.building) return;
    timer.current = window.setTimeout(() => void load(), POLL_MS);
    return () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, [cache, load]);

  async function run() {
    setError(null);
    try {
      await buildModelCache();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const rows: Row[] = [];
  for (const [provider, models] of Object.entries(cache?.text ?? {})) {
    for (const [model, ok] of Object.entries(models)) {
      rows.push({
        key: `text:${provider}/${model}`,
        label: `${provider}/${model}`,
        verdict: ok ? "working" : "failed",
        detail: ok ? "a real completion succeeded" : "the completion did not answer",
      });
    }
  }
  for (const [key, ok] of Object.entries(cache?.reachable ?? {})) {
    rows.push({
      key: `reach:${key}`,
      label: key,
      verdict: ok ? "reachable" : "unreachable",
      // Stated, never implied: this is the whole reason the two words are
      // different. Nothing was generated and nothing was synthesised.
      detail: ok
        ? "the provider answered its catalogue — this entry itself is unproven"
        : "the provider's catalogue did not answer",
    });
  }
  const building = Boolean(cache?.building);

  return (
    <section className="errorlog" aria-label="Test providers">
      <header className="errorlog__head">
        <div>
          <h2 className="health-stage__title">Test providers</h2>
          <p className="health-stage__detail">
            {cache?.updated
              ? `Last tested ${new Date(cache.updated).toLocaleString()}.`
              : "Untested. Nothing here has been checked yet."}{" "}
            Ticked text models get a real completion; image and voice providers get their own
            catalogue only — nothing that costs money is ever generated.
          </p>
        </div>
        <span className="home__spacer" />
        <button type="button" className="btn" onClick={() => void run()} disabled={building}>
          {building ? "Testing…" : cache?.updated ? "Test again" : "Test providers"}
        </button>
      </header>

      {error && <p className="settings__save-error">Could not test the providers: {error}</p>}

      {rows.length === 0 && !building && (
        <p className="home__empty">
          Untested. Tick the models and voices you want on the Live List, then press Test
          providers.
        </p>
      )}

      <ul className="errorlog__list">
        {rows.map((row) => (
          <li key={row.key} className="errorlog__row">
            <span className={chipFor(row.verdict)}>{VERDICT_LABEL[row.verdict]}</span>
            <span className="errorlog__summary mono" style={{ cursor: "default" }}>
              {row.label}
            </span>
            <span className="errorlog__meta">{row.detail}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

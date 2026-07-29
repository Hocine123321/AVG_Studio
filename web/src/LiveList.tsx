import { useCallback, useEffect, useMemo, useState } from "react";
import {
  addLiveEntry,
  getLiveList,
  patchLiveEntry,
  refreshLiveList,
  removeLiveEntry,
  type LiveEntry,
  type LiveKind,
  type LiveListResponse,
} from "./api";
import Select from "./Select";
import { DeferredInput } from "./fields/DeferredInput";
import { IconCheck, IconPlus } from "./Icons";

/**
 * The Live List: which models and voices the settings pickers are allowed
 * to offer, which are favourites, and what the user thinks of each.
 *
 * The legacy app's live-list idea, rebuilt. Three tabs because the three
 * kinds come from different places and are chosen in different places -
 * text models from a real probe, image models and voices from what the
 * provider advertises (probing those costs money, so the app does not).
 *
 * A note is the feature that makes the rest worth having: it shows up as
 * the field's help text at the moment the model is picked, so "warm, good
 * for narration" is in front of you when you choose, not in a file
 * somewhere.
 *
 * One filled terracotta action on this screen: Refresh catalogues.
 */
const TABS: { kind: LiveKind; label: string; hint: string }[] = [
  { kind: "text", label: "Text models", hint: "Verified by the model-cache probe on App Health." },
  { kind: "image", label: "Image models", hint: "Listed from each provider — image generation is never used to test one." },
  { kind: "voice", label: "Voices", hint: "Listed from Edge and ElevenLabs. Listing costs nothing; synthesising would." },
];

const SOURCE_LABEL: Record<LiveEntry["source"], string> = {
  probed: "Verified",
  catalogue: "Listed",
  manual: "Added by you",
};

function sourceClass(source: LiveEntry["source"]): string {
  if (source === "probed") return "chip chip--ok";
  if (source === "manual") return "chip chip--amber";
  return "chip";
}

export default function LiveList({ onBack }: { onBack: () => void }) {
  const [data, setData] = useState<LiveListResponse | null>(null);
  const [kind, setKind] = useState<LiveKind>("text");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState("");
  // Voice-only filters. Edge alone advertises several hundred voices, so
  // the shortlist is the point of this screen rather than a limitation.
  const [locale, setLocale] = useState("all");
  const [gender, setGender] = useState("all");
  const [onlySelected, setOnlySelected] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await getLiveList());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function refresh() {
    setBusy(true);
    setError(null);
    try {
      await refreshLiveList();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  /** Optimistic: the row reflects the click immediately and reloads
   * underneath. A tick that waits for a round trip feels broken. */
  async function patch(entry: LiveEntry, body: { selected?: boolean; favourite?: boolean; note?: string }) {
    setData((prev) =>
      prev
        ? { ...prev, [kind]: prev[kind].map((e) => (e.id === entry.id ? { ...e, ...body } : e)) }
        : prev,
    );
    try {
      await patchLiveEntry(kind, entry.id, body);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      await load();
    }
  }

  async function add() {
    const id = adding.trim();
    if (!id) return;
    try {
      await addLiveEntry(kind, id);
      setAdding("");
      await load();
    } catch (err) {
      setError(extractDetail(err instanceof Error ? err.message : String(err)));
    }
  }

  async function remove(entry: LiveEntry) {
    try {
      await removeLiveEntry(kind, entry.id);
      await load();
    } catch (err) {
      setError(extractDetail(err instanceof Error ? err.message : String(err)));
    }
  }

  const rows = data?.[kind] ?? [];
  const meta = data?.voice_meta ?? {};

  // Derived from `data` alone: `meta` is a fresh object every render (it
  // falls back to `{}`), so depending on it would defeat the memo.
  const locales = useMemo(() => {
    const found = new Set<string>();
    for (const row of data?.voice ?? []) {
      const l = data?.voice_meta?.[row.id]?.locale;
      if (l) found.add(l);
    }
    return ["all", ...[...found].sort()];
  }, [data]);

  const visible = rows.filter((row) => {
    if (onlySelected && !row.selected) return false;
    if (kind !== "voice") return true;
    const info = meta[row.id];
    if (locale !== "all" && info?.locale !== locale) return false;
    if (gender !== "all" && (info?.gender ?? "").toLowerCase() !== gender) return false;
    return true;
  });

  const selectedCount = rows.filter((r) => r.selected).length;

  return (
    <div className="settings">
      <header className="settings__header">
        <div>
          <div className="home__kicker">Diagnostics</div>
          <h1 className="home__title">Live List</h1>
        </div>
        <span className="home__spacer" />
        {data && (
          <span className="chip" title="A channel can keep its own shortlist instead of the app-wide one.">
            {data.scope === "channel" ? "This channel’s own list" : "App-wide list"}
          </span>
        )}
        <button type="button" className="btn" onClick={onBack}>
          Back to App health
        </button>
        <button type="button" className="btn btn--primary" onClick={() => void refresh()} disabled={busy}>
          {busy ? "Listing…" : "Refresh catalogues"}
        </button>
      </header>

      <p className="channels__note">
        Only ticked entries are offered when you pick a model in Settings. Favourites come first, and
        your note shows under the field as you choose.
      </p>

      {error && <p className="settings__save-error">{error}</p>}

      <div className="livelist__tabs" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab.kind}
            type="button"
            role="tab"
            aria-selected={kind === tab.kind}
            className={`livelist__tab${kind === tab.kind ? " livelist__tab--on" : ""}`}
            onClick={() => setKind(tab.kind)}
          >
            {tab.label}
            <span className="livelist__tab-count">{(data?.[tab.kind] ?? []).length}</span>
          </button>
        ))}
      </div>

      <p className="health-stage__detail">{TABS.find((t) => t.kind === kind)?.hint}</p>

      <div className="livelist__controls">
        {/* The app's own switch and Select, never the native controls:
            Windows draws `<select>` and `<input type=checkbox>` itself, in
            OS grey with square corners, and they were the only two things
            on this screen ignoring the theme. `Select.tsx` exists for
            exactly this reason. */}
        <div className="livelist__filter">
          <button
            type="button"
            role="switch"
            aria-checked={onlySelected}
            aria-label="Show ticked entries only"
            className={`switch${onlySelected ? " switch--on" : ""}`}
            onClick={() => setOnlySelected((on) => !on)}
          >
            <span className="switch__thumb" aria-hidden="true" />
          </button>
          Ticked only ({selectedCount})
        </div>

        {kind === "voice" && (
          <>
            <div className="livelist__filter">
              Language
              <Select
                value={locale}
                ariaLabel="Filter by language"
                options={locales.map((l) => ({ value: l, label: l === "all" ? "Any" : l }))}
                onChange={setLocale}
              />
            </div>
            <div className="livelist__filter">
              Gender
              <Select
                value={gender}
                ariaLabel="Filter by gender"
                options={[
                  { value: "all", label: "Any" },
                  { value: "female", label: "Female" },
                  { value: "male", label: "Male" },
                ]}
                onChange={setGender}
              />
            </div>
          </>
        )}

        <span className="home__spacer" />

        <div className="livelist__add">
          <input
            value={adding}
            placeholder="provider/model — add one by hand"
            onChange={(e) => setAdding(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void add();
            }}
          />
          <button type="button" className="btn btn--sm" onClick={() => void add()} disabled={!adding.trim()}>
            <IconPlus size={14} />
            Add
          </button>
        </div>
      </div>

      {data === null && <p className="home__empty">Loading…</p>}

      {data !== null && visible.length === 0 && (
        <p className="home__empty">
          {rows.length === 0
            ? kind === "text"
              ? "No verified text models yet — build the model cache on App health."
              : "Nothing listed yet — press Refresh catalogues."
            : "Nothing matches these filters."}
        </p>
      )}

      <ul className="livelist__rows">
        {visible.map((entry) => {
          const info = meta[entry.id];
          return (
            <li key={entry.id} className={`livelist__row${entry.selected ? " livelist__row--on" : ""}`}>
              <button
                type="button"
                className={`livelist__tick${entry.selected ? " livelist__tick--on" : ""}`}
                aria-pressed={entry.selected}
                title={entry.selected ? "Offered in Settings" : "Not offered in Settings"}
                onClick={() => void patch(entry, { selected: !entry.selected })}
              >
                {entry.selected && <IconCheck size={13} />}
              </button>

              <button
                type="button"
                className={`livelist__star${entry.favourite ? " livelist__star--on" : ""}`}
                aria-pressed={entry.favourite}
                title="Favourites are offered first"
                onClick={() => void patch(entry, { favourite: !entry.favourite })}
              >
                ★
              </button>

              <div className="livelist__id">
                <span className="mono">{entry.id}</span>
                {info && (info.locale || info.gender) && (
                  <span className="livelist__meta">
                    {[info.locale, info.gender].filter(Boolean).join(" · ")}
                  </span>
                )}
              </div>

              <span className={sourceClass(entry.source)}>{SOURCE_LABEL[entry.source]}</span>

              <div className="livelist__note">
                <DeferredInput
                  value={entry.note}
                  placeholder="Note — shown under the field when you pick this"
                  onCommit={(raw) => void patch(entry, { note: raw })}
                />
              </div>

              {entry.source === "manual" && (
                <button
                  type="button"
                  className="btn btn--sm"
                  title="Remove this hand-added entry"
                  onClick={() => void remove(entry)}
                >
                  Remove
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** The server's own refusal sentence, not the "PATCH … failed: 400 - {…}"
 * wrapper around it. Same helper every other screen uses. */
function extractDetail(message: string): string {
  const match = message.match(/"detail"\s*:\s*"([^"]*)"/);
  return match ? match[1] : message;
}

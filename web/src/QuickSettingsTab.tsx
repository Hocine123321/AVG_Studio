import { useEffect, useState } from "react";
import { getQuickSettings, saveQuickSettings, type SettingSpec } from "./api";
import Select from "./Select";
import SettingsGroup from "./SettingsGroup";
import type { SettingsCtx } from "./fields/types";

/**
 * The Quick panel's own settings tab (spec §3.2) - the user's chosen
 * subset of the full catalogue, rendered with the exact same `SettingsGroup`
 * Settings.tsx uses. This is where Task 3's extraction pays for itself:
 * `schema`/`ctx`/`dirtyCount`/`saving`/`saveError`/`handleSave` are the
 * shared `useSettingsData()` instance QuickPanel holds - rendering the
 * chosen ids needs no new field code at all.
 *
 * The chosen-ids list (`GET`/`PUT /api/quick-settings`) is a document
 * entirely separate from the settings *values* `ctx`/`handleSave` manage:
 * adding or removing a setting from this tab saves immediately, on its own,
 * while editing a value still goes through the batched draft/Save flow
 * every other settings surface uses.
 */
export default function QuickSettingsTab({
  schema,
  ctx,
  dirtyCount,
  saving,
  saveError,
  handleSave,
}: {
  schema: SettingSpec[] | null;
  ctx: SettingsCtx;
  dirtyCount: number;
  saving: boolean;
  saveError: string | null;
  handleSave: () => void;
}) {
  const [ids, setIds] = useState<string[] | null>(null);

  useEffect(() => {
    void getQuickSettings().then((r) => setIds(r.ids));
  }, []);

  async function addId(id: string) {
    if (!id || ids === null || ids.includes(id)) return;
    const next = [...ids, id];
    setIds(next);
    await saveQuickSettings(next);
  }

  async function removeId(id: string) {
    if (ids === null) return;
    const next = ids.filter((i) => i !== id);
    setIds(next);
    await saveQuickSettings(next);
  }

  if (schema === null || ids === null) return <p className="home__empty">Loading…</p>;

  const specs = ids.map((id) => schema.find((s) => s.id === id)).filter((s): s is SettingSpec => s !== undefined);
  const addable = schema.filter((s) => !ids.includes(s.id));

  return (
    <div className="quickpanel-settings">
      {specs.length === 0 ? (
        <p className="home__empty">Nothing here yet — add a setting below.</p>
      ) : (
        <SettingsGroup title="Quick Settings" specs={specs} ctx={ctx} />
      )}

      <div className="quickpanel-settings__manage">
        <Select
          value=""
          ariaLabel="Add a setting"
          options={[
            { value: "", label: "Add a setting…" },
            ...addable.map((s) => ({ value: s.id, label: s.label })),
          ]}
          onChange={(v) => void addId(v)}
        />
        {ids.map((id) => (
          <button key={id} type="button" className="btn btn--sm" onClick={() => void removeId(id)}>
            Remove {schema.find((s) => s.id === id)?.label ?? id}
          </button>
        ))}
      </div>

      {saveError && <p className="settings__save-error">{saveError}</p>}
      {/* Plain `.btn`, never `.btn--primary` - the panel's one filled-action
          budget belongs to the screen underneath it (spec §4), not to a
          batched-value save that happens inside a panel over that screen. */}
      <button type="button" className="btn" disabled={dirtyCount === 0 || saving} onClick={handleSave}>
        {saving ? "Saving…" : `Save changes${dirtyCount ? ` (${dirtyCount})` : ""}`}
      </button>
    </div>
  );
}

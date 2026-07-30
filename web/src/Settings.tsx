import { useEffect, useMemo, useRef, useState } from "react";
import {
  deleteProviderKey,
  getChannels,
  getKeys,
  getSettingsSchema,
  getSettingsValues,
  saveSettingsValues,
  setProviderKey,
  type ChannelDoc,
  type SettingSpec,
} from "./api";
import HealthPanel from "./HealthPanel";
import ModelsPanel from "./ModelsPanel";
import SettingsGroup from "./SettingsGroup";
import type { SettingsCtx } from "./fields/types";

/** Nicer display names for the schema's raw `group` strings. Anything not
 * listed here still renders - just title-cased - so a brand-new group
 * value on the backend needs no matching entry here to show up correctly;
 * this map only ever improves on the fallback, never gates it. */
const GROUP_LABELS: Record<string, string> = {
  runtime: "Runtime & components",
  video: "Video format",
  captions: "Captions",
  voice: "Voice",
  images: "Images",
  render: "Render",
  creative: "Creative",
  models: "Models & cost",
  keys: "Provider keys",
};

function labelForGroup(group: string): string {
  return GROUP_LABELS[group] ?? group.charAt(0).toUpperCase() + group.slice(1);
}

/** Pulls the JSON `detail` out of `api.ts`'s formatted error message, same
 * trick `App.tsx` uses for render-start failures - so a 400 like "no
 * active channel — activate a channel before saving channel-scoped
 * settings" reaches the user as that sentence, not as the whole
 * "POST /api/... failed: 400 Bad Request - {...}" wrapper. */
function extractDetail(message: string): string {
  const match = message.match(/"detail"\s*:\s*"([^"]*)"/);
  return match ? match[1] : message;
}

/**
 * The settings screen: renders itself entirely from
 * `GET /api/settings/schema` (35 entries at last count) rather than one
 * hand-written control per setting - the legacy PowerShell app's settings
 * menu was ~4,300 lines for exactly that reason. Add a row to
 * `avg.core.settings_schema.SETTINGS` and it appears here with no change
 * to this file, `SettingsGroup.tsx`, or anything under `fields/` (as long
 * as its `type` is one of the seven the schema already declares).
 *
 * Three things this screen is built around:
 *
 * 1. Search leads. The search box filters every group's fields by label,
 *    help text AND id, and search always wins over the category list -
 *    the category nav is the fallback for browsing, not the main door in.
 * 2. Every non-secret edit joins one `draft` batch and is only sent on
 *    "Save changes" - the one terracotta action on this whole screen -
 *    via a single `POST /api/settings/values` call. That mirrors the
 *    route's own atomicity (it validates the whole batch, including "is a
 *    channel even active", before writing anything), and means a failed
 *    save never leaves the user guessing which of several edits landed.
 * 3. Secret fields are a separate lane entirely: they never enter
 *    `draft`, are saved/cleared immediately via `/api/keys/{provider}`,
 *    and this component never sees or stores a real key value - only
 *    `SecretField`'s own transient input does, for as long as one request
 *    takes (see that file).
 */
export default function Settings() {
  const [schema, setSchema] = useState<SettingSpec[] | null>(null);
  const [appValues, setAppValues] = useState<Record<string, unknown>>({});
  const [channelValues, setChannelValues] = useState<Record<string, unknown>>({});
  const [activeChannelId, setActiveChannelId] = useState<string | null>(null);
  const [channels, setChannels] = useState<ChannelDoc[]>([]);
  const [keys, setKeys] = useState<Record<string, boolean>>({});
  const [loadError, setLoadError] = useState<string | null>(null);

  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [keyBusy, setKeyBusy] = useState<Record<string, boolean>>({});
  const [keyErrors, setKeyErrors] = useState<Record<string, string | null>>({});

  const [search, setSearch] = useState("");
  /** Which group is currently in view. Derived from an IntersectionObserver,
   * never from a click: clicking scrolls, and the observer then reports
   * where the scroll landed - so the mark can never disagree with the page. */
  const [activeGroup, setActiveGroup] = useState<string | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    Promise.all([getSettingsSchema(), getSettingsValues(), getChannels(), getKeys()])
      .then(([schemaRes, valuesRes, channelsRes, keysRes]) => {
        setSchema(schemaRes.settings);
        setAppValues(valuesRes.app);
        setChannelValues(valuesRes.channel);
        setActiveChannelId(valuesRes.active_channel_id);
        setChannels(channelsRes.channels);
        setKeys(keysRes);
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : String(err)));
  }, []);

  const channelLabel = useMemo(() => {
    if (!activeChannelId) return null;
    return channels.find((c) => c.id === activeChannelId)?.label ?? null;
  }, [channels, activeChannelId]);

  function getValue(spec: SettingSpec): unknown {
    if (Object.prototype.hasOwnProperty.call(draft, spec.id)) return draft[spec.id];
    if (spec.type === "secret") return keys[spec.id] ?? false;
    const source = spec.scope === "app" ? appValues : channelValues;
    return Object.prototype.hasOwnProperty.call(source, spec.id) ? source[spec.id] : spec.default;
  }

  /** `getValue` by id rather than by spec, as a string - what the read-only
   * Models overview needs. Goes through `getValue`, so it shows unsaved
   * edits, not just what is on disk. */
  function valueById(id: string): string {
    const spec = schema?.find((s) => s.id === id);
    return spec ? String(getValue(spec) ?? "") : "";
  }

  function isDirty(id: string): boolean {
    return Object.prototype.hasOwnProperty.call(draft, id);
  }

  function onCommit(spec: SettingSpec, value: unknown) {
    setSaveError(null);
    setDraft((d) => ({ ...d, [spec.id]: value }));
  }

  const dirtyCount = Object.keys(draft).length;

  async function handleSave() {
    if (dirtyCount === 0) return;
    setSaving(true);
    setSaveError(null);
    try {
      const res = await saveSettingsValues(draft);
      setAppValues(res.app);
      setChannelValues(res.channel);
      setActiveChannelId(res.active_channel_id);
      setDraft({});
    } catch (err) {
      // Fail cleanly, honestly - keep every pending edit exactly as the
      // user left it rather than pretending any of it saved.
      setSaveError(extractDetail(err instanceof Error ? err.message : String(err)));
    } finally {
      setSaving(false);
    }
  }

  function handleDiscard() {
    setDraft({});
    setSaveError(null);
  }

  async function handleSetKey(provider: string, key: string) {
    setKeyBusy((b) => ({ ...b, [provider]: true }));
    setKeyErrors((e) => ({ ...e, [provider]: null }));
    try {
      const res = await setProviderKey(provider, key);
      setKeys((k) => ({ ...k, ...res }));
    } catch (err) {
      setKeyErrors((e) => ({
        ...e,
        [provider]: extractDetail(err instanceof Error ? err.message : String(err)),
      }));
    } finally {
      setKeyBusy((b) => ({ ...b, [provider]: false }));
    }
  }

  async function handleDeleteKey(provider: string) {
    setKeyBusy((b) => ({ ...b, [provider]: true }));
    setKeyErrors((e) => ({ ...e, [provider]: null }));
    try {
      const res = await deleteProviderKey(provider);
      setKeys((k) => ({ ...k, ...res }));
    } catch (err) {
      setKeyErrors((e) => ({
        ...e,
        [provider]: extractDetail(err instanceof Error ? err.message : String(err)),
      }));
    } finally {
      setKeyBusy((b) => ({ ...b, [provider]: false }));
    }
  }

  const ctx: SettingsCtx = {
    getValue,
    valueById,
    isDirty,
    onCommit,
    activeChannelId,
    channelLabel,
    keys,
    keyBusy,
    keyErrors,
    onSetKey: handleSetKey,
    onDeleteKey: handleDeleteKey,
  };

  const groups = useMemo(() => {
    if (!schema) return [];
    const order: string[] = [];
    const byGroup = new Map<string, SettingSpec[]>();
    for (const spec of schema) {
      if (!byGroup.has(spec.group)) {
        byGroup.set(spec.group, []);
        order.push(spec.group);
      }
      byGroup.get(spec.group)!.push(spec);
    }
    return order.map((group) => ({ group, label: labelForGroup(group), specs: byGroup.get(group) ?? [] }));
  }, [schema]);

  const query = search.trim().toLowerCase();
  const searching = query.length > 0;

  /** Search filters fields by label, help and id, and a group with no
   * surviving field disappears from the page AND from the nav. Every group
   * renders otherwise - the nav navigates, it no longer filters. */
  const visibleGroups = useMemo(() => {
    if (!searching) return groups;
    return groups
      .map((g) => ({
        ...g,
        specs: g.specs.filter(
          (s) =>
            s.label.toLowerCase().includes(query) ||
            s.help.toLowerCase().includes(query) ||
            s.id.toLowerCase().includes(query),
        ),
      }))
      .filter((g) => g.specs.length > 0);
  }, [groups, searching, query]);

  // Marks whichever group crosses the middle band of the viewport. The
  // root is the viewport rather than `.app-shell__content`: the scroll
  // container fills the viewport, so the two agree, and reaching for an
  // ancestor by class from here would couple this screen to the shell.
  useEffect(() => {
    const root = contentRef.current;
    if (!root) return;
    const sections = Array.from(root.querySelectorAll<HTMLElement>("[data-settings-group]"));
    if (sections.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveGroup(entry.target.getAttribute("data-settings-group"));
          }
        }
      },
      { rootMargin: "-40% 0px -55% 0px", threshold: 0 },
    );
    for (const section of sections) observer.observe(section);
    return () => observer.disconnect();
  }, [visibleGroups]);

  function jumpTo(group: string) {
    contentRef.current
      ?.querySelector(`[data-settings-group="${group}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
    setActiveGroup(group);
  }

  if (loadError) {
    return (
      <div className="settings">
        <p className="home__empty">Could not load settings: {loadError}</p>
      </div>
    );
  }

  if (!schema) {
    return (
      <div className="settings">
        <p className="home__empty">Loading…</p>
      </div>
    );
  }

  const matchCount = visibleGroups.reduce((n, g) => n + g.specs.length, 0);

  return (
    <div className="settings">
      <header className="settings__header">
        <div>
          <div className="home__kicker">Configure</div>
          <h1 className="home__title">Settings</h1>
        </div>
        <span className="home__spacer" />
        {activeChannelId ? (
          <span className="chip">Channel: {channelLabel ?? activeChannelId}</span>
        ) : (
          <span className="chip chip--bad">No active channel</span>
        )}
      </header>

      <div className="settings__layout">
        <nav className="settings__nav" aria-label="Setting categories">
          <input
            className="settings__search"
            type="search"
            placeholder="Search settings…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search settings by name or description"
          />
          <div className="settings__nav-list">
            {visibleGroups.map((g) => (
              <button
                key={g.group}
                type="button"
                aria-current={activeGroup === g.group || undefined}
                className={`settings__nav-item${activeGroup === g.group ? " settings__nav-item--on" : ""}`}
                onClick={() => jumpTo(g.group)}
              >
                {g.label}
              </button>
            ))}
          </div>
        </nav>

        <div className="settings__content" ref={contentRef}>
          {searching && (
            <p className="settings__result-count">
              {matchCount} match{matchCount === 1 ? "" : "es"} for &ldquo;{search.trim()}&rdquo;
            </p>
          )}

          {visibleGroups.length === 0 ? (
            <p className="home__empty">Nothing matches &ldquo;{search.trim()}&rdquo;.</p>
          ) : (
            visibleGroups.map((g) => (
              <div key={g.group} data-settings-group={g.group}>
                {/* Only when the whole group is on screen: while searching,
                    a filtered subset of the model fields would give the
                    overview table nothing coherent to summarise. */}
                {/* Components heads Runtime rather than getting a nav entry
                    of its own: both answer "where are this app's tools and
                    do they work", and Runtime is the first group the screen
                    renders.

                    It was keyed to a `paths` group until 2026-07-30, and
                    `avg.core.settings_schema.SETTINGS` has no such group -
                    it declares nine (runtime, creative, video, models,
                    captions, voice, images, render, keys), so this panel
                    had silently never rendered. Keyed to a real group, it
                    is on screen again. */}
                {g.group === "runtime" && !searching && <HealthPanel />}
                {g.group === "models" && !searching && (
                  <ModelsPanel
                    specs={g.specs}
                    valueOf={valueById}
                    lengthPreset={valueById("length_preset") || "60s"}
                  />
                )}
                <SettingsGroup title={g.label} specs={g.specs} ctx={ctx} />
              </div>
            ))
          )}
        </div>
      </div>

      <div className="settings__savebar">
        {saveError && <p className="settings__save-error">{saveError}</p>}
        <span className="settings__dirty-note">
          {dirtyCount > 0 ? `${dirtyCount} unsaved change${dirtyCount === 1 ? "" : "s"}` : "No unsaved changes"}
        </span>
        <button type="button" className="btn" onClick={handleDiscard} disabled={dirtyCount === 0 || saving}>
          Discard
        </button>
        <button type="button" className="btn btn--primary" onClick={handleSave} disabled={dirtyCount === 0 || saving}>
          {saving ? "Saving…" : "Save changes"}
        </button>
      </div>
    </div>
  );
}

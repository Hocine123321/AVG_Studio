import { useEffect, useMemo, useState } from "react";
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
import { applyMotionScale } from "./theme";
import type { SettingsCtx } from "./fields/types";

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
 * Everything `Settings.tsx` needs to load, edit and save settings, minus
 * the screen-only concerns (search, category nav, port-to-global) - see
 * that file's own header comment for the full picture of the three-part
 * model (search leads / one batched draft / secrets are a separate lane)
 * this hook implements the middle two of.
 *
 * Extracted verbatim out of `Settings.tsx` for Slice 18: no behavior
 * change, just a seam so `QuickSettingsTab` and `PreviewTab` (the Quick
 * panel's own consumers of `SettingsCtx`) get the exact same
 * value-resolution and save machinery Settings.tsx trusts, rather than a
 * second implementation that could drift from it. `handlePortToGlobal` and
 * its state stay behind in `Settings.tsx` - porting is a Settings-screen-
 * only action, not something a Quick Settings tab needs, and keeping this
 * hook's surface to exactly what `SettingsCtx` requires is what keeps it
 * reusable.
 */
export function useSettingsData() {
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

  useEffect(() => {
    // One shared function (`theme.ts`) drives both the numeric custom
    // property and the `data-motion="none"` attribute, rather than this
    // effect re-deriving the mapping. `prefers-reduced-motion` still wins -
    // see that block in tokens.css, which is unchanged.
    applyMotionScale(appValues["motion_scale"]);
  }, [appValues]);

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

  // True while the active channel reads its channel-scope settings from the
  // app-wide defaults document rather than its own - see `SettingsCtx`'s
  // `followingGlobal` for what this gates. Read via `valueById` rather than
  // `channelValues.settings_source` directly, so an unsaved edit to the
  // switch itself (still in `draft`, not yet round-tripped through Save)
  // takes effect the moment it is made, not after a save.
  const followingGlobal = valueById("settings_source") === "global";

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
    followingGlobal,
    keys,
    keyBusy,
    keyErrors,
    onSetKey: handleSetKey,
    onDeleteKey: handleDeleteKey,
  };

  return {
    schema,
    ctx,
    dirtyCount,
    saving,
    saveError,
    handleSave,
    handleDiscard,
    loadError,
    channels,
    activeChannelId,
    channelLabel,
  };
}

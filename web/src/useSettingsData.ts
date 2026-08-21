import { useEffect, useMemo, useState } from "react";
import {
  deleteProviderKey,
  getChannels,
  getHardwareInfo,
  getKeys,
  getSettingsSchema,
  getSettingsValues,
  releaseSettingValue,
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
  const [cpuCount, setCpuCount] = useState<number | null>(null);

  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // The Settings screen's Global/Channel toggle (Slice 19 §3) - a
  // presentation-mode switch, not persisted anywhere; switching it does
  // not write anything by itself. Starts on "channel" so every existing
  // screen (Quick Settings included) sees exactly what it did before this
  // slice.
  const [scope, setScope] = useState<"channel" | "global">("channel");

  const [keyBusy, setKeyBusy] = useState<Record<string, boolean>>({});
  const [keyErrors, setKeyErrors] = useState<Record<string, string | null>>({});

  useEffect(() => {
    Promise.all([getSettingsSchema(), getSettingsValues(), getChannels(), getKeys(), getHardwareInfo()])
      .then(([schemaRes, valuesRes, channelsRes, keysRes, hardwareRes]) => {
        setSchema(schemaRes.settings);
        setAppValues(valuesRes.app);
        setChannelValues(valuesRes.channel);
        setActiveChannelId(valuesRes.active_channel_id);
        setChannels(channelsRes.channels);
        setKeys(keysRes);
        setCpuCount(hardwareRes.cpu_count);
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

  // Re-reads channel-scope values whenever the toggle itself changes -
  // "channel" and "global" are two different documents (the active
  // channel's own vs the app-wide defaults), not two views of the same
  // data, so switching needs a real refetch, not a client-side recompute.
  // Guarded on `schema` so this never double-fires alongside the initial
  // load effect above (that one always starts from "channel" scope).
  useEffect(() => {
    if (schema === null) return;
    getSettingsValues(scope)
      .then((res) => {
        setChannelValues(res.channel);
        setActiveChannelId(res.active_channel_id);
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : String(err)));
    // A pending edit belongs to whichever document was on screen when it
    // was made - carrying it across to the other document would silently
    // apply it somewhere the user never saw it land.
    setDraft({});
    setSaveError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

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

  /** Whether the active channel's own document holds an explicit key for
   * `id` - `false` also when no channel is active (nothing to own).
   * Purely a client-side read of data the hook already has in memory
   * (`channels`, from `getChannels()`); goes stale the moment ownership
   * changes, so both `handleSave` and `releaseOwnership` refetch
   * `channels` after a successful write, same discipline as their own
   * values refetch. */
  function isOwned(id: string): boolean {
    const channel = channels.find((c) => c.id === activeChannelId);
    return channel !== undefined && Object.prototype.hasOwnProperty.call(channel.settings, id);
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
      const res = await saveSettingsValues(draft, scope);
      setAppValues(res.app);
      setChannelValues(res.channel);
      setActiveChannelId(res.active_channel_id);
      setDraft({});
      // A save can turn a previously-inherited field into an owned one -
      // refetch `channels` so `isOwned` reflects that immediately rather
      // than lagging until the next unrelated reload.
      const channelsRes = await getChannels();
      setChannels(channelsRes.channels);
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

  /** `DELETE /api/settings/values/{id}` - deletes the key from the active
   * channel's own document immediately, not batched into `draft` like an
   * ordinary edit (mirrors the secret lane's own immediate delete). */
  async function releaseOwnership(id: string) {
    setSaveError(null);
    try {
      const res = await releaseSettingValue(id);
      setChannelValues(res.channel);
      setActiveChannelId(res.active_channel_id);
      const channelsRes = await getChannels();
      setChannels(channelsRes.channels);
    } catch (err) {
      setSaveError(extractDetail(err instanceof Error ? err.message : String(err)));
    }
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
    scope,
    isOwned,
    onReleaseOwnership: releaseOwnership,
    keys,
    keyBusy,
    keyErrors,
    onSetKey: handleSetKey,
    onDeleteKey: handleDeleteKey,
    cpuCount,
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
    scope,
    setScope,
  };
}

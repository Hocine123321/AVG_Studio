import type { SettingSpec } from "../api";

/**
 * Everything a field renderer might need, bundled so `Settings.tsx` builds
 * it once and every row/field gets the same object - no prop-drilling a
 * dozen individual callbacks through `SettingsGroup`.
 *
 * `getValue`/`isDirty`/`onCommit` cover the six ordinary types, all of
 * which flow through one pending "draft" batch in `Settings.tsx` and are
 * saved together (see that file's header comment for why). `secret` is
 * structurally different - its value never lives in the draft or in any
 * component state a moment longer than the request that sends it, so it
 * gets its own lane: `keys` (provider -> is-a-key-set), `keyBusy`/
 * `keyErrors` per provider, and `onSetKey`/`onDeleteKey` which hit
 * `/api/keys/{provider}` directly rather than the batched values endpoint
 * (which refuses `type="secret"` outright).
 */
export interface SettingsCtx {
  getValue: (spec: SettingSpec) => unknown;
  /** Another setting's current value, by id, as a string. A field
   * occasionally needs one: the image and voice pickers filter their
   * options by whichever provider is selected beside them, and asking for
   * it here is cheaper than every field carrying the whole values map. */
  valueById: (id: string) => string;
  isDirty: (id: string) => boolean;
  onCommit: (spec: SettingSpec, value: unknown) => void;

  /** Which channel a `scope="channel"` field will land on, for the "this
   * is obviously going to land here" chip - `null` means no channel is
   * active, so those fields render disabled with an explanation rather
   * than silently failing on save. */
  activeChannelId: string | null;
  channelLabel: string | null;

  keys: Record<string, boolean>;
  keyBusy: Record<string, boolean>;
  keyErrors: Record<string, string | null>;
  onSetKey: (provider: string, key: string) => void;
  onDeleteKey: (provider: string) => void;
}

export interface FieldProps {
  spec: SettingSpec;
  ctx: SettingsCtx;
  disabled?: boolean;
}

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

  /** The Settings screen's Global/Channel toggle (Slice 19 §3) - a
   * presentation-mode switch, not persisted state. `"channel"`: fields
   * show the active channel's resolved values, and an inherited
   * (unowned) `scope="channel"` field renders disabled with a "Take
   * ownership" affordance (see `isOwned`). `"global"`: fields read/write
   * the app-wide defaults document directly - nothing is ever disabled in
   * this mode, since there is nothing above the defaults document to
   * inherit from. */
  scope: "channel" | "global";
  /** Whether the active channel's own document holds an explicit key for
   * `id` (as opposed to inheriting it from the app-wide defaults document
   * or the `SettingSpec`'s own default). Only meaningful when `scope ===
   * "channel"` and the field itself is `scope: "channel"` - callers outside
   * that combination should treat every field as owned/editable. */
  isOwned: (id: string) => boolean;
  /** `DELETE /api/settings/values/{id}` - deletes the key from the active
   * channel's own document immediately (not batched into the draft, unlike
   * every other edit this context exposes - see `onCommit`), so the field
   * goes back to showing whatever it now inherits. */
  onReleaseOwnership: (id: string) => void;

  keys: Record<string, boolean>;
  keyBusy: Record<string, boolean>;
  keyErrors: Record<string, string | null>;
  onSetKey: (provider: string, key: string) => void;
  onDeleteKey: (provider: string) => void;

  /** The machine's own core count (`GET /api/system/hardware`) -
   * `render_threads`' slider needs it for its own upper bound (Slice 29);
   * `null` until that one fetch resolves. Every other field ignores it. */
  cpuCount: number | null;
}

export interface FieldProps {
  spec: SettingSpec;
  ctx: SettingsCtx;
  disabled?: boolean;
}

import type { SettingSpec } from "./api";
import FieldRow from "./FieldRow";
import type { SettingsCtx } from "./fields/types";

export interface SettingsGroupProps {
  title: string;
  specs: SettingSpec[];
  ctx: SettingsCtx;
  /** Whether this group is the one nearest the middle of the viewport
   * right now (Settings.tsx's scrollspy, Slice 19 Task 10.4) - purely a
   * `.settings-group__title--active` highlight for multi-group categories
   * (Models, Captions, Render); optional so every other caller
   * (QuickSettingsTab, NewTakePanel) is unaffected. */
  active?: boolean;
}

/**
 * One category's worth of field rows. Renders whatever `specs` it is
 * given - nothing in this file names a specific setting id, which is the
 * point: a 36th schema entry lands in whichever group it declares with no
 * change here (see `Settings.tsx`'s header comment for the full story).
 *
 * Each row states three things plainly: the label/help from the schema,
 * which channel a channel-scoped field will land on (or that none is
 * active, in which case the control is disabled rather than silently
 * failing later), and whether the row has an unsaved edit.
 *
 * Ownership (Slice 19 §2.4) is the fourth thing, and only applies to
 * `scope="channel"` fields while `ctx.scope === "channel"`: an inherited
 * (unowned) field renders disabled - reusing the same `.field-input:
 * disabled`/`.switch:disabled` tokens `channelBlocked` already uses, not a
 * new visual state - with a "Take ownership" button beside it; an owned
 * one gains a small "Release" affordance. In `ctx.scope === "global"`
 * mode nothing is ever disabled for this reason: editing the defaults
 * document directly has nothing above it to inherit from.
 */
export default function SettingsGroup({ title, specs, ctx, active }: SettingsGroupProps) {
  return (
    <section className="settings-group">
      <h2 className={`settings-group__title${active ? " settings-group__title--active" : ""}`}>{title}</h2>
      <div className="settings-group__list">
        {specs.map((spec) => (
          <FieldRow key={spec.id} spec={spec} ctx={ctx} />
        ))}
      </div>
    </section>
  );
}

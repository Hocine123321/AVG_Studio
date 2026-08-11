import type { SettingSpec } from "./api";
import { Field } from "./fields";
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
        {specs.map((spec) => {
          const dirty = ctx.isDirty(spec.id);
          // Secrets never join the draft batch (see SecretField's header
          // comment) so they are never "blocked" by channel state - only
          // the six ordinary channel-scoped types can be.
          const channelBlocked = spec.scope === "channel" && spec.type !== "secret" && !ctx.activeChannelId;
          // Ownership only means something for a channel-scope field while
          // looking at the channel's own (not the global) view - no
          // `scope="channel"` setting is ever `type="secret"` (see
          // `avg.core.settings_schema`), so no extra type guard is needed.
          const ownershipApplies = spec.scope === "channel" && ctx.scope === "channel";
          const owned = !ownershipApplies || ctx.isOwned(spec.id);
          const inherited = ownershipApplies && !owned;

          return (
            <div key={spec.id} className={`field-row${dirty ? " field-row--dirty" : ""}`}>
              <div className="field-row__info">
                <div className="field-row__label-line">
                  <span className="field-row__label">{spec.label}</span>
                  {spec.scope === "channel" && (
                    <span
                      className={`field-row__scope${ctx.activeChannelId ? "" : " field-row__scope--none"}`}
                    >
                      {ctx.activeChannelId ? `→ ${ctx.channelLabel ?? ctx.activeChannelId}` : "No active channel"}
                    </span>
                  )}
                  {dirty && <span className="field-row__dirty-dot" title="Unsaved change" aria-hidden="true" />}
                </div>
                <p className="field-row__help">{spec.help}</p>
                {channelBlocked && (
                  <p className="field-row__blocked">Activate a channel before this can be saved.</p>
                )}
                {inherited && (
                  <p className="field-row__blocked field-row__blocked--muted">
                    Inherited from the app-wide defaults. Take ownership to set this channel&rsquo;s
                    own value.
                  </p>
                )}
              </div>
              <div className="field-row__control">
                <Field spec={spec} ctx={ctx} disabled={channelBlocked || inherited} />
                {inherited && (
                  <button
                    type="button"
                    className="btn btn--sm"
                    onClick={() => ctx.onCommit(spec, ctx.getValue(spec))}
                  >
                    Take ownership
                  </button>
                )}
                {ownershipApplies && owned && (
                  <button
                    type="button"
                    className="btn btn--sm btn--ghost"
                    onClick={() => ctx.onReleaseOwnership(spec.id)}
                    title="Release this channel's own value and go back to inheriting"
                  >
                    Release
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

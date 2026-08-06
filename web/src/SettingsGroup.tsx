import type { SettingSpec } from "./api";
import { Field } from "./fields";
import type { SettingsCtx } from "./fields/types";

export interface SettingsGroupProps {
  title: string;
  specs: SettingSpec[];
  ctx: SettingsCtx;
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
 * A third disabled state sits beside "no active channel": the active
 * channel's `settings_source` reading `"global"`. The route still write-
 * throughs an edit to the channel's own document in that state (see
 * `avg.api.routes_settings.post_settings_values` and its docstring on why
 * that is intentional), but `_channel_values` is reading the defaults
 * document while it is on, so the screen would show the save land and then
 * never show it applied - the exact silent no-op Important 4 of the Slice
 * 12 review found. `settings_source` itself is exempted: it is how the user
 * gets back, and disabling it would strand them.
 */
export default function SettingsGroup({ title, specs, ctx }: SettingsGroupProps) {
  return (
    <section className="settings-group">
      <h2 className="settings-group__title">{title}</h2>
      <div className="settings-group__list">
        {specs.map((spec) => {
          const dirty = ctx.isDirty(spec.id);
          // Secrets never join the draft batch (see SecretField's header
          // comment) so they are never "blocked" by channel state - only
          // the six ordinary channel-scoped types can be.
          const channelBlocked = spec.scope === "channel" && spec.type !== "secret" && !ctx.activeChannelId;
          const followingGlobal =
            spec.scope === "channel" && spec.id !== "settings_source" && ctx.followingGlobal;

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
              </div>
              <div className="field-row__control">
                <Field spec={spec} ctx={ctx} disabled={channelBlocked || followingGlobal} />
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

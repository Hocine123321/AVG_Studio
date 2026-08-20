import type { SettingSpec } from "./api";
import { Field } from "./fields";
import type { SettingsCtx } from "./fields/types";

/**
 * One setting's row - label, scope/ownership chip, help text, dirty dot,
 * and its `Field`. Extracted out of `SettingsGroup.tsx` (Slice 29): the
 * per-phase grouping in `ModelsFieldGroups.tsx` needs the identical
 * rendering, ownership semantics included, not a second copy of it that
 * could drift. `SettingsGroup` itself is unchanged in what it renders,
 * only in how it's structured internally.
 */
export default function FieldRow({ spec, ctx }: { spec: SettingSpec; ctx: SettingsCtx }) {
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
    <div className={`field-row${dirty ? " field-row--dirty" : ""}`}>
      <div className="field-row__info">
        <div className="field-row__label-line">
          <span className="field-row__label">{spec.label}</span>
          {spec.scope === "channel" && (
            <span className={`field-row__scope${ctx.activeChannelId ? "" : " field-row__scope--none"}`}>
              {ctx.activeChannelId ? `→ ${ctx.channelLabel ?? ctx.activeChannelId}` : "No active channel"}
            </span>
          )}
          {dirty && <span className="field-row__dirty-dot" title="Unsaved change" aria-hidden="true" />}
        </div>
        <p className="field-row__help">{spec.help}</p>
        {channelBlocked && <p className="field-row__blocked">Activate a channel before this can be saved.</p>}
        {inherited && (
          <p className="field-row__blocked field-row__blocked--muted">
            Inherited from the app-wide defaults. Take ownership to set this channel&rsquo;s own value.
          </p>
        )}
      </div>
      <div className="field-row__control">
        <Field spec={spec} ctx={ctx} disabled={channelBlocked || inherited} />
        {inherited && (
          <button type="button" className="btn btn--sm" onClick={() => ctx.onCommit(spec, ctx.getValue(spec))}>
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
}

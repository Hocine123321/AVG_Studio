import type { SettingSpec } from "./api";
import FieldRow from "./FieldRow";
import type { SettingsCtx } from "./fields/types";

interface PhaseGroup {
  phase: string;
  label: string;
  model?: SettingSpec;
  backup?: SettingSpec;
  onFailure?: SettingSpec;
}

const SUFFIXES: { suffix: string; slot: "model" | "backup" | "onFailure" }[] = [
  { suffix: "_model", slot: "model" },
  { suffix: "_backup", slot: "backup" },
  { suffix: "_on_failure", slot: "onFailure" },
];

const LABEL_SUFFIXES: Record<"model" | "backup" | "onFailure", string> = {
  model: " — model",
  backup: " — backup",
  onFailure: " — if both fail",
};

/** Which phase group (if any) a `models`-group spec belongs to, and which
 * slot within it - derived from the id's own suffix, matching exactly
 * how `avg.core.settings_schema`'s per-phase generator built the id and
 * label in the first place (`f"{phase_key}_model"`, `f"{phase_label} —
 * model"`, etc.) - not a second naming convention invented here. Verified
 * against the schema's complete `group="models"` list (17 specs: 5
 * phases × 3 slots, plus `text_model_default` and `live_list_source`)
 * before writing this, not assumed - the similarly-suffixed
 * `image_provider_backup`/`image_model_backup`/`image_on_failure` fields
 * that could otherwise collide with this suffix matching live in
 * `group="images"` entirely, a different category this component never
 * receives. `text_model_default` doesn't end in any tracked suffix
 * (`_default`, not `_model`) so it already falls through unclassified
 * without the explicit check below - kept anyway as a readable, direct
 * statement of intent rather than relying on that being incidental.
 */
function classify(spec: SettingSpec): { phase: string; slot: "model" | "backup" | "onFailure" } | null {
  if (spec.id === "text_model_default") return null;
  for (const { suffix, slot } of SUFFIXES) {
    if (spec.id.endsWith(suffix)) {
      return { phase: spec.id.slice(0, -suffix.length), slot };
    }
  }
  return null;
}

/**
 * Groups the `models` category's flat spec list into one section per
 * phase (Primary + Backup + If-both-fail together, spec's own §1) plus
 * whatever doesn't belong to a phase (`text_model_default`,
 * `live_list_source`) rendered at its natural position in the original
 * order. Reuses `FieldRow` for every individual field - this is a layout
 * change over the generic renderer, not a new field-rendering path.
 *
 * Fully replaces `SettingsGroup` for this one category in `Settings.tsx`
 * (not composed alongside it), so it reproduces that component's outer
 * `<section>`/`<h2>` wrapper verbatim, `active`-highlight included -
 * losing the category's own title, or its scrollspy highlight, would be
 * a real regression for the one category besides Captions and Render
 * where that highlight is the scrollspy's only visible effect (see
 * `SettingsGroup`'s own comment on `active`).
 */
export default function ModelsFieldGroups({
  title,
  specs,
  ctx,
  active,
}: {
  title: string;
  specs: SettingSpec[];
  ctx: SettingsCtx;
  active?: boolean;
}) {
  const groups = new Map<string, PhaseGroup>();
  for (const spec of specs) {
    const classified = classify(spec);
    if (!classified) continue;
    const { phase, slot } = classified;
    const suffix = LABEL_SUFFIXES[slot];
    const group = groups.get(phase) ?? {
      phase,
      label: spec.label.endsWith(suffix) ? spec.label.slice(0, -suffix.length) : phase,
    };
    group[slot] = spec;
    groups.set(phase, group);
  }

  const rendered = new Set<string>();

  return (
    <section className="settings-group">
      <h2 className={`settings-group__title${active ? " settings-group__title--active" : ""}`}>{title}</h2>
      <div className="settings-group__list">
        {specs.map((spec) => {
          const classified = classify(spec);
          if (!classified) return <FieldRow key={spec.id} spec={spec} ctx={ctx} />;
          const group = groups.get(classified.phase);
          if (!group || rendered.has(group.phase)) return null;
          rendered.add(group.phase);
          return (
            <section key={group.phase} className="models-phase-group">
              <h3 className="models-phase-group__title">{group.label}</h3>
              {group.model && <FieldRow spec={group.model} ctx={ctx} />}
              {group.backup && <FieldRow spec={group.backup} ctx={ctx} />}
              {group.onFailure && <FieldRow spec={group.onFailure} ctx={ctx} />}
            </section>
          );
        })}
      </div>
    </section>
  );
}

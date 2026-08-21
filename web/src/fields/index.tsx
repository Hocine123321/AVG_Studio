import type { ComponentType } from "react";
import type { SettingType } from "../api";
import { BoolField, ChoiceField, FloatField, IntField, PathField, TextField } from "./Fields";
import SecretField from "./SecretField";
import ModelPicker from "../ModelPicker";
import Slider from "../Slider";
import type { FieldProps } from "./types";

export type { FieldProps, SettingsCtx } from "./types";

/**
 * One renderer per `SettingSpec.type`, chosen by lookup - not a chain of
 * `if (spec.type === ...)` in one component. This map is the entire
 * contract a new setting has to satisfy: give it a `type` that's already a
 * key here (the schema only has these seven) and it renders with zero
 * changes to `Settings.tsx` or `SettingsGroup.tsx`.
 */
const REGISTRY: Record<SettingType, ComponentType<FieldProps>> = {
  bool: BoolField,
  int: IntField,
  float: FloatField,
  text: TextField,
  choice: ChoiceField,
  path: PathField,
  secret: SecretField,
};

/** Every slider except `render_threads`, whose upper bound is the
 * machine's own core count and so can't live in a table keyed only by id
 * - handled as its own check in `Field`, Task 4 (Slice 29). Step sizes
 * are 5% of each range (design spec §2's own table); `fps`'s 2.25 and
 * `captions_font_size`'s 4.8 are left un-rounded on purpose - a native
 * `<input type="range">` step lands on the nearest multiple regardless,
 * so a "clean" rounded number buys nothing a real fraction doesn't
 * already give the same UX for. Verified all four ids and their default
 * values against the real schema before writing this table, not assumed
 * from the design spec's own numbers alone. */
const SLIDER_BOUNDS: Record<string, { min: number; max: number; step: number; zeroLabel?: string }> = {
  voice_stability: { min: 0, max: 1, step: 0.05 },
  voice_speed: { min: 0.5, max: 2, step: 0.075 },
  fps: { min: 15, max: 60, step: 2.25 },
  captions_font_size: { min: 0, max: 96, step: 4.8, zeroLabel: "Auto-fit" },
};

export function Field({ spec, ctx, disabled }: FieldProps) {
  // `source` outranks `type`: a field whose options only exist at runtime
  // cannot be described by the catalogue, so the schema declares where they
  // come from instead. Only `model_cache` exists today; an unknown source
  // falls through to the ordinary renderer rather than rendering nothing.
  // Three sources today, one component: `model_cache` (text models, kept
  // for any channel document written before Slice 8) and the two
  // `live_list:` kinds. The kind is the part after the colon.
  if (spec.source === "model_cache" || spec.source?.startsWith("live_list:")) {
    return <ModelPicker spec={spec} ctx={ctx} disabled={disabled} />;
  }
  // Chosen by id, not by type or source (Slice 29) - `fps` is `type="int"`
  // the same as several non-slider ints, and none of these four declare a
  // `source`.
  if (spec.id in SLIDER_BOUNDS) {
    return <Slider spec={spec} ctx={ctx} disabled={disabled} {...SLIDER_BOUNDS[spec.id]} />;
  }
  // A sixth id, kept out of SLIDER_BOUNDS: its upper bound is this
  // machine's own core count, not a fixed constant. `?? 8` only covers
  // the brief window before `GET /api/system/hardware` resolves (or, more
  // rarely, a panel like NewTakePanel.tsx that never fetches hardware
  // info at all) - the slider becomes accurate the moment that fetch
  // lands, same render, no remount.
  if (spec.id === "render_threads") {
    const max = ctx.cpuCount ?? 8;
    return (
      <Slider
        spec={spec}
        ctx={ctx}
        disabled={disabled}
        min={0}
        max={max}
        step={Math.max(1, Math.round(max / 20))}
        zeroLabel="Auto"
      />
    );
  }
  const Component = REGISTRY[spec.type];
  if (!Component) {
    // Unreachable for anything the current schema can produce - kept as an
    // honest fallback rather than a crash if a future type ships on the
    // backend before this map is updated.
    return <p className="field-row__help">Unsupported setting type: {spec.type}</p>;
  }
  return <Component spec={spec} ctx={ctx} disabled={disabled} />;
}

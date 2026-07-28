import type { ComponentType } from "react";
import type { SettingType } from "../api";
import { BoolField, ChoiceField, FloatField, IntField, PathField, TextField } from "./Fields";
import SecretField from "./SecretField";
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

export function Field({ spec, ctx, disabled }: FieldProps) {
  const Component = REGISTRY[spec.type];
  if (!Component) {
    // Unreachable for anything the current schema can produce - kept as an
    // honest fallback rather than a crash if a future type ships on the
    // backend before this map is updated.
    return <p className="field-row__help">Unsupported setting type: {spec.type}</p>;
  }
  return <Component spec={spec} ctx={ctx} disabled={disabled} />;
}

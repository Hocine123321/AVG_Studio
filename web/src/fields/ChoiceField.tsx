import type { FieldProps } from "./types";

/** A closed set of options - a native `<select>`, since the app has no
 * form-widget library and this is a solved problem without one. */
export default function ChoiceField({ spec, ctx, disabled }: FieldProps) {
  const value = String(ctx.getValue(spec) ?? "");
  return (
    <select
      className="field-select"
      aria-label={spec.label}
      value={value}
      disabled={disabled}
      onChange={(e) => ctx.onCommit(spec, e.target.value)}
    >
      {(spec.choices ?? []).map((choice) => (
        <option key={choice} value={choice}>
          {choice}
        </option>
      ))}
    </select>
  );
}

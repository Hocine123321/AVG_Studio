import type { FieldProps } from "./types";

/** A toggle switch. Commits immediately (there's nothing to "finish
 * typing") - it just joins the pending draft batch like every other
 * non-secret field; nothing hits the network until Save. */
export default function BoolField({ spec, ctx, disabled }: FieldProps) {
  const value = Boolean(ctx.getValue(spec));
  return (
    <button
      type="button"
      role="switch"
      aria-checked={value}
      aria-label={spec.label}
      className={`switch${value ? " switch--on" : ""}`}
      onClick={() => ctx.onCommit(spec, !value)}
      disabled={disabled}
    >
      <span className="switch__thumb" aria-hidden="true" />
    </button>
  );
}

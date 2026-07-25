import type { FieldProps } from "./types";
import { DeferredInput } from "./DeferredInput";

export default function FloatField({ spec, ctx, disabled }: FieldProps) {
  const value = String(Number(ctx.getValue(spec) ?? 0));
  return (
    <DeferredInput
      value={value}
      disabled={disabled}
      inputMode="decimal"
      className="mono field-input--num"
      onCommit={(raw) => {
        const n = Number.parseFloat(raw);
        if (Number.isFinite(n)) ctx.onCommit(spec, n);
      }}
    />
  );
}

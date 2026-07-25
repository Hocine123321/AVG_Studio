import type { FieldProps } from "./types";
import { DeferredInput } from "./DeferredInput";

export default function IntField({ spec, ctx, disabled }: FieldProps) {
  const value = String(Number(ctx.getValue(spec) ?? 0));
  return (
    <DeferredInput
      value={value}
      disabled={disabled}
      inputMode="numeric"
      className="mono field-input--num"
      onCommit={(raw) => {
        const n = Number.parseInt(raw, 10);
        if (Number.isFinite(n)) ctx.onCommit(spec, n);
      }}
    />
  );
}

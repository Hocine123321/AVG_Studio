import type { FieldProps } from "./types";
import { DeferredInput } from "./DeferredInput";

export default function TextField({ spec, ctx, disabled }: FieldProps) {
  const value = String(ctx.getValue(spec) ?? "");
  return <DeferredInput value={value} disabled={disabled} onCommit={(raw) => ctx.onCommit(spec, raw)} />;
}

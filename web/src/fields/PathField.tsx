import type { FieldProps } from "./types";
import { DeferredInput } from "./DeferredInput";

/** A filesystem path. Plain text entry (no native browse dialog wired up
 * here - the only folder picker the app has, `window.pywebview`'s
 * `browse_folder`, is specific to the first-run library folder in
 * `Welcome.tsx` and picks directories, not the executables/files most
 * `type="path"` settings name). `.mono` gives it the same tabular-figures
 * treatment as every other data-shaped value in the app. */
export default function PathField({ spec, ctx, disabled }: FieldProps) {
  const value = String(ctx.getValue(spec) ?? "");
  return (
    <DeferredInput
      value={value}
      disabled={disabled}
      className="mono"
      onCommit={(raw) => ctx.onCommit(spec, raw)}
    />
  );
}

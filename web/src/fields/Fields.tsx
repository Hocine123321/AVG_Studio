import type { FieldProps } from "./types";
import { DeferredInput } from "./DeferredInput";
import Select from "../Select";

/**
 * The six ordinary field renderers, one per `SettingSpec.type` that isn't
 * `secret`. Each is a handful of lines whose only real difference is the
 * input element and how a raw string turns back into a value, so they live
 * together rather than in six files that would each be a single import and
 * a single component.
 *
 * `SecretField` stays in its own file on purpose: its value never enters
 * the pending-draft batch these six share, it talks to `/api/keys/{provider}`
 * directly, and it carries per-provider busy/error state (see `types.ts`'s
 * `SettingsCtx` for that split). `DeferredInput` likewise stays separate -
 * it is the shared input primitive four of these wrap, not a renderer.
 *
 * `index.tsx` is what maps a type to one of these; nothing here knows about
 * that map.
 */

// ── Bool Field ──────────────────────────────────────────────

/** A toggle switch. Commits immediately (there's nothing to "finish
 * typing") - it just joins the pending draft batch like every other
 * non-secret field; nothing hits the network until Save. */
export function BoolField({ spec, ctx, disabled }: FieldProps) {
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

// ── Choice Field ────────────────────────────────────────────

/** A closed set of options. Uses the app's own `Select` rather than a native
 * `<select>`: the native dropdown list is drawn by Windows and cannot be
 * styled, so it opened in OS grey with square corners in the middle of a
 * warm, rounded interface. See `Select.tsx`. */
export function ChoiceField({ spec, ctx, disabled }: FieldProps) {
  const value = String(ctx.getValue(spec) ?? "");
  // A bare "" in `choices` (e.g. `image_provider_backup`'s "leave unset to
  // never fall back") is `Select`'s only empty-string option in the whole
  // catalogue. Left as a raw string it normalises to an unlabelled blank
  // row and a blank closed trigger - "no backup" reads as a broken control,
  // not a deliberate choice. `ModelPicker.tsx` already labels its own ""
  // entry "Not set"; give every choice field the same treatment here rather
  // than changing the schema.
  const options = (spec.choices ?? []).map((choice) => (choice === "" ? { value: "", label: "Not set" } : choice));

  // A literal "custom" choice (Slice 29 - `visual_tone` already had this
  // entry sitting in its own `choices` with no way to act on it;
  // `font_family` gets one too) is an escape hatch, not a real value: the
  // Select shows "custom" whenever the actual stored value isn't one of
  // the OTHER choices, and a follow-up text field carries what the value
  // really is. One fix serves both fields rather than a font-specific
  // special case. `avg.core.settings_schema.validate_value` was fixed
  // alongside this - it used to reject anything not literally IN
  // `choices`, which would have silently 400'd every save from this new
  // text field (and already did for visual_tone, before this existed).
  const choices = spec.choices ?? [];
  const hasCustomEscape = choices.includes("custom");
  const isCustom = hasCustomEscape && !choices.filter((c) => c !== "custom").includes(value);

  return (
    <div className={hasCustomEscape ? "field-choice-with-custom" : undefined}>
      <Select
        value={isCustom ? "custom" : value}
        options={options}
        disabled={disabled}
        ariaLabel={spec.label}
        onChange={(next) => ctx.onCommit(spec, next === "custom" ? "" : next)}
      />
      {isCustom && (
        <DeferredInput
          value={value === "custom" ? "" : value}
          disabled={disabled}
          placeholder="Enter a custom value"
          onCommit={(raw) => ctx.onCommit(spec, raw)}
        />
      )}
    </div>
  );
}

// ── Text Field ──────────────────────────────────────────────

export function TextField({ spec, ctx, disabled }: FieldProps) {
  const value = String(ctx.getValue(spec) ?? "");
  return <DeferredInput value={value} disabled={disabled} onCommit={(raw) => ctx.onCommit(spec, raw)} />;
}

// ── Path Field ──────────────────────────────────────────────

/** A filesystem path. Plain text entry (no native browse dialog wired up
 * here - the only folder picker the app has, `window.pywebview`'s
 * `browse_folder`, is specific to the first-run library folder in
 * `Welcome.tsx` and picks directories, not the executables/files most
 * `type="path"` settings name). `.mono` gives it the same tabular-figures
 * treatment as every other data-shaped value in the app. */
export function PathField({ spec, ctx, disabled }: FieldProps) {
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

// ── Int Field ───────────────────────────────────────────────

export function IntField({ spec, ctx, disabled }: FieldProps) {
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

// ── Float Field ─────────────────────────────────────────────

export function FloatField({ spec, ctx, disabled }: FieldProps) {
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

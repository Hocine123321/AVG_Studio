import { useEffect, useState } from "react";
import Select from "./Select";
import { DeferredInput } from "./fields/DeferredInput";
import { getModelCache } from "./api";
import type { FieldProps } from "./fields/types";

/**
 * The model fields, backed by the working-models cache.
 *
 * `text_model_default`, `<phase>_model` and `<phase>_backup` used to be
 * blank text boxes: the user had to know a provider's exact model id, and a
 * typo only surfaced when a phase failed mid-run. This offers the
 * `provider/model` pairs a probe actually proved working
 * (`avg/core/model_cache.py`), which is the legacy app's behaviour.
 *
 * **When the cache is empty it falls back to the text box**, with a line
 * saying where to build it. The plan called for a disabled control here,
 * but disabling would take away a field that has always been editable and
 * leave a fresh install unable to set a model at all until a sweep runs -
 * a worse failure than an unhelpful blank. The prompt to build the cache
 * is the part worth keeping.
 *
 * The empty option is offered only for the per-phase slots, where empty
 * genuinely means "use the default text model". The default slot itself
 * has no such meaning, so it is not offered one.
 */
function optionsFrom(providers: Record<string, string[]>): string[] {
  return Object.entries(providers)
    .flatMap(([provider, models]) => models.map((model) => `${provider}/${model}`))
    .sort();
}

export default function ModelPicker({ spec, ctx, disabled }: FieldProps) {
  const value = String(ctx.getValue(spec) ?? "");
  const [providers, setProviders] = useState<Record<string, string[]> | null>(null);

  useEffect(() => {
    // A failed fetch is the same as an empty cache as far as this control
    // is concerned: fall back to typing, do not raise about it.
    getModelCache()
      .then((r) => setProviders(r.providers))
      .catch(() => setProviders({}));
  }, []);

  const options = optionsFrom(providers ?? {});
  const optional = spec.id !== "text_model_default";

  if (options.length === 0) {
    return (
      <div className="modelpicker">
        <DeferredInput
          value={value}
          disabled={disabled}
          onCommit={(raw) => ctx.onCommit(spec, raw)}
        />
        <p className="field-row__help">
          No models verified yet — build the model cache in App Health to pick from a list.
        </p>
      </div>
    );
  }

  // A value saved before the cache was built, or from a provider that has
  // since stopped answering, still has to be shown and kept. Adding it to
  // the list is how the control avoids silently changing a setting the
  // user chose.
  const items = [
    ...(optional ? [{ value: "", label: "Use the default text model" }] : []),
    ...options.map((o) => ({ value: o })),
    ...(value && !options.includes(value) ? [{ value, label: `${value} (not verified)` }] : []),
  ];

  return (
    <Select
      value={value}
      options={items}
      disabled={disabled}
      ariaLabel={spec.label}
      onChange={(next) => ctx.onCommit(spec, next)}
    />
  );
}

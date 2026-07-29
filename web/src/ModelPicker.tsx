import { useEffect, useState } from "react";
import Select from "./Select";
import { DeferredInput } from "./fields/DeferredInput";
import { getLiveList, type LiveEntry, type LiveKind, type LiveListResponse } from "./api";
import type { FieldProps } from "./fields/types";

/**
 * The model, image and voice fields, backed by the Live List.
 *
 * These used to be blank text boxes: the user had to know a provider's
 * exact model id or voice id, and a typo only surfaced when a phase failed
 * mid-run. The picker offers what the Live List says is worth offering -
 * ticked entries only, favourites first - and shows the note written
 * against an entry underneath the field, which is where a note about a
 * model is actually useful.
 *
 * **When nothing is ticked it falls back to the text box**, with a line
 * saying where to fix that. Disabling would take away a field that has
 * always been editable and leave a fresh install unable to set a model at
 * all until a sweep runs - a worse failure than an unhelpful blank.
 *
 * Two value shapes, because the settings mean two different things:
 *
 *   - text slots store `provider/model`, since the router has to know both
 *     (`avg.providers.text.router.parse_target`).
 *   - `image_model` and `voice_id` store the BARE id, because that is what
 *     the image and voice backends are handed. The picker filters those
 *     by whichever provider is selected beside them, so a Stable Horde
 *     model cannot be chosen while Pollinations is the provider.
 */

/** The sibling setting whose provider decides which entries apply. `null`
 * for text, where the value carries its own provider. */
const PROVIDER_FIELD: Record<LiveKind, string | null> = {
  text: null,
  image: "image_provider",
  voice: "voice_provider",
};

function kindOf(source: string | null | undefined): LiveKind {
  if (source === "live_list:image") return "image";
  if (source === "live_list:voice") return "voice";
  return "text";
}

export default function ModelPicker({ spec, ctx, disabled }: FieldProps) {
  const value = String(ctx.getValue(spec) ?? "");
  const [live, setLive] = useState<LiveListResponse | null>(null);

  useEffect(() => {
    // A failed fetch is the same as an empty list as far as this control
    // is concerned: fall back to typing, do not raise about it.
    getLiveList()
      .then(setLive)
      .catch(() => setLive(null));
  }, []);

  const kind = kindOf(spec.source);
  const providerField = PROVIDER_FIELD[kind];
  const provider = providerField ? ctx.valueById(providerField) : null;

  const entries: LiveEntry[] = (live?.[kind] ?? []).filter((entry) => {
    if (!entry.selected) return false;
    if (provider === null) return true;
    return entry.id.startsWith(`${provider}/`);
  });

  // Text keeps `provider/model`; the other two hand the backend a bare id.
  const optionFor = (entry: LiveEntry): string =>
    kind === "text" ? entry.id : entry.id.slice(entry.id.indexOf("/") + 1);

  const noteFor = entries.find((entry) => optionFor(entry) === value)?.note ?? "";

  if (entries.length === 0) {
    return (
      <div className="modelpicker">
        <DeferredInput value={value} disabled={disabled} onCommit={(raw) => ctx.onCommit(spec, raw)} />
        <p className="field-row__help">
          {provider
            ? `Nothing ticked for ${provider} — open the Live List on App health to choose what this offers.`
            : "Nothing ticked yet — open the Live List on App health to choose what this offers."}
        </p>
      </div>
    );
  }

  // A value saved before the list was built, or one whose entry has since
  // been unticked, still has to be shown and kept: adding it to the list
  // is how the control avoids silently changing a setting the user chose.
  const options = entries.map((entry) => ({ value: optionFor(entry) }));
  const known = options.some((option) => option.value === value);
  const items = [
    ...(spec.id === "text_model_default" || value ? [] : [{ value: "", label: "Not set" }]),
    ...options,
    ...(value && !known ? [{ value, label: `${value} (not in your Live List)` }] : []),
  ];

  return (
    <div className="modelpicker">
      <Select
        value={value}
        options={items}
        disabled={disabled}
        ariaLabel={spec.label}
        onChange={(next) => ctx.onCommit(spec, next)}
      />
      {noteFor && <p className="field-row__help">{noteFor}</p>}
    </div>
  );
}

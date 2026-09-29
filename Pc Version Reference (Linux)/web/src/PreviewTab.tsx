import { useState } from "react";
import PreviewPanel, { type PreviewKind } from "./PreviewPanel";
import Select from "./Select";
import type { SettingsCtx } from "./fields/types";

const KIND_OPTIONS: { value: PreviewKind; label: string }[] = [
  { value: "caption", label: "Captions" },
  { value: "overlay", label: "Overlay & cards" },
  { value: "image", label: "Images" },
  { value: "voice", label: "Voice" },
];

/**
 * The Quick panel's Preview tab (spec §3.3) - `PreviewPanel` moved out of
 * Settings' cramped form column and given a layout designed for it: the
 * frame is the subject, at the size the thing being previewed actually is,
 * with a kind picker beside it rather than the group-implied choice
 * Settings.tsx relied on.
 *
 * `PreviewPanel` itself takes `{ kind, valueOf, refreshKey }` and knows
 * nothing about its container, so `ctx.valueById` (the same
 * `useSettingsData()` instance `QuickSettingsTab` shares - decision #3 of
 * the plan) is all this needs to pass through, alongside the `refreshKey`
 * counter this Refresh button bumps.
 *
 * The money rule holds regardless of `refreshKey`: neither `VoicePreview`
 * nor `ImagePreview` has any EFFECT that reaches `confirm_paid: true` -
 * only their own "Generate a sample" button does, unchanged.
 * `VoicePreview` does read `refreshKey`, but only inside its free-provider
 * auto-refresh effect (see `PreviewPanel.tsx`'s own doc on the prop) - a
 * press does nothing for a paid voice provider, same as it always has.
 * `ImagePreview` doesn't accept `refreshKey` at all, so the button is
 * disabled outright on the Image tab rather than sit there looking live
 * and doing nothing.
 */
export default function PreviewTab({ ctx }: { ctx: SettingsCtx }) {
  const [kind, setKind] = useState<PreviewKind>("caption");
  // Bumped to force an immediate re-fetch rather than waiting for the
  // debounce - see PreviewPanel.tsx's `refreshKey` doc for exactly which
  // kinds and which of their effects it reaches.
  const [refreshKey, setRefreshKey] = useState(0);
  return (
    <div className="quickpanel-preview">
      <div className="quickpanel-preview__picker">
        <span className="field-row__label">Preview</span>
        <Select
          value={kind}
          options={KIND_OPTIONS}
          onChange={(v) => setKind(v as PreviewKind)}
          ariaLabel="Choose what to preview"
        />
        <button
          type="button"
          className="btn btn--sm"
          disabled={kind === "image"}
          title={kind === "image" ? "Image generation always costs a call - use \u201cGenerate a sample\u201d below" : undefined}
          onClick={() => setRefreshKey((k) => k + 1)}
        >
          Refresh
        </button>
      </div>
      <div className="quickpanel-preview__frame">
        <PreviewPanel kind={kind} valueOf={ctx.valueById} refreshKey={refreshKey} />
      </div>
    </div>
  );
}

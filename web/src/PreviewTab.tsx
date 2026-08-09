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
 * `PreviewPanel` itself is untouched - it already takes `{ kind, valueOf }`
 * and knows nothing about its container, so `ctx.valueById` (the same
 * `useSettingsData()` instance `QuickSettingsTab` shares - decision #3 of
 * the plan) is all this needs to pass through.
 *
 * The money rule travels with `PreviewPanel` unchanged: neither
 * `VoicePreview` nor `ImagePreview` has any effect that reaches
 * `confirm_paid: true` - only their own "Generate a sample" button does.
 * Switching `kind` here just changes WHICH free-or-paid component mounts;
 * it does not add a new path to either paid call.
 */
export default function PreviewTab({ ctx }: { ctx: SettingsCtx }) {
  const [kind, setKind] = useState<PreviewKind>("caption");
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
      </div>
      <div className="quickpanel-preview__frame">
        <PreviewPanel kind={kind} valueOf={ctx.valueById} />
      </div>
    </div>
  );
}

import type { PhaseStatus } from "./api";

/** Mirrors `avg.config.legacy_import.PHASE_ORDER` / `avg.pipeline.registry.
 * REGISTRY`. Sentence case, per the design rules, not the legacy app's
 * title case. This is known, deliberate frontend duplication until the
 * backend serves phase definitions; the nested Script display does not add
 * backend phase keys.
 *
 * Lives in its own module (rather than inside `Studio.tsx`, where it used
 * to live) purely so `NewTakePanel.tsx` - the re-roll phase picker, and the
 * rerun/keeps consequence preview - can share this one ordered list without
 * `Studio.tsx` having to export a non-component value alongside its default
 * component export (which breaks React Fast Refresh for that file).
 */
export interface PhaseDef {
  key: string;
  label: string;
  sub?: readonly PhaseDef[];
}

export type DisplayPhaseStatus = PhaseStatus | "partial";

export const PHASE_DEFS: readonly PhaseDef[] = [
  { key: "brief", label: "Brief" },
  {
    key: "script",
    label: "Script",
    sub: [
      { key: "draft", label: "Draft" },
      { key: "segmentation", label: "Segmentation" },
    ],
  },
  { key: "director1", label: "Director I" },
  { key: "visuals", label: "Visuals" },
  { key: "voice", label: "Voice & audio" },
  { key: "director2", label: "Director II" },
  { key: "screen_text", label: "Screen Text" },
  {
    // Two parts, as the old app had: the base encode, then the pass that
    // composites overlays, cards and captions onto it
    // (`avg/pipeline/phases/render.py`). Their state comes from
    // `render_substate`, derived off disk on the backend.
    key: "render",
    label: "Render",
    sub: [
      { key: "base", label: "Base video" },
      { key: "composite", label: "Overlays & captions" },
    ],
  },
];

/** The reroll API accepts only the eight backend phase keys. In particular,
 * Draft and Segmentation are display-only Script leaves and must never be
 * offered to `POST /takes` as `reroll_from`. */
export const PHASE_ORDER = PHASE_DEFS.map(({ key, label }) => ({ key, label }));

export function leavesOf(def: PhaseDef): readonly string[] {
  return def.sub?.map(({ key }) => key) ?? [def.key];
}

/** The group’s visual state, following the mockup's failure-first ordering.
 * `statuses` has frontend-only Script leaf entries filled by Studio; neither
 * `draft` nor `segmentation` is a backend phase or persisted phase record. */
export function groupStatus(
  def: PhaseDef,
  statuses: Readonly<Record<string, PhaseStatus | undefined>>,
): DisplayPhaseStatus {
  const leaves = leavesOf(def).map((key) => statuses[key] ?? "pending");
  if (leaves.some((status) => status === "failed")) return "failed";
  if (leaves.every((status) => status === "done")) return "done";
  if (leaves.some((status) => status === "running")) return "running";
  if (def.sub && leaves[0] === "done" && leaves[1] !== "done") return "partial";
  return "pending";
}

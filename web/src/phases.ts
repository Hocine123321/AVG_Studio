/** Mirrors `avg.config.legacy_import.PHASE_ORDER` / `avg.pipeline.registry.
 * REGISTRY`. Sentence case, per the design rules, not the legacy app's
 * title case.
 *
 * Lives in its own module (rather than inside `Studio.tsx`, where it used
 * to live) purely so `NewTakePanel.tsx` - the re-roll phase picker, and the
 * rerun/keeps consequence preview - can share this one ordered list without
 * `Studio.tsx` having to export a non-component value alongside its default
 * component export (which breaks React Fast Refresh for that file).
 */
export const PHASE_ORDER: { key: string; label: string }[] = [
  { key: "brief", label: "Brief" },
  { key: "script", label: "Script" },
  { key: "director1", label: "Director I" },
  { key: "visuals", label: "Visuals" },
  { key: "voice", label: "Voice & audio" },
  { key: "director2", label: "Director II" },
  { key: "captions", label: "Captions" },
  { key: "render", label: "Render" },
];

import type { CostTable, PhaseRecord, ProjectDoc, ScriptSubstate } from "./api";

/** Spend arithmetic over the table `GET /api/costs` serves.
 *
 * The numbers themselves live on the backend (`avg/core/costs.py`) and are
 * MAINTAINED ESTIMATES, not metered usage - nothing counts tokens, images
 * or audio seconds anywhere in this app. Everything this module returns is
 * an estimate and must be labelled as one wherever it is shown.
 *
 * This file is arithmetic only: it never hardcodes a price, so a table
 * change on the backend needs no edit here.
 */

/** What one project has cost so far: every phase that is done or running,
 * since work in progress has already been paid for.
 *
 * `script` is the one phase counted per-leaf. A running script, or a
 * finished-looking one whose `script_substate` is `partial`, has written
 * the narration and not the scenes - so it is charged for the draft only.
 * That is the same rule `avg.core.costs.spent` applies server-side.
 */
export function projectSpend(
  phases: Record<string, PhaseRecord>,
  table: CostTable,
  scriptSubstate: ScriptSubstate | undefined,
): number {
  let total = 0;
  for (const [key, record] of Object.entries(phases)) {
    if (record.status !== "done" && record.status !== "running") continue;
    if (key === "script") {
      total += table.leaf_cost.draft ?? 0;
      if (record.status !== "running" && scriptSubstate !== "partial") {
        total += table.leaf_cost.segmentation ?? 0;
      }
      continue;
    }
    for (const leaf of table.phase_leaves[key] ?? []) total += table.leaf_cost[leaf] ?? 0;
  }
  return total;
}

/** Spend across every project in the library. */
export function librarySpend(projects: readonly ProjectDoc[], table: CostTable): number {
  return projects.reduce((sum, p) => sum + projectSpend(p.phases, table, p.script_substate), 0);
}

/** What a whole video is estimated to cost at `lengthPreset`. An unknown
 * preset scales by 1 rather than throwing - this feeds a number on a
 * screen, and a settings value the table has not caught up with must not
 * break the Create screen. */
export function estimateForLength(table: CostTable, lengthPreset: string): number {
  return table.base_cost * (table.length_multiplier[lengthPreset] ?? 1);
}

/** Always three decimals: these figures are fractions of a cent, and
 * rounding them to two would show most phases as $0.00. */
export function formatUsd(amount: number): string {
  return `$${amount.toFixed(3)}`;
}

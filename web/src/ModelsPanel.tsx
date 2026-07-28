import { useEffect, useState } from "react";
import {
  apiGet,
  getCosts,
  type CostTable,
  type ProjectDoc,
  type ProjectsResponse,
  type SettingSpec,
} from "./api";
import { estimateForLength, formatUsd, librarySpend } from "./costs";

/** Which cost leaf each router phase's spend lands on.
 *
 * The router's phase keys and the cost table's display leaves are two
 * different vocabularies that happen to overlap: `script_draft` bills
 * `draft`, `script_segmentation` bills `segmentation`, and the rest are
 * named the same in both. This map is the only place that mismatch is
 * spelled out; everything else derives from the schema itself, so adding a
 * router phase means one entry here and one in `avg.core.costs`, not a new
 * row of hardcoded UI.
 */
const COST_LEAF: Record<string, string> = {
  brief: "brief",
  script_draft: "draft",
  script_segmentation: "segmentation",
  director1: "director1",
  director2: "director2",
};

const MODEL_SUFFIX = " — model";

interface Props {
  /** The schema's `models` group, in catalogue order. */
  specs: readonly SettingSpec[];
  /** Current value for a setting id, including unsaved edits - this is
   * Settings.tsx's own `getValue`, so the table reflects what the user is
   * about to save, not only what is on disk. */
  valueOf: (id: string) => string;
  lengthPreset: string;
}

/**
 * The mockup's Models tab: a spend card over a read-only summary of what
 * each phase is routed to (`F:\Downloads\organic_night_v2.html`, its
 * `MODEL_ROWS` table and `.spend-card`).
 *
 * Read-only on purpose. The editable controls are the schema-driven fields
 * rendered directly below this panel by `SettingsGroup` - duplicating them
 * as an editable grid would mean two controls writing one setting, and the
 * whole point of the settings catalogue is that a setting has exactly one
 * UI. What this adds is the overview those individual fields cannot give:
 * every phase's route on one line, next to what it costs.
 *
 * Every figure here is a MAINTAINED ESTIMATE from `avg/core/costs.py`, not
 * measured spend - hence the wording on the card, which should not be
 * softened into sounding like a bill.
 */
export default function ModelsPanel({ specs, valueOf, lengthPreset }: Props) {
  const [table, setTable] = useState<CostTable | null>(null);
  const [projects, setProjects] = useState<ProjectDoc[] | null>(null);

  useEffect(() => {
    // A failure here costs the overview, not the settings screen - the
    // fields below render and save either way.
    getCosts().then(setTable).catch(() => setTable(null));
    apiGet<ProjectsResponse>("/api/projects")
      .then((res) => setProjects(res.projects))
      .catch(() => setProjects(null));
  }, []);

  if (!table) return null;

  const rows = specs
    .filter((spec) => spec.id.endsWith("_model") && spec.id !== "text_model_default")
    .map((spec) => {
      const phase = spec.id.slice(0, -"_model".length);
      const fallback = valueOf("text_model_default");
      const model = valueOf(spec.id);
      return {
        phase,
        label: spec.label.endsWith(MODEL_SUFFIX) ? spec.label.slice(0, -MODEL_SUFFIX.length) : spec.label,
        model: model || fallback,
        inherited: !model,
        backup: valueOf(`${phase}_backup`),
        action: valueOf(`${phase}_on_failure`),
        cost: table.leaf_cost[COST_LEAF[phase] ?? phase] ?? 0,
      };
    });

  const spend = projects ? librarySpend(projects, table) : null;

  return (
    <section className="models-panel" aria-label="Models and cost">
      <div className="spend-card">
        <div>
          <p className="lbl">Library spend</p>
          <b>{spend === null ? "—" : formatUsd(spend)}</b>
        </div>
        <div>
          <p className="lbl">Across</p>
          <b>{projects?.length ?? "—"}</b>
        </div>
        <div>
          <p className="lbl">Next video, {lengthPreset}</p>
          <b>{formatUsd(estimateForLength(table, lengthPreset))}</b>
        </div>
        <span className="spend-card__note">
          Estimated from a maintained price table, not metered usage. Completed and in-progress phases only.
        </span>
      </div>

      <div className="models-scroll">
        <table className="models">
          <thead>
            <tr>
              <th>Phase</th>
              <th>Model</th>
              <th>Backup</th>
              <th>If both fail</th>
              <th>Est. / video</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.phase}>
                <td>{row.label}</td>
                <td className="mono">
                  {row.model}
                  {row.inherited && <span className="models__inherited"> default</span>}
                </td>
                <td className="mono">{row.backup || "none"}</td>
                <td>
                  <span className={`chip ${row.action === "fall_back" ? "chip--ok" : "chip--bad"}`}>
                    {row.action === "fall_back" ? "fall back" : "stop"}
                  </span>
                </td>
                <td className="mono">{formatUsd(row.cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

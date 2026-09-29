import { useEffect, useState } from "react";
import {
  getChannels,
  getCosts,
  getHealth,
  getSettingsValues,
  type CostTable,
  type ProjectDoc,
} from "./api";
import { estimateForLength, formatUsd, librarySpend } from "./costs";

/**
 * The Overview row above the library: four numbers that each answer a
 * question the user would otherwise have to go and look for, and each links
 * into the screen that owns it. From the 3rd redesign.
 *
 * Every figure is read from a route that already exists - spend from the
 * maintained cost table, components from the health check, the active
 * channel from the settings values, storage from the library scan. Nothing
 * here computes a number of its own, so nothing here can disagree with the
 * screen it links to.
 *
 * A card whose data has not arrived shows an em dash rather than a zero: on
 * this screen a confident `$0.000` and "no answer yet" look identical, and
 * only one of them is true.
 */
export interface OverviewProps {
  projects: readonly ProjectDoc[];
  /** Total bytes across the library, from `GET /api/projects`. `null` until
   * the startup scan has finished summing it. */
  storageBytes: number | null;
  onGoSettings: () => void;
  onGoHealth: () => void;
  onGoChannels: () => void;
}

function formatBytes(n: number | null): string {
  if (n === null) return "—";
  if (n <= 0) return "0 MB";
  const mb = n / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${Math.round(mb)} MB`;
}

export default function Overview({
  projects,
  storageBytes,
  onGoSettings,
  onGoHealth,
  onGoChannels,
}: OverviewProps) {
  const [costs, setCosts] = useState<CostTable | null>(null);
  const [healthOk, setHealthOk] = useState<boolean | null>(null);
  const [channelLabel, setChannelLabel] = useState<string | null>(null);
  const [channelChips, setChannelChips] = useState<string[]>([]);

  useEffect(() => {
    // Each of these degrades on its own - one failing card must not take the
    // other three down, so no Promise.all here.
    getCosts().then(setCosts).catch(() => setCosts(null));
    getHealth().then((h) => setHealthOk(h.ok)).catch(() => setHealthOk(null));

    Promise.all([getSettingsValues(), getChannels()])
      .then(([values, channels]) => {
        const active = channels.channels.find((c) => c.id === values.active_channel_id);
        setChannelLabel(active?.label ?? null);
        setChannelChips(
          [values.channel.video_form, values.channel.length_preset, values.channel.visual_tone]
            .filter((v): v is string => typeof v === "string" && v.length > 0)
            .map((v) => v.replace(/_/g, " ")),
        );
      })
      .catch(() => {
        setChannelLabel(null);
        setChannelChips([]);
      });
  }, []);

  const spend = costs ? librarySpend(projects, costs) : null;
  const nextEstimate = costs ? estimateForLength(costs, "60s") : null;

  return (
    <>
      <p className="lbl" style={{ margin: "0 0 12px" }}>
        Overview
      </p>
      <div className="overview">
        <div className="ovcard">
          <p className="ovcard__label">Library spend</p>
          <b className="ovcard__value ovcard__value--accent">{spend === null ? "—" : formatUsd(spend)}</b>
          <p className="ovcard__note">
            Across {projects.length} video{projects.length === 1 ? "" : "s"}
            {nextEstimate !== null && ` · next video est. ${formatUsd(nextEstimate)}`}
          </p>
          <button type="button" className="ovcard__link" onClick={onGoSettings}>
            Models &amp; cost →
          </button>
        </div>

        <div className="ovcard">
          <p className="ovcard__label">Components</p>
          <span
            className={healthOk === null ? "chip" : healthOk ? "chip chip--ok" : "chip chip--bad"}
          >
            {healthOk === null ? "Not checked" : healthOk ? "All present" : "Needs attention"}
          </span>
          <p className="ovcard__note">FFmpeg, Pillow, moviepy — checked on this machine.</p>
          <button type="button" className="ovcard__link" onClick={onGoHealth}>
            Diagnostics →
          </button>
        </div>

        <div className="ovcard">
          <p className="ovcard__label">Active channel</p>
          <b className="ovcard__value ovcard__value--name">{channelLabel ?? "None active"}</b>
          <div className="ovcard__chips">
            {channelChips.map((chip) => (
              <span key={chip} className="chip">
                {chip}
              </span>
            ))}
          </div>
          <button type="button" className="ovcard__link" onClick={onGoChannels}>
            Manage channels →
          </button>
        </div>

        <div className="ovcard">
          <p className="ovcard__label">Storage used</p>
          <b className="ovcard__value">{formatBytes(storageBytes)}</b>
          <p className="ovcard__note">Across every take · never auto-pruned</p>
        </div>
      </div>
    </>
  );
}

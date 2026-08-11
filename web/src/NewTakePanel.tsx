import { useEffect, useMemo, useState } from "react";
import {
  createTake,
  getChannels,
  getSettingsSchema,
  getSettingsValues,
  previewTake,
  type SettingSpec,
  type Take,
  type TakePreviewResult,
} from "./api";
import SettingsGroup from "./SettingsGroup";
import type { SettingsCtx } from "./fields/types";
import { PHASE_ORDER } from "./phases";
import Select from "./Select";

/** Nicer display names for the handful of groups a per-video override can
 * ever land in - `scope="channel"` settings only span these six (see
 * `avg.core.settings_schema`), unlike `Settings.tsx`'s full table. */
const GROUP_LABELS: Record<string, string> = {
  video: "Video format",
  captions: "Captions",
  voice: "Voice",
  images: "Images",
  render: "Render",
  creative: "Creative",
};

function labelForGroup(group: string): string {
  return GROUP_LABELS[group] ?? group.charAt(0).toUpperCase() + group.slice(1);
}

function labelForPhase(key: string): string {
  return PHASE_ORDER.find((p) => p.key === key)?.label ?? key;
}

function formatEstimate(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  if (m === 0) return `${s}s`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** Same trick every other screen in this app uses to surface the server's
 * own refusal sentence instead of the raw "POST ... failed: 4xx - {...}"
 * wrapper. */
function extractDetail(message: string): string {
  const match = message.match(/"detail"\s*:\s*"([^"]*)"/);
  return match ? match[1] : message;
}

export interface NewTakePanelProps {
  projectId: string;
  /** The take being branched from - stays visible in the strip behind this
   * slide-over (see this file's own header comment), and supplies the
   * baseline value for every field a user hasn't touched yet. */
  branchFromTake: Take;
  /** Pre-selects the re-roll picker. Set when the panel was opened from a
   * phase row's "Run again" rather than from the take strip's "New take" -
   * the user has already named the phase they mean, and being made to pick
   * it again from a dropdown would be asking twice. Must be a backend
   * phase key; see `phases.ts`. */
  initialRerollFrom?: string | null;
  onClose: () => void;
  onCreated: (take: Take) => void;
}

/**
 * The new-take slide-over: change a setting, or pick a phase to re-roll, or
 * both - the consequence preview updates live either way. Reuses Slice 2's
 * schema-driven field renderers (`fields/`, via `SettingsGroup`) unchanged;
 * this component only supplies a `SettingsCtx` whose `onCommit` writes into
 * a local `overrides` draft instead of `Settings.tsx`'s batched save.
 *
 * Only `scope="channel"` settings are offered - the same set
 * `avg.api.routes_takes._validate_override_ids` accepts; nothing here lets
 * a user attempt an app-scoped override the route would 400 on anyway.
 *
 * One terracotta action here: *Create take*. Everything else (the re-roll
 * picker, every field, Cancel) is deliberately unaccented.
 */
export default function NewTakePanel({
  projectId,
  branchFromTake,
  initialRerollFrom = null,
  onClose,
  onCreated,
}: NewTakePanelProps) {
  const [schema, setSchema] = useState<SettingSpec[] | null>(null);
  const [channelValues, setChannelValues] = useState<Record<string, unknown>>({});
  const [activeChannelId, setActiveChannelId] = useState<string | null>(null);
  const [channelLabel, setChannelLabel] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [overrides, setOverrides] = useState<Record<string, unknown>>({});
  const [rerollFrom, setRerollFrom] = useState<string | null>(initialRerollFrom);

  const [preview, setPreview] = useState<TakePreviewResult | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getSettingsSchema(), getSettingsValues(), getChannels()])
      .then(([schemaRes, valuesRes, channelsRes]) => {
        setSchema(schemaRes.settings);
        setChannelValues(valuesRes.channel);
        setActiveChannelId(valuesRes.active_channel_id);
        setChannelLabel(
          valuesRes.active_channel_id
            ? (channelsRes.channels.find((c) => c.id === valuesRes.active_channel_id)?.label ?? null)
            : null,
        );
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : String(err)));
  }, []);

  // Close on Escape - the one keyboard affordance a slide-over needs beyond
  // its own Cancel button.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const hasChange = Object.keys(overrides).length > 0 || rerollFrom !== null;

  // The live consequence preview - debounced so a fast typist (a text
  // field, style_lock) doesn't fire one request per keystroke. Computes
  // only (`POST .../takes/preview` creates nothing) - safe to call on
  // every edit.
  useEffect(() => {
    if (!hasChange) {
      setPreview(null);
      setPreviewError(null);
      setPreviewLoading(false);
      return;
    }
    setPreviewLoading(true);
    const handle = setTimeout(() => {
      previewTake(projectId, { overrides, reroll_from: rerollFrom })
        .then((res) => {
          setPreview(res);
          setPreviewError(null);
        })
        .catch((err) => {
          setPreview(null);
          setPreviewError(extractDetail(err instanceof Error ? err.message : String(err)));
        })
        .finally(() => setPreviewLoading(false));
    }, 300);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overrides, rerollFrom, projectId]);

  function getValue(spec: SettingSpec): unknown {
    if (Object.prototype.hasOwnProperty.call(overrides, spec.id)) return overrides[spec.id];
    const snapshot = branchFromTake.settings_snapshot;
    if (snapshot && Object.prototype.hasOwnProperty.call(snapshot, spec.id)) return snapshot[spec.id];
    if (Object.prototype.hasOwnProperty.call(channelValues, spec.id)) return channelValues[spec.id];
    return spec.default;
  }

  function isDirty(id: string): boolean {
    return Object.prototype.hasOwnProperty.call(overrides, id);
  }

  function onCommit(spec: SettingSpec, value: unknown) {
    setCreateError(null);
    setOverrides((o) => ({ ...o, [spec.id]: value }));
  }

  const ctx: SettingsCtx = {
    getValue,
    // The take panel edits channel-scoped settings only, and the picker
    // uses this to read whichever provider is selected beside it - which
    // in this panel is either an override the user has just made or the
    // channel's own saved value, exactly what `getValue` resolves.
    valueById: (id) => {
      const found = (schema ?? []).find((candidate) => candidate.id === id);
      return found ? String(getValue(found) ?? "") : "";
    },
    isDirty,
    onCommit,
    activeChannelId,
    channelLabel,
    // This panel edits per-take OVERRIDES, never the channel document
    // itself - ownership (Slice 19 Task 8/9) governs where the Settings
    // screen's fields read a channel's own values from, and doesn't apply
    // to an override lane. `scope: "channel"` + `isOwned` always `true`
    // together mean every field here renders editable, never the
    // disabled/"take ownership" treatment - applying Settings' own
    // inheritance rule to a control that isn't Settings would be wrong.
    scope: "channel",
    isOwned: () => true,
    onReleaseOwnership: () => {},
    // No `scope="channel"` setting is ever `type="secret"` (see
    // `avg.core.settings_schema`), so `Field`/`SecretField` never actually
    // reaches these - present only to satisfy `SettingsCtx`'s shape.
    keys: {},
    keyBusy: {},
    keyErrors: {},
    onSetKey: () => {},
    onDeleteKey: () => {},
  };

  const groups = useMemo(() => {
    const order: string[] = [];
    const byGroup = new Map<string, SettingSpec[]>();
    for (const spec of schema ?? []) {
      if (spec.scope !== "channel") continue;
      if (!byGroup.has(spec.group)) {
        byGroup.set(spec.group, []);
        order.push(spec.group);
      }
      byGroup.get(spec.group)!.push(spec);
    }
    return order.map((group) => ({ group, label: labelForGroup(group), specs: byGroup.get(group) ?? [] }));
  }, [schema]);

  async function handleCreate() {
    if (!hasChange || creating) return;
    setCreating(true);
    setCreateError(null);
    try {
      const take = await createTake(projectId, { overrides, reroll_from: rerollFrom });
      onCreated(take);
    } catch (err) {
      setCreateError(extractDetail(err instanceof Error ? err.message : String(err)));
      setCreating(false);
    }
  }

  return (
    <div className="take-panel-overlay" onClick={onClose}>
      <aside
        className="take-panel"
        role="dialog"
        aria-modal="true"
        aria-label="New take"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="take-panel__head">
          <div style={{ minWidth: 0 }}>
            <div className="home__kicker">Branching from {branchFromTake.label}</div>
            <h2 className="take-panel__title">New take</h2>
          </div>
          <button type="button" className="take-panel__close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <div className="take-panel__body">
          <section className="take-panel__reroll">
            <label className="lbl" htmlFor="take-panel-reroll">
              Re-roll a phase
            </label>
            {/* PHASE_ORDER intentionally excludes the display-only Draft /
                Segmentation leaves: the backend accepts only REGISTRY keys. */}
            <Select
              id="take-panel-reroll"
              value={rerollFrom ?? ""}
              options={[{ value: "", label: "No re-roll" }, ...PHASE_ORDER.map((p) => ({ value: p.key, label: p.label }))]}
              onChange={(next) => setRerollFrom(next || null)}
              ariaLabel="Re-roll a phase"
            />
            <p className="field-row__help">
              {/* Says what the route actually does. `reroll_from` is a START
                  phase: `_build_plan` keeps everything before it and re-runs
                  it and everything after. The old wording here promised one
                  phase in isolation, which nothing implements. */}
              Re-run this phase and everything after it, with nothing else changed — for when a result
              was weak and deserves another roll.
            </p>
          </section>

          {activeChannelId === null && (
            <p className="take-panel__warning">No channel is active — creating a take will fail until one is.</p>
          )}

          {loadError && <p className="home__empty">Could not load settings: {loadError}</p>}
          {!schema && !loadError && <p className="home__empty">Loading…</p>}

          {schema &&
            groups.map((g) => <SettingsGroup key={g.group} title={g.label} specs={g.specs} ctx={ctx} />)}
        </div>

        <div className="take-panel__preview">
          {previewError && <p className="take-panel__error">{previewError}</p>}
          {!previewError && preview && (
            <>
              <p>
                <strong>Re-runs</strong> {preview.rerun.map(labelForPhase).join(", ")}
              </p>
              {preview.keeps.length > 0 && (
                <p>
                  <strong>Keeps</strong> {preview.keeps.map(labelForPhase).join(", ")}
                </p>
              )}
              {preview.estimate_seconds !== null && (
                <p className="take-panel__estimate mono">About {formatEstimate(preview.estimate_seconds)}</p>
              )}
            </>
          )}
          {!preview && !previewError && (
            <p className="take-panel__hint">
              {previewLoading ? "Working it out…" : "Change a setting or pick a phase to re-roll."}
            </p>
          )}
        </div>

        <footer className="take-panel__footer">
          {createError && <p className="take-panel__error">{createError}</p>}
          <button type="button" className="btn btn--ghost" onClick={onClose} disabled={creating}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn--primary"
            onClick={handleCreate}
            disabled={creating || !hasChange}
          >
            {creating ? "Creating…" : "Create take"}
          </button>
        </footer>
      </aside>
    </div>
  );
}

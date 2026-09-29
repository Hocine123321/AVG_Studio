import { useEffect, useState } from "react";
import { artifactFileUrl, artifactUrl, getTakeArtifactList } from "../api";
import VoicePlayer from "../VoicePlayer";

/**
 * One body per phase, reading that phase's real output.
 *
 * Every phase has always written files; nothing read them back, so six of
 * the eight rendered the word "Done.". Each component here fetches exactly
 * the artifacts its phase owns (`avg/core/artifacts.py` is the table) and
 * shows them as what they are - a brief, a script, prompts, images, audio,
 * motion, caption cards - rather than as a JSON dump.
 *
 * A missing file is a stated fact, never an error: a phase that failed
 * halfway leaves some of its artifacts and not others.
 */

interface BodyProps {
  projectId: string;
  takeId: number;
  /** From `GET .../artifacts`: which keys exist, and how big. */
  files: Record<string, number>;
}

/** Fetch one JSON artifact. `null` while loading, and on any failure -
 * this view must not raise about a file it cannot read. */
function useArtifactJson<T>(projectId: string, takeId: number, key: string, enabled: boolean): T | null {
  const [data, setData] = useState<T | null>(null);
  useEffect(() => {
    if (!enabled) {
      setData(null);
      return;
    }
    let cancelled = false;
    fetch(artifactUrl(projectId, takeId, key))
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (!cancelled) setData(j as T);
      })
      .catch(() => {
        if (!cancelled) setData(null);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, takeId, key, enabled]);
  return data;
}

/** The same, for a plain-text artifact. */
function useArtifactText(projectId: string, takeId: number, key: string, enabled: boolean): string | null {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled) {
      setText(null);
      return;
    }
    let cancelled = false;
    fetch(artifactUrl(projectId, takeId, key))
      .then((r) => (r.ok ? r.text() : null))
      .then((t) => {
        if (!cancelled) setText(t);
      })
      .catch(() => {
        if (!cancelled) setText(null);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, takeId, key, enabled]);
  return text;
}

function Missing({ what }: { what: string }) {
  return <p className="artifact-empty">No {what} in this take.</p>;
}

function fieldValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map((v) => (typeof v === "string" ? v : JSON.stringify(v))).join(", ");
  return JSON.stringify(value, null, 2);
}

// ── Brief ───────────────────────────────────────────────────

export function BriefBody({ projectId, takeId, files }: BodyProps) {
  const brief = useArtifactJson<Record<string, unknown>>(projectId, takeId, "brief", "brief" in files);
  if (!("brief" in files)) return <Missing what="brief" />;
  if (!brief) return <p className="artifact-empty">Loading the brief…</p>;

  return (
    <dl className="artifact-fields">
      {Object.entries(brief).map(([field, value]) => (
        <div key={field} className="artifact-fields__row">
          <dt>{field.replace(/_/g, " ")}</dt>
          <dd>{fieldValue(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

// ── Script ──────────────────────────────────────────────────

interface Scene {
  index?: number;
  text?: string;
  image_prompt?: string;
  label?: string;
}

export function ScriptBody({ projectId, takeId, files }: BodyProps) {
  const narration = useArtifactText(projectId, takeId, "narration", "narration" in files);
  const scenes = useArtifactJson<Scene[]>(projectId, takeId, "scenes", "scenes" in files);

  if (!("narration" in files) && !("scenes" in files)) return <Missing what="script" />;

  // Mirrors avg.pipeline.phases.writing.script_substate's own three
  // states (pending/partial/done), derived the same way it is server-side
  // - from which of these two files exist - rather than a new field: this
  // component already has `files`, and Pass 2 (segmentation) can genuinely
  // take a while, so "narration exists, scenes doesn't yet" deserves its
  // own message instead of silently showing an empty scenes section.
  const partial = ("narration" in files) && !("scenes" in files);

  return (
    <div className="artifact-stack">
      {partial && (
        <p className="lbl">
          <span className="dot pulse" style={{ background: "var(--accent)" }} aria-hidden="true" />
          Draft written — segmenting into scenes…
        </p>
      )}
      {Array.isArray(scenes) && scenes.length > 0 && (
        <>
          <p className="lbl">{scenes.length} scenes</p>
          {/* The same `.artifact-table` Director I and II use. The scene
              texts already contain every word of the narration, so this is
              the script - the prose below is the same words again, which is
              why it is folded away rather than printed at full height. */}
          <table className="artifact-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Text</th>
                <th>Label</th>
                <th>Image prompt</th>
              </tr>
            </thead>
            <tbody>
              {scenes.map((scene, i) => (
                <tr key={scene.index ?? i}>
                  <td className="mono">{scene.index ?? i + 1}</td>
                  <td>{scene.text || "—"}</td>
                  <td>{scene.label ? <span className="chip">{scene.label}</span> : "—"}</td>
                  <td>{scene.image_prompt || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {narration && (
        <details className="artifact-disclosure">
          <summary>Narration</summary>
          {/* Prose, because this is what the voice phase reads aloud. */}
          <pre className="artifact-prose">{narration}</pre>
        </details>
      )}
    </div>
  );
}

// ── Director I ──────────────────────────────────────────────

interface VideoMap {
  image_count?: number;
  notes?: string;
  scenes?: { scene?: number; role?: string; beat?: string; visual_intent?: string }[];
  zoom_words?: unknown[];
}

export function Director1Body({ projectId, takeId, files }: BodyProps) {
  const map = useArtifactJson<VideoMap>(projectId, takeId, "video_map", "video_map" in files);
  if (!("video_map" in files)) return <Missing what="image plan" />;
  if (!map) return <p className="artifact-empty">Loading the image plan…</p>;

  const scenes = map.scenes ?? [];
  return (
    <div className="artifact-stack">
      <p className="lbl">
        {map.image_count ?? scenes.length} images planned across {scenes.length} scenes
      </p>
      {map.notes && <p className="artifact-note">{map.notes}</p>}
      <table className="artifact-table">
        <thead>
          <tr>
            <th>Scene</th>
            <th>Role</th>
            <th>Beat</th>
            <th>Visual intent</th>
          </tr>
        </thead>
        <tbody>
          {scenes.map((scene, i) => (
            <tr key={scene.scene ?? i}>
              <td className="mono">{scene.scene ?? i + 1}</td>
              <td>{scene.role || "—"}</td>
              <td>{scene.beat || "—"}</td>
              <td>{scene.visual_intent || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Visuals ─────────────────────────────────────────────────

export function VisualsBody({ projectId, takeId, files }: BodyProps) {
  const [names, setNames] = useState<string[] | null>(null);

  useEffect(() => {
    if (!("images" in files)) {
      setNames([]);
      return;
    }
    let cancelled = false;
    getTakeArtifactList(projectId, takeId, "images")
      .then((r) => {
        if (!cancelled) setNames(r.files);
      })
      .catch(() => {
        if (!cancelled) setNames([]);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, takeId, files]);

  if (!("images" in files)) return <Missing what="images" />;
  if (names === null) return <p className="artifact-empty">Loading images…</p>;

  return (
    <div>
      <p className="lbl" style={{ marginBottom: 12 }}>
        {names.length} image{names.length === 1 ? "" : "s"}
      </p>
      <div className="shots">
        {names.map((name) => (
          <div key={name}>
            <div className="shot">
              <img className="washed" src={artifactFileUrl(projectId, takeId, "images", name)} alt="" loading="lazy" />
            </div>
            <div className="shot__caption">{name}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Voice ───────────────────────────────────────────────────

export function VoiceBody({ projectId, takeId, files }: BodyProps) {
  const has = (key: string) => key in files;
  if (!has("raw_voice") && !has("optimized_voice")) return <Missing what="narration audio" />;

  return (
    <div className="artifact-stack">
      {/* Two tracks, not one per scene: the phase assembles the whole
          narration and then a silence-trimmed version of it. The render
          uses the optimized one. */}
      {has("raw_voice") && (
        <section className="artifact-audio">
          <p className="lbl">Raw narration</p>
          <VoicePlayer src={artifactUrl(projectId, takeId, "raw_voice")} label="raw narration" />
        </section>
      )}
      {has("optimized_voice") && (
        <section className="artifact-audio">
          <p className="lbl">Optimized narration — this is what the render uses</p>
          <VoicePlayer src={artifactUrl(projectId, takeId, "optimized_voice")} label="optimized narration" />
        </section>
      )}
    </div>
  );
}

// ── Director II ─────────────────────────────────────────────

interface DirectionPlan {
  grade?: { look?: string };
  scenes?: {
    scene?: number;
    motion?: { type?: string; intensity?: number; ease?: string };
    transition_in?: string;
  }[];
}

export function Director2Body({ projectId, takeId, files }: BodyProps) {
  const plan = useArtifactJson<DirectionPlan>(projectId, takeId, "direction_plan", "direction_plan" in files);
  if (!("direction_plan" in files)) return <Missing what="motion plan" />;
  if (!plan) return <p className="artifact-empty">Loading the motion plan…</p>;

  const scenes = plan.scenes ?? [];
  return (
    <div className="artifact-stack">
      <p className="lbl">Grade: {plan.grade?.look ?? "—"}</p>
      <table className="artifact-table">
        <thead>
          <tr>
            <th>Scene</th>
            <th>Motion</th>
            <th>Intensity</th>
            <th>Ease</th>
            <th>Transition in</th>
          </tr>
        </thead>
        <tbody>
          {scenes.map((scene, i) => (
            <tr key={scene.scene ?? i}>
              <td className="mono">{scene.scene ?? i + 1}</td>
              <td>{scene.motion?.type ?? "—"}</td>
              <td className="mono">{scene.motion?.intensity ?? "—"}</td>
              <td>{scene.motion?.ease ?? "—"}</td>
              <td>{scene.transition_in ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Captions ────────────────────────────────────────────────

interface CardPlan {
  cards?: { scene?: number; text?: string; start?: number; end?: number; element?: string }[];
}

function seconds(value: number | undefined): string {
  if (value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(2)}s`;
}

export function CaptionsBody({ projectId, takeId, files }: BodyProps) {
  const plan = useArtifactJson<CardPlan>(projectId, takeId, "card_plan", "card_plan" in files);
  if (!("card_plan" in files)) return <Missing what="caption cards" />;
  if (!plan) return <p className="artifact-empty">Loading the caption cards…</p>;

  const cards = plan.cards ?? [];
  if (cards.length === 0) {
    // A real outcome, not a failure: cards come from `((question))`
    // markers in the script, and a script may contain none.
    return <p className="artifact-empty">No takeover cards — the script has no question markers.</p>;
  }

  const timed = cards.some((c) => c.start !== undefined);
  return (
    <div className="artifact-stack">
      <p className="lbl">
        {cards.length} card{cards.length === 1 ? "" : "s"}
        {timed ? "" : " — timings are added by the Captions phase"}
      </p>
      <ol className="artifact-cards">
        {cards.map((card, i) => (
          <li key={i} className="artifact-card">
            <span className="artifact-card__num">{card.scene ?? i + 1}</span>
            <div className="artifact-card__body">
              <p>{card.text}</p>
              <span className="mono artifact-card__meta">
                {seconds(card.start)} → {seconds(card.end)}
              </span>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

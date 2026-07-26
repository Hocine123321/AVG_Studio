import { useEffect, useMemo, useState } from "react";
import {
  createProject,
  getChannels,
  getSettingsSchema,
  getSettingsValues,
  type ProjectDoc,
  type SettingSpec,
} from "./api";

/** Same trick `App.tsx`/`Settings.tsx`/`Channels.tsx` use to surface the
 * server's own refusal sentence instead of the raw "POST ... failed: 4xx -
 * {...}" wrapper. */
function extractDetail(message: string): string {
  const match = message.match(/"detail"\s*:\s*"([^"]*)"/);
  return match ? match[1] : message;
}

/** The three channel-scoped, bounded-choice settings this screen shows as
 * overridable chips - "format, length, tone" per the brief, in that order.
 * `content_shape` ("personality") is deliberately not a chip here: it's
 * free text, not a bounded choice, so it doesn't fit a pill control - the
 * active channel still answers it, it's just not one of the three things
 * this screen lets you nudge for a single video. */
const CHIP_IDS: readonly string[] = ["video_form", "length_preset", "visual_tone"];
const CHIP_LABELS: Record<string, string> = {
  video_form: "Format",
  length_preset: "Length",
  visual_tone: "Tone",
};

/**
 * The Create wizard: one screen, topic -> confirm. `Studio` and `Transport`
 * already render whatever the pipeline broadcasts over `/ws/events` (see
 * their own files) - this component's only job is collecting a topic and
 * any per-video overrides, then handing off to `onCreated` so `App.tsx` can
 * navigate to Studio and let that existing wiring take over. No progress
 * bar, no polling, no second event listener lives here.
 *
 * The active channel already answers format/length/tone (and personality),
 * so this never re-asks those as a form - it shows the channel's current
 * answers as chips the user can nudge for this one video, via `overrides`,
 * which travel with `POST /api/projects` and are never written back to the
 * channel (see `api.ts`'s `createProject`).
 */
export default function Create({
  onCreated,
  onGoToChannels,
}: {
  onCreated: (project: ProjectDoc) => void;
  onGoToChannels: () => void;
}) {
  const [schema, setSchema] = useState<SettingSpec[] | null>(null);
  const [channelValues, setChannelValues] = useState<Record<string, unknown>>({});
  const [activeChannelId, setActiveChannelId] = useState<string | null>(null);
  const [channelLabel, setChannelLabel] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [topic, setTopic] = useState("");
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

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

  const specById = useMemo(() => {
    const map = new Map<string, SettingSpec>();
    for (const spec of schema ?? []) map.set(spec.id, spec);
    return map;
  }, [schema]);

  function valueFor(id: string): string {
    if (Object.prototype.hasOwnProperty.call(overrides, id)) return overrides[id];
    const fromChannel = channelValues[id];
    if (fromChannel !== undefined) return String(fromChannel);
    return String(specById.get(id)?.default ?? "");
  }

  function isOverridden(id: string): boolean {
    return Object.prototype.hasOwnProperty.call(overrides, id) && overrides[id] !== String(channelValues[id] ?? "");
  }

  async function handleStart() {
    const trimmed = topic.trim();
    if (!trimmed || starting) return;
    setStarting(true);
    setStartError(null);
    try {
      const project = await createProject(trimmed, overrides);
      onCreated(project);
    } catch (err) {
      setStartError(extractDetail(err instanceof Error ? err.message : String(err)));
      setStarting(false);
    }
  }

  if (loadError) {
    return (
      <div className="create">
        <p className="home__empty">Could not load: {loadError}</p>
      </div>
    );
  }

  if (!schema) {
    return (
      <div className="create">
        <p className="home__empty">Loading…</p>
      </div>
    );
  }

  if (!activeChannelId) {
    return (
      <div className="create">
        <header className="create__header">
          <div className="home__kicker">New video</div>
          <h1 className="home__title">What&rsquo;s it about?</h1>
        </header>
        <div className="create__empty">
          <p>
            No channel is active. A channel is what answers format, length, tone and personality for a video, so
            pick one first.
          </p>
          <button type="button" className="btn btn--primary" onClick={onGoToChannels}>
            Choose a channel
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="create">
      <header className="create__header">
        <div className="home__kicker">New video</div>
        <h1 className="home__title">What&rsquo;s it about?</h1>
      </header>

      <textarea
        className="create__topic"
        placeholder="A topic, a headline, a few sentences — whatever you've got."
        aria-label="Video topic"
        value={topic}
        autoFocus
        onChange={(e) => setTopic(e.target.value)}
      />

      <div className="create__chips">
        <span className="chip">Channel: {channelLabel ?? activeChannelId}</span>
        {CHIP_IDS.map((id) => {
          const spec = specById.get(id);
          if (!spec || !spec.choices) return null;
          return (
            <label key={id} className={`create-chip${isOverridden(id) ? " create-chip--dirty" : ""}`}>
              <span className="create-chip__label">{CHIP_LABELS[id] ?? spec.label}</span>
              <select
                className="create-chip__select"
                aria-label={`${CHIP_LABELS[id] ?? spec.label} for this video`}
                value={valueFor(id)}
                onChange={(e) => setOverrides((o) => ({ ...o, [id]: e.target.value }))}
              >
                {spec.choices.map((choice) => (
                  <option key={choice} value={choice}>
                    {choice}
                  </option>
                ))}
              </select>
            </label>
          );
        })}
      </div>
      <p className="create__note">Changes here apply to this video only — the channel itself is unchanged.</p>

      {startError && <p className="create__error">{startError}</p>}

      <div className="create__actions">
        <button
          type="button"
          className="btn btn--primary btn--lg"
          onClick={handleStart}
          disabled={!topic.trim() || starting}
        >
          {starting ? "Starting…" : "Start"}
        </button>
      </div>
    </div>
  );
}

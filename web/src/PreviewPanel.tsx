import { useEffect, useRef, useState } from "react";
import { previewCaption, previewImage, previewVoice } from "./api";

/** How long a settings change waits before it fires a free preview request -
 * long enough that dragging a slider or holding a key down settles before
 * anything is sent, short enough that the frame still reads as "live". The
 * two paid kinds (`voice` on ElevenLabs, `image`) never use this: they have
 * no debounced effect at all, only the button in `generateSample` below. */
const DEBOUNCE_MS = 350;

/** Mirrors `avg.media.preview.PAID_VOICE_PROVIDERS` - the ONE other place
 * this fact is spelled out. Duplicated rather than fetched because there is
 * no route that would exist only to answer this one question; the same
 * shape `phases.ts`'s own docstring already accepts for `REGISTRY`. Getting
 * this wrong in either direction is a real defect: too small and a paid
 * provider gets the free debounced effect (money on every keystroke), too
 * large and a free provider (Edge) is stuck behind an unnecessary button. */
const PAID_VOICE_PROVIDERS = new Set(["elevenlabs"]);

export type PreviewKind = "caption" | "voice" | "image";

interface Props {
  kind: PreviewKind;
  /** Settings.tsx's own `valueById` - draft-aware, so the preview reflects
   * an edit the instant it is made rather than waiting for Save. */
  valueOf: (id: string) => string;
}

/** Pulls FastAPI's `{"detail": "..."}` body out of the raw text `api.ts`'s
 * `blobUrl` throws. Deliberately NOT `Settings.tsx`'s own `extractDetail`:
 * that one unwraps the "METHOD path failed: status - {...}" shape the
 * shared `request()` wrapper produces, but `blobUrl` throws the response
 * body verbatim (see its `res.text()` fallback), so the shape here is
 * different and a shared helper would have to special-case one of the two
 * callers anyway. */
function errorMessage(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err);
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown };
    if (typeof parsed.detail === "string" && parsed.detail) return parsed.detail;
  } catch {
    // Not JSON - a network failure or a plain-text body. The raw message is
    // the best available answer either way.
  }
  return raw;
}

/** `"9:16"` -> `"9 / 16"`, the CSS `aspect-ratio` shorthand - same idiom
 * `ProjectCard.tsx` already uses for the same setting, so the preview frame
 * holds its shape from the first render rather than collapsing to nothing
 * until an image arrives. Falls back to the schema's own default when the
 * value doesn't parse, rather than producing a CSS property with no value. */
function aspectRatioCss(value: string): string {
  return /^\d+:\d+$/.test(value) ? value.replace(":", " / ") : "9 / 16";
}

/** The pixel size a caption preview is rendered at. Kept modest - large
 * enough that libass's text and spacing read true to the real render,
 * small enough that the ffmpeg call behind every debounced request stays
 * cheap. The frame is displayed at `width: 100%` of its column regardless
 * (`.preview-frame img` in tokens.css), so this only affects the request
 * cost and the exact pixel grid libass lays text out on, never the
 * on-screen size. */
function captionFrameSize(aspectRatio: string): { width: number; height: number } {
  const match = /^(\d+):(\d+)$/.exec(aspectRatio);
  const width = 540;
  if (!match) return { width, height: Math.round((width * 16) / 9) };
  const w = Number(match[1]);
  const h = Number(match[2]);
  return { width, height: Math.round((width * h) / w) };
}

/** One panel, dispatched by `kind` to the component that actually knows how
 * to build that kind's request body - the shapes are different enough
 * (dimensions and text for a caption, a provider/voice pair for voice, a
 * provider/model/ratio for an image) that one shared implementation would
 * be a pile of kind-specific branches masquerading as one component. What
 * IS shared is the debounce interval, the paid-provider set, and the frame
 * markup, all defined once above/below rather than per kind. */
export default function PreviewPanel({ kind, valueOf }: Props) {
  if (kind === "caption") return <CaptionPreview valueOf={valueOf} />;
  if (kind === "voice") return <VoicePreview valueOf={valueOf} />;
  return <ImagePreview valueOf={valueOf} />;
}

function CaptionPreview({ valueOf }: { valueOf: (id: string) => string }) {
  const fontName = valueOf("font_family") || "Figtree";
  const fontSize = Number(valueOf("captions_font_size")) || 0;
  const position = valueOf("captions_position") || "bottom";
  const aspectRatio = valueOf("aspect_ratio") || "9:16";
  const { width, height } = captionFrameSize(aspectRatio);

  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const urlRef = useRef<string | null>(null);
  // Bumped on every dependency change so a response that arrives after a
  // newer request has already started is discarded instead of clobbering
  // the frame with a stale settings combination - a caption preview that
  // briefly shows last generation's font while typing the next one would
  // be its own small lie.
  const generationRef = useRef(0);

  useEffect(() => {
    const generation = ++generationRef.current;
    const timer = window.setTimeout(() => {
      setBusy(true);
      setError(null);
      previewCaption({ width, height, font_name: fontName, font_size: fontSize, position })
        .then((blobUrl) => {
          if (generation !== generationRef.current) {
            URL.revokeObjectURL(blobUrl);
            return;
          }
          if (urlRef.current) URL.revokeObjectURL(urlRef.current);
          urlRef.current = blobUrl;
          setUrl(blobUrl);
        })
        .catch((err: unknown) => {
          if (generation !== generationRef.current) return;
          setError(errorMessage(err));
        })
        .finally(() => {
          if (generation === generationRef.current) setBusy(false);
        });
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [width, height, fontName, fontSize, position]);

  useEffect(
    () => () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    },
    [],
  );

  return (
    <section className="preview-panel" aria-label="Caption preview">
      <p className="lbl">Live preview</p>
      <div
        className={`preview-frame${busy ? " preview-frame--busy" : ""}`}
        style={{ aspectRatio: aspectRatioCss(aspectRatio) }}
      >
        {url && <img src={url} alt="Sample caption, burned by the render's own libass pass" />}
      </div>
      {error && <p className="field-row__blocked">{error}</p>}
    </section>
  );
}

function VoicePreview({ valueOf }: { valueOf: (id: string) => string }) {
  const provider = valueOf("voice_provider") || "elevenlabs";
  const voiceId = valueOf("voice_id");
  const paid = PAID_VOICE_PROVIDERS.has(provider);

  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const urlRef = useRef<string | null>(null);
  const generationRef = useRef(0);

  function replace(next: string | null) {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    urlRef.current = next;
    setUrl(next);
  }

  // Free path only. THIS is the effect the paid rule is about: it must
  // never run for a provider in `PAID_VOICE_PROVIDERS`, or a settings
  // change - not a press - would spend ElevenLabs characters. `paid` sits
  // first in the guard specifically so that flips to true before anything
  // else in this effect can run.
  useEffect(() => {
    if (paid || !voiceId) return;
    const generation = ++generationRef.current;
    const timer = window.setTimeout(() => {
      setBusy(true);
      setError(null);
      previewVoice({ provider, voice_id: voiceId })
        .then((blobUrl) => {
          if (generation !== generationRef.current) {
            URL.revokeObjectURL(blobUrl);
            return;
          }
          replace(blobUrl);
        })
        .catch((err: unknown) => {
          if (generation !== generationRef.current) return;
          setError(errorMessage(err));
        })
        .finally(() => {
          if (generation === generationRef.current) setBusy(false);
        });
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [paid, provider, voiceId]);

  useEffect(
    () => () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    },
    [],
  );

  // The ONLY path that can reach a paid provider's synthesis: an onClick
  // handler, never an effect. `confirm_paid: true` is set here and nowhere
  // else in this file.
  async function generateSample() {
    generationRef.current += 1;
    setBusy(true);
    setError(null);
    try {
      const blobUrl = await previewVoice({ provider, voice_id: voiceId, confirm_paid: true });
      replace(blobUrl);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="preview-panel" aria-label="Voice preview">
      <p className="lbl">Live preview</p>
      <div className={`preview-frame preview-frame--audio${busy ? " preview-frame--busy" : ""}`}>
        {url ? (
          <audio controls src={url} />
        ) : (
          <p className="preview-panel__empty">
            {paid
              ? "No sample generated yet."
              : voiceId
                ? busy
                  ? "Loading…"
                  : "No sample yet."
                : "Pick a voice to hear it."}
          </p>
        )}
      </div>
      {paid && (
        <button type="button" className="btn" onClick={generateSample} disabled={busy || !voiceId}>
          Generate a sample — spends ElevenLabs characters
        </button>
      )}
      {error && <p className="field-row__blocked">{error}</p>}
    </section>
  );
}

function ImagePreview({ valueOf }: { valueOf: (id: string) => string }) {
  const provider = valueOf("image_provider") || "pollinations";
  const model = valueOf("image_model");
  const aspectRatio = valueOf("aspect_ratio") || "9:16";

  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const urlRef = useRef<string | null>(null);

  useEffect(
    () => () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    },
    [],
  );

  // The ONLY path that can reach a real generation: an onClick handler.
  // There is no effect in this component that watches `provider`, `model`
  // or `aspectRatio` - changing any of them updates what the NEXT press
  // would generate, and does nothing on its own.
  async function generateSample() {
    setBusy(true);
    setError(null);
    try {
      const blobUrl = await previewImage({ provider, model, aspect_ratio: aspectRatio, confirm_paid: true });
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = blobUrl;
      setUrl(blobUrl);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="preview-panel" aria-label="Image preview">
      <p className="lbl">Live preview</p>
      <p className="preview-panel__empty">
        Generates one image from a fixed sample prompt - a calm landscape - never a scene from
        your video.
      </p>
      <div
        className={`preview-frame${busy ? " preview-frame--busy" : ""}`}
        style={{ aspectRatio: aspectRatioCss(aspectRatio) }}
      >
        {url && <img src={url} alt="AI-graphics sample, from a fixed prompt" />}
      </div>
      <button type="button" className="btn" onClick={generateSample} disabled={busy || !model}>
        Generate a sample image — costs one image
      </button>
      {error && <p className="field-row__blocked">{error}</p>}
    </section>
  );
}

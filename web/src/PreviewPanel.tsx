import { useEffect, useRef, useState } from "react";
import { previewCaption, previewImage, previewVoice } from "./api";

/**
 * Live Preview: what a setting will actually look and sound like, using the
 * code that will produce it (`docs/superpowers/specs/2026-07-31-slice-14-
 * live-preview-design.md`).
 *
 * Free previews (caption, and Edge voice) re-render as their settings
 * change, debounced - dragging a slider must not queue twenty ffmpeg calls.
 * Paid previews (ElevenLabs voice, any image provider) render nothing until
 * an explicit press: the backend refuses them without `confirm_paid`
 * regardless of what this component does, but the effect that watches
 * settings must never be the thing that calls them either - a keystroke
 * must never cost money on either side of that line.
 */

/** How long to wait after the last relevant keystroke before firing a free
 * preview. Long enough that a slider drag or a few keystrokes in a row
 * collapse into one call; short enough that it still reads as "live". */
const DEBOUNCE_MS = 350;

type Kind = "caption" | "voice" | "images";

export default function PreviewPanel({ kind, valueOf }: { kind: Kind; valueOf: (id: string) => string }) {
  if (kind === "caption") return <CaptionPreview valueOf={valueOf} />;
  if (kind === "voice") return <VoicePreview valueOf={valueOf} />;
  return <ImagePreview valueOf={valueOf} />;
}

/** Revokes `url` on unmount or replacement - a blob URL held past its use
 * leaks the memory backing it for the life of the page. */
function useRevokeOnReplace(url: string | null) {
  const previous = useRef<string | null>(null);
  useEffect(() => {
    if (previous.current && previous.current !== url) {
      URL.revokeObjectURL(previous.current);
    }
    previous.current = url;
  }, [url]);
  useEffect(
    () => () => {
      if (previous.current) URL.revokeObjectURL(previous.current);
    },
    [],
  );
}

function Frame({ busy, error, url, alt }: { busy: boolean; error: string | null; url: string | null; alt: string }) {
  return (
    <div className={`preview-frame${busy ? " preview-frame--busy" : ""}`}>
      {url && <img src={url} alt={alt} />}
      {!url && !busy && !error && <p className="preview-frame__empty">No preview yet.</p>}
      {error && <p className="preview-frame__error">{error}</p>}
    </div>
  );
}

function CaptionPreview({ valueOf }: { valueOf: (id: string) => string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fontName = valueOf("font_family") || "Figtree";
  const fontSizeRaw = Number(valueOf("captions_font_size"));
  const fontSize = fontSizeRaw > 0 ? fontSizeRaw : 64; // 0 means "auto-fit" - the preview needs a concrete number
  const position = valueOf("captions_position") || "bottom";

  useEffect(() => {
    let cancelled = false;
    setBusy(true);
    setError(null);
    const timer = window.setTimeout(() => {
      previewCaption({ width: 1080, height: 1920, font_name: fontName, font_size: fontSize, position })
        .then((blob) => {
          if (!cancelled) setUrl(blob);
        })
        .catch((err) => {
          if (!cancelled) setError(err instanceof Error ? err.message : String(err));
        })
        .finally(() => {
          if (!cancelled) setBusy(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [fontName, fontSize, position]);

  useRevokeOnReplace(url);

  return (
    <section className="models-panel" aria-label="Caption preview">
      <p className="lbl">Live preview</p>
      <Frame busy={busy} error={error} url={url} alt="Caption preview" />
    </section>
  );
}

function VoicePreview({ valueOf }: { valueOf: (id: string) => string }) {
  const provider = valueOf("voice_provider") || "elevenlabs";
  const voiceId = valueOf("voice_id");
  const isPaid = provider === "elevenlabs";

  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Edge is free, so it may re-render live exactly like the caption preview.
  // ElevenLabs never runs from this effect - only from the button below.
  useEffect(() => {
    if (isPaid) return;
    if (!voiceId) {
      setUrl(null);
      return;
    }
    let cancelled = false;
    setBusy(true);
    setError(null);
    const timer = window.setTimeout(() => {
      previewVoice({ provider, voice_id: voiceId })
        .then((blob) => {
          if (!cancelled) setUrl(blob);
        })
        .catch((err) => {
          if (!cancelled) setError(err instanceof Error ? err.message : String(err));
        })
        .finally(() => {
          if (!cancelled) setBusy(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [isPaid, provider, voiceId]);

  useRevokeOnReplace(url);

  async function generatePaidSample() {
    setBusy(true);
    setError(null);
    try {
      const blob = await previewVoice({ provider, voice_id: voiceId, confirm_paid: true });
      setUrl(blob);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="models-panel" aria-label="Voice preview">
      <p className="lbl">Live preview</p>
      {isPaid && (
        <div className="spend-card">
          <div>
            <p className="lbl">Sample</p>
            <b>Hear this voice</b>
          </div>
          <button type="button" className="btn" onClick={() => void generatePaidSample()} disabled={busy || !voiceId}>
            Generate a sample — spends ElevenLabs characters
          </button>
        </div>
      )}
      {busy && isPaid && <div className="preview-frame preview-frame--busy" aria-label="Generating sample" />}
      {url && <audio controls src={url} />}
      {error && <p className="preview-frame__error">{error}</p>}
      {!isPaid && !url && !busy && !error && <p className="preview-frame__empty">No preview yet.</p>}
    </section>
  );
}

function ImagePreview({ valueOf }: { valueOf: (id: string) => string }) {
  const provider = valueOf("image_provider") || "pollinations";
  const model = valueOf("image_model") || "flux";
  const aspectRatio = valueOf("aspect_ratio") || "9:16";

  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useRevokeOnReplace(url);

  // Every image provider spends money on every press - there is no free
  // branch here, unlike voice. This never runs from an effect.
  async function generateSample() {
    setBusy(true);
    setError(null);
    try {
      const blob = await previewImage({ provider, model, aspect_ratio: aspectRatio, confirm_paid: true });
      setUrl(blob);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="models-panel" aria-label="Image preview">
      <p className="lbl">Live preview</p>
      <p className="preview-frame__note">
        This is a fixed sample prompt, not a scene from your video — it shows what {provider} draws, not what your
        video will look like.
      </p>
      <div className="spend-card">
        <div>
          <p className="lbl">Sample</p>
          <b>See this model&rsquo;s style</b>
        </div>
        <button type="button" className="btn" onClick={() => void generateSample()} disabled={busy}>
          Generate a sample image — costs one image
        </button>
      </div>
      {(busy || url || error) && <Frame busy={busy} error={error} url={url} alt="Image sample" />}
    </section>
  );
}

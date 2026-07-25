import { useState } from "react";
import { saveSettings, scanFolder, type SettingsDoc } from "./api";

/**
 * pywebview's bridge from JS back into the Python process (see
 * `avg/shell.py`'s `_Api` and `js_api=` on `webview.create_window`). Only
 * present when this page is running inside the real desktop window - the
 * dev server (`tools/_dev_serve.py`) and a plain browser tab have no
 * `window.pywebview`, so every call site below treats it as optional.
 */
declare global {
  interface Window {
    pywebview?: { api?: { browse_folder?: () => Promise<string | null> } };
  }
}

type Step = "welcome" | "library" | "ready";

/**
 * The first-run welcome. Three full-window, centred steps, no wizard chrome
 * (no Next/Back bar, no progress dots) - deliberately not a port of the old
 * PowerShell app's six-question console wizard. The only real question is
 * "where does your library live", because everything else already has a
 * sane default or belongs to a later slice.
 *
 * Mounted by `App.tsx` in place of the whole app shell whenever
 * `GET /api/settings` reports `onboarded: false`. `onComplete` hands back
 * the freshly-saved document so `App` can flip straight to the library
 * without a second round trip.
 */
export default function Welcome({
  settings,
  onComplete,
}: {
  settings: SettingsDoc;
  onComplete: (outputRoot: string) => void;
}) {
  const [step, setStep] = useState<Step>("welcome");
  const [outputRoot, setOutputRoot] = useState(settings.suggestion.path);
  const [projectCount, setProjectCount] = useState(settings.suggestion.project_count);
  const [scanning, setScanning] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const [starting, setStarting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const canBrowse = typeof window !== "undefined" && !!window.pywebview?.api?.browse_folder;

  async function handleBrowse() {
    const browse = window.pywebview?.api?.browse_folder;
    if (!browse) return;
    setErrorMsg(null);
    setBrowsing(true);
    try {
      const picked = await browse();
      if (!picked) return;
      setOutputRoot(picked);
      setScanning(true);
      try {
        const res = await scanFolder(picked);
        setProjectCount(res.project_count);
      } finally {
        setScanning(false);
      }
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBrowsing(false);
    }
  }

  async function handleStart() {
    setErrorMsg(null);
    setStarting(true);
    try {
      const saved = await saveSettings(outputRoot);
      onComplete(saved.output_root);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
      setStarting(false);
    }
  }

  const sentence =
    projectCount > 0
      ? `Found ${projectCount} project${projectCount === 1 ? "" : "s"} in ${outputRoot}.`
      : `No projects yet in ${outputRoot}.`;

  return (
    <div className="welcome">
      <span className="blob blob--accent drift" style={{ right: -140, top: -160, width: 460, height: 460 }} />
      <span className="blob blob--sage" style={{ left: "34%", bottom: -120, width: 320, height: 320 }} />

      <div className="welcome__panel">
        {step === "welcome" && (
          <>
            <p className="welcome__kicker">Welcome</p>
            <h1 className="welcome__headline">Ideas in, videos out.</h1>
            <p className="welcome__lede">
              AVG Studio turns a topic into a finished short-form or long-form video, start to finish.
            </p>
            <div className="welcome__actions">
              <button type="button" className="btn btn--primary btn--lg" onClick={() => setStep("library")}>
                Let&rsquo;s go
              </button>
            </div>
          </>
        )}

        {step === "library" && (
          <>
            <p className="welcome__kicker">Your library</p>
            <h1 className="welcome__headline">Where your videos live</h1>
            <p className="welcome__lede">{sentence}</p>
            <p className="welcome__hint">Use this folder, or browse to a different one.</p>

            {errorMsg && <p className="welcome__error">{errorMsg}</p>}

            <div className="welcome__actions">
              <button
                type="button"
                className="btn btn--ghost"
                onClick={handleBrowse}
                disabled={!canBrowse || browsing}
                title={canBrowse ? undefined : "The folder picker is only available in the desktop app"}
              >
                {browsing ? "Waiting for picker…" : "Browse…"}
              </button>
              <button type="button" className="btn btn--primary btn--lg" onClick={() => setStep("ready")} disabled={scanning}>
                Continue
              </button>
            </div>
          </>
        )}

        {step === "ready" && (
          <>
            <p className="welcome__kicker">Ready</p>
            <h1 className="welcome__headline">All set</h1>
            <p className="welcome__lede">Your library is {outputRoot}.</p>
            <p className="welcome__lede">
              {projectCount > 0
                ? `${projectCount} project${projectCount === 1 ? "" : "s"} are already there.`
                : "It's empty for now - your first video will start it."}
            </p>

            {errorMsg && <p className="welcome__error">{errorMsg}</p>}

            <div className="welcome__actions">
              <button type="button" className="btn btn--primary btn--lg" onClick={handleStart} disabled={starting}>
                {starting ? "Starting…" : "Start"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

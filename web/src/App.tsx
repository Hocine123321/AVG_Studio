import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  apiGet,
  apiPost,
  getSettings,
  type PhaseRecord,
  type ProjectDoc,
  type ProjectsResponse,
  type SettingsDoc,
} from "./api";
import AppHealth from "./AppHealth";
import Channels from "./Channels";
import Create from "./Create";
import Home from "./Home";
import Rail from "./Rail";
import Settings from "./Settings";
import Studio from "./Studio";
import StatusBar from "./StatusBar";
import Transport from "./Transport";
import Welcome from "./Welcome";
import { useTheme } from "./theme";
import { useEvents } from "./useEvents";

type Screen =
  | { name: "home" }
  | { name: "project"; id: string }
  | { name: "create" }
  | { name: "channels" }
  | { name: "settings" }
  | { name: "health" };

function withPhase(project: ProjectDoc, key: string, patch: Partial<PhaseRecord>): ProjectDoc {
  const existing: PhaseRecord = project.phases[key] ?? { status: "pending" };
  return { ...project, phases: { ...project.phases, [key]: { ...existing, ...patch } } };
}

/**
 * Folds the most recent live WS event onto a project's cached phases, so
 * both the home grid and the Studio header reflect a render's outcome the
 * instant it happens rather than waiting on a fresh `GET /api/projects`.
 * Mirrors exactly what the backend's own `_apply_terminal_event` does to
 * its cached copy (`avg/api/app.py`) - in particular, a cancel reverts the
 * phase to "pending" on both sides, never inventing a "cancelled" status
 * that the server-side truth doesn't have.
 */
function applyLiveState(project: ProjectDoc, events: ReturnType<typeof useEvents>): ProjectDoc {
  const id = project.id;
  if (events.progress?.project_id === id) {
    return withPhase(project, events.phaseStarted?.project_id === id ? events.phaseStarted.phase : "render", {
      status: "running",
    });
  }
  if (events.phaseStarted?.project_id === id) {
    return withPhase(project, events.phaseStarted.phase, { status: "running" });
  }
  if (events.phaseFinished?.project_id === id) {
    return withPhase(project, events.phaseFinished.phase, {
      status: "done",
      duration_ms: events.phaseFinished.duration_ms,
      error: null,
    });
  }
  if (events.failure?.project_id === id) {
    return withPhase(project, events.failure.phase, { status: "failed", error: events.failure.message });
  }
  if (events.pipelineCancelled?.project_id === id) {
    return withPhase(project, events.pipelineCancelled.phase, { status: "pending", error: null });
  }
  if (events.finished?.project_id === id) {
    return { ...withPhase(project, "render", { status: "done", error: null }), master_output: events.finished.output };
  }
  if (events.cancelled?.project_id === id) {
    return withPhase(project, "render", { status: "pending", error: null });
  }
  return project;
}

/**
 * Pulls the JSON `detail` out of `api.ts`'s formatted error message (e.g. a
 * 409 body `{"detail": "a render is already running for project '...'"}`)
 * so the UI can show the server's actual refusal reason instead of the
 * whole "POST /api/... failed: 409 Conflict - {...}" wrapper. Falls back to
 * the raw message when the body isn't shaped like that.
 */
function extractDetail(message: string): string {
  const match = message.match(/"detail"\s*:\s*"([^"]*)"/);
  return match ? match[1] : message;
}

/**
 * Routing, the shared project list, and the one WebSocket for the whole
 * app. `Transport` is mounted here below the content, unconditionally, so
 * it renders on every screen (loading and error states included) - that is
 * the entire point of it: a running render must be ambient, never
 * something the user navigates back to check on.
 */
export default function App() {
  // Lifted here (not left inside Rail, where it lived before) so the
  // stored night/day choice applies to Welcome too - Rail doesn't mount at
  // all until onboarding is done, so if this stayed in Rail, a "day"
  // preference from a previous run would silently not apply to any of the
  // three welcome screens and only kick in once the user reached the
  // library.
  const { theme, toggle: toggleTheme } = useTheme();
  const [settings, setSettings] = useState<SettingsDoc | null>(null);
  const [projects, setProjects] = useState<ProjectDoc[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [screen, setScreen] = useState<Screen>({ name: "home" });
  const [startError, setStartError] = useState<string | null>(null);
  const events = useEvents();

  // First-run gate: onboarding state decides whether the welcome screen or
  // the real app shell renders at all, so it has to resolve before either
  // does. Nothing else in this effect depends on it - `getSettings` is
  // deliberately the very first network call the app makes.
  useEffect(() => {
    getSettings()
      .then(setSettings)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  /** The one place the project list is (re)loaded. Returned as a promise so
   * a caller that just changed something server-side - a bulk archive, say -
   * can await the refreshed truth instead of guessing at it locally. */
  const reloadProjects = useCallback(async () => {
    try {
      const res = await apiGet<ProjectsResponse>("/api/projects");
      setProjects(res.projects);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    if (!settings?.onboarded) return;
    void reloadProjects();
  }, [settings?.onboarded, reloadProjects]);

  async function startRender(id: string) {
    setStartError(null);
    try {
      await apiPost(`/api/projects/${id}/render`);
    } catch (err) {
      setStartError(extractDetail(err instanceof Error ? err.message : String(err)));
    }
  }

  function cancelRender(id: string) {
    // Best-effort: the render's own terminal event (or its absence) is the
    // honest source of truth for whether it actually stopped, not this
    // request's resolution.
    apiPost(`/api/projects/${id}/cancel`).catch(() => {});
  }

  function openProject(id: string) {
    setStartError(null);
    setScreen({ name: "project", id });
  }

  /** `Create`'s `onCreated` - the project already exists and its pipeline
   * is already running server-side (see `avg/api/app.py`'s `POST
   * /api/projects`) by the time this fires, so this only has to fold it
   * into the local cache (so Studio can find it without waiting on a fresh
   * `GET /api/projects`) and navigate there - the running pipeline shows up
   * through the same `/ws/events` wiring every other screen already uses. */
  function handleProjectCreated(project: ProjectDoc) {
    setProjects((prev) => (prev ? [project, ...prev] : [project]));
    setStartError(null);
    setScreen({ name: "project", id: project.id });
  }

  /** What "Run again" (a done project) and "Resume" (a failed one) do:
   * open Studio AND immediately start a fresh render - distinct from Play
   * / Open, which only navigate. */
  function openAndRender(id: string) {
    openProject(id);
    startRender(id);
  }

  // First-run gate. Nothing below this - not even the Rail or Transport,
  // per the brief's "no wizard chrome" - renders until onboarding either
  // resolves (skip straight through) or the welcome screen has run and
  // completed. A settings-load failure falls through to the ordinary error
  // panel below instead of getting stuck here forever.
  if (!error && settings === null) {
    return <div className="welcome welcome--loading" />;
  }
  if (settings && !settings.onboarded) {
    return (
      <Welcome
        settings={settings}
        onComplete={(outputRoot) => setSettings({ ...settings, onboarded: true, output_root: outputRoot })}
      />
    );
  }

  let content: ReactNode;
  if (error) {
    content = (
      <div className="home">
        <p className="home__empty">Could not load AVG Studio: {error}</p>
      </div>
    );
  } else if (projects === null) {
    content = (
      <div className="home">
        <p className="home__empty">Loading…</p>
      </div>
    );
  } else {
    const live = projects.map((p) => applyLiveState(p, events));
    if (screen.name === "project") {
      const project = live.find((p) => p.id === screen.id);
      content = project ? (
        <Studio
          project={project}
          progress={events.progress?.project_id === project.id ? events.progress : null}
          activePhase={events.phaseStarted?.project_id === project.id ? events.phaseStarted.phase : null}
          failure={events.failure?.project_id === project.id ? events.failure : null}
          cancelled={events.cancelled?.project_id === project.id ? events.cancelled : null}
          finished={events.finished?.project_id === project.id ? events.finished : null}
          startError={startError}
          onStartRender={() => startRender(project.id)}
          onCancel={() => cancelRender(project.id)}
        />
      ) : (
        <div className="home">
          <button type="button" className="btn" onClick={() => setScreen({ name: "home" })}>
            Back
          </button>
          <p className="home__empty">Unknown project: {screen.id}</p>
        </div>
      );
    } else if (screen.name === "create") {
      content = (
        <Create onCreated={handleProjectCreated} onGoToChannels={() => setScreen({ name: "channels" })} />
      );
    } else if (screen.name === "settings") {
      content = <Settings />;
    } else if (screen.name === "health") {
      content = <AppHealth />;
    } else if (screen.name === "channels") {
      content = <Channels />;
    } else {
      content = (
        <Home
          projects={live}
          progress={events.progress}
          onOpen={openProject}
          onRender={openAndRender}
          onCancel={cancelRender}
          onArchived={reloadProjects}
        />
      );
    }
  }

  return (
    <div className="app-shell">
      <Rail
        active={
          screen.name === "project"
            ? "studio"
            : screen.name === "create"
              ? "create"
              : screen.name === "channels"
                ? "channels"
                : screen.name === "settings"
                  ? "settings"
                  : screen.name === "health"
                    ? "health"
                    : "home"
        }
        onHome={() => setScreen({ name: "home" })}
        onCreate={() => setScreen({ name: "create" })}
        onChannels={() => setScreen({ name: "channels" })}
        onSettings={() => setScreen({ name: "settings" })}
        onHealth={() => setScreen({ name: "health" })}
        theme={theme}
        onToggleTheme={toggleTheme}
      />
      <div className="app-shell__main">
        {/* `key` on the screen name, not the content: remounting on a route
            change is what lets the enter transition actually run each time
            rather than only on first paint. */}
        <div className="app-shell__content screen-enter" key={screen.name}>
          {content}
        </div>
        <Transport events={events} projects={projects ?? []} onCancel={cancelRender} onOpen={openProject} />
        <StatusBar connected={events.connected} />
      </div>
    </div>
  );
}

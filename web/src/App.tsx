import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { apiGet, apiPost, type PhaseRecord, type ProjectDoc, type ProjectsResponse } from "./api";
import Home from "./Home";
import Rail from "./Rail";
import Studio from "./Studio";
import Transport from "./Transport";
import { useEvents } from "./useEvents";

type Screen = { name: "home" } | { name: "project"; id: string };

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
    return withPhase(project, "render", { status: "running" });
  }
  if (events.failure?.project_id === id) {
    return withPhase(project, "render", { status: "failed", error: events.failure.message });
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
  const [projects, setProjects] = useState<ProjectDoc[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [screen, setScreen] = useState<Screen>({ name: "home" });
  const [startError, setStartError] = useState<string | null>(null);
  const events = useEvents();

  useEffect(() => {
    apiGet<ProjectsResponse>("/api/projects")
      .then((res) => setProjects(res.projects))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

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

  /** What "Run again" (a done project) and "Resume" (a failed one) do:
   * open Studio AND immediately start a fresh render - distinct from Play
   * / Open, which only navigate. */
  function openAndRender(id: string) {
    openProject(id);
    startRender(id);
  }

  let content: ReactNode;
  if (error) {
    content = (
      <div className="home">
        <p className="home__empty">Could not load projects: {error}</p>
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
    } else {
      content = (
        <Home
          projects={live}
          progress={events.progress}
          onOpen={openProject}
          onRender={openAndRender}
          onCancel={cancelRender}
        />
      );
    }
  }

  return (
    <div className="app-shell">
      <Rail active={screen.name === "project" ? "studio" : "home"} onHome={() => setScreen({ name: "home" })} />
      <div className="app-shell__main">
        <div className="app-shell__content">{content}</div>
        <Transport events={events} projects={projects ?? []} onCancel={cancelRender} onOpen={openProject} />
      </div>
    </div>
  );
}

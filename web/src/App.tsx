import { useEffect, useState } from "react";
import { apiGet, type ProjectDoc, type ProjectsResponse } from "./api";
import Home from "./Home";

type Screen = { name: "home" } | { name: "project"; id: string };

/**
 * Routing only - no state management library, no router package (two
 * screens don't need one). `App` owns the project list and which screen is
 * showing; every screen-specific concern lives in that screen's own
 * component.
 */
export default function App() {
  const [projects, setProjects] = useState<ProjectDoc[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [screen, setScreen] = useState<Screen>({ name: "home" });

  useEffect(() => {
    apiGet<ProjectsResponse>("/api/projects")
      .then((res) => setProjects(res.projects))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  if (error) {
    return (
      <div className="home">
        <p className="home__empty">Could not load projects: {error}</p>
      </div>
    );
  }

  if (projects === null) {
    return (
      <div className="home">
        <p className="home__empty">Loading…</p>
      </div>
    );
  }

  if (screen.name === "project") {
    const project = projects.find((p) => p.id === screen.id);
    // The Studio view (Task 10) replaces this placeholder - Task 9 only
    // needs App to be able to leave the home grid and come back.
    return (
      <div className="home">
        <button type="button" className="btn" onClick={() => setScreen({ name: "home" })}>
          Back
        </button>
        <p className="home__empty">
          {project ? project.title : screen.id} - studio view arrives in Task 10.
        </p>
      </div>
    );
  }

  return <Home projects={projects} onOpen={(id) => setScreen({ name: "project", id })} />;
}

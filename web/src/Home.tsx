import type { ProjectDoc } from "./api";
import ProjectCard from "./ProjectCard";

export interface HomeProps {
  projects: ProjectDoc[];
  onOpen: (id: string) => void;
  onRender: (id: string) => void;
}

/** The project grid - the first screen the user sees. */
export default function Home({ projects, onOpen, onRender }: HomeProps) {
  return (
    <div className="home">
      <div className="home__header">
        <h2 className="home__title">Your videos</h2>
        <span className="home__count mono">{projects.length}</span>
      </div>
      {projects.length === 0 ? (
        <p className="home__empty">No projects yet.</p>
      ) : (
        <div className="project-grid">
          {projects.map((project) => (
            <ProjectCard key={project.id} project={project} onOpen={onOpen} onRender={onRender} />
          ))}
        </div>
      )}
    </div>
  );
}

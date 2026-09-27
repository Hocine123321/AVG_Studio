import SwiftUI

struct HomeView: View {
    @Environment(Store.self) private var store
    @State private var showArchived = false

    private var visible: [Project] { store.projects.filter { $0.archived == showArchived } }
    private let cols = [GridItem(.flexible(), spacing: 14), GridItem(.flexible(), spacing: 14)]

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                if !showArchived, let id = store.interruptedProjectID, let p = store.project(id) {
                    interruptedBanner(p).padding([.horizontal, .top], 16)
                }
                if visible.isEmpty {
                    ContentUnavailableView {
                        Label(showArchived ? "Nothing archived" : "No videos yet", systemImage: "film.stack")
                    } description: {
                        Text(showArchived ? "Archived videos show up here." : "Create your first video — everything renders right on your iPhone.")
                    } actions: {
                        if !showArchived {
                            Button("Create a video") { store.tab = .create }.buttonStyle(.glassProminent)
                        }
                    }
                    .padding(.top, 80)
                } else {
                    LazyVGrid(columns: cols, spacing: 14) {
                        ForEach(visible) { p in
                            NavigationLink(value: p.id) { ProjectCard(project: p) }
                                .buttonStyle(.plain)
                                .contextMenu {
                                    Button(p.archived ? "Unarchive" : "Archive", systemImage: "archivebox") { store.setArchived(p.id, !p.archived) }
                                    Button("Delete", systemImage: "trash", role: .destructive) { store.delete(p.id) }
                                }
                        }
                    }
                    .padding(16)
                }
            }
        }
        .screenBackground()
        .navigationTitle(showArchived ? "Archive" : "Library")
        .navigationDestination(for: String.self) { StudioView(projectID: $0) }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button(showArchived ? "Library" : "Archive", systemImage: showArchived ? "square.grid.2x2" : "archivebox") {
                    withAnimation(.spring) { showArchived.toggle() }
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button("New", systemImage: "plus") { store.tab = .create }
            }
        }
    }

    @ViewBuilder private func interruptedBanner(_ p: Project) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "exclamationmark.arrow.triangle.2.circlepath")
                .font(.title3).foregroundStyle(Theme.accent)
            VStack(alignment: .leading, spacing: 2) {
                Text("“\(p.title)” was interrupted").font(.subheadline.weight(.semibold))
                Text("The app closed mid-render. Resume from where it left off.").font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Button("Resume") {
                let next = PhaseID.allCases.first { p.phase($0).status != .done } ?? .render
                store.start(p.id, from: next)
                store.libraryPath = [p.id]
            }
            .buttonStyle(.glassProminent)
        }
        .glassCard()
    }
}

struct ProjectCard: View {
    @Environment(Store.self) private var store
    let project: Project

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            thumb
                .frame(height: 190)
                .frame(maxWidth: .infinity)
                .clipShape(.rect(cornerRadius: 18))
            Text(project.title).font(.subheadline.weight(.semibold)).lineLimit(2)
            if let job = store.jobs[project.id] {
                ProgressView(value: job.progress) { Text(job.phase.title).font(.caption2) }
                    .tint(Theme.accent)
            } else {
                StatusBadge(status: project.overallStatus)
            }
        }
        .padding(10)
        .glassEffect(.regular.interactive(), in: .rect(cornerRadius: 26))
    }

    @ViewBuilder private var thumb: some View {
        if let url = store.file(project, project.scenes.first(where: { $0.imageFile != nil })?.imageFile),
           let img = UIImage(contentsOfFile: url.path) {
            Image(uiImage: img).resizable().scaledToFill()
        } else {
            LinearGradient(colors: [Theme.accent.opacity(0.6), Theme.violet.opacity(0.6)], startPoint: .topLeading, endPoint: .bottomTrailing)
                .overlay(Image(systemName: "sparkles").font(.largeTitle).foregroundStyle(.white.opacity(0.8)))
        }
    }
}

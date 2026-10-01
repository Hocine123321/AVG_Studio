import SwiftUI

/// The mock's Library screen: `seg(['All','Running','Done','Archived'])`
/// replaces the old separate "Archive" toolbar toggle entirely - archived
/// projects are just one more filter option now, not a whole different
/// screen mode.
enum LibraryFilter: String, CaseIterable, Hashable {
    case all = "All", running = "Running", done = "Done", archived = "Archived"
}

struct HomeView: View {
    @Environment(Store.self) private var store
    @State private var filter: LibraryFilter = .all
    @State private var query = ""
    // The mock's `S.sm`/`S.sel` - Library's selection mode + which project
    // ids are checked, driving the floating `.selbar`.
    @State private var selectMode = false
    @State private var selected: Set<String> = []

    private var visible: [Project] {
        store.projects.filter { p in
            let statusMatch: Bool
            switch filter {
            case .all: statusMatch = !p.archived
            case .running: statusMatch = !p.archived && p.overallStatus == .running
            case .done: statusMatch = !p.archived && p.overallStatus == .done
            case .archived: statusMatch = p.archived
            }
            guard statusMatch else { return false }
            return query.isEmpty || p.title.localizedCaseInsensitiveContains(query)
        }
    }

    /// Groups `visible` into grid rows: a 16:9 project is the mock's
    /// `.pc.wide` (spans both columns, alone in its row); everything else
    /// pairs up two-per-row - `LazyVGrid` has no per-cell column-span, so
    /// this rebuilds the same effect as plain HStack rows.
    private var rows: [[Project]] {
        var out: [[Project]] = []
        let v = visible
        var i = 0
        while i < v.count {
            if v[i].aspectRatio == .landscape {
                out.append([v[i]]); i += 1
            } else if i + 1 < v.count, v[i + 1].aspectRatio != .landscape {
                out.append([v[i], v[i + 1]]); i += 2
            } else {
                out.append([v[i]]); i += 1
            }
        }
        return out
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if !selectMode, let id = store.interruptedProjectID, let p = store.project(id) {
                    interruptedBanner(p)
                }
                TextField("Search projects", text: $query)
                    .fieldStyle()
                SegmentedFilter(options: LibraryFilter.allCases, label: { $0.rawValue }, selection: $filter)
                // The mock's own subtitle counts ALL projects (`P.length`),
                // not just the filtered set - kept that, added a real
                // "rendering now" count in place of its fake "job slots,
                // 1 busy, extra jobs queue" text (this app has no job-slot
                // limit; every render starts immediately).
                Text(subtitle)
                    .font(.system(size: 12.5)).foregroundStyle(Theme.ink2)
                    .padding(.horizontal, 4)

                if visible.isEmpty {
                    ContentUnavailableView {
                        Label(emptyTitle, systemImage: "film.stack")
                    } description: {
                        Text(emptyDescription)
                    } actions: {
                        if filter == .all, query.isEmpty {
                            Button("Create a video") { store.tab = .create }.buttonStyle(.glassProminent)
                        }
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.top, 60)
                } else {
                    VStack(spacing: 12) {
                        ForEach(rows.indices, id: \.self) { i in
                            let row = rows[i]
                            if row.count == 2 {
                                HStack(spacing: 12) {
                                    card(row[0], wide: false)
                                    card(row[1], wide: false)
                                }
                            } else {
                                card(row[0], wide: row[0].aspectRatio == .landscape)
                            }
                        }
                    }
                }
            }
            .padding(16)
        }
        .screenBackground()
        .navigationTitle(filter == .archived ? "Archive" : "Library")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Haptics.light()
                    selectMode.toggle()
                    selected.removeAll()
                } label: {
                    Image(systemName: selectMode ? "xmark.circle" : "checkmark.circle")
                }
                .accessibilityLabel(selectMode ? "Cancel selection" : "Select")
            }
        }
        .safeAreaInset(edge: .bottom) {
            if selectMode { selectBar }
        }
    }

    private var subtitle: String {
        let base = "\(store.projects.count) project\(store.projects.count == 1 ? "" : "s")"
        let running = store.jobs.count
        let queued = store.queuedIDs.count
        var parts = [base]
        if running > 0 { parts.append("\(running) running") }
        if queued > 0 { parts.append("\(queued) queued") }
        return parts.joined(separator: " · ")
    }

    private var emptyTitle: String {
        if !query.isEmpty { return "No matches" }
        switch filter {
        case .archived: return "Nothing archived"
        case .running: return "Nothing rendering"
        case .done: return "Nothing finished yet"
        case .all: return "No videos yet"
        }
    }

    private var emptyDescription: String {
        if !query.isEmpty { return "Nothing in this view matches \u{201C}\(query)\u{201D}." }
        switch filter {
        case .archived: return "Archived videos show up here."
        case .running: return "Videos currently rendering show up here."
        case .done: return "Finished videos show up here."
        case .all: return "Create your first video — everything renders right on your iPhone."
        }
    }

    @ViewBuilder private func card(_ p: Project, wide: Bool) -> some View {
        ProjectCard(
            project: p, wide: wide, selectMode: selectMode, isSelected: selected.contains(p.id),
            onTap: {
                if selectMode {
                    Haptics.light()
                    if selected.contains(p.id) { selected.remove(p.id) } else { selected.insert(p.id) }
                } else {
                    store.selectForStudio(p.id)
                }
            },
            onArchive: { archiveToggle(p) },
            onDelete: { delete(p) }
        )
    }

    private var selectBar: some View {
        HStack(spacing: 10) {
            Text("\(selected.count) selected").fontWeight(.semibold)
            Spacer()
            // In the Archived filter the same action must UN-archive - it used
            // to say "Archive" and re-archive things that were already there.
            Button(filter == .archived ? "Unarchive" : "Archive") {
                Haptics.light()
                let ids = selected
                let archive = filter != .archived
                withAnimation(.spring) {
                    for id in ids { store.setArchived(id, archive) }
                    selectMode = false
                    selected.removeAll()
                }
            }
            .buttonStyle(.glassProminent)
            .disabled(selected.isEmpty)
        }
        .padding(.leading, 18).padding(.trailing, 8).padding(.vertical, 8)
        .glass(RoundedRectangle(cornerRadius: 26, style: .continuous))
        .padding(.horizontal, 14)
        .padding(.bottom, 4)
    }

    private func archiveToggle(_ p: Project) {
        Haptics.light()
        withAnimation(.spring) { store.setArchived(p.id, !p.archived) }
    }

    private func delete(_ p: Project) {
        Haptics.warning()
        withAnimation(.spring) { store.delete(p.id) }
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
                store.selectForStudio(p.id)
            }
            .buttonStyle(.glassProminent)
        }
        .glassCard()
        .frame(maxWidth: .infinity)
    }
}

/// The mock's `.pc.g` library card: thumbnail with a play-glyph overlay, a
/// top row (status chip +, in selection mode, the `.dot` checkbox) and a
/// bottom row (date + aspect ratio) painted ON the thumbnail, then a
/// `.meta` panel below with the title and (while rendering) a progress
/// bar. The existing per-card archive/delete overflow menu is kept
/// (hidden while selecting) - the plan called for ADDING selection mode,
/// not removing that affordance.
struct ProjectCard: View {
    @Environment(Store.self) private var store
    let project: Project
    let wide: Bool
    let selectMode: Bool
    let isSelected: Bool
    var onTap: () -> Void
    var onArchive: () -> Void
    var onDelete: () -> Void

    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "MMM d"; return f
    }()

    var body: some View {
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 0) {
                ZStack {
                    thumb
                    Image(systemName: "play.fill")
                        .font(.system(size: wide ? 26 : 30))
                        .foregroundStyle(.white.opacity(0.8))
                    VStack {
                        HStack(alignment: .top, spacing: 6) {
                            Text(statusText)
                                .foregroundStyle(statusColor)
                                .chipStyle()
                            Spacer()
                            if selectMode { selectionDot }
                        }
                        Spacer()
                        HStack {
                            Text("\(Self.dateFormatter.string(from: project.created)), \(project.aspectRatio.rawValue)")
                                .font(.system(size: 12.5, weight: .semibold))
                                .foregroundStyle(.white)
                                .shadow(color: .black.opacity(0.5), radius: 4, y: 1)
                            Spacer()
                        }
                    }
                    .padding(10)
                }
                .aspectRatio(wide ? 16.0 / 8.0 : 9.0 / 12.0, contentMode: .fill)
                .clipped()

                VStack(alignment: .leading, spacing: 7) {
                    Text(project.title).font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.ink).lineLimit(2)
                    if let job = store.jobs[project.id] {
                        progressBar(job.progress)
                    }
                }
                .padding(EdgeInsets(top: 10, leading: 12, bottom: 12, trailing: 12))
            }
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity, alignment: .leading)
        .modifier(GlassCardShape())
        .overlay(alignment: .topTrailing) {
            if !selectMode {
                Menu {
                    Button(project.archived ? "Unarchive" : "Archive", systemImage: "archivebox", action: onArchive)
                    Button("Delete", systemImage: "trash", role: .destructive, action: onDelete)
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.footnote.weight(.bold))
                        .foregroundStyle(.white)
                        .frame(width: 28, height: 28)
                        .background(.black.opacity(0.25), in: .circle)
                }
                .padding(8)
                .accessibilityLabel("More options for \(project.title)")
            }
        }
        .contextMenu {
            Button(project.archived ? "Unarchive" : "Archive", systemImage: "archivebox", action: onArchive)
            Button("Delete", systemImage: "trash", role: .destructive, action: onDelete)
        }
    }

    private var statusText: String {
        switch project.overallStatus { case .pending: "Draft"; case .running: "Rendering"; case .done: "Ready"; case .failed: "Failed" }
    }
    private var statusColor: Color {
        switch project.overallStatus { case .pending: Theme.ink2; case .running: Theme.accent; case .done: Theme.ok; case .failed: Theme.bad }
    }

    private var selectionDot: some View {
        ZStack {
            Circle().fill(isSelected ? Theme.accent : .black.opacity(0.25))
                .overlay(Circle().strokeBorder(.white.opacity(0.85), lineWidth: isSelected ? 0 : 1.5))
            if isSelected {
                Image(systemName: "checkmark").font(.system(size: 11, weight: .bold)).foregroundStyle(Theme.onAccent)
            }
        }
        .frame(width: 24, height: 24)
    }

    private func progressBar(_ value: Double) -> some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Theme.fill)
                Capsule().fill(Theme.accent).frame(width: geo.size.width * max(0, min(1, value)))
            }
        }
        .frame(height: 5)
    }

    /// Placeholder gradient underneath; the real first-scene image fades in
    /// on top once `ImageCache` has decoded it off the main thread.
    private var thumb: some View {
        ZStack {
            LinearGradient(colors: [Theme.accent.opacity(0.55), Theme.accent.opacity(0.22)], startPoint: .topLeading, endPoint: .bottomTrailing)
            CachedThumb(url: store.file(project, project.scenes.first(where: { $0.imageFile != nil })?.imageFile), maxPixel: 720)
        }
    }
}

/// Thin wrapper so `ProjectCard` gets the mock's exact `.pc{border-
/// radius:24px;overflow:hidden}` glass card without `glassCard()`'s own
/// default padding (the card manages its own internal padding per-row).
private struct GlassCardShape: ViewModifier {
    func body(content: Content) -> some View {
        content.glassCard(24, padding: 0)
    }
}

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

    /// A grid row, identified by its first project so SwiftUI keeps a card
    /// (and its decoded thumbnail) attached to the project when the filter or a
    /// search moves it to another row, instead of re-using it by position.
    private struct GridRow: Identifiable {
        let items: [Project]
        let wide: Bool
        var id: String { items.first?.id ?? "" }
    }

    /// The grid's rows - see `LibraryLayout` for the packing rules (a lone
    /// card is half width, a landscape project spans both columns).
    private static func gridRows(_ shown: [Project]) -> [GridRow] {
        LibraryLayout.rows(shown.map { $0.aspectRatio }).map { row in
            GridRow(items: row.indices.map { shown[$0] }, wide: row.isWide)
        }
    }

    var body: some View {
        // Filtered and packed ONCE per update. This used to be recomputed
        // (filter, then layout) every time the grid asked for `rows`, which it
        // does for every row: work grew with the square of the library.
        let shown = filter.apply(to: store.projects, query: query)
        let rows = Self.gridRows(shown)
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if !selectMode, let id = store.interruptedProjectID, let p = store.project(id) {
                    interruptedBanner(p)
                }
                SegmentedFilter(options: LibraryFilter.allCases, label: { $0.rawValue }, selection: $filter)
                // Its own view: it reads `store.jobs`, which changes on every
                // progress tick of every running video. Read here, it would
                // rebuild the whole Library several times a second.
                LibrarySubtitle()
                    .padding(.horizontal, 4)

                if shown.isEmpty {
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
                    // Lazy: only the cards near the screen are built (each one
                    // draws layered shadows and decodes an image).
                    LazyVStack(spacing: 12) {
                        ForEach(rows) { row in
                            if row.wide {
                                card(row.items[0], wide: true)
                            } else {
                                // Two columns of equal width. A lone card keeps
                                // its half-width cell (the spacer), so it is
                                // never stretched across the whole row.
                                HStack(alignment: .top, spacing: 12) {
                                    card(row.items[0], wide: false)
                                    if row.items.count == 2 {
                                        card(row.items[1], wide: false)
                                    } else {
                                        Color.clear.frame(maxWidth: .infinity)
                                    }
                                }
                            }
                        }
                    }
                }
            }
            .padding(16)
        }
        .screenBackground()
        .navigationTitle(filter == .archived ? "Archive" : "Library")
        // The system search field: a real Liquid Glass bar that sits where
        // the OS puts it, morphs on focus, and brings its own Cancel and
        // clear buttons.
        .searchable(text: $query, prompt: "Search projects")
        .toolbar {
            ToolbarItem(placement: .topBarLeading) { AgentToolbarButton() }
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
        .equatable()
    }

    private var selectBar: some View {
        HStack(spacing: 10) {
            Text("\(selected.count) selected").fontWeight(.semibold).foregroundStyle(.primary)
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
        .liquidGlass(RoundedRectangle(cornerRadius: 26, style: .continuous))
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
/// status chip (+, in selection mode, the checkbox dot) and a date/aspect
/// caption painted ON the thumbnail, then a meta panel with the title and
/// (while rendering) a progress bar.
///
/// LAYOUT: the thumbnail's SIZE comes only from an empty `Color.clear` with
/// a fixed aspect ratio; the gradient, the image and the overlays are drawn
/// over it and clipped. The previous version put the image (`scaledToFill`,
/// no frame) straight into the aspect-ratio'd stack, and a filled image
/// reports a size bigger than it was offered - so cards in the same row came
/// out with different widths and overflowing content. The title and the
/// progress slot also reserve their height, so every card in a row is the
/// same height whether its title wraps or a render is running.
struct ProjectCard: View, Equatable {
    @Environment(Store.self) private var store
    let project: Project
    let wide: Bool
    let selectMode: Bool
    let isSelected: Bool
    var onTap: () -> Void
    var onArchive: () -> Void
    var onDelete: () -> Void

    /// Equal when everything the card DRAWS is equal. The closures are left
    /// out on purpose: they read live state (`selectMode`, `store`) when called,
    /// so an old one is as good as a new one. With `.equatable()` a project
    /// update during a render redraws that project's card, not the whole grid.
    static func == (l: ProjectCard, r: ProjectCard) -> Bool {
        l.wide == r.wide && l.selectMode == r.selectMode && l.isSelected == r.isSelected
            && l.project.id == r.project.id && l.project.title == r.project.title
            && l.project.archived == r.project.archived && l.project.aspectRatio == r.project.aspectRatio
            && l.project.created == r.project.created && l.project.overallStatus == r.project.overallStatus
            && l.firstImage == r.firstImage
    }

    /// The image the thumbnail shows, by file name.
    private var firstImage: String? { project.scenes.first(where: { $0.imageFile != nil })?.imageFile }

    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "MMM d"; return f
    }()

    /// Wide cards are 2:1 (the mock's 16:8); everything else 3:4.
    private var ratio: CGFloat { wide ? 2.0 : 0.75 }

    var body: some View {
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 0) {
                Color.clear
                    .aspectRatio(ratio, contentMode: .fit)
                    .background(placeholder)
                    .overlay { CachedThumb(url: thumbURL, maxPixel: 720) }
                    .overlay { thumbOverlays }
                    .clipped()

                VStack(alignment: .leading, spacing: 7) {
                    Text(project.title)
                        .font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.ink)
                        .lineLimit(2, reservesSpace: !wide)
                        .multilineTextAlignment(.leading)
                    // Always 5pt tall, so a running card is not taller than its neighbour.
                    // Its own view: only THIS reads `store.jobs`, so a progress tick
                    // redraws a 5pt bar, not every card in the Library.
                    CardProgress(projectID: project.id)
                        .frame(height: 5)
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
                        .foregroundStyle(.primary)
                        .frame(width: 30, height: 30)
                        .liquidGlass(Circle())
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

    /// Status chip, selection dot and the date/aspect caption.
    private var thumbOverlays: some View {
        ZStack {
            Image(systemName: "play.fill")
                .font(.system(size: wide ? 26 : 30))
                .foregroundStyle(.white.opacity(0.8))
            VStack {
                HStack(alignment: .top, spacing: 6) {
                    statusChip
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
    }

    /// A small Liquid Glass capsule, tinted by status, over the photo.
    private var statusChip: some View {
        Text(statusText)
            .font(.system(size: 11.5, weight: .semibold))
            .foregroundStyle(.primary)
            .padding(.horizontal, 10).padding(.vertical, 4)
            .liquidGlass(Capsule(), tint: chipTint)
    }

    private var chipTint: Color? {
        switch project.overallStatus {
        case .pending: nil
        case .running: Theme.accent.opacity(0.55)
        case .done: Theme.ok.opacity(0.5)
        case .failed: Theme.bad.opacity(0.55)
        }
    }

    private var statusText: String {
        switch project.overallStatus { case .pending: "Draft"; case .running: "Rendering"; case .done: "Ready"; case .failed: "Failed" }
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

    /// First scene's image, once there is one.
    private var thumbURL: URL? {
        store.file(project, project.scenes.first(where: { $0.imageFile != nil })?.imageFile)
    }

    /// Gradient under the (fading-in) image.
    private var placeholder: some View {
        LinearGradient(colors: [Theme.accent.opacity(0.55), Theme.accent.opacity(0.22)],
                       startPoint: .topLeading, endPoint: .bottomTrailing)
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

/// The "N projects · M running" line. Separate from `HomeView` on purpose: it
/// is the only part of the Library header that reads the live job table.
private struct LibrarySubtitle: View {
    @Environment(Store.self) private var store

    var body: some View {
        let base = "\(store.projects.count) project\(store.projects.count == 1 ? "" : "s")"
        let running = store.jobs.count
        let queued = store.queuedIDs.count
        var parts = [base]
        if running > 0 { parts.append("\(running) running") }
        if queued > 0 { parts.append("\(queued) queued") }
        return Text(parts.joined(separator: " · "))
            .font(.system(size: 12.5)).foregroundStyle(Theme.ink2)
    }
}

/// A running card's progress bar. It alone observes the job's progress.
private struct CardProgress: View {
    @Environment(Store.self) private var store
    let projectID: String

    var body: some View {
        if let job = store.jobs[projectID] {
            let value = max(0, min(1, job.progress))
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Theme.fill)
                    Capsule().fill(Theme.accent).frame(width: geo.size.width * value)
                }
            }
        } else {
            Color.clear
        }
    }
}

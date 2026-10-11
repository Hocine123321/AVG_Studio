import Foundation

/// How the Library packs project cards into a two-column grid.
///
/// A landscape project spans both columns, alone in its row; portrait and
/// square projects pair up two to a row. The old packing paired strictly in
/// order, so a portrait followed by a landscape (or the last card of an odd
/// count) ended up ALONE in a row and was drawn full width at 3:4 - one huge
/// card in a grid of small ones. Here a lone card is always half width, and a
/// portrait waiting for a partner pulls the next portrait/square up past a
/// landscape row, so the grid has no holes. The list is newest-first, so a
/// card sliding one row is barely noticeable; it is the only reordering done,
/// and every project appears exactly once.
struct LibraryRow: Equatable {
    /// Indices into the list passed to `LibraryLayout.rows`.
    let indices: [Int]
    /// One landscape project spanning both columns.
    let isWide: Bool
}

enum LibraryLayout {
    static func rows(_ aspects: [AspectRatio]) -> [LibraryRow] {
        var out: [LibraryRow] = []
        var used = Set<Int>()
        var waiting: Int?    // a portrait/square looking for a partner

        for i in aspects.indices where !used.contains(i) {
            if aspects[i] == .landscape {
                if let w = waiting {
                    // Fill the gap with the next narrow card, if there is one.
                    let partner = (i + 1 ..< aspects.count).first { aspects[$0] != .landscape && !used.contains($0) }
                    if let partner {
                        used.insert(partner)
                        out.append(LibraryRow(indices: [w, partner], isWide: false))
                    } else {
                        out.append(LibraryRow(indices: [w], isWide: false))
                    }
                    waiting = nil
                }
                out.append(LibraryRow(indices: [i], isWide: true))
            } else if let w = waiting {
                out.append(LibraryRow(indices: [w, i], isWide: false))
                waiting = nil
            } else {
                waiting = i
            }
        }
        if let w = waiting { out.append(LibraryRow(indices: [w], isWide: false)) }
        return out
    }
}

extension LibraryFilter {
    /// The projects this filter and the search box show, in the store's
    /// order. Archived projects appear only under Archived.
    func apply(to projects: [Project], query: String) -> [Project] {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        return projects.filter { p in
            let statusMatch: Bool
            switch self {
            case .all: statusMatch = !p.archived
            case .running: statusMatch = !p.archived && p.overallStatus == .running
            case .done: statusMatch = !p.archived && p.overallStatus == .done
            case .archived: statusMatch = p.archived
            }
            return statusMatch && (q.isEmpty || p.title.localizedCaseInsensitiveContains(q))
        }
    }
}

import SwiftUI

/// This video's own run log - the iOS analogue of the PC's per-take "Log"
/// toggle (`web/src/Studio.tsx`'s `TakeLogToggle`, reading `GET .../takes/
/// {id}/log`). Presented as its own sheet rather than an inline toggle: the
/// PC's panel lives inside a page that scrolls with it, where this app's
/// `StudioView` is a fixed-height card stack better suited to a toolbar
/// button that opens a focused, full-height reader.
///
/// One deliberate improvement over the PC's literal behaviour: the PC
/// fetches the log once when the toggle opens and never again ("Log" reads
/// a snapshot). This view refreshes every second while the sheet is open
/// AND the project is still running, so it behaves like an actual live
/// console instead of requiring a close/reopen to see what happened next -
/// exactly what "see everything happening" is asking for.
struct ProjectLogView: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    let projectID: String
    let title: String
    @State private var tail: (lines: [String], truncated: Bool, total: Int) = ([], false, 0)

    var body: some View {
        NavigationStack {
            ScrollView {
                if tail.lines.isEmpty {
                    ContentUnavailableView("No log yet", systemImage: "text.alignleft",
                        description: Text("Nothing has run for this video yet."))
                        .padding(.top, 60)
                } else {
                    VStack(alignment: .leading, spacing: 4) {
                        if tail.truncated {
                            Text("Showing the last \(tail.lines.count) of \(tail.total) lines.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        // Readable panel: text sits on a solid-ish layer
                        // instead of straight on the window's blur.
                        Text(tail.lines.joined(separator: "\n"))
                            .font(.system(.footnote, design: .monospaced))
                            .foregroundStyle(Theme.ink)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(14)
                            .background(Theme.logPanel, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                    }
                    .padding(16)
                }
            }
            .glassSheetBackground()
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } }
            }
            .task(id: projectID) { await refreshLoop() }
        }
    }

    private func refreshLoop() async {
        tail = store.projectLogTail(projectID)
        while !Task.isCancelled {
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            if Task.isCancelled { break }
            tail = store.projectLogTail(projectID)
            if !store.isRunning(projectID) { break }
        }
    }
}

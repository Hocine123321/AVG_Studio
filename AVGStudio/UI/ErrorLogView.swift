import SwiftUI

/// Activity & errors - restyled from a bare system `List` onto the same
/// ScrollView + glass-card language as Library, Studio and App health, so
/// the one diagnostics screen no longer looks like a different app. The
/// log itself is unchanged (in memory, newest first, capped at 300 lines
/// by `Store.record`).
struct ErrorLogView: View {
    @Environment(Store.self) private var store
    @State private var scope: Scope = .all

    private enum Scope: String, CaseIterable, Hashable { case all = "All", errors = "Errors" }

    private var entries: [LogEntry] {
        scope == .errors ? store.log.filter(\.isError) : store.log
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                SegmentedFilter(options: Scope.allCases, label: { $0.rawValue }, selection: $scope)
                if entries.isEmpty {
                    ContentUnavailableView(
                        scope == .errors ? "No errors" : "No activity yet",
                        systemImage: "list.bullet.rectangle"
                    )
                    .padding(.top, 40)
                } else {
                    LazyVStack(spacing: 8) {
                        ForEach(entries) { row($0) }
                    }
                }
            }
            .padding(16)
        }
        .screenBackground()
        .navigationTitle("Activity")
    }

    private func row(_ e: LogEntry) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: e.isError ? "exclamationmark.triangle.fill" : "checkmark.circle")
                .foregroundStyle(e.isError ? Theme.bad : Theme.ink2)
            VStack(alignment: .leading, spacing: 3) {
                Text(e.message)
                    .font(.footnote)
                    .foregroundStyle(e.isError ? Theme.bad : Theme.ink)
                    .textSelection(.enabled)
                Text(e.date, style: .time).font(.caption2).foregroundStyle(Theme.ink2)
            }
            Spacer(minLength: 0)
        }
        .glassCard(18, padding: 12)
    }
}

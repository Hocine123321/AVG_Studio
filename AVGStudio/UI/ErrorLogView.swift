import SwiftUI

/// Activity & errors, on the same ScrollView + glass-card language as the
/// rest of the app.
///
///  - All: this session's activity (in memory, 300 lines, gone on relaunch).
///  - Errors: every error the app has noticed, KEPT across launches
///    (`ErrorHistory`, 200 newest, credentials redacted), with a one-tap
///    report to paste into a bug report - the PC's "hand it to an assistant".
struct ErrorLogView: View {
    @Environment(Store.self) private var store
    @State private var scope: Scope = .errors
    @State private var confirmClear = false

    private enum Scope: String, CaseIterable, Hashable { case errors = "Errors", all = "All activity" }

    private var report: String {
        let info = Bundle.main.infoDictionary
        let version = "\(info?["CFBundleShortVersionString"] as? String ?? "?") (\(info?["CFBundleVersion"] as? String ?? "?"))"
        let device = "\(UIDevice.current.model) · iOS \(UIDevice.current.systemVersion)"
        return ErrorHistory.report(store.errorHistory, appVersion: version, device: device,
                                   secrets: APIKey.allCases.compactMap { Keychain.get($0) })
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                SegmentedFilter(options: Scope.allCases, label: { $0.rawValue }, selection: $scope)
                switch scope {
                case .errors: errorsList
                case .all: activityList
                }
            }
            .padding(16)
        }
        .screenBackground()
        .navigationTitle("Activity")
        .toolbar {
            if scope == .errors, !store.errorHistory.isEmpty {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        ShareLink(item: report, subject: Text("AVG Studio error report")) {
                            Label("Share error report", systemImage: "square.and.arrow.up")
                        }
                        Button("Copy error report", systemImage: "doc.on.doc") {
                            UIPasteboard.general.string = report
                            Haptics.success()
                        }
                        Button("Clear error history", systemImage: "trash", role: .destructive) { confirmClear = true }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
        }
        .confirmationDialog("Clear the error history?", isPresented: $confirmClear, titleVisibility: .visible) {
            Button("Clear", role: .destructive) { store.clearErrorHistory() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This removes the saved errors from this iPhone. It doesn't change any video.")
        }
    }

    @ViewBuilder private var errorsList: some View {
        if store.errorHistory.isEmpty {
            ContentUnavailableView("No errors", systemImage: "checkmark.seal",
                                   description: Text("Errors are kept here, even after you close the app."))
                .padding(.top, 40)
        } else {
            LazyVStack(spacing: 8) {
                ForEach(store.errorHistory) { record in
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(Theme.bad)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(record.message)
                                .font(.footnote).foregroundStyle(Theme.bad)
                                .textSelection(.enabled)
                            Text([record.date.formatted(date: .abbreviated, time: .shortened), record.project]
                                .compactMap { $0 }.joined(separator: " · "))
                                .font(.caption2).foregroundStyle(Theme.ink2)
                        }
                        Spacer(minLength: 0)
                    }
                    .glassCard(18, padding: 12)
                }
            }
        }
    }

    @ViewBuilder private var activityList: some View {
        if store.log.isEmpty {
            ContentUnavailableView("No activity yet", systemImage: "list.bullet.rectangle").padding(.top, 40)
        } else {
            LazyVStack(spacing: 8) {
                ForEach(store.log) { e in
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
        }
    }
}

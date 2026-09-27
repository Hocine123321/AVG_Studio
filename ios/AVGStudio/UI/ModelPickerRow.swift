import SwiftUI

/// One setting backed by a `ModelCatalog` - a `Picker` fed from the live
/// list once one has loaded, a plain `TextField` before that (or if the
/// list ever comes back empty), and a small Refresh control underneath
/// either way.
///
/// The text-field fallback is deliberately not a loading spinner that
/// blocks the field: it is ported straight from the PC's own
/// `ModelPicker.tsx`, whose header comment states the reasoning this row
/// exists to preserve - "Disabling would take away a field that has
/// always been editable and leave a fresh install unable to set a model
/// at all until a sweep runs - a worse failure than an unhelpful blank."
/// A value the user already typed, or one from an entry that has since
/// dropped out of the live list, is always kept selectable too (the
/// synthesized "not in the live list" row below) - same as the PC's
/// `known`/`items` handling - so switching this row to a picker can never
/// silently discard a setting that was working.
struct ModelPickerRow: View {
    let title: String
    @Binding var value: String
    let catalog: ModelCatalog

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if catalog.entries.isEmpty {
                TextField(title, text: $value)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            } else {
                Picker(title, selection: $value) {
                    if !value.isEmpty && !catalog.entries.contains(where: { $0.id == value }) {
                        Text("\(value) (not in the live list)").tag(value)
                    }
                    ForEach(catalog.entries) { entry in
                        Text(entry.label).tag(entry.id)
                    }
                }
                if let sub = catalog.entries.first(where: { $0.id == value })?.sublabel, !sub.isEmpty {
                    Text(sub).font(.caption).foregroundStyle(.secondary)
                }
            }

            HStack(spacing: 8) {
                Button(refreshTitle) { Task { await catalog.refresh() } }
                    .font(.caption)
                    .disabled(catalog.status == .loading)
                if catalog.status == .loading { ProgressView().controlSize(.mini) }
            }

            if case .failed(let message, _) = catalog.status {
                Text("Couldn't fetch a live list (\(message)).\(catalog.entries.isEmpty ? " Type an id above, or tap Refresh to try again." : " Showing the last list that worked.")")
                    .font(.caption2).foregroundStyle(.secondary)
            } else if catalog.entries.isEmpty, catalog.status != .loading {
                Text("No live list yet — type an id above, or tap Refresh to fetch one.")
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }
        .task {
            if catalog.status == .idle {
                catalog.loadCacheIfPresent()
                // An empty cache (first launch, or a cache wipe) is worth
                // one automatic attempt - it costs nothing on every source
                // here (listing, never generating) - rather than making
                // the user find the Refresh button on a screen that looks
                // otherwise unremarkable.
                if catalog.entries.isEmpty { await catalog.refresh() }
            }
        }
    }

    private var refreshTitle: String {
        if case .loaded(let date) = catalog.status {
            return "Refresh (updated \(date.formatted(date: .omitted, time: .shortened)))"
        }
        return catalog.status == .loading ? "Refreshing…" : "Refresh live list"
    }
}

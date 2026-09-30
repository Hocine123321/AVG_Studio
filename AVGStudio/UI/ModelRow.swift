import SwiftUI

/// One setting backed by a `ModelCatalog`, rebuilt for one-handed use.
///
/// The old row was a stack of a menu `Picker`, a tiny Refresh button and
/// caption text inside a `Form`: a menu over hundreds of ElevenLabs voices or
/// Gemini models with no way to search, popping up at the row's position.
/// This one is a single 56pt row; tapping it opens a bottom sheet with the
/// list, a SEARCH field and a REFRESH button docked at the bottom, in the
/// thumb zone.
///
/// Kept from the old row, because it is load-bearing (ported from the PC's
/// `ModelPicker.tsx`): the setting is ALWAYS editable. With no live list you
/// can type an id; with a list you can still use an id that is not in it; and
/// the current value always stays selectable even when it has dropped out of
/// the live list. Disabling the field until a list loads would leave a fresh
/// install unable to set a model at all.
struct ModelRow: View {
    let title: String
    @Binding var value: String
    let catalog: ModelCatalog
    @State private var open = false

    private var display: String {
        if value.isEmpty { return "Not set" }
        return catalog.entries.first(where: { $0.id == value })?.label ?? value
    }

    var body: some View {
        Button {
            Haptics.light()
            open = true
        } label: {
            HStack(spacing: 12) {
                Text(title).foregroundStyle(Theme.ink)
                Spacer(minLength: 8)
                Text(display)
                    .foregroundStyle(Theme.ink2)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .frame(maxWidth: 190, alignment: .trailing)
                Image(systemName: "chevron.up.chevron.down").font(.caption.weight(.semibold)).foregroundStyle(Theme.ink2)
            }
            .frame(minHeight: settingsRowMinHeight)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title)
        .accessibilityValue(display)
        .task {
            if catalog.status == .idle {
                catalog.loadCacheIfPresent()
                // An empty cache (first launch, or a wipe) is worth one
                // automatic attempt - listing costs nothing on any source
                // here - rather than making the user hunt for Refresh.
                if catalog.entries.isEmpty { await catalog.refresh() }
            }
        }
        .sheet(isPresented: $open) {
            ModelSheet(title: title, value: $value, catalog: catalog)
        }
    }
}

private struct ModelSheet: View {
    @Environment(\.dismiss) private var dismiss
    let title: String
    @Binding var value: String
    let catalog: ModelCatalog
    @State private var query = ""

    private var typed: String { query.trimmingCharacters(in: .whitespacesAndNewlines) }

    private var filtered: [CatalogEntry] {
        guard !typed.isEmpty else { return catalog.entries }
        let q = typed.lowercased()
        return catalog.entries.filter {
            $0.label.lowercased().contains(q) || $0.id.lowercased().contains(q) || ($0.sublabel ?? "").lowercased().contains(q)
        }
    }
    /// The typed text, offered as-is when it is not already a listed id.
    private var offerTyped: Bool { !typed.isEmpty && !catalog.entries.contains(where: { $0.id == typed }) }
    /// The saved value, when it has dropped out of the live list.
    private var orphan: Bool { !value.isEmpty && !catalog.entries.contains(where: { $0.id == value }) }
    private var hasRows: Bool { offerTyped || orphan || !filtered.isEmpty }

    var body: some View {
        VStack(spacing: 0) {
            // Same grabber and corner radius as every `GlassSheet`.
            Capsule().fill(Theme.ink2).frame(width: 38, height: 5).opacity(0.5).padding(.top, 10)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.disp(24)).foregroundStyle(Theme.ink)
                statusLine
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 20).padding(.top, 14).padding(.bottom, 10)

            ScrollView {
                if hasRows {
                    LazyVStack(spacing: 0) {
                        if offerTyped {
                            row("Use “\(typed)”", subtitle: "Use this id as typed", selected: false) { pick(typed) }
                        }
                        if orphan {
                            row(value, subtitle: "Current value (not in the live list)", selected: true) { dismiss() }
                        }
                        ForEach(filtered) { entry in
                            row(entry.label, subtitle: entry.sublabel, selected: entry.id == value) { pick(entry.id) }
                        }
                    }
                    .padding(.horizontal, 16)
                    .glassCard(24, padding: 0)
                    .padding(.horizontal, 18)
                } else if !typed.isEmpty {
                    Text("No match. Press return to use “\(typed)” as typed.")
                        .font(.footnote).foregroundStyle(Theme.ink2).padding(20)
                }
            }
            .scrollDismissesKeyboard(.interactively)

            dock
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.hidden)
        .presentationCornerRadius(34)
        .glassSheetBackground(scroll: false)
    }

    @ViewBuilder private var statusLine: some View {
        switch catalog.status {
        case .loading:
            HStack(spacing: 6) { ProgressView().controlSize(.mini); Text("Refreshing the live list…") }
                .font(.caption).foregroundStyle(Theme.ink2)
        case .failed(let message, let hasCache):
            Text("Couldn't fetch a live list (\(message)). \(hasCache ? "Showing the last list that worked." : "Type an id below, or tap refresh to try again.")")
                .font(.caption).foregroundStyle(Theme.ink2)
        case .loaded(let date):
            Text("\(catalog.entries.count) available · updated \(date.formatted(date: .omitted, time: .shortened))")
                .font(.caption).foregroundStyle(Theme.ink2)
        case .idle:
            Text("No live list yet. Type an id below, or tap refresh.")
                .font(.caption).foregroundStyle(Theme.ink2)
        }
    }

    private func row(_ title: String, subtitle: String?, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 12) {
                RowText(title: title, subtitle: subtitle)
                Spacer(minLength: 8)
                if selected { Image(systemName: "checkmark").font(.body.weight(.semibold)).foregroundStyle(Theme.accent) }
            }
            .frame(minHeight: settingsRowMinHeight)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .overlay(alignment: .top) { Rectangle().fill(Theme.glassEdge).frame(height: 0.5) }
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func pick(_ id: String) {
        Haptics.light()
        value = id
        dismiss()
    }

    /// Search + refresh, docked where the thumb already is.
    private var dock: some View {
        HStack(spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").foregroundStyle(Theme.ink2)
                TextField("Search, or type a model id", text: $query)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .submitLabel(.done)
                    .onSubmit { if !typed.isEmpty { pick(typed) } }
            }
            .padding(.horizontal, 14)
            .frame(height: 48)
            .glass(Capsule())

            Button {
                Task { await catalog.refresh() }
            } label: {
                Group {
                    if catalog.status == .loading { ProgressView() }
                    else { Image(systemName: "arrow.clockwise").font(.system(size: 16, weight: .semibold)).foregroundStyle(Theme.ink) }
                }
                .frame(width: 48, height: 48)
                .glassCircle()
            }
            .buttonStyle(.plain)
            .disabled(catalog.status == .loading)
            .accessibilityLabel("Refresh list")
        }
        .padding(.horizontal, 16)
        .padding(.top, 8)
        .padding(.bottom, 12)
    }
}

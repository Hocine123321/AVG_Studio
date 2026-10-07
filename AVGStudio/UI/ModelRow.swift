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
    @Environment(Store.self) private var store
    let title: String
    @Binding var value: String
    let catalog: ModelCatalog
    @State private var query = ""
    @State private var workingOnly = false

    private var prefs: ModelPrefs { store.modelPrefs }
    private var canProbe: Bool { ModelProbe.isProbeable(catalog.cacheKey) }
    private var sweep: ModelPrefs.Sweep? { prefs.sweeping[catalog.cacheKey] }

    private var typed: String { query.trimmingCharacters(in: .whitespacesAndNewlines) }

    private var filtered: [CatalogEntry] {
        var list = prefs.ordered(catalog.entries, catalog: catalog.cacheKey)
        if workingOnly {
            // "Working" means proven OR starred: a favourite you chose stays
            // visible even before it has been tested.
            list = list.filter {
                prefs.isFavourite($0.id, in: catalog.cacheKey) || prefs.result($0.id, in: catalog.cacheKey)?.works == true
            }
        }
        guard !typed.isEmpty else { return list }
        let q = typed.lowercased()
        return list.filter {
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
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(.disp(24)).foregroundStyle(Theme.ink)
                statusLine
                if let sweep {
                    HStack(spacing: 8) {
                        ProgressView(value: Double(sweep.done), total: Double(max(sweep.total, 1))).tint(Theme.accent)
                        Text("Testing \(sweep.done) of \(sweep.total)").font(.caption).foregroundStyle(Theme.ink2).monospacedDigit()
                        Button("Stop") { prefs.cancelSweep(catalog.cacheKey) }
                            .font(.caption.weight(.semibold)).foregroundStyle(Theme.accent)
                    }
                } else if let note = prefs.lastNote[catalog.cacheKey] {
                    Text("Testing stopped: \(note)").font(.caption).foregroundStyle(Theme.bad)
                } else if prefs.hasResults(catalog.cacheKey) {
                    HStack {
                        Text("\(prefs.workingCount(catalog.cacheKey)) of \(catalog.entries.count) tested models work")
                            .font(.caption).foregroundStyle(Theme.ink2)
                        Spacer()
                        Toggle("Working only", isOn: $workingOnly)
                            .toggleStyle(.button).font(.caption.weight(.semibold)).tint(Theme.accent)
                    }
                }
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
                            entryRow(entry)
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

    /// A listed model: tap to choose it; the star pins it to the top; a small
    /// badge says whether it was found to work.
    private func entryRow(_ entry: CatalogEntry) -> some View {
        let key = catalog.cacheKey
        let favourite = prefs.isFavourite(entry.id, in: key)
        let result = prefs.result(entry.id, in: key)
        let selected = entry.id == value
        return HStack(spacing: 4) {
            Button { pick(entry.id) } label: {
                HStack(spacing: 10) {
                    RowText(title: entry.label, subtitle: entry.sublabel)
                        .opacity(result?.works == false ? 0.55 : 1)
                    Spacer(minLength: 6)
                    if let result { probeBadge(result) }
                    if selected { Image(systemName: "checkmark").font(.body.weight(.semibold)).foregroundStyle(Theme.accent) }
                }
                .frame(minHeight: settingsRowMinHeight)
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            Button {
                Haptics.light()
                prefs.toggleFavourite(entry.id, in: key)
            } label: {
                Image(systemName: favourite ? "star.fill" : "star")
                    .font(.system(size: 17))
                    .foregroundStyle(favourite ? Theme.accent : Theme.ink2)
                    .frame(width: 44, height: 44)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(favourite ? "Remove \(entry.label) from favourites" : "Add \(entry.label) to favourites")
        }
        .overlay(alignment: .top) { Rectangle().fill(Theme.glassEdge).frame(height: 0.5) }
        .accessibilityElement(children: .contain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func probeBadge(_ r: ProbeResult) -> some View {
        Text(r.works ? "Works" : (r.note ?? "Failed"))
            .font(.caption2.weight(.semibold))
            .foregroundStyle(r.works ? Theme.ok : Theme.bad)
            .padding(.horizontal, 7).padding(.vertical, 2)
            .background((r.works ? Theme.ok : Theme.bad).opacity(0.15), in: .capsule)
            .lineLimit(1)
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

            if canProbe {
                Button {
                    Haptics.light()
                    prefs.startSweep(catalog)
                } label: {
                    Image(systemName: "checkmark.shield")
                        .font(.system(size: 16, weight: .semibold)).foregroundStyle(Theme.ink)
                        .frame(width: 48, height: 48)
                        .glassCircle()
                }
                .buttonStyle(.plain)
                .disabled(sweep != nil || catalog.entries.isEmpty)
                .accessibilityLabel("Test which models work")
                .accessibilityHint("Sends each listed model one tiny request")
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 8)
        .padding(.bottom, 12)
    }
}

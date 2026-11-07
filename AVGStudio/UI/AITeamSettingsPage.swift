import SwiftUI

/// Settings → AI team: the Welcome step, available any time, plus what the
/// automatic model setup did and a way to undo it.
struct AITeamSettingsPage: View {
    @Environment(Store.self) private var store
    var focus: SettingAnchor?

    @State private var savedKeys: Set<APIKey> = []
    @State private var picked: AITeamPreset?
    @State private var editingKey: APIKey?
    @State private var checking = false
    @State private var confirmingUndo = false
    @State private var undoResult: String?

    private var settings: AppSettings { store.settings }

    /// The preset the settings already match, or nil for a mixed team.
    private var matching: AITeamPreset? {
        AITeamPreset.matching(text: settings.text, image: settings.image, voice: settings.voice,
                              keys: savedKeys, appleAvailable: AppleText.isAvailable)
    }

    private var selected: AITeamPreset? { picked ?? matching }

    /// Who the selected preset would staff, with the keys saved right now.
    private var team: TeamAssignment? {
        selected.map { AITeamPreset.assignment($0, keys: savedKeys, appleAvailable: AppleText.isAvailable) }
    }

    private var differs: Bool {
        guard let team else { return false }
        return team.text != settings.text || team.image != settings.image || team.voice != settings.voice
    }

    private var sweeping: Bool { !store.modelPrefs.sweeping.isEmpty }
    private var hasModelKey: Bool { savedKeys.contains(.gemini) || savedKeys.contains(.groq) }
    private var canCheck: Bool { settings.autoPickModels && hasModelKey && !sweeping }
    private var hasPicks: Bool {
        ModelAutoSetup.pickable.contains { store.modelPrefs.autoPick($0.catalog) != nil }
    }

    var body: some View {
        @Bindable var store = store
        SettingsPage(title: "AI team", focus: focus) {
            SettingsGroup(title: "Team",
                          footer: "Choose how much to lean on online AI. Every model can still be changed under Writing, Images and Voice.") {
                ForEach(AITeamPreset.allCases) { presetRow($0) }
            }
            .anchored(.aiTeam)

            SettingsGroup(title: "Right now") {
                teamRow("Writer", settings.text.label)
                teamRow("Images", settings.image.label)
                teamRow("Voice", settings.voice.label)
            }

            if let selected, let team, differs {
                SettingsGroup(footer: team.needs.isEmpty ? nil
                              : "\(selected.title) needs \(team.needs.map { $0.label }.joined(separator: " and ")). Until you add it, the free options are used for that part.") {
                    ActionRow(title: "Use \(selected.title)",
                              subtitle: "Writer \(team.text.label), images \(team.image.label), voice \(team.voice.label)",
                              symbol: "checkmark.circle") { apply(team) }
                    ForEach(team.needs) { key in
                        ActionRow(title: "Add \(key.label) key", symbol: "key.fill") { editingKey = key }
                    }
                }
            }

            SettingsGroup(title: "Automatic model picks",
                          footer: "When you add a key, the app checks which of that provider’s models work and picks the best one. It only replaces the built-in default or a model it picked itself, never one you chose.") {
                ToggleRow(title: "Pick the best model when I add a key", isOn: $store.settings.autoPickModels)
                    .anchored(.autoPickModels)
                ActionRow(title: "Check my models now", subtitle: checkSubtitle,
                          symbol: "arrow.clockwise", busy: checking || sweeping, enabled: canCheck) { runCheck() }
                ActionRow(title: "Undo automatic picks", subtitle: undoResult ?? undoSubtitle,
                          symbol: "arrow.uturn.backward", enabled: hasPicks) { confirmingUndo = true }
            }

            let notes = Array(store.modelPrefs.setupNotes.suffix(5).reversed())
            if !notes.isEmpty {
                SettingsGroup(title: "What it changed") {
                    ForEach(notes, id: \.self) { note in
                        Text(note)
                            .font(.callout)
                            .foregroundStyle(Theme.ink)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.vertical, 12)
                    }
                }
            }
        }
        .sheet(item: $editingKey) { key in
            APIKeySheet(key: key, showsBackup: false) { refreshKeys() }
        }
        .confirmationDialog("Undo automatic picks?", isPresented: $confirmingUndo, titleVisibility: .visible) {
            Button("Undo picks", role: .destructive) {
                let changed = ModelAutoSetup.undoAutoPicks(store: store)
                undoResult = changed.isEmpty ? "Nothing to undo: you have chosen those models yourself since."
                                             : changed.joined(separator: "\n")
            }
        } message: {
            Text("Models the app chose go back to the built-in defaults. Models you chose yourself are not touched.")
        }
        .onAppear { refreshKeys() }
    }

    // MARK: Rows

    private func presetRow(_ option: AITeamPreset) -> some View {
        Button {
            Haptics.light()
            picked = option
        } label: {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: selected == option ? "largecircle.fill.circle" : "circle")
                    .foregroundStyle(selected == option ? Theme.accent : Theme.ink2)
                    .padding(.top, 2)
                VStack(alignment: .leading, spacing: 2) {
                    Text(option.title).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.ink)
                    Text(option.blurb).font(.caption).foregroundStyle(Theme.ink2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
            .padding(.vertical, 10)
            .frame(minHeight: settingsRowMinHeight)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(selected == option ? .isSelected : [])
    }

    private func teamRow(_ label: String, _ value: String) -> some View {
        HStack(spacing: 10) {
            Text(label).foregroundStyle(Theme.ink)
            Spacer()
            Text(value).font(.callout).foregroundStyle(Theme.ink2).lineLimit(1)
        }
        .frame(minHeight: settingsRowMinHeight)
        .accessibilityElement(children: .combine)
    }

    // MARK: Text

    private var checkSubtitle: String {
        if !settings.autoPickModels { return "Turn on automatic picking first" }
        if !hasModelKey { return "Add a Google or Groq key first" }
        if let when = store.modelPrefs.lastChecked {
            return "Last checked \(when.formatted(.relative(presentation: .named)))"
        }
        return "Not checked yet"
    }

    private var undoSubtitle: String {
        hasPicks ? "Puts the models the app chose back to the defaults" : "The app has not changed any model"
    }

    // MARK: Actions

    private func refreshKeys() {
        savedKeys = Set(APIKey.allCases.filter { Keychain.has($0) })
    }

    private func apply(_ team: TeamAssignment) {
        Haptics.success()
        team.apply(to: &store.settings)
        picked = nil
    }

    private func runCheck() {
        checking = true
        Task {
            await ModelAutoSetup.run(store: store, force: true)
            checking = false
        }
    }
}

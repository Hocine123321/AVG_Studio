import SwiftUI

/// "Connected" / "Add key" pill shown next to a provider - one definition
/// for Settings and the Welcome flow, instead of each screen restyling it.
struct KeyStatusPill: View {
    let saved: Bool

    var body: some View {
        Text(saved ? "Connected" : "Add key")
            .font(.caption.weight(.semibold))
            .foregroundStyle(saved ? Theme.ok : Theme.ink2)
            .padding(.horizontal, 12).padding(.vertical, 6)
            .background(saved ? Theme.ok.opacity(0.16) : Theme.fill, in: Capsule())
            .overlay(Capsule().strokeBorder(saved ? Theme.ok.opacity(0.4) : Theme.glassEdge, lineWidth: 0.5))
    }
}

/// Key entry for one provider. Shared by Settings and Welcome.
///
/// Deliberately never reads a saved key back into the UI: the old sheet
/// pre-filled its SecureField with the stored secret (and Settings kept
/// every key in a `@State` dictionary for the life of the screen). The PC
/// app's rule is that the UI learns only WHETHER a key exists
/// (`GET /api/keys` returns booleans), so this does the same - to replace
/// a key you paste a new one, to remove it you tap Remove.
struct APIKeySheet: View {
    @Environment(\.dismiss) private var dismiss
    let key: APIKey
    /// The Welcome flow hides the backup section; Settings shows it.
    var showsBackup = true
    /// Called after a save or removal so the presenting screen can refresh
    /// its "Connected" pills.
    let onChange: () -> Void
    @State private var draft = ""
    @State private var backupDraft = ""
    @State private var hasSaved: Bool
    @State private var hasBackup: Bool
    @State private var saveFailed = false
    @State private var windowOpen: TimeInterval?

    init(key: APIKey, showsBackup: Bool = true, onChange: @escaping () -> Void) {
        self.key = key
        self.showsBackup = showsBackup
        self.onChange = onChange
        _hasSaved = State(initialValue: Keychain.has(key))
        _hasBackup = State(initialValue: Keychain.has(key, slot: .backup))
        _windowOpen = State(initialValue: KeyBackup.remaining(key))
    }

    private var canSave: Bool { !draft.trimmed.isEmpty || !backupDraft.trimmed.isEmpty }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    if hasSaved {
                        Label("A key is saved on this iPhone", systemImage: "checkmark.seal.fill")
                            .foregroundStyle(Theme.ok)
                    }
                    SecureField(hasSaved ? "Paste a new key to replace it" : "Paste your API key", text: $draft)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                } footer: {
                    if saveFailed {
                        Text("The Keychain refused the key. Try again.").foregroundStyle(Theme.bad)
                    } else {
                        Text("Kept in your iPhone's Keychain, on this device only. It is never shown again and never written into a project.")
                    }
                }
                if hasSaved {
                    Section {
                        Button("Remove key", role: .destructive) {
                            Keychain.remove(key)
                            KeyBackup.deactivate(key)
                            onChange()
                            dismiss()
                        }
                    }
                }
                if showsBackup { backupSection }
            }
            .glassSheetBackground()
            .navigationTitle(key.label)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save", action: save).disabled(!canSave)
                }
            }
        }
        .presentationDetents(showsBackup ? [.medium, .large] : [.medium])
    }

    /// A second key for the same service. When the main one is rejected, out
    /// of quota or rate-limited and the fallback did not help, the app uses
    /// this one for 30 minutes, then goes back to the main key.
    @ViewBuilder
    private var backupSection: some View {
        Section {
            if hasBackup {
                Label("A backup key is saved", systemImage: "checkmark.seal")
                    .foregroundStyle(Theme.ok)
                if let left = windowOpen {
                    Label("In use now - \(KeyBackup.remainingText(left))", systemImage: "arrow.triangle.swap")
                        .foregroundStyle(Theme.accent)
                    Button("Switch back to the main key") {
                        KeyBackup.deactivate(key)
                        windowOpen = nil
                        onChange()
                    }
                }
            }
            SecureField(hasBackup ? "Paste a new backup key to replace it" : "Paste a backup key", text: $backupDraft)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            if hasBackup {
                Button("Remove backup key", role: .destructive) {
                    Keychain.remove(key, slot: .backup)
                    KeyBackup.deactivate(key)
                    hasBackup = false
                    windowOpen = nil
                    onChange()
                }
            }
        } header: {
            Text("Backup key")
        } footer: {
            Text("A second key for the same service, such as another account. If the main key is rejected, out of quota or rate-limited and the fallback did not help, the app uses this one for the next 30 minutes, then tries the main key again.")
        }
    }

    private func save() {
        var ok = true
        if !draft.trimmed.isEmpty {
            ok = Keychain.set(key, draft)
            if ok { KeyBackup.deactivate(key) }   // a fresh main key ends any backup window
        }
        if ok, !backupDraft.trimmed.isEmpty {
            ok = Keychain.set(key, backupDraft, slot: .backup)
        }
        if ok {
            // A new main key: let the app check that provider's models and pick
            // the best working one (ModelAutoSetup), without another trip to Settings.
            if !draft.trimmed.isEmpty { NotificationCenter.default.post(name: .avgKeySaved, object: nil) }
            onChange()
            dismiss()
        } else {
            saveFailed = true
        }
    }
}

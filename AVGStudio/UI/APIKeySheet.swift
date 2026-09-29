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
    /// Called after a save or removal so the presenting screen can refresh
    /// its "Connected" pills.
    let onChange: () -> Void
    @State private var draft = ""
    @State private var hasSaved: Bool
    @State private var saveFailed = false

    init(key: APIKey, onChange: @escaping () -> Void) {
        self.key = key
        self.onChange = onChange
        _hasSaved = State(initialValue: Keychain.has(key))
    }

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
                            onChange()
                            dismiss()
                        }
                    }
                }
            }
            .glassSheetBackground()
            .navigationTitle(key.label)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        if Keychain.set(key, draft) {
                            onChange()
                            dismiss()
                        } else {
                            saveFailed = true
                        }
                    }
                    .disabled(draft.trimmed.isEmpty)
                }
            }
        }
        .presentationDetents([.medium])
    }
}

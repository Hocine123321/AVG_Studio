import SwiftUI

/// Hook Bench: ten opening lines for the topic from one short writer call.
/// Tap one and the script will open with it word for word.
struct HookBenchView: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    let topic: String
    let onPick: (String) -> Void

    @State private var hooks: [String] = []
    @State private var loading = false
    @State private var failure: String?

    var body: some View {
        NavigationStack {
            List {
                if loading {
                    HStack(spacing: 12) {
                        ProgressView()
                        Text("Asking \(store.settings.text.label) for ten openings…").foregroundStyle(.secondary)
                    }
                } else if let failure {
                    Text(failure).foregroundStyle(Theme.bad)
                } else {
                    Section {
                        ForEach(hooks, id: \.self) { hook in
                            Button {
                                Haptics.success()
                                onPick(hook)
                                dismiss()
                            } label: {
                                Text("“\(hook)”")
                                    .foregroundStyle(Theme.ink)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .contentShape(.rect)
                            }
                        }
                    } footer: {
                        Text("Tap one and the script opens with it, word for word. Uses one short call to \(store.settings.text.label).")
                    }
                }
            }
            .screenBackground()
            .navigationTitle("Opening line")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Another ten") { Task { await load() } }.disabled(loading)
                }
            }
        }
        .task { if hooks.isEmpty { await load() } }
    }

    private func load() async {
        loading = true
        failure = nil
        do {
            let provider = try TextProviderFactory.make(store.settings.text, settings: store.settings)
            hooks = try await HookBench.generate(topic: topic, provider: provider)
        } catch {
            failure = error.localizedDescription
        }
        loading = false
    }
}

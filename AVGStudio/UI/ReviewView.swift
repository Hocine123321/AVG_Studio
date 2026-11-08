import SwiftUI

/// Identifies the video a review sheet is open for.
struct ReviewTarget: Identifiable {
    let id: String
}

/// The review gate's sheet: read the script scene by scene, fix the wording
/// and each scene's image prompt, then continue. The voice and the captions
/// both read these scenes, so what is fixed here is what gets recorded.
struct ReviewView: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    let projectID: String

    @State private var drafts: [VideoScene] = []
    @State private var loaded = false

    private var channel: Channel? { store.project(projectID).map { store.channel(for: $0) } }
    private var showsPrompts: Bool { channel?.reviewStoryboard ?? false }
    /// The words are editable unless only the storyboard is being reviewed.
    private var editsText: Bool { (channel?.reviewScript ?? true) || !showsPrompts }

    var body: some View {
        NavigationStack {
            Form {
                if drafts.isEmpty {
                    Text("There are no scenes yet.").foregroundStyle(.secondary)
                }
                ForEach($drafts) { $scene in
                    Section {
                        if editsText {
                            TextField("What is said", text: $scene.text, axis: .vertical)
                        } else {
                            Text(scene.text).foregroundStyle(.secondary)
                        }
                        if showsPrompts {
                            TextField("Image prompt", text: $scene.imagePrompt, axis: .vertical)
                                .font(.callout)
                        }
                    } header: {
                        Text(header(scene))
                    }
                }
                Section {
                } footer: {
                    Text("Changes are saved when you continue. The voice and the captions use this text, and images are made from these prompts.")
                }
            }
            .screenBackground()
            .navigationTitle(channel.map { ReviewGatePolicy.title($0) } ?? "Review")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Continue") {
                        Haptics.success()
                        store.saveReview(projectID, edits: drafts.map {
                            ReviewGatePolicy.SceneEdit(index: $0.index, text: $0.text, imagePrompt: $0.imagePrompt)
                        })
                        store.continueAfterReview(projectID)
                        dismiss()
                    }
                    .disabled(drafts.isEmpty)
                }
            }
        }
        .onAppear {
            guard !loaded else { return }
            loaded = true
            drafts = (store.project(projectID)?.scenes ?? []).sorted { $0.index < $1.index }
        }
    }

    private func header(_ scene: VideoScene) -> String {
        let number = "Scene \(scene.index + 1)"
        guard let label = scene.label, !label.isEmpty else { return number }
        return "\(number) · \(label)"
    }
}

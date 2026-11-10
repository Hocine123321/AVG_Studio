import SwiftUI

/// Saved recipes: tap one to apply it (after a confirmation), swipe to delete.
/// Used from the channel editor and from Create; `onPick` decides what applying means there.
struct RecipePickerSheet: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    /// The channel the recipe would change, for the confirmation's wording.
    let channelName: String
    let onPick: (Recipe) -> Void

    @State private var pending: Recipe?

    var body: some View {
        NavigationStack {
            List {
                if store.recipes.isEmpty {
                    ContentUnavailableView("No recipes yet", systemImage: "square.stack.3d.up",
                                           description: Text("Open a channel and choose Save as recipe to keep its look, sound and editing for later."))
                } else {
                    Section {
                        ForEach(store.recipes) { recipe in
                            Button {
                                Haptics.light()
                                pending = recipe
                            } label: {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(recipe.name).font(.body.weight(.semibold)).foregroundStyle(Theme.ink)
                                    Text(recipe.summary).font(.footnote).foregroundStyle(Theme.ink2).lineLimit(2)
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .contentShape(.rect)
                            }
                            .swipeActions {
                                Button("Delete", systemImage: "trash", role: .destructive) {
                                    Haptics.warning()
                                    store.deleteRecipe(recipe.id)
                                }
                            }
                        }
                    } footer: {
                        Text("Applying a recipe changes a channel’s look, sound and editing settings. Its name, niche, language and providers stay as they are.")
                    }
                }
            }
            .screenBackground()
            .navigationTitle("Recipes")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done", role: .close) { dismiss() } } }
            .confirmationDialog("Apply “\(pending?.name ?? "")” to \(channelName)?",
                                isPresented: Binding(get: { pending != nil }, set: { if !$0 { pending = nil } }),
                                titleVisibility: .visible) {
                Button("Apply") {
                    if let recipe = pending {
                        Haptics.success()
                        onPick(recipe)
                    }
                    pending = nil
                    dismiss()
                }
            } message: {
                Text("Its look, sound and editing settings replace this channel’s.")
            }
        }
    }
}

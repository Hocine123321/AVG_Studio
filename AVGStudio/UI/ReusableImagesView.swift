import SwiftUI
import PhotosUI

/// Manages the shared image recycling pool - see `ReusableImage`'s doc
/// comment in Models.swift. The PC has no equivalent screen at all (its
/// pool is a hand-edited `images.json`), so this is a genuine improvement,
/// not a parity gap: pick a photo, describe what it shows, and the writer
/// can reference it by that description when splitting a script into scenes.
struct ReusableImagesView: View {
    @Environment(Store.self) private var store
    @State private var picked: PhotosPickerItem?
    @State private var editingID: String?
    @State private var draftDescription = ""
    @State private var isImporting = false

    var body: some View {
        List {
            Section {
                PhotosPicker(selection: $picked, matching: .images) {
                    Label(isImporting ? "Adding…" : "Add from Photos", systemImage: "plus.circle.fill")
                }
                .disabled(isImporting)
            } footer: {
                Text("The writer can reuse one of these instead of generating fresh art for a scene, when a channel's Recycle setting is on.")
            }
            if !store.reusableImages.isEmpty {
                Section("Pool (\(store.reusableImages.count))") {
                    ForEach(store.reusableImages) { r in
                        HStack(spacing: 12) {
                            thumb(r)
                                .frame(width: 56, height: 56)
                                .clipShape(.rect(cornerRadius: 10))
                            if editingID == r.id {
                                TextField("What does this image show?", text: $draftDescription, axis: .vertical)
                                    .onSubmit { commit(r.id) }
                                Button("Save") { commit(r.id) }.buttonStyle(.borderless)
                            } else {
                                Text(r.description.isEmpty ? "No description" : r.description)
                                    .font(.subheadline)
                                    .foregroundStyle(r.description.isEmpty ? .secondary : .primary)
                                    .onTapGesture { editingID = r.id; draftDescription = r.description }
                            }
                        }
                        .swipeActions {
                            Button("Delete", systemImage: "trash", role: .destructive) { store.deleteReusableImage(r.id) }
                        }
                    }
                }
            }
        }
        .screenBackground()
        .navigationTitle("Reusable images")
        .onChange(of: picked) { _, item in
            guard let item else { return }
            isImporting = true
            Task {
                defer { isImporting = false; picked = nil }
                if let data = try? await item.loadTransferable(type: Data.self) {
                    let r = store.addReusableImage(data: data, description: "")
                    editingID = r.id
                    draftDescription = ""
                }
            }
        }
    }

    private func commit(_ id: String) {
        store.updateReusableImage(id, description: draftDescription)
        editingID = nil
    }

    private func thumb(_ r: ReusableImage) -> some View {
        ZStack {
            Rectangle().fill(.quaternary)
            CachedThumb(url: store.reusableImageURL(r), maxPixel: 168)
        }
    }
}

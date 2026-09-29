import Photos

/// Saves a finished video to the photo library and REPORTS the outcome.
///
/// The old export row called `UISaveVideoAtPathToSavedPhotosAlbum(_, nil,
/// nil, nil)` - fire and forget - and showed "Saved to Photos" whether or
/// not it worked, including when the user had denied access. This asks for
/// add-only permission, waits for the change to land, and throws a
/// readable error otherwise.
enum PhotoSaver {
    enum SaveError: LocalizedError {
        case denied
        var errorDescription: String? {
            "Allow AVG Studio to add to Photos in iOS Settings to save videos."
        }
    }

    static func saveVideo(at url: URL) async throws {
        let status = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
        guard status == .authorized || status == .limited else { throw SaveError.denied }
        try await PHPhotoLibrary.shared().performChanges {
            _ = PHAssetChangeRequest.creationRequestForAssetFromVideo(atFileURL: url)
        }
    }
}

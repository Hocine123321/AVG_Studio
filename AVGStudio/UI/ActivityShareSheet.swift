import SwiftUI
import UIKit

/// Thin SwiftUI wrapper around `UIActivityViewController`. Needed
/// specifically for `CapCutExporter`'s output: `ShareLink`'s
/// `Transferable` story is built around single files, and CapCut export
/// hands back a whole *directory*. `UIActivityViewController` already
/// handles folder URLs natively (Save to Files copies the directory;
/// AirDrop/Messages zip it on the way out) - the smallest correct bridge,
/// rather than hand-rolling a zip archiver this app has no other need for.
struct ActivityShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    var onDismiss: (() -> Void)?

    func makeUIViewController(context: Context) -> UIActivityViewController {
        let vc = UIActivityViewController(activityItems: items, applicationActivities: nil)
        vc.completionWithItemsHandler = { _, _, _, _ in onDismiss?() }
        return vc
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}

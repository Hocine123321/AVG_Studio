import Foundation

/// Review gates: after the script is written, a video can wait for the person
/// to read and fix its scenes (and the image prompt of each) BEFORE anything
/// that costs money is made. Per channel, off by default, and never an
/// obstacle: "Continue" in Studio carries on exactly where it stopped.
///
/// (Reviewing the edit is the existing edit gate, `EditingMode`.)
enum ReviewGatePolicy {
    /// Whether a video on this channel should wait after its script is written.
    static func holds(_ channel: Channel) -> Bool {
        channel.reviewScript || channel.reviewStoryboard
    }

    /// What the waiting card and the review sheet are called.
    static func title(_ channel: Channel) -> String {
        switch (channel.reviewScript, channel.reviewStoryboard) {
        case (true, true): return "Review the script and storyboard"
        case (false, true): return "Review the storyboard"
        default: return "Review the script"
        }
    }

    /// What the person changed about one scene.
    struct SceneEdit: Equatable {
        var index: Int
        var text: String
        var imagePrompt: String
    }

    /// The scenes with the person's changes applied, and the narration kept in
    /// step with them (the voice and the captions both read the scenes). A
    /// scene whose text was emptied keeps its old text: a review never deletes
    /// a scene. An emptied image prompt is allowed (the scene's text is used).
    static func apply(_ edits: [SceneEdit], to scenes: [VideoScene]) -> (scenes: [VideoScene], narration: String) {
        var updated = scenes
        for edit in edits {
            guard let i = updated.firstIndex(where: { $0.index == edit.index }) else { continue }
            let text = edit.text.trimmed
            if !text.isEmpty { updated[i].text = text }
            updated[i].imagePrompt = edit.imagePrompt.trimmed
        }
        let narration = updated.sorted { $0.index < $1.index }.map(\.text).joined(separator: " ")
        return (updated, narration)
    }
}

import UIKit

// Looking at pictures: one request, many small images.
//
// Used only by the "Smart" edit level to find where the subject sits in each
// scene's picture. The images are reduced to thumbnails first (the answer is a
// position, not a description, so detail is wasted), which keeps the request
// to a few thousand tokens - comfortably inside a free Google tier. Failure is
// never fatal: the caller keeps the default crops.

enum SubjectImages {
    /// A JPEG of `url` no larger than `longEdge` pixels on its long side.
    static func thumbnail(_ url: URL, longEdge: CGFloat = 384) -> Data? {
        guard let image = UIImage(contentsOfFile: url.path), image.size.width > 0, image.size.height > 0 else { return nil }
        let scale = min(1, longEdge / max(image.size.width, image.size.height))
        let size = CGSize(width: max(1, image.size.width * scale), height: max(1, image.size.height * scale))
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        return UIGraphicsImageRenderer(size: size, format: format).jpegData(withCompressionQuality: 0.7) { _ in
            image.draw(in: CGRect(origin: .zero, size: size))
        }
    }
}

struct GeminiVision {
    let key: String
    let model: String

    static let instruction = """
        You are helping crop pictures for a video. For each numbered picture, give the point where its main \
        subject is centred (a face, the main object, or the focus of the image) as x and y fractions from 0 to 1, \
        measured from the top-left corner (x to the right, y downward). If there is no clear subject, use \
        x 0.5 and y 0.45. Reply with JSON only, exactly like this: \
        {"pictures":[{"picture":1,"x":0.5,"y":0.4},{"picture":2,"x":0.3,"y":0.5}]}
        """

    /// The request body: the instruction, then "Picture N:" and the image for each.
    static func requestBody(_ pictures: [(scene: Int, jpeg: Data)]) -> [String: Any] {
        var parts: [[String: Any]] = [["text": instruction]]
        for picture in pictures {
            parts.append(["text": "Picture \(picture.scene + 1):"])
            parts.append(["inlineData": ["mimeType": "image/jpeg", "data": picture.jpeg.base64EncodedString()]])
        }
        return [
            "contents": [["role": "user", "parts": parts]],
            "generationConfig": ["responseMimeType": "application/json", "temperature": 0.1],
        ]
    }

    /// The model's text, from a `generateContent` reply.
    static func text(in response: [String: Any]) -> String? {
        let parts = (((response["candidates"] as? [[String: Any]])?.first?["content"] as? [String: Any])?["parts"] as? [[String: Any]]) ?? []
        let joined = parts.compactMap { $0["text"] as? String }.joined()
        return joined.isEmpty ? nil : joined
    }

    func locateSubjects(_ pictures: [(scene: Int, jpeg: Data)]) async throws -> [FocusPoint] {
        guard !pictures.isEmpty else { return [] }
        let url = URL(string: "https://generativelanguage.googleapis.com/v1beta/models/\(model.trimmed):generateContent")!
        let body = Self.requestBody(pictures)
        let data = try await HTTP.retrying({
            try await HTTP.send(url, headers: ["x-goog-api-key": key], json: body, provider: "gemini")
        }, provider: "gemini")
        guard let text = Self.text(in: try HTTP.object(data)) else {
            throw AVGError.message("Google returned no answer.")
        }
        return SubjectFocus.parse(text, valid: Set(pictures.map { $0.scene }))
    }
}

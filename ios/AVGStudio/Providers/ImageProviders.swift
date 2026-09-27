import Foundation
import UIKit

protocol ImageProvider {
    func generate(prompt: String, size: CGSize) async throws -> Data
}

private func validImage(_ d: Data) throws -> Data {
    guard UIImage(data: d) != nil else { throw AVGError.message("The image service returned something that isn't an image.") }
    return d
}

struct PollinationsImages: ImageProvider {
    func generate(prompt: String, size: CGSize) async throws -> Data {
        let enc = prompt.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed.subtracting(CharacterSet(charactersIn: "/?#"))) ?? "image"
        let seed = Int.random(in: 1...999_999)
        let url = URL(string: "https://image.pollinations.ai/prompt/\(enc)?width=\(Int(size.width))&height=\(Int(size.height))&nologo=true&seed=\(seed)")!
        let d = try await HTTP.retrying(4) { try await HTTP.send(url, method: "GET", timeout: 120) }
        return try validImage(d)
    }
}

struct GeminiImages: ImageProvider {
    let key: String
    func generate(prompt: String, size: CGSize) async throws -> Data {
        let url = URL(string: "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent")!
        let ratio = size.width > size.height ? "16:9" : (size.width < size.height ? "9:16" : "1:1")
        let body: [String: Any] = [
            "contents": [["parts": [["text": prompt]]]],
            "generationConfig": ["responseModalities": ["IMAGE"], "imageConfig": ["aspectRatio": ratio]],
        ]
        let data = try await HTTP.retrying { try await HTTP.send(url, headers: ["x-goog-api-key": key], json: body) }
        let o = try HTTP.object(data)
        let parts = (((o["candidates"] as? [[String: Any]])?.first?["content"] as? [String: Any])?["parts"] as? [[String: Any]]) ?? []
        for p in parts {
            if let inline = (p["inlineData"] ?? p["inline_data"]) as? [String: Any],
               let b64 = inline["data"] as? String, let d = Data(base64Encoded: b64) {
                return try validImage(d)
            }
        }
        throw AVGError.message("Gemini returned no image.")
    }
}

struct OpenAIImages: ImageProvider {
    let key: String
    func generate(prompt: String, size: CGSize) async throws -> Data {
        let url = URL(string: "https://api.openai.com/v1/images/generations")!
        let sz = size.width > size.height ? "1536x1024" : (size.width < size.height ? "1024x1536" : "1024x1024")
        let body: [String: Any] = ["model": "gpt-image-1", "prompt": prompt, "size": sz, "n": 1]
        let data = try await HTTP.retrying { try await HTTP.send(url, headers: ["Authorization": "Bearer \(key)"], json: body) }
        let o = try HTTP.object(data)
        guard let b64 = (o["data"] as? [[String: Any]])?.first?["b64_json"] as? String, let d = Data(base64Encoded: b64) else {
            throw AVGError.message("OpenAI returned no image.")
        }
        return try validImage(d)
    }
}

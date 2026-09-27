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
        let d = try await HTTP.retrying({ try await HTTP.send(url, method: "GET", timeout: 120, provider: "pollinations") }, provider: "pollinations")
        return try validImage(d)
    }
}

/// Ported from avg.providers.images.community.StableHordeImageProvider - a
/// free, keyless, crowd-sourced Stable Diffusion cluster. Async by nature:
/// submit a job, poll a shared community queue (which the PC calls out as
/// the ONE async provider in its package), fetch the result once done.
/// Every constant below (timeouts, poll backoff, sampler/cfg/steps, the
/// per-ratio size table) is copied from the PC's own module verbatim.
///
/// One simplification: the PC calls a live `GET /status/models?type=image`
/// catalogue to offer a model picker; this always requests
/// `"stable_diffusion"`, AI Horde's broadest-availability baseline model,
/// rather than fetching that catalogue per generation. The horde's own
/// "faulted: no workers available" handling below still covers the case
/// where even that model has nobody online to serve it.
struct StableHordeImages: ImageProvider {
    private let base = "https://aihorde.net/api/v2"
    private let anonymousKey = "0000000000"
    private let model = "stable_diffusion"
    private let maxWaitSeconds: Double = 180

    func generate(prompt: String, size: CGSize) async throws -> Data {
        let (w, h) = Self.sizeFor(size)
        let key = Keychain.get(.stablehorde) ?? anonymousKey
        let headers = ["apikey": key, "Client-Agent": "AVG-Studio-iOS:1.0:local"]

        let jobID = try await submit(prompt: prompt, width: w, height: h, headers: headers)
        do {
            return try await poll(jobID: jobID, headers: headers)
        } catch {
            if error is CancellationError { await cancelJob(jobID, headers: headers) }
            throw error
        }
    }

    private static func sizeFor(_ size: CGSize) -> (Int, Int) {
        // Matches _SIZE_BY_RATIO exactly: 1:1, 9:16, 16:9.
        if size.width > size.height { return (1344, 768) }
        if size.width < size.height { return (768, 1344) }
        return (1024, 1024)
    }

    private func submit(prompt: String, width: Int, height: Int, headers: [String: String]) async throws -> String {
        let body: [String: Any] = [
            "prompt": prompt,
            "params": ["sampler_name": "k_euler_a", "cfg_scale": 7, "steps": 30, "width": width, "height": height, "n": 1],
            "models": [model],
            "nsfw": false,
            "censor_nsfw": true,
            "r2": true, // CDN URL response instead of inline base64 - matches the PC's own reasoning (avoids a huge body).
        ]
        let data = try await HTTP.retrying({ try await HTTP.send(URL(string: "\(base)/generate/async")!, headers: headers, json: body, timeout: 60, provider: "stablehorde") }, provider: "stablehorde")
        let o = try HTTP.object(data)
        guard let id = o["id"] as? String else { throw AVGError.message("Stable Horde: no job ID returned.") }
        return id
    }

    private func poll(jobID: String, headers: [String: String]) async throws -> Data {
        var elapsed: Double = 0
        var wait: Double = 4 // _POLL_START_SECONDS
        while elapsed < maxWaitSeconds {
            try Task.checkCancellation()
            try await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
            elapsed += wait
            wait = min(8, wait + 1) // _POLL_MAX_SECONDS / _POLL_STEP_SECONDS

            let data = try await HTTP.retrying({ try await HTTP.send(URL(string: "\(base)/generate/check/\(jobID)")!, method: "GET", headers: headers, timeout: 30, provider: "stablehorde") }, provider: "stablehorde")
            let check = try HTTP.object(data)
            if (check["faulted"] as? Bool) == true {
                await cancelJob(jobID, headers: headers)
                throw AVGError.message("Stable Horde: generation faulted (no workers available or content blocked).")
            }
            if (check["done"] as? Bool) == true {
                return try await fetchResult(jobID: jobID, headers: headers)
            }
        }
        await cancelJob(jobID, headers: headers)
        throw AVGError.message("Stable Horde: timed out waiting for generation after \(Int(maxWaitSeconds))s.")
    }

    private func fetchResult(jobID: String, headers: [String: String]) async throws -> Data {
        let data = try await HTTP.retrying({ try await HTTP.send(URL(string: "\(base)/generate/status/\(jobID)")!, method: "GET", headers: headers, timeout: 30, provider: "stablehorde") }, provider: "stablehorde")
        let o = try HTTP.object(data)
        guard let gens = o["generations"] as? [[String: Any]], let gen = gens.first else {
            throw AVGError.message("Stable Horde: done but no generation in response.")
        }
        if (gen["censored"] as? Bool) == true { throw AVGError.message("Stable Horde: image was censored.") }
        guard let img = gen["img"] as? String, !img.isEmpty else {
            throw AVGError.message("Stable Horde: generation returned no image data.")
        }
        if img.hasPrefix("http://") || img.hasPrefix("https://") {
            let d = try await HTTP.retrying({ try await HTTP.send(URL(string: img)!, method: "GET", timeout: 60, provider: "stablehorde") }, provider: "stablehorde")
            return try validImage(d)
        }
        guard let d = Data(base64Encoded: img) else { throw AVGError.message("Stable Horde: unreadable image data.") }
        return try validImage(d)
    }

    private func cancelJob(_ jobID: String, headers: [String: String]) async {
        _ = try? await HTTP.send(URL(string: "\(base)/generate/status/\(jobID)")!, method: "DELETE", headers: headers, timeout: 15, provider: "stablehorde")
    }
}

struct GeminiImages: ImageProvider {
    let key: String
    func generate(prompt: String, size: CGSize) async throws -> Data {
        // Same belt-and-suspenders auth as GeminiText: header AND `key=` query param.
        var comps = URLComponents(string: "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent")!
        comps.queryItems = [URLQueryItem(name: "key", value: key)]
        let url = comps.url!
        let ratio = size.width > size.height ? "16:9" : (size.width < size.height ? "9:16" : "1:1")
        let body: [String: Any] = [
            "contents": [["parts": [["text": prompt]]]],
            "generationConfig": ["responseModalities": ["IMAGE"], "imageConfig": ["aspectRatio": ratio]],
        ]
        let data = try await HTTP.retrying({ try await HTTP.send(url, headers: ["x-goog-api-key": key], json: body, provider: "gemini") }, provider: "gemini")
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
        let data = try await HTTP.retrying({ try await HTTP.send(url, headers: ["Authorization": "Bearer \(key)"], json: body, provider: "openai") }, provider: "openai")
        let o = try HTTP.object(data)
        guard let b64 = (o["data"] as? [[String: Any]])?.first?["b64_json"] as? String, let d = Data(base64Encoded: b64) else {
            throw AVGError.message("OpenAI returned no image.")
        }
        return try validImage(d)
    }
}

import Foundation
#if canImport(FoundationModels)
import FoundationModels
#endif

protocol TextProvider {
    func complete(_ prompt: String, system: String?) async throws -> String
}

/// iOS 26 on-device model: free, offline, private.
struct AppleText: TextProvider {
    static var isAvailable: Bool {
        #if canImport(FoundationModels)
        if case .available = SystemLanguageModel.default.availability { return true }
        #endif
        return false
    }

    func complete(_ prompt: String, system: String?) async throws -> String {
        #if canImport(FoundationModels)
        guard AppleText.isAvailable else {
            throw AVGError.message("Apple Intelligence isn't available on this iPhone. Turn it on in iOS Settings or pick another writer.")
        }
        let session = LanguageModelSession(instructions: system ?? "You are a helpful video script writer.")
        let reply = try await session.respond(to: prompt)
        return reply.content
        #else
        throw AVGError.message("Apple Intelligence needs iOS 26.")
        #endif
    }
}

struct GeminiText: TextProvider {
    let key: String
    let model: String

    func complete(_ prompt: String, system: String?) async throws -> String {
        // Header-only auth. The PC port sent the key in the header AND a
        // `key=` query param ("a genuine quirk ... rather than guessing").
        // On this side that duplicate is pure downside: URLs end up in system
        // logs, URLError userInfo and proxies. The header is Google's
        // documented auth, and this app's Gemini model-list call
        // (ModelCatalogSources) already works with the header alone.
        let url = URL(string: "https://generativelanguage.googleapis.com/v1beta/models/\(model):generateContent")!
        var body: [String: Any] = ["contents": [["role": "user", "parts": [["text": prompt]]]]]
        if let system { body["systemInstruction"] = ["parts": [["text": system]]] }
        let data = try await HTTP.retrying({ try await HTTP.send(url, headers: ["x-goog-api-key": key], json: body, provider: "gemini") }, provider: "gemini")
        let o = try HTTP.object(data)
        guard let c = (o["candidates"] as? [[String: Any]])?.first,
              let parts = (c["content"] as? [String: Any])?["parts"] as? [[String: Any]] else {
            // Mirrors the PC's own care here: a 200 with no candidates can
            // mean a safety block (promptFeedback/finishReason), not just
            // "no text" - surface that instead of a generic message when
            // it's present, so Settings/Activity shows something actionable.
            if let feedback = o["promptFeedback"] as? [String: Any], let reason = feedback["blockReason"] as? String {
                throw AVGError.message("Gemini blocked this prompt (\(reason)). Try rephrasing the topic.")
            }
            throw AVGError.message("Gemini returned no text.")
        }
        return parts.compactMap { $0["text"] as? String }.joined()
    }
}

/// OpenAI-compatible chat endpoint (Groq, Pollinations).
struct ChatText: TextProvider {
    let url: URL
    let key: String?
    let model: String
    /// Feeds `classify()` - "groq" or "pollinations", set by the matching
    /// factory below rather than guessed from the URL, so a provider-
    /// specific pattern (see `ErrorPolicy.swift`) is never silently missed.
    let provider: String

    func complete(_ prompt: String, system: String?) async throws -> String {
        var msgs: [[String: String]] = []
        if let system { msgs.append(["role": "system", "content": system]) }
        msgs.append(["role": "user", "content": prompt])
        var headers: [String: String] = [:]
        if let key { headers["Authorization"] = "Bearer \(key)" }
        let body: [String: Any] = ["model": model, "messages": msgs]
        let data = try await HTTP.retrying({ try await HTTP.send(url, headers: headers, json: body, provider: provider) }, provider: provider)
        let o = try HTTP.object(data)
        guard let msg = ((o["choices"] as? [[String: Any]])?.first?["message"] as? [String: Any])?["content"] as? String else {
            throw AVGError.message("The writer returned no text.")
        }
        return msg
    }

    static func groq(key: String, model: String) -> ChatText {
        ChatText(url: URL(string: "https://api.groq.com/openai/v1/chat/completions")!, key: key, model: model, provider: "groq")
    }
    static func pollinations(key: String?, model: String) -> ChatText {
        ChatText(url: URL(string: "https://text.pollinations.ai/openai")!, key: key, model: model, provider: "pollinations")
    }
}

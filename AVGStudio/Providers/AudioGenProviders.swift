import Foundation

// Making audio with AI - the paid, optional way to fill the shelf.
//
// Uploading your own music and sounds is always the free path (AudioLibrary).
// These two providers exist for the moments you have nothing to upload: a
// background bed in a mood you name (Google Lyria) and a starter set of short
// sound effects (ElevenLabs). Both are one request, one file, paid per result,
// and the result goes onto the same shelf - generate once, reuse for every
// later video.
//
// Request shapes come from Google's and ElevenLabs' own docs (checked
// October 2026). Model ids are passed in, never baked into the call, because
// preview ids get retired.

/// Builds the text prompts. Pure, so what we ask for is pinned by tests.
enum MusicPrompt {
    static let moods = ["calm", "upbeat", "cinematic", "tense", "warm", "playful", "dark", "hopeful"]

    /// A bed that sits UNDER a narrator: instrumental, even, no surprises.
    /// (Lyria blocks prompts naming real artists or copyrighted lyrics, so the
    /// template asks for neither.)
    static func background(mood: String, detail: String) -> String {
        var parts = ["An instrumental background music track for a narrated video.", "Mood: \(mood)."]
        let extra = detail.trimmed
        if !extra.isEmpty { parts.append("Style notes: \(extra).") }
        parts.append("No vocals and no lyrics. Steady, low dynamic range so a spoken voice stays clear on top, "
                     + "no sudden loud moments, a smooth consistent groove, and a clean ending.")
        return parts.joined(separator: " ")
    }
}

/// The five sounds that cover every `SFXMoment`.
enum StarterSounds {
    struct Item {
        let moment: SFXMoment
        let name: String
        let prompt: String
        let seconds: Double
    }

    /// Tag on every generated starter sound, next to its moment's raw value.
    static let tag = "starter"

    static let items: [Item] = [
        Item(moment: .impact, name: "Starter · Impact",
             prompt: "A deep, punchy cinematic impact hit with a short clean tail, no music", seconds: 1.5),
        Item(moment: .riser, name: "Starter · Riser",
             prompt: "A short rising tension swell that builds upward and stops abruptly, no music", seconds: 2.0),
        Item(moment: .whoosh, name: "Starter · Whoosh",
             prompt: "A fast, clean air whoosh transition sound, no music", seconds: 0.8),
        Item(moment: .pop, name: "Starter · Pop",
             prompt: "A soft, light bubble pop interface sound, friendly and short, no music", seconds: 0.5),
        Item(moment: .softHit, name: "Starter · Soft hit",
             prompt: "A soft, warm low thud, a gentle cinematic hit, no music", seconds: 1.0),
    ]

    /// Gives every moment that has no sound yet the starter sound for it.
    /// Never replaces a sound the person already chose.
    static func fill(_ map: [String: String], from library: [AudioItem]) -> [String: String] {
        var out = map
        for moment in SFXMoment.allCases where out[moment.rawValue] == nil {
            if let match = library.first(where: { $0.kind == .sfx && $0.tags.contains(tag) && $0.tags.contains(moment.rawValue) }) {
                out[moment.rawValue] = match.id
            }
        }
        return out
    }
}

/// Google Lyria through the Gemini API (`generateContent`, audio out).
struct GeminiMusic {
    let key: String

    /// 30-second clips, about $0.04 each.
    static let clipModel = "lyria-3-clip-preview"
    /// Full songs of a couple of minutes, about $0.08 each. Reported id; the
    /// live model list is the authority if it changes.
    static let fullModel = "lyria-3.5"

    func generate(prompt: String, model: String) async throws -> Data {
        let url = URL(string: "https://generativelanguage.googleapis.com/v1beta/models/\(model.trimmed):generateContent")!
        let body: [String: Any] = [
            "contents": [["parts": [["text": prompt]]]],
            "generationConfig": ["responseModalities": ["AUDIO", "TEXT"]],
        ]
        let data = try await HTTP.retrying({
            try await HTTP.send(url, headers: ["x-goog-api-key": key], json: body, provider: "gemini")
        }, provider: "gemini")
        guard let audio = Self.audio(in: try HTTP.object(data)) else {
            throw AVGError.message("Google returned no audio. The prompt may have been blocked; try different words.")
        }
        return audio
    }

    /// The first audio part of a `generateContent` reply, decoded.
    static func audio(in response: [String: Any]) -> Data? {
        let parts = (((response["candidates"] as? [[String: Any]])?.first?["content"] as? [String: Any])?["parts"] as? [[String: Any]]) ?? []
        for part in parts {
            guard let inline = (part["inlineData"] ?? part["inline_data"]) as? [String: Any],
                  let mime = (inline["mimeType"] ?? inline["mime_type"]) as? String, mime.hasPrefix("audio"),
                  let b64 = inline["data"] as? String, let data = Data(base64Encoded: b64), !data.isEmpty else { continue }
            return data
        }
        return nil
    }
}

/// ElevenLabs text-to-sound-effects.
struct ElevenLabsEffects {
    let key: String

    func generate(prompt: String, seconds: Double) async throws -> Data {
        let url = URL(string: "https://api.elevenlabs.io/v1/sound-generation?output_format=mp3_44100_128")!
        let body: [String: Any] = [
            "text": prompt,
            "duration_seconds": min(22, max(0.5, seconds)),
            "prompt_influence": 0.5,
        ]
        let data = try await HTTP.retrying({
            try await HTTP.send(url, headers: ["xi-api-key": key, "Accept": "audio/mpeg"], json: body, provider: "elevenlabs")
        }, provider: "elevenlabs")
        guard !data.isEmpty else { throw AVGError.message("ElevenLabs returned no audio.") }
        return data
    }
}

import Foundation

/// The `ModelCatalog.fetch` closures this app needs - one per provider
/// endpoint that can meaningfully offer a live list. Deliberately NOT
/// ported for every provider with a "model" setting: OpenAI's `gpt-image-1`
/// is the one model that call shape offers (`OpenAIImages` hardcodes it) -
/// a picker with exactly one entry has nothing to pick. Gemini's image and
/// voice-model pickers below are NOT ports of a PC feature - the PC's own
/// `catalogues.py` explicitly gives Gemini no image-model catalogue
/// ("OpenAI and Gemini publish no image-model catalogue" through the
/// endpoint IT restricts itself to) and has no Gemini voice provider at
/// all - they reuse GeminiText's own `/v1beta/models` fetch with a
/// different keep-filter, added on request.
///
/// Every function here returns already-sorted, already-filtered entries -
/// `ModelCatalog` itself has no filtering opinion, on purpose, so a new
/// source never has to fight the generic class's assumptions about shape.
enum ModelCatalogSources {
    // MARK: Gemini - shared catalogue fetch

    /// `GET /v1beta/models` (paginated, `pageSize=1000` so this is a
    /// single round trip in practice) - same endpoint and the same
    /// `supportedGenerationMethods` filter as Palabra's own
    /// `GeminiClient.listModels` (`Palabra/Services/AI/GeminiClient.swift`).
    /// `keep` decides which of the three Gemini catalogues (text/image/
    /// voice) this call is for - see the three thin wrappers below - and
    /// runs on the BARE id (the `models/` prefix already stripped), since
    /// every call site's own URL already supplies that segment literally
    /// and the stored setting has to match.
    private static func geminiModels(key: String, keep: @escaping (String) -> Bool) async throws -> [CatalogEntry] {
        var all: [CatalogEntry] = []
        var pageToken: String?
        repeat {
            var comps = URLComponents(string: "https://generativelanguage.googleapis.com/v1beta/models")!
            var items = [URLQueryItem(name: "pageSize", value: "1000")]
            if let pageToken { items.append(URLQueryItem(name: "pageToken", value: pageToken)) }
            comps.queryItems = items
            let data = try await HTTP.retrying(
                { try await HTTP.send(comps.url!, method: "GET", headers: ["x-goog-api-key": key], timeout: 30, provider: "gemini") },
                provider: "gemini"
            )
            let o = try HTTP.object(data)
            let models = (o["models"] as? [[String: Any]]) ?? []
            for m in models {
                guard let name = m["name"] as? String else { continue }
                let methods = (m["supportedGenerationMethods"] as? [String]) ?? []
                guard methods.contains("generateContent") else { continue }
                let bare = name.hasPrefix("models/") ? String(name.dropFirst("models/".count)) : name
                guard keep(bare) else { continue }
                let display = (m["displayName"] as? String) ?? bare
                let desc = m["description"] as? String
                all.append(CatalogEntry(id: bare, label: display, sublabel: desc))
            }
            pageToken = o["nextPageToken"] as? String
        } while pageToken != nil
        // Newest-first, numeric-aware - matches Palabra's ModelFilter.
        return all.sorted { $0.id.localizedStandardCompare($1.id) == .orderedDescending }
    }

    // MARK: Gemini text models

    /// Excludes image/embedding/TTS-only ids so this offers exactly the
    /// models `GeminiText.complete` can actually call.
    static func geminiText(key: String) async throws -> [CatalogEntry] {
        try await geminiModels(key: key) { bare in
            !bare.localizedCaseInsensitiveContains("embedding")
                && !bare.localizedCaseInsensitiveContains("aqa")
                && !bare.localizedCaseInsensitiveContains("image")
                && !bare.localizedCaseInsensitiveContains("tts")
        }
    }

    // MARK: Gemini image models

    /// The mirror image of `geminiText`'s exclusion: KEEPS only ids that
    /// look like an image-output model (`gemini-2.5-flash-image`, any
    /// `imagen-*`), which `GeminiImages.generate`'s `responseModalities:
    /// ["IMAGE"]` call needs.
    static func geminiImageModels(key: String) async throws -> [CatalogEntry] {
        try await geminiModels(key: key) { bare in
            bare.localizedCaseInsensitiveContains("image") || bare.lowercased().hasPrefix("imagen")
        }
    }

    // MARK: Gemini TTS models

    /// KEEPS only ids containing "tts" - the models `GeminiVoice.
    /// synthesize`'s `responseModalities: ["AUDIO"]` call needs. The
    /// PREBUILT VOICE NAME (Kore, Puck, ...) is a separate, fixed setting -
    /// see `GeminiVoice.prebuiltVoices` - because Google publishes that
    /// list in docs, not through an endpoint this could fetch.
    static func geminiVoiceModels(key: String) async throws -> [CatalogEntry] {
        try await geminiModels(key: key) { bare in bare.localizedCaseInsensitiveContains("tts") }
    }

    // MARK: Groq text models

    /// `GET /openai/v1/models` (OpenAI-compatible shape). Groq's catalogue
    /// mixes chat models in with Whisper transcription, guard/moderation,
    /// and PlayAI TTS entries that `ChatText`'s `/chat/completions` call
    /// can't use - excluded by substring the same best-effort way
    /// Palabra's `ModelFilter` excludes non-text Gemini models, since
    /// neither endpoint labels "chat-capable" explicitly.
    static func groqText(key: String) async throws -> [CatalogEntry] {
        let url = URL(string: "https://api.groq.com/openai/v1/models")!
        let data = try await HTTP.retrying(
            { try await HTTP.send(url, method: "GET", headers: ["Authorization": "Bearer \(key)"], timeout: 30, provider: "groq") },
            provider: "groq"
        )
        let o = try HTTP.object(data)
        let excluded = ["whisper", "tts", "guard", "playai"]
        let models = (o["data"] as? [[String: Any]]) ?? []
        let entries = models.compactMap { m -> CatalogEntry? in
            guard let id = m["id"] as? String else { return nil }
            if (m["active"] as? Bool) == false { return nil }
            if excluded.contains(where: { id.localizedCaseInsensitiveContains($0) }) { return nil }
            let context = (m["context_window"] as? NSNumber)?.intValue
            let sub = context.map { "\($0 / 1024)K context" }
            return CatalogEntry(id: id, label: id, sublabel: sub)
        }
        return entries.sorted { $0.id.localizedStandardCompare($1.id) == .orderedAscending }
    }

    // MARK: ElevenLabs

    /// `GET /v1/voices` - every voice on the account, premade and custom
    /// alike. `sublabel` surfaces category + a label or two (accent,
    /// description) exactly because those are usually the deciding factor
    /// between two voices with similar names.
    static func elevenlabsVoices(key: String) async throws -> [CatalogEntry] {
        let url = URL(string: "https://api.elevenlabs.io/v1/voices")!
        let data = try await HTTP.retrying(
            { try await HTTP.send(url, method: "GET", headers: ["xi-api-key": key], timeout: 30, provider: "elevenlabs") },
            provider: "elevenlabs"
        )
        let o = try HTTP.object(data)
        let voices = (o["voices"] as? [[String: Any]]) ?? []
        let entries = voices.compactMap { v -> CatalogEntry? in
            guard let id = v["voice_id"] as? String else { return nil }
            let name = (v["name"] as? String) ?? id
            let category = v["category"] as? String
            let labels = (v["labels"] as? [String: Any])?.values.compactMap { $0 as? String } ?? []
            let bits = ([category].compactMap { $0 } + labels).prefix(3)
            return CatalogEntry(id: id, label: name, sublabel: bits.isEmpty ? nil : bits.joined(separator: " · "))
        }
        return entries.sorted { $0.label.localizedStandardCompare($1.label) == .orderedAscending }
    }

    /// `GET /v1/models` - a bare array, unlike `/v1/voices`. Filtered to
    /// `can_do_text_to_speech`: the same endpoint also lists this
    /// account's speech-to-text and voice-conversion models, which
    /// `ElevenLabsVoice.synthesize`'s `/text-to-speech` call can't use.
    static func elevenlabsModels(key: String) async throws -> [CatalogEntry] {
        let url = URL(string: "https://api.elevenlabs.io/v1/models")!
        let data = try await HTTP.retrying(
            { try await HTTP.send(url, method: "GET", headers: ["xi-api-key": key], timeout: 30, provider: "elevenlabs") },
            provider: "elevenlabs"
        )
        let models = try HTTP.array(data)
        let entries = models.compactMap { m -> CatalogEntry? in
            guard let id = m["model_id"] as? String, (m["can_do_text_to_speech"] as? Bool) == true else { return nil }
            let name = (m["name"] as? String) ?? id
            let desc = m["description"] as? String
            return CatalogEntry(id: id, label: name, sublabel: desc)
        }
        return entries.sorted { $0.label.localizedStandardCompare($1.label) == .orderedAscending }
    }

    // MARK: Stable Horde image models

    /// `GET /status/models?type=image` - no key required (this app's
    /// `StableHordeImages` already treats its key as optional, defaulting
    /// to the anonymous `"0000000000"`, same here). Sorted by worker
    /// `count` descending rather than alphabetically: on a crowd-sourced
    /// cluster, "how many workers are actually online for this model right
    /// now" is the whole reason to check this list instead of just reusing
    /// the app's baseline default, so it belongs first, not buried.
    static func stableHordeImage() async throws -> [CatalogEntry] {
        let url = URL(string: "https://aihorde.net/api/v2/status/models?type=image")!
        let data = try await HTTP.retrying(
            { try await HTTP.send(url, method: "GET", timeout: 30, provider: "stablehorde") },
            provider: "stablehorde"
        )
        let models = try HTTP.array(data)
        let entries = models.compactMap { m -> CatalogEntry? in
            guard let name = m["name"] as? String else { return nil }
            let count = (m["count"] as? NSNumber)?.intValue ?? 0
            return CatalogEntry(id: name, label: name, sublabel: "\(count) worker\(count == 1 ? "" : "s") online")
        }
        return entries.sorted { lhs, rhs in
            let l = Int(lhs.sublabel?.split(separator: " ").first ?? "0") ?? 0
            let r = Int(rhs.sublabel?.split(separator: " ").first ?? "0") ?? 0
            return l > r
        }
    }

    // MARK: Pollinations

    /// `GET https://text.pollinations.ai/models` - a bare array of
    /// objects, each with a `name` and (usually) an `output_modalities`
    /// list. Ported filter, from `avg.providers.text.community.
    /// PollinationsBackend.list_models`: keep an entry if it has no
    /// `output_modalities` field at all, or if that list includes "text" -
    /// the same either-or the PC's own generator expression encodes,
    /// because Pollinations' text-only legacy models predate the field
    /// existing and still have to be offered.
    ///
    /// Key OPTIONAL - matches every call site here matching the PC's own
    /// `if key: headers["Authorization"] = ...` (never required, only
    /// raises the anonymous rate ceiling).
    static func pollinationsText(key: String?) async throws -> [CatalogEntry] {
        let url = URL(string: "https://text.pollinations.ai/models")!
        var headers: [String: String] = [:]
        if let key, !key.isEmpty { headers["Authorization"] = "Bearer \(key)" }
        let data = try await HTTP.retrying(
            { try await HTTP.send(url, method: "GET", headers: headers, timeout: 30, provider: "pollinations") },
            provider: "pollinations"
        )
        guard let raw = try JSONSerialization.jsonObject(with: data) as? [[String: Any]] else { return [] }
        let entries = raw.compactMap { item -> CatalogEntry? in
            guard let name = item["name"] as? String, !name.isEmpty else { return nil }
            if let modalities = item["output_modalities"] as? [String], !modalities.contains("text") { return nil }
            let desc = item["description"] as? String
            return CatalogEntry(id: name, label: name, sublabel: desc)
        }
        return entries.sorted { $0.label.localizedStandardCompare($1.label) == .orderedAscending }
    }

    /// `GET https://image.pollinations.ai/models` - a bare array of short
    /// model name STRINGS ("flux", "turbo", "kontext", ...), unlike the
    /// text endpoint's array of objects - matches `avg.providers.
    /// catalogues._IMAGE_CATALOGUE_URLS`'s own comment on this endpoint's
    /// shape. Key optional, same reasoning as `pollinationsText`.
    static func pollinationsImage(key: String?) async throws -> [CatalogEntry] {
        let url = URL(string: "https://image.pollinations.ai/models")!
        var headers: [String: String] = [:]
        if let key, !key.isEmpty { headers["Authorization"] = "Bearer \(key)" }
        let data = try await HTTP.retrying(
            { try await HTTP.send(url, method: "GET", headers: headers, timeout: 30, provider: "pollinations") },
            provider: "pollinations"
        )
        guard let raw = try JSONSerialization.jsonObject(with: data) as? [Any] else { return [] }
        let names: [String] = raw.compactMap { item in
            if let s = item as? String, !s.isEmpty { return s }
            if let d = item as? [String: Any], let n = d["name"] as? String, !n.isEmpty { return n }
            return nil
        }
        return Set(names).sorted().map { CatalogEntry(id: $0, label: $0, sublabel: nil) }
    }
}

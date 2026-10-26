import Foundation

// Which model on a provider's live list is "the good default"?
//
// Model names change every few months, so the app cannot ship the right id.
// It ships a RULE instead and applies it to the list the provider returns
// (ModelCatalogSources) - then a one-word probe (ModelProbe) confirms the
// winner works for THIS account before anything is switched. The rule only
// orders candidates; it never decides alone.
//
// Why the rule looks the way it does:
// - Gemini "flash" family only: it is the one that is cheap, fast and has a
//   free tier. "pro" costs several times more and is not the right silent
//   default; "lite" is the fallback preset, not the first choice.
// - A stable id beats a preview even when the preview is newer: previews are
//   retired or changed under an app that hard-wired them, which is exactly the
//   failure this ranking exists to avoid. Among stable ids (and among previews)
//   the newer version wins.
// - Groq has no tidy family names, so size in the id ("70b", "120b") is the
//   only signal; it is used solely when the current model has stopped working.

enum ModelRanking {
    // MARK: Gemini

    /// Text models, best first: `gemini-<version>-flash[...]` without lite,
    /// image, speech, live-audio or similar variants.
    static func geminiText(_ ids: [String]) -> [String] {
        rankGemini(ids, mustContain: [],
                   exclude: ["lite", "image", "tts", "live", "audio", "native", "dialog",
                             "robotics", "computer", "embedding", "thinking"])
    }

    /// Image-output models (Nano Banana family), best first. Flash only: the
    /// "pro" image models cost several times more.
    static func geminiImage(_ ids: [String]) -> [String] {
        rankGemini(ids, mustContain: ["image"], exclude: ["lite", "tts", "live", "audio"])
    }

    /// Speech models, best first (flash, not flash-lite).
    static func geminiVoice(_ ids: [String]) -> [String] {
        rankGemini(ids, mustContain: ["tts"], exclude: ["lite", "image", "live"])
    }

    private struct Candidate {
        let id: String
        let version: [Int]
        let preview: Bool
    }

    /// "gemini-3.8-flash-lite" -> ([3, 8], "-lite"). `nil` for anything that is
    /// not `gemini-<number>-flash...` ("gemini-flash-latest", "gemini-2.5-pro").
    private static func parseFlash(_ id: String) -> (version: [Int], rest: String)? {
        guard id.hasPrefix("gemini-") else { return nil }
        let body = id.dropFirst("gemini-".count)
        guard let dash = body.firstIndex(of: "-") else { return nil }
        let parts = body[body.startIndex..<dash].split(separator: ".")
        let numbers = parts.compactMap { Int($0) }
        guard !numbers.isEmpty, numbers.count == parts.count else { return nil }
        let tail = String(body[dash...])
        guard tail.hasPrefix("-flash") else { return nil }
        return (numbers, String(tail.dropFirst("-flash".count)))
    }

    private static func rankGemini(_ ids: [String], mustContain: [String], exclude: [String]) -> [String] {
        var found: [Candidate] = []
        for id in ids {
            guard let parsed = parseFlash(id) else { continue }
            let rest = parsed.rest
            if mustContain.contains(where: { !rest.contains($0) }) { continue }
            if exclude.contains(where: { rest.contains($0) }) { continue }
            let preview = rest.contains("preview") || rest.contains("exp")
            found.append(Candidate(id: id, version: parsed.version, preview: preview))
        }
        found.sort { a, b in
            if a.preview != b.preview { return !a.preview }
            let aOlder = versionLess(a.version, b.version)
            let bOlder = versionLess(b.version, a.version)
            if aOlder != bOlder { return bOlder }
            if a.id.count != b.id.count { return a.id.count < b.id.count }
            return a.id < b.id
        }
        return found.map { $0.id }
    }

    /// [3, 8] < [3, 10]; a missing component counts as 0.
    private static func versionLess(_ a: [Int], _ b: [Int]) -> Bool {
        for i in 0..<max(a.count, b.count) {
            let x = i < a.count ? a[i] : 0
            let y = i < b.count ? b[i] : 0
            if x != y { return x < y }
        }
        return false
    }

    // MARK: Groq

    /// Chat models, biggest first by the parameter count in the id; speech,
    /// guard and embedding models are never candidates.
    static func groqText(_ ids: [String]) -> [String] {
        let skip = ["whisper", "guard", "tts", "playai", "orpheus", "embed", "distil"]
        return ids
            .filter { id in !skip.contains(where: { id.lowercased().contains($0) }) }
            .sorted { a, b in
                let x = billions(a), y = billions(b)
                return x != y ? x > y : a < b
            }
    }

    /// "llama-3.3-70b-versatile" -> 70, "openai/gpt-oss-120b" -> 120, no size -> 0.
    static func billions(_ id: String) -> Int {
        var best = 0
        for part in id.lowercased().split(whereSeparator: { "-/_:".contains($0) }) {
            guard part.hasSuffix("b"), let n = Int(part.dropLast()) else { continue }
            best = max(best, n)
        }
        return best
    }
}

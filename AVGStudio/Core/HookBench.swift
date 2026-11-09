import Foundation

/// Hook Bench: before a video is written, ten opening lines for its topic from
/// ONE short writer call; the person picks one and the script then opens with
/// it word for word. A few hundred tokens, so it costs next to nothing, and it
/// is optional: a video without a chosen hook is written exactly as before.
enum HookBench {
    static let count = 10
    static let system = "You write the opening lines of short videos. Reply with JSON only."

    static func prompt(topic: String) -> String {
        """
        Topic: "\(topic)"

        Write \(count) DIFFERENT opening lines for a short spoken video about this topic. Each is \
        the very first sentence the narrator says, 6 to 18 words, written for the ear.
        Vary the approach: a question, a bold claim, a surprising number, a short story opening, \
        a contradiction, a "what if", a direct challenge. No warm-up, no greeting, no hashtags, \
        no emojis, and never repeat the same opening words.

        Reply with ONLY this JSON, nothing else: {"hooks": ["...", "..."]}
        """
    }

    /// What the script writer is told when the person chose a line ("" when none).
    static func directionBlock(hook: String?) -> String {
        guard let hook = hook?.trimmed, !hook.isEmpty else { return "" }
        return """
        OPENING LINE (chosen by the person): the narration MUST begin with exactly this line, \
        word for word, then continue naturally from it without repeating it:
        "\(hook)"
        """
    }

    private struct Reply: Decodable { var hooks: [String] }

    /// The hooks in a reply, tolerant of code fences, a bare JSON array, or a
    /// plain numbered or bulleted list; cleaned, de-duplicated, at most `count`.
    static func parse(_ reply: String) -> [String] {
        var text = reply.trimmed
        if text.hasPrefix("```") {
            text = text.replacingOccurrences(of: "```json", with: "").replacingOccurrences(of: "```", with: "").trimmed
        }
        var found: [String] = []
        if let open = text.firstIndex(of: "{"), let close = text.lastIndex(of: "}"), open < close,
           let data = String(text[open...close]).data(using: .utf8),
           let decoded = try? JSONDecoder().decode(Reply.self, from: data) {
            found = decoded.hooks
        } else if let open = text.firstIndex(of: "["), let close = text.lastIndex(of: "]"), open < close,
                  let data = String(text[open...close]).data(using: .utf8),
                  let decoded = try? JSONDecoder().decode([String].self, from: data) {
            found = decoded
        } else {
            found = text.split(whereSeparator: \.isNewline).map { line in
                var s = String(line).trimmed
                while let first = s.first, first.isNumber || ".)-*•".contains(first) || first == " " { s.removeFirst() }
                return s
            }
        }
        var seen = Set<String>()
        var out: [String] = []
        for raw in found {
            let line = raw.trimmingCharacters(in: CharacterSet(charactersIn: "\"“”'‘’ \n"))
            guard !line.isEmpty, line.count <= 200, seen.insert(line.lowercased()).inserted else { continue }
            out.append(line)
            if out.count == count { break }
        }
        return out
    }

    static func generate(topic: String, provider: TextProvider) async throws -> [String] {
        let reply = try await provider.complete(prompt(topic: topic), system: system)
        let hooks = parse(reply)
        guard !hooks.isEmpty else { throw AVGError.message("The writer did not return any opening lines. Try again.") }
        return hooks
    }
}

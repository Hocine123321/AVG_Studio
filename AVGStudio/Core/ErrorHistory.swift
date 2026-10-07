import Foundation

// A failure log that survives the app closing, ported from the PC's
// avg/core/error_store.py.
//
// What was here: `Store.log`, 300 lines in memory that vanish on relaunch. A
// failure noticed at 2am was gone by morning, and "what went wrong?" had to be
// answered from memory or a screenshot.
//
// What this adds: errors (not every activity line) are also written to
// `errors.json`, newest first and capped, and the Activity screen can hand the
// whole history over as one paste.
//
// The rule that outranks usefulness (the PC's own): a stored error must never
// contain a key. HTTP error bodies and URLs routinely carry credentials, so
// every message passes through `Redactor` before it is shown, logged or
// written, and the tests assert against the raw file bytes - the point is that
// the secret never lands, not that a reader hides it.

enum Redactor {
    private static let rules: [(regex: NSRegularExpression, template: String)] = {
        let specs: [(String, String)] = [
            (#"(Authorization\s*:\s*Bearer\s+)\S+"#, "$1REDACTED"),
            (#"((?:x-)?(?:goog-)?api[-_]?key\s*[:=]\s*)[^\s,;&"']+"#, "$1REDACTED"),
            (#"([?&]key=)[^&\s"']+"#, "$1REDACTED"),
            (#"("(?:api[-_]?key|apikey|token|authorization)"\s*:\s*")[^"]+(")"#, "$1REDACTED$2"),
            (#"\b(?:sk-|gsk_|hf_|AIza|xi-)[A-Za-z0-9_\-]{8,}"#, "REDACTED"),
        ]
        return specs.compactMap { pattern, template in
            (try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive])).map { ($0, template) }
        }
    }()

    /// Strips anything credential-shaped. Deliberately broad: a false positive
    /// costs one redacted word in a log, a false negative leaks a key.
    /// `secrets` are the exact stored values, caught even when they have no
    /// recognisable prefix (an AssemblyAI key is bare hex).
    static func scrub(_ text: String, secrets: [String] = []) -> String {
        var out = text
        for secret in secrets where secret.count >= 8 {
            out = out.replacingOccurrences(of: secret, with: "REDACTED")
        }
        for (regex, template) in rules {
            out = regex.stringByReplacingMatches(in: out, range: NSRange(out.startIndex..., in: out), withTemplate: template)
        }
        return out
    }
}

struct ErrorRecord: Codable, Identifiable, Hashable {
    var id = UUID()
    var date: Date
    var message: String
    /// The video it happened in, by title, when there was one.
    var project: String?
}

enum ErrorHistory {
    /// Newest-first cap: the oldest fall off, never the newest.
    static let limit = 200

    /// Never throws: a missing or unreadable file is an empty history.
    static func load(_ url: URL) -> [ErrorRecord] {
        guard let data = try? Data(contentsOf: url) else { return [] }
        return (try? JSONDecoder().decode([ErrorRecord].self, from: data)) ?? []
    }

    /// Prepends `record` (already scrubbed by the caller), trims to `limit`,
    /// writes atomically, and returns the new list. A failed write costs the
    /// on-disk copy only - the returned list is still right.
    @discardableResult
    static func append(_ record: ErrorRecord, to url: URL, limit: Int = ErrorHistory.limit) -> [ErrorRecord] {
        var all = load(url)
        all.insert(record, at: 0)
        if all.count > limit { all.removeLast(all.count - limit) }
        if let data = try? JSONEncoder().encode(all) { try? data.write(to: url, options: .atomic) }
        return all
    }

    static func clear(_ url: URL) {
        try? FileManager.default.removeItem(at: url)
    }

    /// Everything worth pasting into a bug report, scrubbed once more on the
    /// way out in case an older entry predates a redaction rule.
    static func report(_ records: [ErrorRecord], appVersion: String, device: String, secrets: [String] = []) -> String {
        var lines = ["AVG Studio error report", "App \(appVersion) · \(device)", "\(records.count) error(s), newest first", ""]
        let stamp = ISO8601DateFormatter()
        for r in records {
            let where_ = r.project.map { " [\($0)]" } ?? ""
            lines.append("\(stamp.string(from: r.date))\(where_)")
            lines.append(Redactor.scrub(r.message, secrets: secrets))
            lines.append("")
        }
        return lines.joined(separator: "\n")
    }
}

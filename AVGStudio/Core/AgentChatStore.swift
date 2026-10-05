import Foundation

// The optional saved conversation ("Keep chat" in Settings > Assistant).
//
// What is written: the person's messages and the assistant's answers, as
// plain text, newest 40 lines - nothing else. Never tool results, activity
// chips, errors, announcements or approvals, and never a provider payload,
// so nothing stale or sensitive is replayed to a provider later. The file is
// its own hidden file beside `.avg-meta.json` (settings, channels and the
// image pool live there; a chat does not belong in it).

enum AgentChatStore {
    static let maxLines = 40
    static let fileName = ".avg-agent-chat.json"

    @MainActor static func url() -> URL {
        Store.root.appendingPathComponent(fileName)
    }

    private struct Saved: Codable {
        var role: String   // "user" | "assistant"
        var text: String
    }

    static func save(_ lines: [Agent.Line], to url: URL) throws {
        var kept = lines.compactMap { l -> Saved? in
            switch l.kind {
            case .user: return Saved(role: "user", text: redact(l.text))
            case .assistant: return Saved(role: "assistant", text: redact(l.text))
            case .activity, .error, .announcement: return nil
            }
        }
        if kept.count > maxLines { kept = Array(kept.suffix(maxLines)) }
        // A chat must open on something the person said.
        if let firstUser = kept.firstIndex(where: { $0.role == "user" }) {
            kept = Array(kept[firstUser...])
        } else {
            kept = []
        }
        guard !kept.isEmpty else { delete(at: url); return }
        let data = try JSONEncoder().encode(kept)
        try data.write(to: url, options: .atomic)
    }

    /// Empty for a missing, unreadable or corrupt file - never a crash.
    static func load(from url: URL) -> [Agent.Line] {
        guard let data = try? Data(contentsOf: url),
              let saved = try? JSONDecoder().decode([Saved].self, from: data) else { return [] }
        return saved.compactMap { s in
            switch s.role {
            case "user": return Agent.Line(kind: .user, text: s.text)
            case "assistant": return Agent.Line(kind: .assistant, text: s.text)
            default: return nil
            }
        }
    }

    static func delete(at url: URL) {
        try? FileManager.default.removeItem(at: url)
    }

    /// Masks anything shaped like an API key (a long unbroken run of
    /// letters, digits, `_` and `-`) before it is written to disk. People do
    /// paste keys into chats; the saved file must not keep them. Ordinary
    /// words and URLs are far shorter or contain other characters.
    static func redact(_ text: String) -> String {
        let pattern = "[A-Za-z0-9_\\-]{32,}"
        return text.replacingOccurrences(of: pattern, with: "[key removed]", options: .regularExpression)
    }
}

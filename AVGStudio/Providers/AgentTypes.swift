import Foundation

// The agent's provider-neutral vocabulary. Groq speaks the OpenAI chat
// format and Gemini speaks `functionDeclarations`; neither shape leaks past
// the two codecs (`AgentGroq.swift`, `AgentGemini.swift`). Everything the
// loop, the tools and the tests handle is one of the types below, so the
// loop can be unit-tested with a scripted provider and no network.

// MARK: - Tool description

/// One argument of a tool, as the model sees it.
struct AgentToolParam: Sendable {
    enum Kind: Sendable { case string, integer, boolean }

    let name: String
    let kind: Kind
    let summary: String
    var required = true
    /// A closed set of values; rendered as a JSON-schema `enum`.
    var allowed: [String]? = nil
}

extension AgentToolParam.Kind {
    fileprivate var jsonType: String {
        switch self {
        case .string: return "string"
        case .integer: return "integer"
        case .boolean: return "boolean"
        }
    }
}

/// One tool the model may call. The `summary` is sent on every request, so
/// each one is a single short sentence - Groq's free plan allows about 8K
/// tokens per minute and the tool list is part of every request.
struct AgentToolSpec: Sendable {
    let name: String
    let summary: String
    let params: [AgentToolParam]

    /// The JSON-schema `parameters` object. Lower-case type names, accepted
    /// by both Groq and Gemini's REST API. `required` is left out when empty
    /// because some validators reject an empty list.
    func jsonSchema() -> [String: Any] {
        var properties: [String: Any] = [:]
        for p in params {
            var d: [String: Any] = ["type": p.kind.jsonType, "description": p.summary]
            if let allowed = p.allowed { d["enum"] = allowed }
            properties[p.name] = d
        }
        var schema: [String: Any] = ["type": "object", "properties": properties]
        let required = params.filter { $0.required }.map { $0.name }
        if !required.isEmpty { schema["required"] = required }
        return schema
    }
}

// MARK: - Messages

/// A call the model made. `argumentsJSON` stays a string: Groq sends it as
/// one, and it is decoded (tolerantly) by `AgentArgs` only when the tool runs.
struct AgentToolCall: Sendable, Equatable {
    let id: String
    let name: String
    let argumentsJSON: String
}

struct AgentMessage: Sendable, Equatable, Identifiable {
    enum Role: Sendable { case user, assistant, tool }

    let id: UUID
    var role: Role
    var text: String
    var toolCalls: [AgentToolCall]
    /// Set on `.tool` messages: the call this result answers.
    var toolCallID: String?
    var toolName: String?
    /// The provider's own wire form of a model turn, replayed verbatim on
    /// the next request. Gemini 3 answers 400 unless a `functionCall` part
    /// comes back exactly as returned (thought signatures); no other code
    /// reads this. Dropped by compaction so a stale one never travels.
    var providerPayload: Data?
    /// A `.user` message the loop wrote itself (the "your tool call was
    /// malformed" nudge). It is not something the person said, so it never
    /// starts a turn: trimming never cuts at one and compaction drops it.
    var synthetic: Bool

    init(id: UUID = UUID(), role: Role, text: String = "", toolCalls: [AgentToolCall] = [],
         toolCallID: String? = nil, toolName: String? = nil, providerPayload: Data? = nil,
         synthetic: Bool = false) {
        self.id = id
        self.role = role
        self.text = text
        self.toolCalls = toolCalls
        self.toolCallID = toolCallID
        self.toolName = toolName
        self.providerPayload = providerPayload
        self.synthetic = synthetic
    }

    static func user(_ text: String) -> AgentMessage {
        AgentMessage(role: .user, text: text)
    }

    static func nudge(_ text: String) -> AgentMessage {
        AgentMessage(role: .user, text: text, synthetic: true)
    }

    static func assistant(text: String, calls: [AgentToolCall] = [], payload: Data? = nil) -> AgentMessage {
        AgentMessage(role: .assistant, text: text, toolCalls: calls, providerPayload: payload)
    }

    static func tool(callID: String, name: String, text: String) -> AgentMessage {
        AgentMessage(role: .tool, text: text, toolCallID: callID, toolName: name)
    }
}

/// One chat-completion round trip. Throws `AVGError.provider(kind, …)` so
/// the loop reacts to the app's own `ErrorKind`, never to a status code.
protocol AgentProvider: Sendable {
    func respond(system: String, messages: [AgentMessage], tools: [AgentToolSpec]) async throws -> AgentMessage
}

// MARK: - Tolerant arguments

/// A model's tool arguments, read forgivingly. Small models send
/// `"limit":"3"` for an integer, `"true"` for a boolean, or something that
/// is not JSON at all; none of that may crash a tool. Anything unreadable
/// is simply absent, and the tool answers with a clear error instead.
struct AgentArgs {
    private let dict: [String: Any]

    init(json: String) {
        if let data = json.data(using: .utf8),
           let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            dict = object
        } else {
            dict = [:]
        }
    }

    /// A trimmed, non-empty string, or nil.
    func string(_ key: String) -> String? {
        guard let s = dict[key] as? String else { return nil }
        let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? nil : t
    }

    /// A number, or a string holding one. A fractional number is not an Int.
    func int(_ key: String) -> Int? {
        guard let v = dict[key] else { return nil }
        if let s = v as? String { return Int(s.trimmingCharacters(in: .whitespacesAndNewlines)) }
        if let i = v as? Int { return i }
        if let d = v as? Double, d == d.rounded(), abs(d) < 1e15 { return Int(d) }
        return nil
    }

    /// `true`/`false`, or the strings "true"/"yes"/"1" and "false"/"no"/"0".
    func bool(_ key: String) -> Bool? {
        guard let v = dict[key] else { return nil }
        if let s = v as? String {
            switch s.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
            case "true", "yes", "1": return true
            case "false", "no", "0": return false
            default: return nil
            }
        }
        return v as? Bool
    }
}

// MARK: - History

/// Keeps what is sent to a provider small and well-formed.
///
/// A turn is one real user message and everything the agent did in answer.
/// Groq answers HTTP 400 to a `tool` message whose assistant `tool_calls`
/// message is missing, so no operation here may ever leave one orphaned.
enum AgentHistory {
    private static func startsTurn(_ m: AgentMessage) -> Bool {
        m.role == .user && !m.synthetic
    }

    /// The last `keepLast` messages, moved forward to the next real user
    /// message so the window never starts mid tool exchange. If no user
    /// message follows the cut, it moves back to the latest one instead; a
    /// window slightly larger than asked is harmless, an orphan is a 400.
    static func trimmed(_ messages: [AgentMessage], keepLast: Int) -> [AgentMessage] {
        guard keepLast > 0 else { return [] }
        guard messages.count > keepLast else { return messages }
        let cut = messages.count - keepLast
        if let i = messages[cut...].firstIndex(where: { startsTurn($0) }) {
            return Array(messages[i...])
        }
        if let j = messages.lastIndex(where: { startsTurn($0) }) {
            return Array(messages[j...])
        }
        return []
    }

    /// Drops each finished turn's tool exchange, keeping the user's message
    /// and the final answer (text only: the provider payload is removed).
    /// The answer repeats the refs it needs, and the model can call
    /// `list_projects` again, so nothing is lost that matters - and it cuts
    /// the tokens sent every request. With `keepingLastTurn` the newest turn
    /// is left whole, for use while it is still running.
    static func compacted(_ messages: [AgentMessage], keepingLastTurn: Bool) -> [AgentMessage] {
        var turns: [[AgentMessage]] = []
        for m in messages {
            if startsTurn(m) {
                turns.append([m])
            } else if !turns.isEmpty {
                turns[turns.count - 1].append(m)
            }
            // Anything before the first real user message is dropped.
        }
        var out: [AgentMessage] = []
        for (i, turn) in turns.enumerated() {
            if keepingLastTurn && i == turns.count - 1 {
                out += turn
                continue
            }
            out.append(turn[0])
            if var answer = turn.last(where: { $0.role == .assistant && $0.toolCalls.isEmpty && !$0.text.isEmpty }) {
                answer.providerPayload = nil
                out.append(answer)
            }
        }
        return out
    }
}

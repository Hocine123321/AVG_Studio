import Foundation

// Groq speaks the OpenAI chat-completions format. Tool definitions go in
// `tools`; the model answers with `tool_calls` whose `function.arguments` is
// a JSON *string*; a result goes back as a `role: "tool"` message carrying
// the matching `tool_call_id`.
//
// Request knobs, from Groq's API reference:
//  - `max_completion_tokens` (`max_tokens` is deprecated),
//  - `parallel_tool_calls: false` (gpt-oss models cannot do parallel calls,
//    and one call at a time keeps approvals simple),
//  - `tool_choice: "auto"`, `temperature: 0.2` (low, as Groq's own tool
//    examples use, so the model fills arguments instead of improvising).

struct GroqAgent: AgentProvider {
    let key: String
    let model: String
    /// See `HTTP.retrying(attemptCap:)`. nil = the policy's own budget.
    var attemptCap: Int? = nil

    static let url = URL(string: "https://api.groq.com/openai/v1/chat/completions")!

    func respond(system: String, messages: [AgentMessage], tools: [AgentToolSpec]) async throws -> AgentMessage {
        let body = Self.encode(system: system, messages: messages, tools: tools, model: model)
        // Key in a header only - never the URL, a log line, or an error.
        let headers = ["Authorization": "Bearer \(key)"]
        let data = try await HTTP.retrying({
            try await HTTP.send(Self.url, headers: headers, json: body, timeout: 90, provider: "groq")
        }, provider: "groq", attemptCap: attemptCap)
        return try Self.decode(data)
    }

    // MARK: Wire format

    static func encode(system: String, messages: [AgentMessage], tools: [AgentToolSpec], model: String) -> [String: Any] {
        var wire: [[String: Any]] = [["role": "system", "content": system]]
        for m in messages {
            switch m.role {
            case .user:
                wire.append(["role": "user", "content": m.text])
            case .assistant:
                var d: [String: Any] = ["role": "assistant"]
                // A tool-calling turn with no words is `content: null`; a
                // plain turn must carry a string.
                d["content"] = (m.text.isEmpty && !m.toolCalls.isEmpty) ? NSNull() : m.text
                if !m.toolCalls.isEmpty {
                    d["tool_calls"] = m.toolCalls.map { callJSON($0) }
                }
                wire.append(d)
            case .tool:
                wire.append(["role": "tool", "tool_call_id": m.toolCallID ?? "", "content": m.text])
            }
        }
        var body: [String: Any] = [
            "model": model,
            "messages": wire,
            "temperature": 0.2,
            "max_completion_tokens": 4096,
        ]
        if !tools.isEmpty {
            body["tools"] = tools.map { toolJSON($0) }
            body["tool_choice"] = "auto"
            body["parallel_tool_calls"] = false
        }
        return body
    }

    private static func callJSON(_ c: AgentToolCall) -> [String: Any] {
        let function: [String: Any] = ["name": c.name, "arguments": c.argumentsJSON]
        return ["id": c.id, "type": "function", "function": function]
    }

    private static func toolJSON(_ t: AgentToolSpec) -> [String: Any] {
        let function: [String: Any] = ["name": t.name, "description": t.summary, "parameters": t.jsonSchema()]
        return ["type": "function", "function": function]
    }

    static func decode(_ data: Data) throws -> AgentMessage {
        let o = try HTTP.object(data)
        guard let choice = (o["choices"] as? [[String: Any]])?.first,
              let message = choice["message"] as? [String: Any] else {
            throw AVGError.message("The assistant returned no reply.")
        }
        let text = (message["content"] as? String) ?? ""
        var calls: [AgentToolCall] = []
        for (i, c) in ((message["tool_calls"] as? [[String: Any]]) ?? []).enumerated() {
            guard let function = c["function"] as? [String: Any], let name = function["name"] as? String else { continue }
            calls.append(AgentToolCall(id: (c["id"] as? String) ?? "call_\(i)", name: name,
                                       argumentsJSON: argumentsString(function["arguments"])))
        }
        if calls.isEmpty && text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            if (choice["finish_reason"] as? String) == "length" {
                throw AVGError.message("The assistant's reply was cut off before it finished. Try again, or pick a different model.")
            }
            throw AVGError.message("The assistant returned an empty reply.")
        }
        return .assistant(text: text, calls: calls)
    }

    /// `arguments` is a JSON string per the format, but some models send an
    /// object; accept both so the tool still runs.
    private static func argumentsString(_ raw: Any?) -> String {
        if let s = raw as? String { return s.isEmpty ? "{}" : s }
        if let obj = raw as? [String: Any], JSONSerialization.isValidJSONObject(obj),
           let data = try? JSONSerialization.data(withJSONObject: obj),
           let s = String(data: data, encoding: .utf8) { return s }
        return "{}"
    }
}

// MARK: - Choosing a provider

enum AgentProviderFactory {
    /// The provider for `kind`, or the one in settings. Throws the usual
    /// "add your key" error when the key is missing, so the chat can say so.
    static func make(settings: AppSettings, kind: AgentProviderKind? = nil, attemptCap: Int? = nil) throws -> any AgentProvider {
        switch kind ?? settings.agentProvider {
        case .groq:
            return GroqAgent(key: try Keychain.require(.groq), model: settings.agentGroqModel, attemptCap: attemptCap)
        case .gemini:
            return GeminiAgent(key: try Keychain.require(.gemini), model: settings.agentGeminiModel, attemptCap: attemptCap)
        }
    }
}

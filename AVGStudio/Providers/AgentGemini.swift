import Foundation

// Gemini's native function-calling format (not OpenAI-compatible):
//  - tools: `[{"functionDeclarations":[{name, description, parameters}]}]`
//  - a call is a `functionCall{name,args,id?}` PART inside a `model` content;
//    `args` is an object, not a string. Gemini 3 always sends `id`; 2.5 may not.
//  - a result is a `functionResponse{name,response,id?}` part in a `user` content.
//  - Gemini 3 REQUIRES the model's `thoughtSignature` (a sibling of the
//    `functionCall` part) to come back unchanged, or the next request answers
//    HTTP 400. So the whole model turn is kept as `providerPayload` and replayed
//    verbatim; it is dropped by compaction and never saved to disk.
//  - `generationConfig.maxOutputTokens` includes thinking tokens.
//  - A tool with no parameters must OMIT `parameters`: Gemini rejects an
//    OBJECT schema with empty `properties`.

struct GeminiAgent: AgentProvider {
    let key: String
    let model: String
    /// See `HTTP.retrying(attemptCap:)`. nil = the policy's own budget.
    var attemptCap: Int? = nil

    /// Ids the model did not supply are made up locally with this prefix, so
    /// the loop can match a result to its call. They are never sent back to
    /// Gemini (a 2.5 model has no `id` field to match them against).
    static let syntheticIDPrefix = "gemini_call_"

    func respond(system: String, messages: [AgentMessage], tools: [AgentToolSpec]) async throws -> AgentMessage {
        let encodedModel = model.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? model
        guard !model.isEmpty,
              let url = URL(string: "https://generativelanguage.googleapis.com/v1beta/models/\(encodedModel):generateContent") else {
            throw AVGError.message("The Gemini model name looks invalid. Pick one in Settings > Assistant.")
        }
        let body = Self.encode(system: system, messages: messages, tools: tools)
        // Key in a header only - never the URL, a log line, or an error.
        let headers = ["x-goog-api-key": key]
        let data = try await HTTP.retrying({
            try await HTTP.send(url, headers: headers, json: body, timeout: 90, provider: "gemini")
        }, provider: "gemini", attemptCap: attemptCap)
        return try Self.decode(data)
    }

    // MARK: Encode

    static func encode(system: String, messages: [AgentMessage], tools: [AgentToolSpec]) -> [String: Any] {
        var contents: [[String: Any]] = []

        /// Gemini wants roles to alternate, so adjacent user parts (a nudge
        /// after a failed call, tool results) share one content.
        func appendUser(_ parts: [[String: Any]]) {
            if let last = contents.last, last["role"] as? String == "user", var existing = last["parts"] as? [[String: Any]] {
                existing.append(contentsOf: parts)
                contents[contents.count - 1] = ["role": "user", "parts": existing]
            } else {
                contents.append(["role": "user", "parts": parts])
            }
        }

        for m in messages {
            switch m.role {
            case .user:
                appendUser([["text": m.text]])
            case .assistant:
                if let payload = m.providerPayload,
                   let raw = try? JSONSerialization.jsonObject(with: payload) as? [String: Any] {
                    var replay = raw
                    if replay["role"] == nil { replay["role"] = "model" }
                    contents.append(replay)
                } else {
                    var parts: [[String: Any]] = []
                    if !m.text.isEmpty { parts.append(["text": m.text]) }
                    for c in m.toolCalls {
                        var call: [String: Any] = ["name": c.name, "args": argsObject(c.argumentsJSON)]
                        if !c.id.hasPrefix(syntheticIDPrefix) { call["id"] = c.id }
                        parts.append(["functionCall": call])
                    }
                    if parts.isEmpty { parts.append(["text": ""]) }
                    contents.append(["role": "model", "parts": parts])
                }
            case .tool:
                var response: [String: Any] = ["name": m.toolName ?? "tool", "response": ["result": m.text]]
                if let id = m.toolCallID, !id.hasPrefix(syntheticIDPrefix) { response["id"] = id }
                appendUser([["functionResponse": response]])
            }
        }

        // Typed explicitly: an untyped `["temperature": 0.2, "maxOutputTokens": 4096]`
        // literal infers [String: Double], which would send the token limit as 4096.0.
        let generation: [String: Any] = ["temperature": 0.2, "maxOutputTokens": 4096]
        var body: [String: Any] = [
            "systemInstruction": ["parts": [["text": system]]],
            "contents": contents,
            "generationConfig": generation,
        ]
        if !tools.isEmpty {
            body["tools"] = [["functionDeclarations": tools.map { declaration($0) }]]
            body["toolConfig"] = ["functionCallingConfig": ["mode": "AUTO"]]
        }
        return body
    }

    private static func declaration(_ t: AgentToolSpec) -> [String: Any] {
        var d: [String: Any] = ["name": t.name, "description": t.summary]
        if !t.params.isEmpty { d["parameters"] = t.jsonSchema() }
        return d
    }

    private static func argsObject(_ json: String) -> [String: Any] {
        guard let data = json.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return obj
    }

    // MARK: Decode

    static func decode(_ data: Data) throws -> AgentMessage {
        let o = try HTTP.object(data)
        guard let candidate = (o["candidates"] as? [[String: Any]])?.first else {
            if let feedback = o["promptFeedback"] as? [String: Any], let reason = feedback["blockReason"] as? String {
                throw AVGError.message("Gemini blocked this message (\(reason)). Try rephrasing.")
            }
            throw AVGError.message("The assistant returned no reply.")
        }
        let finish = (candidate["finishReason"] as? String) ?? ""
        if finish.lowercased().contains("malformed") {
            throw AVGError.provider(.malformedOutput, provider: "gemini",
                                    message: "The model made a malformed tool call (\(finish)).")
        }

        let content = candidate["content"] as? [String: Any]
        // Iterate every part by type - a text part may come before, after or
        // between function calls; never assume a position.
        let parts = (content?["parts"] as? [[String: Any]]) ?? []
        var text = ""
        var calls: [AgentToolCall] = []
        for part in parts {
            if let call = part["functionCall"] as? [String: Any], let name = call["name"] as? String {
                let id = (call["id"] as? String).flatMap { $0.isEmpty ? nil : $0 }
                    ?? syntheticIDPrefix + UUID().uuidString.prefix(8)
                calls.append(AgentToolCall(id: id, name: name, argumentsJSON: argsString(call["args"])))
            } else if let t = part["text"] as? String, (part["thought"] as? Bool) != true {
                text += t
            }
        }

        if calls.isEmpty && text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            switch finish.uppercased() {
            case "MAX_TOKENS":
                throw AVGError.message("The assistant's reply was cut off before it finished. Try again, or pick a different model.")
            case "SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII":
                throw AVGError.message("Gemini blocked its own reply (\(finish)). Try rephrasing.")
            default:
                throw AVGError.message("The assistant returned an empty reply.")
            }
        }

        var payload: Data?
        if let content, !parts.isEmpty, JSONSerialization.isValidJSONObject(content) {
            payload = try? JSONSerialization.data(withJSONObject: content)
        }
        return .assistant(text: text, calls: calls, payload: payload)
    }

    private static func argsString(_ raw: Any?) -> String {
        if let obj = raw as? [String: Any], JSONSerialization.isValidJSONObject(obj),
           let data = try? JSONSerialization.data(withJSONObject: obj),
           let s = String(data: data, encoding: .utf8) { return s }
        if let s = raw as? String, !s.isEmpty { return s }
        return "{}"
    }
}

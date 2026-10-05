import XCTest
@testable import AVGStudio

/// Gemini's native function-calling wire format. Pure encode/decode - no network.
final class AgentGeminiTests: XCTestCase {

    private let tool = AgentToolSpec(name: "list_projects", summary: "List projects", params: [
        AgentToolParam(name: "limit", kind: .integer, summary: "How many", required: false),
    ])
    private let ping = AgentToolSpec(name: "ping", summary: "Ping", params: [])

    private func contents(_ body: [String: Any]) throws -> [[String: Any]] {
        try XCTUnwrap(body["contents"] as? [[String: Any]])
    }

    private func parts(_ content: [String: Any]) throws -> [[String: Any]] {
        try XCTUnwrap(content["parts"] as? [[String: Any]])
    }

    // MARK: Encode

    func testEncodeSendsSystemInstructionAndDeclarations() throws {
        let body = GeminiAgent.encode(system: "SYS", messages: [.user("hi")], tools: [tool, ping])
        XCTAssertTrue(JSONSerialization.isValidJSONObject(body))
        let sys = try XCTUnwrap(body["systemInstruction"] as? [String: Any])
        XCTAssertEqual((sys["parts"] as? [[String: Any]])?.first?["text"] as? String, "SYS")

        let tools = try XCTUnwrap(body["tools"] as? [[String: Any]])
        let decls = try XCTUnwrap(tools[0]["functionDeclarations"] as? [[String: Any]])
        XCTAssertEqual(decls.count, 2)
        XCTAssertEqual(decls[0]["name"] as? String, "list_projects")
        XCTAssertEqual(decls[0]["description"] as? String, "List projects")
        XCTAssertEqual((decls[0]["parameters"] as? [String: Any])?["type"] as? String, "object")
        XCTAssertNil(decls[1]["parameters"], "a tool with no parameters must omit `parameters`; Gemini rejects empty properties")

        let cfg = try XCTUnwrap((body["toolConfig"] as? [String: Any])?["functionCallingConfig"] as? [String: Any])
        XCTAssertEqual(cfg["mode"] as? String, "AUTO")
        let gen = try XCTUnwrap(body["generationConfig"] as? [String: Any])
        XCTAssertEqual(gen["temperature"] as? Double, 0.2)
        XCTAssertEqual(gen["maxOutputTokens"] as? Int, 4096)
    }

    func testEncodeWithoutToolsSendsNoToolFields() {
        let body = GeminiAgent.encode(system: "S", messages: [.user("hi")], tools: [])
        XCTAssertNil(body["tools"])
        XCTAssertNil(body["toolConfig"])
    }

    func testRolesAreUserAndModel() throws {
        let body = GeminiAgent.encode(system: "S", messages: [.user("q"), .assistant(text: "a")], tools: [])
        let c = try contents(body)
        XCTAssertEqual(c.map { $0["role"] as? String }, ["user", "model"])
        XCTAssertEqual(try parts(c[1]).first?["text"] as? String, "a")
    }

    func testModelTurnIsReplayedVerbatim() throws {
        // The model's real turn, as Gemini 3 returned it: the signature rides
        // beside the functionCall part and must come back unchanged.
        let raw = #"{"role":"model","parts":[{"functionCall":{"id":"function-call-1","name":"list_projects","args":{"limit":2}},"thoughtSignature":"SIG-ABC123=="}]}"#
        let call = AgentToolCall(id: "function-call-1", name: "list_projects", argumentsJSON: #"{"limit":2}"#)
        let msgs: [AgentMessage] = [.user("q"), .assistant(text: "", calls: [call], payload: Data(raw.utf8)),
                                    .tool(callID: "function-call-1", name: "list_projects", text: "r")]
        let c = try contents(GeminiAgent.encode(system: "S", messages: msgs, tools: [tool]))
        XCTAssertEqual(c.count, 3)
        let modelParts = try parts(c[1])
        XCTAssertEqual(modelParts.count, 1)
        XCTAssertEqual(modelParts[0]["thoughtSignature"] as? String, "SIG-ABC123==")
        let fc = try XCTUnwrap(modelParts[0]["functionCall"] as? [String: Any])
        XCTAssertEqual(fc["id"] as? String, "function-call-1")
        XCTAssertEqual((fc["args"] as? [String: Any])?["limit"] as? Int, 2)
    }

    func testToolResultsMergeIntoOneUserContentWithIDs() throws {
        let calls = [AgentToolCall(id: "function-call-1", name: "get_status", argumentsJSON: #"{"project":"a"}"#),
                     AgentToolCall(id: "function-call-2", name: "list_channels", argumentsJSON: "{}")]
        let msgs: [AgentMessage] = [.user("q"), .assistant(text: "", calls: calls),
                                    .tool(callID: "function-call-1", name: "get_status", text: "status text"),
                                    .tool(callID: "function-call-2", name: "list_channels", text: "channels text")]
        let c = try contents(GeminiAgent.encode(system: "S", messages: msgs, tools: []))
        XCTAssertEqual(c.map { $0["role"] as? String }, ["user", "model", "user"])
        let results = try parts(c[2])
        XCTAssertEqual(results.count, 2)
        let r0 = try XCTUnwrap(results[0]["functionResponse"] as? [String: Any])
        XCTAssertEqual(r0["id"] as? String, "function-call-1")
        XCTAssertEqual(r0["name"] as? String, "get_status")
        XCTAssertEqual((r0["response"] as? [String: Any])?["result"] as? String, "status text")
        let r1 = try XCTUnwrap(results[1]["functionResponse"] as? [String: Any])
        XCTAssertEqual(r1["id"] as? String, "function-call-2")
    }

    func testRebuiltCallWithoutPayloadCarriesNameArgsAndRealID() throws {
        let call = AgentToolCall(id: "function-call-9", name: "list_projects", argumentsJSON: #"{"limit":3}"#)
        let c = try contents(GeminiAgent.encode(system: "S", messages: [.user("q"), .assistant(text: "Checking…", calls: [call])], tools: []))
        let p = try parts(c[1])
        XCTAssertEqual(p[0]["text"] as? String, "Checking…")
        let fc = try XCTUnwrap(p[1]["functionCall"] as? [String: Any])
        XCTAssertEqual(fc["id"] as? String, "function-call-9")
        XCTAssertEqual((fc["args"] as? [String: Any])?["limit"] as? Int, 3)
    }

    func testAdjacentUserMessagesShareOneContent() throws {
        // A nudge after a failed call follows a user message: roles must alternate.
        let c = try contents(GeminiAgent.encode(system: "S", messages: [.user("q"), .nudge("fix it")], tools: []))
        XCTAssertEqual(c.count, 1)
        XCTAssertEqual(try parts(c[0]).count, 2)
    }

    func testCompactedHistoryEncodesWithoutSignatures() throws {
        let compacted = AgentHistory.compacted([.user("q"), .assistant(text: "answer", payload: Data(#"{"role":"model","parts":[{"text":"answer","thoughtSignature":"SIG"}]}"#.utf8))],
                                               keepingLastTurn: false)
        let body = GeminiAgent.encode(system: "S", messages: compacted, tools: [])
        let data = try JSONSerialization.data(withJSONObject: body)
        XCTAssertFalse(String(decoding: data, as: UTF8.self).contains("thoughtSignature"))
    }

    // MARK: Decode

    func testDecodeToolCall() throws {
        let json = #"""
        {"candidates":[{"finishReason":"STOP","content":{"role":"model","parts":[
        {"functionCall":{"id":"function-call-77","name":"list_projects","args":{"limit":2}},"thoughtSignature":"SIG"}]}}]}
        """#
        let m = try GeminiAgent.decode(Data(json.utf8))
        XCTAssertEqual(m.toolCalls.count, 1)
        XCTAssertEqual(m.toolCalls[0].id, "function-call-77")
        XCTAssertEqual(m.toolCalls[0].name, "list_projects")
        XCTAssertEqual(AgentArgs(json: m.toolCalls[0].argumentsJSON).int("limit"), 2)
        let payload = try XCTUnwrap(m.providerPayload)
        XCTAssertTrue(String(decoding: payload, as: UTF8.self).contains("SIG"), "the whole turn is kept for replay")
    }

    func testDecodeIteratesPartsNotPositions() throws {
        let json = #"""
        {"candidates":[{"content":{"role":"model","parts":[
        {"text":"Let me check. "},{"functionCall":{"name":"get_status","args":{"project":"abc"}}},{"text":"One moment."}]}}]}
        """#
        let m = try GeminiAgent.decode(Data(json.utf8))
        XCTAssertEqual(m.text, "Let me check. One moment.")
        XCTAssertEqual(m.toolCalls.map { $0.name }, ["get_status"])
    }

    func testDecodeSkipsThoughtSummaries() throws {
        let json = #"{"candidates":[{"content":{"role":"model","parts":[{"text":"internal reasoning","thought":true},{"text":"The answer."}]}}]}"#
        XCTAssertEqual(try GeminiAgent.decode(Data(json.utf8)).text, "The answer.")
    }

    func testDecodeSynthesisesIDWhenModelOmitsItAndEncodeNeverSendsIt() throws {
        let json = #"{"candidates":[{"content":{"role":"model","parts":[{"functionCall":{"name":"list_channels","args":{}}}]}}]}"#
        let m = try GeminiAgent.decode(Data(json.utf8))
        let id = m.toolCalls[0].id
        XCTAssertTrue(id.hasPrefix(GeminiAgent.syntheticIDPrefix))
        XCTAssertGreaterThan(id.count, GeminiAgent.syntheticIDPrefix.count)

        // Rebuilt without the payload, the made-up id must not reach Gemini.
        var noPayload = m
        noPayload.providerPayload = nil
        let msgs: [AgentMessage] = [.user("q"), noPayload, .tool(callID: id, name: "list_channels", text: "r")]
        let c = try contents(GeminiAgent.encode(system: "S", messages: msgs, tools: []))
        let fc = try XCTUnwrap(try parts(c[1])[0]["functionCall"] as? [String: Any])
        XCTAssertNil(fc["id"])
        let fr = try XCTUnwrap(try parts(c[2])[0]["functionResponse"] as? [String: Any])
        XCTAssertNil(fr["id"])
        XCTAssertEqual(fr["name"] as? String, "list_channels")
    }

    func testTwoIdlessCallsGetDifferentIDs() throws {
        let json = #"{"candidates":[{"content":{"role":"model","parts":[{"functionCall":{"name":"a","args":{}}},{"functionCall":{"name":"a","args":{}}}]}}]}"#
        let ids = try GeminiAgent.decode(Data(json.utf8)).toolCalls.map { $0.id }
        XCTAssertEqual(Set(ids).count, 2)
    }

    func testMalformedFunctionCallFinishReasonThrowsMalformedOutput() {
        for reason in ["MALFORMED_FUNCTION_CALL", "Malformed_Function_Call", "malformed_function_call"] {
            let json = #"{"candidates":[{"finishReason":"\#(reason)"}]}"#
            XCTAssertThrowsError(try GeminiAgent.decode(Data(json.utf8)), reason) { error in
                XCTAssertEqual((error as? AVGError)?.kind, .malformedOutput)
            }
        }
    }

    func testPromptBlockAndEmptyAndTruncatedRepliesAreReadableErrors() {
        func message(_ json: String) -> String {
            do { _ = try GeminiAgent.decode(Data(json.utf8)); return "" }
            catch { return (error as? AVGError)?.errorDescription ?? "\(error)" }
        }
        XCTAssertTrue(message(#"{"promptFeedback":{"blockReason":"SAFETY"}}"#).contains("SAFETY"))
        XCTAssertTrue(message(#"{"candidates":[{"finishReason":"MAX_TOKENS","content":{"role":"model","parts":[]}}]}"#).contains("cut off"))
        XCTAssertTrue(message(#"{"candidates":[{"finishReason":"STOP","content":{"role":"model","parts":[{"text":"  "}]}}]}"#).contains("empty"))
        XCTAssertFalse(message(#"{}"#).isEmpty)
    }

    func testPlainReplyCarriesTextAndNoCalls() throws {
        let m = try GeminiAgent.decode(Data(#"{"candidates":[{"finishReason":"STOP","content":{"role":"model","parts":[{"text":"Hello"}]}}]}"#.utf8))
        XCTAssertEqual(m.text, "Hello")
        XCTAssertTrue(m.toolCalls.isEmpty)
    }
}

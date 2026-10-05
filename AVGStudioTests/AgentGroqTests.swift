import XCTest
@testable import AVGStudio

/// Groq's OpenAI-style tool-calling wire format. Pure encode/decode - no network.
final class AgentGroqTests: XCTestCase {

    private let tool = AgentToolSpec(name: "list_projects", summary: "List projects", params: [
        AgentToolParam(name: "limit", kind: .integer, summary: "How many", required: false),
    ])

    func testEncodeShape() throws {
        let call = AgentToolCall(id: "call_1", name: "list_projects", argumentsJSON: #"{"limit":3}"#)
        let messages: [AgentMessage] = [
            .user("hi"),
            .assistant(text: "", calls: [call]),
            .tool(callID: "call_1", name: "list_projects", text: "1. Cats"),
            .assistant(text: "done"),
        ]
        let body = GroqAgent.encode(system: "SYS", messages: messages, tools: [tool], model: "m")
        XCTAssertTrue(JSONSerialization.isValidJSONObject(body), "the body must serialise")
        XCTAssertEqual(body["model"] as? String, "m")
        XCTAssertEqual(body["tool_choice"] as? String, "auto")
        XCTAssertEqual(body["parallel_tool_calls"] as? Bool, false)
        XCTAssertEqual(body["temperature"] as? Double, 0.2)
        XCTAssertEqual(body["max_completion_tokens"] as? Int, 4096)
        XCTAssertNil(body["max_tokens"], "max_tokens is deprecated on Groq")

        let wire = try XCTUnwrap(body["messages"] as? [[String: Any]])
        XCTAssertEqual(wire.count, 5)
        XCTAssertEqual(wire[0]["role"] as? String, "system")
        XCTAssertEqual(wire[0]["content"] as? String, "SYS")
        XCTAssertEqual(wire[1]["role"] as? String, "user")

        let assistant = wire[2]
        XCTAssertEqual(assistant["role"] as? String, "assistant")
        XCTAssertTrue(assistant["content"] is NSNull, "a wordless tool-calling turn has null content")
        let calls = try XCTUnwrap(assistant["tool_calls"] as? [[String: Any]])
        XCTAssertEqual(calls.count, 1)
        XCTAssertEqual(calls[0]["id"] as? String, "call_1")
        XCTAssertEqual(calls[0]["type"] as? String, "function")
        let fn = try XCTUnwrap(calls[0]["function"] as? [String: Any])
        XCTAssertEqual(fn["name"] as? String, "list_projects")
        XCTAssertEqual(fn["arguments"] as? String, #"{"limit":3}"#, "arguments travel as a JSON string")

        XCTAssertEqual(wire[3]["role"] as? String, "tool")
        XCTAssertEqual(wire[3]["tool_call_id"] as? String, "call_1")
        XCTAssertEqual(wire[3]["content"] as? String, "1. Cats")

        XCTAssertEqual(wire[4]["content"] as? String, "done")
        XCTAssertNil(wire[4]["tool_calls"])

        let tools = try XCTUnwrap(body["tools"] as? [[String: Any]])
        XCTAssertEqual(tools.count, 1)
        XCTAssertEqual(tools[0]["type"] as? String, "function")
        let spec = try XCTUnwrap(tools[0]["function"] as? [String: Any])
        XCTAssertEqual(spec["name"] as? String, "list_projects")
        XCTAssertEqual(spec["description"] as? String, "List projects")
        let params = try XCTUnwrap(spec["parameters"] as? [String: Any])
        XCTAssertEqual(params["type"] as? String, "object")
    }

    func testEncodeWithoutToolsSendsNoToolFields() {
        let body = GroqAgent.encode(system: "S", messages: [.user("hi")], tools: [], model: "m")
        XCTAssertNil(body["tools"])
        XCTAssertNil(body["tool_choice"], "Groq rejects tool_choice when there are no tools")
        XCTAssertNil(body["parallel_tool_calls"])
    }

    func testAssistantTextWithCallsKeepsItsText() throws {
        let call = AgentToolCall(id: "c", name: "x", argumentsJSON: "{}")
        let body = GroqAgent.encode(system: "S", messages: [.user("q"), .assistant(text: "Checking…", calls: [call])],
                                    tools: [tool], model: "m")
        let wire = try XCTUnwrap(body["messages"] as? [[String: Any]])
        XCTAssertEqual(wire[2]["content"] as? String, "Checking…")
    }

    func testDecodeToolCalls() throws {
        let json = #"""
        {"id":"x","choices":[{"index":0,"finish_reason":"tool_calls","message":{"role":"assistant","content":null,
        "tool_calls":[{"id":"call_abc","type":"function","function":{"name":"list_projects","arguments":"{\"limit\":2}"}}]}}]}
        """#
        let m = try GroqAgent.decode(Data(json.utf8))
        XCTAssertEqual(m.role, .assistant)
        XCTAssertEqual(m.text, "")
        XCTAssertEqual(m.toolCalls.count, 1)
        XCTAssertEqual(m.toolCalls[0].id, "call_abc")
        XCTAssertEqual(m.toolCalls[0].name, "list_projects")
        XCTAssertEqual(AgentArgs(json: m.toolCalls[0].argumentsJSON).int("limit"), 2)
    }

    func testDecodePlainReply() throws {
        let json = #"{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"Hello there"}}]}"#
        let m = try GroqAgent.decode(Data(json.utf8))
        XCTAssertEqual(m.text, "Hello there")
        XCTAssertTrue(m.toolCalls.isEmpty)
    }

    func testDecodeToleratesObjectArgumentsAndMissingID() throws {
        let json = #"""
        {"choices":[{"message":{"role":"assistant","content":"",
        "tool_calls":[{"type":"function","function":{"name":"get_status","arguments":{"project":"abc123"}}}]}}]}
        """#
        let m = try GroqAgent.decode(Data(json.utf8))
        XCTAssertEqual(m.toolCalls.count, 1)
        XCTAssertEqual(m.toolCalls[0].id, "call_0", "a missing id is synthesised, never empty")
        XCTAssertEqual(AgentArgs(json: m.toolCalls[0].argumentsJSON).string("project"), "abc123")
    }

    func testDecodeNullContentWithNoCallsThrows() {
        let json = #"{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":null}}]}"#
        XCTAssertThrowsError(try GroqAgent.decode(Data(json.utf8)))
    }

    func testFinishReasonLengthWithoutCallsThrowsReadableError() {
        let json = #"{"choices":[{"finish_reason":"length","message":{"role":"assistant","content":""}}]}"#
        XCTAssertThrowsError(try GroqAgent.decode(Data(json.utf8))) { error in
            let text = (error as? AVGError)?.errorDescription ?? ""
            XCTAssertTrue(text.contains("cut off"), "got: \(text)")
        }
    }

    func testDecodeWithNoChoicesThrows() {
        XCTAssertThrowsError(try GroqAgent.decode(Data(#"{"choices":[]}"#.utf8)))
        XCTAssertThrowsError(try GroqAgent.decode(Data(#"{}"#.utf8)))
    }

    func testFactoryNeedsTheMatchingKey() throws {
        // The simulator's Keychain is empty; skip rather than fail on a dev machine that has keys.
        try XCTSkipIf(Keychain.has(.groq) || Keychain.has(.gemini), "a real key is present")
        for kind in AgentProviderKind.allCases {
            var s = AppSettings()
            s.agentProvider = kind
            XCTAssertThrowsError(try AgentProviderFactory.make(settings: s)) { error in
                guard case AVGError.missingKey = error else { return XCTFail("expected missingKey, got \(error)") }
            }
            XCTAssertThrowsError(try AgentProviderFactory.make(settings: AppSettings(), kind: kind), "an explicit kind overrides the setting")
        }
    }
}

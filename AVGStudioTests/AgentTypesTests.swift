import XCTest
@testable import AVGStudio

/// The agent's provider-neutral vocabulary. The history helpers matter most:
/// Groq answers HTTP 400 to a `tool` message whose assistant `tool_calls`
/// message is missing, so trimming or compacting through the middle of a
/// tool exchange breaks every later request in the chat.
final class AgentTypesTests: XCTestCase {

    // MARK: Fixtures

    private func call(_ id: String) -> AgentToolCall {
        AgentToolCall(id: id, name: "list_projects", argumentsJSON: "{}")
    }

    /// user, assistant(call), tool result, assistant(final answer)
    private func turn(_ n: Int) -> [AgentMessage] {
        [
            .user("q\(n)"),
            .assistant(text: "", calls: [call("c\(n)")]),
            .tool(callID: "c\(n)", name: "list_projects", text: "r\(n)"),
            .assistant(text: "a\(n)"),
        ]
    }

    private func assertWellFormed(_ m: [AgentMessage], file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(m.first?.role == .user, "window must start on a user message", file: file, line: line)
        XCTAssertFalse(m.first?.synthetic ?? true, "window must start on a real user message", file: file, line: line)
        var issued = Set<String>()
        for x in m {
            if x.role == .assistant { x.toolCalls.forEach { issued.insert($0.id) } }
            if x.role == .tool {
                XCTAssertTrue(issued.contains(x.toolCallID ?? ""), "orphan tool result \(x.toolCallID ?? "?")",
                              file: file, line: line)
            }
        }
    }

    // MARK: Schema

    func testSchemaRendersTypesRequiredAndEnum() throws {
        let spec = AgentToolSpec(name: "create_video", summary: "Make a video", params: [
            AgentToolParam(name: "topic", kind: .string, summary: "What it is about"),
            AgentToolParam(name: "aspect", kind: .string, summary: "Shape", required: false,
                           allowed: ["9:16", "16:9", "1:1"]),
            AgentToolParam(name: "limit", kind: .integer, summary: "How many", required: false),
            AgentToolParam(name: "flag", kind: .boolean, summary: "A switch", required: false),
        ])
        let schema = spec.jsonSchema()
        XCTAssertEqual(schema["type"] as? String, "object")
        let props = try XCTUnwrap(schema["properties"] as? [String: Any])
        let aspect = try XCTUnwrap(props["aspect"] as? [String: Any])
        XCTAssertEqual(aspect["enum"] as? [String], ["9:16", "16:9", "1:1"])
        XCTAssertEqual(aspect["type"] as? String, "string")
        let limit = try XCTUnwrap(props["limit"] as? [String: Any])
        XCTAssertEqual(limit["type"] as? String, "integer")
        let flag = try XCTUnwrap(props["flag"] as? [String: Any])
        XCTAssertEqual(flag["type"] as? String, "boolean")
        XCTAssertEqual(schema["required"] as? [String], ["topic"])
    }

    func testSpecWithNoParamsHasEmptyPropertiesAndNoRequired() throws {
        let schema = AgentToolSpec(name: "ping", summary: "Ping", params: []).jsonSchema()
        XCTAssertEqual(schema["type"] as? String, "object")
        let props = try XCTUnwrap(schema["properties"] as? [String: Any])
        XCTAssertTrue(props.isEmpty)
        XCTAssertNil(schema["required"], "an empty `required` list is rejected by some validators")
    }

    // MARK: Arguments

    func testArgsAreTolerant() {
        let a = AgentArgs(json: #"{"limit":"3","flag":"true","n":4,"half":2.5,"whole":6.0}"#)
        XCTAssertEqual(a.int("limit"), 3)
        XCTAssertEqual(a.int("n"), 4)
        XCTAssertEqual(a.int("whole"), 6)
        XCTAssertNil(a.int("half"), "a fractional number is not an integer")
        XCTAssertEqual(a.bool("flag"), true)
        XCTAssertEqual(AgentArgs(json: #"{"x":"No"}"#).bool("x"), false)
        XCTAssertNil(AgentArgs(json: #"{"x":"maybe"}"#).bool("x"))
        XCTAssertNil(a.int("missing"))
    }

    func testBrokenOrNonObjectJSONIsEmptyNotACrash() {
        XCTAssertNil(AgentArgs(json: "not json").string("x"))
        XCTAssertNil(AgentArgs(json: "").string("x"))
        XCTAssertNil(AgentArgs(json: "[1,2]").string("x"))
        XCTAssertNil(AgentArgs(json: "[1,2]").int("0"))
        XCTAssertNil(AgentArgs(json: #"{"x": "#).string("x"))
    }

    func testStringsAreTrimmedAndBlankIsNil() {
        XCTAssertEqual(AgentArgs(json: #"{"topic":"  cats  "}"#).string("topic"), "cats")
        XCTAssertNil(AgentArgs(json: #"{"topic":"   "}"#).string("topic"))
        XCTAssertNil(AgentArgs(json: #"{"topic":5}"#).string("topic"), "a number is not a string")
    }

    // MARK: Trimming

    func testTrimNeverStartsOnAToolMessage() {
        let all = turn(1) + turn(2) + turn(3) + turn(4)
        for keep in 1...20 {
            let t = AgentHistory.trimmed(all, keepLast: keep)
            assertWellFormed(t)
            XCTAssertEqual(t.last?.id, all.last?.id, "the newest message is always kept (keepLast \(keep))")
        }
    }

    func testTrimKeepsToolResultsWithTheirAssistantCall() {
        // keepLast 3 would cut at turn 4's tool result; with no user message
        // after the cut it must move BACK to turn 4's user message.
        let all = turn(1) + turn(2) + turn(3) + turn(4)
        let t = AgentHistory.trimmed(all, keepLast: 3)
        XCTAssertEqual(t.count, 4)
        XCTAssertEqual(t.first?.text, "q4")
        assertWellFormed(t)
    }

    func testTrimMovesForwardToTheNextUserMessage() {
        let all = turn(1) + turn(2) + turn(3)
        // keepLast 6 cuts at index 6 (inside turn 2's exchange); next user is turn 3.
        let t = AgentHistory.trimmed(all, keepLast: 6)
        XCTAssertEqual(t.first?.text, "q3")
        XCTAssertEqual(t.count, 4)
    }

    func testTrimLeavesShortHistoryUnchanged() {
        let all = turn(1) + turn(2)
        XCTAssertEqual(AgentHistory.trimmed(all, keepLast: 8).map(\.id), all.map(\.id))
        XCTAssertEqual(AgentHistory.trimmed(all, keepLast: 99).map(\.id), all.map(\.id))
    }

    func testTrimNeverCutsAtANudge() {
        let all = [AgentMessage.user("q1"), .assistant(text: "", calls: [call("c1")]),
                   .tool(callID: "c1", name: "x", text: "r"), .nudge("fix your call"),
                   .assistant(text: "a1"), .user("q2"), .assistant(text: "a2")]
        // keepLast 4 cuts exactly at the nudge (index 3); the next REAL user is q2.
        let t = AgentHistory.trimmed(all, keepLast: 4)
        XCTAssertEqual(t.first?.text, "q2")
        assertWellFormed(t)
    }

    func testTrimWithNoUserMessageIsEmpty() {
        let all = [AgentMessage.assistant(text: "a"), .assistant(text: "b"), .assistant(text: "c")]
        XCTAssertTrue(AgentHistory.trimmed(all, keepLast: 2).isEmpty)
        XCTAssertTrue(AgentHistory.trimmed(all, keepLast: 0).isEmpty)
    }

    // MARK: Compaction

    func testCompactionKeepsUserTextAndFinalAnswers() {
        let all = turn(1) + turn(2)
        let c = AgentHistory.compacted(all, keepingLastTurn: false)
        XCTAssertEqual(c.map(\.text), ["q1", "a1", "q2", "a2"])
        XCTAssertEqual(c.map(\.role), [.user, .assistant, .user, .assistant])
        XCTAssertTrue(c.allSatisfy { $0.toolCalls.isEmpty })
    }

    func testCompactionKeepsTheLastTurnIntactWhenAsked() {
        let unfinished: [AgentMessage] = [.user("q3"), .assistant(text: "", calls: [call("c3")]),
                                          .tool(callID: "c3", name: "x", text: "r3")]
        let c = AgentHistory.compacted(turn(1) + unfinished, keepingLastTurn: true)
        XCTAssertEqual(c.map(\.text), ["q1", "a1", "q3", "", "r3"])
        assertWellFormed(c)
    }

    func testCompactionOfATurnWithNoFinalAnswerKeepsOnlyTheUserMessage() {
        let unfinished: [AgentMessage] = [.user("q1"), .assistant(text: "", calls: [call("c1")]),
                                          .tool(callID: "c1", name: "x", text: "r1")]
        let c = AgentHistory.compacted(unfinished, keepingLastTurn: false)
        XCTAssertEqual(c.map(\.text), ["q1"])
    }

    func testCompactionDropsNudgesAndDoesNotSplitATurn() {
        let all = [AgentMessage.user("q1"), .assistant(text: "", calls: [call("c1")]),
                   .tool(callID: "c1", name: "x", text: "r"), .nudge("fix your call"),
                   .assistant(text: "final")]
        let c = AgentHistory.compacted(all, keepingLastTurn: false)
        XCTAssertEqual(c.map(\.text), ["q1", "final"])
    }

    func testCompactionPicksTheLastAnswerWithoutCalls() {
        let all = [AgentMessage.user("q1"), .assistant(text: "Checking…", calls: [call("c1")]),
                   .tool(callID: "c1", name: "x", text: "r"), .assistant(text: "first answer"),
                   .assistant(text: "second answer")]
        let c = AgentHistory.compacted(all, keepingLastTurn: false)
        XCTAssertEqual(c.map(\.text), ["q1", "second answer"])
    }

    func testCompactionDropsProviderPayloads() {
        let all = [AgentMessage.user("q1"),
                   .assistant(text: "done", payload: Data("raw model turn".utf8))]
        let c = AgentHistory.compacted(all, keepingLastTurn: false)
        XCTAssertEqual(c.count, 2)
        XCTAssertNil(c[1].providerPayload, "a stale payload must never travel to the next request")
    }

    func testCompactionDropsAnythingBeforeTheFirstUserMessage() {
        let all = [AgentMessage.assistant(text: "stray")] + turn(1)
        let c = AgentHistory.compacted(all, keepingLastTurn: false)
        XCTAssertEqual(c.map(\.text), ["q1", "a1"])
    }
}

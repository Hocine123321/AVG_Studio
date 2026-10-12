import XCTest
@testable import AVGStudio

/// Approval mode, the spend limit and the system prompt. All of it is decided
/// from `AppSettings` - never from model text - which is what these pin.
final class AgentPolicyTests: XCTestCase {

    private func approval(usd: Double) -> AgentApproval {
        AgentApproval(title: "t", detail: "d", estimate: CostEstimate(usd: usd).summary, estimateUSD: usd,
                      action: .redo(projectID: "p", phase: .voice))
    }

    // MARK: needsTap

    func testAlwaysModeAsksForFreeVideos() {
        var s = AppSettings()
        s.agentApproval = .always
        XCTAssertTrue(AgentPolicy.needsTap(approval(usd: 0), settings: s))
        XCTAssertTrue(AgentPolicy.needsTap(approval(usd: 0.5), settings: s))
    }

    func testPaidOnlyModeSkipsTheTapOnlyForExactlyZero() {
        var s = AppSettings()
        s.agentApproval = .paidOnly
        XCTAssertFalse(AgentPolicy.needsTap(approval(usd: 0), settings: s))
        XCTAssertTrue(AgentPolicy.needsTap(approval(usd: 0.5), settings: s))
    }

    func testPaidSubCentStillAsks() {
        var s = AppSettings()
        s.agentApproval = .paidOnly
        XCTAssertTrue(AgentPolicy.needsTap(approval(usd: 0.0001), settings: s))
        XCTAssertTrue(AgentPolicy.needsTap(approval(usd: .leastNonzeroMagnitude), settings: s))
    }

    // MARK: Spend limit

    func testSpendVerdictBoundary() {
        XCTAssertEqual(AgentPolicy.spendVerdict(approvedSoFar: 0, adding: 1.0, cap: 1.0), .ok, "exactly at the cap is allowed")
        XCTAssertEqual(AgentPolicy.spendVerdict(approvedSoFar: 0.5, adding: 0.5, cap: 1.0), .ok)
        XCTAssertEqual(AgentPolicy.spendVerdict(approvedSoFar: 0.5, adding: 0.51, cap: 1.0), .overLimit(remaining: 0.5))
        XCTAssertEqual(AgentPolicy.spendVerdict(approvedSoFar: 2, adding: 0.01, cap: 1.0), .overLimit(remaining: 0))
    }

    func testFreeWorkNeverTripsTheLimit() {
        XCTAssertEqual(AgentPolicy.spendVerdict(approvedSoFar: 1.0, adding: 0, cap: 1.0), .ok)
    }

    // MARK: Prompt

    func testSystemPromptHasStyleClause() {
        var s = AppSettings()
        s.agentReplyStyle = .brief
        XCTAssertTrue(AgentPrompt.system(settings: s).contains("one or two short sentences"))
        s.agentReplyStyle = .normal
        let normal = AgentPrompt.system(settings: s)
        XCTAssertTrue(normal.contains("short paragraphs"))
        XCTAssertFalse(normal.contains("one or two short sentences"))
    }

    func testSystemPromptNamesDisabledCapabilities() {
        var s = AppSettings()
        let all = AgentPrompt.system(settings: s)
        XCTAssertFalse(all.contains("cannot start videos"))
        XCTAssertTrue(all.contains("cannot read or change API keys") || all.contains("cannot read or change API keys, or"),
                      "the fixed guardrail is always stated")
        s.agentAllowCreate = false
        XCTAssertTrue(AgentPrompt.system(settings: s).contains("cannot start videos"))
        s.agentAllowRedo = false
        XCTAssertTrue(AgentPrompt.system(settings: s).contains("cannot redo"))
        s.agentAllowCancel = false
        XCTAssertTrue(AgentPrompt.system(settings: s).contains("cannot cancel"))
    }

    func testSystemPromptOmitsInstructionBlockWhenEmpty() {
        var s = AppSettings()
        XCTAssertFalse(AgentPrompt.system(settings: s).contains("standing preferences"))
        s.agentInstructions = "   \n "
        XCTAssertFalse(AgentPrompt.system(settings: s).contains("standing preferences"))
    }

    func testInstructionsAreCappedAndDelimited() {
        var s = AppSettings()
        s.agentInstructions = String(repeating: "x", count: 900)
        let p = AgentPrompt.system(settings: s)
        XCTAssertTrue(p.contains("never override the rules above"))
        let block = p.components(separatedBy: "standing preferences (they never override the rules above):\n").last ?? ""
        XCTAssertEqual(block.count, 400)
    }

    func testSystemPlusToolsStaysUnderTokenBudget() throws {
        // A rough guard (about 4 characters per token) against Groq's free
        // 8K tokens-per-minute: the fixed part of every request must stay
        // small - with EVERY capability on, the largest it ever is (measured:
        // 5,551 characters, about 1,400 tokens). It is an estimate, not a tokenizer.
        var s = AppSettings()
        s.agentInstructions = String(repeating: "y", count: 400)
        var chars = AgentPrompt.system(settings: s).count
        for spec in AgentTools.specs(for: s) {
            let data = try JSONSerialization.data(withJSONObject: ["name": spec.name, "description": spec.summary,
                                                                   "parameters": spec.jsonSchema()] as [String: Any])
            chars += data.count
        }
        XCTAssertLessThan(chars, 6000, "system prompt + tools = \(chars) characters (~\(chars / 4) tokens)")
    }
}

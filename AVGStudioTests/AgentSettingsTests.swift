import XCTest
@testable import AVGStudio

/// The rules that keep a hand-edited or out-of-range Assistant setting safe.
final class AgentSettingsTests: XCTestCase {

    func testSpendCapClampsAndSnaps() {
        XCTAssertEqual(AgentSettingsRules.spendCap(0), 0.25)
        XCTAssertEqual(AgentSettingsRules.spendCap(-3), 0.25)
        XCTAssertEqual(AgentSettingsRules.spendCap(99), 10)
        XCTAssertEqual(AgentSettingsRules.spendCap(.infinity), 10)
        XCTAssertEqual(AgentSettingsRules.spendCap(-.infinity), 0.25)
        XCTAssertEqual(AgentSettingsRules.spendCap(0.6), 0.5)
        XCTAssertEqual(AgentSettingsRules.spendCap(1.0), 1.0)
    }

    func testSpendCapNaNFallsBackToTheDefaultNotToNoLimit() {
        XCTAssertEqual(AgentSettingsRules.spendCap(.nan), AgentSettingsRules.spendCapDefault)
    }

    func testInstructionsAreTrimmedAndCapped() {
        XCTAssertEqual(AgentSettingsRules.instructions("  \n be brief \n "), "be brief")
        let long = String(repeating: "a", count: 500)
        XCTAssertEqual(AgentSettingsRules.instructions(long).count, 400)
    }

    func testInstructionCutNeverSplitsACharacter() {
        let emoji = String(repeating: "👍🏽", count: 450)   // each is ONE Character
        let cut = AgentSettingsRules.instructions(emoji)
        XCTAssertEqual(cut.count, 400)
        XCTAssertEqual(cut, String(repeating: "👍🏽", count: 400))
    }

    func testBlankInstructionsBecomeEmpty() {
        XCTAssertEqual(AgentSettingsRules.instructions("   \n\t "), "")
        XCTAssertEqual(AgentSettingsRules.instructions(""), "")
    }

    func testTheOtherProviderIsTheOtherOne() {
        XCTAssertEqual(AgentProviderKind.groq.other, .gemini)
        XCTAssertEqual(AgentProviderKind.gemini.other, .groq)
    }

    func testEveryChoiceHasALabel() {
        XCTAssertTrue(AgentProviderKind.allCases.allSatisfy { !$0.label.isEmpty })
        XCTAssertTrue(AgentApprovalMode.allCases.allSatisfy { !$0.label.isEmpty })
        XCTAssertTrue(AgentReplyStyle.allCases.allSatisfy { !$0.label.isEmpty })
    }
}

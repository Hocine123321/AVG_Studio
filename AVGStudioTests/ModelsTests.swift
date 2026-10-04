import XCTest
@testable import AVGStudio

/// `AppSettings` (and `Channel`) hand-write a tolerant `init(from:)` so an
/// older settings file survives a new field. That is exactly the code where
/// forgetting to add a field is invisible until a user's value silently
/// resets - so the contract is pinned here.
final class ModelsTests: XCTestCase {

    func testEmptyDocumentDecodesToDefaults() throws {
        let decoded = try JSONDecoder().decode(AppSettings.self, from: Data("{}".utf8))
        let defaults = AppSettings()
        XCTAssertEqual(decoded.onboarded, defaults.onboarded)
        XCTAssertEqual(decoded.fps, defaults.fps)
        XCTAssertEqual(decoded.glassOpacity, defaults.glassOpacity)
        XCTAssertEqual(decoded.imageStyle, defaults.imageStyle)
    }

    func testUnknownKeysAreIgnored() throws {
        let json = #"{"onboarded": true, "someFutureField": 42}"#
        let decoded = try JSONDecoder().decode(AppSettings.self, from: Data(json.utf8))
        XCTAssertTrue(decoded.onboarded)
    }

    func testSettingsRoundTrip() throws {
        var s = AppSettings()
        s.onboarded = true
        s.imageStyle = "watercolor"
        s.fps = 24
        s.glassOpacity = 0.6
        let back = try JSONDecoder().decode(AppSettings.self, from: JSONEncoder().encode(s))
        XCTAssertTrue(back.onboarded)
        XCTAssertEqual(back.imageStyle, "watercolor")
        XCTAssertEqual(back.fps, 24)
        XCTAssertEqual(back.glassOpacity, 0.6, accuracy: 1e-9)
    }

    func testPhaseOrderMatchesTheDocumentedPipeline() {
        XCTAssertEqual(PhaseID.allCases, [.writing, .visuals, .voice, .captions, .render])
        XCTAssertEqual(PhaseID.render.order, 4)
    }
}

    // MARK: Assistant settings

    func testAgentSettingsDecodeToDefaultsFromEmptyJSON() throws {
        let s = try JSONDecoder().decode(AppSettings.self, from: Data("{}".utf8))
        XCTAssertEqual(s.agentProvider, .groq)
        XCTAssertEqual(s.agentGroqModel, "llama-3.3-70b-versatile")
        XCTAssertEqual(s.agentGeminiModel, "gemini-2.5-flash")
        XCTAssertTrue(s.agentFailover)
        XCTAssertTrue(s.agentAllowCreate)
        XCTAssertTrue(s.agentAllowRedo)
        XCTAssertTrue(s.agentAllowCancel)
        XCTAssertEqual(s.agentApproval, .always)
        XCTAssertEqual(s.agentChatSpendCapUSD, 1.0, accuracy: 1e-9)
        XCTAssertEqual(s.agentReplyStyle, .brief)
        XCTAssertTrue(s.agentAnnounceResults)
        XCTAssertFalse(s.agentKeepChat)
        XCTAssertEqual(s.agentInstructions, "")
    }

    func testUnknownAgentEnumValuesFallBackToDefaults() throws {
        let json = #"{"agentProvider":"nope","agentApproval":"nope","agentReplyStyle":"nope","agentFailover":false}"#
        let s = try JSONDecoder().decode(AppSettings.self, from: Data(json.utf8))
        XCTAssertEqual(s.agentProvider, .groq)
        XCTAssertEqual(s.agentApproval, .always)
        XCTAssertEqual(s.agentReplyStyle, .brief)
        XCTAssertFalse(s.agentFailover, "a bad sibling value must not reset a good one")
    }

    func testAgentSettingsRoundTrip() throws {
        var s = AppSettings()
        s.agentProvider = .gemini
        s.agentGroqModel = "openai/gpt-oss-120b"
        s.agentGeminiModel = "gemini-3.8-flash"
        s.agentFailover = false
        s.agentAllowCreate = false
        s.agentAllowRedo = false
        s.agentAllowCancel = false
        s.agentApproval = .paidOnly
        s.agentChatSpendCapUSD = 2.5
        s.agentReplyStyle = .normal
        s.agentAnnounceResults = false
        s.agentKeepChat = true
        s.agentInstructions = "Use my Facts channel."
        let back = try JSONDecoder().decode(AppSettings.self, from: JSONEncoder().encode(s))
        XCTAssertEqual(back.agentProvider, .gemini)
        XCTAssertEqual(back.agentGroqModel, "openai/gpt-oss-120b")
        XCTAssertEqual(back.agentGeminiModel, "gemini-3.8-flash")
        XCTAssertFalse(back.agentFailover)
        XCTAssertFalse(back.agentAllowCreate)
        XCTAssertFalse(back.agentAllowRedo)
        XCTAssertFalse(back.agentAllowCancel)
        XCTAssertEqual(back.agentApproval, .paidOnly)
        XCTAssertEqual(back.agentChatSpendCapUSD, 2.5, accuracy: 1e-9)
        XCTAssertEqual(back.agentReplyStyle, .normal)
        XCTAssertFalse(back.agentAnnounceResults)
        XCTAssertTrue(back.agentKeepChat)
        XCTAssertEqual(back.agentInstructions, "Use my Facts channel.")
    }
}

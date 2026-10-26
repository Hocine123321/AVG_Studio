import XCTest
@testable import AVGStudio

final class AITeamTests: XCTestCase {
    private func team(_ preset: AITeamPreset, keys: Set<APIKey> = [], apple: Bool = true) -> TeamAssignment {
        AITeamPreset.assignment(preset, keys: keys, appleAvailable: apple)
    }

    func testFreeNeedsNothingAndIgnoresKeys() {
        let expected = TeamAssignment(text: .apple, image: .pollinations, voice: .apple, needs: [])
        XCTAssertEqual(team(.free), expected)
        XCTAssertEqual(team(.free, keys: [.gemini, .elevenlabs]), expected, "Free stays free even when keys are saved")
    }

    func testFreeWriterIsPollinationsWithoutAppleIntelligence() {
        XCTAssertEqual(team(.free, apple: false).text, .pollinations)
    }

    func testBalancedWithoutAKeyFallsBackAndSaysWhatIsMissing() {
        let t = team(.balanced)
        XCTAssertEqual(t.text, .apple)
        XCTAssertEqual(t.needs, [.gemini])
        XCTAssertEqual(team(.balanced, apple: false).text, .pollinations)
    }

    func testBalancedWithAGoogleKeyStaffsTheWriterOnly() {
        XCTAssertEqual(team(.balanced, keys: [.gemini]),
                       TeamAssignment(text: .gemini, image: .pollinations, voice: .apple, needs: []))
    }

    func testPremiumUsesGoogleForWritingAndImages() {
        XCTAssertEqual(team(.premium, keys: [.gemini]),
                       TeamAssignment(text: .gemini, image: .gemini, voice: .gemini, needs: []))
    }

    func testPremiumPrefersElevenLabsForTheVoiceWhenItsKeyIsSaved() {
        XCTAssertEqual(team(.premium, keys: [.gemini, .elevenlabs]).voice, .elevenlabs)
    }

    func testPremiumWithoutGoogleStaysFreeExceptAnElevenLabsVoice() {
        XCTAssertEqual(team(.premium, keys: [.elevenlabs]),
                       TeamAssignment(text: .apple, image: .pollinations, voice: .elevenlabs, needs: [.gemini]))
    }

    func testEveryPresetHasATitleAndABlurb() {
        XCTAssertEqual(AITeamPreset.allCases.map(\.title), ["Free", "Balanced", "Premium"])
        XCTAssertTrue(AITeamPreset.allCases.allSatisfy { !$0.blurb.isEmpty })
    }
}

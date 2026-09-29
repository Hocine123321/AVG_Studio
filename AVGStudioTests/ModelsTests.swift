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

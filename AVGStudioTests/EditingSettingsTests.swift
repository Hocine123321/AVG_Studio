import XCTest
@testable import AVGStudio

final class EditingSettingsTests: XCTestCase {
    func testAnOlderSettingsFileGetsTheDefaults() throws {
        let s = try JSONDecoder().decode(AppSettings.self, from: Data("{}".utf8))
        XCTAssertEqual(s.editGateSeconds, 10)
        XCTAssertEqual(s.defaultEditingMode, .ask)
        XCTAssertEqual(s.defaultEditLevel, .fast)
        XCTAssertFalse(s.previewSharp)
    }

    func testTheNewSettingsSurviveARoundTrip() throws {
        var s = AppSettings()
        s.editGateSeconds = 0
        s.defaultEditingMode = .manual
        s.defaultEditLevel = .smart
        s.previewSharp = true
        let back = try JSONDecoder().decode(AppSettings.self, from: JSONEncoder().encode(s))
        XCTAssertEqual(back.editGateSeconds, 0)
        XCTAssertEqual(back.defaultEditingMode, .manual)
        XCTAssertEqual(back.defaultEditLevel, .smart)
        XCTAssertTrue(back.previewSharp)
    }

    func testNeverAskMakesEveryModeGoStraightOn() {
        for mode in EditingMode.allCases {
            XCTAssertEqual(EditGatePolicy.outcome(mode: mode, hasTakes: false, alreadyAnswered: false, appActive: true, gateSeconds: 0),
                           .goOn, "\(mode)")
        }
    }

    func testAnyPositiveLengthKeepsTheNormalBehaviour() {
        for seconds in EditGate.choices where seconds > 0 {
            XCTAssertEqual(EditGatePolicy.outcome(mode: .ask, hasTakes: false, alreadyAnswered: false, appActive: true, gateSeconds: seconds), .ask)
            XCTAssertEqual(EditGatePolicy.outcome(mode: .manual, hasTakes: false, alreadyAnswered: false, appActive: true, gateSeconds: seconds), .hold)
        }
    }

    func testTheChoicesIncludeNeverAndTheDefault() {
        XCTAssertTrue(EditGate.choices.contains(0))
        XCTAssertTrue(EditGate.choices.contains(EditGate.defaultSeconds))
        XCTAssertEqual(EditGate.choices, EditGate.choices.sorted())
    }

    func testTheEditingSettingsAreSearchable() {
        XCTAssertTrue(SettingsIndex.search("edit it yourself").contains { $0.title == "Offer to edit before the first render" })
        XCTAssertTrue(SettingsIndex.search("punch").contains { $0.title == "New channels: cuts and punch-ins" })
        XCTAssertTrue(SettingsIndex.search("preview").contains { $0.title == "Sharper editor preview" })
        let editing = SettingsIndex.all.filter { $0.place == "Editing" }
        XCTAssertEqual(editing.count, 4)
        for entry in editing {
            guard case .category(let category, _) = entry.route else { return XCTFail(entry.title) }
            XCTAssertEqual(category, .editing, entry.title)
        }
    }
}

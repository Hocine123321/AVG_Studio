import XCTest
@testable import AVGStudio

// The per-video spending limit: a guard rail, not a bill. Pure rules here;
// the pipeline calls them before every paid step.

final class SpendLimitTests: XCTestCase {
    func testNoLimitNeverBlocks() {
        XCTAssertFalse(SpendLimit.blocks(spent: 99, next: 99, cap: 0))
        XCTAssertFalse(SpendLimit.blocks(spent: 99, cap: 0))
    }

    func testAStepThatWouldGoPastTheLimitIsStoppedBeforeItRuns() {
        XCTAssertFalse(SpendLimit.blocks(spent: 0.30, next: 0.20, cap: 0.50), "exactly at the limit is allowed")
        XCTAssertTrue(SpendLimit.blocks(spent: 0.30, next: 0.21, cap: 0.50))
        XCTAssertTrue(SpendLimit.blocks(spent: 0, next: 0.40, cap: 0.25), "a single step bigger than the limit")
    }

    func testAStepWithAnUnknownCostIsStoppedOnceTheLimitIsReached() {
        XCTAssertFalse(SpendLimit.blocks(spent: 0.49, cap: 0.50))
        XCTAssertTrue(SpendLimit.blocks(spent: 0.50, cap: 0.50))
        XCTAssertTrue(SpendLimit.blocks(spent: 0.80, cap: 0.50))
    }

    func testTheMessagesSayWhatHappenedAndWhereToChangeIt() {
        let before = SpendLimit.message(spent: 0.30, next: 0.40, cap: 0.50)
        XCTAssertTrue(before.contains("$0.40"))
        XCTAssertTrue(before.contains("$0.30"))
        XCTAssertTrue(before.contains("$0.50"))
        XCTAssertTrue(before.contains("Settings > API keys > Spending"))

        let reached = SpendLimit.message(spent: 0.52, cap: 0.50)
        XCTAssertTrue(reached.contains("reached"))
        XCTAssertTrue(reached.contains("$0.52"))
    }

    func testTheChoicesStartWithNoLimitAndGrow() {
        XCTAssertEqual(SpendLimit.choices.first, 0)
        XCTAssertEqual(SpendLimit.choices, SpendLimit.choices.sorted())
        XCTAssertEqual(SpendLimit.label(0), "No limit")
        XCTAssertEqual(SpendLimit.label(0.5), "$0.50")
        XCTAssertEqual(SpendLimit.label(5), "$5.00")
    }

    func testTheLimitIsOffByDefaultSoNothingChangesForAnOlderFile() throws {
        XCTAssertEqual(AppSettings().videoSpendCapUSD, 0)
        let old = try JSONDecoder().decode(AppSettings.self, from: Data("{}".utf8))
        XCTAssertEqual(old.videoSpendCapUSD, 0)
        var s = AppSettings()
        s.videoSpendCapUSD = 1
        let back = try JSONDecoder().decode(AppSettings.self, from: JSONEncoder().encode(s))
        XCTAssertEqual(back.videoSpendCapUSD, 1)
    }

    func testTheLimitIsSearchableOnTheKeysPage() {
        let hit = SettingsIndex.search("spending limit").first { $0.title == "Spending limit per video" }
        XCTAssertNotNil(hit)
        guard case .category(let category, let focus)? = hit?.route else { return XCTFail("opens the keys page") }
        XCTAssertEqual(category, .keys)
        XCTAssertEqual(focus, .videoSpendCap)
    }
}

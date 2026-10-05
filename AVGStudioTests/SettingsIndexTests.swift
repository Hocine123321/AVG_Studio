import XCTest
@testable import AVGStudio

/// Settings search is only as good as its index. These tests make a setting
/// that ships without a search entry a failing test, not a silent gap.
final class SettingsIndexTests: XCTestCase {

    func testEverySettingAnchorIsSearchable() {
        let indexed = Set(SettingsIndex.all.compactMap(\.anchor))
        for anchor in SettingAnchor.allCases {
            XCTAssertTrue(indexed.contains(anchor), "\(anchor) has no search entry")
        }
    }

    func testEntryTitlesAreUnique() {
        let titles = SettingsIndex.all.map(\.title)
        XCTAssertEqual(titles.count, Set(titles).count, "duplicate titles break identity in the results list")
    }

    func testEntriesPointAtTheirOwnAnchor() {
        for entry in SettingsIndex.all {
            if case .category(_, let focus) = entry.route {
                XCTAssertEqual(focus, entry.anchor, entry.title)
            }
        }
    }

    func testEmptyAndBlankQueriesFindNothing() {
        XCTAssertTrue(SettingsIndex.search("").isEmpty)
        XCTAssertTrue(SettingsIndex.search("   ").isEmpty)
    }

    func testKeywordsAndAliasesFindTheRightSetting() {
        XCTAssertEqual(SettingsIndex.search("resolution").first?.title, "Quality")
        XCTAssertEqual(SettingsIndex.search("fps").first?.title, "Frame rate")
        XCTAssertEqual(SettingsIndex.search("dark").first?.title, "Theme")
        XCTAssertTrue(SettingsIndex.search("groq").contains { $0.title == "Writer" })
    }

    func testTitleMatchesRankAboveKeywordMatches() {
        XCTAssertEqual(SettingsIndex.search("theme").first?.title, "Theme")
        let pause = SettingsIndex.search("pause").map(\.title)
        XCTAssertEqual(pause.first, "Pause length", "prefix match outranks a substring match")
        XCTAssertTrue(pause.contains("Dynamic pauses"))
    }

    func testEveryWordOfAMultiWordQueryMustMatch() {
        XCTAssertEqual(SettingsIndex.search("caption size").first?.title, "Caption size")
        XCTAssertTrue(SettingsIndex.search("caption zzzz").isEmpty)
    }

    func testEveryCategoryHasAtLeastOneSearchEntry() {
        let places = Set(SettingsIndex.all.map(\.place))
        for category in SettingsCategory.allCases {
            XCTAssertTrue(places.contains(category.title), "\(category.title) is unreachable by search")
        }
    }

    func testAssistantSettingsAreSearchable() {
        XCTAssertEqual(SettingsIndex.search("budget").first?.title, "Spend limit per chat")
        XCTAssertTrue(SettingsIndex.search("approve").contains { $0.title == "Ask before starting" })
        XCTAssertTrue(SettingsIndex.search("memory").contains { $0.title == "Keep chat" })
        XCTAssertTrue(SettingsIndex.search("assistant").count >= 14, "every Assistant setting answers to its category name")
        XCTAssertTrue(SettingsIndex.search("assistant").allSatisfy { $0.place == "Assistant" })
    }

    func testAssistantEntriesLandOnTheAssistantPage() {
        for entry in SettingsIndex.all where entry.place == "Assistant" {
            guard case .category(let category, _) = entry.route else { return XCTFail(entry.title) }
            XCTAssertEqual(category, .assistant, entry.title)
        }
    }
}

import XCTest
@testable import AVGStudio

/// The Library's pure filter, and the assistant's Markdown clean-up.
final class LibraryFilterTests: XCTestCase {

    private func project(_ title: String, archived: Bool = false, _ status: PhaseStatus = .pending) -> Project {
        var p = Project(id: "id-\(title)", title: title, topic: title)
        p.archived = archived
        for phase in PhaseID.allCases { p.phases[phase.rawValue] = PhaseRecord(status: status) }
        return p
    }

    private var sample: [Project] {
        [project("Volcanoes", .done), project("Cats of Rome", .running), project("Old one", archived: true, .done),
         project("Draft idea"), project("Café culture", .done)]
    }

    private func titles(_ f: LibraryFilter, _ q: String = "") -> [String] { f.apply(to: sample, query: q).map { $0.title } }

    func testAllHidesTheArchive() {
        XCTAssertEqual(titles(.all), ["Volcanoes", "Cats of Rome", "Draft idea", "Café culture"])
    }

    func testRunningAndDoneAreByStatusAndNeverIncludeArchived() {
        XCTAssertEqual(titles(.running), ["Cats of Rome"])
        XCTAssertEqual(titles(.done), ["Volcanoes", "Café culture"], "the archived finished video is not here")
    }

    func testArchivedShowsOnlyTheArchive() {
        XCTAssertEqual(titles(.archived), ["Old one"])
    }

    func testSearchIsCaseAndAccentInsensitiveAndKeepsOrder() {
        XCTAssertEqual(titles(.all, "CATS"), ["Cats of Rome"])
        XCTAssertEqual(titles(.all, "cafe"), ["Café culture"])
        XCTAssertEqual(titles(.all, "o"), ["Volcanoes", "Cats of Rome", "Café culture"].filter { $0.localizedCaseInsensitiveContains("o") })
    }

    func testSearchAppliesWithinTheFilter() {
        XCTAssertEqual(titles(.done, "cats"), [])
        XCTAssertEqual(titles(.archived, "old"), ["Old one"])
    }

    func testBlankOrSpacesOnlyQueryShowsEverything() {
        XCTAssertEqual(titles(.all, "   ").count, 4)
    }

    func testNothingMatchesGivesAnEmptyList() {
        XCTAssertTrue(titles(.all, "zzzz").isEmpty)
    }
}

final class AgentMarkdownTests: XCTestCase {

    func testStarDashAndPlusBulletsBecomeBullets() {
        XCTAssertEqual(AgentMarkdown.normalize("* One\n- Two\n+ Three"), "•  One\n•  Two\n•  Three")
    }

    func testNestedBulletsKeepTheirIndent() {
        XCTAssertEqual(AgentMarkdown.normalize("* Top\n  * Inner"), "•  Top\n  •  Inner")
    }

    func testALineThatStartsWithBoldIsNotABullet() {
        XCTAssertEqual(AgentMarkdown.normalize("**Heads up** this is bold"), "**Heads up** this is bold")
        XCTAssertEqual(AgentMarkdown.normalize("*emphasis* at the start"), "*emphasis* at the start")
    }

    func testBoldInsideABulletSurvives() {
        XCTAssertEqual(AgentMarkdown.normalize("* **List Channels** (`list_channels`): View them"),
                       "•  **List Channels** (`list_channels`): View them")
    }

    func testHeadingsBecomeBold() {
        XCTAssertEqual(AgentMarkdown.normalize("## Tools\ntext"), "**Tools**\ntext")
        XCTAssertEqual(AgentMarkdown.normalize("#hashtag"), "#hashtag", "no space = not a heading")
    }

    func testPlainTextNumbersAndNegativesAreUntouched() {
        for line in ["Just a sentence.", "1. First", "-5 degrees", "a - b", "3 * 4 = 12"] {
            XCTAssertEqual(AgentMarkdown.normalize(line), line)
        }
    }

    func testAttributedRendersBoldAndCodeWithoutLiteralMarkers() {
        let rendered = String(AgentMarkdown.attributed("* **Create Video** (`create_video`): make one").characters)
        XCTAssertFalse(rendered.contains("*"), rendered)
        XCTAssertFalse(rendered.contains("`"), rendered)
        XCTAssertTrue(rendered.hasPrefix("•"), rendered)
        XCTAssertTrue(rendered.contains("Create Video"))
    }

    func testAttributedNeverFailsOnBrokenMarkdown() {
        XCTAssertFalse(String(AgentMarkdown.attributed("**unclosed and `half").characters).isEmpty)
        XCTAssertEqual(String(AgentMarkdown.attributed("").characters), "")
    }
}

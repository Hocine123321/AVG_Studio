import XCTest
@testable import AVGStudio

/// The Library grid's packing. The bug these pin: a portrait or square card
/// alone in a row used to be drawn full width, as one huge card.
final class LibraryLayoutTests: XCTestCase {

    private let p = AspectRatio.portrait, l = AspectRatio.landscape, s = AspectRatio.square

    private func shape(_ a: [AspectRatio]) -> [[Int]] { LibraryLayout.rows(a).map { $0.indices } }

    func testEmptyLibraryHasNoRows() {
        XCTAssertTrue(LibraryLayout.rows([]).isEmpty)
    }

    func testNarrowCardsPairUpInOrder() {
        XCTAssertEqual(shape([p, s, p, p]), [[0, 1], [2, 3]])
    }

    func testLandscapeIsAloneAndWide() {
        let rows = LibraryLayout.rows([l, l])
        XCTAssertEqual(rows.map { $0.indices }, [[0], [1]])
        XCTAssertTrue(rows.allSatisfy { $0.isWide })
    }

    func testAnOddNarrowCardIsAHalfWidthRowNotAWideOne() {
        let rows = LibraryLayout.rows([p, p, p])
        XCTAssertEqual(rows.map { $0.indices }, [[0, 1], [2]])
        XCTAssertFalse(rows[1].isWide, "a lone portrait must never span both columns")
    }

    func testAPortraitBeforeALandscapeBorrowsTheNextNarrowCardInsteadOfStandingAlone() {
        XCTAssertEqual(shape([p, l, p]), [[0, 2], [1]])
    }

    func testAPortraitBeforeALandscapeWithNoPartnerIsHalfWidth() {
        let rows = LibraryLayout.rows([p, l])
        XCTAssertEqual(rows.map { $0.indices }, [[0], [1]])
        XCTAssertFalse(rows[0].isWide)
        XCTAssertTrue(rows[1].isWide)
    }

    func testOneNarrowCardAloneIsHalfWidth() {
        let rows = LibraryLayout.rows([s])
        XCTAssertEqual(rows.map { $0.indices }, [[0]])
        XCTAssertFalse(rows[0].isWide)
    }

    func testPartnerIsPulledPastSeveralLandscapes() {
        XCTAssertEqual(shape([p, l, l, s]), [[0, 3], [1], [2]])
    }

    func testEveryProjectAppearsExactlyOnceAndRowsAreWellFormed() {
        // A deterministic pseudo-random walk over many shapes of library.
        var seed: UInt64 = 0x9E3779B97F4A7C15
        func next() -> Int { seed = seed &* 6364136223846793005 &+ 1442695040888963407; return Int(seed >> 33) }
        let all: [AspectRatio] = [.portrait, .landscape, .square]
        for _ in 0..<300 {
            let n = next() % 14
            let aspects = (0..<n).map { _ in all[next() % 3] }
            let rows = LibraryLayout.rows(aspects)
            XCTAssertEqual(rows.flatMap { $0.indices }.sorted(), Array(0..<n), "\(aspects)")
            for r in rows {
                if r.isWide {
                    XCTAssertEqual(r.indices.count, 1)
                    XCTAssertEqual(aspects[r.indices[0]], .landscape)
                } else {
                    XCTAssertTrue((1...2).contains(r.indices.count))
                    XCTAssertTrue(r.indices.allSatisfy { aspects[$0] != .landscape }, "\(aspects)")
                }
            }
            // After a lone card nothing narrow is left to pair, so there is at most
            // one - and exactly one when the number of narrow cards is odd.
            let lonely = rows.filter { !$0.isWide && $0.indices.count == 1 }
            let narrow = aspects.filter { $0 != .landscape }.count
            XCTAssertEqual(lonely.count, narrow % 2, "\(aspects)")
        }
    }
}

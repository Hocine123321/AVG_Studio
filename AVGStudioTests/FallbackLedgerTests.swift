import XCTest
@testable import AVGStudio

/// A fallback is for one run. The bug these pin: it used to be written into
/// the person's settings for good, so they had to re-select their main model
/// after every fallback.
final class FallbackLedgerTests: XCTestCase {

    private func settings() -> AppSettings {
        var s = AppSettings()
        s.geminiModel = "gemini-2.5-pro"
        s.text = .gemini
        return s
    }

    func testOverrideChangesTheSettingForTheRun() {
        var s = settings(), ledger = FallbackLedger()
        ledger.override(\.geminiModel, to: "gemini-2.5-flash-lite", owner: "p1", in: &s)
        XCTAssertEqual(s.geminiModel, "gemini-2.5-flash-lite")
        XCTAssertFalse(ledger.isEmpty)
    }

    func testReleaseRestoresTheMainModel() {
        var s = settings(), ledger = FallbackLedger()
        ledger.override(\.geminiModel, to: "gemini-2.5-flash-lite", owner: "p1", in: &s)
        XCTAssertTrue(ledger.release(owner: "p1", in: &s))
        XCTAssertEqual(s.geminiModel, "gemini-2.5-pro", "the person's choice must come back")
        XCTAssertTrue(ledger.isEmpty)
    }

    func testProviderSwitchesAreUndoneToo() {
        var s = settings(), ledger = FallbackLedger()
        ledger.override(\.text, to: .pollinations, owner: "p1", in: &s)
        ledger.override(\.voice, to: .gemini, owner: "p1", in: &s)
        XCTAssertEqual(s.text, .pollinations)
        ledger.release(owner: "p1", in: &s)
        XCTAssertEqual(s.text, .gemini)
        XCTAssertEqual(s.voice, .apple)
    }

    func testWhatIsSavedToDiskIsNeverTheFallback() {
        var s = settings(), ledger = FallbackLedger()
        ledger.override(\.geminiModel, to: "gemini-2.5-flash-lite", owner: "p1", in: &s)
        XCTAssertEqual(ledger.reverted(s).geminiModel, "gemini-2.5-pro", "a kill mid-run must not leave the fallback saved")
        XCTAssertEqual(s.geminiModel, "gemini-2.5-flash-lite", "the live run still uses the fallback")
    }

    func testAnEditMadeDuringTheRunIsNotClobbered() {
        var s = settings(), ledger = FallbackLedger()
        ledger.override(\.geminiModel, to: "gemini-2.5-flash-lite", owner: "p1", in: &s)
        s.geminiModel = "gemini-3-pro"      // the person picks something else meanwhile
        XCTAssertFalse(ledger.release(owner: "p1", in: &s))
        XCTAssertEqual(s.geminiModel, "gemini-3-pro")
        XCTAssertEqual(ledger.reverted(s).geminiModel, "gemini-3-pro", "and it is saved as they set it")
    }

    func testTwoProjectsShareOneOriginalUntilTheLastEnds() {
        var s = settings(), ledger = FallbackLedger()
        ledger.override(\.geminiModel, to: "lite", owner: "a", in: &s)
        ledger.override(\.geminiModel, to: "lite", owner: "b", in: &s)
        ledger.release(owner: "a", in: &s)
        XCTAssertEqual(s.geminiModel, "lite", "b is still running on the fallback")
        ledger.release(owner: "b", in: &s)
        XCTAssertEqual(s.geminiModel, "gemini-2.5-pro")
    }

    func testASecondOverrideKeepsTheFirstOriginal() {
        var s = settings(), ledger = FallbackLedger()
        ledger.override(\.geminiModel, to: "lite", owner: "a", in: &s)
        ledger.override(\.geminiModel, to: "even-lighter", owner: "b", in: &s)
        ledger.release(owner: "a", in: &s)
        ledger.release(owner: "b", in: &s)
        XCTAssertEqual(s.geminiModel, "gemini-2.5-pro", "never the intermediate fallback")
    }

    func testOverridingToTheSameValueIsANoOp() {
        var s = settings(), ledger = FallbackLedger()
        ledger.override(\.geminiModel, to: "gemini-2.5-pro", owner: "p1", in: &s)
        XCTAssertTrue(ledger.isEmpty)
    }

    func testReleasingAnOwnerWithNothingIsHarmless() {
        var s = settings(), ledger = FallbackLedger()
        XCTAssertFalse(ledger.release(owner: "nobody", in: &s))
        XCTAssertEqual(s.geminiModel, "gemini-2.5-pro")
    }

    func testOtherSettingsAreNeverTouched() {
        var s = settings(), ledger = FallbackLedger()
        s.groqModel = "custom-groq"
        ledger.override(\.geminiModel, to: "lite", owner: "p1", in: &s)
        ledger.release(owner: "p1", in: &s)
        XCTAssertEqual(s.groqModel, "custom-groq")
    }
}

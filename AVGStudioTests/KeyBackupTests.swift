import XCTest
@testable import AVGStudio

/// The backup key's 30-minute window and the rule for when it opens.
final class KeyBackupTests: XCTestCase {

    private var defaults: UserDefaults!
    private let t0 = Date(timeIntervalSince1970: 1_800_000_000)

    override func setUp() {
        super.setUp()
        defaults = UserDefaults(suiteName: "KeyBackupTests-\(UUID().uuidString)")
    }

    private func providerError(_ kind: ErrorKind, _ provider: String = "groq") -> Error {
        AVGError.provider(kind, provider: provider, message: "x")
    }

    func testWindowIsThirtyMinutes() {
        XCTAssertEqual(KeyBackup.window, 1800)
        KeyBackup.activate(.groq, now: t0, defaults: defaults)
        XCTAssertTrue(KeyBackup.isActive(.groq, now: t0.addingTimeInterval(1799), defaults: defaults))
        XCTAssertFalse(KeyBackup.isActive(.groq, now: t0.addingTimeInterval(1800), defaults: defaults),
                       "after 30 minutes the main key is used again")
    }

    func testInactiveByDefaultAndPerProvider() {
        XCTAssertFalse(KeyBackup.isActive(.groq, defaults: defaults))
        KeyBackup.activate(.groq, now: t0, defaults: defaults)
        XCTAssertFalse(KeyBackup.isActive(.gemini, now: t0, defaults: defaults), "one provider's window never opens another's")
    }

    func testRemainingCountsDown() {
        KeyBackup.activate(.gemini, now: t0, defaults: defaults)
        XCTAssertEqual(KeyBackup.remaining(.gemini, now: t0.addingTimeInterval(600), defaults: defaults) ?? 0, 1200, accuracy: 0.5)
        XCTAssertNil(KeyBackup.remaining(.gemini, now: t0.addingTimeInterval(2000), defaults: defaults))
    }

    func testDeactivateClosesTheWindow() {
        KeyBackup.activate(.groq, now: t0, defaults: defaults)
        KeyBackup.deactivate(.groq, defaults: defaults)
        XCTAssertFalse(KeyBackup.isActive(.groq, now: t0, defaults: defaults))
    }

    func testTheWindowSurvivesRelaunchBecauseItIsStored() {
        KeyBackup.activate(.groq, now: t0, defaults: defaults)
        XCTAssertNotNil(KeyBackup.until(.groq, defaults: defaults))
    }

    func testOnlyKeyLevelFailuresCount() {
        for k in [ErrorKind.auth, .quota, .billing, .rateLimit] { XCTAssertTrue(KeyBackup.isKeyLevel(k), "\(k)") }
        for k in [ErrorKind.network, .timeout, .modelMissing, .contentFilter, .dependency, .bug, .malformedOutput] {
            XCTAssertFalse(KeyBackup.isKeyLevel(k), "a different key would not fix \(k)")
        }
    }

    func testAKeyLevelFailureOpensTheWindowWhenABackupExists() {
        let key = KeyBackup.activateIfPossible(after: providerError(.quota, "gemini"), now: t0, defaults: defaults,
                                               hasBackup: { $0 == .gemini })
        XCTAssertEqual(key, .gemini)
        XCTAssertTrue(KeyBackup.isActive(.gemini, now: t0, defaults: defaults))
    }

    func testNoBackupSavedMeansNothingHappens() {
        let key = KeyBackup.activateIfPossible(after: providerError(.auth), now: t0, defaults: defaults, hasBackup: { _ in false })
        XCTAssertNil(key)
        XCTAssertFalse(KeyBackup.isActive(.groq, now: t0, defaults: defaults))
    }

    func testAFailingBackupIsNotRetriedWithItself() {
        KeyBackup.activate(.groq, now: t0, defaults: defaults)
        let key = KeyBackup.activateIfPossible(after: providerError(.auth), now: t0.addingTimeInterval(60), defaults: defaults,
                                               hasBackup: { _ in true })
        XCTAssertNil(key, "the window is already open; the backup failing means the prompt, not another switch")
    }

    func testANetworkErrorNeverSwitchesKeys() {
        XCTAssertNil(KeyBackup.activateIfPossible(after: providerError(.network), now: t0, defaults: defaults, hasBackup: { _ in true }))
    }

    func testNonProviderErrorsAndUnknownProvidersAreIgnored() {
        XCTAssertNil(KeyBackup.activateIfPossible(after: AVGError.message("x"), now: t0, defaults: defaults, hasBackup: { _ in true }))
        XCTAssertNil(KeyBackup.activateIfPossible(after: providerError(.auth, "mystery"), now: t0, defaults: defaults, hasBackup: { _ in true }))
    }

    func testProviderNamesMatchTheKeyNamesCaseInsensitively() {
        XCTAssertEqual(KeyBackup.activateIfPossible(after: providerError(.rateLimit, "ElevenLabs"), now: t0, defaults: defaults,
                                                    hasBackup: { _ in true }), .elevenlabs)
    }

    func testRemainingTextIsFriendly() {
        XCTAssertEqual(KeyBackup.remainingText(1500), "25 min left")
        XCTAssertEqual(KeyBackup.remainingText(20), "under a minute left")
    }
}

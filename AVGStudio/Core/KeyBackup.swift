import Foundation

/// The backup API key's 30-minute window.
///
/// When a provider's MAIN key fails in a way another key could fix - rejected,
/// out of quota, out of credit, rate-limited past all retries - and the app
/// has already tried what it could (the fallback model, the other service),
/// it switches that provider to its BACKUP key for the next 30 minutes, then
/// goes back to the main one. Every provider reads its key through
/// `Keychain.get`, which returns the backup while the window is open, so
/// nothing else needs to know.
///
/// The window is stored as a date in UserDefaults (not a secret) so quitting
/// the app does not hand a failing main key a fresh chance mid-window.
enum KeyBackup {
    static let window: TimeInterval = 30 * 60
    private static let storeKey = "avg.backupKeyUntil"

    private static func table(_ defaults: UserDefaults) -> [String: Double] {
        (defaults.dictionary(forKey: storeKey) as? [String: Double]) ?? [:]
    }

    static func until(_ key: APIKey, defaults: UserDefaults = .standard) -> Date? {
        table(defaults)[key.rawValue].map { Date(timeIntervalSince1970: $0) }
    }

    static func isActive(_ key: APIKey, now: Date = Date(), defaults: UserDefaults = .standard) -> Bool {
        guard let end = until(key, defaults: defaults) else { return false }
        return end > now
    }

    /// Seconds left in the window, or nil when it is not open.
    static func remaining(_ key: APIKey, now: Date = Date(), defaults: UserDefaults = .standard) -> TimeInterval? {
        guard let end = until(key, defaults: defaults), end > now else { return nil }
        return end.timeIntervalSince(now)
    }

    static func activate(_ key: APIKey, now: Date = Date(), defaults: UserDefaults = .standard) {
        var t = table(defaults)
        t[key.rawValue] = now.addingTimeInterval(window).timeIntervalSince1970
        defaults.set(t, forKey: storeKey)
    }

    static func deactivate(_ key: APIKey, defaults: UserDefaults = .standard) {
        var t = table(defaults)
        t[key.rawValue] = nil
        defaults.set(t, forKey: storeKey)
    }

    /// A failure another key could fix. Deliberately NOT network, timeout,
    /// model-missing or content-filter: a different key changes none of those.
    static func isKeyLevel(_ kind: ErrorKind) -> Bool {
        switch kind {
        case .auth, .quota, .billing, .rateLimit: return true
        case .modelMissing, .contentFilter, .network, .timeout, .dependency, .bug, .malformedOutput: return false
        }
    }

    /// Opens the window for the provider `error` came from, when that failure
    /// is key-level, a backup key is saved, and the window is not already
    /// open (a failing BACKUP is not retried with itself). Returns the key
    /// switched, or nil when nothing was switched.
    @discardableResult
    static func activateIfPossible(after error: Error, now: Date = Date(),
                                   defaults: UserDefaults = .standard,
                                   hasBackup: (APIKey) -> Bool = { Keychain.has($0, slot: .backup) }) -> APIKey? {
        guard let avg = error as? AVGError, case .provider(let kind, let provider, _) = avg,
              isKeyLevel(kind),
              let key = APIKey(rawValue: provider.lowercased()),
              hasBackup(key),
              !isActive(key, now: now, defaults: defaults) else { return nil }
        activate(key, now: now, defaults: defaults)
        return key
    }

    /// "24 min left" for the Keys screen.
    static func remainingText(_ seconds: TimeInterval) -> String {
        let minutes = Int((seconds / 60).rounded(.up))
        return minutes <= 1 ? "under a minute left" : "\(minutes) min left"
    }
}

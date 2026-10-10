import Foundation
import Security

enum APIKey: String, CaseIterable, Identifiable {
    case gemini, groq, openai, elevenlabs, assemblyai, stablehorde, pollinations
    var id: String { rawValue }
    var label: String {
        switch self {
        case .gemini: return "Google Gemini"
        case .groq: return "Groq"
        case .openai: return "OpenAI"
        case .elevenlabs: return "ElevenLabs"
        case .assemblyai: return "AssemblyAI (real caption timing)"
        case .stablehorde: return "Stable Horde (optional, for priority)"
        // Optional on the PC too (avg/core/settings_schema.py's "pollinations"
        // key entry) - Pollinations works fully keyless; a key only lifts
        // its (generous) anonymous rate limit.
        case .pollinations: return "Pollinations (optional, for priority)"
        }
    }
}

/// Which of a provider's two keys. The BACKUP key is another key for the same
/// provider (a second account, a second project) that the app switches to for
/// 30 minutes when the main one fails - see `KeyBackup`.
enum KeySlot: Equatable { case main, backup }

/// API keys live in the iPhone Keychain, never in project files.
enum Keychain {
    private static let service = "com.avgstudio.keys"

    /// The main key keeps its original account name, so keys saved by earlier
    /// versions are found unchanged.
    private static func account(_ key: APIKey, _ slot: KeySlot) -> String {
        slot == .main ? key.rawValue : key.rawValue + ".backup"
    }

    /// One slot, exactly as stored.
    static func read(_ key: APIKey, slot: KeySlot = .main) -> String? {
        let q: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account(key, slot),
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let d = out as? Data else { return nil }
        let s = String(decoding: d, as: UTF8.self)
        return s.isEmpty ? nil : s
    }

    /// The key to SEND: the backup while its 30-minute window is open (and a
    /// backup exists), otherwise the main key. Every provider reads its key
    /// through here, so switching is invisible to them.
    static func get(_ key: APIKey) -> String? {
        if KeyBackup.isActive(key), let backup = read(key, slot: .backup) { return backup }
        return read(key)
    }

    /// Saves (or, for a blank value, removes) a key. Returns whether the
    /// Keychain accepted it. Updates an existing item in place instead of
    /// delete-then-add, so a failed write can no longer destroy the key that
    /// was already saved - and re-saving migrates older items to the
    /// device-only accessibility class below.
    @discardableResult
    static func set(_ key: APIKey, _ value: String, slot: KeySlot = .main) -> Bool {
        let base: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account(key, slot),
        ]
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            let status = SecItemDelete(base as CFDictionary)
            return status == errSecSuccess || status == errSecItemNotFound
        }
        let attrs: [String: Any] = [
            kSecValueData as String: Data(trimmed.utf8),
            // ThisDeviceOnly: provider keys should not travel in backups or
            // to a new phone; the user re-enters them (they can be revoked).
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        let status = SecItemUpdate(base as CFDictionary, attrs as CFDictionary)
        if status == errSecItemNotFound {
            var add = base
            add.merge(attrs) { _, new in new }
            return SecItemAdd(add as CFDictionary, nil) == errSecSuccess
        }
        return status == errSecSuccess
    }

    static func remove(_ key: APIKey, slot: KeySlot = .main) { set(key, "", slot: slot) }

    /// Whether that slot holds a key. (Not "is it in use": a saved backup is
    /// only used while its window is open.)
    static func has(_ key: APIKey, slot: KeySlot = .main) -> Bool { read(key, slot: slot) != nil }

    static func require(_ key: APIKey) throws -> String {
        guard let v = get(key) else { throw AVGError.missingKey(key.label) }
        return v
    }

    /// Every saved secret, main AND backup, for scrubbing logs and reports.
    static var allSecrets: [String] {
        APIKey.allCases.flatMap { [read($0), read($0, slot: .backup)] }.compactMap { $0 }
    }
}

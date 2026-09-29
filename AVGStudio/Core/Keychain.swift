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

/// API keys live in the iPhone Keychain, never in project files.
enum Keychain {
    private static let service = "com.avgstudio.keys"

    static func get(_ key: APIKey) -> String? {
        let q: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key.rawValue,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let d = out as? Data else { return nil }
        let s = String(decoding: d, as: UTF8.self)
        return s.isEmpty ? nil : s
    }

    /// Saves (or, for a blank value, removes) a key. Returns whether the
    /// Keychain accepted it. Updates an existing item in place instead of
    /// delete-then-add, so a failed write can no longer destroy the key that
    /// was already saved - and re-saving migrates older items to the
    /// device-only accessibility class below.
    @discardableResult
    static func set(_ key: APIKey, _ value: String) -> Bool {
        let base: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key.rawValue,
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

    static func remove(_ key: APIKey) { set(key, "") }

    static func has(_ key: APIKey) -> Bool { get(key) != nil }

    static func require(_ key: APIKey) throws -> String {
        guard let v = get(key) else { throw AVGError.missingKey(key.label) }
        return v
    }
}

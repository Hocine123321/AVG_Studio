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

    static func set(_ key: APIKey, _ value: String) {
        let base: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key.rawValue,
        ]
        SecItemDelete(base as CFDictionary)
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        var add = base
        add[kSecValueData as String] = Data(trimmed.utf8)
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        SecItemAdd(add as CFDictionary, nil)
    }

    static func has(_ key: APIKey) -> Bool { get(key) != nil }

    static func require(_ key: APIKey) throws -> String {
        guard let v = get(key) else { throw AVGError.missingKey(key.label) }
        return v
    }
}

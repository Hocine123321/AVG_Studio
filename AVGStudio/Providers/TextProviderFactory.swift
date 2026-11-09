import Foundation

/// Builds the text provider for a kind from the current settings and keys, in
/// one place (the pipeline and Hook Bench both need it).
enum TextProviderFactory {
    static func make(_ kind: TextProviderKind, settings s: AppSettings) throws -> TextProvider {
        switch kind {
        case .apple: return AppleText()
        case .gemini: return GeminiText(key: try Keychain.require(.gemini), model: s.geminiModel)
        case .groq: return ChatText.groq(key: try Keychain.require(.groq), model: s.groqModel)
        case .pollinations: return ChatText.pollinations(key: Keychain.get(.pollinations), model: s.pollinationsTextModel)
        }
    }
}

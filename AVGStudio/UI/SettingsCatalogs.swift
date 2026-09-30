import SwiftUI

/// The nine live model lists behind Settings, owned in ONE place.
///
/// They used to be `@State` properties of the single Settings view. Now that
/// each category is its own screen, every screen needs the SAME instances
/// (so a list fetched on one is already loaded on another, and "Refresh all"
/// refreshes what the pickers actually show). The hub creates this once and
/// hands it to the screens through the environment.
///
/// One `ModelCatalog` per provider endpoint - see ModelCatalog.swift for why
/// these are closures rather than near-identical classes.
@MainActor
@Observable
final class SettingsCatalogs {
    let geminiText = ModelCatalog(cacheKey: "gemini_text") {
        guard let key = Keychain.get(.gemini) else { throw AVGError.missingKey(APIKey.gemini.label) }
        return try await ModelCatalogSources.geminiText(key: key)
    }
    let groqText = ModelCatalog(cacheKey: "groq_text") {
        guard let key = Keychain.get(.groq) else { throw AVGError.missingKey(APIKey.groq.label) }
        return try await ModelCatalogSources.groqText(key: key)
    }
    let stableHorde = ModelCatalog(cacheKey: "stablehorde_image") {
        try await ModelCatalogSources.stableHordeImage()
    }
    let elevenVoices = ModelCatalog(cacheKey: "elevenlabs_voices") {
        guard let key = Keychain.get(.elevenlabs) else { throw AVGError.missingKey(APIKey.elevenlabs.label) }
        return try await ModelCatalogSources.elevenlabsVoices(key: key)
    }
    let elevenModels = ModelCatalog(cacheKey: "elevenlabs_models") {
        guard let key = Keychain.get(.elevenlabs) else { throw AVGError.missingKey(APIKey.elevenlabs.label) }
        return try await ModelCatalogSources.elevenlabsModels(key: key)
    }
    let pollinationsText = ModelCatalog(cacheKey: "pollinations_text") {
        try await ModelCatalogSources.pollinationsText(key: Keychain.get(.pollinations))
    }
    let pollinationsImage = ModelCatalog(cacheKey: "pollinations_image") {
        try await ModelCatalogSources.pollinationsImage(key: Keychain.get(.pollinations))
    }
    let geminiImage = ModelCatalog(cacheKey: "gemini_image") {
        guard let key = Keychain.get(.gemini) else { throw AVGError.missingKey(APIKey.gemini.label) }
        return try await ModelCatalogSources.geminiImageModels(key: key)
    }
    let geminiVoiceModel = ModelCatalog(cacheKey: "gemini_voice_model") {
        guard let key = Keychain.get(.gemini) else { throw AVGError.missingKey(APIKey.gemini.label) }
        return try await ModelCatalogSources.geminiVoiceModels(key: key)
    }

    var all: [ModelCatalog] {
        [geminiText, groqText, stableHorde, elevenVoices, elevenModels,
         pollinationsText, pollinationsImage, geminiImage, geminiVoiceModel]
    }

    /// The list matching the provider currently in use for each AI (nil when
    /// that provider has no model list, e.g. on-device).
    func writerCatalog(_ s: AppSettings) -> ModelCatalog? {
        switch s.text {
        case .gemini: return geminiText
        case .groq: return groqText
        case .pollinations: return pollinationsText
        case .apple: return nil
        }
    }

    func imageCatalog(_ s: AppSettings) -> ModelCatalog? {
        switch s.image {
        case .gemini: return geminiImage
        case .pollinations: return pollinationsImage
        case .stablehorde: return stableHorde
        default: return nil
        }
    }

    func voiceFallbackCatalog(_ s: AppSettings) -> ModelCatalog? {
        switch s.voice {
        case .elevenlabs: return elevenModels
        case .gemini: return geminiVoiceModel
        case .apple: return nil
        }
    }

    /// Refreshes only the lists for the providers currently selected (cheap,
    /// and exactly what the user is about to look at).
    func refreshCurrent(_ s: AppSettings) async {
        var lists: [ModelCatalog] = []
        if let c = writerCatalog(s) { lists.append(c) }
        if let c = imageCatalog(s) { lists.append(c) }
        switch s.voice {
        case .elevenlabs: lists += [elevenVoices, elevenModels]
        case .gemini: lists.append(geminiVoiceModel)
        default: break
        }
        for c in lists { await c.refresh() }
    }

    /// Sequential, not a TaskGroup: `ModelCatalog` is main-actor-isolated and
    /// not `Sendable`, so a plain chain of awaits keeps everything on one
    /// actor. A few extra seconds for an occasional manual refresh is the
    /// right trade against fighting the concurrency checker.
    func refreshAll() async {
        for c in all { await c.refresh() }
    }
}

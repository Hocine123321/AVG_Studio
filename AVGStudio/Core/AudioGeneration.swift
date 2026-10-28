import Foundation

// The glue between the paid audio providers and the shelf: ask once, keep the
// file, reuse it for every video after. Nothing here runs unless the person
// presses a button that states the price first.

@MainActor
enum AudioGeneration {
    enum Length: String, CaseIterable, Identifiable {
        case clip, full
        var id: String { rawValue }

        var model: String { self == .clip ? GeminiMusic.clipModel : GeminiMusic.fullModel }
        var title: String { self == .clip ? "30-second clip" : "Full song" }
        /// Per Google's published price list (October 2026).
        var price: String { self == .clip ? "about $0.04" : "about $0.08" }
    }

    /// One background track in `mood`, saved to the library as "generated".
    static func generateMusic(store: Store, mood: String, detail: String, length: Length) async throws -> AudioItem {
        guard let key = Keychain.get(.gemini) else {
            throw AVGError.message("Add a Google (Gemini) key first. Music generation needs a paid Google plan; it has no free tier.")
        }
        let data = try await GeminiMusic(key: key).generate(prompt: MusicPrompt.background(mood: mood, detail: detail),
                                                            model: length.model)
        return try await store.audioLibrary.importData(
            data, fileExtension: "mp3", kind: .music, name: "\(mood.capitalized) bed", origin: .generated,
            license: "Generated with Google Lyria", tags: [mood])
    }

    /// The five starter sounds, skipping any moment that already has one.
    static func generateStarterSounds(store: Store) async throws -> [AudioItem] {
        guard let key = Keychain.get(.elevenlabs) else {
            throw AVGError.message("Add an ElevenLabs key first to generate sound effects.")
        }
        let have = Set(store.audioLibrary.effects.filter { $0.tags.contains(StarterSounds.tag) }
            .flatMap { $0.tags })
        var made: [AudioItem] = []
        for item in StarterSounds.items where !have.contains(item.moment.rawValue) {
            let data = try await ElevenLabsEffects(key: key).generate(prompt: item.prompt, seconds: item.seconds)
            made.append(try await store.audioLibrary.importData(
                data, fileExtension: "mp3", kind: .sfx, name: item.name, origin: .generated,
                license: "Generated with ElevenLabs", tags: [StarterSounds.tag, item.moment.rawValue]))
        }
        return made
    }
}

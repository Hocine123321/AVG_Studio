import Foundation

/// A saved look-and-sound: the parts of a channel worth reusing (its style,
/// music, sound effects, editing habits and length), captured by name and
/// applied to any channel later. A recipe NEVER carries a channel's name, niche,
/// language, providers or keys, so applying one cannot change who or what a
/// channel is, only how its videos look, sound and get edited.
struct Recipe: Codable, Hashable, Identifiable {
    var id = UUID()
    var name: String
    var created = Date()

    // Style
    var tone: String
    var contentShape: String
    var visualTone: String
    var styleLock: String
    var brandPreset: String
    // Sound
    var musicAuto: Bool
    var musicTrackID: String?
    var musicGainDB: Double
    var sfxEnabled: Bool
    var sfxMap: [String: String]
    var sfxGainDB: Double
    // Editing and shape
    var editingMode: EditingMode
    var editLevel: EditLevel
    var reviewScript: Bool
    var reviewStoryboard: Bool
    var targetSeconds: Int
    var aspectRatio: AspectRatio

    init(name: String, capturing ch: Channel) {
        self.name = name
        tone = ch.tone
        contentShape = ch.contentShape
        visualTone = ch.visualTone
        styleLock = ch.styleLock
        brandPreset = ch.brandPreset
        musicAuto = ch.musicAuto
        musicTrackID = ch.musicTrackID
        musicGainDB = ch.musicGainDB
        sfxEnabled = ch.sfxEnabled
        sfxMap = ch.sfxMap
        sfxGainDB = ch.sfxGainDB
        editingMode = ch.editingMode
        editLevel = ch.editLevel
        reviewScript = ch.reviewScript
        reviewStoryboard = ch.reviewStoryboard
        targetSeconds = ch.targetSeconds
        aspectRatio = ch.aspectRatio
    }

    /// Each part decodes on its own, so a recipe saved by an older or newer
    /// version still loads, with the missing parts at a channel's defaults.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: AnyKey.self)
        let d = Recipe(name: "Recipe", capturing: Channel(name: ""))
        id = c.value("id", d.id)
        name = c.value("name", d.name)
        created = c.value("created", d.created)
        tone = c.value("tone", d.tone)
        contentShape = c.value("contentShape", d.contentShape)
        visualTone = c.value("visualTone", d.visualTone)
        styleLock = c.value("styleLock", d.styleLock)
        brandPreset = c.value("brandPreset", d.brandPreset)
        musicAuto = c.value("musicAuto", d.musicAuto)
        musicTrackID = c.value("musicTrackID", d.musicTrackID)
        musicGainDB = c.value("musicGainDB", d.musicGainDB)
        sfxEnabled = c.value("sfxEnabled", d.sfxEnabled)
        sfxMap = c.value("sfxMap", d.sfxMap)
        sfxGainDB = c.value("sfxGainDB", d.sfxGainDB)
        editingMode = c.value("editingMode", d.editingMode)
        editLevel = c.value("editLevel", d.editLevel)
        reviewScript = c.value("reviewScript", d.reviewScript)
        reviewStoryboard = c.value("reviewStoryboard", d.reviewStoryboard)
        targetSeconds = c.value("targetSeconds", d.targetSeconds)
        aspectRatio = c.value("aspectRatio", d.aspectRatio)
    }

    /// Copies the recipe onto a channel. `knownAudio` is the set of library
    /// items that still exist: a track or sound the recipe points at that has
    /// been deleted is dropped rather than left dangling.
    func apply(to ch: inout Channel, knownAudio: Set<String>) {
        ch.tone = tone
        ch.contentShape = contentShape
        ch.visualTone = visualTone
        ch.styleLock = styleLock
        ch.brandPreset = brandPreset
        ch.musicAuto = musicAuto
        ch.musicTrackID = musicTrackID.flatMap { knownAudio.contains($0) ? $0 : nil }
        ch.musicGainDB = musicGainDB
        ch.sfxEnabled = sfxEnabled
        ch.sfxMap = sfxMap.filter { knownAudio.contains($0.value) }
        ch.sfxGainDB = sfxGainDB
        ch.editingMode = editingMode
        ch.editLevel = editLevel
        ch.reviewScript = reviewScript
        ch.reviewStoryboard = reviewStoryboard
        ch.targetSeconds = targetSeconds
        ch.aspectRatio = aspectRatio
    }

    /// One line for a list: "documentary cinematic · auto music · sound effects · edit: ask".
    var summary: String {
        var parts = [visualTone.replacingOccurrences(of: "_", with: " ")]
        parts.append(musicAuto ? "auto music" : (musicTrackID != nil ? "chosen music" : "no music"))
        parts.append(sfxEnabled ? "sound effects" : "no effects")
        parts.append("edit: \(editingMode.title.lowercased())")
        return parts.joined(separator: " · ")
    }
}

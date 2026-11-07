import Foundation

/// What the editing assistant (the chat inside the editor) may change, one
/// switch each (Settings > Assistant > Editing assistant). Like the chat
/// assistant's capabilities these are TOOLS: a switch that is off means the
/// tool is not offered to the model at all, and a call made anyway is refused.
/// Reading the edit and undoing a change are always available.
enum EditCapability: String, CaseIterable, Identifiable {
    case captions, titleCards, music, soundEffects, cuts

    var id: String { rawValue }

    var title: String {
        switch self {
        case .captions: return "Change captions"
        case .titleCards: return "Change title cards"
        case .music: return "Change music"
        case .soundEffects: return "Change sound effects"
        case .cuts: return "Change cuts and punch-ins"
        }
    }

    /// The thing itself, for sentences ("captions is switched off" reads wrong, so this is plural-safe).
    var noun: String {
        switch self {
        case .captions: return "captions"
        case .titleCards: return "title cards"
        case .music: return "music"
        case .soundEffects: return "sound effects"
        case .cuts: return "cuts and punch-ins"
        }
    }

    var summary: String {
        switch self {
        case .captions: return "Fix a word or retype a caption line."
        case .titleCards: return "Add or remove full-screen text cards."
        case .music: return "Pick this video's music and how loud it is."
        case .soundEffects: return "Turn effects on or off and set their level."
        case .cuts: return "Set the shot cuts and punch-ins for this video."
        }
    }

    /// The tools this switch unlocks.
    var tools: [String] {
        switch self {
        case .captions: return ["fix_caption_word", "set_caption_line"]
        case .titleCards: return ["add_title_card", "remove_title_card"]
        case .music: return ["set_music"]
        case .soundEffects: return ["set_sound_effects"]
        case .cuts: return ["set_cuts"]
        }
    }

    var keyPath: WritableKeyPath<AppSettings, Bool> {
        switch self {
        case .captions: return \.editAllowCaptions
        case .titleCards: return \.editAllowCards
        case .music: return \.editAllowMusic
        case .soundEffects: return \.editAllowEffects
        case .cuts: return \.editAllowCuts
        }
    }

    static func enabledCount(_ settings: AppSettings) -> Int {
        allCases.filter { settings[keyPath: $0.keyPath] }.count
    }

    /// The switch that governs `tool`, or nil for the always-available ones.
    static func governing(tool: String) -> EditCapability? {
        allCases.first { $0.tools.contains(tool) }
    }
}

extension EditTools {
    /// The tools offered under these settings: every one whose switch is on,
    /// plus reading the edit and undo.
    static func offeredSpecs(for settings: AppSettings) -> [AgentToolSpec] {
        specs.filter { spec in
            guard let capability = EditCapability.governing(tool: spec.name) else { return true }
            return settings[keyPath: capability.keyPath]
        }
    }

    /// The prompt, plus what is switched off and the person's standing instructions.
    static func prompt(for settings: AppSettings) -> String {
        var text = systemPrompt
        let off = EditCapability.allCases.filter { !settings[keyPath: $0.keyPath] }
        if !off.isEmpty {
            text += "\n\nSwitched off in Settings, so you cannot change: "
                + off.map { $0.noun }.joined(separator: ", ")
                + ". If asked, say they are switched off."
        }
        let extra = String(settings.editAssistantInstructions.trimmed.prefix(AgentSettingsRules.instructionsLimit))
        if !extra.isEmpty {
            text += "\n\nThe person's standing instructions for editing (they never allow more than your tools): " + extra
        }
        return text
    }

    /// The settings the editing assistant's provider is built from: the chat
    /// assistant's, unless the editor has its own model.
    static func providerSettings(from settings: AppSettings) -> AppSettings {
        guard settings.editAssistantOwnModel else { return settings }
        var own = settings
        own.agentProvider = settings.editAssistantProvider
        own.agentGroqModel = settings.editAssistantGroqModel
        own.agentGeminiModel = settings.editAssistantGeminiModel
        return own
    }

    /// The refusal for a call to a tool whose switch is off.
    static func switchedOff(_ capability: EditCapability) -> String {
        "Changing \(capability.noun) is switched off in Settings > Assistant, so I can't do that."
    }
}

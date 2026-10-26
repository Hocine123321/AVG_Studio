import Foundation

// "Your AI team": three ready-made ways to staff the writer, the image maker
// and the narrator, chosen once on the Welcome screen instead of digging
// through each category in Settings.
//
// The presets are about WHAT the person has to set up, not about model names:
// names go stale (ModelRanking / ModelAutoSetup pick the actual model once a
// key is saved). A preset that wants a key the person has not added does not
// half-apply - it falls back to the free option for that role and reports what
// is missing, so the summary never promises a writer that cannot run.
//
// Pure on purpose (no store, no Keychain, no device checks passed in), so the
// table below is pinned by tests.

enum AITeamPreset: String, CaseIterable, Identifiable {
    case free, balanced, premium

    var id: String { rawValue }

    var title: String {
        switch self {
        case .free: return "Free"
        case .balanced: return "Balanced"
        case .premium: return "Premium"
        }
    }

    var blurb: String {
        switch self {
        case .free:
            return "No keys. The iPhone writes, free online images, the iPhone voice."
        case .balanced:
            return "One free Google key. Gemini writes the script (free tier, rate-limited); images and voice stay free."
        case .premium:
            return "Google for writing and images, and ElevenLabs for the voice if you add its key. Google images and voice may need billing turned on."
        }
    }
}

struct TeamAssignment: Equatable {
    var text: TextProviderKind
    var image: ImageProviderKind
    var voice: VoiceProviderKind
    /// Keys the preset wants that are not saved yet (empty = fully staffed).
    var needs: [APIKey]
}

extension AITeamPreset {
    /// Who staffs each role for `preset`, given the keys saved right now.
    /// `appleAvailable` is whether the on-device writer exists on this iPhone;
    /// without it the free writer is Pollinations.
    static func assignment(_ preset: AITeamPreset, keys: Set<APIKey>, appleAvailable: Bool) -> TeamAssignment {
        let freeWriter: TextProviderKind = appleAvailable ? .apple : .pollinations
        let hasGoogle = keys.contains(.gemini)
        let hasEleven = keys.contains(.elevenlabs)
        switch preset {
        case .free:
            return TeamAssignment(text: freeWriter, image: .pollinations, voice: .apple, needs: [])
        case .balanced:
            return TeamAssignment(text: hasGoogle ? .gemini : freeWriter, image: .pollinations, voice: .apple,
                                  needs: hasGoogle ? [] : [.gemini])
        case .premium:
            let voice: VoiceProviderKind = hasEleven ? .elevenlabs : (hasGoogle ? .gemini : .apple)
            return TeamAssignment(text: hasGoogle ? .gemini : freeWriter,
                                  image: hasGoogle ? .gemini : .pollinations,
                                  voice: voice,
                                  needs: hasGoogle ? [] : [.gemini])
        }
    }
}

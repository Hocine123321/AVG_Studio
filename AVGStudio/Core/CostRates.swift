import Foundation

// One flat rate table, shared by `Pipeline` (the cost lines recorded on a
// project) and the estimate the assistant shows before it starts anything.
// Two copies would drift: the approval card would quote a price the project
// then never matches. These are clearly-labelled guesses (see `CostEntry`),
// not billing. Every `switch` is exhaustive with no `default:`, so a new
// provider kind cannot silently price as free - the compiler forces a rate.

enum CostRates {
    /// US dollars per character of text sent plus received.
    static func text(_ kind: TextProviderKind) -> Double {
        switch kind {
        case .apple, .pollinations: return 0
        case .gemini: return 0.00000025   // ~$0.25 / 1M chars, ballpark for a flash-tier model
        case .groq: return 0.00000015
        }
    }

    /// US dollars per generated image.
    static func image(_ kind: ImageProviderKind) -> Double {
        switch kind {
        case .pollinations, .stablehorde: return 0
        case .gemini: return 0.02
        case .openai: return 0.04
        }
    }

    /// US dollars per narrated character.
    static func voice(_ kind: VoiceProviderKind) -> Double {
        switch kind {
        case .apple: return 0
        case .elevenlabs: return 0.00003
        // Ballpark for a preview-tier Gemini TTS model.
        case .gemini: return 0.00002
        }
    }

    /// US dollars per minute of audio sent for word-level caption timing.
    static let transcriptionPerMinute = 0.01
}

/// What one video is guessed to cost, split by the phase that spends it, so
/// "redo from Voice" is priced from Voice onward and not as a whole new video.
struct CostBreakdown: Equatable {
    var text = 0.0            // spent in Script
    var images = 0.0          // spent in Visuals
    var voice = 0.0           // spent in Voice
    var transcription = 0.0   // spent in Captions (only with an AssemblyAI key)

    /// Everything that would be spent running `phase` and every phase after it.
    func total(from phase: PhaseID) -> Double {
        var sum = 0.0
        if phase.order <= PhaseID.writing.order { sum += text }
        if phase.order <= PhaseID.visuals.order { sum += images }
        if phase.order <= PhaseID.voice.order { sum += voice }
        if phase.order <= PhaseID.captions.order { sum += transcription }
        return sum
    }

    /// A flat guess from the channel's target length: about 15 narrated
    /// characters per second; the prompts and replies of the writing phase
    /// total about three times the narration; one image per ~8 seconds,
    /// clamped to 5...12 scenes; captions are transcribed only when an
    /// AssemblyAI key exists.
    static func forVideo(settings: AppSettings, channel: Channel, hasAssemblyKey: Bool) -> CostBreakdown {
        let seconds = max(channel.targetSeconds, 1)
        let narrationChars = Double(seconds * 15)
        let scenes = Double(min(max(seconds / 8, 5), 12))
        return CostBreakdown(
            text: narrationChars * 3 * CostRates.text(settings.text),
            images: scenes * CostRates.image(settings.image),
            voice: narrationChars * CostRates.voice(settings.voice),
            transcription: hasAssemblyKey ? Double(seconds) / 60.0 * CostRates.transcriptionPerMinute : 0)
    }
}

struct CostEstimate: Equatable {
    var usd: Double
    /// Only an exactly-zero estimate is free. A tiny non-zero one is paid.
    var isFree: Bool { usd == 0 }

    var summary: String {
        if usd == 0 { return "free" }
        if usd < 0.005 { return "< $0.01" }
        return String(format: "≈ $%.2f", usd)
    }

    static func forNewVideo(settings: AppSettings, channel: Channel, hasAssemblyKey: Bool) -> CostEstimate {
        forRedo(from: .writing, settings: settings, channel: channel, hasAssemblyKey: hasAssemblyKey)
    }

    static func forRedo(from phase: PhaseID, settings: AppSettings, channel: Channel, hasAssemblyKey: Bool) -> CostEstimate {
        CostEstimate(usd: CostBreakdown.forVideo(settings: settings, channel: channel,
                                                 hasAssemblyKey: hasAssemblyKey).total(from: phase))
    }
}

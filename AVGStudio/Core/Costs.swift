import Foundation

/// The one table of what providers are estimated to cost - used both by the
/// pipeline (posting a cost as each call happens) and by the Create screen
/// (estimating a whole video BEFORE it runs), so the two can never disagree.
///
/// The PC keeps the same thing as `avg/core/costs.py` and is explicit about
/// what it is: maintained ESTIMATES, not metered spend. Nothing here inspects
/// a provider response for a price; every rate is a figure a person wrote
/// down and has to keep current. Show it as "about", never as an invoice.
enum CostTable {
    /// USD per character of prompt + reply.
    static func textRate(_ kind: TextProviderKind) -> Double {
        switch kind {
        case .apple, .pollinations: return 0
        case .gemini: return 0.00000025 // ~$0.25 / 1M chars, ballpark for a flash-tier model
        case .groq: return 0.00000015
        }
    }

    /// USD per generated image.
    static func imageFlat(_ kind: ImageProviderKind, quality: ImageQuality = .medium) -> Double {
        switch kind {
        case .pollinations, .stablehorde: return 0
        case .gemini: return 0.02
        case .openai:
            // OpenAI prices by quality tier; medium is the figure this table has always used.
            switch quality {
            case .low: return 0.015
            case .medium: return 0.04
            case .high: return 0.16
            }
        }
    }

    /// USD per spoken character.
    static func voiceRate(_ kind: VoiceProviderKind) -> Double {
        switch kind {
        case .apple: return 0
        case .elevenlabs: return 0.00003
        case .gemini: return 0.00002 // preview-tier TTS ballpark
        }
    }

    /// USD per minute of audio transcribed by AssemblyAI.
    static let transcriptionPerMinute = 0.01

    // MARK: Whole-video estimate

    struct Line: Identifiable, Equatable {
        let phase: String
        let usd: Double
        var id: String { phase }
    }

    struct Estimate: Equatable {
        var lines: [Line]
        var total: Double { lines.reduce(0) { $0 + $1.usd } }
        /// "Free" for $0, otherwise "about $0.18" - never a false-precision figure.
        var label: String { Self.format(total) }

        static func format(_ usd: Double) -> String {
            usd <= 0 ? "Free" : (usd < 0.01 ? "under $0.01" : "about $" + String(format: "%.2f", usd))
        }
    }

    /// Spoken characters per second of video (150 wpm at ~6 chars per word).
    private static let spokenCharsPerSecond = 15.0

    /// What one video is estimated to cost with these settings. A pure
    /// function of its inputs, so it is testable and cheap to recompute as
    /// the Create screen's pickers change.
    ///
    /// Text is modelled as the narration passing through five calls (brief,
    /// narration, scene split, story map, motion plan), each carrying roughly
    /// the script in its prompt AND its reply, plus a fixed allowance for
    /// instructions.
    static func estimate(targetSeconds: Int, settings s: AppSettings, channel: Channel,
                         hasTranscriptionKey: Bool, poolSize: Int) -> Estimate {
        let seconds = Double(max(10, targetSeconds))
        let scriptChars = seconds * spokenCharsPerSecond

        let textChars = scriptChars * 5 * 2 + 5_500
        let text = textChars * textRate(s.text)

        // About one scene per 7 seconds, between 5 and 12 (what the scene
        // splitter asks for). Recycled pool images cost nothing.
        let scenes = Double(min(12, max(5, Int((seconds / 7).rounded()))))
        let recycled = (channel.recycleEnabled && poolSize > 0) ? scenes * min(100, max(0, channel.recycleRatio)) / 100 : 0
        let images = max(0, scenes - recycled) * imageFlat(s.image, quality: ImageQuality.forVideo(shortEdge: s.shortEdge))

        let voice = scriptChars * voiceRate(s.voice)
        let captions = hasTranscriptionKey ? seconds / 60 * transcriptionPerMinute : 0

        return Estimate(lines: [
            Line(phase: "Writing", usd: text),
            Line(phase: "Images", usd: images),
            Line(phase: "Voice", usd: voice),
            Line(phase: "Captions", usd: captions),
        ])
    }
}

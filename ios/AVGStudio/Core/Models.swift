import Foundation

enum PhaseStatus: String, Codable { case pending, running, done, failed }

/// Mirrors the PC pipeline: writing (brief + script + scenes), visuals, voice, captions, render.
enum PhaseID: String, Codable, CaseIterable, Identifiable {
    case writing, visuals, voice, captions, render
    var id: String { rawValue }
    var title: String {
        switch self {
        case .writing: return "Script"
        case .visuals: return "Visuals"
        case .voice: return "Voice"
        case .captions: return "Captions"
        case .render: return "Render"
        }
    }
    var symbol: String {
        switch self {
        case .writing: return "text.quote"
        case .visuals: return "photo.stack"
        case .voice: return "waveform"
        case .captions: return "captions.bubble"
        case .render: return "film.stack"
        }
    }
    var order: Int { PhaseID.allCases.firstIndex(of: self) ?? 0 }
}

struct PhaseRecord: Codable, Hashable {
    var status: PhaseStatus = .pending
    var started: Date?
    var finished: Date?
    var error: String?
}

struct VideoScene: Codable, Hashable, Identifiable {
    var index: Int
    var text: String
    var imagePrompt: String
    var imageFile: String?
    var id: Int { index }
}

/// One word's estimated (or, with AssemblyAI, real) spoken timing, in seconds
/// from the start of the narration audio. Mirrors the PC's per-word
/// `{text, start, end}` shape (`avg.pipeline.phases.captions._synthetic_word_timings`
/// / AssemblyAI's own word list) - needed so caption animation styles that sync to
/// individual words (highlight, typewriter) have something to sync to.
struct WordTiming: Codable, Hashable {
    var text: String
    var start: Double
    var end: Double
}

struct CaptionCue: Codable, Hashable {
    var start: Double
    var end: Double
    var text: String
    var words: [WordTiming] = []
}

struct Take: Codable, Hashable, Identifiable {
    var id: Int
    var label: String
    var created: Date
    var file: String
}

enum AspectRatio: String, Codable, CaseIterable, Identifiable {
    case portrait = "9:16", landscape = "16:9", square = "1:1"
    var id: String { rawValue }
    func size(shortEdge: Int) -> CGSize {
        let s = CGFloat(shortEdge)
        switch self {
        case .portrait: return CGSize(width: s, height: (s * 16 / 9).rounded(.down) - (s * 16 / 9).rounded(.down).truncatingRemainder(dividingBy: 2))
        case .landscape: return CGSize(width: (s * 16 / 9).rounded(.down) - (s * 16 / 9).rounded(.down).truncatingRemainder(dividingBy: 2), height: s)
        case .square: return CGSize(width: s, height: s)
        }
    }
}

/// A rough, clearly-labelled cost estimate for one provider call. Mirrors the
/// PC's `avg/core/costs.py` intent (surface what a render is costing) at a
/// much simpler level: a flat per-call guess, not real token/second billing -
/// good enough to notice "ElevenLabs is the expensive part", not to reconcile
/// against an invoice.
struct CostEntry: Codable, Hashable, Identifiable {
    var id = UUID()
    var label: String
    var amountUSD: Double
}

struct Project: Codable, Hashable, Identifiable {
    var id: String
    var title: String
    var topic: String
    var channelID: UUID?
    var aspectRatio: AspectRatio = .portrait
    var created: Date = Date()
    var phases: [String: PhaseRecord] = [:]
    var brief: String?
    var narration: String?
    var scenes: [VideoScene] = []
    var audioFile: String?
    var sceneDurations: [Double] = []
    var captions: [CaptionCue] = []
    var captionsSource: String = "estimated"
    var output: String?
    var takes: [Take] = []
    var archived: Bool = false
    var costs: [CostEntry] = []

    func phase(_ p: PhaseID) -> PhaseRecord { phases[p.rawValue] ?? PhaseRecord() }

    var overallStatus: PhaseStatus {
        let s = PhaseID.allCases.map { phase($0).status }
        if s.contains(.running) { return .running }
        if s.contains(.failed) { return .failed }
        if s.allSatisfy({ $0 == .done }) { return .done }
        return .pending
    }

    var estimatedCostUSD: Double { costs.reduce(0) { $0 + $1.amountUSD } }
}

struct Channel: Codable, Hashable, Identifiable {
    var id = UUID()
    var name: String
    var niche: String = ""
    var tone: String = "engaging, clear, curious"
    var language: String = "English"
    var targetSeconds: Int = 60
    var aspectRatio: AspectRatio = .portrait
    /// Brand accent colour, hex "RRGGBB" or empty for "use the app default".
    /// Applied to the highlight/typewriter caption accent and takeover-card
    /// tint - a small, iPhone-sized analog of the PC's `resolve_brand` palette.
    var accentColorHex: String = ""
}

enum TextProviderKind: String, Codable, CaseIterable, Identifiable {
    case apple, gemini, groq, pollinations
    var id: String { rawValue }
    var label: String {
        switch self {
        case .apple: return "Apple Intelligence (on-device)"
        case .gemini: return "Google Gemini"
        case .groq: return "Groq"
        case .pollinations: return "Pollinations (free)"
        }
    }
}

enum ImageProviderKind: String, Codable, CaseIterable, Identifiable {
    case pollinations, gemini, openai
    var id: String { rawValue }
    var label: String {
        switch self {
        case .pollinations: return "Pollinations (free)"
        case .gemini: return "Google Gemini"
        case .openai: return "OpenAI"
        }
    }
}

enum VoiceProviderKind: String, Codable, CaseIterable, Identifiable {
    case apple, elevenlabs
    var id: String { rawValue }
    var label: String {
        switch self {
        case .apple: return "Apple voices (on-device)"
        case .elevenlabs: return "ElevenLabs"
        }
    }
}

enum CaptionPosition: String, Codable, CaseIterable, Identifiable {
    case top, center, bottom
    var id: String { rawValue }
}

/// The six caption entrance/emphasis mechanisms, ported from the PC's
/// `avg.pipeline.phases.captions._animated_text` ("Six genuinely different
/// mechanisms... not reskins of one another"). `highlight`/`typewriter` need
/// per-word timing (`CaptionCue.words`); the others only need the cue's own
/// start/end.
enum CaptionAnimationStyle: String, Codable, CaseIterable, Identifiable {
    case none, pop, fade, rise, highlight, typewriter
    var id: String { rawValue }
    var label: String {
        switch self {
        case .none: return "None"
        case .pop: return "Pop"
        case .fade: return "Fade"
        case .rise: return "Rise"
        case .highlight: return "Highlight (karaoke fill)"
        case .typewriter: return "Typewriter (word-synced)"
        }
    }
}

/// Whole-video look filter. A procedural stand-in for the PC's vendored
/// `film_grain.mov`/`light_leak.mov` alpha overlays (`avg/assets/Filters/`,
/// composited by `compose_filter_overlays`) - same idea (a looping effect
/// layer alpha-blended over the finished frames at `intensity/100` opacity),
/// generated on-device instead of shipping the source footage. The PC picks
/// a filter per scene via the AI director; this picks one for the whole
/// video - a deliberately smaller version of that feature, not the same one.
enum FilterStyle: String, Codable, CaseIterable, Identifiable {
    case none, filmGrain, lightLeak
    var id: String { rawValue }
    var label: String {
        switch self {
        case .none: return "None"
        case .filmGrain: return "Film grain"
        case .lightLeak: return "Light leak"
        }
    }
}

struct AppSettings: Codable {
    var onboarded = false
    var text: TextProviderKind = .apple
    var image: ImageProviderKind = .pollinations
    var voice: VoiceProviderKind = .apple
    var geminiModel = "gemini-2.5-flash"
    var groqModel = "llama-3.3-70b-versatile"
    var elevenVoiceID = "21m00Tcm4TlvDq8ikWAM"
    // Matches the PC's own legacy default (avg/pipeline/phases/voice.py's
    // _DEFAULT_MODEL["elevenlabs"]) rather than a higher-cost model picked
    // independently on iOS.
    var elevenModel = "eleven_flash_v2_5"
    var appleVoiceIdentifier = ""
    var imageStyle = "cinematic, highly detailed, dramatic lighting"
    var fps = 30
    var shortEdge = 1080
    var kenBurns = true
    var kbIntensity: Double = 1.0
    var captionsEnabled = true
    var captionMaxWords = 4
    var captionPosition: CaptionPosition = .center
    var captionFontSize: Double = 72
    // Matches the PC's own default fallback ("fade") in _animated_text.
    var captionAnimationStyle: CaptionAnimationStyle = .fade
    // Dynamic [pause] handling: PC's named invariant is "[pause] becomes
    // real silence, never spoken text" (avg/pipeline/phases/voice.py). This
    // toggle exists so a user can turn deliberate pauses off; the "never
    // spoken text" half of that invariant is NOT optional and always holds.
    var dynamicPausesEnabled = true
    var pauseMs: Double = 700 // matches the PC's _DEFAULT_PAUSE_MS
    var filterStyle: FilterStyle = .none
    var filterIntensity: Double = 50
}

enum AVGError: LocalizedError {
    case missingKey(String)
    case http(Int, String)
    case message(String)

    var errorDescription: String? {
        switch self {
        case .missingKey(let k): return "Add your \(k) key in Settings first."
        case .http(let code, let body): return "Service error \(code): \(body)"
        case .message(let m): return m
        }
    }

    /// Whether retrying the same call has a real chance of succeeding:
    /// rate limits and server-side errors are transient; a bad request, an
    /// auth failure, or "the service replied but had nothing useful" are
    /// not going to change on attempt two. Fixes the old retry policy,
    /// which retried EVERY non-http error (including deterministic
    /// content-filter/parse failures) purely because it wasn't `.http`.
    var isTransient: Bool {
        switch self {
        case .http(let code, _): return code == 429 || code >= 500
        case .missingKey: return false
        case .message: return false
        }
    }
}

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

struct CaptionCue: Codable, Hashable {
    var start: Double
    var end: Double
    var text: String
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
    var output: String?
    var takes: [Take] = []
    var archived: Bool = false

    func phase(_ p: PhaseID) -> PhaseRecord { phases[p.rawValue] ?? PhaseRecord() }

    var overallStatus: PhaseStatus {
        let s = PhaseID.allCases.map { phase($0).status }
        if s.contains(.running) { return .running }
        if s.contains(.failed) { return .failed }
        if s.allSatisfy({ $0 == .done }) { return .done }
        return .pending
    }
}

struct Channel: Codable, Hashable, Identifiable {
    var id = UUID()
    var name: String
    var niche: String = ""
    var tone: String = "engaging, clear, curious"
    var language: String = "English"
    var targetSeconds: Int = 60
    var aspectRatio: AspectRatio = .portrait
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

struct AppSettings: Codable {
    var onboarded = false
    var text: TextProviderKind = .apple
    var image: ImageProviderKind = .pollinations
    var voice: VoiceProviderKind = .apple
    var geminiModel = "gemini-2.5-flash"
    var groqModel = "llama-3.3-70b-versatile"
    var elevenVoiceID = "21m00Tcm4TlvDq8ikWAM"
    var elevenModel = "eleven_multilingual_v2"
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
}

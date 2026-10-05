import Foundation

// New user-facing options and the small value types behind them.
//
//  * Performance: the PC exposes `render_engine`, `render_codec` and
//    `render_threads` (ffmpeg knobs). A phone has different levers - the
//    encoder, the GPU, heat, the battery, and whether the app is allowed to
//    keep working when you look away - so the iOS options are those.
//  * Overlay look: how captions and takeover/chapter cards are drawn.
//  * Image quality: derived from the video quality, never asked for.

// MARK: - Performance

enum RenderEngine: String, Codable, CaseIterable, Identifiable {
    /// The GPU draws every frame whenever iOS allows it, and the CPU draws any
    /// frame it doesn't (the app in the background, a GPU error).
    case auto
    /// Always draw on the CPU: slower, but the one path that has no GPU
    /// dependence at all. For comparing speed or working around a device quirk.
    case cpu
    var id: String { rawValue }
    var label: String { self == .auto ? "Automatic (GPU)" : "CPU only" }
}

enum VideoCodec: String, Codable, CaseIterable, Identifiable {
    case hevc, h264
    var id: String { rawValue }
    var label: String { self == .hevc ? "HEVC (smaller files)" : "H.264 (most compatible)" }
}

enum EncoderQuality: String, Codable, CaseIterable, Identifiable {
    /// Let the system pick (what the app always did before this option).
    case automatic, compact, balanced, high
    var id: String { rawValue }
    var label: String {
        switch self {
        case .automatic: return "Automatic"
        case .compact: return "Compact"
        case .balanced: return "Balanced"
        case .high: return "High"
        }
    }

    /// Average bits per pixel per frame for `codec`, or nil for "automatic".
    /// H.264 needs roughly 1.6x HEVC's rate for the same look.
    func bitsPerPixel(codec: VideoCodec) -> Double? {
        let hevc: Double
        switch self {
        case .automatic: return nil
        case .compact: hevc = 0.05
        case .balanced: hevc = 0.09
        case .high: hevc = 0.16
        }
        return codec == .hevc ? hevc : hevc * 1.6
    }

    /// The average bitrate to ask the encoder for, or nil to leave it to the system.
    func bitrate(width: Int, height: Int, fps: Int, codec: VideoCodec) -> Int? {
        bitsPerPixel(codec: codec).map { Int(Double(width * height * max(1, fps)) * $0) }
    }
}

// MARK: - Image quality (from the video quality)

/// How hard to ask an image maker to try. Chosen from the video's quality so a
/// 720p video does not pay for pictures it cannot show, and a 1080p+ one does
/// not get soft ones.
enum ImageQuality: String, Codable {
    case low, medium, high

    static func forVideo(shortEdge: Int) -> ImageQuality {
        switch shortEdge {
        case ..<600: return .low
        case ..<900: return .medium
        default: return .high
        }
    }

    /// OpenAI's `quality` parameter for gpt-image-1.
    var openAI: String { rawValue }

    /// Multiplier on a provider's own base picture size (medium = the size
    /// the PC uses). Applied then rounded to a multiple of 64.
    var sizeScale: Double {
        switch self {
        case .low: return 0.75
        case .medium: return 1.0
        case .high: return 1.33
        }
    }

    /// `base` scaled by `sizeScale`, each side a multiple of 64 (what the
    /// diffusion models accept) and never smaller than 256.
    func scaled(_ base: (Int, Int), cap: Int? = nil) -> (Int, Int) {
        func fit(_ v: Int) -> Int {
            var out = max(256, Int((Double(v) * sizeScale / 64).rounded()) * 64)
            if let cap { out = min(out, cap) }
            return out
        }
        return (fit(base.0), fit(base.1))
    }
}

// MARK: - Overlay look

enum CaptionBackground: String, Codable, CaseIterable, Identifiable {
    case none, pill, box
    var id: String { rawValue }
    var label: String { self == .none ? "None" : (self == .pill ? "Pill" : "Box") }
}

enum CardBackground: String, Codable, CaseIterable, Identifiable {
    /// The PC's default card colour (#0B0F1A).
    case dark
    /// The channel's first accent colour as a deep tint.
    case brand
    case light
    var id: String { rawValue }
    var label: String { self == .dark ? "Dark" : (self == .brand ? "Brand colour" : "Light") }
}

enum CardAlignment: String, Codable, CaseIterable, Identifiable {
    case center, left
    var id: String { rawValue }
    var label: String { self == .center ? "Centred" : "Left" }
}

enum CardAnimation: String, Codable, CaseIterable, Identifiable {
    case none, fade, pop
    var id: String { rawValue }
    var label: String { self == .none ? "None" : (self == .fade ? "Fade in" : "Pop in") }
}

/// How the on-screen text is drawn. A channel setting (the PC's overlay
/// settings are channel-scoped too). Every default reproduces the look the app
/// had before these options existed, so existing channels do not change.
struct OverlayStyle: Codable, Hashable {
    // Captions
    var captionBackground: CaptionBackground = .none
    /// Outline thickness (0 = none). The old fixed value was 5.
    var captionOutline: Double = 5
    var captionShadow = true
    /// Captions were always uppercase.
    var captionUppercase = true
    var captionTextColorHex = "FFFFFF"
    /// Backing-plate opacity when a background is chosen.
    var captionBackgroundOpacity: Double = 0.55

    // Takeover and chapter cards
    var cardBackground: CardBackground = .dark
    var cardAlignment: CardAlignment = .center
    /// Multiplier on the auto-fitted text size.
    var cardTextScale: Double = 1.0
    var cardUppercase = false
    /// The accent stripe along the top and the rule beneath the headline.
    var cardAccentBars = true
    var cardAnimation: CardAnimation = .none

    init() {}

    /// Tolerant, like `AppSettings`: a channel saved before a field existed gets
    /// that field's default instead of failing to load.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: AnyKey.self)
        var d = OverlayStyle()
        d.captionBackground = c.value("captionBackground", d.captionBackground)
        d.captionOutline = c.value("captionOutline", d.captionOutline)
        d.captionShadow = c.value("captionShadow", d.captionShadow)
        d.captionUppercase = c.value("captionUppercase", d.captionUppercase)
        d.captionTextColorHex = c.value("captionTextColorHex", d.captionTextColorHex)
        d.captionBackgroundOpacity = c.value("captionBackgroundOpacity", d.captionBackgroundOpacity)
        d.cardBackground = c.value("cardBackground", d.cardBackground)
        d.cardAlignment = c.value("cardAlignment", d.cardAlignment)
        d.cardTextScale = c.value("cardTextScale", d.cardTextScale)
        d.cardUppercase = c.value("cardUppercase", d.cardUppercase)
        d.cardAccentBars = c.value("cardAccentBars", d.cardAccentBars)
        d.cardAnimation = c.value("cardAnimation", d.cardAnimation)
        self = d
    }
}

// MARK: - Timeline and chapters

/// Where one spoken segment sits in the final narration audio. Recorded by the
/// Voice phase (which knows the exact lengths it joined) so Captions can anchor
/// its word timing to real positions instead of guessing from text length.
struct AudioSegment: Codable, Hashable {
    var start: Double
    var end: Double
    /// Silence placed before this segment: a [pause], or a chapter card.
    var gapBefore: Double = 0
    /// "chapter" when `gapBefore` is a chapter card's window.
    var gapKind: String?
}

/// A chapter (the PC's "part"): a title card shown before `beforeScene`.
struct Chapter: Codable, Hashable {
    var beforeScene: Int
    var title: String
    var subtitle: String?
    /// Seconds the card is on screen (narration pauses for it).
    var duration: Double = Chapter.defaultDuration

    static let defaultDuration = 1.8
    static let minDuration = 1.0
    static let maxDuration = 3.0
}

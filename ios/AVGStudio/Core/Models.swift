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

/// A full-screen takeover card - ported from the PC's
/// `avg.pipeline.phases.director._build_card_plan`. Two sources merge into
/// this same array: a deterministic "Focused" card per `((question))`
/// marker found in a scene's script text (NOT AI-picked), and - when the
/// channel's `explainerCardsEnabled` is on - a FEW AI-picked "Explainer"
/// cards for scenes whose narration states a concept worth a full-screen
/// pause (Focused always wins a scene both would land on). The merged set
/// is frequency-capped to roughly one card per 3 scenes. A card OWNS its
/// scene's entire window - the scene's image and captions are replaced by
/// the card for that span, cutting in/out instantly - regardless of which
/// of the two sources placed it there; the PC's own `card_plan.json`
/// carries no field distinguishing them at render time either.
struct TakeoverCard: Codable, Hashable, Identifiable {
    var scene: Int
    var text: String
    var start: Double
    var end: Double
    var id: Int { scene }
}

/// One hand-added entry in the shared image recycling pool - ported from
/// the PC's `avg.config.recycle_pool` (a hand-edited `images.json` manifest;
/// "No in-app editor exists in either app version" per its own docstring,
/// so an iOS management screen here is a small improvement, not a gap).
/// Shared across every channel; a channel opts in/out and sets how heavily
/// it draws on it via `Channel.recycleEnabled`/`recycleRatio`.
struct ReusableImage: Codable, Hashable, Identifiable {
    var id: String = UUID().uuidString
    var fileName: String
    var description: String
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
    var cards: [TakeoverCard] = []
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

/// Mirrors the PC's `image_on_failure` catalogue entry: what happens once
/// BOTH the primary and backup image providers have failed for a scene.
/// A failure a second provider would hit identically (a blocked prompt, a
/// bug) never falls back regardless of this setting - see visuals()'s own
/// fallback-eligibility check.
enum ImageOnFailure: String, Codable, CaseIterable, Identifiable {
    case stop, fallBack
    var id: String { rawValue }
    var label: String {
        switch self {
        case .stop: return "Stop the run"
        case .fallBack: return "Fall back to Pollinations"
        }
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
    /// Brand accent colour, hex "RRGGBB" or empty for "use the app default".
    /// Applied to the highlight/typewriter caption accent and takeover-card
    /// tint - a small, iPhone-sized analog of the PC's `resolve_brand` palette.
    var accentColorHex: String = ""
    /// Mirrors the PC's per-channel `recycle_enabled`/`recycle_ratio`: the
    /// master switch and how heavily (0-100%) the writer should reach for
    /// the shared reusable-image pool over generating fresh art.
    var recycleEnabled: Bool = true
    var recycleRatio: Double = 70
    /// Mirrors the PC's `voice_trim_aggressiveness`/`voice_max_removable_
    /// silence_pct` channel settings - how readily the dead-air removal
    /// step (SilenceTrim.swift) treats a quiet stretch as silence to cut,
    /// and the cap it backs off one level for if it cut too much.
    var voiceTrimAggressiveness: VoiceTrimAggressiveness = .medium
    var voiceMaxRemovableSilencePct: Double = 40
    /// Mirrors the PC's `overlay_accent1`/`overlay_accent2` (primary/
    /// secondary card accent colours) - `accentColorHex` above already
    /// covers `overlay_highlight`'s role (captions + a card's highlight
    /// colour). Empty uses `accentColorHex`, then the app default -
    /// matching the PC's own "empty uses the drawing library's own
    /// default" chain, just one level deeper.
    var accent1ColorHex: String = ""
    var accent2ColorHex: String = ""
    /// Mirrors the PC's `font_family` - the one font shared by captions
    /// AND takeover/explainer cards. Empty uses the system font.
    var fontFamily: String = ""
    /// Mirrors `topic_research`: spend one extra AI call gathering facts
    /// about the topic before the brief is written, so the script draws
    /// on it.
    var topicResearchEnabled: Bool = false
    /// Mirrors the PC's `image_provider_backup`/`image_on_failure` exactly,
    /// including its default: "stop" - a scene whose only configured
    /// provider fails stops the run, UNLESS a backup provider is set AND
    /// this is switched to "fall back". PC does NOT auto-fall-back to a
    /// free provider by default; this app previously did (always silently
    /// retrying on Pollinations), which was its own bug relative to the PC,
    /// not a deliberate iOS difference - fixed here to match.
    var imageProviderBackup: ImageProviderKind? = nil
    var imageOnFailure: ImageOnFailure = .stop
    /// Mirrors `takeover_cards_enabled` (avg/core/settings_schema.py) -
    /// "Master switch for every full-screen card (Focused, Explainer,
    /// Chapters). Off means none." PC scopes this per-CHANNEL; this app
    /// previously made it a single global Settings toggle, which is its
    /// own iOS-only parity bug (a channel opting out couldn't without
    /// opting every other channel out too) - moved here to fix it.
    var takeoverCardsEnabled = true
    /// Mirrors `explainer_enabled`: an AI pick of a few scenes worth a
    /// full-screen concept card, in addition to the deterministic Focused
    /// cards. Ignored when `takeoverCardsEnabled` is off, like the PC.
    var explainerCardsEnabled = false

    // ── Per-phase text model overrides ──────────────────────────────
    //
    // Mirrors the PC's "Models" settings tab (avg/core/settings_schema.py's
    // per-phase block, `avg.providers.text.router.config_from_settings`):
    // each text-generating phase can name its own provider, an optional
    // backup tried once on a switchable failure, and what to do if both
    // fail. `nil`/`.stop` on every field (the default) behaves exactly
    // like an unconfigured PC channel - everything routes to the one
    // global default provider (`store.settings.text`), matching
    // `config_from_settings`'s own "a phase whose _model is blank simply
    // gets no entry" fallback.
    //
    // The PC names a model as a free `"provider/model"` string per phase;
    // this app already keeps one GLOBAL model id per provider (e.g.
    // `Settings.geminiModel`) rather than a per-channel-per-phase model id,
    // so a phase override here picks the PROVIDER only - the model id for
    // whichever provider is picked still comes from that global field,
    // the same simplification the app already makes for the base case.
    //
    // Only `brief`, `scriptDraft` and `scriptSegmentation` exist, because
    // those are the only text-generating AI calls this app's pipeline
    // actually makes today (Pipeline.writing()'s research+brief call,
    // narration call, and scene-split call, in that order - matching the
    // PC's own `brief`/`script_draft`/`script_segmentation` router phase
    // keys exactly, including research sharing "brief"'s model like the
    // PC's own `generate_text(..., "brief", ...)` call for research does).
    // The PC's OTHER two router phases, `director1` and `director2`, plan
    // visual structure and motion/grade the PC's Director I/II AI phases
    // decide - phases this app has not ported yet (no `video_map.json`/
    // `direction_plan.json` equivalent exists here). Adding model-override
    // settings for them now, with no AI call for them to govern, would be
    // the exact "dead switch" mistake already found and skipped for
    // `voice_stability`/`voice_speed` - they belong with porting Director
    // I/II themselves, not here.
    var briefTextProvider: TextProviderKind? = nil
    var briefTextBackup: TextProviderKind? = nil
    var briefOnFailure: PhaseOnFailure = .stop
    var scriptDraftTextProvider: TextProviderKind? = nil
    var scriptDraftTextBackup: TextProviderKind? = nil
    var scriptDraftOnFailure: PhaseOnFailure = .stop
    var scriptSegmentationTextProvider: TextProviderKind? = nil
    var scriptSegmentationTextBackup: TextProviderKind? = nil
    var scriptSegmentationOnFailure: PhaseOnFailure = .stop
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

/// Generic "what to do if both the phase model and its backup fail" policy
/// - mirrors the PC's `<phase>_on_failure` catalogue entries (`stop` /
/// `fall_back`, avg/core/settings_schema.py), used identically by every
/// text phase below. Kept separate from `ImageOnFailure` because that
/// enum's label names a fixed fallback provider (Pollinations); a text
/// phase falls back to the channel's own default text provider instead,
/// which has no fixed name to put in a label.
enum PhaseOnFailure: String, Codable, CaseIterable, Identifiable {
    case stop, fallBack
    var id: String { rawValue }
    var label: String {
        switch self {
        case .stop: return "Stop the run"
        case .fallBack: return "Fall back to the default text model"
        }
    }
}

enum ImageProviderKind: String, Codable, CaseIterable, Identifiable {
    case pollinations, stablehorde, gemini, openai
    var id: String { rawValue }
    var label: String {
        switch self {
        case .pollinations: return "Pollinations (free)"
        case .stablehorde: return "Stable Horde (free, community)"
        case .gemini: return "Google Gemini"
        case .openai: return "OpenAI"
        }
    }
}

/// Mirrors the PC's `voice_trim_aggressiveness` catalogue entry
/// (avg/core/settings_schema.py) - which of three fixed (threshold dBFS,
/// min-silence ms, keep-silence ms) presets the dead-air stripper uses.
/// "medium" is exactly the PC's own pre-Slice-21 fixed behavior.
enum VoiceTrimAggressiveness: String, Codable, CaseIterable, Identifiable {
    case low, medium, high
    var id: String { rawValue }
    var label: String {
        switch self {
        case .low: return "Low (keep more air)"
        case .medium: return "Medium"
        case .high: return "High (cut more air)"
        }
    }
    /// (thresholdDBFS, minSilenceMs, keepMs) - copied verbatim from
    /// avg/pipeline/phases/voice.py's `_AGGRESSIVENESS_PARAMS`.
    var params: (Double, Int, Int) {
        switch self {
        case .low: return (-45.0, 500, 150)
        case .medium: return (-40.0, 400, 100)
        case .high: return (-35.0, 300, 80)
        }
    }
    /// One step less aggressive - what a capped segment retries with.
    /// "low" maps to itself, matching `_AGGRESSIVENESS_STEP_DOWN`.
    var stepDown: VoiceTrimAggressiveness {
        switch self {
        case .high: return .medium
        case .medium: return .low
        case .low: return .low
        }
    }
}

enum VoiceProviderKind: String, Codable, CaseIterable, Identifiable {
    case apple, elevenlabs, gemini
    var id: String { rawValue }
    var label: String {
        switch self {
        case .apple: return "Apple voices (on-device)"
        case .elevenlabs: return "ElevenLabs"
        // Not a PC provider (avg/providers/voice/providers.py has exactly
        // ElevenLabs + Edge TTS) - added on request as a third option,
        // same "generateContent" endpoint GeminiText/GeminiImages already
        // call, with responseModalities: ["AUDIO"] and a prebuilt voice
        // name instead of a free-form voice id (see GeminiVoice, and
        // GeminiVoice.prebuiltVoices for the full fixed name list Google
        // publishes - there is no list endpoint for these, unlike the TTS
        // MODEL id, which is fetched from the same catalogue GeminiText
        // uses, filtered for "tts").
        case .gemini: return "Google Gemini (TTS)"
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
    // Empty means "use StableHordeImages' own baseline default
    // (stable_diffusion)" - matches every other optional-override field
    // here (e.g. appleVoiceIdentifier's "" == Automatic) rather than
    // duplicating that default string a second time.
    var stableHordeModel = ""
    // Pollinations' own default text/image model ids - "openai" and
    // "flux" match the PC's own hardcoded fallbacks
    // (avg/providers/text/community.py's ChatCompletion model, avg/
    // providers/images/community.py's `_normalize_model`'s fallback).
    var pollinationsTextModel = "openai"
    var pollinationsImageModel = "flux"
    // The one Gemini image model this app calls today - kept as a real
    // setting (not just GeminiImages' own hardcoded literal) now that
    // there is more than one worth offering; see ModelCatalogSources.
    // geminiImageModels for why the PC's own Live List has no equivalent
    // picker to match against (catalogues.py: "OpenAI and Gemini publish
    // no image-model catalogue" through the endpoint the PC restricts
    // itself to) - this reuses GeminiText's own /v1beta/models fetch
    // instead, filtered for image-output ids.
    var geminiImageModel = "gemini-2.5-flash-image"
    var geminiVoiceModel = "gemini-2.5-flash-preview-tts"
    // One of GeminiVoice.prebuiltVoices - "Kore" is Google's own sample
    // default across its TTS docs, kept here for the same reason.
    var geminiVoiceName = "Kore"
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
    /// A classified provider failure - `avg/providers/errors.py`'s
    /// `AvgError(kind, message, detail)` equivalent. `HTTP.retrying` is the
    /// only thing that constructs this case; every provider call site just
    /// lets it propagate, same as before. Replaced the old `.http(Int,
    /// String)` case (an unclassified status+body pair with no policy
    /// attached, and the only reason `isTransient` had to exist as its own
    /// crude classifier) now that `ErrorPolicy.swift` does that job once,
    /// centrally, instead of every reader re-guessing from a status code.
    case provider(ErrorKind, provider: String, message: String)
    case message(String)

    var errorDescription: String? {
        switch self {
        case .missingKey(let k): return "Add your \(k) key in Settings first."
        case .provider(_, let provider, let message): return "\(provider): \(message)"
        case .message(let m): return m
        }
    }

    /// `nil` for `.missingKey`/`.message` - neither has a provider failure
    /// behind it for `POLICY` to have an opinion about.
    var kind: ErrorKind? {
        if case .provider(let k, _, _) = self { return k }
        return nil
    }
}

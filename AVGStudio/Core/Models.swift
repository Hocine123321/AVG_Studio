import Foundation
import SwiftUI

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
    /// What this phase's output depended on when it finished (see
    /// `PhaseInputs`); compared with the settings now to spot a stale phase.
    /// Optional so records saved before takes existed still decode.
    var inputs: [String: String]?
}

struct VideoScene: Codable, Hashable, Identifiable {
    var index: Int
    var text: String
    var imagePrompt: String
    var imageFile: String?
    /// One or two words naming the scene's job ("Hook", "Reveal"), written by
    /// the scene splitter as the PC's `scenes.json` does. Optional so projects
    /// saved before it existed still decode.
    var label: String?
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
    /// "question" (the default, and what every card saved before this field
    /// was), "explainer", or "chapter".
    var kind: String?
    /// A chapter card's second line.
    var subtitle: String?
    /// A chapter card's small label above the title ("PART 2").
    var eyebrow: String?
    /// A chapter card shares its `scene` number with the first scene of the
    /// part it introduces, and a question card can sit on that same scene - so
    /// the id has to include the kind or the two would collide.
    var id: String { "\(kind ?? "question")-\(scene)" }
    var isChapter: Bool { kind == "chapter" }
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
    // Everything below is OPTIONAL: takes saved before real takes existed are
    // just a video file and still decode, show and export - they simply can't
    // be restored (see `Take.canRestore`).
    /// The project's content when this take finished; see Takes.swift.
    var snapshot: TakeSnapshot?
    /// Folder (relative to the project) holding this take's saved images and
    /// audio, or nil while those files are still the working copy.
    var archive: String?
    /// The take this one was branched from.
    var basedOn: Int?
    /// Why it exists, e.g. "Changed: Narrator, Frame rate" or "Redo from Voice".
    var note: String?
}

/// Camera move for one scene, chosen by the Director II AI plan below -
/// ported from `avg/media/vendor/avg.py`'s `_apply_ken_burns` motion
/// vocabulary. `.still` is a deliberate held beat, not "motion off"
/// (that's `Settings.kenBurns == false`, a separate global switch).
enum MotionType: String, Codable, CaseIterable {
    case zoomIn = "zoom_in", zoomOut = "zoom_out"
    case panLeft = "pan_left", panRight = "pan_right", panUp = "pan_up", panDown = "pan_down"
    case still
}

/// Ported from the PC's `_ease` - the same five named curves, applied to a
/// scene's own motion progress `t` (0…1 across that scene's window).
enum EaseCurve: String, Codable, CaseIterable {
    case linear, easeIn = "ease_in", easeOut = "ease_out", easeInOut = "ease_in_out", easeOutCubic = "ease_out_cubic"
}

/// A scene's ENTRANCE transition - ported from `avg.py`'s
/// `_apply_scene_transition`. Despite the name, the PC's own "crossfade"/
/// "dip" behave identically to "fade" in that function (a fade-up-from-
/// black on the entering clip, not a blend between two clips) - matched
/// here rather than invented, since that IS what the vendored engine
/// actually does. `slide_left`/`whip` are likewise one behaviour in the
/// PC (same branch), kept as two names for the AI plan's own vocabulary.
enum SceneTransition: String, Codable, CaseIterable {
    case cut, fade, crossfade, dip
    case slideLeft = "slide_left", slideRight = "slide_right", whip
}

/// One overall colour LOOK for the whole video - ported from `avg.py`'s
/// `_GRADE_PRESETS` table (channel gains → contrast → saturation), values
/// copied verbatim so the same `look` name grades the same on both apps.
enum GradeLook: String, Codable, CaseIterable {
    case none, warm, cool, cinematic, noir, vibrant, soft

    /// (red gain, green gain, blue gain, contrast, saturation) - fed to
    /// `CIColorMatrix`+`CIColorControls` in `VideoRenderer`, the on-device
    /// analog of the PC's per-frame numpy pass.
    var params: (r: Double, g: Double, b: Double, contrast: Double, saturation: Double) {
        switch self {
        case .none: return (1.00, 1.00, 1.00, 1.00, 1.00)
        case .warm: return (1.06, 1.01, 0.94, 1.05, 1.06)
        case .cool: return (0.95, 1.00, 1.08, 1.05, 1.02)
        case .cinematic: return (1.03, 0.99, 1.02, 1.12, 0.96)
        case .noir: return (1.00, 1.00, 1.00, 1.18, 0.30)
        case .vibrant: return (1.02, 1.02, 1.02, 1.08, 1.22)
        case .soft: return (1.02, 1.00, 1.01, 0.94, 1.02)
        }
    }
}

/// One scene's entry in the Director II motion/grade plan - ported from
/// `avg.pipeline.phases.director`'s `direction_plan.json` (`Director2Phase`,
/// `_normalize_direction_entry`). `grade` is shared by the whole plan in
/// the PC (set once, not per scene); kept per-`Project` as `gradeLook`
/// instead of repeated on every entry here, the same de-duplication the
/// PC's own render engine performs by reading `direction_plan["grade"]`
/// once rather than off each scene.
struct SceneDirection: Codable, Hashable {
    var scene: Int
    var motionType: MotionType = .zoomIn
    var intensity: Double = 0.5
    var ease: EaseCurve = .easeOutCubic
    var transitionIn: SceneTransition = .cut
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
    /// Director II's motion/transition plan, one entry per scene in
    /// `scenes` order - see `SceneDirection`'s doc comment. Empty means
    /// "not planned yet, or planning failed" and `VideoRenderer` falls
    /// back to its own deterministic Ken Burns heuristic, exactly like
    /// before this feature existed - never a blocking failure.
    var sceneDirections: [SceneDirection] = []
    /// The one overall colour grade Director II chose for this project.
    var gradeLook: GradeLook = .none
    /// Every scene's narration text, joined, at the time `sceneDirections`
    /// was last planned - a cheap re-plan guard: if the scenes haven't
    /// changed since, rendering again (a new take) reuses the existing
    /// plan instead of spending another AI call on an unchanged script.
    var directionSignature: String = ""
    /// Per-step progress inside each phase (see Steps.swift), keyed
    /// "<phase>.<step>". OPTIONAL on purpose: `Project` uses the compiler's
    /// own Codable, which fails on a missing non-optional key, so a plain
    /// `[String: StepRecord] = [:]` would make every project saved before
    /// this field existed unreadable.
    var stepRecords: [String: StepRecord]?
    /// Director I's plan (each scene's role, beat and visual intent), fed to
    /// Director II's motion prompt. Optional for the same reason as above.
    var storyMap: StoryMap?
    /// The library track `MusicPicker` chose for this video (when its channel
    /// picks automatically), kept so a re-render keeps the same music.
    var autoMusicID: String?
    /// True while the video waits, before its first render, for the person to
    /// edit it (see EditGate.swift). Cleared when they render.
    var awaitingEdit: Bool?
    /// What the person changed about this video beside its channel's settings.
    var edits: ProjectEdits?
    /// The take whose content the working copy (the project's own fields and
    /// files) currently equals; nil once a run has started changing it.
    var workingTakeID: Int?
    /// Where each spoken segment sits in the narration audio (see AudioSegment).
    var audioSegments: [AudioSegment]?
    /// The chapter cards Director I planned, when the channel has chapters on.
    var chapters: [Chapter]?

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
    /// The writing "personality" preset (`personality.txt`): the PC's
    /// `content_shape`. Its full text opens the narration prompt.
    var contentShape: String = "documentary"
    /// A visual-tone catalogue id, or free text (the "custom" tone): the PC's
    /// `visual_tone`. Steers the brief, the narration voice and image prompts.
    var visualTone: String = "documentary_cinematic"
    /// Free-text style tag appended to every generated image prompt so the
    /// pictures look like one set: the PC's `style_lock`.
    var styleLock: String = ""
    /// A brand-preset catalogue id: its motion feel, transitions and grade
    /// steer Director II; its colours seed the channel's accents when picked.
    var brandPreset: String = "modern_dark"
    /// Title cards between the parts of a longer video (the PC's
    /// `chapters_enabled`, off by default).
    var chaptersEnabled: Bool = false
    /// ElevenLabs voice stability, 0...1 (the PC's `voice_stability`).
    var voiceStability: Double = 0.5
    /// Speaking speed, 0.7...1.2 (the PC's `voice_speed`).
    var voiceSpeed: Double = 1.0
    /// How captions and cards are drawn.
    var overlay = OverlayStyle()
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
    /// Background music: the track (an `AudioLibrary` item id) laid under every
    /// video of this channel, or nil for none, and its level while someone is
    /// speaking (see `SoundMix`).
    var musicTrackID: String? = nil
    var musicGainDB: Double = SoundMix.defaultGainDB
    /// When true, `MusicPicker` chooses a library track for each video (and
    /// `musicTrackID` is ignored); `musicRecent` remembers the last few picks,
    /// newest first, so the same bed does not sit under every upload.
    var musicAuto = false
    var musicRecent: [String] = []
    /// Whether to offer the person the edit before a video's first render
    /// (EditGate.swift).
    var editingMode: EditingMode = .ask
    /// Sound effects: off until the person turns them on, a sound (an
    /// `AudioLibrary` item id) per `SFXMoment` raw value, and a channel-wide
    /// level offset in dB applied on top of each moment's own level.
    var sfxEnabled = false
    var sfxMap: [String: String] = [:]
    var sfxGainDB: Double = 0

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
    // The PC's `director1` router phase - visual STRUCTURE (image_count,
    // per-scene role/beat/visual_intent) - is still not ported; see
    // `director2TextProvider`'s own doc comment below for why it has no
    // override field here yet. `director2` (motion/transition/grade) IS
    // now ported (`Pipeline.planDirection()`) and has its own override
    // fields, kept below the other three rather than here since it isn't
    // one of the three text-generating phases this comment describes.
    var briefTextProvider: TextProviderKind? = nil
    var briefTextBackup: TextProviderKind? = nil
    var briefOnFailure: PhaseOnFailure = .stop
    var scriptDraftTextProvider: TextProviderKind? = nil
    var scriptDraftTextBackup: TextProviderKind? = nil
    var scriptDraftOnFailure: PhaseOnFailure = .stop
    var scriptSegmentationTextProvider: TextProviderKind? = nil
    var scriptSegmentationTextBackup: TextProviderKind? = nil
    var scriptSegmentationOnFailure: PhaseOnFailure = .stop
    /// The PC's OTHER router phase key, `director2` - now governs the
    /// Director II motion/transition/grade call in `Pipeline.planDirection()`.
    /// `director1` (visual STRUCTURE - image_count, per-scene role/beat/
    /// visual_intent) is still not ported: this app's Visuals phase always
    /// makes one image per scene (plus the recycling pool), so there is no
    /// structure call for a `director1` override to govern yet - adding one
    /// now would be the same "dead switch" this doc comment used to warn
    /// against for director2 itself.
    var director2TextProvider: TextProviderKind? = nil
    var director2TextBackup: TextProviderKind? = nil
    var director2OnFailure: PhaseOnFailure = .stop
}

enum ThemePreference: String, Codable, CaseIterable, Identifiable {
    case auto, dark, light
    var id: String { rawValue }
    var label: String {
        switch self {
        case .auto: return "Auto"
        case .dark: return "Night"
        case .light: return "Day"
        }
    }
    var colorScheme: ColorScheme? {
        switch self {
        case .auto: return nil
        case .dark: return .dark
        case .light: return .light
        }
    }
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
    /// The mock's own Night/Day/Auto appearance switch (`S.theme`) - this
    /// app had no in-app override before Skin v2's Settings re-merge, it
    /// only ever followed the system automatically. `.auto` keeps that
    /// exact old behaviour (a nil `colorScheme` override).
    var themePreference: ThemePreference = .auto
    /// App-wide glass opacity, 0.15 (very see-through) ... 1.0 (the mock's
    /// own default fill). Drives every glass card AND sheet background so
    /// the main screens and pop-up windows read the same.
    var glassOpacity: Double = 1.0
    /// App-wide UI font: system or the mock's Caprasimo.
    var appFont: AppFontChoice = .system
    /// Model fallback: the cheaper model each AI drops to when the main one is
    /// too expensive, over quota, or keeps failing. One per AI kind. Empty
    /// means "use the built-in light preset for the current provider".
    var writerFallbackModel = ""
    var imageFallbackModel = ""
    var voiceFallbackModel = ""
    /// When on, a quota/billing/"too expensive" failure switches to the
    /// fallback model by itself (and says so in the log) instead of asking.
    var autoUseFallback = false
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
    /// How many videos may run their pipeline at once; extra ones queue and
    /// start as slots free (the PC's `max_parallel_jobs`, default 2). Renders
    /// take turns regardless - see RenderLease.
    var maxParallelJobs = 2
    // Performance (Settings -> Performance)
    var renderEngine: RenderEngine = .auto
    var videoCodec: VideoCodec = .hevc
    var encoderQuality: EncoderQuality = .automatic
    /// Images made at once during Visuals (1...4).
    var imageParallelism = 3
    /// Wait for the iPhone to cool down between frames when it reports heat.
    var pauseWhenHot = true
    /// Stop the screen auto-locking while a video is being made: a locked
    /// screen suspends the app and, with it, the GPU.
    var keepScreenAwake = true
    /// Keep working (more slowly) when you leave the app.
    var allowBackgroundRendering = true

    // MARK: Assistant (the in-app chat agent; see AgentSettings.swift)
    var agentProvider: AgentProviderKind = .groq
    var agentGroqModel = "llama-3.3-70b-versatile"
    var agentGeminiModel = "gemini-2.5-flash"
    /// If the chosen service is rate-limited or out of quota, try the other
    /// one (when its key exists) - but only before any tool has run.
    var agentFailover = true
    /// A turned-off action is not even offered to the model.
    var agentAllowCreate = true
    var agentAllowRedo = true
    var agentAllowCancel = true
    /// More capabilities (see AgentCapability.swift). Each one that turns off
    /// removes its tools from what the model is offered.
    var agentAllowManageProjects = true
    var agentAllowChangeSettings = true
    var agentAllowEditChannels = true
    var agentAllowDiagnostics = true
    var agentApproval: AgentApprovalMode = .always
    /// Most the assistant may have approved in one chat (USD estimate).
    var agentChatSpendCapUSD = AgentSettingsRules.spendCapDefault
    var agentReplyStyle: AgentReplyStyle = .brief
    /// Post "ready" / "stopped" in the chat for videos the assistant started.
    var agentAnnounceResults = true
    /// Save the conversation (your messages and its answers) across launches.
    var agentKeepChat = false
    /// Standing preferences added to the system prompt; capped at 400.
    var agentInstructions = ""

    /// Tolerant decode: a key missing from an older save (i.e. any field
    /// added in an update) keeps its default instead of failing the whole
    /// settings blob - which used to reset EVERY setting on each update.
    init() {}
    init(from decoder: Decoder) throws {
        self.init()
        let c = try decoder.container(keyedBy: AnyKey.self)
        onboarded = c.value("onboarded", onboarded)
        themePreference = c.value("themePreference", themePreference)
        glassOpacity = c.value("glassOpacity", glassOpacity)
        appFont = c.value("appFont", appFont)
        writerFallbackModel = c.value("writerFallbackModel", writerFallbackModel)
        imageFallbackModel = c.value("imageFallbackModel", imageFallbackModel)
        voiceFallbackModel = c.value("voiceFallbackModel", voiceFallbackModel)
        autoUseFallback = c.value("autoUseFallback", autoUseFallback)
        text = c.value("text", text)
        image = c.value("image", image)
        voice = c.value("voice", voice)
        geminiModel = c.value("geminiModel", geminiModel)
        groqModel = c.value("groqModel", groqModel)
        stableHordeModel = c.value("stableHordeModel", stableHordeModel)
        pollinationsTextModel = c.value("pollinationsTextModel", pollinationsTextModel)
        pollinationsImageModel = c.value("pollinationsImageModel", pollinationsImageModel)
        geminiImageModel = c.value("geminiImageModel", geminiImageModel)
        geminiVoiceModel = c.value("geminiVoiceModel", geminiVoiceModel)
        geminiVoiceName = c.value("geminiVoiceName", geminiVoiceName)
        elevenVoiceID = c.value("elevenVoiceID", elevenVoiceID)
        elevenModel = c.value("elevenModel", elevenModel)
        appleVoiceIdentifier = c.value("appleVoiceIdentifier", appleVoiceIdentifier)
        imageStyle = c.value("imageStyle", imageStyle)
        fps = c.value("fps", fps)
        shortEdge = c.value("shortEdge", shortEdge)
        kenBurns = c.value("kenBurns", kenBurns)
        kbIntensity = c.value("kbIntensity", kbIntensity)
        captionsEnabled = c.value("captionsEnabled", captionsEnabled)
        captionMaxWords = c.value("captionMaxWords", captionMaxWords)
        captionPosition = c.value("captionPosition", captionPosition)
        captionFontSize = c.value("captionFontSize", captionFontSize)
        captionAnimationStyle = c.value("captionAnimationStyle", captionAnimationStyle)
        dynamicPausesEnabled = c.value("dynamicPausesEnabled", dynamicPausesEnabled)
        pauseMs = c.value("pauseMs", pauseMs)
        filterStyle = c.value("filterStyle", filterStyle)
        filterIntensity = c.value("filterIntensity", filterIntensity)
        maxParallelJobs = c.value("maxParallelJobs", maxParallelJobs)
        renderEngine = c.value("renderEngine", renderEngine)
        videoCodec = c.value("videoCodec", videoCodec)
        encoderQuality = c.value("encoderQuality", encoderQuality)
        imageParallelism = c.value("imageParallelism", imageParallelism)
        pauseWhenHot = c.value("pauseWhenHot", pauseWhenHot)
        keepScreenAwake = c.value("keepScreenAwake", keepScreenAwake)
        allowBackgroundRendering = c.value("allowBackgroundRendering", allowBackgroundRendering)
        appFont = c.value("appFont", appFont)
        agentProvider = c.value("agentProvider", agentProvider)
        agentGroqModel = c.value("agentGroqModel", agentGroqModel)
        agentGeminiModel = c.value("agentGeminiModel", agentGeminiModel)
        agentFailover = c.value("agentFailover", agentFailover)
        agentAllowCreate = c.value("agentAllowCreate", agentAllowCreate)
        agentAllowRedo = c.value("agentAllowRedo", agentAllowRedo)
        agentAllowCancel = c.value("agentAllowCancel", agentAllowCancel)
        agentAllowManageProjects = c.value("agentAllowManageProjects", agentAllowManageProjects)
        agentAllowChangeSettings = c.value("agentAllowChangeSettings", agentAllowChangeSettings)
        agentAllowEditChannels = c.value("agentAllowEditChannels", agentAllowEditChannels)
        agentAllowDiagnostics = c.value("agentAllowDiagnostics", agentAllowDiagnostics)
        agentApproval = c.value("agentApproval", agentApproval)
        agentChatSpendCapUSD = c.value("agentChatSpendCapUSD", agentChatSpendCapUSD)
        agentReplyStyle = c.value("agentReplyStyle", agentReplyStyle)
        agentAnnounceResults = c.value("agentAnnounceResults", agentAnnounceResults)
        agentKeepChat = c.value("agentKeepChat", agentKeepChat)
        agentInstructions = c.value("agentInstructions", agentInstructions)
    }
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


/// Dynamic coding key so tolerant decoders can name fields by string.
extension KeyedDecodingContainer where K == AnyKey {
    /// The saved value if present AND decodable, else the default.
    func value<T: Decodable>(_ key: String, _ fallback: T) -> T {
        (try? decodeIfPresent(T.self, forKey: AnyKey(key))) ?? fallback
    }
}

struct AnyKey: CodingKey {
    var stringValue: String
    var intValue: Int? { nil }
    init(_ s: String) { stringValue = s }
    init?(stringValue: String) { self.stringValue = stringValue }
    init?(intValue: Int) { return nil }
}

enum AppFontChoice: String, Codable, CaseIterable, Identifiable {
    case system, caprasimo
    var id: String { rawValue }
    var label: String { self == .system ? "System" : "Caprasimo" }
}

// Tolerant Channel decoding, in an extension so the struct keeps its
// synthesized memberwise init (Channel(name:niche:) is used all over).
// A channel saved before a field existed keeps that field's default
// instead of the whole channel list failing to load.
extension Channel {
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: AnyKey.self)
        var d = Channel(name: c.value("name", "My Channel"))
        d.id = c.value("id", d.id)
        d.niche = c.value("niche", d.niche)
        d.tone = c.value("tone", d.tone)
        d.language = c.value("language", d.language)
        d.targetSeconds = c.value("targetSeconds", d.targetSeconds)
        d.aspectRatio = c.value("aspectRatio", d.aspectRatio)
        d.accentColorHex = c.value("accentColorHex", d.accentColorHex)
        d.recycleEnabled = c.value("recycleEnabled", d.recycleEnabled)
        d.recycleRatio = c.value("recycleRatio", d.recycleRatio)
        d.voiceTrimAggressiveness = c.value("voiceTrimAggressiveness", d.voiceTrimAggressiveness)
        d.voiceMaxRemovableSilencePct = c.value("voiceMaxRemovableSilencePct", d.voiceMaxRemovableSilencePct)
        d.accent1ColorHex = c.value("accent1ColorHex", d.accent1ColorHex)
        d.accent2ColorHex = c.value("accent2ColorHex", d.accent2ColorHex)
        d.fontFamily = c.value("fontFamily", d.fontFamily)
        d.topicResearchEnabled = c.value("topicResearchEnabled", d.topicResearchEnabled)
        d.contentShape = c.value("contentShape", d.contentShape)
        d.visualTone = c.value("visualTone", d.visualTone)
        d.styleLock = c.value("styleLock", d.styleLock)
        d.brandPreset = c.value("brandPreset", d.brandPreset)
        d.chaptersEnabled = c.value("chaptersEnabled", d.chaptersEnabled)
        d.voiceStability = c.value("voiceStability", d.voiceStability)
        d.voiceSpeed = c.value("voiceSpeed", d.voiceSpeed)
        d.overlay = c.value("overlay", d.overlay)
        d.imageProviderBackup = c.value("imageProviderBackup", d.imageProviderBackup)
        d.imageOnFailure = c.value("imageOnFailure", d.imageOnFailure)
        d.takeoverCardsEnabled = c.value("takeoverCardsEnabled", d.takeoverCardsEnabled)
        d.explainerCardsEnabled = c.value("explainerCardsEnabled", d.explainerCardsEnabled)
        d.musicTrackID = c.value("musicTrackID", d.musicTrackID)
        d.musicGainDB = c.value("musicGainDB", d.musicGainDB)
        d.musicAuto = c.value("musicAuto", d.musicAuto)
        d.editingMode = c.value("editingMode", d.editingMode)
        d.musicRecent = c.value("musicRecent", d.musicRecent)
        d.sfxEnabled = c.value("sfxEnabled", d.sfxEnabled)
        d.sfxMap = c.value("sfxMap", d.sfxMap)
        d.sfxGainDB = c.value("sfxGainDB", d.sfxGainDB)
        d.briefTextProvider = c.value("briefTextProvider", d.briefTextProvider)
        d.briefTextBackup = c.value("briefTextBackup", d.briefTextBackup)
        d.briefOnFailure = c.value("briefOnFailure", d.briefOnFailure)
        d.scriptDraftTextProvider = c.value("scriptDraftTextProvider", d.scriptDraftTextProvider)
        d.scriptDraftTextBackup = c.value("scriptDraftTextBackup", d.scriptDraftTextBackup)
        d.scriptDraftOnFailure = c.value("scriptDraftOnFailure", d.scriptDraftOnFailure)
        d.scriptSegmentationTextProvider = c.value("scriptSegmentationTextProvider", d.scriptSegmentationTextProvider)
        d.scriptSegmentationTextBackup = c.value("scriptSegmentationTextBackup", d.scriptSegmentationTextBackup)
        d.scriptSegmentationOnFailure = c.value("scriptSegmentationOnFailure", d.scriptSegmentationOnFailure)
        d.director2TextProvider = c.value("director2TextProvider", d.director2TextProvider)
        d.director2TextBackup = c.value("director2TextBackup", d.director2TextBackup)
        d.director2OnFailure = c.value("director2OnFailure", d.director2OnFailure)
        self = d
    }
}

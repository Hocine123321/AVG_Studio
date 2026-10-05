import Foundation

// Takes: "generate, tweak, generate again, compare" - ported from the PC's
// avg/core/takes.py + avg/core/invalidation.py.
//
// What was here before: a "take" was just a finished video file. The working
// state (script, scenes, images, narration) existed once and was overwritten
// in place by every redo, so an earlier video could not be traced back to what
// made it and "Redo from Script" destroyed the previous attempt.
//
// What this adds, in three independent pieces:
//
//  1. PhaseInputs   - what each phase's output depends on, recorded when it
//                     finishes. Compared with the settings now, it tells you
//                     which phases a change made stale, and re-running only
//                     those (and what follows) is the PC's selective redo.
//  2. TakeSnapshot  - the project's content at the moment a take finished, so
//                     an older take can be restored and continued from.
//  3. TakeArchive   - the files (images, audio, narration) behind a snapshot,
//                     copied aside just before a run would overwrite them.
//
// The PC clones a take's folder when branching. Here the working copy stays in
// the project folder (as it always did, so every phase keeps writing where it
// writes) and the OLD take's files are archived at branch time instead.
// Same outcome for the person - the earlier take survives untouched and the new
// one keeps whatever phases the change did not invalidate - with far less
// plumbing to get wrong. Copies are APFS clones: no extra space until a file is
// actually rewritten.

// MARK: - 1. Phase inputs (invalidation)

enum PhaseInputs {
    /// Everything that decides what `phase` produces, as stable key -> text.
    /// Free of I/O so it can be tested; the one thing it cannot see itself
    /// (whether a transcription key exists) is passed in.
    static func describe(_ phase: PhaseID, settings s: AppSettings, channel ch: Channel,
                         hasTranscriptionKey: Bool = false) -> [String: String] {
        func d<T: RawRepresentable>(_ v: T?) -> String where T.RawValue == String { v?.rawValue ?? "default" }
        switch phase {
        case .writing:
            return [
                "text": s.text.rawValue,
                "writerModel": writerModel(s),
                "niche": ch.niche, "tone": ch.tone, "language": ch.language,
                "targetSeconds": String(ch.targetSeconds),
                "contentShape": ch.contentShape, "visualTone": ch.visualTone, "styleLock": ch.styleLock,
                "topicResearch": String(ch.topicResearchEnabled),
                "chapters": String(ch.chaptersEnabled),
                "recycle": ch.recycleEnabled ? "on \(Int(ch.recycleRatio))" : "off",
                "briefProvider": d(ch.briefTextProvider), "briefBackup": d(ch.briefTextBackup),
                "draftProvider": d(ch.scriptDraftTextProvider), "draftBackup": d(ch.scriptDraftTextBackup),
                "splitProvider": d(ch.scriptSegmentationTextProvider), "splitBackup": d(ch.scriptSegmentationTextBackup),
            ]
        case .visuals:
            return [
                "image": s.image.rawValue,
                "imageModel": imageModel(s),
                "imageBackup": d(ch.imageProviderBackup),
                // The app-wide style suffix is only used when there is no style lock.
                "imageStyle": ch.styleLock.trimmed.isEmpty ? s.imageStyle : "",
                // Picture quality follows the video quality, so a change of TIER
                // (not every resolution step) makes the pictures stale. Gemini
                // has no quality control, so it never does.
                "imageQuality": s.image == .gemini ? "" : ImageQuality.forVideo(shortEdge: s.shortEdge).rawValue,
            ]
        case .voice:
            return [
                "voice": s.voice.rawValue,
                "voiceIdentity": voiceIdentity(s),
                "dynamicPauses": s.dynamicPausesEnabled ? "on \(Int(s.pauseMs))" : "off",
                "trim": ch.voiceTrimAggressiveness.rawValue,
                "maxRemovable": String(Int(ch.voiceMaxRemovableSilencePct)),
                // Only the settings the chosen voice actually honours.
                "voiceStability": s.voice == .elevenlabs ? String(ch.voiceStability) : "",
                "voiceSpeed": s.voice == .gemini ? "" : String(ch.voiceSpeed),
                "chapters": String(ch.chaptersEnabled),
            ]
        case .captions:
            return [
                "captionMaxWords": String(s.captionMaxWords),
                "takeoverCards": String(ch.takeoverCardsEnabled),
                "explainerCards": String(ch.explainerCardsEnabled),
                "transcriptionKey": String(hasTranscriptionKey),
                "chapters": String(ch.chaptersEnabled),
            ]
        case .render:
            return [
                "quality": String(s.shortEdge), "fps": String(s.fps),
                "kenBurns": String(s.kenBurns), "kbIntensity": String(s.kbIntensity),
                "filter": "\(s.filterStyle.rawValue) \(Int(s.filterIntensity))",
                "captions": String(s.captionsEnabled),
                "captionAnimation": s.captionAnimationStyle.rawValue,
                "captionPosition": s.captionPosition.rawValue,
                "captionSize": String(Int(s.captionFontSize)),
                "accent": ch.accentColorHex, "accent1": ch.accent1ColorHex, "accent2": ch.accent2ColorHex,
                "font": ch.fontFamily,
                "brand": ch.brandPreset,
                "overlay": Self.overlayDescription(ch.overlay),
                "codec": s.videoCodec.rawValue, "encoderQuality": s.encoderQuality.rawValue,
                "directorProvider": d(ch.director2TextProvider), "directorBackup": d(ch.director2TextBackup),
            ]
        }
    }

    /// A stable one-line description of the overlay look, for change detection.
    static func overlayDescription(_ o: OverlayStyle) -> String {
        [o.captionBackground.rawValue, String(o.captionOutline), String(o.captionShadow), String(o.captionUppercase),
         o.captionTextColorHex, String(o.captionBackgroundOpacity), o.cardBackground.rawValue, o.cardAlignment.rawValue,
         String(o.cardTextScale), String(o.cardUppercase), String(o.cardAccentBars), o.cardAnimation.rawValue].joined(separator: "|")
    }

    private static func writerModel(_ s: AppSettings) -> String {
        switch s.text {
        case .gemini: return s.geminiModel
        case .groq: return s.groqModel
        case .pollinations: return s.pollinationsTextModel
        case .apple: return ""
        }
    }

    private static func imageModel(_ s: AppSettings) -> String {
        switch s.image {
        case .stablehorde: return s.stableHordeModel
        case .pollinations: return s.pollinationsImageModel
        case .gemini: return s.geminiImageModel
        case .openai: return ""
        }
    }

    private static func voiceIdentity(_ s: AppSettings) -> String {
        switch s.voice {
        case .apple: return s.appleVoiceIdentifier
        case .elevenlabs: return "\(s.elevenVoiceID) \(s.elevenModel)"
        case .gemini: return "\(s.geminiVoiceName) \(s.geminiVoiceModel)"
        }
    }

    /// Plain-English names for the keys above, for "Changed: Narrator, Frame rate".
    static let labels: [String: String] = [
        "text": "Writer", "writerModel": "Writer model", "niche": "Niche", "tone": "Tone", "language": "Language",
        "targetSeconds": "Video length", "contentShape": "Personality", "visualTone": "Visual tone",
        "styleLock": "Style lock", "topicResearch": "Topic research", "recycle": "Image reuse",
        "briefProvider": "Brief writer", "briefBackup": "Brief backup", "draftProvider": "Narration writer",
        "draftBackup": "Narration backup", "splitProvider": "Scene-split writer", "splitBackup": "Scene-split backup",
        "image": "Image maker", "imageModel": "Image model", "imageBackup": "Image backup", "imageStyle": "Image style",
        "voice": "Voice provider", "voiceIdentity": "Narrator", "dynamicPauses": "Dynamic pauses",
        "trim": "Dead-air trim", "maxRemovable": "Trim limit",
        "captionMaxWords": "Words per caption", "takeoverCards": "Takeover cards", "explainerCards": "Explainer cards",
        "transcriptionKey": "Transcription key",
        "quality": "Quality", "fps": "Frame rate", "kenBurns": "Ken Burns", "kbIntensity": "Motion amount",
        "filter": "Film filter", "captions": "Captions", "captionAnimation": "Caption animation",
        "captionPosition": "Caption position", "captionSize": "Caption size",
        "accent": "Highlight colour", "accent1": "Accent 1", "accent2": "Accent 2", "font": "Font",
        "brand": "Brand preset", "directorProvider": "Motion planner", "directorBackup": "Motion backup",
        "chapters": "Chapter cards", "imageQuality": "Image quality", "voiceStability": "Voice stability",
        "voiceSpeed": "Voice speed", "overlay": "Overlay look", "codec": "Video format", "encoderQuality": "Encoder quality",
    ]

    /// The keys whose values differ, in a stable (alphabetical-by-label) order.
    static func changedKeys(recorded: [String: String], current: [String: String]) -> [String] {
        let keys = Set(recorded.keys).union(current.keys)
        return keys.filter { recorded[$0] != current[$0] }
            .sorted { (labels[$0] ?? $0) < (labels[$1] ?? $1) }
    }
}

/// Which phases the current settings have made out of date for a project.
struct StaleReport: Equatable {
    /// Phases whose OWN inputs changed since they ran, with what changed.
    var stale: [PhaseID: [String]] = [:]

    var isEmpty: Bool { stale.isEmpty }

    /// Where an update should start: the earliest stale phase.
    var firstPhase: PhaseID? { PhaseID.allCases.first { stale[$0] != nil } }

    /// Every phase an update from `firstPhase` will run again.
    var willRerun: [PhaseID] {
        guard let first = firstPhase else { return [] }
        return PhaseID.allCases.filter { $0.order >= first.order }
    }

    /// Labels of everything that changed, deduplicated, in phase order.
    var changedLabels: [String] {
        var seen = Set<String>(), out: [String] = []
        for phase in PhaseID.allCases {
            for key in stale[phase] ?? [] {
                let label = PhaseInputs.labels[key] ?? key
                if seen.insert(label).inserted { out.append(label) }
            }
        }
        return out
    }

    /// Compares each FINISHED phase's recorded inputs with `describe` now. A
    /// phase with no record (finished before this existed, or never run) is
    /// never called stale: unknown is not the same as changed.
    static func compute(project p: Project, settings: AppSettings, channel: Channel, hasTranscriptionKey: Bool) -> StaleReport {
        var report = StaleReport()
        for phase in PhaseID.allCases {
            let record = p.phase(phase)
            guard record.status == .done, let recorded = record.inputs else { continue }
            let now = PhaseInputs.describe(phase, settings: settings, channel: channel, hasTranscriptionKey: hasTranscriptionKey)
            let changed = PhaseInputs.changedKeys(recorded: recorded, current: now)
            if !changed.isEmpty { report.stale[phase] = changed }
        }
        return report
    }
}

// MARK: - 2. Snapshot

/// The project's content when a take finished - everything a restore must put
/// back (the files behind it are in `TakeArchive`).
struct TakeSnapshot: Codable, Hashable {
    var brief: String?
    var narration: String?
    var scenes: [VideoScene]
    var audioFile: String?
    var sceneDurations: [Double]
    var captions: [CaptionCue]
    var captionsSource: String
    var cards: [TakeoverCard]
    var sceneDirections: [SceneDirection]
    var gradeLook: GradeLook
    var directionSignature: String
    var storyMap: StoryMap?
    var audioSegments: [AudioSegment]?
    var chapters: [Chapter]?
    var phases: [String: PhaseRecord]
    var stepRecords: [String: StepRecord]?

    static func capture(_ p: Project) -> TakeSnapshot {
        TakeSnapshot(brief: p.brief, narration: p.narration, scenes: p.scenes, audioFile: p.audioFile,
                     sceneDurations: p.sceneDurations, captions: p.captions, captionsSource: p.captionsSource,
                     cards: p.cards, sceneDirections: p.sceneDirections, gradeLook: p.gradeLook,
                     directionSignature: p.directionSignature, storyMap: p.storyMap,
                     audioSegments: p.audioSegments, chapters: p.chapters,
                     phases: p.phases, stepRecords: p.stepRecords)
    }

    /// Puts this content back on `p`. Leaves identity, takes, costs, archive
    /// state and the video pointer alone - those belong to the project.
    func apply(to p: inout Project) {
        p.brief = brief; p.narration = narration; p.scenes = scenes; p.audioFile = audioFile
        p.sceneDurations = sceneDurations; p.captions = captions; p.captionsSource = captionsSource
        p.cards = cards; p.sceneDirections = sceneDirections; p.gradeLook = gradeLook
        p.directionSignature = directionSignature; p.storyMap = storyMap
        p.audioSegments = audioSegments; p.chapters = chapters
        p.phases = phases; p.stepRecords = stepRecords
    }
}

extension Take {
    /// True when this take's content can be restored and continued from.
    var canRestore: Bool { snapshot != nil }
    var isArchived: Bool { archive != nil }
}

// MARK: - 3. Archive (files)

/// The working copy's content files, in the project folder. A take's archive
/// holds a copy of exactly these.
enum TakeArchive {
    static let directories = ["images", "audio"]
    static let files = ["narration.m4a"]

    static func archivePath(_ takeID: Int) -> String { "takes/\(takeID)" }

    /// Copies the working content into `takes/<id>/`. The partial folder is
    /// removed on failure (the PC's "register only after the copy succeeds").
    static func archive(takeID: Int, projectFolder: URL, fm: FileManager = .default) throws -> String {
        let rel = archivePath(takeID)
        let dest = projectFolder.appendingPathComponent(rel, isDirectory: true)
        try? fm.removeItem(at: dest)
        do {
            try fm.createDirectory(at: dest, withIntermediateDirectories: true)
            try copyContent(from: projectFolder, to: dest, fm: fm)
        } catch {
            try? fm.removeItem(at: dest)
            throw error
        }
        return rel
    }

    /// Replaces the working content with the archive's.
    static func restore(archive rel: String, projectFolder: URL, fm: FileManager = .default) throws {
        let source = projectFolder.appendingPathComponent(rel, isDirectory: true)
        guard fm.fileExists(atPath: source.path) else {
            throw AVGError.message("This take's saved files are missing.")
        }
        for name in directories + files { try? fm.removeItem(at: projectFolder.appendingPathComponent(name)) }
        try copyContent(from: source, to: projectFolder, fm: fm)
    }

    static func remove(archive rel: String, projectFolder: URL, fm: FileManager = .default) {
        try? fm.removeItem(at: projectFolder.appendingPathComponent(rel, isDirectory: true))
    }

    private static func copyContent(from source: URL, to dest: URL, fm: FileManager) throws {
        for name in directories + files {
            let from = source.appendingPathComponent(name)
            guard fm.fileExists(atPath: from.path) else { continue }
            try fm.copyItem(at: from, to: dest.appendingPathComponent(name))
        }
    }

    /// Archive folders that no remaining take points at once `deleting` is gone.
    static func orphanedArchives(takes: [Take], deleting id: Int) -> [String] {
        guard let doomed = takes.first(where: { $0.id == id })?.archive else { return [] }
        let stillUsed = takes.contains { $0.id != id && $0.archive == doomed }
        return stillUsed ? [] : [doomed]
    }
}

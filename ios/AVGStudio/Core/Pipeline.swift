import Foundation
import AVFoundation
import SwiftUI

/// Swift port of avg/pipeline: each phase reads what the previous one wrote on the project.
@MainActor
struct Pipeline {
    let store: Store
    let projectID: String

    private var project: Project {
        get throws {
            guard let p = store.project(projectID) else { throw AVGError.message("Project was deleted.") }
            return p
        }
    }
    private var folder: URL { store.folder(for: projectID) }

    func execute(_ phase: PhaseID) async throws {
        switch phase {
        case .writing: try await writing()
        case .visuals: try await visuals()
        case .voice: try await voice()
        case .captions: try await captions()
        case .render: try await render()
        }
    }

    // MARK: Providers

    private func textProvider() throws -> TextProvider {
        let s = store.settings
        switch s.text {
        case .apple: return AppleText()
        case .gemini: return GeminiText(key: try Keychain.require(.gemini), model: s.geminiModel)
        case .groq: return ChatText.groq(key: try Keychain.require(.groq), model: s.groqModel)
        case .pollinations: return ChatText.pollinations()
        }
    }

    private func imageProvider() throws -> ImageProvider {
        switch store.settings.image {
        case .pollinations: return PollinationsImages()
        case .gemini: return GeminiImages(key: try Keychain.require(.gemini))
        case .openai: return OpenAIImages(key: try Keychain.require(.openai))
        }
    }

    private func voiceProvider() throws -> VoiceProvider {
        let s = store.settings
        switch s.voice {
        case .apple: return AppleVoice(identifier: s.appleVoiceIdentifier)
        case .elevenlabs: return ElevenLabsVoice(key: try Keychain.require(.elevenlabs), voiceID: s.elevenVoiceID, model: s.elevenModel)
        }
    }

    // MARK: Rough cost estimates
    //
    // Flat, clearly-labelled guesses (see CostEntry's doc comment) - not
    // real billing. Free/on-device providers never post a cost.

    private func costText(_ kind: TextProviderKind, chars: Int) {
        let rate: Double
        switch kind {
        case .apple, .pollinations: return
        case .gemini: rate = 0.00000025 // ~$0.25 / 1M chars, ballpark for a flash-tier model
        case .groq: rate = 0.00000015
        }
        store.addCost(projectID, "\(kind.label) · \(chars) chars", Double(chars) * rate)
    }

    private func costImage(_ kind: ImageProviderKind) {
        let flat: Double
        switch kind {
        case .pollinations: return
        case .gemini: flat = 0.02
        case .openai: flat = 0.04
        }
        store.addCost(projectID, "\(kind.label) image", flat)
    }

    private func costVoice(_ kind: VoiceProviderKind, chars: Int) {
        switch kind {
        case .apple: return
        case .elevenlabs: store.addCost(projectID, "ElevenLabs · \(chars) chars", Double(chars) * 0.00003)
        }
    }

    private func costTranscription(seconds: Double) {
        store.addCost(projectID, "AssemblyAI · \(Int(seconds))s audio", seconds / 60.0 * 0.01)
    }

    // MARK: Phase 0+1 — brief, narration, scenes

    private func writing() async throws {
        let p = try project
        let ch = store.channel(for: p)
        let text = try textProvider()
        let system = """
        You write narration for short videos for the channel "\(ch.name)". Niche: \(ch.niche). \
        Tone: \(ch.tone). Write in \(ch.language).
        """
        store.progress(projectID, 0.1, "Writing the creative brief…")
        let briefPrompt = """
        Write a short creative brief for a ~\(ch.targetSeconds)-second video about: \(p.topic)
        Cover the hook, the angle, the emotional arc and the tone. Max 120 words. Plain text, no headings.
        """
        let brief = try await text.complete(briefPrompt, system: system)
        costText(store.settings.text, chars: briefPrompt.count + brief.count)
        store.update(projectID) { $0.brief = brief.trimmed }

        store.progress(projectID, 0.4, "Writing the narration…")
        let words = max(40, ch.targetSeconds * 150 / 60)
        let narrationPrompt = """
        Creative brief:
        \(brief)

        Write the narration a narrator speaks for a video about: \(p.topic)
        About \(words) words. Flowing prose only: no headings, no scene numbers, no stage directions, \
        no emojis, no quotation marks around the whole text. You may insert [pause] for a dramatic pause.
        Separately, wrap any short rhetorical question or punchy hook in double parentheses, e.g. \
        "((But why?))" - at most a couple across the whole script, only genuine brief questions/hooks, \
        never a whole sentence. These become full-screen emphasis cards; the words inside are still spoken normally.
        """
        let narrationRaw = try await text.complete(narrationPrompt, system: system)
        costText(store.settings.text, chars: narrationPrompt.count + narrationRaw.count)
        let narration = Self.cleanNarration(narrationRaw)
        guard !narration.isEmpty else { throw AVGError.message("The writer returned an empty script.") }

        store.progress(projectID, 0.7, "Splitting into scenes…")
        // Image recycling pool - mirrors writing.py's own three modes
        // ("off"/"partial"/"all"), see avg.config.recycle_pool's doc
        // comment: reuse a hand-curated pool image instead of generating
        // fresh art, when the channel opts in and a pool exists.
        let pool = store.reusableImages
        let recycleRatio = max(0, min(100, ch.recycleRatio))
        let useRecyclePool = ch.recycleEnabled && recycleRatio > 0 && !pool.isEmpty
        var splitInstructions = "Split the narration below into 5 to 12 scenes WITHOUT changing, adding or removing a single word. For each scene write a vivid image prompt describing the visual (no text, no letters in the image)."
        if useRecyclePool {
            let poolLines = pool.enumerated().map { "\($0 + 1). \($1.description)" }.joined(separator: "\n")
            let rule = recycleRatio >= 100
                ? "For EVERY scene's image_prompt, ALWAYS use the token \"[RECYCLE #k]\" naming the pool image (by its number below) that best fits. Never write a fresh description."
                : "For each scene's image_prompt, use the token \"[RECYCLE #k]\" naming a pool image ONLY when it genuinely fits (at most \(Int(recycleRatio))% of scenes) - otherwise write a fresh image_prompt as usual."
            splitInstructions += "\n\nYou also have a pool of existing reusable images:\n\(poolLines)\n\n\(rule)"
        }
        let splitPrompt = """
        \(splitInstructions)

        Reply with ONLY a JSON array like [{"text": "...", "image_prompt": "..."}].

        NARRATION:
        \(narration)
        """
        let raw = try await text.complete(splitPrompt, system: nil)
        costText(store.settings.text, chars: splitPrompt.count + raw.count)
        var scenes = Self.parseScenes(raw)
        if scenes.isEmpty || Self.norm(scenes.map(\.text).joined(separator: " ")) != Self.norm(narration) {
            store.record("Scene split changed the wording, so scenes were split by sentence instead.")
            scenes = Self.sentenceScenes(narration, prompts: scenes.map(\.imagePrompt), topic: p.topic)
        }
        if useRecyclePool {
            Self.normalizeRecyclePicks(&scenes, poolSize: pool.count)
            if recycleRatio >= 100 { Self.forceFillAllMode(&scenes, poolSize: pool.count) }
        }
        store.update(projectID) {
            $0.narration = narration
            $0.scenes = scenes
            $0.audioFile = nil
            $0.output = nil
        }
    }

    // MARK: Phase — visuals

    private func visuals() async throws {
        let p = try project
        let provider = try imageProvider()
        let size = p.aspectRatio.size(shortEdge: 1024)
        let style = store.settings.imageStyle
        let dir = folder.appendingPathComponent("images", isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let scenes = p.scenes
        guard !scenes.isEmpty else { throw AVGError.message("No scenes yet — run Script first.") }

        var done = 0
        let pool = store.reusableImages
        let requestedKind = store.settings.image // captured once: read on MainActor, then only passed by value into the concurrent child tasks below, never read from `store` off-actor.
        for batch in stride(from: 0, to: scenes.count, by: 3) {
            try Task.checkCancellation()
            let slice = Array(scenes[batch..<min(batch + 3, scenes.count)])
            // A `[RECYCLE #k]` scene copies a pool image straight in - no
            // provider call, no cost - mirroring visuals.py's own
            // `_resolve_recycled_scene`. A pick whose file has since gone
            // missing falls back to generating fresh art from that same
            // pool entry's description, never leaving the scene blank.
            var toGenerate: [VideoScene] = []
            for s in slice {
                guard let k = Self.recycleIndex(s.imagePrompt), pool.indices.contains(k - 1) else {
                    toGenerate.append(s)
                    continue
                }
                let entry = pool[k - 1]
                if let data = try? Data(contentsOf: store.reusableImageURL(entry)) {
                    let name = "images/scene_\(String(format: "%02d", s.index)).jpg"
                    try? data.write(to: folder.appendingPathComponent(name))
                    store.update(projectID) { proj in
                        if let i = proj.scenes.firstIndex(where: { $0.index == s.index }) { proj.scenes[i].imageFile = name }
                    }
                    done += 1
                    store.progress(projectID, Double(done) / Double(scenes.count), "Reusing pool image for scene \(s.index)")
                } else {
                    var fallback = s
                    fallback.imagePrompt = entry.description
                    toGenerate.append(fallback)
                }
            }
            guard !toGenerate.isEmpty else { continue }
            try await withThrowingTaskGroup(of: (Int, Data, ImageProviderKind).self) { group in
                for s in toGenerate {
                    let prompt = "\(s.imagePrompt.isEmpty ? s.text : s.imagePrompt). \(style)"
                    group.addTask {
                        do { return (s.index, try await provider.generate(prompt: prompt, size: size), requestedKind) }
                        catch let e as AVGError {
                            // Paid provider failed: fall back to the free one, like the PC app.
                            if provider is PollinationsImages { throw e }
                            return (s.index, try await PollinationsImages().generate(prompt: prompt, size: size), .pollinations)
                        }
                    }
                }
                for try await (index, data, usedKind) in group {
                    let name = "images/scene_\(String(format: "%02d", index)).jpg"
                    try data.write(to: folder.appendingPathComponent(name))
                    costImage(usedKind)
                    store.update(projectID) { proj in
                        if let i = proj.scenes.firstIndex(where: { $0.index == index }) { proj.scenes[i].imageFile = name }
                    }
                    done += 1
                    store.progress(projectID, Double(done) / Double(scenes.count), "Image \(done) of \(scenes.count)")
                }
            }
        }
    }

    // MARK: Phase — voice
    //
    // "[pause] must become real silence, never spoken text" - the PC's
    // named invariant (avg/pipeline/phases/voice.py's module docstring),
    // carried from a real bug in the legacy PowerShell app: it used to feed
    // the TTS engine a literal " ... " and hope the voice paused on it. This
    // phase does the same thing the PC does instead: split the narration AT
    // each [pause] marker (the marker text itself never reaches a
    // provider), synthesize each segment separately, then stitch the
    // segments back together with an explicit, exact-length silent gap
    // (`assembleWithGaps`) - a real silence, not a hope.

    private func voice() async throws {
        let p = try project
        guard let narration = p.narration else { throw AVGError.message("No script yet — run Script first.") }
        let s = store.settings
        let provider = try voiceProvider()
        let dir = folder.appendingPathComponent("audio", isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)

        let rawParts = s.dynamicPausesEnabled ? Self.splitOnPause(narration) : [Self.stripPauseMarkers(narration)]
        let segments = rawParts.map(Self.spoken).filter { !$0.isEmpty }
        guard !segments.isEmpty else { throw AVGError.message("Nothing left to narrate after removing pause markers.") }

        store.progress(projectID, 0.1, segments.count > 1 ? "Recording \(segments.count) segments…" : "Recording the narration…")
        var files: [URL] = []
        for (i, seg) in segments.enumerated() {
            try Task.checkCancellation()
            if segments.count > 1 { store.progress(projectID, 0.1 + 0.6 * Double(i) / Double(segments.count), "Segment \(i + 1) of \(segments.count)…") }
            let name = try await provider.synthesize(seg, into: dir, name: "segment_\(i)")
            files.append(dir.appendingPathComponent(name))
            costVoice(s.voice, chars: seg.count)
        }

        let finalURL: URL
        if files.count == 1 {
            finalURL = files[0]
        } else {
            store.progress(projectID, 0.8, "Assembling narration with pauses…")
            let pauseSeconds = s.dynamicPausesEnabled ? max(0, s.pauseMs / 1000) : 0
            finalURL = try await Self.assembleWithGaps(files, pauseSeconds: pauseSeconds, into: dir)
            for f in files where f.lastPathComponent != finalURL.lastPathComponent {
                try? FileManager.default.removeItem(at: f)
            }
        }
        store.update(projectID) { $0.audioFile = "audio/\(finalURL.lastPathComponent)" }
    }

    /// Concatenates `segments` into one audio file at `folder/narration.m4a`,
    /// inserting `pauseSeconds` of real silence between each pair. An
    /// `AVMutableComposition` track is silent by default wherever nothing
    /// has been inserted, so leaving the gap between two `insertTimeRange`
    /// calls untouched IS the silence - no synthetic silent buffer needed.
    static func assembleWithGaps(_ segments: [URL], pauseSeconds: Double, into folder: URL) async throws -> URL {
        let comp = AVMutableComposition()
        guard let track = comp.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid) else {
            throw AVGError.message("Couldn't assemble the narration audio.")
        }
        var cursor = CMTime.zero
        for (i, url) in segments.enumerated() {
            let asset = AVURLAsset(url: url)
            guard let segTrack = try await asset.loadTracks(withMediaType: .audio).first else { continue }
            let dur = try await asset.load(.duration)
            try track.insertTimeRange(CMTimeRange(start: .zero, duration: dur), of: segTrack, at: cursor)
            cursor = cursor + dur
            if i < segments.count - 1, pauseSeconds > 0 {
                cursor = cursor + CMTime(seconds: pauseSeconds, preferredTimescale: 600)
            }
        }
        guard let export = AVAssetExportSession(asset: comp, presetName: AVAssetExportPresetAppleM4A) else {
            throw AVGError.message("Couldn't export the narration audio.")
        }
        let out = folder.appendingPathComponent("narration.m4a")
        try? FileManager.default.removeItem(at: out)
        try await export.export(to: out, as: .m4a)
        return out
    }

    // MARK: Phase — captions
    //
    // Real transcription (AssemblyAI) when a key is configured; otherwise
    // an estimate, exactly mirroring the PC's own
    // `avg.pipeline.phases.captions._get_words`/`_synthetic_word_timings`:
    // each scene's text gets a character-weighted proportional slice of the
    // audio, then each scene's own words get a character-weighted
    // proportional slice of that. Per-word timing (not just per-cue) is
    // kept so the highlight/typewriter caption animations have something
    // to sync to.

    private func captions() async throws {
        let p = try project
        guard let audio = store.file(p, p.audioFile) else { throw AVGError.message("No narration audio yet.") }
        let duration = try await AVURLAsset(url: audio).load(.duration).seconds
        let cleanTexts = p.scenes.map { Self.spoken($0.text) }
        guard cleanTexts.contains(where: { !$0.isEmpty }) else { throw AVGError.message("No scene text to caption.") }

        let sceneSpans = Self.allocateProportional(cleanTexts, total: duration)
        let sceneDurations = sceneSpans.map { $0.1 - $0.0 }

        var words: [WordTiming] = []
        var source = "estimated"
        if let key = Keychain.get(.assemblyai) {
            store.progress(projectID, 0.15, "Transcribing narration…")
            do {
                let real = try await AssemblyAI.words(fromAudio: audio, key: key)
                if !real.isEmpty {
                    words = real
                    source = "assemblyai"
                    costTranscription(seconds: duration)
                }
            } catch {
                store.record("AssemblyAI transcription unavailable (\(error.localizedDescription)); using estimated timing.")
            }
        }
        if words.isEmpty {
            words = Self.syntheticWordTimings(cleanTexts, spans: sceneSpans)
        }
        guard !words.isEmpty else { throw AVGError.message("Nothing to caption.") }

        // Deterministic Focused takeover cards: one per `((question))`
        // marker found in a scene's own (uncleaned) text, each taking over
        // its whole scene's window - mirrors director.py's `_build_card_plan`
        // exactly (frequency cap, one-card-per-scene, first-question-wins).
        var cards: [TakeoverCard] = []
        if store.settings.takeoverCardsEnabled {
            var bySc: [Int: TakeoverCard] = [:]
            for (scene, span) in zip(p.scenes, sceneSpans) {
                guard bySc[scene.index] == nil, let q = Self.extractQuestionMarkers(scene.text).first else { continue }
                bySc[scene.index] = TakeoverCard(scene: scene.index, text: q, start: span.0, end: span.1)
            }
            let ordered = bySc.values.sorted { $0.scene < $1.scene }
            if !ordered.isEmpty {
                let cap = max(1, p.scenes.count / 3)
                cards = Array(ordered.prefix(cap))
            }
        }
        let cardWindows = cards.map { ($0.start, $0.end) }

        store.progress(projectID, 0.6, "Grouping caption cues…")
        let maxWords = max(1, store.settings.captionMaxWords)
        var cues: [CaptionCue] = []
        for chunkStart in stride(from: 0, to: words.count, by: maxWords) {
            let group = Array(words[chunkStart..<min(chunkStart + maxWords, words.count)])
            guard let first = group.first, let last = group.last else { continue }
            // A caption cue that overlaps a takeover card's window is
            // suppressed - the card owns that span instead (mirrors
            // captions.py's own `_card_suppression_windows` use).
            if cardWindows.contains(where: { Self.overlaps(first.start, last.end, $0.0, $0.1) }) { continue }
            cues.append(CaptionCue(start: first.start, end: last.end, text: group.map(\.text).joined(separator: " "), words: group))
        }

        store.update(projectID) {
            $0.sceneDurations = sceneDurations
            $0.captions = cues
            $0.captionsSource = source
            $0.cards = cards
        }
    }

    // MARK: Phase — render

    private func render() async throws {
        let p = try project
        guard let audio = store.file(p, p.audioFile) else { throw AVGError.message("No narration audio yet.") }
        let s = store.settings
        let ch = store.channel(for: p)
        let takeID = (p.takes.map(\.id).max() ?? 0) + 1
        let name = "render/take_\(takeID).mp4"
        let out = folder.appendingPathComponent(name)
        try FileManager.default.createDirectory(at: out.deletingLastPathComponent(), withIntermediateDirectories: true)
        let input = RenderInput(
            images: p.scenes.map { store.file(p, $0.imageFile) },
            sceneDurations: p.sceneDurations,
            audio: audio,
            cues: p.captions,
            size: p.aspectRatio.size(shortEdge: s.shortEdge),
            fps: s.fps,
            kenBurns: s.kenBurns,
            kbIntensity: s.kbIntensity,
            captions: CaptionStyle(
                enabled: s.captionsEnabled,
                fontSize: CGFloat(s.captionFontSize),
                position: s.captionPosition,
                animation: s.captionAnimationStyle,
                accent: Theme.color(hex: ch.accentColorHex) ?? Theme.accent
            ),
            filter: FilterSpec(style: s.filterStyle, intensity: s.filterIntensity),
            cards: p.cards,
            output: out
        )
        let id = projectID
        let st = store
        try await VideoRenderer().render(input) { value in
            Task { @MainActor in st.progress(id, value, "Rendering \(Int(value * 100))%") }
        }
        store.update(projectID) {
            $0.output = name
            $0.takes.append(Take(id: takeID, label: "Take \(takeID)", created: Date(), file: name))
        }
        store.record("Finished “\(p.title)” (take \(takeID)).")
    }

    // MARK: Text helpers

    static func norm(_ s: String) -> String {
        s.split(whereSeparator: \.isWhitespace).joined(separator: " ")
    }

    static func cleanNarration(_ s: String) -> String {
        var t = s.trimmed
        if t.hasPrefix("\""), t.hasSuffix("\""), t.count > 2 { t = String(t.dropFirst().dropLast()) }
        return t.replacingOccurrences(of: "*", with: "").trimmed
    }

    /// What the narrator actually says: delivery markers removed, [pause]
    /// collapsed to a space (used on the "dynamic pauses off" path, and for
    /// on-screen previews - the actual pause handling on the "on" path
    /// happens BEFORE this via `splitOnPause`, never through this).
    static func spoken(_ s: String) -> String {
        var t = s.replacingOccurrences(of: "[pause]", with: " ", options: .caseInsensitive)
        for m in ["[[", "]]", "((", "))"] { t = t.replacingOccurrences(of: m, with: "") }
        return norm(t)
    }

    static func stripPauseMarkers(_ s: String) -> String {
        pauseRegex.stringByReplacingMatches(in: s, range: NSRange(s.startIndex..., in: s), withTemplate: " ")
    }

    private static let pauseRegex = try! NSRegularExpression(pattern: "\\s*\\[\\s*pause\\s*\\]\\s*", options: [.caseInsensitive])

    /// `((question))` markers' inner text, in order - the deterministic
    /// source of Focused takeover cards. Mirrors `extract_question_markers`
    /// exactly (same regex shape, non-greedy, spans newlines).
    private static let questionRegex = try! NSRegularExpression(pattern: "\\(\\(\\s*(.*?)\\s*\\)\\)", options: [.dotMatchesLineSeparators])
    static func extractQuestionMarkers(_ s: String) -> [String] {
        let ns = s as NSString
        return questionRegex.matches(in: s, range: NSRange(location: 0, length: ns.length)).compactMap { m in
            guard m.numberOfRanges > 1 else { return nil }
            let inner = ns.substring(with: m.range(at: 1)).trimmed
            return inner.isEmpty ? nil : inner
        }
    }

    static func overlaps(_ s1: Double, _ e1: Double, _ s2: Double, _ e2: Double) -> Bool {
        s1 < e2 && s2 < e1
    }

    // MARK: Image recycling pool
    //
    // Mirrors writing.py's own `_normalize_recycle_picks`/`_force_fill_all_mode`
    // (post-parse cleanup of whatever the model sent back) and visuals.py's
    // `_resolve_recycled_scene` (resolving a canonical token to an actual
    // file). See `ReusableImage`'s doc comment in Models.swift.

    private static let recyclePickRegex = try! NSRegularExpression(pattern: "(?i)\\[?\\s*recycle\\s*#?\\s*(\\d+)\\s*\\]?")
    private static let canonicalRecycleRegex = try! NSRegularExpression(pattern: "^\\[RECYCLE #\\d+\\]$")

    /// The pool index a scene's `image_prompt` names, however loosely
    /// written ("Use [RECYCLE #3]", "recycle #3", "[RECYCLE #3]") - or nil
    /// if it doesn't mention recycling at all.
    static func recycleIndex(_ text: String) -> Int? {
        let ns = text as NSString
        guard let m = recyclePickRegex.firstMatch(in: text, range: NSRange(location: 0, length: ns.length)), m.numberOfRanges > 1 else { return nil }
        return Int(ns.substring(with: m.range(at: 1)))
    }

    /// A loosely-written recycle pick becomes the canonical `"[RECYCLE #k]"`
    /// token every downstream reader expects; an out-of-range pick is
    /// stripped instead, leaving whatever prose (if any) came with it.
    static func normalizeRecyclePicks(_ scenes: inout [VideoScene], poolSize: Int) {
        for i in scenes.indices {
            let text = scenes[i].imagePrompt
            guard let k = recycleIndex(text) else { continue }
            if k >= 1, k <= poolSize {
                scenes[i].imagePrompt = "[RECYCLE #\(k)]"
            } else {
                let ns = text as NSString
                scenes[i].imagePrompt = recyclePickRegex.stringByReplacingMatches(in: text, range: NSRange(location: 0, length: ns.length), withTemplate: "").trimmed
            }
        }
    }

    /// `"all"` mode's hard contract: every scene not already carrying a
    /// valid `[RECYCLE #k]` after `normalizeRecyclePicks` is force-filled
    /// by cycling the pool round-robin.
    static func forceFillAllMode(_ scenes: inout [VideoScene], poolSize: Int) {
        guard poolSize > 0 else { return }
        var n = 0
        for i in scenes.indices {
            let text = scenes[i].imagePrompt
            let ns = text as NSString
            if canonicalRecycleRegex.firstMatch(in: text, range: NSRange(location: 0, length: ns.length)) != nil { continue }
            let k = (n % poolSize) + 1
            n += 1
            scenes[i].imagePrompt = "[RECYCLE #\(k)]"
        }
    }

    /// Narration text split at each `[pause]` marker; the marker itself is
    /// consumed by the split and never appears in any returned segment.
    /// Mirrors the PC's `_split_on_pause` exactly (same regex shape).
    static func splitOnPause(_ s: String) -> [String] {
        let ns = s as NSString
        var out: [String] = []
        var last = 0
        let matches = pauseRegex.matches(in: s, range: NSRange(location: 0, length: ns.length))
        for m in matches {
            let piece = ns.substring(with: NSRange(location: last, length: m.range.location - last)).trimmed
            if !piece.isEmpty { out.append(piece) }
            last = m.range.location + m.range.length
        }
        let tail = ns.substring(from: last).trimmed
        if !tail.isEmpty { out.append(tail) }
        return out.isEmpty ? [s.trimmed] : out
    }

    /// Splits `total` seconds across `units` proportional to each unit's own
    /// character count (floor of 1 char so an empty unit doesn't swallow
    /// zero time). Exactly the PC's `allocate_proportional` - shared by
    /// scene-level AND word-level timing so the two can't disagree.
    static func allocateProportional(_ units: [String], total: Double, offset: Double = 0) -> [(Double, Double)] {
        let weights = units.map { max($0.trimmed.count, 1) }
        let totalWeight = weights.reduce(0, +)
        var cursor = offset
        var spans: [(Double, Double)] = []
        for w in weights {
            let dur = totalWeight > 0 ? total * (Double(w) / Double(totalWeight)) : 0
            spans.append((cursor, cursor + dur))
            cursor += dur
        }
        return spans
    }

    /// Per-word estimated timing with no real transcript: each scene's own
    /// words split proportionally across that scene's already-estimated
    /// window. Mirrors `_synthetic_word_timings`.
    static func syntheticWordTimings(_ texts: [String], spans: [(Double, Double)]) -> [WordTiming] {
        var out: [WordTiming] = []
        for (text, span) in zip(texts, spans) {
            let words = text.split(whereSeparator: \.isWhitespace).map(String.init)
            guard !words.isEmpty else { continue }
            let wordSpans = allocateProportional(words, total: max(span.1 - span.0, 0), offset: span.0)
            for (word, ws) in zip(words, wordSpans) {
                out.append(WordTiming(text: word, start: ws.0, end: ws.1))
            }
        }
        return out
    }

    static func parseScenes(_ raw: String) -> [VideoScene] {
        guard let a = raw.firstIndex(of: "["), let b = raw.lastIndex(of: "]"), a < b,
              let data = String(raw[a...b]).data(using: .utf8),
              let arr = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else { return [] }
        return arr.enumerated().compactMap { i, o in
            guard let text = (o["text"] as? String)?.trimmed, !text.isEmpty else { return nil }
            return VideoScene(index: i + 1, text: text, imagePrompt: (o["image_prompt"] as? String) ?? "")
        }
    }

    static func sentenceScenes(_ narration: String, prompts: [String], topic: String) -> [VideoScene] {
        var sentences: [String] = []
        narration.enumerateSubstrings(in: narration.startIndex..., options: .bySentences) { sub, _, _, _ in
            if let s = sub?.trimmed, !s.isEmpty { sentences.append(s) }
        }
        if sentences.isEmpty { sentences = [narration] }
        let per = max(1, Int(ceil(Double(sentences.count) / 10.0)))
        var out: [VideoScene] = []
        for (n, start) in stride(from: 0, to: sentences.count, by: per).enumerated() {
            let text = sentences[start..<min(start + per, sentences.count)].joined(separator: " ")
            let prompt = prompts.indices.contains(n) && !prompts[n].isEmpty ? prompts[n] : "\(topic): \(text)"
            out.append(VideoScene(index: n + 1, text: text, imagePrompt: prompt))
        }
        return out
    }
}

extension String {
    var trimmed: String { trimmingCharacters(in: .whitespacesAndNewlines) }
}

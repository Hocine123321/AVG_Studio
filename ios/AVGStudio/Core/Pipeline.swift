import Foundation
import AVFoundation

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
        let brief = try await text.complete("""
        Write a short creative brief for a ~\(ch.targetSeconds)-second video about: \(p.topic)
        Cover the hook, the angle, the emotional arc and the tone. Max 120 words. Plain text, no headings.
        """, system: system)
        store.update(projectID) { $0.brief = brief.trimmed }

        store.progress(projectID, 0.4, "Writing the narration…")
        let words = max(40, ch.targetSeconds * 150 / 60)
        let narration = Self.cleanNarration(try await text.complete("""
        Creative brief:
        \(brief)

        Write the narration a narrator speaks for a video about: \(p.topic)
        About \(words) words. Flowing prose only: no headings, no scene numbers, no stage directions, \
        no emojis, no quotation marks around the whole text. You may insert [pause] for a dramatic pause.
        """, system: system))
        guard !narration.isEmpty else { throw AVGError.message("The writer returned an empty script.") }

        store.progress(projectID, 0.7, "Splitting into scenes…")
        let raw = try await text.complete("""
        Split the narration below into 5 to 12 scenes WITHOUT changing, adding or removing a single word. \
        For each scene write a vivid image prompt describing the visual (no text, no letters in the image).
        Reply with ONLY a JSON array like [{"text": "...", "image_prompt": "..."}].

        NARRATION:
        \(narration)
        """, system: nil)
        var scenes = Self.parseScenes(raw)
        if scenes.isEmpty || Self.norm(scenes.map(\.text).joined(separator: " ")) != Self.norm(narration) {
            store.record("Scene split changed the wording, so scenes were split by sentence instead.")
            scenes = Self.sentenceScenes(narration, prompts: scenes.map(\.imagePrompt), topic: p.topic)
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
        for batch in stride(from: 0, to: scenes.count, by: 3) {
            try Task.checkCancellation()
            let slice = Array(scenes[batch..<min(batch + 3, scenes.count)])
            try await withThrowingTaskGroup(of: (Int, Data).self) { group in
                for s in slice {
                    let prompt = "\(s.imagePrompt.isEmpty ? s.text : s.imagePrompt). \(style)"
                    group.addTask {
                        do { return (s.index, try await provider.generate(prompt: prompt, size: size)) }
                        catch let e as AVGError {
                            // Paid provider failed: fall back to the free one, like the PC app.
                            if provider is PollinationsImages { throw e }
                            return (s.index, try await PollinationsImages().generate(prompt: prompt, size: size))
                        }
                    }
                }
                for try await (index, data) in group {
                    let name = "images/scene_\(String(format: "%02d", index)).jpg"
                    try data.write(to: folder.appendingPathComponent(name))
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

    private func voice() async throws {
        let p = try project
        guard let narration = p.narration else { throw AVGError.message("No script yet — run Script first.") }
        let spoken = Self.spoken(narration)
        store.progress(projectID, 0.2, "Recording the narration…")
        let dir = folder.appendingPathComponent("audio", isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let name = try await voiceProvider().synthesize(spoken, into: dir)
        store.update(projectID) { $0.audioFile = "audio/\(name)" }
    }

    // MARK: Phase — captions (estimated word timing, like the PC's "estimated" provider)

    private func captions() async throws {
        let p = try project
        guard let audio = store.file(p, p.audioFile) else { throw AVGError.message("No narration audio yet.") }
        let duration = try await AVURLAsset(url: audio).load(.duration).seconds
        let texts = p.scenes.map { Self.spoken($0.text) }
        let weights = texts.map { Double(max($0.count, 1)) }
        let total = weights.reduce(0, +)
        let durations = weights.map { $0 / total * duration }

        var cues: [CaptionCue] = []
        var t = 0.0
        let maxWords = max(1, store.settings.captionMaxWords)
        for (text, d) in zip(texts, durations) {
            let words = text.split(whereSeparator: \.isWhitespace).map(String.init)
            let chars = Double(max(words.reduce(0) { $0 + $1.count + 1 }, 1))
            var local = t
            for chunk in stride(from: 0, to: words.count, by: maxWords) {
                let group = Array(words[chunk..<min(chunk + maxWords, words.count)])
                let len = Double(group.reduce(0) { $0 + $1.count + 1 }) / chars * d
                cues.append(CaptionCue(start: local, end: local + len, text: group.joined(separator: " ")))
                local += len
            }
            t += d
        }
        store.update(projectID) {
            $0.sceneDurations = durations
            $0.captions = cues
        }
    }

    // MARK: Phase — render

    private func render() async throws {
        let p = try project
        guard let audio = store.file(p, p.audioFile) else { throw AVGError.message("No narration audio yet.") }
        let s = store.settings
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
            captions: CaptionStyle(enabled: s.captionsEnabled, fontSize: CGFloat(s.captionFontSize), position: s.captionPosition),
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

    /// What the narrator actually says: delivery markers removed.
    static func spoken(_ s: String) -> String {
        var t = s.replacingOccurrences(of: "[pause]", with: " ", options: .caseInsensitive)
        for m in ["[[", "]]", "((", "))"] { t = t.replacingOccurrences(of: m, with: "") }
        return norm(t)
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

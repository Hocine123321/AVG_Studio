import Foundation

// The creative prompt engine, ported from the PC app:
//   avg/pipeline/personality.py   - writing "personality" presets
//   avg/pipeline/prompts.py       - brief / narration / scene-split / Director I / style bible
//   avg/pipeline/phases/_common.py - the scene-block parser
// and the two tuned asset files it reads, bundled here VERBATIM
// (Resources/personality.txt, Resources/dynamic_options.txt) because that
// phrasing is the product: it is what makes a documentary script sound like
// a documentary rather than like a generic summary.
//
// The earlier iOS prompts were short paraphrases ("Write a short creative
// brief... About N words... Split into 5 to 12 scenes"). They produced valid
// output, but with no voice, no visual tone and no style tag, so videos from
// the two apps differed in quality even with the same model. Everything here
// is pure (string in, string out) so it is unit-tested without a network.

// MARK: - Personality presets

struct PersonalityPreset: Hashable, Identifiable {
    let name: String
    let description: String
    let body: String
    var id: String { name }
}

enum Personality {
    /// Used when the catalogue itself can't be read: a prompt can always be built.
    static let generic = PersonalityPreset(
        name: "generic", description: "",
        body: "You are a video script writer. Follow ALL instructions exactly.")

    /// Every preset in the bundled file, in file order.
    static let all: [PersonalityPreset] = parse(CreativeAssets.text("personality"))

    /// Parses `## PRESET: name` / optional `## DESCRIPTION: ...` / body blocks.
    /// Never throws: a missing or unparseable file yields an empty list.
    static func parse(_ raw: String) -> [PersonalityPreset] {
        var presets: [PersonalityPreset] = []
        var name: String?
        var description = ""
        var body: [String] = []
        var expectingDescription = false

        func flush() {
            defer { name = nil; description = ""; body = []; expectingDescription = false }
            guard let n = name?.lowercased(), !n.isEmpty else { return }
            let text = body.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { return }
            presets.append(PersonalityPreset(name: n, description: description, body: text))
        }

        let cleaned = raw.replacingOccurrences(of: "\u{FEFF}", with: "")
        for rawLine in cleaned.components(separatedBy: "\n") {
            let line = rawLine.trimmingCharacters(in: CharacterSet(charactersIn: "\r"))
            if let value = header(line, "PRESET") {
                flush()
                name = value.split(whereSeparator: \.isWhitespace).first.map(String.init)
                expectingDescription = true
                continue
            }
            if expectingDescription, let value = header(line, "DESCRIPTION") {
                description = value
                expectingDescription = false
                continue
            }
            expectingDescription = false
            if name != nil { body.append(line) }
        }
        flush()
        return presets
    }

    /// The preset named `name`, or - exactly the PC's degradation ladder - the
    /// FIRST preset when the name is blank/unknown, or the built-in generic one
    /// when the file is missing. Never an empty persona: that was a real PC
    /// regression (a renamed preset silently produced voiceless scripts).
    static func preset(named name: String, in presets: [PersonalityPreset] = Personality.all) -> PersonalityPreset {
        let key = name.trimmingCharacters(in: .whitespaces).lowercased()
        if let hit = presets.first(where: { $0.name == key }) { return hit }
        return presets.first ?? generic
    }

    fileprivate static func header(_ line: String, _ key: String) -> String? {
        let t = line.trimmingCharacters(in: .whitespaces)
        guard t.hasPrefix("##") else { return nil }
        let rest = t.dropFirst(2).trimmingCharacters(in: .whitespaces)
        let prefix = key + ":"
        guard rest.hasPrefix(prefix) else { return nil }
        return String(rest.dropFirst(prefix.count)).trimmingCharacters(in: .whitespaces)
    }
}

// MARK: - Option catalogue (visual tones, brand presets)

struct CatalogOption: Hashable, Identifiable {
    let id: String
    let label: String
    let hint: String
    /// `hint` re-read as `key=value;key=value`. Prose hints (the visual tones)
    /// yield `[:]`; a brand preset's hint IS this payload.
    let payload: [String: String]
}

enum OptionCatalog {
    static let bundled: [String: [CatalogOption]] = parse(CreativeAssets.text("dynamic_options"))

    static func options(_ category: String) -> [CatalogOption] { bundled[category] ?? [] }

    static func parse(_ raw: String) -> [String: [CatalogOption]] {
        var out: [String: [CatalogOption]] = [:]
        var current: String?
        for rawLine in raw.replacingOccurrences(of: "\u{FEFF}", with: "").components(separatedBy: "\n") {
            let line = rawLine.trimmingCharacters(in: .whitespacesAndNewlines)
            if let category = Personality.header(line, "CATEGORY") {
                current = category.split(whereSeparator: \.isWhitespace).first.map(String.init)
                if let current, out[current] == nil { out[current] = [] }
                continue
            }
            guard let current, let rest = Personality.header(line, "OPTION") else { continue }
            let parts = rest.split(separator: "|", maxSplits: 2, omittingEmptySubsequences: false)
                .map { $0.trimmingCharacters(in: .whitespaces) }
            guard let id = parts.first, !id.isEmpty else { continue }
            let label = parts.count > 1 ? parts[1] : id
            let hint = parts.count > 2 ? parts[2] : ""
            out[current, default: []].append(CatalogOption(id: id, label: label, hint: hint, payload: payload(hint)))
        }
        return out
    }

    /// `"a=1;b=2"` -> `["a": "1", "b": "2"]`. A token with no `=` (ordinary
    /// prose) is skipped, never an error - which is what lets this run on every
    /// option's hint, prose or not.
    static func payload(_ raw: String) -> [String: String] {
        var out: [String: String] = [:]
        for token in raw.split(separator: ";") {
            guard let eq = token.firstIndex(of: "=") else { continue }
            let key = token[..<eq].trimmingCharacters(in: .whitespaces)
            if !key.isEmpty { out[key] = token[token.index(after: eq)...].trimmingCharacters(in: .whitespaces) }
        }
        return out
    }
}

enum CreativeAssets {
    /// A bundled resource's text, or "" when it is missing (every consumer
    /// degrades rather than failing - see `Personality.preset`).
    static func text(_ name: String) -> String {
        guard let url = Bundle.main.url(forResource: name, withExtension: "txt"),
              let s = try? String(contentsOf: url, encoding: .utf8) else { return "" }
        return s
    }
}

// MARK: - Visual tone, brand preset

enum Brand {
    /// Expands a stored `visualTone` into prompt text. An id from the catalogue
    /// becomes "Label — description"; anything that isn't a bare id is already
    /// descriptive text (the "custom" tone a person typed) and is returned as is;
    /// an unknown id degrades to itself rather than blanking the prompt.
    static func resolveVisualTone(_ value: String, catalog: [String: [CatalogOption]] = OptionCatalog.bundled) -> String {
        let v = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !v.isEmpty else { return "" }
        guard v.range(of: "^[a-z0-9_]+$", options: .regularExpression) != nil else { return v }
        guard let opt = catalog["video_tone"]?.first(where: { $0.id == v }) else { return v }
        return opt.hint.isEmpty ? opt.label : "\(opt.label) — \(opt.hint)"
    }

    static func preset(_ id: String, catalog: [String: [CatalogOption]] = OptionCatalog.bundled) -> CatalogOption? {
        catalog["brand_presets"]?.first { $0.id == id.trimmingCharacters(in: .whitespaces) }
    }

    /// Only the LABEL is prompt-worthy: a brand's hint is a colour payload for
    /// rendering, not prose for a model.
    static func label(_ id: String, catalog: [String: [CatalogOption]] = OptionCatalog.bundled) -> String {
        let v = id.trimmingCharacters(in: .whitespaces)
        guard !v.isEmpty else { return "" }
        return preset(v, catalog: catalog)?.label ?? v
    }

    struct StyleBible: Equatable { var motion: String; var transitions: String; var grade: String }

    private static let styleDefaults: [String: StyleBible] = [
        "modern_dark": StyleBible(motion: "balanced push-ins with occasional slow drifts", transitions: "cut, crossfade", grade: "cinematic"),
        "neon_cyber": StyleBible(motion: "punchy fast pushes and quick pans, high energy", transitions: "cut, whip, slide_left", grade: "vibrant"),
        "warm_organic": StyleBible(motion: "gentle slow drifts and soft zoom-ins, unhurried", transitions: "crossfade, dip, fade", grade: "warm"),
        "clean_light": StyleBible(motion: "minimal, precise, mostly slow zoom-ins", transitions: "cut, fade", grade: "soft"),
        "luxury_gold": StyleBible(motion: "slow, deliberate, elegant push-ins", transitions: "crossfade, dip", grade: "cinematic"),
        "wireframe_ink": StyleBible(motion: "minimal, precise, mostly static holds with slow pans", transitions: "cut, fade", grade: "none"),
        "tiktok_viral": StyleBible(motion: "fast punchy pushes and quick cuts, high energy", transitions: "cut, whip, slide_left", grade: "vibrant"),
        "vsauce_curiosity": StyleBible(motion: "measured pushes with the occasional quick zoom on a reveal", transitions: "crossfade, cut", grade: "cool"),
        "academic_editorial": StyleBible(motion: "minimal, precise, mostly slow zoom-ins", transitions: "cut, fade", grade: "soft"),
        "tactical_terminal": StyleBible(motion: "sharp fast pushes and quick pans, high-alert energy", transitions: "cut, whip", grade: "noir"),
    ]
    private static let styleFallback = StyleBible(motion: "balanced push-ins with occasional drifts", transitions: "cut, crossfade", grade: "none")

    /// A brand's motion feel, transition palette and colour grade - a brand
    /// carries a LOOK, not just colours, which is what makes a different brand
    /// feel like a different video.
    static func styleBible(_ id: String, catalog: [String: [CatalogOption]] = OptionCatalog.bundled) -> StyleBible {
        let v = id.trimmingCharacters(in: .whitespaces)
        var base = styleDefaults[v] ?? styleFallback
        if let opt = preset(v, catalog: catalog) {
            if let m = opt.payload["motion"], !m.isEmpty { base.motion = m }
            if let t = opt.payload["transitions"], !t.isEmpty { base.transitions = t }
            if let g = opt.payload["grade"], !g.isEmpty { base.grade = g }
        }
        return base
    }

    /// The brand's accent colours as `#RRGGBB`, for captions and cards when the
    /// channel hasn't picked its own. `rgba(...)` payload values are ignored
    /// (those are panel fills, not accents).
    static func accentHex(_ id: String, _ key: String, catalog: [String: [CatalogOption]] = OptionCatalog.bundled) -> String? {
        guard let v = preset(id, catalog: catalog)?.payload[key]?.trimmingCharacters(in: .whitespaces),
              v.hasPrefix("#"), v.count == 7, v.dropFirst().allSatisfy(\.isHexDigit) else { return nil }
        return v.uppercased()
    }
}

// MARK: - Prompt inputs

struct CreativeInputs: Equatable {
    var topic: String
    var contentShape = "documentary"
    var visualTone = "documentary_cinematic"
    var styleLock = ""
    var brandPreset = ""
    var formLabel = "Short-form (9:16)"
    var aspectRatio = "9:16"
    var durationSeconds = 30
}

// MARK: - Prompt builders

enum CreativePrompts {
    /// "30 seconds" / "1 minute" / "3 minutes" (plural from 120s, like the PC).
    static func durationPhrase(_ seconds: Int) -> String {
        guard seconds >= 60 else { return "\(seconds) seconds" }
        let minutes = Int((Double(seconds) / 60).rounded(.toNearestOrEven)) // Python round(), like the PC
        return "\(minutes) minute\(seconds >= 120 ? "s" : "")"
    }

    // MARK: Brief

    static func brief(_ i: CreativeInputs, personalities: [PersonalityPreset] = Personality.all) -> String {
        let preset = Personality.preset(named: i.contentShape, in: personalities)
        let label = preset.description.isEmpty ? preset.name : preset.description
        let tone = Brand.resolveVisualTone(i.visualTone)
        let toneSection = tone.isEmpty ? "" : "\nVisual tone: \(tone)"
        return """
        You are a creative director setting the CREATIVE DIRECTION for a short \(durationPhrase(i.durationSeconds)) video on this topic: "\(i.topic)"
        Content personality: \(label)\(toneSection)
        Do NOT plan individual shots, beats, per-scene roles, or visual intent — that happens later, after the script is written.
        The writer (next phase) will mark rhetorical QUESTIONS inline so they can be emphasised on screen — keep the emotional_arc and angle punchy enough to give them strong hooks to mark.
        Decide only the high-level creative intention the writer will follow:
        - emotional_arc: one sentence describing the viewer's journey hook→tension→payoff→cta
        - tone: a short phrase for the overall tone/energy of the whole video (e.g. "urgent and investigative", "warm and playful")
        - angle: the specific angle or thesis this video takes on the topic — what makes THIS telling of it land

        Return ONLY a JSON object, no preamble, no markdown fences:
        {
          "emotional_arc": "<string>",
          "tone": "<string>",
          "angle": "<string>",
          "notes": "<optional one-line rationale, logged not rendered>"
        }
        """
    }

    struct Brief: Equatable {
        var emotionalArc = ""
        var tone = ""
        var angle = ""
        var notes = ""
        var isUsable: Bool { !emotionalArc.isEmpty }

        /// The `CREATIVE DIRECTION (...)` block the narration prompt receives.
        /// Phrasing carried over from the PC verbatim.
        var directionBlock: String {
            var lines = ["CREATIVE DIRECTION (from the brief — follow this intent, you decide the scene-by-scene execution):"]
            if !emotionalArc.isEmpty { lines.append("Emotional arc: \(emotionalArc)") }
            if !tone.isEmpty { lines.append("Tone: \(tone)") }
            if !angle.isEmpty { lines.append("Angle: \(angle)") }
            return lines.count > 1 ? lines.joined(separator: "\n") : ""
        }

        /// What is stored on the project (and shown to the person): the same
        /// three lines, without the instruction header.
        var displayText: String {
            var lines: [String] = []
            if !emotionalArc.isEmpty { lines.append("Emotional arc: \(emotionalArc)") }
            if !tone.isEmpty { lines.append("Tone: \(tone)") }
            if !angle.isEmpty { lines.append("Angle: \(angle)") }
            return lines.joined(separator: "\n")
        }
    }

    /// Reads the model's brief JSON. Nil when there is no usable
    /// `emotional_arc` - the PC treats that as a failed generation ("a brief
    /// with no arc is useless direction"), and so does the caller.
    static func parseBrief(_ raw: String) -> Brief? {
        guard let json = Pipeline.extractJSONObject(raw),
              let obj = (try? JSONSerialization.jsonObject(with: Data(json.utf8))) as? [String: Any] else { return nil }
        func str(_ k: String) -> String { ((obj[k] as? String) ?? "").trimmingCharacters(in: .whitespacesAndNewlines) }
        let brief = Brief(emotionalArc: str("emotional_arc"), tone: str("tone"), angle: str("angle"), notes: str("notes"))
        return brief.isUsable ? brief : nil
    }

    // MARK: Narration

    private static let wordsPerSecondMin = 2.0
    private static let wordsPerSecondMax = 2.5

    /// The spoken-length target for a duration. Calibrated so 30s gives the
    /// PC's historical 60-75 words; a fixed budget was the actual cause of the
    /// 2m and 4m presets collapsing to ~30s.
    static func wordBudget(seconds: Int) -> (min: Int, max: Int) {
        (max(1, Int((Double(seconds) * wordsPerSecondMin).rounded())),
         max(1, Int((Double(seconds) * wordsPerSecondMax).rounded())))
    }

    private static let pauseFrequencyGuide: [String: String] = [
        "low": "SPARINGLY — only at the one or two most dramatic beats in the whole script",
        "high": "LIBERALLY — at every punchy, dramatic beat, several times across the script",
    ]

    static func pauseSection(frequency: String = "medium") -> String {
        let guide = pauseFrequencyGuide[frequency]
            ?? "occasionally — at natural dramatic beats, roughly once every couple of scenes"
        return """

        === DYNAMIC PAUSES (human delivery) ===
        Insert the literal marker [pause] INLINE inside Script: lines where a spoken dramatic pause
        would land an emotional beat — before a reveal, after a bold claim, or to let a word breathe.
        Place [pause] BETWEEN words, e.g. "These inventions are LITERALLY [pause] 2000 years old" or
        "Yes [pause] that is true". Use them \(guide). Never put a [pause] at the very start or end
        of a scene, and never two in a row. They are SILENT beats — the narration must still read
        naturally and the word count above counts every real word (ignore the markers).

        """
    }

    /// Only the QUESTION half of the PC's text-marker block. The other half asks
    /// for `[[focus]]` words, which the PC feeds to its Director II; nothing on
    /// iOS reads them yet, and asking a model for cues nothing consumes only
    /// spends tokens - so they are left out until something does.
    static let questionMarkerSection = """

    === TEXT MARKERS (questions) ===
    Wrap any short rhetorical QUESTION or punchy hook in DOUBLE PARENTHESES, e.g.
    "((But why?))" or "((How is this even possible?))". These are isolated for emphasis cards, so
    only wrap genuine brief questions/hooks — never a whole sentence. The narration still reads
    naturally — the parentheses are invisible cues, not spoken, and the word count ignores them.

    """

    static func narration(_ i: CreativeInputs, extraContext: String = "", pauseFrequency: String? = "medium",
                          includeQuestionMarkers: Bool = true,
                          personalities: [PersonalityPreset] = Personality.all) -> String {
        let preset = Personality.preset(named: i.contentShape, in: personalities)
        let tone = Brand.resolveVisualTone(i.visualTone)
        let toneLine = tone.isEmpty ? "" : "\nTone & style: \(tone) — apply this to the narration voice."
        let budget = wordBudget(seconds: i.durationSeconds)
        let pause = pauseFrequency.map { pauseSection(frequency: $0) } ?? ""
        let markers = includeQuestionMarkers ? questionMarkerSection : ""
        let extra = extraContext.isEmpty ? "" : "\n\(extraContext)"
        return """
        \(preset.body)

        === SYSTEM RULES ===\(extra)

        === TASK ===
        Topic: "\(i.topic)"
        Video format: \(i.formLabel)
        Total spoken length: \(durationPhrase(i.durationSeconds))\(toneLine)

        Write the NARRATION ONLY — the exact words a narrator will speak, start to finish.

        This is prose, not a storyboard. Do NOT include any of:
        - scene numbers, timestamps, or [MM:SS] markers
        - image prompts, shot descriptions, or art direction
        - headings, labels, bullet points, or any other structural scaffold

        Requirements:
        - Total length: \(budget.min) to \(budget.max) words.
        - Hook in the very first sentence — no warmup, no throat-clearing.
        - It must read as ONE continuous piece with a real throughline: an idea that builds,
          turns, and pays off. NOT a stack of related facts that happen to share a topic.
        - VARY THE SENTENCE RHYTHM ON PURPOSE. A reveal or a punchline gets a short, blunt line.
          A mechanism or a piece of context gets a longer, flowing one. The sentences must NOT all
          come out the same length — that uniformity is the single most common failure here, and
          it is the thing you are being asked to avoid.
        - Write for the ear. This is read aloud, so prefer plain spoken phrasing over
          written-essay constructions.
        \(pause)\(markers)
        Return ONLY the narration text. Zero preamble, commentary, markdown, or code fences.
        """
    }

    // MARK: Scene split

    static func sceneCountRule(min: Int, max: Int, exact: Int = 0) -> String {
        if exact > 0 { return "Produce EXACTLY \(exact) scenes — no more, no fewer. This count is required." }
        return """
        Decide the number of scenes from the CONTENT, not from arithmetic. Cut where the narration
        naturally turns — a new idea, a reveal, a shift in subject. It must land between \(min) and
        \(max) scenes. A dense explanatory passage may run long in one scene; a punchy reveal may be
        a scene on its own. Do NOT make every scene the same length — uneven scenes are correct here.
        """
    }

    struct PoolEntry: Equatable { var index: Int; var description: String }

    static func segmentation(_ i: CreativeInputs, narration: String, minScenes: Int = 3, maxScenes: Int = 15,
                             pool: [PoolEntry] = [], recycleRatio: Int = 0) -> String {
        let tone = Brand.resolveVisualTone(i.visualTone)
        let lock = i.styleLock.trimmingCharacters(in: .whitespacesAndNewlines)
        let styleTail = lock.isEmpty ? "" : " Append this style tag verbatim at the end: \(lock)"
        let countRule = sceneCountRule(min: minScenes, max: maxScenes)
        let styleGuide = tone.isEmpty ? "No specific visual style constraints." : "Visual tone: \(tone)"

        let usePool = !pool.isEmpty && recycleRatio > 0
        let all = usePool && recycleRatio >= 100

        var poolSection = ""
        var jobLine = "Below is a finished narration. Split it into scenes and write one image prompt per scene."
        var imageRule = """
        - an Image line: ONE concrete visual subject suited to a \(i.aspectRatio) frame. Describe what
          is literally seen — no metaphors, no text-in-image.\(styleTail)

        """
        var poolRules = ""
        var imageHint = "<the image prompt>"

        if usePool {
            let lines = pool.map { "#\($0.index): \($0.description)" }.joined(separator: "\n")
            poolSection = "\n\n=== RE-USABLE IMAGE POOL (#k : description) ===\n\(lines)\n"
            imageHint = "<the image prompt, or [RECYCLE #k]>"
            poolRules = """
            - Only use image numbers (#k) that appear in the pool above.
            - The same pool image MAY serve more than one scene; each scene gets at most one.

            """
            if all {
                jobLine = "Below is a finished narration. Split it into scenes and assign each scene a re-usable image."
                imageRule = """
                - an Image line: ALWAYS the token [RECYCLE #k] naming the pool image that fits this scene best.
                  This video re-uses ONLY pooled images — no new images will be generated — so EVERY scene must
                  get a [RECYCLE #k]. When nothing fits well, still pick the nearest. Never write a description.

                """
            } else {
                imageRule = """
                - an Image line, which is ONE of:
                    [RECYCLE #k]  — when a pool image GENUINELY fits this scene. Re-using a good match saves
                                    generation, but a FORCED match looks worse than a fresh image, so only
                                    re-use on a strong, obvious fit. Re-use at most \(recycleRatio)% of the scenes.
                    a prompt      — otherwise: ONE concrete visual subject suited to a \(i.aspectRatio) frame,
                                    describing what is literally seen. No metaphors, no text-in-image.\(styleTail)
                  Decide this scene by scene, in order, from the first scene to the last.

                """
                jobLine = "Below is a finished narration. Split it into scenes, re-use pool images where they genuinely fit, and write an image prompt for every other scene."
            }
        }

        return """
        === VISUAL STYLE GUIDE ===
        \(styleGuide)\(poolSection)

        === TASK ===
        Topic: "\(i.topic)"
        Image aspect ratio: \(i.aspectRatio)

        \(jobLine)

        \(countRule)

        ABSOLUTE RULE — the narration is FINAL:
        - Reproduce every word EXACTLY as written, in the original order.
        - Do NOT reword, rewrite, shorten, expand, summarise, or fix anything.
        - Do NOT add or drop a single word.
        - Preserve any [pause] and ((double parenthesis)) markers exactly where
          they appear. They are delivery cues, not typos.
        - Concatenating your scene texts in order must reproduce the narration verbatim.

        For each scene also write:
        \(imageRule)- a Label: one or two words naming the scene's job (Hook, Reveal, Context, Payoff, ...).
        \(poolRules)
        Output EXACTLY this structure for every scene, in order, and nothing else:

        @@SCENE@@
        <the verbatim narration words for this scene>
        @@IMAGE@@
        \(imageHint)
        @@LABEL@@
        <the label>

        No preamble, no commentary, no markdown, no code fences, no numbering.

        === NARRATION TO SPLIT ===
        \(narration)
        """
    }

    struct SceneBlock: Equatable { var text: String; var imagePrompt: String; var label: String }

    /// Parses the `@@SCENE@@ / @@IMAGE@@ / @@LABEL@@` reply. Tolerant, like the
    /// PC's: a block with no `@@IMAGE@@` is skipped, a missing label becomes
    /// "Scene", and a reply with no blocks gives `[]` (the caller decides what
    /// that means).
    static func parseSceneBlocks(_ raw: String) -> [SceneBlock] {
        var lines = raw.components(separatedBy: "\n").map { $0.trimmingCharacters(in: CharacterSet(charactersIn: "\r")) }
        // A single ``` fence wrapped around the whole reply.
        if let first = lines.firstIndex(where: { !$0.trimmingCharacters(in: .whitespaces).isEmpty }),
           lines[first].trimmingCharacters(in: .whitespaces).hasPrefix("```") {
            lines.remove(at: first)
            if let last = lines.lastIndex(where: { !$0.trimmingCharacters(in: .whitespaces).isEmpty }),
               lines[last].trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                lines.remove(at: last)
            }
        }

        enum Section { case none, text, image, label }
        var blocks: [SceneBlock] = []
        var section = Section.none
        var text: [String] = [], image: [String] = [], label: [String] = []
        var sawImage = false

        func collapse(_ ls: [String]) -> String {
            ls.map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }.joined(separator: " ")
                .replacingOccurrences(of: "\\s{2,}", with: " ", options: .regularExpression)
                .trimmingCharacters(in: .whitespaces)
        }
        func flush() {
            defer { text = []; image = []; label = []; sawImage = false }
            guard sawImage else { return }
            let t = collapse(text)
            guard !t.isEmpty else { return }
            let l = label.map { $0.trimmingCharacters(in: .whitespaces) }.first { !$0.isEmpty } ?? ""
            blocks.append(SceneBlock(text: t, imagePrompt: collapse(image), label: l.isEmpty ? "Scene" : l))
        }

        for line in lines {
            switch line.trimmingCharacters(in: .whitespaces).uppercased() {
            case "@@SCENE@@": flush(); section = .text
            case "@@IMAGE@@" where section != .none: sawImage = true; section = .image
            case "@@LABEL@@" where section != .none: section = .label
            default:
                switch section {
                case .none: break
                case .text: text.append(line)
                case .image: image.append(line)
                case .label: label.append(line)
                }
            }
        }
        flush()
        return blocks
    }

    // MARK: Director I (story map)

    static func director1(_ i: CreativeInputs, sceneTexts: [String], minImages: Int = 3, maxImages: Int = 160) -> String {
        let numbered = sceneTexts.enumerated().map { "[\($0.offset + 1)] \"\($0.element)\"" }.joined(separator: "\n")
        let n = sceneTexts.count
        return """
        You are the STRUCTURE DIRECTOR for a short narrated video built from still images.
        The script below is FINAL. Your job is to decide its VISUAL STRUCTURE: how many
        distinct images the story needs, how each scene should feel, and what kind of image
        each scene wants.

        Personality: \(i.contentShape). Total length: ~\(i.durationSeconds) seconds (\(i.formLabel)).
        The script has \(n) scene(s). ONE image per scene is the default and almost
        always correct — the scene count reflects the content's own natural pacing, so scenes
        are intentionally uneven in length.

        SCRIPT (numbered narration lines):
        \(numbered)

        Decide:
        - image_count: how many distinct images tell THIS story. KEEP THIS EQUAL to \(n)
          (one image per scene) unless the script is CLEARLY mis-sized for its length — only then
          adjust. Must be between \(minImages) and \(maxImages). Do NOT change it for stylistic
          preference; a changed count triggers a full script rewrite, so only diverge from
          \(n) when the script is genuinely broken.
        - scenes: one entry per EXISTING script scene (exactly \(n), indexed 1..\(n)), each with:
            - scene: the scene number
            - role: hook | tension | reveal | proof | example | pause | hit | cta
            - beat: none | HIT | PAUSE | ZOOM | SLOWDOWN  (HIT = hard punch; PAUSE = held breath;
              ZOOM = one key word deserves zoom emphasis; SLOWDOWN = lower energy; none = normal)
            - visual_intent: BACKGROUND | ASSET | POP
                BACKGROUND = full-frame scene backdrop (most scenes)
                ASSET = a floating graphic/icon/diagram composited over a clean background
                POP = a bold full-frame impact graphic that cuts in hard
        - zoom_words: array of {scene, word} — for each ZOOM-beat scene, the single word to emphasize.

        Return ONLY a JSON object, no preamble, no markdown fences:
        {"version":1,"image_count":<int>,
         "scenes":[{"scene":1,"role":"hook","beat":"HIT","visual_intent":"POP"}],
         "zoom_words":[{"scene":<int>,"word":"<string>"}],
         "notes":"one line on your structural reasoning"}
        """
    }

    // MARK: Style bible (Director II)

    /// The "VIDEO STYLE" block Director II opens with: personality, visual tone,
    /// the brief's emotional arc, and the brand's motion feel, transitions and
    /// grade - so the motion plan matches the video's identity rather than
    /// being chosen in a vacuum.
    static func styleBible(_ i: CreativeInputs, emotionalArc: String = "",
                           personalities: [PersonalityPreset] = Personality.all) -> String {
        let preset = Personality.preset(named: i.contentShape, in: personalities)
        let tone = Brand.resolveVisualTone(i.visualTone)
        let brandLabel = Brand.label(i.brandPreset)
        var brandSection = ""
        if !brandLabel.isEmpty {
            let bible = Brand.styleBible(i.brandPreset)
            brandSection = "\n- brand: \(brandLabel)\n- brand motion feel: \(bible.motion)\n- brand transitions: \(bible.transitions)\n- brand grade: \(bible.grade)"
        }
        return """
        VIDEO STYLE (the creative bible — keep the whole piece feeling consistent with this):
        - personality: \(preset.description.isEmpty ? preset.name : preset.description)
        - visual tone: \(tone)
        - emotional arc: \(emotionalArc)\(brandSection)
        """
    }
}

// MARK: - Story map (Director I's result)

struct StoryMapScene: Codable, Hashable {
    var scene: Int
    var role: String
    var beat: String
    var visualIntent: String
}

struct StoryMapZoomWord: Codable, Hashable {
    var scene: Int
    var word: String
}

struct StoryMap: Codable, Hashable {
    var imageCount: Int
    var scenes: [StoryMapScene]
    var zoomWords: [StoryMapZoomWord]
    var notes: String

    static let validRoles: Set<String> = ["hook", "tension", "reveal", "proof", "example", "pause", "hit", "cta"]
    static let validBeats: Set<String> = ["none", "HIT", "PAUSE", "ZOOM", "SLOWDOWN"]
    static let validIntents: Set<String> = ["BACKGROUND", "ASSET", "POP"]
    static let minImages = 3
    static let maxImages = 160

    /// One safe default entry per scene: what a failed or unusable plan
    /// degrades to, so nothing downstream ever sees a scene with no entry.
    static func neutral(sceneCount: Int) -> StoryMap {
        StoryMap(imageCount: sceneCount,
                 scenes: (0..<sceneCount).map { StoryMapScene(scene: $0 + 1, role: "", beat: "none", visualIntent: "BACKGROUND") },
                 zoomWords: [], notes: "")
    }

    /// Reads Director I's reply, coercing every field to a known value (the
    /// PC's `_normalize_*` rules). Never throws: a reply with no usable
    /// structure becomes `neutral`.
    static func parse(_ raw: String, sceneCount: Int) -> StoryMap {
        guard let json = Pipeline.extractJSONObject(raw),
              let obj = (try? JSONSerialization.jsonObject(with: Data(json.utf8))) as? [String: Any] else {
            return neutral(sceneCount: sceneCount)
        }
        func int(_ v: Any?) -> Int? {
            if let i = v as? Int { return i }
            if let d = v as? Double { return Int(d) }
            if let s = v as? String { return Int(s.trimmingCharacters(in: .whitespaces)) }
            return nil
        }
        // The lower bound must never push image_count ABOVE the scenes that
        // exist (a 2-scene script must not be "inflated" to 3).
        let minBound = sceneCount > 0 ? min(minImages, sceneCount) : minImages
        let count = max(minBound, min(maxImages, int(obj["image_count"]) ?? sceneCount))

        let scenes: [StoryMapScene] = ((obj["scenes"] as? [Any]) ?? []).compactMap { entry in
            guard let e = entry as? [String: Any], let n = int(e["scene"]), n >= 1 else { return nil }
            var role = ((e["role"] as? String) ?? "").trimmingCharacters(in: .whitespaces).lowercased()
            if !validRoles.contains(role) { role = "" }
            var beat = ((e["beat"] as? String) ?? "none").trimmingCharacters(in: .whitespaces)
            beat = beat.lowercased() == "none" ? "none" : beat.uppercased()
            if !validBeats.contains(beat) { beat = "none" }
            var intent = ((e["visual_intent"] as? String) ?? "BACKGROUND").trimmingCharacters(in: .whitespaces).uppercased()
            if !validIntents.contains(intent) { intent = "BACKGROUND" }
            return StoryMapScene(scene: n, role: role, beat: beat, visualIntent: intent)
        }
        if scenes.isEmpty { return neutral(sceneCount: sceneCount) }

        let zoom: [StoryMapZoomWord] = ((obj["zoom_words"] as? [Any]) ?? []).compactMap { entry in
            guard let e = entry as? [String: Any], let n = int(e["scene"]), n >= 1,
                  let w = (e["word"] as? String)?.trimmingCharacters(in: .whitespaces), !w.isEmpty else { return nil }
            return StoryMapZoomWord(scene: n, word: w)
        }
        let notes = ((obj["notes"] as? String) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return StoryMap(imageCount: count, scenes: scenes, zoomWords: zoom, notes: notes)
    }

    /// One line per scene for Director II's prompt, e.g. `[3] role=reveal beat=HIT intent=POP`.
    /// Empty when the map carries no information (every entry neutral).
    var directorHints: String {
        let lines = scenes.sorted { $0.scene < $1.scene }.compactMap { s -> String? in
            guard !s.role.isEmpty || s.beat != "none" || s.visualIntent != "BACKGROUND" else { return nil }
            var parts: [String] = []
            if !s.role.isEmpty { parts.append("role=\(s.role)") }
            if s.beat != "none" { parts.append("beat=\(s.beat)") }
            if s.visualIntent != "BACKGROUND" { parts.append("intent=\(s.visualIntent)") }
            return "[\(s.scene)] " + parts.joined(separator: " ")
        }
        return lines.isEmpty ? "" : lines.joined(separator: "\n")
    }
}

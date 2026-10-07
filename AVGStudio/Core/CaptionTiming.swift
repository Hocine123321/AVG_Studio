import Foundation

// Caption and scene timing, rebuilt.
//
// WHY. Captions and scene changes are only as good as the word timings behind
// them, and the old code had three weaknesses that all show up as "the captions
// drift":
//
//  1. Scene boundaries were estimated from character counts over the WHOLE
//     audio, ignoring the real silences the Voice phase puts in (every [pause],
//     every trimmed breath). One pause shifted every later scene.
//  2. Even with real AssemblyAI word times, scenes still used that text-length
//     guess, so pictures changed at the wrong moment while the captions were
//     right - or the reverse.
//  3. Captions showed AssemblyAI's spelling ("2,000", a misheard name) instead
//     of the script's own words, and grouped cues across sentences and pauses.
//
// THE FIX, in layers, each testable on its own and each falling back to the one
// below it:
//
//   VoicePlan   - the Voice phase's segmentation, as ONE pure function shared
//                 with Captions, so both agree exactly which words are in which
//                 recorded segment.
//   anchored    - the Voice phase records where each segment really sits in the
//                 final audio; words are spread inside THAT span (by a
//                 syllable-and-punctuation weight, not raw characters), so the
//                 error can never exceed one segment.
//   align       - when a transcript exists, the SCRIPT's words are matched to
//                 its words (longest common subsequence on normalised text) and
//                 take its real times; words the transcript missed or spelled
//                 differently are interpolated between their neighbours.
//   boundaries  - scene changes come from the first spoken word of each scene,
//                 a hair early so the picture is there as the word starts.
//   cues        - grouped per sentence and per breath, never across a pause.

// MARK: - The voice plan

struct VoicePlan: Equatable {
    /// One spoken take: the text sent to the voice, and the silence before it.
    struct Run: Equatable {
        var text: String
        var gapBefore: Double
        /// "chapter" when the gap is a chapter card's window; nil for a [pause].
        var gapKind: String?
    }

    /// One spoken word, and where it belongs.
    struct Word: Equatable {
        var text: String
        /// Index into the scenes array.
        var scene: Int
        /// Index into `runs`.
        var run: Int
    }

    var runs: [Run]
    var words: [Word]

    /// Splits the scenes' narration into spoken takes.
    ///
    /// A take ends at a `[pause]` marker (when dynamic pauses are on) and at the
    /// start of a chapter - and nowhere else, so ordinary scene boundaries do not
    /// chop the voice into more (paid, prosody-breaking) calls than before.
    /// `chapterGaps` maps a scene index to the seconds its chapter card lasts.
    static func make(sceneTexts: [String], dynamicPauses: Bool, pauseSeconds: Double,
                     chapterGaps: [Int: Double] = [:]) -> VoicePlan {
        var runs: [Run] = []
        var words: [Word] = []
        var current: [Word] = []
        var gap = 0.0
        var kind: String?

        func close() {
            guard !current.isEmpty else { return }
            let index = runs.count
            runs.append(Run(text: current.map(\.text).joined(separator: " "), gapBefore: gap, gapKind: kind))
            for var w in current { w.run = index; words.append(w) }
            current = []
            gap = 0
            kind = nil
        }

        for (i, text) in sceneTexts.enumerated() {
            if let seconds = chapterGaps[i] {
                close()
                gap = max(gap, seconds)
                kind = "chapter"
            }
            let pieces = dynamicPauses ? splitKeepingEmpties(text) : [text]
            for (j, piece) in pieces.enumerated() {
                if j > 0 {
                    close()
                    // A pause before anything has been said is ignored (it would
                    // only add dead air at the start), and never stacks on a chapter.
                    if kind != "chapter", !(runs.isEmpty && current.isEmpty), pauseSeconds > 0 {
                        gap = max(gap, pauseSeconds)
                    }
                }
                for w in cleaned(piece).split(whereSeparator: \.isWhitespace) {
                    current.append(Word(text: String(w), scene: i, run: 0))
                }
            }
        }
        close()
        return VoicePlan(runs: runs, words: words)
    }

    // The same marker clean-up as `Pipeline.spoken`, kept local so this file
    // stays free of the main actor.
    private static let pauseRegex = try! NSRegularExpression(pattern: #"\s*\[\s*pause\s*\]\s*"#, options: [.caseInsensitive])

    static func splitKeepingEmpties(_ text: String) -> [String] {
        let marked = pauseRegex.stringByReplacingMatches(in: text, range: NSRange(text.startIndex..., in: text), withTemplate: "\u{1}")
        return marked.components(separatedBy: "\u{1}")
    }

    static func cleaned(_ text: String) -> String {
        var t = pauseRegex.stringByReplacingMatches(in: text, range: NSRange(text.startIndex..., in: text), withTemplate: " ")
        for m in ["[[", "]]", "((", "))"] { t = t.replacingOccurrences(of: m, with: "") }
        return t.split(whereSeparator: \.isWhitespace).joined(separator: " ")
    }
}

// MARK: - Timing

enum CaptionTiming {
    struct Span: Equatable { var start: Double; var end: Double }

    /// Fraction of a second the picture changes BEFORE the first word of a new
    /// scene, so the new image is already up as the narrator starts on it.
    static let sceneLead = 0.10
    static let minWordSeconds = 0.06

    // MARK: Weights

    /// How long a word takes to say, relative to others: syllables, not letters
    /// ("strength" is one beat, "ubiquitous" four), with numbers counted as a
    /// few syllables. `gap` is the pause punctuation adds AFTER the word.
    static func weight(_ text: String) -> (word: Double, gap: Double) {
        let letters = text.lowercased().filter { $0.isLetter || $0.isNumber }
        var syllables = 0.0
        var inVowel = false
        var digitRun = 0
        for ch in letters {
            if ch.isNumber {
                digitRun += 1
                inVowel = false
                continue
            }
            if digitRun > 0 { syllables += digitSyllables(digitRun); digitRun = 0 }
            let vowel = "aeiouy".contains(ch)
            if vowel && !inVowel { syllables += 1 }
            inVowel = vowel
        }
        if digitRun > 0 { syllables += digitSyllables(digitRun) }
        let word = max(1, syllables) + 0.35 // a floor: even "a" takes a moment

        var gap = 0.0
        var tail = Substring(text)
        while let last = tail.last, "\"')]}”’".contains(last) { tail = tail.dropLast() }
        if let last = tail.last {
            switch last {
            case ".", "?", "!": gap = 1.4
            case "…": gap = 1.1
            case ";", ":": gap = 0.9
            case "—", "–": gap = 0.7
            case ",": gap = 0.55
            default: break
            }
        }
        return (word, gap)
    }

    private static func digitSyllables(_ digits: Int) -> Double { max(2, Double(digits) * 0.9) }

    /// Spreads `words` across `span`: each takes its share of the time and the
    /// punctuation pauses sit between them (none after the last, which is where
    /// the span ends).
    static func estimate(_ words: [String], in span: Span) -> [Span] {
        guard !words.isEmpty else { return [] }
        let w = words.map(weight)
        var units = 0.0
        for (i, x) in w.enumerated() { units += x.word + (i < w.count - 1 ? x.gap : 0) }
        guard units > 0, span.end > span.start else {
            return words.map { _ in Span(start: span.start, end: span.start) }
        }
        let per = (span.end - span.start) / units
        var t = span.start
        var out: [Span] = []
        for (i, x) in w.enumerated() {
            let end = t + x.word * per
            out.append(Span(start: t, end: i == w.count - 1 ? span.end : end))
            t = end + (i < w.count - 1 ? x.gap * per : 0)
        }
        return out
    }

    // MARK: Anchoring to the recorded audio

    /// Times for every word of `plan`. When the Voice phase's recorded segments
    /// match the plan's runs, each run's words are spread inside that run's REAL
    /// span; otherwise (an older project, or a mismatch) the whole audio is the
    /// one span - the old behaviour, but with the better weights.
    static func anchored(plan: VoicePlan, segments: [AudioSegment]?, duration: Double) -> [Span] {
        guard !plan.words.isEmpty else { return [] }
        var out = [Span](repeating: Span(start: 0, end: 0), count: plan.words.count)
        let usable = segments.map { $0.count == plan.runs.count } ?? false
        for run in plan.runs.indices {
            let idx = plan.words.indices.filter { plan.words[$0].run == run }
            let span: Span
            if usable, let seg = segments?[run] {
                span = Span(start: seg.start, end: max(seg.start, min(seg.end, duration)))
            } else {
                span = Span(start: 0, end: duration)
            }
            let timed = estimate(idx.map { plan.words[$0].text }, in: span)
            for (k, i) in idx.enumerated() { out[i] = timed[k] }
        }
        if !usable {
            // One span for everything: re-spread the whole list, not run by run.
            return estimate(plan.words.map(\.text), in: Span(start: 0, end: duration))
        }
        return out
    }

    // MARK: Aligning to a transcript

    static func key(_ text: String) -> String {
        text.lowercased().filter { $0.isLetter || $0.isNumber }
    }

    /// For each script word, the index of the transcript word it matches, or
    /// nil. A longest-common-subsequence over normalised text: the largest set
    /// of words that appear in both, in the same order.
    static func align(script: [String], transcript: [String]) -> [Int?] {
        let a = script.map(key), b = transcript.map(key)
        let n = a.count, m = b.count
        guard n > 0, m > 0 else { return [Int?](repeating: nil, count: n) }
        let width = m + 1
        var table = [UInt16](repeating: 0, count: (n + 1) * width)
        for i in stride(from: n - 1, through: 0, by: -1) {
            for j in stride(from: m - 1, through: 0, by: -1) {
                table[i * width + j] = (!a[i].isEmpty && a[i] == b[j])
                    ? table[(i + 1) * width + j + 1] + 1
                    : max(table[(i + 1) * width + j], table[i * width + j + 1])
            }
        }
        var out = [Int?](repeating: nil, count: n)
        var i = 0, j = 0
        while i < n, j < m {
            if !a[i].isEmpty, a[i] == b[j] {
                out[i] = j; i += 1; j += 1
            } else if table[(i + 1) * width + j] >= table[i * width + j + 1] {
                i += 1
            } else {
                j += 1
            }
        }
        return out
    }

    /// Script word times taken from the transcript where they matched, and
    /// interpolated (by the same weights) between their neighbours where they
    /// did not. `matched` is the fraction that matched; below `minTrust` the
    /// transcript is not believed and `fallback` is returned unchanged.
    static func merge(words: [String], transcript: [WordTiming], fallback: [Span], duration: Double,
                      minTrust: Double = 0.5) -> (spans: [Span], matched: Double) {
        let match = align(script: words, transcript: transcript.map(\.text))
        let hits = match.compactMap { $0 }.count
        let ratio = words.isEmpty ? 0 : Double(hits) / Double(words.count)
        guard ratio >= minTrust, fallback.count == words.count else { return (fallback, ratio) }

        var out = fallback
        for (i, m) in match.enumerated() {
            if let m { out[i] = Span(start: transcript[m].start, end: transcript[m].end) }
        }
        var i = 0
        while i < words.count {
            guard match[i] == nil else { i += 1; continue }
            var j = i
            while j < words.count, match[j] == nil { j += 1 }
            let left = i > 0 ? out[i - 1].end : (fallback[i].start)
            let right = j < words.count ? out[j].start : max(left, fallback[j - 1].end)
            let filled = estimate(Array(words[i..<j]), in: Span(start: left, end: max(left, right)))
            for (k, s) in filled.enumerated() { out[i + k] = s }
            i = j
        }
        return (clean(out, duration: duration), ratio)
    }

    /// Times must only move forward and every word needs a little duration.
    static func clean(_ spans: [Span], duration: Double) -> [Span] {
        var out: [Span] = []
        var floor = 0.0
        for s in spans {
            let start = min(max(s.start, floor), duration)
            let end = min(max(s.end, start + minWordSeconds), max(duration, start))
            out.append(Span(start: start, end: end))
            floor = start
        }
        return out
    }

    // MARK: Scenes

    /// Where each scene starts, plus the end: `sceneCount + 1` times. A scene
    /// begins at its first spoken word (a little early), the first at 0 and the
    /// last scene ends with the audio. A scene with no spoken words gets zero
    /// length rather than stealing time.
    static func sceneBoundaries(words: [VoicePlan.Word], spans: [Span], sceneCount: Int, duration: Double,
                                lead: Double = sceneLead) -> [Double] {
        guard sceneCount > 0 else { return [] }
        var firstStart = [Double?](repeating: nil, count: sceneCount)
        for (w, s) in zip(words, spans) where w.scene < sceneCount && firstStart[w.scene] == nil {
            firstStart[w.scene] = s.start
        }
        var filled = [Double](repeating: duration, count: sceneCount)
        var next = duration
        for i in stride(from: sceneCount - 1, through: 0, by: -1) {
            if let s = firstStart[i] { next = s }
            filled[i] = next
        }
        var bounds: [Double] = [0]
        for i in 1..<max(sceneCount, 1) {
            bounds.append(min(duration, max(bounds[i - 1], filled[i] - lead)))
        }
        bounds.append(duration)
        return bounds
    }

    // MARK: Cues

    private static func endsSentence(_ text: String) -> Bool {
        var tail = Substring(text)
        while let last = tail.last, "\"')]}”’".contains(last) { tail = tail.dropLast() }
        guard let last = tail.last else { return false }
        return ".?!…".contains(last)
    }

    /// Groups timed words into caption cues. A cue never crosses a sentence end,
    /// a recorded segment (a pause), or a silent gap longer than `maxGap`, and
    /// never exceeds `maxWords`. Each cue then lingers a moment (never into the
    /// next one, and never less than `minSeconds`), so a one-word cue is readable.
    static func cues(words: [VoicePlan.Word], spans: [Span], maxWords: Int,
                     maxGap: Double = 0.40, tail: Double = 0.18, minSeconds: Double = 0.35,
                     suppressing windows: [(Double, Double)] = []) -> [CaptionCue] {
        let limit = max(1, maxWords)
        var groups: [[Int]] = []
        var current: [Int] = []
        for i in words.indices {
            if let prev = current.last {
                let gap = spans[i].start - spans[prev].end
                let breaks = current.count >= limit
                    || words[i].run != words[prev].run
                    || gap > maxGap
                    || endsSentence(words[prev].text)
                if breaks { groups.append(current); current = [] }
            }
            current.append(i)
        }
        if !current.isEmpty { groups.append(current) }

        var cues: [CaptionCue] = []
        for (g, idx) in groups.enumerated() {
            guard let first = idx.first, let last = idx.last else { continue }
            let nextStart = g + 1 < groups.count ? spans[groups[g + 1][0]].start : Double.greatestFiniteMagnitude
            var end = max(spans[last].end, min(spans[last].end + tail, nextStart))
            end = max(end, min(spans[first].start + minSeconds, nextStart))
            end = min(end, nextStart)
            let timed = idx.map { WordTiming(text: words[$0].text, start: spans[$0].start, end: spans[$0].end) }
            if windows.contains(where: { overlaps(spans[first].start, end, $0.0, $0.1) }) { continue }
            cues.append(CaptionCue(start: spans[first].start, end: max(end, spans[first].start),
                                   text: timed.map(\.text).joined(separator: " "), words: timed))
        }
        return cues
    }

    private static func overlaps(_ s1: Double, _ e1: Double, _ s2: Double, _ e2: Double) -> Bool {
        s1 < e2 && s2 < e1
    }

    /// The window of every chapter card: the silence the Voice phase left for it.
    static func chapterWindows(_ segments: [AudioSegment]?) -> [(Double, Double)] {
        (segments ?? []).compactMap { seg in
            guard seg.gapKind == "chapter", seg.gapBefore > 0 else { return nil }
            return (max(0, seg.start - seg.gapBefore), seg.start)
        }
    }
}

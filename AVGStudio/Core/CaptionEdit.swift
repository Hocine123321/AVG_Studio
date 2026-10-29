import Foundation

// Fixing captions by hand.
//
// A caption is drawn word by word from `CaptionCue.words` (each with its own
// timing, so the highlight follows the voice), not from the cue's plain text.
// So fixing a misheard word means editing the words, and keeping their timing.
// Two edits, both pure:
//
//  - `setText`: retype one line. Same number of words: each keeps its own
//    timing, so only the spelling changes. A different number: the new words
//    share the line's time in proportion to their length - the highlight is
//    then approximate, which is the honest best without the audio.
//  - `replace`: change a whole word everywhere (a name the transcriber got
//    wrong), keeping its punctuation.

enum CaptionEdit {
    static func setText(_ cue: CaptionCue, to raw: String) -> CaptionCue {
        let text = raw.trimmed
        guard !text.isEmpty else { return cue }       // never blank out a caption
        var out = cue
        out.text = text
        guard !cue.words.isEmpty else { return out }  // a plain cue has only text

        let new = text.split(whereSeparator: { $0.isWhitespace }).map { String($0) }
        if new.count == cue.words.count {
            for i in new.indices { out.words[i].text = new[i] }
            return out
        }
        let from = cue.words.first?.start ?? cue.start
        let to = cue.words.last?.end ?? cue.end
        let length = max(0.01, to - from)
        let weight = Double(new.reduce(0) { $0 + max(1, $1.count) })
        var cursor = from
        var words: [WordTiming] = []
        for w in new {
            let share = length * Double(max(1, w.count)) / weight
            words.append(WordTiming(text: w, start: cursor, end: cursor + share))
            cursor += share
        }
        out.words = words
        return out
    }

    /// Replaces whole words equal to `find` (case-insensitive, ignoring the
    /// punctuation around them) and returns how many were changed.
    static func replace(_ find: String, with replacement: String, in cues: [CaptionCue]) -> (cues: [CaptionCue], count: Int) {
        let target = find.trimmed.lowercased()
        let new = replacement.trimmed
        guard !target.isEmpty, !new.isEmpty else { return (cues, 0) }
        var count = 0

        func swapped(_ word: String) -> String {
            let p = parts(word)
            guard p.core.lowercased() == target else { return word }
            count += 1
            return p.lead + new + p.trail
        }

        let out = cues.map { cue -> CaptionCue in
            var c = cue
            if c.words.isEmpty {
                let pieces = c.text.split(separator: " ", omittingEmptySubsequences: false).map { String($0) }
                c.text = pieces.map { swapped($0) }.joined(separator: " ")
            } else {
                for i in c.words.indices { c.words[i].text = swapped(c.words[i].text) }
                c.text = c.words.map { $0.text }.joined(separator: " ")
            }
            return c
        }
        return (out, count)
    }

    /// "“Hello,”" -> ("“", "Hello", ",”"): the letters and digits, and what surrounds them.
    static func parts(_ word: String) -> (lead: String, core: String, trail: String) {
        let chars = Array(word)
        var s = 0
        var e = chars.count
        while s < e, !(chars[s].isLetter || chars[s].isNumber) { s += 1 }
        while e > s, !(chars[e - 1].isLetter || chars[e - 1].isNumber) { e -= 1 }
        return (String(chars[0..<s]), String(chars[s..<e]), String(chars[e...]))
    }
}

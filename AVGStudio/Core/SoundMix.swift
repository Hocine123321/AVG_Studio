import Foundation

// The numbers behind "music that sits under the voice".
//
// A background track played at one fixed level is the usual amateur tell: loud
// enough to hear in the pauses means it fights the narrator, quiet enough for
// the narrator means it vanishes between sentences. Real editors DUCK it: down
// while someone speaks, back up in the gaps, faded in at the start and out at
// the end. This file computes that curve as plain keyframes, with no audio
// framework in sight, so the shape is pinned by tests. `Mixdown` only applies it.
//
// The speech spans come from what Voice already recorded (`Project.audioSegments`:
// where each narrated take landed), so there is no voice-activity guessing.

enum SoundMix {
    /// Music level while someone is speaking, in dB relative to full scale.
    /// -20 dB keeps a voice clear on phone speakers (verify by ear on a device).
    static let defaultGainDB = -20.0
    /// How much louder the music may be in pauses and before/after the voice.
    static let openLiftDB = 8.0

    static func linear(_ db: Double) -> Double { pow(10, db / 20) }
}

struct GainKeyframe: Equatable {
    var time: Double
    var gain: Double
}

enum MusicEnvelope {
    /// Seconds to go down before a sentence starts / come back up after it ends.
    static let attack = 0.25
    static let release = 0.6
    static let fadeIn = 1.0
    static let fadeOut = 2.0
    /// Where a short track repeats, the level dips to zero and back over this
    /// long on each side, so the seam is a breath, not a click.
    static let loopDip = 0.12
    /// Speech closer together than this is treated as one stretch, so the music
    /// does not pump up and down between two quick sentences. Chosen so a
    /// release and the next attack can never overlap.
    static var mergeGap: Double { attack + release + 0.05 }

    /// Piecewise-linear gain over `0...total`, one keyframe per place the slope
    /// changes. Gains are linear (0...1), not dB.
    static func keyframes(speech: [(start: Double, end: Double)], total: Double, gainDB: Double,
                          loopPoints: [Double] = []) -> [GainKeyframe] {
        guard total > 0.1 else { return [] }
        let ducked = SoundMix.linear(gainDB)
        let lifted = min(1, SoundMix.linear(gainDB + SoundMix.openLiftDB))
        let spans = merged(speech, total: total)

        var times: [Double] = [0, total, min(fadeIn, total), max(0, total - fadeOut)]
        for s in spans { times += [s.start - attack, s.start, s.end, s.end + release] }
        for p in loopPoints where p > 0 && p < total { times += [p - loopDip, p, p + loopDip] }

        var points: [Double] = []
        for t in times.map({ min(max($0, 0), total) }).sorted() {
            if let last = points.last, t - last < 0.001 { continue }
            points.append(t)
        }
        return points.map { t in
            let level = levelUnderSpeech(at: t, spans: spans, ducked: ducked, lifted: lifted)
            let fade = max(0, min(1, t / fadeIn, (total - t) / fadeOut))
            var dip = 1.0
            for p in loopPoints { dip = min(dip, min(1, abs(t - p) / loopDip)) }
            return GainKeyframe(time: t, gain: level * fade * dip)
        }
    }

    /// Clips spans to the video, drops empty ones and joins near neighbours.
    static func merged(_ speech: [(start: Double, end: Double)], total: Double) -> [(start: Double, end: Double)] {
        let clipped = speech.compactMap { s -> (start: Double, end: Double)? in
            let a = max(0, s.start), b = min(total, s.end)
            return b > a ? (start: a, end: b) : nil
        }.sorted { $0.start < $1.start }
        var out: [(start: Double, end: Double)] = []
        for s in clipped {
            if let last = out.last, s.start - last.end < mergeGap {
                out[out.count - 1].end = max(last.end, s.end)
            } else {
                out.append(s)
            }
        }
        return out
    }

    /// The gain the keyframes imply at `t` (linear between keyframes).
    static func gain(at t: Double, in keyframes: [GainKeyframe]) -> Double {
        guard let first = keyframes.first, let last = keyframes.last else { return 0 }
        if t <= first.time { return first.gain }
        if t >= last.time { return last.gain }
        for i in 1..<keyframes.count where t <= keyframes[i].time {
            let a = keyframes[i - 1], b = keyframes[i]
            let span = b.time - a.time
            return span <= 0 ? b.gain : a.gain + (b.gain - a.gain) * (t - a.time) / span
        }
        return last.gain
    }

    private static func levelUnderSpeech(at t: Double, spans: [(start: Double, end: Double)],
                                         ducked: Double, lifted: Double) -> Double {
        for s in spans {
            if t >= s.start && t <= s.end { return ducked }
            if t > s.start - attack && t < s.start {
                return lifted + (ducked - lifted) * (t - (s.start - attack)) / attack
            }
            if t > s.end && t < s.end + release {
                return ducked + (lifted - ducked) * (t - s.end) / release
            }
        }
        return lifted
    }
}

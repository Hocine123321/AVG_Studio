import Foundation

// Where a sound effect goes - and, just as important, where it does NOT.
//
// A hit on every scene change is the quickest way to make a video feel like a
// template. So this plans sparingly, from moments the project already knows
// about (a slide or whip transition, a HIT beat, a reveal, a card appearing),
// gives each moment a priority, and keeps only what survives two limits:
// events at least `minGap` apart, and no more than `maxPerCluster` within
// `clusterRadius` of each other. Pure: no audio, no library, so the whole
// policy is pinned by tests. `Pipeline` turns the cues into `Mixdown.Effect`s.
//
// Only moments the channel actually gave a sound to are planned; a moment with
// no sound simply does nothing.

enum SFXMoment: String, CaseIterable, Codable, Identifiable {
    case impact, riser, whoosh, pop, softHit

    var id: String { rawValue }

    var title: String {
        switch self {
        case .impact: return "Impact"
        case .riser: return "Riser"
        case .whoosh: return "Whoosh"
        case .pop: return "Pop"
        case .softHit: return "Soft hit"
        }
    }

    var detail: String {
        switch self {
        case .impact: return "On a hit beat"
        case .riser: return "Builds into a reveal"
        case .whoosh: return "On a slide or whip transition"
        case .pop: return "When a question or explainer card appears"
        case .softHit: return "When a chapter card appears"
        }
    }

    /// Level before the channel's offset, in dB. Effects sit above the music
    /// bed but well under the voice; a riser is quieter because it lasts longer.
    var defaultGainDB: Double {
        switch self {
        case .impact: return -6
        case .riser: return -10
        case .whoosh: return -12
        case .pop: return -10
        case .softHit: return -8
        }
    }

    /// When two moments compete for the same stretch, the higher one stays.
    fileprivate var priority: Int {
        switch self {
        case .impact: return 5
        case .riser: return 4
        case .softHit: return 3
        case .pop: return 2
        case .whoosh: return 1
        }
    }
}

struct SFXCue: Equatable {
    var moment: SFXMoment
    /// When the thing it points at happens (a scene starts, a card appears).
    var event: Double
    /// When the sound itself starts: just ahead of the event (sound leads the
    /// picture), or - for a riser - early enough to end on it.
    var at: Double
}

enum SFXPlanner {
    /// The sound starts this long before the picture changes; a hit that lands
    /// even a few frames late reads as a mistake, one slightly early reads as a cut.
    static let lead = 0.05
    /// Two events closer than this share one sound (except a riser into its impact).
    static let minGap = 1.0
    static let clusterRadius = 5.0
    static let maxPerCluster = 2
    /// Nothing in the first or last moments of the video.
    static let edgeMargin = 0.3
    /// A riser longer than this is started late rather than early.
    static let maxRiser = 2.5

    struct Input {
        /// Seconds per scene, in order (scene 1 first).
        var sceneDurations: [Double]
        /// The transition INTO each scene, by scene number (1-based).
        var transitions: [Int: SceneTransition] = [:]
        /// Director I's beat and role per scene number: "HIT", "reveal", ...
        var beats: [Int: String] = [:]
        var roles: [Int: String] = [:]
        var cards: [(start: Double, kind: String?)] = []
        /// Moments that have a sound, with that sound's length in seconds.
        var soundLength: [SFXMoment: Double] = [:]
    }

    static func plan(_ input: Input) -> [SFXCue] {
        let total = input.sceneDurations.reduce(0, +)
        guard total > 1, !input.soundLength.isEmpty else { return [] }

        var starts: [Double] = []
        var cursor = 0.0
        for d in input.sceneDurations { starts.append(cursor); cursor += d }

        var found: [SFXCue] = []
        func add(_ moment: SFXMoment, event: Double) {
            guard let length = input.soundLength[moment] else { return }
            guard event >= edgeMargin, event <= total - edgeMargin else { return }
            let at = moment == .riser ? max(0, event - min(length, maxRiser)) : max(0, event - lead)
            found.append(SFXCue(moment: moment, event: event, at: at))
        }

        for (k, start) in starts.enumerated() {
            let scene = k + 1
            if let t = input.transitions[scene], [.slideLeft, .slideRight, .whip].contains(t) {
                add(.whoosh, event: start)
            }
            if input.beats[scene] == "HIT" { add(.impact, event: start) }
            if input.roles[scene] == "reveal" { add(.riser, event: start) }
        }
        for card in input.cards {
            add(card.kind == "chapter" ? .softHit : .pop, event: card.start)
        }

        var kept: [SFXCue] = []
        for cue in found.sorted(by: { ($0.moment.priority, -$0.event) > ($1.moment.priority, -$1.event) }) {
            let tooClose = kept.contains { other in
                guard abs(other.event - cue.event) < minGap else { return false }
                let riserIntoImpact = Set([other.moment, cue.moment]) == [.riser, .impact]
                return !riserIntoImpact
            }
            if tooClose { continue }
            let crowd = kept.filter { abs($0.event - cue.event) <= clusterRadius }.count
            if crowd >= maxPerCluster { continue }
            kept.append(cue)
        }
        return kept.sorted { $0.at < $1.at }
    }
}

import Foundation

// What a human editor does to a still image that the AI used to leave alone.
//
// A scene is one picture for five, six, eight seconds. Two cheap, classic
// moves make that feel edited instead of slideshow-like, and neither needs a
// new asset or an AI call:
//
//  - SHOT SPLITS: cut inside a long scene to a different crop of the same
//    picture (a close-up, then a medium, then a wider hold). A hard cut on a
//    word, not a crossfade, is what makes it read as a cut.
//  - PUNCH-INS: a quick push on one word that matters - a number, a shouted
//    word, the word the story map marked - that settles back.
//
// `EditPlanner` decides both from the word timings the pipeline already has.
// It is pure and deterministic: the same video always gets the same edit, the
// editor preview and the final render agree, and it costs no tokens. A later
// vision pass may tell it where the subject is (`focus`), which only improves
// where the crops land; it is never required.

/// How one shot frames its picture: a zoom, and which point of the image sits
/// at the centre of the frame (0...1 from the top-left).
struct Framing: Codable, Hashable {
    var zoom: Double = 1
    var fx: Double = 0.5
    var fy: Double = 0.5
}

/// A hard cut INSIDE a scene: from `at` seconds on, the scene shows `framing`.
struct ShotCut: Codable, Hashable {
    var scene: Int        // 0-based index into the video's scenes
    var at: Double        // absolute seconds from the start of the video
    var framing: Framing
}

struct EditPlan: Codable, Hashable {
    var cuts: [ShotCut] = []
    /// Absolute times of quick zoom pulses.
    var punches: [Double] = []
    /// What the planner did, in plain words (shown in the editor / log).
    var notes: [String] = []

    /// How long a punch-in takes to settle, and how far it pushes.
    static let punchLength = 0.45
    static let punchStrength = 0.07
    /// A shot slowly pushes in by this much over its own length, so even a
    /// held crop is never perfectly dead.
    static let drift = 0.035

    var isEmpty: Bool { cuts.isEmpty && punches.isEmpty }

    /// The shot showing at `t` in `scene`, and how far through it we are (0...1),
    /// or nil before the scene's first cut (the scene's own motion applies).
    func shot(at t: Double, scene: Int, sceneEnd: Double) -> (framing: Framing, progress: Double)? {
        let mine = cuts.filter { $0.scene == scene }.sorted { $0.at < $1.at }
        guard let active = mine.last(where: { $0.at <= t }) else { return nil }
        let end = mine.first(where: { $0.at > active.at })?.at ?? sceneEnd
        let span = max(end - active.at, 0.01)
        return (active.framing, min(max((t - active.at) / span, 0), 1))
    }

    /// 0 when no punch is active; up to 1 the instant one lands, easing to 0.
    func pulse(at t: Double) -> Double {
        var strongest = 0.0
        for p in punches where t >= p && t < p + Self.punchLength {
            let x = (t - p) / Self.punchLength
            strongest = max(strongest, (1 - x) * (1 - x))
        }
        return strongest
    }
}

enum EditLevel: String, Codable, CaseIterable, Identifiable {
    case off, fast

    var id: String { rawValue }

    var title: String {
        switch self {
        case .off: return "Off"
        case .fast: return "Shot cuts and punch-ins"
        }
    }

    var detail: String {
        switch self {
        case .off: return "Each scene is one picture with its own slow motion, as before."
        case .fast: return "Long scenes are cut into several crops of the same picture on a spoken word, and key words (numbers, shouted words, the story's key terms) get a quick punch-in. No AI call, no cost."
        }
    }
}

enum EditPlanner {
    struct Input {
        var sceneDurations: [Double]
        /// Every spoken word, in order, with absolute times.
        var words: [WordTiming]
        /// Director I's key words, by 1-based scene number.
        var zoomWords: [(scene: Int, word: String)] = []
        /// Short-form video (vertical or under about 75 s) cuts faster.
        var shortForm: Bool = true
        /// Where the subject sits in each scene's picture, by 0-based scene index
        /// (from a vision pass); scenes without one are framed around the upper middle.
        var focus: [Int: (x: Double, y: Double)] = [:]
    }

    static func targetShot(shortForm: Bool) -> Double { shortForm ? 3.0 : 6.0 }
    static let minShot = 1.4
    /// A cut moves to a spoken word if one starts within this many seconds.
    static let snapWindow = 0.6
    static let minPunchGap = 2.5
    static let edgeMargin = 0.5

    static func plan(_ input: Input) -> EditPlan {
        var plan = EditPlan()
        let total = input.sceneDurations.reduce(0, +)
        guard total > 1 else { return plan }

        var starts: [Double] = []
        var cursor = 0.0
        for d in input.sceneDurations { starts.append(cursor); cursor += d }

        // MARK: shot splits
        let target = targetShot(shortForm: input.shortForm)
        var previousZoom = 1.0
        var splitScenes = 0
        for (i, dur) in input.sceneDurations.enumerated() {
            let n = max(1, Int((dur / target).rounded()))
            guard n > 1 else { previousZoom = 1.0; continue }
            let start = starts[i], end = start + dur
            var times: [Double] = []
            for k in 1..<n {
                let ideal = start + dur * Double(k) / Double(n)
                var at = nearestWordStart(to: ideal, in: input.words) ?? ideal
                let lowest = (times.last ?? start) + minShot
                at = min(max(at, lowest), end - minShot)
                if at < lowest || at > end - minShot { continue }
                times.append(at)
            }
            guard !times.isEmpty else { continue }
            splitScenes += 1
            let base = input.focus[i] ?? (x: 0.5, y: 0.45)
            for (k, at) in times.enumerated() {
                let framing = framingFor(shot: k + 1, scene: i, base: base, previousZoom: previousZoom)
                plan.cuts.append(ShotCut(scene: i, at: at, framing: framing))
                previousZoom = framing.zoom
            }
        }
        if splitScenes > 0 {
            plan.notes.append("Cut \(splitScenes) long scene\(splitScenes == 1 ? "" : "s") into \(plan.cuts.count + splitScenes) shots.")
        }

        // MARK: punch-ins
        plan.punches = punchTimes(input, starts: starts, total: total)
        if !plan.punches.isEmpty {
            plan.notes.append("\(plan.punches.count) punch-in\(plan.punches.count == 1 ? "" : "s") on key words.")
        }
        return plan
    }

    // MARK: Pieces (internal for tests)

    static func nearestWordStart(to t: Double, in words: [WordTiming]) -> Double? {
        var best: Double?
        var bestGap = snapWindow
        for w in words {
            let gap = abs(w.start - t)
            if gap <= bestGap { best = w.start; bestGap = gap }
        }
        return best
    }

    /// The crop for the `shot`-th cut of a scene: alternating close and medium
    /// crops, leaning left and right, never the same zoom twice in a row.
    static func framingFor(shot: Int, scene: Int, base: (x: Double, y: Double), previousZoom: Double) -> Framing {
        let recipes: [(zoom: Double, lean: Double)] = [(1.34, 0.0), (1.14, 0.14), (1.5, -0.08), (1.22, -0.14)]
        let side: Double = (scene + shot) % 2 == 0 ? -1 : 1
        var index = (shot - 1 + scene) % recipes.count
        for _ in 0..<recipes.count where abs(recipes[index].zoom - previousZoom) < 0.1 {
            index = (index + 1) % recipes.count
        }
        let r = recipes[index]
        return Framing(zoom: r.zoom,
                       fx: min(max(base.x + r.lean * side, 0.15), 0.85),
                       fy: min(max(base.y, 0.2), 0.8))
    }

    /// Words worth a push, strongest first, thinned so they never crowd.
    static func punchTimes(_ input: Input, starts: [Double], total: Double) -> [Double] {
        var candidates: [(time: Double, weight: Int)] = []

        for zw in input.zoomWords {
            let i = zw.scene - 1
            guard starts.indices.contains(i) else { continue }
            let from = starts[i]
            let to = from + input.sceneDurations[i]
            let target = zw.word.trimmed.lowercased()
            if let hit = input.words.first(where: { $0.start >= from && $0.start < to && coreOf($0.text) == target }) {
                candidates.append((hit.start, 3))
            }
        }
        for (k, w) in input.words.enumerated() {
            let core = coreOf(w.text)
            if core.contains(where: { $0.isNumber }) { candidates.append((w.start, 2)) }
            else if isShouted(w.text) { candidates.append((w.start, 2)) }
            else if k > 0, w.start - input.words[k - 1].end >= 0.45 { candidates.append((w.start, 1)) }
        }

        let limit = max(1, Int(total / 4))
        var kept: [Double] = []
        let ordered = candidates
            .filter { $0.time >= edgeMargin && $0.time <= total - edgeMargin }
            .sorted { ($0.weight, -$0.time) > ($1.weight, -$1.time) }
        for c in ordered where kept.count < limit {
            if kept.contains(where: { abs($0 - c.time) < minPunchGap }) { continue }
            kept.append(c.time)
        }
        // The push starts a hair early so it lands WITH the word.
        return kept.sorted().map { max(0, $0 - 0.03) }
    }

    static func coreOf(_ word: String) -> String { CaptionEdit.parts(word).core.lowercased() }

    /// "NEVER", "WOW!" - three or more letters, all capitals.
    static func isShouted(_ word: String) -> Bool {
        let letters = CaptionEdit.parts(word).core.filter { $0.isLetter }
        return letters.count >= 3 && letters.allSatisfy { $0.isUppercase }
    }
}

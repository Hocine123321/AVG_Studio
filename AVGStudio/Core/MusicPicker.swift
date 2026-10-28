import Foundation

// "Choose a track for me."
//
// A channel can name one track (always the same bed), or let the app choose per
// video. Choosing is deliberately simple and explainable - no model call, no
// quota, the same answer every time for the same inputs:
//
//  1. Score each track by how well its tags match the video's words (title,
//     topic, brief, the channel's tone). A tag found in the text counts most; a
//     few related words for common moods ("tense" ~ mystery, danger, secret)
//     count a little.
//  2. Never the track used in the channel's previous video when another exists,
//     because the same bed under every upload is what makes a channel feel
//     templated (and YouTube's policy targets low-variation uploads).
//  3. Ties go to the track used least recently, then to uploads over generated
//     tracks (the person's own choice beats a stand-in), then to the id so the
//     result never depends on dictionary order.
//
// Pure: the Pipeline remembers the pick on the project, so re-rendering a video
// keeps its music.

enum MusicPicker {
    struct Candidate: Equatable {
        var id: String
        var tags: [String]
        /// An upload or linked track, as opposed to a generated one.
        var isOwn: Bool
    }

    /// Words that suggest a mood tag even when the tag itself is not in the text.
    static let related: [String: [String]] = [
        "calm": ["relax", "peace", "sleep", "meditat", "gentle", "quiet", "nature"],
        "upbeat": ["fun", "happy", "energy", "party", "celebrat", "win", "exciting"],
        "cinematic": ["epic", "story", "history", "journey", "war", "legend", "empire"],
        "tense": ["mystery", "crime", "danger", "secret", "thriller", "scary", "unsolved"],
        "warm": ["family", "love", "home", "friend", "memory", "kind"],
        "playful": ["kids", "funny", "game", "cartoon", "silly", "joke"],
        "dark": ["horror", "death", "shadow", "murder", "disaster", "haunt"],
        "hopeful": ["hope", "inspire", "dream", "future", "success", "motivat"],
    ]

    /// How well `tags` fit `text`: 3 per tag found in the text, plus 1 if any
    /// related word is there. Case-insensitive.
    static func score(tags: [String], text: String) -> Int {
        let haystack = text.lowercased()
        var total = 0
        for raw in tags {
            let tag = raw.trimmed.lowercased()
            guard !tag.isEmpty else { continue }
            if haystack.contains(tag) { total += 3 }
            if let words = related[tag], words.contains(where: { haystack.contains($0) }) { total += 1 }
        }
        return total
    }

    /// The id to use, or nil when there are no tracks. `recent` is the channel's
    /// last picks, newest first.
    static func pick(from tracks: [Candidate], text: String, recent: [String]) -> String? {
        guard !tracks.isEmpty else { return nil }
        if tracks.count == 1 { return tracks[0].id }
        var pool = tracks
        if let last = recent.first, pool.contains(where: { $0.id != last }) {
            pool.removeAll { $0.id == last }
        }
        func age(_ id: String) -> Int { recent.firstIndex(of: id) ?? Int.max }
        let ranked = pool.sorted { a, b in
            let sa = score(tags: a.tags, text: text), sb = score(tags: b.tags, text: text)
            if sa != sb { return sa > sb }
            if age(a.id) != age(b.id) { return age(a.id) > age(b.id) }
            if a.isOwn != b.isOwn { return a.isOwn }
            return a.id < b.id
        }
        return ranked.first?.id
    }
}

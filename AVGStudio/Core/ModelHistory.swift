import Foundation

// Which models actually worked, newest first, and the rule that turns that
// into a fallback.
//
// The problem it solves: when a model fails (over quota, retired, billing),
// the old fallback was a hard-coded "light" id per provider. Those ids go
// stale - Google and Groq retire models every few months - so the fallback
// itself became the next failure. A model that finished a phase for THIS
// account a few days ago is a far better bet than a name written into the app
// last year, and on Gemini's free tier quotas are counted per model, so
// "another model that recently worked" is also the one most likely to have
// quota left.
//
// Deliberately NOT a probe result (see `ProbeResult` in ModelPrefs.swift):
// a probe asks one tiny question on demand, history records real work the
// app already did, at zero extra cost. They are kept apart, and the planner
// below uses both: history picks, probes veto.

/// One model that just finished a phase successfully.
struct ModelUse: Codable, Hashable {
    /// "gemini", "groq", "pollinations", "elevenlabs" - lowercase, as `Recovery.Slot` names them.
    var provider: String
    var model: String
    var at: Date
}

/// The most recent models that worked, per kind of AI ("writer", "image", "voice").
struct ModelHistory: Codable, Equatable {
    /// Newest first. Keyed by kind so a Gemini image model is never offered for the writer.
    var byKind: [String: [ModelUse]] = [:]

    /// A dozen is plenty: only the newest few ever matter, and the file stays tiny.
    static let keep = 12

    /// Moves `model` to the front (one entry per provider and model, never duplicates).
    mutating func record(kind: String, provider: String, model: String, at: Date = Date()) {
        guard !model.isEmpty else { return }
        var list = byKind[kind] ?? []
        list.removeAll { $0.provider == provider && $0.model == model }
        list.insert(ModelUse(provider: provider, model: model, at: at), at: 0)
        if list.count > Self.keep { list.removeLast(list.count - Self.keep) }
        byKind[kind] = list
    }

    func recent(kind: String) -> [ModelUse] { byKind[kind] ?? [] }
}

/// Picks the model a failed phase drops to. Pure: no settings, no disk, so the
/// order below is pinned by tests instead of by reading `Recovery`.
enum FallbackPlanner {
    /// A model last proven this long ago may have been retired since, so it is
    /// not trusted as a fallback (the built-in preset is tried instead).
    static let maxAge: TimeInterval = 30 * 24 * 3600

    /// In order:
    ///  1. the person's own fallback model, if they set one (never second-guessed);
    ///  2. the newest model of the SAME provider that worked recently, is not the
    ///     one failing now, was not already tried this run and no probe marked bad;
    ///  3. the built-in light preset for the provider (may be nil: nothing lighter).
    /// Another provider is never picked here: switching provider is its own
    /// recovery action (`RecoveryAction.otherProvider`) and needs the person's say-so.
    static func pick(userChoice: String, provider: String, current: String, preset: String?,
                     history: [ModelUse], excluded: Set<String>,
                     now: Date = Date()) -> String? {
        if !userChoice.isEmpty { return userChoice }
        let recent = history.first {
            $0.provider == provider
                && $0.model != current
                && !excluded.contains($0.model)
                && now.timeIntervalSince($0.at) <= maxAge
        }
        return recent?.model ?? preset
    }
}

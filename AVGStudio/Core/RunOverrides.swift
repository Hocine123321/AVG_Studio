import Foundation

/// Per-video setting overrides. When a video hits a failure and the app (or
/// the person, in the recovery prompt) switches to a fallback model or another
/// provider, that is a decision about THAT VIDEO'S RUN - not about the app.
///
/// History of this bug: the original code wrote the fallback straight into
/// `store.settings`, so after every fallback the person had to re-select their
/// main model. The first fix (a ledger that put the value back afterwards)
/// still changed the GLOBAL setting while the run lasted: the Settings screen
/// showed the fallback, a second video starting meanwhile silently used it,
/// and a crash mid-run needed special handling to avoid saving it.
///
/// Now nothing global changes at all. An override lives here, keyed by
/// project; `Store.effectiveSettings(for:)` is the person's settings with that
/// project's overrides laid on top, and the pipeline reads ONLY that. Ending
/// the run discards the overrides. There is nothing to restore, nothing to
/// persist, and no other project can see them.
struct RunOverrides {
    private var byProject: [String: [AnyKeyPath: (inout AppSettings) -> Void]] = [:]

    /// Sets `keyPath` to `value` for `project`'s run. A later override of the
    /// same setting replaces the earlier one.
    mutating func set<T>(_ keyPath: WritableKeyPath<AppSettings, T>, to value: T, project: String) {
        byProject[project, default: [:]][keyPath] = { $0[keyPath: keyPath] = value }
    }

    /// `settings` as `project` should run: its overrides on top.
    func applied(to settings: AppSettings, project: String) -> AppSettings {
        guard let edits = byProject[project], !edits.isEmpty else { return settings }
        var copy = settings
        for edit in edits.values { edit(&copy) }
        return copy
    }

    func has(project: String) -> Bool { !(byProject[project]?.isEmpty ?? true) }

    /// Ends a run's overrides. Returns whether there were any.
    @discardableResult
    mutating func clear(project: String) -> Bool {
        byProject.removeValue(forKey: project) != nil
    }
}

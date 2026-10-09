import Foundation

/// Run-scoped setting overrides. When a video hits a failure and the app (or
/// the person, in the recovery prompt) switches to a fallback model or
/// another provider, that is a decision about THIS run - not about what the
/// person chose in Settings.
///
/// The old code wrote the fallback straight into `store.settings`, and
/// nothing ever put the main model back (and `saveMeta` persisted it), so
/// after every fallback the person had to re-select their main model. Here
/// each override remembers the value it replaced:
///  - `release(owner:)` puts the original back when that project's run ends,
///    unless the person changed the setting themselves in the meantime;
///  - `reverted(_:)` is what gets saved to disk, so even a crash or a kill
///    mid-run cannot leave a fallback behind.
/// Two projects overriding the same setting share one original, restored when
/// the last of them ends.
struct FallbackLedger {
    private struct Entry {
        var owners: Set<String>
        /// Is the setting still holding the value we put there?
        var stillApplied: (AppSettings) -> Bool
        var restore: (inout AppSettings) -> Void
    }

    private var entries: [AnyKeyPath: Entry] = [:]

    var isEmpty: Bool { entries.isEmpty }

    /// Sets `keyPath` to `value` for `owner`'s run.
    mutating func override<T: Equatable>(_ keyPath: WritableKeyPath<AppSettings, T>, to value: T,
                                         owner: String, in settings: inout AppSettings) {
        if var e = entries[keyPath] {
            // Already overridden by someone: keep the FIRST original.
            e.owners.insert(owner)
            e.stillApplied = { $0[keyPath: keyPath] == value }
            entries[keyPath] = e
        } else {
            let original = settings[keyPath: keyPath]
            guard original != value else { return }   // nothing to undo later
            entries[keyPath] = Entry(owners: [owner],
                                     stillApplied: { $0[keyPath: keyPath] == value },
                                     restore: { $0[keyPath: keyPath] = original })
        }
        settings[keyPath: keyPath] = value
    }

    /// Ends `owner`'s overrides. Returns whether any setting was put back.
    @discardableResult
    mutating func release(owner: String, in settings: inout AppSettings) -> Bool {
        var changed = false
        for (keyPath, var e) in entries where e.owners.contains(owner) {
            e.owners.remove(owner)
            if e.owners.isEmpty {
                if e.stillApplied(settings) {
                    e.restore(&settings)
                    changed = true
                }
                entries[keyPath] = nil
            } else {
                entries[keyPath] = e
            }
        }
        return changed
    }

    /// `settings` as it should be written to disk: every still-active
    /// override replaced by the value it replaced.
    func reverted(_ settings: AppSettings) -> AppSettings {
        var copy = settings
        for e in entries.values where e.stillApplied(copy) { e.restore(&copy) }
        return copy
    }
}

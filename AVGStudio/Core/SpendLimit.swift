import Foundation

/// The per-video spending limit (Settings > API keys > Spending): a guard rail
/// that stops a run BEFORE a paid step would take one video past the limit.
/// Costs are the app's own estimates (`CostTable`), never a bill, and free
/// providers are never stopped. 0 means no limit.
enum SpendLimit {
    static let choices: [Double] = [0, 0.25, 0.5, 1, 2, 5]

    static func label(_ cap: Double) -> String {
        cap <= 0 ? "No limit" : dollars(cap)
    }

    /// Whether a step estimated at `next` dollars must not start. With no
    /// `next` (a step whose cost is only known as it runs) it blocks once the
    /// limit has been reached.
    static func blocks(spent: Double, next: Double = 0, cap: Double) -> Bool {
        guard cap > 0 else { return false }
        return next > 0 ? spent + next > cap : spent >= cap
    }

    static func message(spent: Double, next: Double = 0, cap: Double) -> String {
        let used = "about \(dollars(spent)) already used"
        let tail = "Raise the limit in Settings > API keys > Spending, or use a free option for this step."
        if next > 0 {
            return "Stopped before spending: this step would cost about \(dollars(next)), with \(used), "
                + "which is over your \(dollars(cap)) limit for one video. \(tail)"
        }
        return "Stopped: this video has reached your \(dollars(cap)) limit (\(used)). \(tail)"
    }

    static func dollars(_ value: Double) -> String {
        String(format: "$%.2f", value)
    }
}

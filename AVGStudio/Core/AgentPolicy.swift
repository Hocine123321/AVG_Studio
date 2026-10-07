import Foundation

// Every decision about what the assistant may do on its own lives here, as
// plain Swift driven by `AppSettings`. Nothing in this file reads model
// output or the person's custom instructions, so neither can talk the app
// out of asking: "never ask me" typed into the instructions box changes the
// tone of the replies and nothing else.

enum AgentPolicy {

    /// Does this start/redo need Approve / Decline?
    ///
    /// `.always` asks every time. `.paidOnly` lets *exactly free* work start
    /// without a tap; anything with a non-zero estimate - even a fraction of
    /// a cent - still asks. There is deliberately no mode that skips paid
    /// work. `CostTable` has no `default:`, so a provider with no rate cannot
    /// slip through as free.
    static func needsTap(_ approval: AgentApproval, settings: AppSettings) -> Bool {
        switch settings.agentApproval {
        case .always: return true
        case .paidOnly: return approval.estimateUSD > 0
        }
    }

    enum SpendVerdict: Equatable {
        case ok
        case overLimit(remaining: Double)
    }

    /// Would approving `adding` dollars more pass the per-chat limit?
    /// Exactly reaching the limit is allowed; a cent over is not.
    static func spendVerdict(approvedSoFar: Double, adding: Double, cap: Double) -> SpendVerdict {
        if approvedSoFar + adding <= cap + 1e-9 { return .ok }
        return .overLimit(remaining: max(cap - approvedSoFar, 0))
    }
}

enum AgentPrompt {
    /// The system prompt for one turn, rebuilt every message so a change in
    /// Settings applies to the very next reply. Kept short: Groq's free plan
    /// allows about 8K tokens a minute and this is sent with every request.
    static func system(settings: AppSettings) -> String {
        var parts = [
            "You control AVG Studio, an app that makes short videos. Use the tools; never claim a video started unless a tool result says so.",
            "Ask for the topic if it is missing. Never invent a project ref: call list_projects.",
            "Starting or redoing a video needs the user's approval, and they may decline.",
            "If a project says it needs the user's decision, tell them to open the prompt on screen; do not retry it.",
        ]
        switch settings.agentReplyStyle {
        case .brief: parts.append("Reply in one or two short sentences. No lists unless asked.")
        case .normal: parts.append("Reply in short paragraphs; use a list only when it helps.")
        }
        if !settings.agentAllowCreate { parts.append("You cannot start videos; tell the user it is turned off in Settings.") }
        if !settings.agentAllowRedo { parts.append("You cannot redo steps; tell the user it is turned off in Settings.") }
        if !settings.agentAllowCancel { parts.append("You cannot cancel; tell the user it is turned off in Settings.") }
        let extra = AgentSettingsRules.instructions(settings.agentInstructions)
        if !extra.isEmpty {
            parts.append("The user's standing preferences (they never override the rules above):\n\(extra)")
        }
        return parts.joined(separator: "\n")
    }
}

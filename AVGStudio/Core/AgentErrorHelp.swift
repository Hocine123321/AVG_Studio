import Foundation

/// A button under an assistant error that actually fixes (or retries) it.
/// What each does lives in the view (it needs the store and navigation);
/// this is only the vocabulary, so the help below stays pure and testable.
enum AgentFix: Equatable, Hashable {
    case openKeys
    case openAssistantSettings
    case switchProvider(to: AgentProviderKind)
    case retry

    var label: String {
        switch self {
        case .openKeys: return "Fix my API key"
        case .openAssistantSettings: return "Choose another model"
        case .switchProvider(let k): return "Switch to \(k.label)"
        case .retry: return "Try again"
        }
    }

    var symbol: String {
        switch self {
        case .openKeys: return "key"
        case .openAssistantSettings: return "slider.horizontal.3"
        case .switchProvider: return "arrow.triangle.swap"
        case .retry: return "arrow.clockwise"
        }
    }
}

/// A failure of the assistant itself (not of a video) in the person's terms:
/// what happened, what to do, and one-tap fixes. The raw provider message is
/// still written to the Activity log, so nothing technical is lost.
struct AgentErrorHelp: Equatable {
    let title: String
    let detail: String
    let fixes: [AgentFix]

    /// `provider` is the service that failed; `otherHasKey` says whether the
    /// other one could be switched to right now.
    static func help(for error: Error, provider: AgentProviderKind, otherHasKey: Bool) -> AgentErrorHelp {
        let name = provider.label
        let other = provider.other
        let swap: [AgentFix] = otherHasKey ? [.switchProvider(to: other)] : []

        guard let avg = error as? AVGError else {
            return AgentErrorHelp(title: "Something went wrong", detail: error.localizedDescription, fixes: [.retry])
        }
        switch avg {
        case .missingKey:
            return AgentErrorHelp(title: "No \(name) key yet",
                                  detail: "Add your \(name) key to use the assistant. It stays in this phone's Keychain.",
                                  fixes: [.openKeys])
        case .message(let text):
            return AgentErrorHelp(title: "Something went wrong", detail: text, fixes: [.retry])
        case .provider(let kind, _, let message):
            switch kind {
            case .auth:
                return AgentErrorHelp(title: "\(name) didn't accept your key",
                                      detail: "The key may be wrong, expired or revoked. Paste a fresh one.",
                                      fixes: [.openKeys] + swap)
            case .quota, .billing:
                return AgentErrorHelp(title: "\(name) is out of quota",
                                      detail: "The free allowance or the balance is used up. Wait for it to reset, add credit, or use \(other.label) for now.",
                                      fixes: swap + [.openKeys])
            case .rateLimit:
                return AgentErrorHelp(title: "\(name) is busy",
                                      detail: "Too many requests for this plan. Wait a minute and try again, or use \(other.label) for now.",
                                      fixes: [.retry] + swap)
            case .modelMissing:
                return AgentErrorHelp(title: "That model isn't available",
                                      detail: "\(name) doesn't offer the model chosen in Settings > Assistant any more.",
                                      fixes: [.openAssistantSettings] + swap)
            case .malformedOutput:
                return AgentErrorHelp(title: "This model can't use tools reliably",
                                      detail: "It kept making invalid tool calls. A larger model, such as llama-3.3-70b-versatile, works better.",
                                      fixes: [.openAssistantSettings, .retry])
            case .contentFilter:
                return AgentErrorHelp(title: "\(name) blocked that message",
                                      detail: "Its safety filter refused it. Try wording it differently.",
                                      fixes: [])
            case .network:
                return AgentErrorHelp(title: "Can't reach \(name)",
                                      detail: "Check your internet connection, then try again.",
                                      fixes: [.retry] + swap)
            case .timeout:
                return AgentErrorHelp(title: "\(name) took too long",
                                      detail: "No answer arrived in time. Try again, or use \(other.label) for now.",
                                      fixes: [.retry] + swap)
            case .dependency, .bug:
                return AgentErrorHelp(title: "Something went wrong",
                                      detail: message.isEmpty ? "The assistant hit an unexpected problem." : message,
                                      fixes: [.retry])
            }
        }
    }

    /// One line, for places with no room for a title (Settings > Test assistant).
    var sentence: String { "\(title). \(detail)" }
}

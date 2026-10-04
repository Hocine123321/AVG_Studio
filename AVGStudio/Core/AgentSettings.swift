import Foundation

// The Assistant's settings vocabulary. The fields themselves live on
// `AppSettings` (so they are saved, decoded tolerantly and searchable like
// every other setting); this file holds the enums they use and the rules
// that keep a hand-edited or out-of-range value safe.

/// Which service runs the assistant's chat. Separate from the writer's
/// `TextProviderKind`: the writer may be Apple Intelligence, which this
/// feature cannot use (it has no tool calling here, and some phones lack it).
enum AgentProviderKind: String, Codable, CaseIterable, Identifiable {
    case groq, gemini
    var id: String { rawValue }
    var label: String {
        switch self {
        case .groq: return "Groq"
        case .gemini: return "Google Gemini"
        }
    }
    /// The other service, for failover.
    var other: AgentProviderKind { self == .groq ? .gemini : .groq }
}

/// When the assistant must wait for a tap before starting work.
enum AgentApprovalMode: String, Codable, CaseIterable, Identifiable {
    /// Every start or redo shows Approve / Decline.
    case always
    /// Work that is free starts without a tap; anything that costs money
    /// always asks. There is deliberately no mode that skips paid work.
    case paidOnly
    var id: String { rawValue }
    var label: String {
        switch self {
        case .always: return "Every video"
        case .paidOnly: return "Only videos that cost money"
        }
    }
}

enum AgentReplyStyle: String, Codable, CaseIterable, Identifiable {
    case brief, normal
    var id: String { rawValue }
    var label: String {
        switch self {
        case .brief: return "Brief"
        case .normal: return "Normal"
        }
    }
}

enum AgentSettingsRules {
    /// The most the assistant may have approved in one chat, in US dollars.
    static let spendCapRange: ClosedRange<Double> = 0.25...10
    static let spendCapStep = 0.25
    static let spendCapDefault = 1.0
    /// Custom instructions are sent with every request, so they are short.
    static let instructionsLimit = 400

    /// A usable spend limit: clamped into range and snapped to the step. A
    /// hand-edited `nan` falls back to the default rather than to "no limit".
    static func spendCap(_ value: Double) -> Double {
        guard !value.isNaN else { return spendCapDefault }
        let clamped = min(max(value, spendCapRange.lowerBound), spendCapRange.upperBound)
        let snapped = (clamped / spendCapStep).rounded() * spendCapStep
        return min(max(snapped, spendCapRange.lowerBound), spendCapRange.upperBound)
    }

    /// Custom instructions as they are sent: trimmed and cut to the limit.
    /// The cut is by `Character`, so an emoji is never split in half.
    static func instructions(_ text: String) -> String {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return String(trimmed.prefix(instructionsLimit)).trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

import Foundation

/// Swift port of `avg.providers.errors` (`ErrorKind`, `POLICY`, `classify`):
/// ONE place that turns a raw provider failure (HTTP status + body, or a
/// connection-layer error) into a `kind`, and ONE table saying what to do
/// about each kind - never retry the same non-retryable failure, never
/// guess a fix from a status code alone once the body said something more
/// specific. `HTTP.retrying` is the only reader of `POLICY`; nothing else
/// in this app should branch on a status code or grep an error string.
///
/// Ported faithfully for every provider this app actually has (gemini,
/// groq, pollinations, elevenlabs, openai, stablehorde). The PC's
/// `huggingface`-specific patterns are NOT ported: this app has no
/// HuggingFace provider for them to ever match against.
enum ErrorKind: String, Sendable {
    case auth, quota, billing, rateLimit, modelMissing, contentFilter, network, timeout, dependency, bug
}

/// What `POLICY[kind].onExhausted` means once retrying is no longer an
/// option (either the kind isn't retryable at all, or every attempt was
/// used up) - mirrors the PC's `OnExhausted` literal exactly.
enum OnExhausted: Sendable { case fail, switchKey, switchProvider, switchModel }

struct RetryRule: Sendable {
    let retryable: Bool
    let maxAttempts: Int
    let backoffSeconds: Double
    let onExhausted: OnExhausted
}

/// network/timeout: the first try plus 3 automatic retries, waiting 4s, 8s,
/// then 16s (exponential) - long enough to ride out a briefly busy server
/// before the user is asked what to do (Recovery.swift). Otherwise verbatim
/// from `avg/providers/errors.py`'s own table - same kinds, same
/// attempt counts, same backoff seconds. `rateLimit` gets the deepest
/// budget with a real exponential base (a 429 is the one failure genuinely
/// expected to clear if given room); `network`/`timeout` get a short fixed
/// retry; everything non-retryable is one attempt, decide immediately.
let POLICY: [ErrorKind: RetryRule] = [
    .rateLimit: RetryRule(retryable: true, maxAttempts: 5, backoffSeconds: 2.0, onExhausted: .switchProvider),
    .network: RetryRule(retryable: true, maxAttempts: 4, backoffSeconds: 4.0, onExhausted: .switchProvider),
    .timeout: RetryRule(retryable: true, maxAttempts: 4, backoffSeconds: 4.0, onExhausted: .switchProvider),
    .auth: RetryRule(retryable: false, maxAttempts: 1, backoffSeconds: 0, onExhausted: .switchKey),
    .quota: RetryRule(retryable: false, maxAttempts: 1, backoffSeconds: 0, onExhausted: .switchProvider),
    .billing: RetryRule(retryable: false, maxAttempts: 1, backoffSeconds: 0, onExhausted: .switchProvider),
    .modelMissing: RetryRule(retryable: false, maxAttempts: 1, backoffSeconds: 0, onExhausted: .switchModel),
    .contentFilter: RetryRule(retryable: false, maxAttempts: 1, backoffSeconds: 0, onExhausted: .fail),
    .bug: RetryRule(retryable: false, maxAttempts: 1, backoffSeconds: 0, onExhausted: .fail),
    .dependency: RetryRule(retryable: false, maxAttempts: 1, backoffSeconds: 0, onExhausted: .fail),
]

/// One human sentence for `onExhausted`, appended to the final error so a
/// failed phase's message tells the user what to actually DO - not just
/// that it failed. Empty for `.fail`: a content-filter block or a bug is
/// not fixed by switching anything, so no suggestion is offered.
extension OnExhausted {
    var userGuidance: String {
        switch self {
        case .fail: return ""
        case .switchKey: return " Check the API key in Settings, or switch providers."
        case .switchProvider: return " Try a different provider (Settings, or this channel's Models tab), or wait if it's a quota that resets."
        case .switchModel: return " Try a different model for this phase (Channel → Models), or a different provider."
        }
    }
}

private func rx(_ pattern: String) -> NSRegularExpression {
    // swiftlint:disable:next force_try - every pattern below is a fixed
    // literal checked in at compile time, not user input.
    try! NSRegularExpression(pattern: pattern, options: [.caseInsensitive])
}

private func matches(_ text: String, _ patterns: [NSRegularExpression]) -> Bool {
    let range = NSRange(text.startIndex..., in: text)
    return patterns.contains { $0.firstMatch(in: text, range: range) != nil }
}

/// Checked top-to-bottom, first match wins - billing (a payment problem)
/// before quota (a cap that resets on its own), matching the PC's own
/// `_CHECK_ORDER` reasoning: a body mentioning both loses to billing, the
/// more actionable, more specific claim.
private let checkOrder: [ErrorKind] = [.contentFilter, .auth, .billing, .quota, .modelMissing, .rateLimit, .network, .timeout]

/// Patterns holding regardless of provider - ported verbatim from
/// `avg/providers/errors.py`'s `_GENERIC` table.
private let generic: [ErrorKind: [NSRegularExpression]] = [
    .contentFilter: ["content[_ ]filter", "content polic", "content moderation", "moderation flagged",
                     "safety system", "blocked by safety", "harmful content", "violates.*polic",
                     "policy violation", "prohibited_content", "\\bcensored\\b", "flagged.*safety"].map(rx),
    .auth: ["\\b401\\b", "\\b403\\b", "unauthori[sz]ed", "invalid api key", "invalid auth",
            "incorrect api key", "wrong api key", "api key invalid", "authentication failed",
            "auth failed", "access denied", "\\bforbidden\\b", "permission_denied",
            "invalid token", "expired token", "invalid_api_key"].map(rx),
    .billing: ["insufficient_quota", "\\bbilling\\b", "payment required", "payment method",
               "credit card", "check your plan and billing", "trial expired", "\\b402\\b"].map(rx),
    .quota: ["quota exceeded", "quota_exceeded", "resource_exhausted", "daily limit", "monthly limit",
             "weekly limit", "usage limit", "requests per (day|min)", "tokens per min", "\\btpm\\b", "\\brpm\\b"].map(rx),
    .modelMissing: ["model_not_found", "model not found", "no such model", "unsupported model",
                    "invalid model", "model .* not found", "is not a valid model", "voice_not_found",
                    "voice.*not found", "context length", "context_length", "maximum context",
                    "context window", "max_tokens", "exceeds.*context"].map(rx),
    .rateLimit: ["rate[_ -]?limit", "too many requests", "\\b429\\b", "throttled", "slow down", "rate exceeded"].map(rx),
    .network: ["\\bconnection\\b", "\\bconnect\\b", "\\bnetwork\\b", "unreachable", "\\b50[0-4]\\b",
               "bad gateway", "service unavailable", "gateway timeout", "reset by peer", "refused"].map(rx),
    .timeout: ["\\btimeout\\b", "timed out"].map(rx),
]

/// Provider-specific vocabulary the generic table can't know - ported
/// verbatim from `_PROVIDER`, minus `huggingface` (no such provider here).
private let providerSpecific: [String: [ErrorKind: [NSRegularExpression]]] = [
    "pollinations": [.billing: ["\\bpollen\\b", "credit balance", "insufficient.*credit"].map(rx)],
    "stablehorde": [.contentFilter: ["\\bcensored\\b"].map(rx)],
    "elevenlabs": [
        .auth: ["invalid_api_key"].map(rx),
        .quota: ["quota_exceeded"].map(rx),
        .modelMissing: ["voice_not_found"].map(rx),
        .network: ["system_busy"].map(rx),
    ],
    "gemini": [
        .contentFilter: ["\\bsafety\\b", "prohibited_content", "blockreason"].map(rx),
        .auth: ["permission_denied"].map(rx),
    ],
    // openai / groq: no provider-specific vocabulary found in the PC's own
    // legacy classifier either - generic patterns apply as-is.
]

private func providerKey(_ provider: String) -> String {
    provider.lowercased().filter { $0.isLetter || $0.isNumber }
}

private func bodyMatches(_ text: String, provider: String, kind: ErrorKind) -> Bool {
    let key = providerKey(provider)
    if let specific = providerSpecific[key]?[kind], matches(text, specific) { return true }
    if let g = generic[kind], matches(text, g) { return true }
    return false
}

/// Classifies one connection-layer failure (no HTTP response reached at
/// all) using `URLError`'s own typed code - more reliable than the PC's
/// exception-name/text sniffing (`_classify_exception`), since Swift gives
/// a real error code where the PC had to guess from a string. Anything not
/// explicitly network/timeout/dependency-shaped fails safe to `.bug`,
/// matching the PC's own "don't guess a more specific kind" default.
func classifyURLError(_ error: URLError) -> ErrorKind {
    switch error.code {
    case .timedOut: return .timeout
    case .notConnectedToInternet, .networkConnectionLost, .cannotConnectToHost, .cannotFindHost,
         .dnsLookupFailed, .internationalRoamingOff, .callIsActive, .dataNotAllowed, .secureConnectionFailed:
        return .network
    default: return .bug
    }
}

/// Classifies one provider HTTP failure. `status` is the HTTP status code;
/// `body` the raw response text (possibly empty). Mirrors
/// `avg.providers.errors.classify` exactly: status-based hard rules first
/// (401/403 -> auth, 5xx -> network), then body-text patterns in
/// `checkOrder`, then status-based defaults (429/402/404) once no body
/// text said anything more specific, else `.bug`.
func classify(status: Int, body: String, provider: String) -> ErrorKind {
    if status == 401 || status == 403 { return .auth }
    if (500..<600).contains(status) { return .network }
    if !body.isEmpty {
        for kind in checkOrder where bodyMatches(body, provider: provider, kind: kind) { return kind }
    }
    if status == 429 { return .rateLimit }
    if status == 402 { return .billing }
    if status == 404 { return .modelMissing }
    return .bug
}

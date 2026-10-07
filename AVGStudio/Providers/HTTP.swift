import Foundation

/// Carries "where to log a retry/give-up line" down into `HTTP` without
/// threading a closure through every provider's init and every call site.
/// A `@TaskLocal` rather than a mutable global: `Store.start(_:from:)`
/// binds it once per render `Task`, and Swift concurrency propagates it
/// automatically through every `await` inside that task's tree - including
/// calls into `HTTP.send`/`retrying` several layers down - so two projects
/// rendering at once (`Store.tasks` is keyed by project id; nothing stops
/// two ids running concurrently) each see their OWN project's logger, never
/// each other's. A plain `static var` here would attribute both projects'
/// retry lines to whichever one last set it.
enum RenderLog {
    @TaskLocal static var record: (@Sendable (String) async -> Void)?
    static func emit(_ message: String) async { await record?(message) }
}

enum HTTP {
    // Matches the PC's own _GENERATE_TIMEOUT_SECONDS (avg/providers/text/*.py,
    // avg/providers/text/gemini.py): 180s was too tight for a slower model's
    // full generation (scene-segmentation prompts especially), and the PC
    // deliberately raised its own transport default for exactly that reason.
    //
    // `provider` feeds `classify()` on a failure - see `ErrorPolicy.swift`.
    // Required (no default) rather than optional: an uncalled-out provider
    // name would silently fall back to only the generic patterns, quietly
    // losing whichever provider-specific vocabulary actually distinguishes
    // e.g. Gemini's safety block from a plain auth failure.
    static func send(_ url: URL, method: String = "POST", headers: [String: String] = [:], json body: Any? = nil, timeout: TimeInterval = 300, provider: String) async throws -> Data {
        var r = URLRequest(url: url)
        r.httpMethod = method
        r.timeoutInterval = timeout
        for (k, v) in headers { r.setValue(v, forHTTPHeaderField: k) }
        if let body {
            r.httpBody = try JSONSerialization.data(withJSONObject: body)
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let data: Data
        let resp: URLResponse
        do {
            (data, resp) = try await URLSession.shared.data(for: r)
        } catch let urlError as URLError {
            // No HTTP response reached at all (DNS failure, dropped
            // connection, request-level timeout) - classified from the
            // typed URLError code rather than left to float up unwrapped,
            // so `HTTP.retrying` has a `kind` to look up a policy for.
            let kind = classifyURLError(urlError)
            throw AVGError.provider(kind, provider: provider, message: urlError.localizedDescription)
        }
        guard let h = resp as? HTTPURLResponse else { throw AVGError.message("No response from service.") }
        guard (200..<300).contains(h.statusCode) else {
            let bodyText = String(decoding: data.prefix(400), as: UTF8.self)
            let kind = classify(status: h.statusCode, body: bodyText, provider: provider)
            throw AVGError.provider(kind, provider: provider, message: "HTTP \(h.statusCode): \(bodyText)")
        }
        return data
    }

    static func object(_ data: Data) throws -> [String: Any] {
        guard let o = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw AVGError.message("Unexpected reply from service.")
        }
        return o
    }

    /// Same as `object(_:)`, for the handful of catalogue endpoints
    /// (ElevenLabs' `/v1/models`, AI Horde's `/status/models`) that answer
    /// with a bare JSON array at the top level instead of wrapping it in
    /// an object field.
    static func array(_ data: Data) throws -> [[String: Any]] {
        guard let a = try JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
            throw AVGError.message("Unexpected reply from service.")
        }
        return a
    }

    /// Policy-driven retry - mirrors `avg.providers.transport.Transport.
    /// _send_with_retry` exactly: classify the failure, look its `kind` up
    /// in `POLICY`, and either back off exponentially and try again (while
    /// `rule.retryable` and attempts remain) or give up with a message that
    /// tells the user what to actually do about it
    /// (`OnExhausted.userGuidance`). Replaces the old fixed "3 attempts,
    /// 1.5s linear backoff, binary transient/not" loop, which could not
    /// distinguish "Google's servers are hot, worth five patient retries"
    /// from "the quota is gone, retrying is pointless - go switch
    /// provider" - both used to get the exact same treatment.
    ///
    /// An error with no `kind` (`.missingKey`, `.message` - a local
    /// validation failure or a malformed reply, never a classified
    /// provider failure) is not retried and propagates immediately, same
    /// as the old loop's non-`.http` branch.
    ///
    /// `attemptCap` lowers the number of attempts below the policy's, for a
    /// caller that has somewhere better to go than waiting: the assistant
    /// passes 2 when a backup service is available, so a rate limit fails
    /// over after one short wait instead of sitting out the full backoff.
    /// Omitted, nothing changes.
    static func retrying<T>(_ op: () async throws -> T, provider: String, attemptCap: Int? = nil) async throws -> T {
        var attempt = 1
        while true {
            do { return try await op() }
            catch {
                if error is CancellationError { throw error }
                // A `kind` implies `avg` is the `.provider` case (every
                // other case's `kind` is nil), so the `.provider` pattern
                // below always matches once this guard passes.
                guard let avg = error as? AVGError, let kind = avg.kind, let rule = POLICY[kind],
                      case .provider(_, let providerName, let message) = avg
                else { throw error }
                let maxAttempts = min(rule.maxAttempts, max(attemptCap ?? rule.maxAttempts, 1))
                if rule.retryable, attempt < maxAttempts {
                    let delay = rule.backoffSeconds * pow(2.0, Double(attempt - 1))
                    await RenderLog.emit("\(provider): \(kind.rawValue) - retrying in \(String(format: "%.0f", delay))s (attempt \(attempt + 1)/\(maxAttempts)).")
                    try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
                    attempt += 1
                    continue
                }
                let guidance = rule.onExhausted.userGuidance
                await RenderLog.emit("\(provider): \(kind.rawValue), giving up.\(guidance)")
                throw AVGError.provider(kind, provider: providerName, message: message + guidance)
            }
        }
    }
}

import Foundation

enum HTTP {
    // Matches the PC's own _GENERATE_TIMEOUT_SECONDS (avg/providers/text/*.py,
    // avg/providers/text/gemini.py): 180s was too tight for a slower model's
    // full generation (scene-segmentation prompts especially), and the PC
    // deliberately raised its own transport default for exactly that reason.
    static func send(_ url: URL, method: String = "POST", headers: [String: String] = [:], json body: Any? = nil, timeout: TimeInterval = 300) async throws -> Data {
        var r = URLRequest(url: url)
        r.httpMethod = method
        r.timeoutInterval = timeout
        for (k, v) in headers { r.setValue(v, forHTTPHeaderField: k) }
        if let body {
            r.httpBody = try JSONSerialization.data(withJSONObject: body)
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, resp) = try await URLSession.shared.data(for: r)
        guard let h = resp as? HTTPURLResponse else { throw AVGError.message("No response from service.") }
        guard (200..<300).contains(h.statusCode) else {
            throw AVGError.http(h.statusCode, String(decoding: data.prefix(400), as: UTF8.self))
        }
        return data
    }

    static func object(_ data: Data) throws -> [String: Any] {
        guard let o = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw AVGError.message("Unexpected reply from service.")
        }
        return o
    }

    /// Retries only genuinely transient failures - a rate limit, a 5xx, a
    /// dropped connection/timeout at the network layer. A deterministic
    /// failure (bad request, auth error, "the service replied 200 but had
    /// nothing usable" - e.g. a content-filter block or a parse miss, both
    /// surfaced as `AVGError.message`) will not change on attempt two, so it
    /// is now thrown immediately instead of burning 3-4 attempts and several
    /// seconds of backoff on something retrying can't fix. Previously ANY
    /// non-`.http` error fell through to a full retry loop purely because it
    /// wasn't the `.http` case - this is the fix for that.
    static func retrying<T>(_ attempts: Int = 3, _ op: () async throws -> T) async throws -> T {
        var last: Error = AVGError.message("Unknown error")
        for i in 0..<attempts {
            do { return try await op() } catch {
                last = error
                if error is CancellationError { throw error }
                if let avg = error as? AVGError {
                    if !avg.isTransient { throw error }
                } else if !(error is URLError) {
                    // Not a network-layer failure and not one of our own
                    // classified errors - treat as non-transient too.
                    throw error
                }
                try await Task.sleep(nanoseconds: UInt64(1_500_000_000 * Double(i + 1)))
            }
        }
        throw last
    }
}

import Foundation

enum HTTP {
    static func send(_ url: URL, method: String = "POST", headers: [String: String] = [:], json body: Any? = nil, timeout: TimeInterval = 180) async throws -> Data {
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

    /// Retries transient failures (rate limits, 5xx, timeouts) with backoff.
    static func retrying<T>(_ attempts: Int = 3, _ op: () async throws -> T) async throws -> T {
        var last: Error = AVGError.message("Unknown error")
        for i in 0..<attempts {
            do { return try await op() } catch {
                last = error
                if case AVGError.http(let code, _) = error, code != 429, code < 500 { throw error }
                if error is CancellationError { throw error }
                try await Task.sleep(nanoseconds: UInt64(1_500_000_000 * Double(i + 1)))
            }
        }
        throw last
    }
}

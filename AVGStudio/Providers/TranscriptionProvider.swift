import Foundation

/// Optional real word-level caption timing via AssemblyAI, mirroring the PC's
/// `avg.pipeline.phases.captions._assemblyai_poll`/`_get_words`: upload the
/// finished narration audio, submit a transcription job, poll until done,
/// return real per-word `{text, start, end}` timing. Used only when an
/// AssemblyAI key is configured; ANY failure here degrades to the estimated
/// timing in `Pipeline.captions()` rather than failing the phase - same
/// "no key OR a failing key both mean: use the estimate" invariant the PC
/// names explicitly.
enum AssemblyAI {
    private static let base = "https://api.assemblyai.com/v2"
    private static let pollInterval: UInt64 = 3_000_000_000 // 3s, matches the PC's own interval
    private static let maxPolls = 60 // ~3 minutes, matches the PC's own poll budget

    static func words(fromAudio url: URL, key: String) async throws -> [WordTiming] {
        let bytes = try Data(contentsOf: url)

        var upReq = URLRequest(url: URL(string: "\(base)/upload")!)
        upReq.httpMethod = "POST"
        upReq.setValue(key, forHTTPHeaderField: "authorization")
        upReq.httpBody = bytes
        let (upData, upResp) = try await URLSession.shared.data(for: upReq)
        guard let upHTTP = upResp as? HTTPURLResponse, (200..<300).contains(upHTTP.statusCode) else {
            throw AVGError.message("AssemblyAI upload failed.")
        }
        guard let uploadURL = (try HTTP.object(upData))["upload_url"] as? String else {
            throw AVGError.message("AssemblyAI upload returned no upload_url.")
        }

        var subReq = URLRequest(url: URL(string: "\(base)/transcript")!)
        subReq.httpMethod = "POST"
        subReq.setValue(key, forHTTPHeaderField: "authorization")
        subReq.setValue("application/json", forHTTPHeaderField: "Content-Type")
        subReq.httpBody = try JSONSerialization.data(withJSONObject: ["audio_url": uploadURL])
        let (subData, subResp) = try await URLSession.shared.data(for: subReq)
        guard let subHTTP = subResp as? HTTPURLResponse, (200..<300).contains(subHTTP.statusCode) else {
            throw AVGError.message("AssemblyAI transcript submission failed.")
        }
        guard let transcriptID = (try HTTP.object(subData))["id"] as? String else {
            throw AVGError.message("AssemblyAI transcript submission returned no id.")
        }

        for _ in 0..<maxPolls {
            try Task.checkCancellation()
            var pollReq = URLRequest(url: URL(string: "\(base)/transcript/\(transcriptID)")!)
            pollReq.setValue(key, forHTTPHeaderField: "authorization")
            let (pollData, pollResp) = try await URLSession.shared.data(for: pollReq)
            guard let pollHTTP = pollResp as? HTTPURLResponse, (200..<300).contains(pollHTTP.statusCode) else {
                throw AVGError.message("AssemblyAI poll failed.")
            }
            let o = try HTTP.object(pollData)
            let status = o["status"] as? String
            if status == "completed" {
                let raw = (o["words"] as? [[String: Any]]) ?? []
                return raw.compactMap { w in
                    guard let text = (w["text"] as? String)?.trimmed, !text.isEmpty else { return nil }
                    let startMs = (w["start"] as? NSNumber)?.doubleValue ?? 0
                    let endMs = (w["end"] as? NSNumber)?.doubleValue ?? 0
                    return WordTiming(text: text, start: startMs / 1000.0, end: endMs / 1000.0)
                }
            }
            if status == "error" {
                let msg = (o["error"] as? String) ?? "unknown error"
                throw AVGError.message("AssemblyAI transcription error: \(msg)")
            }
            try await Task.sleep(nanoseconds: pollInterval)
        }
        throw AVGError.message("AssemblyAI transcription timed out.")
    }
}

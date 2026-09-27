import Foundation
import AVFoundation

/// Ported from avg.media.vendor.avg.remove_silences_core - the PC's
/// threshold-based dead-air stripper - via avg.pipeline.phases.voice's
/// `_clean_segment` wrapper (the retry-once-if-over-cap policy).
///
/// pydub's `detect_nonsilent` works on a per-ms dBFS scan; this reimplements
/// the same shape (a window-by-window loudness scan, silence runs only
/// count once they last `minSilenceMs`, kept chunks get up to `keepMs` of
/// trailing pad copied from the gap they're cut from - never both sides,
/// matching the PC's own `sound[gap_e - pad:gap_e]` slice) at a coarser
/// 20ms window instead of 1ms - accurate enough at the 300-500ms silence
/// lengths this app's aggressiveness presets use, and about 20x cheaper to
/// scan on a phone.
enum SilenceTrim {
    struct Result {
        let originalSeconds: Double
        let cleanedSeconds: Double
        /// `nil` when there's nothing meaningful to divide by (no speech
        /// detected, or a zero-length original) - matches the PC's own
        /// `_removed_pct`'s "no cap concern" `None`, not a 0% removed.
        let removedPercent: Double?
    }

    private static let windowSeconds = 0.02 // 20ms scan window.

    /// Writes a dead-air-trimmed copy of `input` to `output` (linear PCM
    /// .caf - this project's own intermediate audio format, see
    /// Pipeline.voice()) at the given `(thresholdDBFS, minSilenceMs,
    /// keepMs)` triple. Trailing silence after the last speech chunk is
    /// dropped entirely (no pad) - the PC's own behavior when there is no
    /// beat/timeline/scene-map window protecting it, which iOS never has
    /// at this pipeline point either.
    static func clean(input: URL, output: URL, thresholdDBFS: Double, minSilenceMs: Int, keepMs: Int) throws -> Result {
        let file = try AVAudioFile(forReading: input)
        let format = file.processingFormat
        let sampleRate = format.sampleRate
        let frameCount = AVAudioFrameCount(file.length)
        guard frameCount > 0, let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount) else {
            throw AVGError.message("Couldn't read narration audio for dead-air trimming.")
        }
        try file.read(into: buffer)
        let totalFrames = Int(buffer.frameLength)
        let originalSeconds = Double(totalFrames) / sampleRate

        // Mono-downmix loudness per window - matches pydub's own dBFS
        // (RMS relative to full-scale amplitude, i.e. 1.0 for Float32).
        let windowFrames = max(1, Int(sampleRate * windowSeconds))
        let channels = Int(format.channelCount)
        var windowLoud: [Bool] = []
        if let chans = buffer.floatChannelData {
            var start = 0
            while start < totalFrames {
                let end = min(start + windowFrames, totalFrames)
                var sumSquares: Double = 0
                var n = 0
                for c in 0..<channels {
                    let p = chans[c]
                    for i in start..<end { sumSquares += Double(p[i]) * Double(p[i]); n += 1 }
                }
                let rms = n > 0 ? (sumSquares / Double(n)).squareRoot() : 0
                let dbfs = rms > 1e-9 ? 20 * log10(rms) : -160
                windowLoud.append(dbfs >= thresholdDBFS)
                start = end
            }
        }
        guard !windowLoud.isEmpty else {
            try copyAsIs(file: file, buffer: buffer, to: output)
            return Result(originalSeconds: originalSeconds, cleanedSeconds: originalSeconds, removedPercent: nil)
        }

        // Silence *runs* only count once they last >= minSilenceMs - a
        // short quiet window inside ordinary speech cadence stays part of
        // a non-silent chunk, exactly like pydub's own min_silence_len.
        let minSilenceWindows = Int(ceil(Double(minSilenceMs) / (windowSeconds * 1000)))
        var nonsilent: [(Int, Int)] = [] // frame ranges, half-open
        var i = 0
        var chunkStart: Int? = nil
        while i < windowLoud.count {
            if windowLoud[i] {
                if chunkStart == nil { chunkStart = i }
                i += 1
                continue
            }
            // Count the quiet run.
            var j = i
            while j < windowLoud.count && !windowLoud[j] { j += 1 }
            if j - i >= minSilenceWindows {
                // Genuine silence: close any open chunk here.
                if let cs = chunkStart { nonsilent.append((cs, i)); chunkStart = nil }
            } else if chunkStart == nil {
                // Too short to count as silence, but nothing loud opened a
                // chunk yet (e.g. leading room noise) - stays silence.
            }
            // Too-short quiet runs inside an open chunk fall through and
            // are simply skipped over (the chunk stays open across them).
            i = j
        }
        if let cs = chunkStart { nonsilent.append((cs, windowLoud.count)) }
        // Merge any chunks left adjacent by the "too short to split" rule.
        var merged: [(Int, Int)] = []
        for r in nonsilent {
            if let last = merged.last, r.0 <= last.1 { merged[merged.count - 1] = (last.0, max(last.1, r.1)) }
            else { merged.append(r) }
        }

        guard !merged.isEmpty else {
            try copyAsIs(file: file, buffer: buffer, to: output)
            return Result(originalSeconds: originalSeconds, cleanedSeconds: originalSeconds, removedPercent: nil)
        }

        let keepFrames = Int(Double(keepMs) / 1000.0 * sampleRate)
        var outFrameCount = 0
        var prevEndFrame = 0
        var segments: [(Int, Int)] = [] // frames to copy from the source, in order
        for (csw, cew) in merged {
            let cs = csw * windowFrames, ce = min(cew * windowFrames, totalFrames)
            let gapS = prevEndFrame, gapE = cs
            if gapE > gapS {
                let pad = min(keepFrames, gapE - gapS)
                if pad > 0 { segments.append((gapE - pad, gapE)); outFrameCount += pad }
            }
            segments.append((cs, ce)); outFrameCount += (ce - cs)
            prevEndFrame = ce
        }
        // Trailing silence after the last chunk: dropped entirely, matching
        // the PC's own no-protected-window behavior.

        guard let outBuffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(max(outFrameCount, 1))) else {
            throw AVGError.message("Couldn't assemble trimmed narration audio.")
        }
        outBuffer.frameLength = AVAudioFrameCount(outFrameCount)
        if let srcChans = buffer.floatChannelData, let dstChans = outBuffer.floatChannelData {
            var cursor = 0
            for (s, e) in segments {
                let len = e - s
                guard len > 0 else { continue }
                for c in 0..<channels {
                    (dstChans[c] + cursor).update(from: srcChans[c] + s, count: len)
                }
                cursor += len
            }
        }

        try write(outBuffer, format: format, to: output)
        let cleanedSeconds = Double(outFrameCount) / sampleRate
        let removedPercent = originalSeconds > 0 ? 100.0 * (originalSeconds - cleanedSeconds) / originalSeconds : nil
        return Result(originalSeconds: originalSeconds, cleanedSeconds: cleanedSeconds, removedPercent: removedPercent)
    }

    private static func copyAsIs(file: AVAudioFile, buffer: AVAudioPCMBuffer, to output: URL) throws {
        try write(buffer, format: file.processingFormat, to: output)
    }

    private static func write(_ buffer: AVAudioPCMBuffer, format: AVAudioFormat, to output: URL) throws {
        try? FileManager.default.removeItem(at: output)
        let outFile = try AVAudioFile(forWriting: output, settings: format.settings)
        try outFile.write(from: buffer)
    }
}

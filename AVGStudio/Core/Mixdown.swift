import AVFoundation

// Voice + music (+ sound effects) -> one m4a, ready for the existing mux.
//
// Why a separate file and not a change to VideoRenderer: the renderer already
// muxes ONE audio file under the finished video with a passthrough export (no
// second encode). Mixing before that keeps it untouched: the Render phase hands
// it `mix.m4a` instead of `narration.m4a`, and everything downstream - HEVC
// passthrough, takes, CapCut export of the plain narration - stays as it was.
//
// The music is laid on its own composition track and shaped with volume ramps
// from `MusicEnvelope` (ducked under speech, faded at both ends). A track
// shorter than the video repeats, with a short dip at each seam.

enum Mixdown {
    struct Effect {
        var url: URL
        /// Where it starts, in seconds from the beginning of the video.
        var at: Double
        var gainDB: Double = 0
    }

    struct Input {
        var voice: URL
        /// Where narration is speaking (`Project.audioSegments`), for ducking.
        var speech: [(start: Double, end: Double)]
        var music: URL?
        var musicGainDB: Double = SoundMix.defaultGainDB
        var effects: [Effect] = []
    }

    struct Outcome {
        var duration: Double
        /// How many times the music repeated to cover the video (0 = it was long enough).
        var loops: Int
    }

    /// The mix is exactly as long as the narration; music is cut or repeated to fit.
    @discardableResult
    static func render(_ input: Input, to out: URL) async throws -> Outcome {
        let comp = AVMutableComposition()
        let voiceAsset = AVURLAsset(url: input.voice)
        guard let voiceSource = try await voiceAsset.loadTracks(withMediaType: .audio).first,
              let voiceTrack = comp.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid) else {
            throw AVGError.message("The narration audio could not be read.")
        }
        let voiceDuration = try await voiceAsset.load(.duration)
        try voiceTrack.insertTimeRange(CMTimeRange(start: .zero, duration: voiceDuration), of: voiceSource, at: .zero)
        let total = voiceDuration.seconds

        var params: [AVMutableAudioMixInputParameters] = []
        var loops = 0

        if let musicURL = input.music {
            let asset = AVURLAsset(url: musicURL)
            guard let source = try await asset.loadTracks(withMediaType: .audio).first,
                  let track = comp.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid) else {
                throw AVGError.message("The music track could not be read.")
            }
            let length = try await asset.load(.duration).seconds
            guard length > 0.5 else { throw AVGError.message("The music track is too short to use.") }

            var cursor = 0.0
            var seams: [Double] = []
            while cursor < total - 0.001 {
                let piece = min(length, total - cursor)
                try track.insertTimeRange(CMTimeRange(start: .zero, duration: time(piece)), of: source, at: time(cursor))
                cursor += piece
                if cursor < total - 0.001 { seams.append(cursor) }
            }
            loops = seams.count

            let keys = MusicEnvelope.keyframes(speech: input.speech, total: total,
                                               gainDB: input.musicGainDB, loopPoints: seams)
            let p = AVMutableAudioMixInputParameters(track: track)
            // Each ramp starts exactly where the last one ended (same CMTime
            // values), so the ranges never overlap.
            for (a, b) in zip(keys, keys.dropFirst()) {
                let from = time(a.time), to = time(b.time)
                guard to > from else { continue }
                p.setVolumeRamp(fromStartVolume: Float(a.gain), toEndVolume: Float(b.gain),
                                timeRange: CMTimeRange(start: from, end: to))
            }
            params.append(p)
        }

        for effect in input.effects where effect.at >= 0 && effect.at < total {
            let asset = AVURLAsset(url: effect.url)
            guard let source = try await asset.loadTracks(withMediaType: .audio).first,
                  let track = comp.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid) else { continue }
            let length = try await asset.load(.duration).seconds
            let piece = min(length, total - effect.at)
            guard piece > 0.01 else { continue }
            try track.insertTimeRange(CMTimeRange(start: .zero, duration: time(piece)), of: source, at: time(effect.at))
            let p = AVMutableAudioMixInputParameters(track: track)
            p.setVolume(Float(min(2, SoundMix.linear(effect.gainDB))), at: .zero)
            params.append(p)
        }

        guard let export = AVAssetExportSession(asset: comp, presetName: AVAssetExportPresetAppleM4A) else {
            throw AVGError.message("Couldn't export the mixed audio.")
        }
        let mix = AVMutableAudioMix()
        mix.inputParameters = params
        export.audioMix = mix
        try? FileManager.default.removeItem(at: out)
        try FileManager.default.createDirectory(at: out.deletingLastPathComponent(), withIntermediateDirectories: true)
        try await export.export(to: out, as: .m4a)
        return Outcome(duration: total, loops: loops)
    }

    private static func time(_ seconds: Double) -> CMTime {
        CMTime(seconds: seconds, preferredTimescale: 600)
    }
}

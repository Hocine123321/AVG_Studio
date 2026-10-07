import Foundation
import AVFoundation

protocol VoiceProvider {
    /// Synthesizes `text` into `folder/<name>.<ext>` and returns the file
    /// name actually written. `name` lets a caller request several
    /// independent segments (Pipeline.voice() does this to give each
    /// `[pause]`-separated segment of the narration its own file, which it
    /// then stitches back together with real silence in between - see
    /// Pipeline.swift's `assemble`).
    func synthesize(_ text: String, into folder: URL, name: String) async throws -> String
}

struct ElevenLabsVoice: VoiceProvider {
    let key: String
    let voiceID: String
    let model: String
    /// 0...1: lower is more expressive and varied, higher more even.
    var stability: Double = 0.5
    /// 0.7...1.2, ElevenLabs' supported range for `speed`.
    var speed: Double = 1.0

    /// The request body's `voice_settings`. `speed` is only sent when it is not
    /// 1.0, so a default channel makes exactly the request it always did.
    static func voiceSettings(stability: Double, speed: Double) -> [String: Any] {
        var settings: [String: Any] = ["stability": min(1, max(0, stability))]
        let s = min(1.2, max(0.7, speed))
        if abs(s - 1.0) > 0.001 { settings["speed"] = s }
        return settings
    }

    func synthesize(_ text: String, into folder: URL, name: String) async throws -> String {
        let url = URL(string: "https://api.elevenlabs.io/v1/text-to-speech/\(voiceID)?output_format=mp3_44100_128")!
        let body: [String: Any] = ["text": text, "model_id": model,
                                   "voice_settings": Self.voiceSettings(stability: stability, speed: speed)]
        let data = try await HTTP.retrying({ try await HTTP.send(url, headers: ["xi-api-key": key, "Accept": "audio/mpeg"], json: body, provider: "elevenlabs") }, provider: "elevenlabs")
        let file = "\(name).mp3"
        try data.write(to: folder.appendingPathComponent(file))
        return file
    }
}

/// On-device speech (includes Premium/Enhanced and Personal Voice if downloaded).
final class AppleVoice: VoiceProvider {
    let identifier: String
    /// A multiple of the default speaking rate (0.7...1.2).
    let speed: Double
    init(identifier: String, speed: Double = 1.0) {
        self.identifier = identifier
        self.speed = speed
    }

    /// `AVSpeechUtterance.rate` is NOT a multiplier: it runs 0...1 with the
    /// default (~0.5) in the middle and is roughly logarithmic in loudness of
    /// change. Scaling the default by `speed` and clamping to the legal range
    /// is the simple mapping that behaves sensibly across 0.7x to 1.2x.
    static func utteranceRate(speed: Double) -> Float {
        let s = Float(min(1.2, max(0.7, speed)))
        return min(AVSpeechUtteranceMaximumSpeechRate, max(AVSpeechUtteranceMinimumSpeechRate, AVSpeechUtteranceDefaultSpeechRate * s))
    }

    static func voices() -> [AVSpeechSynthesisVoice] {
        AVSpeechSynthesisVoice.speechVoices().sorted {
            if $0.quality.rawValue != $1.quality.rawValue { return $0.quality.rawValue > $1.quality.rawValue }
            return $0.name < $1.name
        }
    }

    func synthesize(_ text: String, into folder: URL, name: String) async throws -> String {
        let file = "\(name).caf"
        let url = folder.appendingPathComponent(file)
        try? FileManager.default.removeItem(at: url)
        let synth = AVSpeechSynthesizer()
        let utt = AVSpeechUtterance(string: text)
        utt.voice = AVSpeechSynthesisVoice(identifier: identifier)
            ?? AppleVoice.voices().first(where: { $0.language.hasPrefix("en") })
            ?? AVSpeechSynthesisVoice(language: "en-US")
        utt.rate = Self.utteranceRate(speed: speed)

        // `synth` only needs to outlive this call, which it does: it's a
        // local `let` referenced by the closure passed to its own
        // `write(_:bufferCallback:)`, and ARC keeps a local alive across an
        // `await` suspension for as long as the function body can still
        // reference it - no separate `withExtendedLifetime` needed.
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            var file: AVAudioFile?
            var resumed = false
            synth.write(utt) { buffer in
                guard !resumed, let pcm = buffer as? AVAudioPCMBuffer else { return }
                if pcm.frameLength == 0 {
                    resumed = true
                    file = nil
                    cont.resume()
                    return
                }
                do {
                    if file == nil {
                        file = try AVAudioFile(forWriting: url, settings: pcm.format.settings,
                                               commonFormat: pcm.format.commonFormat, interleaved: pcm.format.isInterleaved)
                    }
                    try file?.write(from: pcm)
                } catch {
                    resumed = true
                    cont.resume(throwing: error)
                }
            }
        }
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw AVGError.message("The voice produced no audio. Try another voice in Settings.")
        }
        return file
    }
}

/// Not a PC provider - `avg.providers.voice.providers` has exactly
/// ElevenLabs and Edge TTS - added on request as a third voice option,
/// reusing the same `generateContent` endpoint `GeminiText`/`GeminiImages`
/// already call, this time with `responseModalities: ["AUDIO"]` and a
/// `speechConfig` naming one of Google's fixed prebuilt voices.
///
/// Gemini's TTS reply carries raw 24kHz/16-bit/mono PCM in `inlineData`
/// (confirmed by the `mimeType` it sends back, `audio/L16;rate=24000` or
/// similar - never a container format), so this - unlike every other
/// voice provider here, which gets back a playable file directly - has to
/// prepend a PCM WAV header itself before AVFoundation (or anything else
/// downstream) can open the file at all.
struct GeminiVoice: VoiceProvider {
    let key: String
    let model: String
    let voiceName: String

    /// The complete, fixed set of prebuilt voices Gemini's TTS models
    /// support (Google's own docs enumerate exactly these - there is no
    /// list endpoint for voices the way ElevenLabs has one, only for the
    /// TTS-capable MODEL id, which does come from a live catalogue - see
    /// `ModelCatalogSources.geminiVoiceModels`).
    static let prebuiltVoices: [String] = [
        "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede",
        "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
        "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
        "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
        "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
    ]

    func synthesize(_ text: String, into folder: URL, name: String) async throws -> String {
        let bareModel = model.trimmed.isEmpty ? "gemini-2.5-flash-preview-tts" : model.trimmed
        let voice = voiceName.trimmed.isEmpty ? "Kore" : voiceName.trimmed
        // Header-only auth, same as GeminiText (see the note there).
        let url = URL(string: "https://generativelanguage.googleapis.com/v1beta/models/\(bareModel):generateContent")!
        let body: [String: Any] = [
            "contents": [["parts": [["text": text]]]],
            "generationConfig": [
                "responseModalities": ["AUDIO"],
                "speechConfig": ["voiceConfig": ["prebuiltVoiceConfig": ["voiceName": voice]]],
            ],
        ]
        let data = try await HTTP.retrying({ try await HTTP.send(url, headers: ["x-goog-api-key": key], json: body, provider: "gemini") }, provider: "gemini")
        let o = try HTTP.object(data)
        let parts = (((o["candidates"] as? [[String: Any]])?.first?["content"] as? [String: Any])?["parts"] as? [[String: Any]]) ?? []
        for p in parts {
            if let inline = (p["inlineData"] ?? p["inline_data"]) as? [String: Any],
               let b64 = inline["data"] as? String, let pcm = Data(base64Encoded: b64) {
                // Gemini's own docs fix this at 24kHz/16-bit/mono for every
                // TTS model it currently offers - there is nothing in the
                // reply to negotiate a different rate from.
                let wav = GeminiVoice.wavHeader(pcmByteCount: pcm.count, sampleRate: 24_000, channels: 1, bitsPerSample: 16) + pcm
                let file = "\(name).wav"
                try wav.write(to: folder.appendingPathComponent(file))
                return file
            }
        }
        throw AVGError.message("Gemini returned no audio.")
    }

    /// A minimal 44-byte canonical PCM WAV header - the one container
    /// format every audio API in this app (AVAudioFile included) can open
    /// without help, and the smallest one that fits `pcm`'s raw bytes
    /// without re-encoding them.
    private static func wavHeader(pcmByteCount: Int, sampleRate: Int, channels: Int, bitsPerSample: Int) -> Data {
        let blockAlign = channels * bitsPerSample / 8
        let byteRate = sampleRate * blockAlign
        var d = Data()
        func le32(_ v: Int) { var x = UInt32(v).littleEndian; d.append(Data(bytes: &x, count: 4)) }
        func le16(_ v: Int) { var x = UInt16(v).littleEndian; d.append(Data(bytes: &x, count: 2)) }
        d.append("RIFF".data(using: .ascii)!)
        le32(36 + pcmByteCount)
        d.append("WAVE".data(using: .ascii)!)
        d.append("fmt ".data(using: .ascii)!)
        le32(16)               // fmt chunk size
        le16(1)                // PCM
        le16(channels)
        le32(sampleRate)
        le32(byteRate)
        le16(blockAlign)
        le16(bitsPerSample)
        d.append("data".data(using: .ascii)!)
        le32(pcmByteCount)
        return d
    }
}

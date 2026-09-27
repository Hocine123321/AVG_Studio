import Foundation
import AVFoundation

protocol VoiceProvider {
    /// Writes narration audio into `folder`, returns the file name.
    func synthesize(_ text: String, into folder: URL) async throws -> String
}

struct ElevenLabsVoice: VoiceProvider {
    let key: String
    let voiceID: String
    let model: String

    func synthesize(_ text: String, into folder: URL) async throws -> String {
        let url = URL(string: "https://api.elevenlabs.io/v1/text-to-speech/\(voiceID)?output_format=mp3_44100_128")!
        let body: [String: Any] = ["text": text, "model_id": model]
        let data = try await HTTP.retrying { try await HTTP.send(url, headers: ["xi-api-key": key, "Accept": "audio/mpeg"], json: body) }
        let name = "narration.mp3"
        try data.write(to: folder.appendingPathComponent(name))
        return name
    }
}

/// On-device speech (includes Premium/Enhanced and Personal Voice if downloaded).
final class AppleVoice: VoiceProvider {
    let identifier: String
    init(identifier: String) { self.identifier = identifier }

    static func voices() -> [AVSpeechSynthesisVoice] {
        AVSpeechSynthesisVoice.speechVoices().sorted {
            if $0.quality.rawValue != $1.quality.rawValue { return $0.quality.rawValue > $1.quality.rawValue }
            return $0.name < $1.name
        }
    }

    func synthesize(_ text: String, into folder: URL) async throws -> String {
        let name = "narration.caf"
        let url = folder.appendingPathComponent(name)
        try? FileManager.default.removeItem(at: url)
        let synth = AVSpeechSynthesizer()
        let utt = AVSpeechUtterance(string: text)
        utt.voice = AVSpeechSynthesisVoice(identifier: identifier)
            ?? AppleVoice.voices().first(where: { $0.language.hasPrefix("en") })
            ?? AVSpeechSynthesisVoice(language: "en-US")
        utt.rate = AVSpeechUtteranceDefaultSpeechRate

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
        withExtendedLifetime(synth) {}
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw AVGError.message("The voice produced no audio. Try another voice in Settings.")
        }
        return name
    }
}

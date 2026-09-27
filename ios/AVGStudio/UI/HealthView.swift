import SwiftUI
import AVFoundation

struct HealthView: View {
    @Environment(Store.self) private var store

    private struct Check: Identifiable {
        let id = UUID()
        let title: String
        let detail: String
        let ok: Bool
    }

    private var checks: [Check] {
        let s = store.settings
        var out: [Check] = []
        out.append(Check(title: "Apple Intelligence", detail: AppleText.isAvailable ? "On-device writer ready" : "Not available — use another writer", ok: AppleText.isAvailable || s.text != .apple))
        let keyFor: [TextProviderKind: APIKey] = [.gemini: .gemini, .groq: .groq]
        if let k = keyFor[s.text] { out.append(Check(title: "\(k.label) key", detail: Keychain.has(k) ? "Saved" : "Missing", ok: Keychain.has(k))) }
        if s.image == .gemini { out.append(Check(title: "Gemini key (images)", detail: Keychain.has(.gemini) ? "Saved" : "Missing", ok: Keychain.has(.gemini))) }
        if s.image == .openai { out.append(Check(title: "OpenAI key", detail: Keychain.has(.openai) ? "Saved" : "Missing", ok: Keychain.has(.openai))) }
        if s.voice == .elevenlabs { out.append(Check(title: "ElevenLabs key", detail: Keychain.has(.elevenlabs) ? "Saved" : "Missing", ok: Keychain.has(.elevenlabs))) }
        let voices = AVSpeechSynthesisVoice.speechVoices()
        out.append(Check(title: "Voices", detail: "\(voices.count) installed, \(voices.filter { $0.quality != .default }.count) high quality", ok: !voices.isEmpty))
        let hevc = AVAssetExportSession.allExportPresets().contains(AVAssetExportPresetHEVCHighestQuality)
        out.append(Check(title: "Hardware video encoder", detail: hevc ? "HEVC available" : "HEVC missing", ok: hevc))
        let free: Int64 = ((try? Store.root.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]))?.volumeAvailableCapacityForImportantUsage ?? nil) ?? 0
        let gb = Double(free) / 1_000_000_000
        out.append(Check(title: "Free storage", detail: String(format: "%.1f GB", gb), ok: gb > 1))
        return out
    }

    var body: some View {
        List(checks) { c in
            HStack {
                Image(systemName: c.ok ? "checkmark.circle.fill" : "xmark.octagon.fill").foregroundStyle(c.ok ? .green : .red)
                VStack(alignment: .leading) {
                    Text(c.title).font(.subheadline.weight(.medium))
                    Text(c.detail).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .screenBackground()
        .navigationTitle("App health")
    }
}

import SwiftUI
import AVFoundation

struct HealthView: View {
    @Environment(Store.self) private var store

    private struct Check: Identifiable {
        let id = UUID()
        let title: String
        let detail: String
        let ok: Bool
        /// Mirrors the PC's `avg.core.health.Component.required` - overall
        /// health only reads `required` checks (`Report.healthy`'s own
        /// `all(check.ok for check in self.checks if check.required)`); a
        /// missing optional dependency or key is shown but never turns the
        /// whole page red, the same "a degradable dependency stops being
        /// treated as an alarm" the PC's own doc comment names.
        var required: Bool = true
    }

    private var checks: [Check] {
        let s = store.settings
        var out: [Check] = []
        out.append(Check(title: "Apple Intelligence", detail: AppleText.isAvailable ? "On-device writer ready" : "Not available — use another writer", ok: AppleText.isAvailable || s.text != .apple, required: s.text == .apple))
        let keyFor: [TextProviderKind: APIKey] = [.gemini: .gemini, .groq: .groq]
        if let k = keyFor[s.text] { out.append(Check(title: "\(k.label) key", detail: Keychain.has(k) ? "Saved" : "Missing", ok: Keychain.has(k), required: true)) }
        if s.image == .gemini { out.append(Check(title: "Gemini key (images)", detail: Keychain.has(.gemini) ? "Saved" : "Missing", ok: Keychain.has(.gemini), required: true)) }
        if s.image == .openai { out.append(Check(title: "OpenAI key", detail: Keychain.has(.openai) ? "Saved" : "Missing", ok: Keychain.has(.openai), required: true)) }
        if s.voice == .elevenlabs { out.append(Check(title: "ElevenLabs key", detail: Keychain.has(.elevenlabs) ? "Saved" : "Missing", ok: Keychain.has(.elevenlabs), required: true)) }
        if s.voice == .gemini { out.append(Check(title: "Gemini key (voice)", detail: Keychain.has(.gemini) ? "Saved" : "Missing", ok: Keychain.has(.gemini), required: true)) }
        // AssemblyAI is never required: missing it just means captions fall
        // back to the estimated per-word timing (Pipeline.captions()'s own
        // synthetic-timing path) rather than real transcription - degraded,
        // not broken, mirroring the PC's own "assemblyai" key check which
        // Component never marks required either.
        out.append(Check(title: "AssemblyAI key", detail: Keychain.has(.assemblyai) ? "Saved — real caption timing" : "Missing — captions use estimated timing", ok: Keychain.has(.assemblyai), required: false))
        let voices = AVSpeechSynthesisVoice.speechVoices()
        out.append(Check(title: "Voices", detail: "\(voices.count) installed, \(voices.filter { $0.quality != .default }.count) high quality", ok: !voices.isEmpty, required: false))
        let hevc = AVAssetExportSession.allExportPresets().contains(AVAssetExportPresetHEVCHighestQuality)
        out.append(Check(title: "Hardware video encoder", detail: hevc ? "HEVC available" : "HEVC missing — falls back to H.264", ok: hevc, required: false))
        let free: Int64 = ((try? Store.root.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]))?.volumeAvailableCapacityForImportantUsage ?? nil) ?? 0
        let gb = Double(free) / 1_000_000_000
        out.append(Check(title: "Free storage", detail: String(format: "%.1f GB", gb), ok: gb > 1, required: true))
        return out
    }

    private var healthy: Bool { checks.filter(\.required).allSatisfy(\.ok) }

    var body: some View {
        List {
            if !healthy {
                Section {
                    HStack {
                        Image(systemName: "xmark.octagon.fill").foregroundStyle(.red)
                        Text("Something required needs attention before your next render.")
                            .font(.subheadline.weight(.medium))
                    }
                }
            }
            Section {
                ForEach(checks) { c in
                    HStack {
                        Image(systemName: icon(for: c)).foregroundStyle(color(for: c))
                        VStack(alignment: .leading) {
                            Text(c.title).font(.subheadline.weight(.medium))
                            Text(c.detail).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if !c.required {
                            Text("Optional").font(.caption2).foregroundStyle(.tertiary)
                        }
                    }
                }
            }
        }
        .screenBackground()
        .navigationTitle("App health")
    }

    private func icon(for c: Check) -> String {
        if c.ok { return "checkmark.circle.fill" }
        return c.required ? "xmark.octagon.fill" : "exclamationmark.triangle.fill"
    }
    private func color(for c: Check) -> Color {
        if c.ok { return .green }
        return c.required ? .red : .yellow
    }
}

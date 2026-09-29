import SwiftUI
import AVFoundation

/// The mock's own App health screen: 3-up status tiles, a provider/
/// dependency list, and an activity card - restyled from a native `List`
/// onto the same ScrollView+glassCard pattern as the rest of Skin v2.
/// All data is real and unchanged from before (the same `Check` model,
/// same `store.log`); only the layout is new.
struct HealthView: View {
    @Environment(Store.self) private var store

    private struct Check: Identifiable {
        // Stable identity. It used to be `UUID()`, regenerated on every
        // evaluation of the computed `checks`, so SwiftUI saw a brand-new
        // list each time.
        var id: String { title }
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
        /// Whether this check is about a saved API key - drives the
        /// "Keys saved" summary tile.
        var isKey: Bool = false
    }

    /// Computed ONCE per appearance (and when the chosen providers change),
    /// not on every read. It used to be a computed property that `healthy`,
    /// `issueCount`, `freeGBText`, `keyChecks` and `body` each re-evaluated -
    /// 5+ passes of a speech-voice enumeration, a Keychain query per key and
    /// a disk-capacity read, every time anything on screen changed.
    @State private var checks: [Check] = []

    private static func makeChecks(_ s: AppSettings) -> [Check] {
        var out: [Check] = []
        out.append(Check(title: "Apple Intelligence", detail: AppleText.isAvailable ? "On-device writer ready" : "Not available — use another writer", ok: AppleText.isAvailable || s.text != .apple, required: s.text == .apple))
        let keyFor: [TextProviderKind: APIKey] = [.gemini: .gemini, .groq: .groq]
        if let k = keyFor[s.text] { out.append(Check(title: "\(k.label) key", detail: Keychain.has(k) ? "Saved" : "Missing", ok: Keychain.has(k), required: true, isKey: true)) }
        if s.image == .gemini { out.append(Check(title: "Gemini key (images)", detail: Keychain.has(.gemini) ? "Saved" : "Missing", ok: Keychain.has(.gemini), required: true, isKey: true)) }
        if s.image == .openai { out.append(Check(title: "OpenAI key", detail: Keychain.has(.openai) ? "Saved" : "Missing", ok: Keychain.has(.openai), required: true, isKey: true)) }
        if s.voice == .elevenlabs { out.append(Check(title: "ElevenLabs key", detail: Keychain.has(.elevenlabs) ? "Saved" : "Missing", ok: Keychain.has(.elevenlabs), required: true, isKey: true)) }
        if s.voice == .gemini { out.append(Check(title: "Gemini key (voice)", detail: Keychain.has(.gemini) ? "Saved" : "Missing", ok: Keychain.has(.gemini), required: true, isKey: true)) }
        // AssemblyAI is never required: missing it just means captions fall
        // back to the estimated per-word timing (Pipeline.captions()'s own
        // synthetic-timing path) rather than real transcription - degraded,
        // not broken, mirroring the PC's own "assemblyai" key check which
        // Component never marks required either.
        out.append(Check(title: "AssemblyAI key", detail: Keychain.has(.assemblyai) ? "Saved — real caption timing" : "Missing — captions use estimated timing", ok: Keychain.has(.assemblyai), required: false, isKey: true))
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
    private var issueCount: Int { checks.filter { $0.required && !$0.ok }.count }
    private var freeGBText: String { checks.first { $0.title == "Free storage" }?.detail ?? "—" }
    private var keyChecks: [Check] { checks.filter(\.isKey) }

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                if checks.isEmpty { ProgressView().padding(.top, 40) }
                if !checks.isEmpty {
                HStack(spacing: 10) {
                    statTile(
                        icon: healthy ? "checkmark.seal.fill" : "xmark.octagon.fill",
                        value: healthy ? "Healthy" : "\(issueCount)",
                        label: healthy ? "All required checks pass" : "issue\(issueCount == 1 ? "" : "s") to fix",
                        tint: healthy ? Theme.ok : Theme.bad
                    )
                    statTile(icon: "internaldrive.fill", value: freeGBText, label: "Free storage", tint: Theme.accent)
                    statTile(
                        icon: "key.fill",
                        value: "\(keyChecks.filter(\.ok).count)/\(keyChecks.count)",
                        label: "Keys saved",
                        tint: Theme.accent
                    )
                }

                VStack(spacing: 0) {
                    ForEach(Array(checks.enumerated()), id: \.element.id) { i, c in
                        if i > 0 { hairline }
                        checkRow(c)
                    }
                }
                .padding(.horizontal, 14)
                .glassCard(24, padding: 6)
                }

                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Text("Recent activity").sectionHeader()
                        Spacer()
                        NavigationLink("See all", destination: ErrorLogView())
                            .font(.caption.weight(.semibold)).foregroundStyle(Theme.accent)
                    }
                    if store.log.isEmpty {
                        Text("No activity yet.").font(.caption).foregroundStyle(Theme.ink2)
                            .padding(.vertical, 4)
                    } else {
                        VStack(spacing: 0) {
                            ForEach(Array(store.log.prefix(3).enumerated()), id: \.element.id) { i, e in
                                if i > 0 { hairline }
                                HStack(spacing: 10) {
                                    Image(systemName: e.isError ? "exclamationmark.triangle.fill" : "checkmark.circle")
                                        .foregroundStyle(e.isError ? Theme.bad : Theme.ink2)
                                    Text(e.message).font(.footnote).foregroundStyle(Theme.ink).lineLimit(1)
                                    Spacer(minLength: 8)
                                    Text(e.date, style: .time).font(.caption2).foregroundStyle(Theme.ink2)
                                }
                                .padding(.vertical, 8)
                            }
                        }
                    }
                }
                .padding(14)
                .glassCard(24, padding: 0)
            }
            .padding(16)
        }
        .screenBackground()
        .navigationTitle("App health")
        .onAppear { checks = Self.makeChecks(store.settings) }
        .onChange(of: store.settings.text) { _, _ in checks = Self.makeChecks(store.settings) }
        .onChange(of: store.settings.image) { _, _ in checks = Self.makeChecks(store.settings) }
        .onChange(of: store.settings.voice) { _, _ in checks = Self.makeChecks(store.settings) }
    }

    private var hairline: some View {
        Rectangle().fill(Theme.glassEdge).frame(height: 0.5)
    }

    @ViewBuilder private func statTile(icon: String, value: String, label: String, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Image(systemName: icon).font(.title3).foregroundStyle(tint)
            Text(value).font(.system(size: 17, weight: .bold)).foregroundStyle(Theme.ink).lineLimit(1).minimumScaleFactor(0.7)
            Text(label).font(.caption2).foregroundStyle(Theme.ink2).lineLimit(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .glassCard(20, padding: 0)
    }

    @ViewBuilder private func checkRow(_ c: Check) -> some View {
        HStack(spacing: 10) {
            Image(systemName: icon(for: c)).foregroundStyle(color(for: c))
            VStack(alignment: .leading, spacing: 2) {
                Text(c.title).font(.subheadline.weight(.medium)).foregroundStyle(Theme.ink)
                Text(c.detail).font(.caption).foregroundStyle(Theme.ink2)
            }
            Spacer(minLength: 8)
            if !c.required {
                Text("Optional").font(.caption2).foregroundStyle(Theme.ink2)
            }
        }
        .padding(.vertical, 10)
    }

    private func icon(for c: Check) -> String {
        if c.ok { return "checkmark.circle.fill" }
        return c.required ? "xmark.octagon.fill" : "exclamationmark.triangle.fill"
    }
    private func color(for c: Check) -> Color {
        if c.ok { return Theme.ok }
        return c.required ? Theme.bad : Theme.accent
    }
}

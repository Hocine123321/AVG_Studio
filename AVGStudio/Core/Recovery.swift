import Foundation
import SwiftUI
import Network

/// Smart failure recovery. `HTTP.retrying` already retries automatically
/// (the classified `POLICY` table). When that is exhausted - or the failure
/// was never retryable - the render used to just stop with a red error.
/// Now the render PAUSES and asks: `RecoveryPlan` says what went wrong in
/// plain words and offers only the choices that can actually help for THAT
/// kind of failure (a quota problem never offers "retry", a content-filter
/// block never offers "cheaper model").
///
/// Safeguards (trigger -> automatic handling -> what the user is asked):
///  1. busy/network/timeout -> 3 retries, longer waits (HTTP.retrying) -> retry / cheaper model / other provider
///  2. rate limit           -> 5 slow retries                          -> wait a minute / other provider / cheaper model
///  3. quota or billing     -> none (retrying can't fix it)            -> other provider / free provider / top up
///  4. bad or missing key   -> none                                    -> fix key / other provider
///  5. model not found/long -> none                                    -> other model / other provider
///  6. content filter       -> none                                    -> reword the topic / other provider
///  7. device offline       -> waits for connection, auto-resumes      -> cancel
///  8. low storage          -> none                                    -> says how much space to free
///  9. same provider failing repeatedly -> remembered                  -> suggests switching for good
enum RecoveryAction: Identifiable, Equatable {
    case retry
    case waitAndRetry(seconds: Int)
    case cheaperModel
    case otherProvider(name: String)
    case freeProvider
    case openKeys
    case reword
    case stop

    var id: String {
        switch self {
        case .retry: return "retry"
        case .waitAndRetry(let s): return "wait\(s)"
        case .cheaperModel: return "cheaper"
        case .otherProvider(let n): return "other-\(n)"
        case .freeProvider: return "free"
        case .openKeys: return "keys"
        case .reword: return "reword"
        case .stop: return "stop"
        }
    }
    var label: String {
        switch self {
        case .retry: return "Try again"
        case .waitAndRetry(let s): return s >= 60 ? "Wait \(s / 60) min, then retry" : "Wait \(s)s, then retry"
        case .cheaperModel: return "Use the fallback (cheaper) model"
        case .otherProvider(let n): return "Switch to \(n)"
        case .freeProvider: return "Switch to a free provider"
        case .openKeys: return "Fix my API key"
        case .reword: return "Reword the topic and retry"
        case .stop: return "Stop here"
        }
    }
    var symbol: String {
        switch self {
        case .retry, .waitAndRetry: return "arrow.clockwise"
        case .cheaperModel: return "leaf"
        case .otherProvider, .freeProvider: return "arrow.triangle.swap"
        case .openKeys: return "key"
        case .reword: return "text.badge.plus"
        case .stop: return "xmark.circle"
        }
    }
    var isDestructive: Bool { self == .stop }
}

struct RecoveryPlan: Identifiable {
    let id = UUID()
    let projectID: String
    let phase: PhaseID
    let title: String
    let reason: String
    let actions: [RecoveryAction]
    /// Set when the same provider keeps failing (safeguard 9).
    var pattern: String? = nil
}

/// Which AI a phase uses, for picking the right fallback model.
enum AIKind { case writer, image, voice }

@MainActor
enum Recovery {
    static func aiKind(for phase: PhaseID) -> AIKind {
        switch phase { case .visuals: return .image; case .voice: return .voice; default: return .writer }
    }

    /// What an AI is running on right now: the provider, the model, and that
    /// provider's built-in light preset. `nil` when the AI runs on the device
    /// (Apple) or on a provider with one model and no preset (Stable Horde:
    /// only a user-chosen fallback applies there).
    struct Slot {
        let provider: String
        let current: String
        let preset: String?
    }

    static func slot(_ kind: AIKind, settings s: AppSettings) -> Slot? {
        switch kind {
        case .writer:
            switch s.text {
            case .gemini: return Slot(provider: "gemini", current: s.geminiModel, preset: "gemini-2.5-flash-lite")
            case .groq: return Slot(provider: "groq", current: s.groqModel, preset: "llama-3.1-8b-instant")
            case .pollinations: return Slot(provider: "pollinations", current: s.pollinationsTextModel, preset: "openai-fast")
            case .apple: return nil
            }
        case .image:
            switch s.image {
            case .gemini: return Slot(provider: "gemini", current: s.geminiImageModel, preset: "gemini-2.5-flash-image")
            case .pollinations: return Slot(provider: "pollinations", current: s.pollinationsImageModel, preset: "flux")
            default: return nil
            }
        case .voice:
            switch s.voice {
            case .elevenlabs: return Slot(provider: "elevenlabs", current: s.elevenModel, preset: "eleven_flash_v2_5")
            case .gemini: return Slot(provider: "gemini", current: s.geminiVoiceModel, preset: nil)
            case .apple: return nil
            }
        }
    }

    /// The key a kind of AI is filed under in `ModelHistory`.
    static func historyKey(_ kind: AIKind) -> String {
        switch kind {
        case .writer: return "writer"
        case .image: return "image"
        case .voice: return "voice"
        }
    }

    /// Models already dropped to (or away from) in the current streak of
    /// trouble, per kind, so two failing models are not offered back and forth
    /// forever. Cleared by `noteSuccess()`, like `failures`.
    private static var triedThisStreak: [String: Set<String>] = [:]

    /// The model this AI would drop to: the user's own fallback if set, else
    /// the newest model of this provider that recently worked (`prefs.history`,
    /// vetoed by any probe that marked it bad), else a built-in light preset.
    /// `nil` when there is nothing to drop to. See `FallbackPlanner` for the order.
    static func fallbackModel(_ kind: AIKind, settings s: AppSettings, prefs: ModelPrefs? = nil) -> String? {
        let own: String
        switch kind {
        case .writer: own = s.writerFallbackModel
        case .image: own = s.imageFallbackModel
        case .voice: own = s.voiceFallbackModel
        }
        guard let sl = slot(kind, settings: s) else { return own.isEmpty ? nil : own }
        // Only the text lists are ever probed (ModelProbe.isProbeable), so only the writer has vetoes.
        var excluded = triedThisStreak[historyKey(kind)] ?? []
        if kind == .writer, let results = prefs?.probes.byCatalog["\(sl.provider)_text"] {
            excluded.formUnion(results.filter { !$0.value.works }.map { $0.key })
        }
        return FallbackPlanner.pick(
            userChoice: own, provider: sl.provider, current: sl.current, preset: sl.preset,
            history: prefs?.recentUses(kind: historyKey(kind)) ?? [], excluded: excluded)
    }

    /// True when the fallback differs from what is already set (otherwise
    /// offering it would just repeat the same failing request).
    static func fallbackIsUseful(_ kind: AIKind, settings s: AppSettings, prefs: ModelPrefs? = nil) -> Bool {
        guard let m = fallbackModel(kind, settings: s, prefs: prefs) else { return false }
        switch kind {
        case .writer:
            switch s.text {
            case .gemini: return m != s.geminiModel
            case .groq: return m != s.groqModel
            case .pollinations: return m != s.pollinationsTextModel
            case .apple: return false
            }
        case .image:
            switch s.image {
            case .gemini: return m != s.geminiImageModel
            case .pollinations: return m != s.pollinationsImageModel
            case .stablehorde: return m != s.stableHordeModel
            default: return false
            }
        case .voice:
            switch s.voice {
            case .elevenlabs: return m != s.elevenModel
            case .gemini: return m != s.geminiVoiceModel
            case .apple: return false
            }
        }
    }

    /// Switches the AI for `phase` to its fallback model FOR THIS PROJECT'S
    /// RUN. Returns the model name applied, or nil if there was nothing to
    /// switch to. The person's own model is put back when the run ends.
    @discardableResult
    static func applyFallback(phase: PhaseID, store: Store, project: String) -> String? {
        let kind = aiKind(for: phase)
        // What THIS video is running on: its own fallbacks included, so a
        // fallback already applied is not offered again.
        let s = store.effectiveSettings(for: project)
        guard fallbackIsUseful(kind, settings: s, prefs: store.modelPrefs),
              let m = fallbackModel(kind, settings: s, prefs: store.modelPrefs) else { return nil }
        // The model being left is remembered so it is not offered straight back if `m` fails too.
        if let leaving = slot(kind, settings: s)?.current { triedThisStreak[historyKey(kind), default: []].insert(leaving) }
        switch kind {
        case .writer:
            switch s.text {
            case .gemini: store.overrideForRun(\.geminiModel, to: m, project: project)
            case .groq: store.overrideForRun(\.groqModel, to: m, project: project)
            case .pollinations: store.overrideForRun(\.pollinationsTextModel, to: m, project: project)
            case .apple: return nil
            }
        case .image:
            switch s.image {
            case .gemini: store.overrideForRun(\.geminiImageModel, to: m, project: project)
            case .pollinations: store.overrideForRun(\.pollinationsImageModel, to: m, project: project)
            case .stablehorde: store.overrideForRun(\.stableHordeModel, to: m, project: project)
            default: return nil
            }
        case .voice:
            switch s.voice {
            case .elevenlabs: store.overrideForRun(\.elevenModel, to: m, project: project)
            case .gemini: store.overrideForRun(\.geminiVoiceModel, to: m, project: project)
            case .apple: return nil
            }
        }
        return m
    }

    /// Failures remembered this session, per provider (safeguard 9).
    private static var failures: [String: Int] = [:]

    static func noteSuccess() {
        failures.removeAll()
        triedThisStreak.removeAll()
    }

    /// Builds the prompt for a failed phase. `nil` means "nothing useful to
    /// offer" (a plain bug / cancelled) - the caller falls back to the old
    /// red-error behaviour.
    static func plan(for error: Error, project: String, phase: PhaseID, store: Store) -> RecoveryPlan? {
        // 8: storage. Checked first - every other fix is pointless without disk.
        if let free = freeBytes(), free < 300_000_000 {
            let mb = Int(free / 1_000_000)
            return RecoveryPlan(projectID: project, phase: phase, title: "Your iPhone is almost full",
                reason: "Only \(mb) MB free. A render needs a few hundred MB of working space. Free some up (Settings, General, iPhone Storage) and retry.",
                actions: [.retry, .stop])
        }
        guard let avg = error as? AVGError else { return nil }
        if case .missingKey = avg {
            return RecoveryPlan(projectID: project, phase: phase, title: "An API key is missing",
                reason: avg.localizedDescription, actions: [.openKeys, .freeProvider, .stop])
        }
        guard case .provider(let kind, let provider, _) = avg else { return nil }
        failures[provider, default: 0] += 1
        let repeated = failures[provider, default: 0] >= 3
        let others = alternatives(to: provider, store: store, phase: phase)
        let switches: [RecoveryAction] = others.prefix(2).map { .otherProvider(name: $0) }
        var plan: RecoveryPlan?
        let running = store.effectiveSettings(for: project)
        let canFallback = fallbackIsUseful(aiKind(for: phase), settings: running, prefs: store.modelPrefs)
        let fb = fallbackModel(aiKind(for: phase), settings: running, prefs: store.modelPrefs) ?? ""
        switch kind {
        case .network, .timeout:
            plan = RecoveryPlan(projectID: project, phase: phase, title: "\(provider) isn't responding",
                reason: "\(provider) was tried 3 times with longer waits and still couldn't be reached. It is usually a busy server or a weak connection.",
                actions: [.retry, .cheaperModel] + switches + [.stop])
        case .rateLimit:
            plan = RecoveryPlan(projectID: project, phase: phase, title: "\(provider) says: slow down",
                reason: "You've hit \(provider)'s rate limit (too many requests in a short time). It normally clears within a minute.",
                actions: [.waitAndRetry(seconds: 60), .cheaperModel] + switches + [.stop])
        case .quota, .billing:
            plan = RecoveryPlan(projectID: project, phase: phase, title: kind == .billing ? "\(provider) needs payment" : "\(provider) quota used up",
                reason: kind == .billing ? "\(provider) rejected the request over billing or credit. Retrying won't help until that's fixed on their side."
                                         : "Your free or paid allowance on \(provider) is used up. Retrying won't help until it resets.",
                actions: [.cheaperModel] + switches + [.freeProvider, .stop])
        case .auth:
            plan = RecoveryPlan(projectID: project, phase: phase, title: "\(provider) rejected your key",
                reason: "The API key for \(provider) looks invalid, expired or lacks permission.",
                actions: [.openKeys] + switches + [.stop])
        case .modelMissing:
            plan = RecoveryPlan(projectID: project, phase: phase, title: "That model isn't available",
                reason: "\(provider) says the chosen model doesn't exist, was retired, or the text was too long for it.",
                actions: [.cheaperModel] + switches + [.stop])
        case .contentFilter:
            plan = RecoveryPlan(projectID: project, phase: phase, title: "\(provider) blocked this topic",
                reason: "The safety filter flagged the request. Switching model won't change that, but rewording the topic or trying another provider often does.",
                actions: [.reword] + switches + [.stop])
        case .dependency, .bug, .malformedOutput:
            return nil
        }
        if var p = plan {
            if !canFallback {
                p = RecoveryPlan(projectID: p.projectID, phase: p.phase, title: p.title, reason: p.reason,
                                 actions: p.actions.filter { $0 != .cheaperModel }, pattern: p.pattern)
            } else if p.actions.contains(.cheaperModel) {
                p = RecoveryPlan(projectID: p.projectID, phase: p.phase, title: p.title,
                                 reason: p.reason + " Your fallback model is \(fb).", actions: p.actions, pattern: p.pattern)
            }
            plan = p
        }
        if repeated, var p = plan {
            p.pattern = "\(provider) has failed \(failures[provider, default: 0]) times this session. Consider switching it for good in Settings."
            return p
        }
        return plan
    }

    /// Providers other than `current`, that are usable right now (have a
    /// key, or need none), best first.
    static func alternatives(to provider: String, store: Store, phase: PhaseID = .writing) -> [String] {
        let p = provider.lowercased()
        var out: [String] = []
        // Only providers that actually offer this kind of AI.
        switch aiKind(for: phase) {
        case .writer:
            if !p.contains("gemini"), Keychain.get(.gemini) != nil { out.append("Google Gemini") }
            if !p.contains("groq"), Keychain.get(.groq) != nil { out.append("Groq") }
            if !p.contains("pollinations") { out.append("Pollinations (free)") }
        case .image:
            if !p.contains("gemini"), Keychain.get(.gemini) != nil { out.append("Google Gemini") }
            if !p.contains("pollinations") { out.append("Pollinations (free)") }
        case .voice:
            if !p.contains("gemini"), Keychain.get(.gemini) != nil { out.append("Google Gemini") }
            if !p.contains("apple") { out.append("Apple voice (free, on-device)") }
        }
        return out
    }

    /// Applies a chosen action to settings. Returns whether the phase
    /// should be retried (false = stop).
    static func apply(_ action: RecoveryAction, plan: RecoveryPlan, store: Store) async -> Bool {
        switch action {
        case .retry: return true
        case .waitAndRetry(let s):
            store.record(plan.projectID, "Waiting \(s)s before retrying…")
            try? await Task.sleep(nanoseconds: UInt64(s) * 1_000_000_000)
            return !Task.isCancelled
        case .cheaperModel:
            let m = applyFallback(phase: plan.phase, store: store, project: plan.projectID)
            store.record(plan.projectID, m.map { "Switched to the fallback model \($0) for this video and retrying." } ?? "No lighter model available, retrying as is.")
            return true
        case .otherProvider(let name):
            let n = name.lowercased()
            if plan.phase == .visuals {
                store.overrideForRun(\.image, to: n.contains("gemini") ? .gemini : .pollinations, project: plan.projectID)
            } else if plan.phase == .voice {
                store.overrideForRun(\.voice, to: n.contains("gemini") ? .gemini : .apple, project: plan.projectID)
            } else {
                store.overrideForRun(\.text, to: n.contains("gemini") ? .gemini : (n.contains("groq") ? .groq : .pollinations),
                                     project: plan.projectID)
            }
            store.record(plan.projectID, "Switched to \(name) for this video and retrying.")
            return true
        case .freeProvider:
            switch plan.phase {
            case .visuals: store.overrideForRun(\.image, to: .pollinations, project: plan.projectID)
            case .voice: store.overrideForRun(\.voice, to: .apple, project: plan.projectID)
            default: store.overrideForRun(\.text, to: .pollinations, project: plan.projectID)
            }
            store.record(plan.projectID, "Switched to a free provider for this video and retrying.")
            return true
        case .openKeys:
            store.openSettings(.category(.keys, focus: .apiKeys))
            return false
        case .reword:
            store.pendingReword = plan.projectID
            store.tab = .create
            return false
        case .stop: return false
        }
    }

    static func freeBytes() -> Int64? {
        let url = URL(fileURLWithPath: NSHomeDirectory())
        return (try? url.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]))?
            .volumeAvailableCapacityForImportantUsage
    }
}

/// Safeguard 7: knows if the device is online, so a render can wait for
/// the connection instead of failing.
@MainActor
final class Connectivity {
    static let shared = Connectivity()
    private let monitor = NWPathMonitor()
    private(set) var online = true
    /// Call once at launch so the first real network status is known before
    /// a render needs it (the monitor is otherwise created lazily).
    func start() {}
    private init() {
        monitor.pathUpdateHandler = { [weak self] path in
            let up = path.status == .satisfied
            Task { @MainActor in self?.online = up }
        }
        monitor.start(queue: DispatchQueue(label: "avg.connectivity"))
    }
    /// Suspends until back online (max `timeout` seconds). Returns whether it is online.
    func waitUntilOnline(timeout: Double = 600) async -> Bool {
        var waited = 0.0
        while !online, waited < timeout, !Task.isCancelled {
            try? await Task.sleep(nanoseconds: 2_000_000_000); waited += 2
        }
        return online
    }
}

/// The prompt itself: a glass sheet with the reason and the choices.
struct RecoverySheet: View {
    @Environment(Store.self) private var store
    let plan: RecoveryPlan
    let choose: (RecoveryAction) -> Void

    var body: some View {
        GlassSheet(title: plan.title) {
            VStack(alignment: .leading, spacing: 14) {
                Text(plan.reason).font(.subheadline).foregroundStyle(Theme.ink2)
                if let pattern = plan.pattern {
                    Label(pattern, systemImage: "lightbulb")
                        .font(.footnote).foregroundStyle(Theme.accent)
                }
                Text("What would you like to do?").font(.footnote.weight(.semibold)).foregroundStyle(Theme.ink2)
                ForEach(plan.actions) { a in
                    Button { choose(a) } label: {
                        HStack(spacing: 10) {
                            Image(systemName: a.symbol).frame(width: 22)
                            Text(a.label).font(.subheadline.weight(.medium))
                            Spacer()
                        }
                        .foregroundStyle(a.isDestructive ? Color.red : Theme.ink)
                        .padding(12)
                        .frame(maxWidth: .infinity)
                        .glassCard(16, padding: 0)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .presentationDetents([.medium, .large])
        .interactiveDismissDisabled()
    }
}

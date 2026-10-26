import Foundation

// "Add a key and the app works out the rest."
//
// Before this, saving a key changed nothing: the model stayed on whatever the
// factory default was (a name written into the app months ago) until the
// person opened Settings, fetched the list, and ran a check by hand. This runs
// that same flow on its own, gently, the moment a key is saved:
//
//  1. fetch the provider's live model list (free);
//  2. rank it (ModelRanking) and probe only the top few (ModelProbe - one tiny
//     question each, so a free tier is not burned on a full sweep);
//  3. switch to the best model that WORKS, but only if the current model is the
//     factory default or one this code picked earlier. A model the person chose
//     is never touched.
//
// Image and voice models cost money to try, so they get no probe: only a model
// that the provider no longer offers is replaced.
//
// Everything it learns goes through the same `ModelPrefs` results the manual
// "check" shows, so the pickers light up without a second pass.

extension Notification.Name {
    /// Posted after a provider key is saved from the key sheet.
    static let avgKeySaved = Notification.Name("avg.keySaved")
}

@MainActor
enum ModelAutoSetup {
    /// How many ranked candidates are probed. Three covers "newest is not
    /// available to this account yet" without spending more than a few requests.
    static let shortlistSize = 3
    /// A check older than this is repeated at the next launch.
    static let staleAfter: TimeInterval = 7 * 24 * 3600

    private static var running = false
    private static var again = false
    private static var checkedThisLaunch = false

    /// `force` is true when a key was just saved. Otherwise (app launch) it only
    /// runs when a saved key has no recent result for the model in use, and at
    /// most once per launch, so a flaky network is never retried in a loop.
    static func run(store: Store, force: Bool = true) async {
        if running { again = true; return }
        if !force {
            guard !checkedThisLaunch, isDue(store) else { return }
        }
        checkedThisLaunch = true
        running = true
        defer { running = false }
        repeat {
            again = false
            if Keychain.has(.gemini) { await setupGemini(store) }
            if Keychain.has(.groq) { await setupGroq(store) }
        } while again
    }

    // MARK: Decision (pure, tested)

    /// Whether the model in use should be swapped for `pick`.
    /// - Never when the person chose it: only the factory default or an earlier
    ///   automatic pick may be replaced.
    /// - Always when it is no longer offered to this account or a probe said it fails.
    /// - Otherwise only when `upgrade` says to chase the best working model
    ///   (writer on Gemini: yes; Groq: no, a working model stays).
    static func shouldReplace(current: String, pick: String, factoryDefault: String, autoPicked: String?,
                              currentListed: Bool, currentWorks: Bool?, upgrade: Bool) -> Bool {
        guard pick != current else { return false }
        guard current == factoryDefault || current == autoPicked else { return false }
        if !currentListed { return true }
        if currentWorks == false { return true }
        return upgrade
    }

    /// Whether a launch-time check is worth doing: a saved key whose model in
    /// use has no result, or only an old one.
    private static func isDue(_ store: Store) -> Bool {
        func stale(_ catalog: String, _ model: String) -> Bool {
            guard let result = store.modelPrefs.result(model, in: catalog) else { return true }
            return Date().timeIntervalSince(result.checked) > staleAfter
        }
        return (Keychain.has(.gemini) && stale("gemini_text", store.settings.geminiModel))
            || (Keychain.has(.groq) && stale("groq_text", store.settings.groqModel))
    }

    // MARK: Providers

    private static func setupGemini(_ store: Store) async {
        guard let key = Keychain.get(.gemini) else { return }
        let prefs = store.modelPrefs

        if let entries = try? await ModelCatalogSources.geminiText(key: key) {
            let ids = entries.map { $0.id }
            let ranked = ModelRanking.geminiText(ids)
            let current = store.settings.geminiModel
            var shortlist = Array(ranked.prefix(shortlistSize))
            if ids.contains(current), !shortlist.contains(current) { shortlist.append(current) }
            await prefs.check(catalogKey: "gemini_text", models: shortlist)
            if let pick = ranked.first(where: { prefs.result($0, in: "gemini_text")?.works == true }) {
                apply(store, catalog: "gemini_text", label: "Gemini writer", path: \.geminiModel,
                      pick: pick, listed: ids.contains(current),
                      currentWorks: prefs.result(current, in: "gemini_text")?.works, upgrade: true)
            }
        }

        // No probe for these: trying an image or a voice line costs money. A
        // model the provider no longer lists is the one case worth fixing unasked.
        if let entries = try? await ModelCatalogSources.geminiImageModels(key: key) {
            let ids = entries.map { $0.id }
            if let pick = ModelRanking.geminiImage(ids).first {
                apply(store, catalog: "gemini_image", label: "Gemini images", path: \.geminiImageModel,
                      pick: pick, listed: ids.contains(store.settings.geminiImageModel),
                      currentWorks: nil, upgrade: false)
            }
        }
        if let entries = try? await ModelCatalogSources.geminiVoiceModels(key: key) {
            let ids = entries.map { $0.id }
            if let pick = ModelRanking.geminiVoice(ids).first {
                apply(store, catalog: "gemini_voice_model", label: "Gemini voice", path: \.geminiVoiceModel,
                      pick: pick, listed: ids.contains(store.settings.geminiVoiceModel),
                      currentWorks: nil, upgrade: false)
            }
        }
    }

    private static func setupGroq(_ store: Store) async {
        guard let key = Keychain.get(.groq),
              let entries = try? await ModelCatalogSources.groqText(key: key) else { return }
        let prefs = store.modelPrefs
        let ids = entries.map { $0.id }
        let current = store.settings.groqModel

        // A model that works is left alone: the size rule is a rescue, not a taste.
        if ids.contains(current) {
            await prefs.check(catalogKey: "groq_text", models: [current])
            if prefs.result(current, in: "groq_text")?.works == true { return }
        }
        let ranked = ModelRanking.groqText(ids)
        await prefs.check(catalogKey: "groq_text", models: Array(ranked.prefix(shortlistSize)))
        if let pick = ranked.first(where: { prefs.result($0, in: "groq_text")?.works == true }) {
            apply(store, catalog: "groq_text", label: "Groq writer", path: \.groqModel,
                  pick: pick, listed: ids.contains(current),
                  currentWorks: prefs.result(current, in: "groq_text")?.works, upgrade: false)
        }
    }

    private static func apply(_ store: Store, catalog: String, label: String,
                              path: WritableKeyPath<AppSettings, String>, pick: String,
                              listed: Bool, currentWorks: Bool?, upgrade: Bool) {
        let current = store.settings[keyPath: path]
        let factoryDefault = AppSettings()[keyPath: path]
        guard shouldReplace(current: current, pick: pick, factoryDefault: factoryDefault,
                            autoPicked: store.modelPrefs.autoPick(catalog), currentListed: listed,
                            currentWorks: currentWorks, upgrade: upgrade) else { return }
        store.settings[keyPath: path] = pick
        store.modelPrefs.setAutoPick(pick, for: catalog)
        store.modelPrefs.addSetupNote("\(label): now \(pick) (was \(current))")
    }
}

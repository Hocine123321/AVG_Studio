import Foundation
import Observation

// The editing assistant: a second, smaller chatbot that lives in the editor.
//
// The main assistant (Agent.swift) runs the whole app - settings, channels,
// starting renders, spending money. This one does none of that, by
// construction: its tools can only change THIS video's captions, title cards,
// music choice and sound-effects switch. There is no tool for settings, keys,
// channels or rendering, so no wording of a request can reach them - and
// `EditToolsTests` pins that the two tool sets never overlap.
//
// It borrows the main assistant's plumbing (the provider codecs for Groq and
// Gemini, the tool schema, tolerant argument reading, history trimming) and
// none of its policy. Edits apply at once, because they are free and
// reversible: every turn that changed anything can be undone with one tap.

/// A title card built the same way for the editor's own form and the assistant.
enum TitleCards {
    static func make(text: String, start: Double, length: Double, total: Double, existing: [TakeoverCard]) -> TakeoverCard {
        let from = min(max(0, start), max(0, total - 0.5))
        let end = total > 0 ? min(total, from + length) : from + length
        // Cards are told apart by kind + scene number, so a hand-made one takes a
        // number above every scene and every existing card.
        let scene = max(1000, (existing.map { $0.scene }.max() ?? 0) + 1)
        return TakeoverCard(scene: scene, text: text.trimmed, start: from, end: end, kind: "explainer")
    }
}

@MainActor
@Observable
final class EditAssistant {
    struct Line: Identifiable, Equatable {
        enum Kind: Equatable { case user, assistant, activity, error }
        let id = UUID()
        let kind: Kind
        let text: String
    }

    /// What the assistant can change, saved before a turn so it can be undone.
    struct Snapshot: Equatable {
        var cards: [TakeoverCard]
        var captions: [CaptionCue]
        var edits: ProjectEdits?
    }

    static let maxSteps = 6
    static let maxNudges = 2
    static let historyWindow = 16
    static let nudgeText = "Your last tool call was malformed. Call the tool again with valid arguments, or answer in plain text."

    private(set) var lines: [Line] = []
    private(set) var isWorking = false
    private(set) var undoStack: [Snapshot] = []
    var canUndo: Bool { !undoStack.isEmpty }

    /// Makes the provider for a turn. Tests replace it with a scripted one.
    @ObservationIgnored var makeProvider: (AppSettings) throws -> any AgentProvider = { settings in
        try AgentProviderFactory.make(settings: settings)
    }

    @ObservationIgnored private weak var store: Store?
    @ObservationIgnored private(set) var projectID = ""
    @ObservationIgnored private var wire: [AgentMessage] = []
    @ObservationIgnored private var turnTask: Task<Void, Never>?
    @ObservationIgnored private var snapshotTakenThisTurn = false

    /// Points the assistant at a video. Switching videos starts a fresh chat,
    /// because the numbers it quoted (lines, cards) belong to the old one.
    func attach(_ store: Store, projectID id: String) {
        self.store = store
        if projectID != id {
            projectID = id
            reset()
        }
    }

    func reset() {
        turnTask?.cancel()
        turnTask = nil
        lines = []
        wire = []
        undoStack = []
        isWorking = false
    }

    func stop() { turnTask?.cancel() }

    // MARK: Sending

    @discardableResult
    func send(_ text: String) -> Task<Void, Never> {
        let trimmed = text.trimmed
        guard !trimmed.isEmpty, !isWorking, store != nil, !projectID.isEmpty else { return Task {} }
        isWorking = true
        lines.append(Line(kind: .user, text: trimmed))
        let task = Task { [weak self] in
            guard let self else { return }
            await self.runTurn(trimmed)
        }
        turnTask = task
        return task
    }

    private func runTurn(_ userText: String) async {
        defer {
            isWorking = false
            turnTask = nil
        }
        guard let store else { return }
        snapshotTakenThisTurn = false
        wire.append(.user(userText))
        let system = EditTools.prompt(for: store.settings)
        let tools = EditTools.offeredSpecs(for: store.settings)
        var steps = 0
        var nudges = 0

        loop: while true {
            if Task.isCancelled { lines.append(Line(kind: .activity, text: "Stopped.")); break loop }
            steps += 1
            if steps > Self.maxSteps {
                lines.append(Line(kind: .error, text: "I stopped after \(Self.maxSteps) steps without finishing. Try asking for one change at a time."))
                break loop
            }
            let reply: AgentMessage
            do {
                let provider = try makeProvider(EditTools.providerSettings(from: store.settings))
                let history = AgentHistory.trimmed(wire, keepLast: Self.historyWindow)
                reply = try await provider.respond(system: system, messages: history, tools: tools)
            } catch {
                if error is CancellationError || Task.isCancelled { lines.append(Line(kind: .activity, text: "Stopped.")); break loop }
                if let avg = error as? AVGError, avg.kind == .malformedOutput, nudges < Self.maxNudges {
                    nudges += 1
                    wire.append(.nudge(Self.nudgeText))
                    continue loop
                }
                let reason = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
                lines.append(Line(kind: .error, text: "The assistant isn't available: \(reason) Check its key and service under Settings."))
                break loop
            }
            wire.append(reply)
            if !reply.text.isEmpty { lines.append(Line(kind: .assistant, text: reply.text)) }
            if reply.toolCalls.isEmpty { break loop }

            for call in reply.toolCalls {
                lines.append(Line(kind: .activity, text: EditTools.activity(for: call.name)))
                let result = run(call)
                wire.append(.tool(callID: call.id, name: call.name, text: result))
            }
        }
        wire = AgentHistory.compacted(wire, keepingLastTurn: false)
    }

    // MARK: Undo

    /// Puts back what the last turn changed.
    func undo() {
        guard let store, let snap = undoStack.popLast() else { return }
        store.update(projectID) { p in
            p.cards = snap.cards
            p.captions = snap.captions
            p.edits = snap.edits
        }
        lines.append(Line(kind: .activity, text: "Undone."))
    }

    // MARK: Running a tool

    /// Runs one call and returns the text the model gets back. Internal so
    /// tests can drive single tools without a model.
    func run(_ call: AgentToolCall) -> String {
        guard let store, let p = store.project(projectID) else { return "That video is no longer available." }
        let args = AgentArgs(json: call.argumentsJSON)
        if let capability = EditCapability.governing(tool: call.name), !store.settings[keyPath: capability.keyPath] {
            return EditTools.switchedOff(capability)
        }
        switch call.name {
        case "get_edit":
            return EditTools.describe(p, channel: store.channel(for: p), library: store.audioLibrary.music)

        case "list_captions":
            return EditTools.listCaptions(p, from: args.int("from_line") ?? 1)

        case "fix_caption_word":
            guard let find = args.string("find"), let replacement = args.string("replace") else {
                return "Give both the word to find and what to replace it with."
            }
            let result = CaptionEdit.replace(find, with: replacement, in: p.captions)
            guard result.count > 0 else { return "No whole word \"\(find)\" appears in the captions." }
            mutate { $0.captions = result.cues }
            return "Replaced \(result.count) time\(result.count == 1 ? "" : "s")."

        case "set_caption_line":
            guard let number = args.int("line"), let text = args.string("text") else {
                return "Give the line number and the new text."
            }
            guard p.captions.indices.contains(number - 1) else {
                return "There is no line \(number). The captions have \(p.captions.count) lines."
            }
            let updated = CaptionEdit.setText(p.captions[number - 1], to: text)
            mutate { $0.captions[number - 1] = updated }
            return "Line \(number) now reads: \(updated.text)"

        case "add_title_card":
            guard let text = args.string("text") else { return "Give the text for the card." }
            let total = p.sceneDurations.reduce(0, +)
            let at = Double(args.int("at_seconds") ?? 0)
            let length = Double(min(max(args.int("for_seconds") ?? 3, 1), 6))
            guard total <= 0 || at < total else { return "The video is only \(Int(total)) seconds long." }
            let card = TitleCards.make(text: text, start: at, length: length, total: total, existing: p.cards)
            mutate { $0.cards.append(card); $0.cards.sort { $0.start < $1.start } }
            return "Added a card at \(EditTools.clock(card.start)) for \(Int(card.end - card.start)) s: \(card.text)"

        case "remove_title_card":
            let sorted = p.cards.sorted { $0.start < $1.start }
            guard let number = args.int("number"), sorted.indices.contains(number - 1) else {
                return "There is no such card. Call get_edit to see the numbered cards."
            }
            let gone = sorted[number - 1]
            mutate { $0.cards.removeAll { $0.id == gone.id } }
            return "Removed card \(number): \(gone.text)"

        case "set_music":
            let level = args.int("level_db")
            let choice = args.string("choice")
            guard choice != nil || level != nil else {
                return "Say channel, none, auto, or a track name, and/or a level in dB."
            }
            var picked: MusicOverride?
            if let choice {
                switch EditTools.musicChoice(choice, library: store.audioLibrary.music) {
                case .success(let parsed): picked = parsed
                case .failure(let message): return message   // nothing is applied
                }
            }
            // Level: the value given, clamped to the slider's range; "channel" puts it back.
            var gain: Double??
            var clampedFrom: Int?
            if let level {
                let kept = EditResolution.clamp(Double(level), to: EditResolution.musicGainRange)
                if kept != Double(level) { clampedFrom = level }
                gain = .some(kept)
            } else if choice != nil, picked == nil {
                gain = .some(nil)   // "channel" also gives the channel's level back
            }
            mutate { p in
                var e = p.edits ?? ProjectEdits()
                if choice != nil { e.music = picked }
                if let gain { e.musicGainDB = gain }
                p.edits = e.isEmpty ? nil : e
            }
            var parts: [String] = []
            if choice != nil { parts.append("Music for this video: \(EditTools.name(of: picked, library: store.audioLibrary.music)).") }
            if let gain {
                parts.append(EditTools.levelReply("Music level", gain, unit: " under the voice",
                                                  clampedFrom: clampedFrom, range: EditResolution.musicGainRange))
            }
            return parts.joined(separator: " ")

        case "set_sound_effects":
            let level = args.int("level_db")
            let rawMode = args.string("mode")?.lowercased()
            if let rawMode, !["channel", "on", "off"].contains(rawMode) { return "Say channel, on or off." }
            guard rawMode != nil || level != nil else { return "Say channel, on or off, and/or a level in dB." }
            let value: Bool? = (rawMode == nil || rawMode == "channel") ? nil : (rawMode == "on")
            var gain: Double??
            var clampedFrom: Int?
            if let level {
                let kept = EditResolution.clamp(Double(level), to: EditResolution.sfxGainRange)
                if kept != Double(level) { clampedFrom = level }
                gain = .some(kept)
            } else if rawMode == "channel" {
                gain = .some(nil)
            }
            mutate { p in
                var e = p.edits ?? ProjectEdits()
                if rawMode != nil { e.sfxEnabled = value }
                if let gain { e.sfxGainDB = gain }
                p.edits = e.isEmpty ? nil : e
            }
            var parts: [String] = []
            if let rawMode { parts.append("Sound effects for this video: \(rawMode == "channel" ? "same as the channel" : rawMode).") }
            if let gain {
                parts.append(EditTools.levelReply("Effects level", gain, unit: "",
                                                  clampedFrom: clampedFrom, range: EditResolution.sfxGainRange))
            }
            return parts.joined(separator: " ")

        case "set_cuts":
            guard let raw = args.string("level")?.lowercased(), let parsed = EditTools.cutsChoice(raw) else {
                return "Say channel, off, cuts or smart."
            }
            mutate { p in
                var e = p.edits ?? ProjectEdits()
                e.editLevel = parsed.level
                p.edits = e.isEmpty ? nil : e
            }
            return "Cuts and punch-ins for this video: \(parsed.label)."

        case "undo":
            guard canUndo else { return "Nothing to undo." }
            undo()
            return "Undone."

        default:
            return "I can't do that here. I can only change this video's captions, title cards, music, sound effects and cuts."
        }
    }

    /// Applies a change, first saving the state once per turn so Undo is one step.
    private func mutate(_ body: (inout Project) -> Void) {
        guard let store, let p = store.project(projectID) else { return }
        if !snapshotTakenThisTurn {
            snapshotTakenThisTurn = true
            undoStack.append(Snapshot(cards: p.cards, captions: p.captions, edits: p.edits))
            if undoStack.count > 20 { undoStack.removeFirst() }
        }
        store.update(projectID, body)
    }
}

// MARK: - The tools and the prompt

enum EditTools {
    static let systemPrompt = """
        You are the editing assistant inside a video editor, working on ONE video. You can only change this video's \
        captions, title cards, music (choice and level), sound effects (switch and level) and cuts and punch-ins, with your tools. You cannot change app settings, \
        channels, API keys, images or voices, create music, or render - if asked, say that is outside what you can do \
        here. Never guess a line or card number: call list_captions or get_edit first. Make the smallest change that \
        does what was asked, one tool call per change, then say in one short sentence what you did. Times are seconds \
        from the start of the video. Levels are in dB: lower is quieter.
        """

    static let specs: [AgentToolSpec] = [
        AgentToolSpec(name: "get_edit", summary: "Describe this video's edit: length, title cards, music, sound effects, caption count.", params: []),
        AgentToolSpec(name: "list_captions", summary: "List caption lines with numbers and times, 30 at a time.",
                      params: [AgentToolParam(name: "from_line", kind: .integer, summary: "First line number to show (default 1).", required: false)]),
        AgentToolSpec(name: "fix_caption_word", summary: "Replace a whole word everywhere in the captions.",
                      params: [AgentToolParam(name: "find", kind: .string, summary: "The word to find."),
                               AgentToolParam(name: "replace", kind: .string, summary: "The word to put instead.")]),
        AgentToolSpec(name: "set_caption_line", summary: "Retype one caption line.",
                      params: [AgentToolParam(name: "line", kind: .integer, summary: "The line number from list_captions."),
                               AgentToolParam(name: "text", kind: .string, summary: "The new text for the line.")]),
        AgentToolSpec(name: "add_title_card", summary: "Add a full-screen text card.",
                      params: [AgentToolParam(name: "text", kind: .string, summary: "What the card says."),
                               AgentToolParam(name: "at_seconds", kind: .integer, summary: "When it appears, in seconds."),
                               AgentToolParam(name: "for_seconds", kind: .integer, summary: "How long it stays, 1 to 6 (default 3).", required: false)]),
        AgentToolSpec(name: "remove_title_card", summary: "Remove a title card by its number from get_edit.",
                      params: [AgentToolParam(name: "number", kind: .integer, summary: "The card number.")]),
        AgentToolSpec(name: "set_music", summary: "Choose this video's music and/or how loud it is under the voice.",
                      params: [AgentToolParam(name: "choice", kind: .string, summary: "channel (also resets the level), none, auto, or the name of a track in the library.", required: false),
                               AgentToolParam(name: "level_db", kind: .integer, summary: "Level under the voice in dB, -35 (quiet) to -10 (loud).", required: false)]),
        AgentToolSpec(name: "set_sound_effects", summary: "Turn this video's sound effects on or off, follow the channel, and/or set their level.",
                      params: [AgentToolParam(name: "mode", kind: .string, summary: "channel (also resets the level), on or off.", required: false, allowed: ["channel", "on", "off"]),
                               AgentToolParam(name: "level_db", kind: .integer, summary: "Level offset in dB, -12 (quieter) to 6 (louder).", required: false)]),
        AgentToolSpec(name: "set_cuts", summary: "Set this video's shot cuts and punch-ins, or follow the channel.",
                      params: [AgentToolParam(name: "level", kind: .string, summary: "channel, off, cuts (cuts and punch-ins) or smart (also aims crops at the subject).", allowed: ["channel", "off", "cuts", "smart"])]),
        AgentToolSpec(name: "undo", summary: "Undo the last change you made.", params: []),
    ]

    static var toolNames: [String] { specs.map { $0.name } }

    static func activity(for name: String) -> String {
        switch name {
        case "get_edit", "list_captions": return "Looking at the edit…"
        case "fix_caption_word", "set_caption_line": return "Fixing captions…"
        case "add_title_card": return "Adding a title card…"
        case "remove_title_card": return "Removing a title card…"
        case "set_music": return "Choosing music…"
        case "set_sound_effects": return "Setting sound effects…"
        case "set_cuts": return "Setting cuts…"
        case "undo": return "Undoing…"
        default: return "Working…"
        }
    }

    static func clock(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        return String(format: "%d:%02d", s / 60, s % 60)
    }

    static func describe(_ p: Project, channel: Channel, library: [AudioItem]) -> String {
        let total = p.sceneDurations.reduce(0, +)
        var out = ["\"\(p.title)\": \(Int(total.rounded())) s, \(p.sceneDurations.count) scenes."]
        let cards = p.cards.sorted { $0.start < $1.start }
        if cards.isEmpty {
            out.append("Title cards: none.")
        } else {
            out.append("Title cards:")
            for (i, c) in cards.enumerated() { out.append("\(i + 1)) \(clock(c.start))-\(clock(c.end)) \"\(c.text)\"") }
        }
        let musicText: String
        if let own = p.edits?.music {
            musicText = name(of: own, library: library)
        } else if channel.musicAuto {
            musicText = "the channel picks automatically"
        } else if let id = channel.musicTrackID, let track = library.first(where: { $0.id == id }) {
            musicText = "\"\(track.name)\" (channel)"
        } else {
            musicText = "none (channel default)"
        }
        let resolved = EditResolution(project: p, channel: channel)
        let ownMusicLevel = p.edits?.musicGainDB != nil
        out.append("Music: \(musicText), level \(Int(resolved.musicGainDB)) dB under the voice\(ownMusicLevel ? "" : " (channel)").")
        let effectsText: String
        if let own = p.edits?.sfxEnabled {
            effectsText = own ? "on" : "off"
        } else {
            effectsText = channel.sfxEnabled ? "on (channel)" : "off (channel)"
        }
        let ownEffectsLevel = p.edits?.sfxGainDB != nil
        out.append("Sound effects: \(effectsText), level \(Int(resolved.sfxGainDB)) dB\(ownEffectsLevel ? "" : " (channel)").")
        let cutsOwn = p.edits?.editLevel != nil
        out.append("Cuts and punch-ins: \(cutsChoiceLabel(resolved.editLevel))\(cutsOwn ? "" : " (channel)").")
        out.append("Captions: \(p.captions.count) lines.")
        out.append("Tracks in the library: \(library.isEmpty ? "none" : library.prefix(12).map { $0.name }.joined(separator: ", ")).")
        return out.joined(separator: "\n")
    }

    static func listCaptions(_ p: Project, from: Int, limit: Int = 30) -> String {
        guard !p.captions.isEmpty else { return "This video has no captions." }
        let start = min(max(1, from), p.captions.count)
        let end = min(p.captions.count, start + limit - 1)
        var out: [String] = []
        for i in start...end { out.append("\(i) [\(clock(p.captions[i - 1].start))] \(p.captions[i - 1].text)") }
        if end < p.captions.count { out.append("(\(p.captions.count - end) more: call again with from_line \(end + 1))") }
        return out.joined(separator: "\n")
    }

    enum MusicParse {
        case success(MusicOverride?)
        case failure(String)
    }

    /// "channel" -> follow the channel (nil), "none" -> silent, "auto", or a track by name.
    static func musicChoice(_ raw: String, library: [AudioItem]) -> MusicParse {
        let s = raw.trimmed.lowercased()
        switch s {
        case "channel", "same as channel", "default": return .success(nil)
        case "none", "no music", "off", "silent": return .success(.silent)
        case "auto", "automatic", "choose for me": return .success(.auto)
        default: break
        }
        if let exact = library.first(where: { $0.name.lowercased() == s }) { return .success(.track(exact.id)) }
        let partial = library.filter { $0.name.lowercased().contains(s) }
        if partial.count == 1 { return .success(.track(partial[0].id)) }
        let names = library.prefix(8).map { "\"\($0.name)\"" }.joined(separator: ", ")
        return .failure(partial.count > 1
            ? "More than one track matches \"\(raw)\". Use the full name: \(partial.prefix(8).map { "\"\($0.name)\"" }.joined(separator: ", "))."
            : "No track named \"\(raw)\". " + (library.isEmpty ? "The library has no tracks." : "Available: \(names)."))
    }

    /// "Music level: -22 dB under the voice." / "... (-50 was outside the range -35 to -10)." /
    /// "Music level: same as the channel." (nil value).
    static func levelReply(_ title: String, _ value: Double?, unit: String, clampedFrom: Int?, range: ClosedRange<Double>) -> String {
        guard let value else { return "\(title): same as the channel." }
        var text = "\(title): \(Int(value)) dB\(unit)"
        if let from = clampedFrom {
            text += " (\(from) was outside the range \(Int(range.lowerBound)) to \(Int(range.upperBound)))"
        }
        return text + "."
    }

    /// "channel" -> follow the channel (nil), "off", "cuts" (or "fast"), "smart".
    static func cutsChoice(_ raw: String) -> (level: EditLevel?, label: String)? {
        switch raw.trimmed.lowercased() {
        case "channel", "same as channel", "default": return (nil, "same as the channel")
        case "off", "none", "no": return (.off, "off")
        case "cuts", "fast", "on", "cuts and punch-ins": return (.fast, "cuts and punch-ins")
        case "smart", "smart crops": return (.smart, "smart crops (found when the video renders; needs a Google key)")
        default: return nil
        }
    }

    static func cutsChoiceLabel(_ level: EditLevel) -> String {
        switch level {
        case .off: return "off"
        case .fast: return "cuts and punch-ins"
        case .smart: return "smart crops"
        }
    }

    static func name(of override: MusicOverride?, library: [AudioItem]) -> String {
        switch override {
        case .none: return "same as the channel"
        case .some(.silent): return "no music"
        case .some(.auto): return "chosen automatically"
        case .some(.track(let id)): return library.first { $0.id == id }.map { "\"\($0.name)\"" } ?? "a removed track"
        }
    }
}

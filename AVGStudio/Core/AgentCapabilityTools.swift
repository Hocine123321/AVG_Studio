import Foundation

/// The assistant's newer capabilities: reading app state, and - always with
/// the person's approval - changing settings, channels and projects. Each is
/// written for the model: terse names and arguments (the tool list is sent on
/// every request and Groq's free plan allows about 8K tokens a minute), plain
/// text results, and errors that name the valid values so the next call can
/// be right. See `AgentCapability` for the switches that gate them.
enum AgentCapabilityTools {

    // MARK: Specs

    static let manageActions = ["rename", "archive", "unarchive", "delete"]
    static let channelFields = ["name", "niche", "tone", "language", "seconds", "aspect", "active"]

    static var specs: [AgentToolSpec] {
        let project = AgentToolParam(name: "project", kind: .string, summary: "Ref, id or part of the title.")
        return [
            AgentToolSpec(name: "estimate_cost", summary: "Estimate what a new video, or a redo from a step, would cost.", params: [
                AgentToolParam(name: "phase", kind: .string, summary: "Step to estimate from. Default: writing (a whole new video).", required: false,
                               allowed: PhaseID.allCases.map { $0.rawValue }),
                AgentToolParam(name: "channel", kind: .string, summary: "Channel name. Default: the active one.", required: false),
            ]),
            AgentToolSpec(name: "manage_project", summary: "Rename, archive, unarchive or delete a project. Needs approval.", params: [
                project,
                AgentToolParam(name: "action", kind: .string, summary: "What to do.", allowed: manageActions),
                AgentToolParam(name: "title", kind: .string, summary: "New title, for rename.", required: false),
            ]),
            AgentToolSpec(name: "change_setting", summary: "Change one app setting. Needs approval. Call get_settings first for valid values.", params: [
                // Deliberately NOT an enum: 24 keys would be sent on every request. get_settings
                // lists them, and a wrong key gets an error that names every valid one.
                AgentToolParam(name: "setting", kind: .string, summary: "Setting key, from get_settings."),
                AgentToolParam(name: "value", kind: .string, summary: "The new value."),
            ]),
            AgentToolSpec(name: "edit_channel", summary: "Edit a channel, or make it the active one. Needs approval.", params: [
                AgentToolParam(name: "channel", kind: .string, summary: "Channel name."),
                AgentToolParam(name: "field", kind: .string, summary: "What to change. active = make it the active channel.", allowed: channelFields),
                AgentToolParam(name: "value", kind: .string, summary: "New value (seconds 15-600; aspect 9:16, 16:9 or 1:1; active: yes).", required: false),
            ]),
            AgentToolSpec(name: "get_app_status", summary: "Show what is running or queued, which service keys are saved, and the chosen writer, image and voice services.", params: []),
            AgentToolSpec(name: "get_activity", summary: "Show recent log lines, or only errors.", params: [
                AgentToolParam(name: "limit", kind: .integer, summary: "How many, 1-20. Default 8.", required: false),
                AgentToolParam(name: "errors_only", kind: .boolean, summary: "Only errors.", required: false),
            ]),
            AgentToolSpec(name: "get_settings", summary: "List a group of settings with current values and valid values.", params: [
                AgentToolParam(name: "group", kind: .string, summary: "Which group.", allowed: AgentSetting.Group.allCases.map { $0.rawValue }),
            ]),
        ]
    }

    // MARK: estimate_cost

    @MainActor static func estimateCost(_ args: AgentArgs, _ host: any AgentHost, _ context: AgentContext) -> String {
        let phase: PhaseID
        if let raw = args.string("phase") {
            guard let p = PhaseID(rawValue: raw.lowercased()) else {
                return "Error: phase must be one of \(PhaseID.allCases.map { $0.rawValue }.joined(separator: ", "))."
            }
            phase = p
        } else {
            phase = .writing
        }
        let channel: Channel
        switch AgentTools.resolveChannel(args.string("channel"), in: host) {
        case .success(let c): channel = c
        case .failure(let e): return e.text
        }
        let s = host.settings
        let e = CostEstimate.forRedo(from: phase, settings: s, channel: channel,
                                     hasAssemblyKey: context.hasKey(.assemblyai), poolSize: host.reusableImageCount)
        let what = phase == .writing ? "A new \(channel.targetSeconds)s video on “\(channel.name)”" : "Redoing from \(phase.title)"
        return "\(what): \(e.summary).\nWriter \(s.text.label) · Images \(s.image.label) · Voice \(s.voice.label)"
    }

    // MARK: manage_project

    @MainActor static func manageProject(_ args: AgentArgs, _ host: any AgentHost) -> AgentToolOutcome {
        let project: Project
        switch AgentTools.resolveProject(args.string("project") ?? "", in: host.projects) {
        case .success(let p): project = p
        case .failure(let e): return .result(e.text)
        }
        guard let action = args.string("action")?.lowercased(), manageActions.contains(action) else {
            return .result("Error: action must be one of \(manageActions.joined(separator: ", ")).")
        }
        let name = AgentTools.short(project.title)
        switch action {
        case "rename":
            guard let raw = args.string("title") else { return .result("Error: title is required to rename. Ask the user for the new name.") }
            let title = String(raw.prefix(80))
            if title == project.title { return .result("“\(name)” already has that title.") }
            return .needsApproval(AgentApproval(
                title: "Rename this video?", detail: "“\(name)” → “\(AgentTools.short(title, 60))”",
                estimate: "", estimateUSD: 0, action: .renameProject(id: project.id, title: title), kind: .change))
        case "archive":
            if project.archived { return .result("“\(name)” is already archived.") }
            return .needsApproval(AgentApproval(
                title: "Archive this video?", detail: "“\(name)” moves out of the Library. You can unarchive it any time.",
                estimate: "", estimateUSD: 0, action: .archiveProject(id: project.id, archived: true), kind: .change))
        case "unarchive":
            if !project.archived { return .result("“\(name)” isn't archived.") }
            return .needsApproval(AgentApproval(
                title: "Unarchive this video?", detail: "“\(name)” returns to the Library.",
                estimate: "", estimateUSD: 0, action: .archiveProject(id: project.id, archived: false), kind: .change))
        default:
            if host.isBusy(project.id) { return .result("“\(name)” is running or queued. Cancel it first, then delete it.") }
            return .needsApproval(AgentApproval(
                title: "Delete this video?", detail: "“\(name)” (ref \(AgentTools.ref(project))) and all its files will be deleted. This can't be undone.",
                estimate: "", estimateUSD: 0, action: .deleteProject(id: project.id), kind: .destructive))
        }
    }

    // MARK: change_setting

    @MainActor static func changeSetting(_ args: AgentArgs, _ host: any AgentHost, _ context: AgentContext) -> AgentToolOutcome {
        guard let key = args.string("setting") else {
            return .result("Error: setting is required. Valid: \(AgentSettingsCatalog.keys.joined(separator: ", ")).")
        }
        guard let raw = args.string("value") else {
            return .result("Error: value is required. Call get_settings to see valid values.")
        }
        guard let item = AgentSettingsCatalog.setting(key), let value = AgentSettingsCatalog.canonical(raw, for: item) else {
            // `preview` words the error for whichever of the two failed.
            if case .failure(let e) = AgentSettingsCatalog.preview(key: key, value: raw, settings: host.settings) { return .result(e.text) }
            return .result("That value is not valid for \(key).")
        }
        let change: AgentSettingChange
        switch AgentSettingsCatalog.preview(key: key, value: value, settings: host.settings) {
        case .success(let c): change = c
        case .failure(let e): return .result(e.text)
        }
        if change.from == change.to { return .result("\(change.title) is already \(change.to).") }
        if item.key == "writer", value == TextProviderKind.apple.rawValue, !context.appleAvailable {
            return .result("Apple Intelligence isn't available on this phone, so the writer can't be set to it.")
        }
        var detail = "\(change.from) → \(change.to)"
        if let missing = missingKeyNote(for: item.key, value: value, context: context) { detail += "\n" + missing }
        return .needsApproval(AgentApproval(
            title: "Change \(change.title)?", detail: detail,
            estimate: "", estimateUSD: 0, action: .changeSetting(key: item.key, value: value), kind: .change))
    }

    /// "The Groq key isn't saved yet" when a provider is being switched to
    /// one that needs a key the phone doesn't have.
    private static func missingKeyNote(for key: String, value: String, context: AgentContext) -> String? {
        let needed: APIKey?
        switch key {
        case "writer": needed = TextProviderKind(rawValue: value).flatMap { AgentTools.keyNeeded(text: $0) }
        case "images": needed = ImageProviderKind(rawValue: value).flatMap { AgentTools.keyNeeded(image: $0) }
        case "voice": needed = VoiceProviderKind(rawValue: value).flatMap { AgentTools.keyNeeded(voice: $0) }
        default: needed = nil
        }
        guard let needed, !context.hasKey(needed) else { return nil }
        return "The \(needed.label) key isn't saved yet, so videos will stop until you add it."
    }

    // MARK: edit_channel

    @MainActor static func editChannel(_ args: AgentArgs, _ host: any AgentHost) -> AgentToolOutcome {
        guard let name = args.string("channel") else { return .result("Error: channel is required. Call list_channels for names.") }
        let channel: Channel
        switch AgentTools.resolveChannel(name, in: host) {
        case .success(let c): channel = c
        case .failure(let e): return .result(e.text)
        }
        guard let field = args.string("field")?.lowercased(), channelFields.contains(field) else {
            return .result("Error: field must be one of \(channelFields.joined(separator: ", ")).")
        }
        if field == "active" {
            if channel.id == host.activeChannel.id { return .result("“\(channel.name)” is already the active channel.") }
            return .needsApproval(AgentApproval(
                title: "Switch channel?", detail: "Make “\(channel.name)” the active channel.",
                estimate: "", estimateUSD: 0, action: .setActiveChannel(channel.id), kind: .change))
        }
        guard let raw = args.string("value") else { return .result("Error: value is required to change \(field).") }
        let edit: ChannelEdit
        let from: String, to: String
        switch field {
        case "name":
            let v = String(raw.prefix(40))
            if host.channels.contains(where: { $0.id != channel.id && $0.name.lowercased() == v.lowercased() }) {
                return .result("Another channel is already called “\(v)”.")
            }
            (edit, from, to) = (.name(v), channel.name, v)
        case "niche": (edit, from, to) = (.niche(String(raw.prefix(120))), channel.niche, String(raw.prefix(120)))
        case "tone": (edit, from, to) = (.tone(String(raw.prefix(120))), channel.tone, String(raw.prefix(120)))
        case "language": (edit, from, to) = (.language(String(raw.prefix(40))), channel.language, String(raw.prefix(40)))
        case "seconds":
            guard let digits = raw.split(whereSeparator: { !$0.isNumber }).first, let n = Int(digits), (15...600).contains(n) else {
                return .result("Error: seconds must be a number from 15 to 600.")
            }
            let rounded = Int((Double(n) / 15).rounded()) * 15   // the editor steps by 15
            (edit, from, to) = (.seconds(rounded), "\(channel.targetSeconds)s", "\(rounded)s")
        default:
            let v = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            let aspects: [String: AspectRatio] = ["9:16": .portrait, "portrait": .portrait, "16:9": .landscape,
                                                  "landscape": .landscape, "1:1": .square, "square": .square]
            guard let aspect = aspects[v] else { return .result("Error: aspect must be 9:16, 16:9 or 1:1.") }
            (edit, from, to) = (.aspect(aspect), channel.aspectRatio.rawValue, aspect.rawValue)
        }
        if from == to { return .result("“\(channel.name)” already has that \(field).") }
        return .needsApproval(AgentApproval(
            title: "Edit channel “\(AgentTools.short(channel.name, 30))”?",
            detail: "\(field.capitalized): \(from.isEmpty ? "(empty)" : AgentTools.short(from, 40)) → \(to.isEmpty ? "(empty)" : AgentTools.short(to, 40))",
            estimate: "", estimateUSD: 0, action: .updateChannel(id: channel.id, edit: edit), kind: .change))
    }

    // MARK: Read-only

    @MainActor static func appStatus(_ host: any AgentHost, _ context: AgentContext) -> String {
        var lines: [String] = []
        let running = host.projects.filter { host.isRunning($0.id) }
        let queued = host.projects.filter { host.queuePosition($0.id) != nil }
        if running.isEmpty && queued.isEmpty {
            lines.append("Nothing is running or queued.")
        } else {
            if !running.isEmpty { lines.append("Running: " + running.prefix(3).map { "“\(AgentTools.short($0.title, 30))” (\(AgentTools.ref($0)))" }.joined(separator: ", ")) }
            if !queued.isEmpty { lines.append("Queued: " + queued.prefix(3).map { "“\(AgentTools.short($0.title, 30))”" }.joined(separator: ", ")) }
        }
        if let plan = host.recoveryPlan, let p = host.projects.first(where: { $0.id == plan.projectID }) {
            lines.append("Waiting for the user's decision on “\(AgentTools.short(p.title, 30))”: \(AgentTools.short(plan.title, 80)).")
        }
        let s = host.settings
        lines.append("Writer \(s.text.label) · Images \(s.image.label) · Voice \(s.voice.label)")
        lines.append("Auto fallback model: \(s.autoUseFallback ? "on" : "off")")
        let keys = APIKey.allCases.map { key -> String in
            guard context.hasKey(key) else { return "\(key.label): missing" }
            var text = "\(key.label): saved"
            if context.hasBackupKey(key) {
                if let left = KeyBackup.remaining(key) { text += ", backup in use (\(KeyBackup.remainingText(left)))" } else { text += ", backup saved" }
            }
            return text
        }
        lines.append("Keys (never shown): " + keys.joined(separator: "; "))
        lines.append("Apple Intelligence: \(context.appleAvailable ? "available" : "not available on this phone")")
        return lines.joined(separator: "\n")
    }

    @MainActor static func activity(_ args: AgentArgs, _ host: any AgentHost) -> String {
        let limit = min(max(args.int("limit") ?? 8, 1), 20)
        let lines = host.recentActivity(limit: limit, errorsOnly: args.bool("errors_only") ?? false)
        return lines.isEmpty ? "No recent activity." : lines.joined(separator: "\n")
    }

    @MainActor static func settingsGroup(_ args: AgentArgs, _ host: any AgentHost) -> String {
        let valid = AgentSetting.Group.allCases.map { $0.rawValue }.joined(separator: ", ")
        guard let raw = args.string("group")?.lowercased(),
              let group = AgentSetting.Group.allCases.first(where: { $0.rawValue == raw || $0.title.lowercased() == raw }) else {
            return "Error: group must be one of \(valid)."
        }
        return AgentSettingsCatalog.describe(group, settings: host.settings)
    }

    // MARK: Performing approved actions

    /// Carries out the actions this file creates. nil = not one of ours.
    @MainActor static func perform(_ action: AgentApprovedAction, host: any AgentHost) -> AgentPerformed? {
        func done(_ text: String) -> AgentPerformed { AgentPerformed(text: text, projectID: nil) }
        switch action {
        case .changeSetting(let key, let value):
            var s = host.settings
            switch AgentSettingsCatalog.apply(key: key, value: value, to: &s) {
            case .success(let c):
                host.settings = s
                return done("Changed \(c.title): \(c.from) → \(c.to).")
            case .failure(let e):
                return done(e.text)
            }
        case .updateChannel(let id, let edit):
            guard var channel = host.channels.first(where: { $0.id == id }) else { return done("That channel no longer exists.") }
            edit.apply(to: &channel)
            host.upsertChannel(channel)
            return done("Updated channel “\(channel.name)”.")
        case .setActiveChannel(let id):
            guard let c = host.channels.first(where: { $0.id == id }) else { return done("That channel no longer exists.") }
            host.activateChannel(c)
            return done("“\(c.name)” is now the active channel.")
        case .renameProject(let id, let title):
            guard let p = host.projects.first(where: { $0.id == id }) else { return done("That project no longer exists.") }
            host.renameProject(id, to: title)
            return done("Renamed “\(AgentTools.short(p.title))” to “\(AgentTools.short(title))”.")
        case .archiveProject(let id, let archived):
            guard let p = host.projects.first(where: { $0.id == id }) else { return done("That project no longer exists.") }
            host.setArchived(id, archived)
            return done("\(archived ? "Archived" : "Unarchived") “\(AgentTools.short(p.title))”.")
        case .deleteProject(let id):
            guard let p = host.projects.first(where: { $0.id == id }) else { return done("That project no longer exists.") }
            guard !host.isBusy(id) else { return done("“\(AgentTools.short(p.title))” started running; it was not deleted.") }
            host.deleteProject(id)
            return done("Deleted “\(AgentTools.short(p.title))”.")
        case .createVideo, .redo:
            return nil
        }
    }
}

import Foundation

/// What the assistant is allowed to do, one switch each (Settings > Assistant
/// > Assistant capabilities). To the model these are TOOLS: a capability that
/// is off is not offered to it at all - it cannot call what it cannot see -
/// and a call made anyway is refused with the setting's name.
///
/// Anything that changes something (starts or redoes a video, changes a
/// setting or a channel, renames/archives/deletes a project) still waits for
/// the person's tap on every use. The switches decide what the assistant may
/// ATTEMPT; the approval decides what actually happens.
///
/// What the assistant can never do, whatever is switched on: read or change
/// an API key, change its own capabilities, spending limit or approval mode,
/// or change another setting outside the catalogue (`AgentSettingsCatalog`).
enum AgentCapability: String, CaseIterable, Identifiable {
    case startVideos, redoSteps, cancelRuns, manageProjects, changeSettings, editChannels, diagnostics

    var id: String { rawValue }

    var title: String {
        switch self {
        case .startVideos: return "Start videos"
        case .redoSteps: return "Redo and resume steps"
        case .cancelRuns: return "Stop running videos"
        case .manageProjects: return "Manage projects"
        case .changeSettings: return "Change app settings"
        case .editChannels: return "Edit channels"
        case .diagnostics: return "Read settings and activity"
        }
    }

    /// One line under the switch: what it lets the assistant do.
    var summary: String {
        switch self {
        case .startVideos: return "Make a new video from a topic. Asks first."
        case .redoSteps: return "Re-run a video from a step, or pick up where it stopped. Asks first."
        case .cancelRuns: return "Stop a video that is running or waiting."
        case .manageProjects: return "Rename, archive or delete projects. Asks first."
        case .changeSettings: return "Change writer, voice, quality, captions and other settings. Asks first."
        case .editChannels: return "Edit a channel's name, niche, tone, language, length or shape, or switch channels. Asks first."
        case .diagnostics: return "See what is running, which keys are saved, current settings and recent errors."
        }
    }

    var symbol: String {
        switch self {
        case .startVideos: return "wand.and.stars"
        case .redoSteps: return "arrow.clockwise"
        case .cancelRuns: return "stop.circle"
        case .manageProjects: return "folder.badge.gearshape"
        case .changeSettings: return "slider.horizontal.3"
        case .editChannels: return "person.crop.rectangle"
        case .diagnostics: return "stethoscope"
        }
    }

    /// The tools this capability unlocks.
    var tools: [String] {
        switch self {
        case .startVideos: return ["create_video"]
        case .redoSteps: return ["redo_from"]
        case .cancelRuns: return ["cancel"]
        case .manageProjects: return ["manage_project"]
        case .changeSettings: return ["change_setting"]
        case .editChannels: return ["edit_channel"]
        case .diagnostics: return ["get_app_status", "get_activity", "get_settings"]
        }
    }

    var keyPath: WritableKeyPath<AppSettings, Bool> {
        switch self {
        case .startVideos: return \.agentAllowCreate
        case .redoSteps: return \.agentAllowRedo
        case .cancelRuns: return \.agentAllowCancel
        case .manageProjects: return \.agentAllowManageProjects
        case .changeSettings: return \.agentAllowChangeSettings
        case .editChannels: return \.agentAllowEditChannels
        case .diagnostics: return \.agentAllowDiagnostics
        }
    }

    func isOn(_ settings: AppSettings) -> Bool { settings[keyPath: keyPath] }

    /// What the model is told when this one is off, so it explains instead of retrying.
    var offMessage: String {
        switch self {
        case .startVideos: return "You cannot start videos; tell the user it is turned off in Settings."
        case .redoSteps: return "You cannot redo steps; tell the user it is turned off in Settings."
        case .cancelRuns: return "You cannot cancel; tell the user it is turned off in Settings."
        case .manageProjects: return "You cannot rename, archive or delete projects; tell the user it is turned off in Settings."
        case .changeSettings: return "You cannot change app settings; tell the user it is turned off in Settings."
        case .editChannels: return "You cannot edit channels; tell the user it is turned off in Settings."
        case .diagnostics: return "You cannot read app status, settings or logs; tell the user it is turned off in Settings."
        }
    }

    /// Tools every assistant has: they only read, or only estimate.
    static let alwaysOn = ["list_channels", "list_projects", "get_status", "open_project", "estimate_cost"]

    /// The capability that owns a tool, or nil for an always-on one.
    static func owning(_ tool: String) -> AgentCapability? {
        allCases.first { $0.tools.contains(tool) }
    }

    /// Is `tool` currently offered? `get_settings` is also offered with
    /// "change settings": the model cannot choose a valid value without it.
    static func isOffered(_ tool: String, in settings: AppSettings) -> Bool {
        if tool == "get_settings" { return diagnostics.isOn(settings) || changeSettings.isOn(settings) }
        guard let cap = owning(tool) else { return true }
        return cap.isOn(settings)
    }

    static func enabledCount(_ settings: AppSettings) -> Int { allCases.filter { $0.isOn(settings) }.count }
}

import Foundation

// The "edit it yourself?" moment.
//
// Before a video's FIRST render, the person is offered a short window to take
// over the edit - add music or effects, title cards, fix captions - instead of
// letting the AI finish it. Three channel modes decide what happens:
//
//  - Ask   (default): a 10-second card appears; no answer means the AI goes on.
//  - Auto: never asks; the AI finishes the video as before.
//  - Manual: always stops before the render and waits for the person's edit.
//
// Only the first render of a video is ever held. A re-render, a video that
// already has a take, one the person already answered for this session, or a
// run started by hand with the Render button goes straight through: the gate
// exists to offer a choice once, not to ask on every run. And it never waits
// while the app is in the background - a queue must not stall on a card nobody
// can see.

enum EditingMode: String, Codable, CaseIterable, Identifiable {
    case ask, auto, manual

    var id: String { rawValue }

    var title: String {
        switch self {
        case .ask: return "Ask me first"
        case .auto: return "Let AI finish"
        case .manual: return "I edit every video"
        }
    }

    var detail: String {
        switch self {
        case .ask: return "Before a video's first render, you get 10 seconds to take over the edit. If you do nothing, the AI finishes it."
        case .auto: return "Never asks. The AI finishes every video, as before."
        case .manual: return "Always stops before the render so you can edit first."
        }
    }
}

enum EditDecision: Equatable {
    case letAIEdit
    case editMyself
}

/// The card on screen while the countdown runs.
struct EditGate: Identifiable, Equatable {
    let id = UUID()
    let projectID: String
    let title: String
    let deadline: Date

    /// How long the person has.
    static let seconds = 10.0
}

enum EditGatePolicy {
    /// Pure: whether the render should stop and ask / wait, given the situation.
    enum Outcome: Equatable {
        case goOn        // render straight away
        case ask         // show the 10-second card
        case hold        // stop and wait for the person's edit
    }

    static func outcome(mode: EditingMode, hasTakes: Bool, alreadyAnswered: Bool, appActive: Bool) -> Outcome {
        if hasTakes || alreadyAnswered { return .goOn }
        switch mode {
        case .auto: return .goOn
        case .manual: return .hold
        case .ask: return appActive ? .ask : .goOn
        }
    }
}

/// What the person changed about ONE video, beside its channel's settings.
/// `nil` everywhere means "do what the channel says".
struct ProjectEdits: Codable, Hashable {
    var music: MusicOverride?
    var sfxEnabled: Bool?

    var isEmpty: Bool { music == nil && sfxEnabled == nil }
}

enum MusicOverride: Codable, Hashable {
    case silent
    case auto
    case track(String)
}

import Foundation

/// The finer-grained steps inside each pipeline phase.
///
/// The PC app shows eight phases (Brief, Script, Director I, Visuals, Voice,
/// Director II, Screen Text, Render), with Script and Render split further
/// into leaves. This app stores five (`PhaseID`) because several of those
/// are one piece of work here. Steps put that detail back, so a phase can be
/// expanded to show what it is actually doing and which part is slow or
/// failed:
///
///   Script   - Topic research, Creative brief, Narration, Scene split
///              (PC: optional research, Brief, Script draft + segmentation)
///   Visuals  - Scene images
///   Voice    - Record narration, Remove dead air, Join with pauses
///              (PC: Voice & audio)
///   Captions - Word timing, Takeover cards, Caption cues
///              (PC: Screen Text)
///   Render   - Motion plan, Compose frames, Add narration
///              (PC: Director II, then Render's base and composite passes)
///
/// Steps are bookkeeping only: they never change what a phase does, and a
/// project saved before they existed simply shows its finished phases' steps
/// as done (see `Project.displayStatus`).
enum StepStatus: String, Codable, Hashable {
    case pending, running, done, failed, skipped
}

struct StepDef: Identifiable, Hashable {
    let key: String
    let title: String
    var id: String { key }
}

struct StepRecord: Codable, Hashable {
    var status: StepStatus = .pending
    var started: Date?
    var finished: Date?
    /// Set explicitly when the real duration is known better than
    /// `finished - started` (the renderer reports its own frame and mux times).
    var seconds: Double?
    /// One short line shown once the step has finished, e.g. "Groq" or
    /// "8 scenes".
    var detail: String?

    var elapsed: Double? {
        if let seconds { return seconds }
        guard let started, let finished else { return nil }
        return max(0, finished.timeIntervalSince(started))
    }
}

extension PhaseID {
    var steps: [StepDef] {
        switch self {
        case .writing:
            return [StepDef(key: "research", title: "Topic research"),
                    StepDef(key: "brief", title: "Creative brief"),
                    StepDef(key: "draft", title: "Narration"),
                    StepDef(key: "segmentation", title: "Scene split")]
        case .visuals:
            return [StepDef(key: "images", title: "Scene images")]
        case .voice:
            return [StepDef(key: "speech", title: "Record narration"),
                    StepDef(key: "trim", title: "Remove dead air"),
                    StepDef(key: "assemble", title: "Join with pauses")]
        case .captions:
            return [StepDef(key: "timing", title: "Word timing"),
                    StepDef(key: "cards", title: "Takeover cards"),
                    StepDef(key: "cues", title: "Caption cues")]
        case .render:
            return [StepDef(key: "direction", title: "Motion plan"),
                    StepDef(key: "frames", title: "Compose frames"),
                    StepDef(key: "mux", title: "Add narration")]
        }
    }
}

extension Project {
    func stepKey(_ phase: PhaseID, _ key: String) -> String { "\(phase.rawValue).\(key)" }

    /// The stored record, or an empty pending one.
    func step(_ phase: PhaseID, _ key: String) -> StepRecord {
        stepRecords?[stepKey(phase, key)] ?? StepRecord()
    }

    /// What to SHOW for a step. A finished phase whose project predates step
    /// tracking (no records at all) shows every step done rather than a row
    /// of grey "pending" under a green phase.
    func displayStatus(_ phase: PhaseID, _ key: String) -> StepStatus {
        let r = step(phase, key)
        if r.status == .pending, stepRecords?[stepKey(phase, key)] == nil,
           self.phase(phase).status == .done,
           !(stepRecords?.keys.contains { $0.hasPrefix(phase.rawValue + ".") } ?? false) {
            return .done
        }
        return r.status
    }

    /// How many of a phase's steps are finished (done or skipped), for a
    /// "2 of 4" readout when a phase is partly through.
    func stepProgress(_ phase: PhaseID) -> (finished: Int, total: Int) {
        let all = phase.steps
        let n = all.filter {
            let s = displayStatus(phase, $0.key)
            return s == .done || s == .skipped
        }.count
        return (n, all.count)
    }
}

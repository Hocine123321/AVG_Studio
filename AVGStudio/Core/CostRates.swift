import Foundation

// The assistant's view of what a video is estimated to cost. The rates and
// the whole-video model live in `CostTable` (Costs.swift) - the one table the
// pipeline's recorded costs and the Create screen's estimate already share.
// This file adds no rates of its own; it only prices a *redo*, because
// `CostTable.estimate` prices a whole new video and "redo from Voice" must
// not be quoted as one, and it reports the number the approval card and the
// spend limit need.

/// What one video is estimated to cost, split by the phase that spends it,
/// so "redo from Voice" is priced from Voice onward.
struct CostBreakdown: Equatable {
    var text = 0.0            // spent in Script ("Writing")
    var images = 0.0          // spent in Visuals ("Images")
    var voice = 0.0           // spent in Voice
    var transcription = 0.0   // spent in Captions (only with an AssemblyAI key)

    /// Everything that would be spent running `phase` and every phase after it.
    func total(from phase: PhaseID) -> Double {
        var sum = 0.0
        if phase.order <= PhaseID.writing.order { sum += text }
        if phase.order <= PhaseID.visuals.order { sum += images }
        if phase.order <= PhaseID.voice.order { sum += voice }
        if phase.order <= PhaseID.captions.order { sum += transcription }
        return sum
    }

    /// `CostTable.estimate`, split back into its four phase lines. The line
    /// names are the table's own ("Writing", "Images", "Voice", "Captions");
    /// `CostRatesTests` pins them so a rename there cannot silently zero a phase.
    static func forVideo(settings: AppSettings, channel: Channel, hasAssemblyKey: Bool, poolSize: Int = 0) -> CostBreakdown {
        let e = CostTable.estimate(targetSeconds: channel.targetSeconds, settings: settings, channel: channel,
                                   hasTranscriptionKey: hasAssemblyKey, poolSize: poolSize)
        func usd(_ phase: String) -> Double { e.lines.first { $0.phase == phase }?.usd ?? 0 }
        return CostBreakdown(text: usd("Writing"), images: usd("Images"), voice: usd("Voice"), transcription: usd("Captions"))
    }
}

struct CostEstimate: Equatable {
    var usd: Double
    /// Only an exactly-zero estimate is free. A tiny non-zero one is paid.
    var isFree: Bool { usd == 0 }
    /// Worded the way the Create screen words it ("Free", "about $0.18").
    var summary: String { CostTable.Estimate.format(usd) }

    static func forNewVideo(settings: AppSettings, channel: Channel, hasAssemblyKey: Bool, poolSize: Int = 0) -> CostEstimate {
        forRedo(from: .writing, settings: settings, channel: channel, hasAssemblyKey: hasAssemblyKey, poolSize: poolSize)
    }

    static func forRedo(from phase: PhaseID, settings: AppSettings, channel: Channel, hasAssemblyKey: Bool, poolSize: Int = 0) -> CostEstimate {
        CostEstimate(usd: CostBreakdown.forVideo(settings: settings, channel: channel, hasAssemblyKey: hasAssemblyKey,
                                                 poolSize: poolSize).total(from: phase))
    }
}

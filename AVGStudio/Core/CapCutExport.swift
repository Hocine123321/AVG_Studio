import Foundation

/// Ported from the PC's `avg.export.capcut` (its own Slice 5, wired to a
/// real "My Videos" export action in the PC's later Slice 30). CapCut has
/// no project-interchange format - no XML/EDL the way Resolve or Premiere
/// would have, only ever an import of images + an audio track + an `.srt`
/// - so "export" there means assembling a clean folder plus a sheet that
/// saves the one manual step CapCut can't do for you: setting each
/// image's on-screen duration. Same idea here.
///
/// Two deliberate differences from the PC, both because this is iOS:
/// 1. No arbitrary "reveal in Explorer" - the PC's Open Folder route has
///    no iOS equivalent (no Finder-style access to another app's files).
///    The bundle is built in `FileManager.default.temporaryDirectory` and
///    handed to the system share sheet instead - Save to Files, AirDrop,
///    or send to a Mac - which IS the mobile-native "here's your folder."
///    `StudioView` owns cleaning the temp folder up once that sheet is
///    dismissed; this type only builds it.
/// 2. The PC's `export_take` reads a *specific take's own* copy of images/
///    audio/captions (`avg.core.takes.take_root` - a project can be
///    re-run between takes with different results, and the PC keeps each
///    take's inputs alongside its render for exactly that reason). This
///    app doesn't keep a separate per-take snapshot of scenes/captions/
///    audio, only the render itself (`Take.file`) - see `Store`'s own
///    per-project (not per-take) log note for the same simplification
///    elsewhere. So this exports the PROJECT's *current* scenes/captions/
///    audio alongside the *specific take's* video. Flagged in
///    `how-to-import.txt` itself, not just here, since it's the one way
///    this bundle can go stale relative to an older take.
enum CapCutExportError: LocalizedError {
    case noRender
    case missingAudio

    var errorDescription: String? {
        switch self {
        case .noRender: return "This take has no render on disk to export."
        case .missingAudio: return "This project's narration audio is missing - can't build a CapCut bundle without it."
        }
    }
}

@MainActor
enum CapCutExporter {
    /// Builds the export folder and returns its URL. Throws before
    /// creating anything if the take has no playable render or the
    /// project has no narration audio - a half-built export folder that
    /// LOOKS finished but is missing its video or audio would be worse
    /// than a clear error, same reasoning as the PC's own guard.
    static func export(project: Project, take: Take, store: Store) throws -> URL {
        let fm = FileManager.default
        guard let videoURL = store.file(project, take.file), fm.fileExists(atPath: videoURL.path) else {
            throw CapCutExportError.noRender
        }
        guard let audioName = project.audioFile, let audioURL = store.file(project, audioName), fm.fileExists(atPath: audioURL.path) else {
            throw CapCutExportError.missingAudio
        }

        let root = fm.temporaryDirectory.appendingPathComponent(
            "CapCut Export - \(sanitize("\(project.title) - \(take.label)"))", isDirectory: true
        )
        try? fm.removeItem(at: root) // Stale leftovers from a previous export of this same take.
        let imagesDir = root.appendingPathComponent("images", isDirectory: true)
        try fm.createDirectory(at: imagesDir, withIntermediateDirectories: true)

        // images/ - scene order, renamed 001/002/... so CapCut's own
        // filename sort lands in narration order, same as the PC.
        var copiedImages: [(index: Int, fileName: String)] = []
        for scene in project.scenes.sorted(by: { $0.index < $1.index }) {
            guard let src = store.file(project, scene.imageFile), fm.fileExists(atPath: src.path) else { continue }
            let name = String(format: "%03d.%@", scene.index, src.pathExtension.isEmpty ? "jpg" : src.pathExtension)
            try? fm.copyItem(at: src, to: imagesDir.appendingPathComponent(name))
            copiedImages.append((scene.index, name))
        }

        // narration.* - whatever extension Pipeline actually wrote (.m4a).
        try fm.copyItem(at: audioURL, to: root.appendingPathComponent("narration.\(audioURL.pathExtension)"))

        // captions.srt - straight from CaptionCue; no ASS-parsing detour
        // needed like the PC's, since captions here are already
        // structured data, not a rendered subtitle file to reverse-parse.
        try writeSRT(project.captions, to: root.appendingPathComponent("captions.srt"))

        // timing.csv - one row per copied image, cumulative start/end from
        // sceneDurations - the same running-sum VideoRenderer itself uses
        // to place each scene on the timeline (see its own `starts.append`).
        try writeTimingCSV(project: project, copiedImages: copiedImages, to: root.appendingPathComponent("timing.csv"))

        // master_output.mp4 - this take's own render, verbatim.
        try fm.copyItem(at: videoURL, to: root.appendingPathComponent("master_output.mp4"))

        try howToImportText(projectTitle: project.title, takeLabel: take.label)
            .write(to: root.appendingPathComponent("how-to-import.txt"), atomically: true, encoding: .utf8)

        return root
    }

    private static func sanitize(_ s: String) -> String {
        let bad = CharacterSet(charactersIn: "/\\:*?\"<>|")
        return s.components(separatedBy: bad).joined(separator: "-")
    }

    /// `HH:MM:SS,mmm` - the one time format `.srt` accepts. Built from
    /// integer milliseconds (never float arithmetic on the seconds
    /// themselves) so a value like 59.9997s carries into the next
    /// second/minute/hour correctly instead of an out-of-range field.
    private static func srtTime(_ seconds: Double) -> String {
        let totalMs = Int((max(0, seconds) * 1000).rounded())
        let h = totalMs / 3_600_000
        let m = (totalMs % 3_600_000) / 60_000
        let s = (totalMs % 60_000) / 1000
        let ms = totalMs % 1000
        return String(format: "%02d:%02d:%02d,%03d", h, m, s, ms)
    }

    private static func writeSRT(_ cues: [CaptionCue], to url: URL) throws {
        let blocks = cues.enumerated().map { i, cue in
            "\(i + 1)\n\(srtTime(cue.start)) --> \(srtTime(cue.end))\n\(cue.text)\n"
        }
        try blocks.joined(separator: "\n").write(to: url, atomically: true, encoding: .utf8)
    }

    /// `M:SS.mmm` for `timing.csv` - minutes unpadded, seconds zero-padded,
    /// matching the PC's own `_mmss` so the sheet reads the same way.
    private static func mmss(_ seconds: Double) -> String {
        let totalMs = Int((max(0, seconds) * 1000).rounded())
        let minutes = totalMs / 60_000
        let rem = Double(totalMs % 60_000) / 1000
        return String(format: "%d:%06.3f", minutes, rem)
    }

    private static func csvField(_ s: String) -> String {
        guard s.contains(",") || s.contains("\"") || s.contains("\n") else { return s }
        return "\"\(s.replacingOccurrences(of: "\"", with: "\"\""))\""
    }

    private static func writeTimingCSV(project: Project, copiedImages: [(index: Int, fileName: String)], to url: URL) throws {
        var acc = 0.0
        var starts: [Int: Double] = [:]
        var ends: [Int: Double] = [:]
        for (i, d) in project.sceneDurations.enumerated() where project.scenes.indices.contains(i) {
            let sceneIndex = project.scenes[i].index
            starts[sceneIndex] = acc
            acc += d
            ends[sceneIndex] = acc
        }
        let textByScene = Dictionary(uniqueKeysWithValues: project.scenes.map { ($0.index, Pipeline.spoken($0.text)) })

        var rows = ["image,start,end,duration,text"]
        for (index, fileName) in copiedImages {
            let start = starts[index]
            let end = ends[index]
            let durationStr = (start != nil && end != nil) ? String(format: "%.3f", end! - start!) : ""
            rows.append([
                fileName,
                start.map(mmss) ?? "",
                end.map(mmss) ?? "",
                durationStr,
                csvField(textByScene[index] ?? ""),
            ].joined(separator: ","))
        }
        try rows.joined(separator: "\n").write(to: url, atomically: true, encoding: .utf8)
    }

    private static func howToImportText(projectTitle: String, takeLabel: String) -> String {
        """
        How to bring "\(projectTitle)" (\(takeLabel)) into CapCut

        This folder has everything AVG Studio produced for this take, laid
        out the way CapCut can actually use it:

        1. Import the images/ folder as media. CapCut lays them out in
           filename order (001, 002, ...).
        2. Drop narration.* onto the audio track.
        3. Open timing.csv - it lists the exact start, end and duration (in
           seconds) for every image, right next to the line of narration
           spoken over it, so nothing needs to be scrubbed for. Set each
           image's duration from that sheet.
        4. captions.srt has the full caption track. If your CapCut app can
           import .srt directly, use that. If it can't (some mobile
           versions only pull captions from a video file, not a stand-alone
           .srt), the same text and timing is right there in timing.csv to
           re-type, or use as a reference alongside CapCut's own
           auto-captions.

        master_output.mp4 is the reference cut - the finished video this
        take already produced - in case you want to compare against it
        while you edit, or just post it as-is.

        Note: the images and captions above reflect this project's CURRENT
        state, not necessarily a frozen snapshot from when this specific
        take was rendered. If you've regenerated the script or art since,
        render a fresh take before exporting for the closest match.
        """
    }
}

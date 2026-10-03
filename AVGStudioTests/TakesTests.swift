import XCTest
@testable import AVGStudio

final class PhaseInputsTests: XCTestCase {
    private func inputs(_ phase: PhaseID, _ s: AppSettings = AppSettings(), _ ch: Channel = Channel(name: "c")) -> [String: String] {
        PhaseInputs.describe(phase, settings: s, channel: ch)
    }

    func testAChangeOnlyTouchesThePhasesItAffects() {
        let base = AppSettings()
        var voice = base; voice.voice = .elevenlabs
        XCTAssertNotEqual(inputs(.voice, base), inputs(.voice, voice))
        for other in [PhaseID.writing, .visuals, .captions, .render] {
            XCTAssertEqual(inputs(other, base), inputs(other, voice), "\(other) must not depend on the voice")
        }

        var fps = base; fps.fps = 60
        XCTAssertNotEqual(inputs(.render, base), inputs(.render, fps))
        for other in [PhaseID.writing, .visuals, .voice, .captions] {
            XCTAssertEqual(inputs(other, base), inputs(other, fps), "\(other) must not depend on frame rate")
        }

        var words = base; words.captionMaxWords = 7
        XCTAssertNotEqual(inputs(.captions, base), inputs(.captions, words))
        XCTAssertEqual(inputs(.render, base), inputs(.render, words))
    }

    func testWriterModelOnlyMattersForTheProviderInUse() {
        var a = AppSettings(); a.text = .groq; a.groqModel = "m1"
        var b = a; b.geminiModel = "something-else"
        XCTAssertEqual(inputs(.writing, a), inputs(.writing, b), "an unused provider's model is not an input")
        var c = a; c.groqModel = "m2"
        XCTAssertNotEqual(inputs(.writing, a), inputs(.writing, c))
    }

    func testStyleLockMovesTheImageStyleOutOfTheVisualsInputs() {
        var s = AppSettings(); s.imageStyle = "cinematic"
        var ch = Channel(name: "c")
        XCTAssertEqual(inputs(.visuals, s, ch)["imageStyle"], "cinematic")
        ch.styleLock = "ink wash"
        XCTAssertEqual(inputs(.visuals, s, ch)["imageStyle"], "", "a style lock replaces the global suffix")
        XCTAssertEqual(inputs(.writing, s, ch)["styleLock"], "ink wash")
    }

    func testChannelCreativeSettingsInvalidateWriting() {
        var ch = Channel(name: "c")
        let before = inputs(.writing, AppSettings(), ch)
        ch.contentShape = "news_report"
        XCTAssertNotEqual(before, inputs(.writing, AppSettings(), ch))
        ch = Channel(name: "c"); ch.brandPreset = "neon_cyber"
        XCTAssertNotEqual(inputs(.render, AppSettings(), Channel(name: "c")), inputs(.render, AppSettings(), ch))
    }

    func testEveryInputKeyHasAReadableLabel() {
        let s = AppSettings(), ch = Channel(name: "c")
        for phase in PhaseID.allCases {
            for key in PhaseInputs.describe(phase, settings: s, channel: ch).keys {
                XCTAssertNotNil(PhaseInputs.labels[key], "\(key) (\(phase)) has no label for the change list")
            }
        }
    }

    func testChangedKeysAreSortedByLabelAndIncludeAddedOrRemoved() {
        let changed = PhaseInputs.changedKeys(recorded: ["fps": "30", "quality": "720", "font": "x"],
                                              current: ["fps": "60", "quality": "720", "kenBurns": "true"])
        XCTAssertEqual(changed, ["font", "fps", "kenBurns"].sorted { (PhaseInputs.labels[$0] ?? $0) < (PhaseInputs.labels[$1] ?? $1) })
    }
}

final class StaleReportTests: XCTestCase {
    private func project(done phases: [PhaseID], settings: AppSettings, channel: Channel) -> Project {
        var p = Project(id: "p", title: "T", topic: "t")
        for phase in phases {
            p.phases[phase.rawValue] = PhaseRecord(status: .done, inputs: PhaseInputs.describe(phase, settings: settings, channel: channel))
        }
        return p
    }
    private func report(_ p: Project, _ s: AppSettings, _ ch: Channel) -> StaleReport {
        StaleReport.compute(project: p, settings: s, channel: ch, hasTranscriptionKey: false)
    }

    func testNothingChangedMeansNothingStale() {
        let s = AppSettings(), ch = Channel(name: "c")
        let p = project(done: PhaseID.allCases, settings: s, channel: ch)
        XCTAssertTrue(report(p, s, ch).isEmpty)
        XCTAssertNil(report(p, s, ch).firstPhase)
    }

    func testChangingTheFrameRateStalesOnlyRender() {
        let s = AppSettings(), ch = Channel(name: "c")
        let p = project(done: PhaseID.allCases, settings: s, channel: ch)
        var now = s; now.fps = 60
        let r = report(p, now, ch)
        XCTAssertEqual(Set(r.stale.keys), [.render])
        XCTAssertEqual(r.firstPhase, .render)
        XCTAssertEqual(r.willRerun, [.render])
        XCTAssertEqual(r.changedLabels, ["Frame rate"])
    }

    func testChangingTheVoiceReRunsEverythingFromVoice() {
        let s = AppSettings(), ch = Channel(name: "c")
        let p = project(done: PhaseID.allCases, settings: s, channel: ch)
        var now = s; now.voice = .gemini
        now.fps = 60
        let r = report(p, now, ch)
        XCTAssertEqual(r.firstPhase, .voice)
        XCTAssertEqual(r.willRerun, [.voice, .captions, .render])
        XCTAssertTrue(r.changedLabels.contains("Voice provider"))
        XCTAssertTrue(r.changedLabels.contains("Frame rate"))
    }

    func testUnfinishedAndUnrecordedPhasesAreNeverStale() {
        let s = AppSettings(), ch = Channel(name: "c")
        var p = project(done: [.writing], settings: s, channel: ch)
        p.phases[PhaseID.voice.rawValue] = PhaseRecord(status: .running, inputs: ["voice": "old"])
        p.phases[PhaseID.captions.rawValue] = PhaseRecord(status: .done, inputs: nil) // finished before inputs were recorded
        var now = s; now.voice = .gemini
        XCTAssertTrue(report(p, now, ch).isEmpty, "running = not finished; no record = unknown, not changed")
    }

    func testChangedLabelsAreDeduplicatedAcrossPhases() {
        var r = StaleReport()
        r.stale[.writing] = ["tone", "text"]
        r.stale[.render] = ["tone", "fps"]
        XCTAssertEqual(r.changedLabels.filter { $0 == "Tone" }.count, 1)
        XCTAssertEqual(r.changedLabels.first, "Tone", "phase order, not alphabetical")
    }
}

final class TakeSnapshotTests: XCTestCase {
    func testCaptureAndApplyRoundTripContentOnly() throws {
        var p = Project(id: "p", title: "T", topic: "t")
        p.brief = "Emotional arc: x"; p.narration = "Hello."; p.audioFile = "narration.m4a"
        p.scenes = [VideoScene(index: 1, text: "Hello.", imagePrompt: "a", imageFile: "images/scene_01.jpg", label: "Hook")]
        p.sceneDurations = [2.5]; p.captionsSource = "assemblyai"
        p.phases[PhaseID.writing.rawValue] = PhaseRecord(status: .done, inputs: ["text": "groq"])
        let snap = TakeSnapshot.capture(p)

        var changed = p
        changed.narration = "Different."; changed.scenes = []; changed.sceneDurations = []; changed.audioFile = nil
        changed.takes = [Take(id: 1, label: "Take 1", created: Date(), file: "render/take_1.mp4")]
        changed.output = "render/take_1.mp4"
        changed.costs = [CostEntry(label: "x", amountUSD: 1)]
        snap.apply(to: &changed)

        XCTAssertEqual(changed.narration, "Hello.")
        XCTAssertEqual(changed.scenes.first?.label, "Hook")
        XCTAssertEqual(changed.sceneDurations, [2.5])
        XCTAssertEqual(changed.audioFile, "narration.m4a")
        XCTAssertEqual(changed.phase(.writing).inputs?["text"], "groq")
        XCTAssertEqual(changed.takes.count, 1, "restoring content must not touch the take list")
        XCTAssertEqual(changed.output, "render/take_1.mp4", "nor the video pointer")
        XCTAssertEqual(changed.costs.count, 1, "nor what has been spent")

        let decoded = try JSONDecoder().decode(TakeSnapshot.self, from: JSONEncoder().encode(snap))
        XCTAssertEqual(decoded, snap)
    }

    func testTakesSavedBeforeSnapshotsStillDecodeAndCannotBeRestored() throws {
        let old = #"{"id":1,"label":"Take 1","created":0,"file":"render/take_1.mp4"}"#
        let t = try JSONDecoder().decode(Take.self, from: Data(old.utf8))
        XCTAssertNil(t.snapshot)
        XCTAssertFalse(t.canRestore)
        XCTAssertFalse(t.isArchived)
    }

    func testProjectWithTakeLineageRoundTrips() throws {
        var p = Project(id: "p", title: "T", topic: "t")
        var t = Take(id: 2, label: "Take 2", created: Date(timeIntervalSince1970: 5), file: "render/take_2.mp4")
        t.snapshot = TakeSnapshot.capture(p); t.archive = "takes/2"; t.basedOn = 1; t.note = "Changed: Narrator"
        p.takes = [t]; p.workingTakeID = 2
        let back = try JSONDecoder().decode(Project.self, from: JSONEncoder().encode(p))
        XCTAssertEqual(back.takes.first?.basedOn, 1)
        XCTAssertEqual(back.takes.first?.note, "Changed: Narrator")
        XCTAssertEqual(back.takes.first?.archive, "takes/2")
        XCTAssertEqual(back.workingTakeID, 2)
    }
}

final class TakeArchiveTests: XCTestCase {
    private var root: URL!
    private let fm = FileManager.default

    override func setUpWithError() throws {
        root = fm.temporaryDirectory.appendingPathComponent("takearchive-\(UUID().uuidString)", isDirectory: true)
        try fm.createDirectory(at: root.appendingPathComponent("images"), withIntermediateDirectories: true)
        try fm.createDirectory(at: root.appendingPathComponent("audio"), withIntermediateDirectories: true)
        try Data("img-v1".utf8).write(to: root.appendingPathComponent("images/scene_01.jpg"))
        try Data("clean-v1".utf8).write(to: root.appendingPathComponent("audio/clean_0.caf"))
        try Data("narr-v1".utf8).write(to: root.appendingPathComponent("narration.m4a"))
        try Data("video".utf8).write(to: root.appendingPathComponent("project.json"))
    }
    override func tearDownWithError() throws { try? fm.removeItem(at: root) }

    private func text(_ rel: String) -> String? {
        (try? Data(contentsOf: root.appendingPathComponent(rel))).flatMap { String(data: $0, encoding: .utf8) }
    }

    func testArchiveThenOverwriteThenRestoreBringsTheOldFilesBack() throws {
        let rel = try TakeArchive.archive(takeID: 3, projectFolder: root)
        XCTAssertEqual(rel, "takes/3")
        XCTAssertEqual(text("takes/3/images/scene_01.jpg"), "img-v1")
        XCTAssertEqual(text("takes/3/narration.m4a"), "narr-v1")

        // The next run rewrites the working files.
        try Data("img-v2".utf8).write(to: root.appendingPathComponent("images/scene_01.jpg"))
        try fm.removeItem(at: root.appendingPathComponent("audio/clean_0.caf"))
        try Data("narr-v2".utf8).write(to: root.appendingPathComponent("narration.m4a"))
        XCTAssertEqual(text("takes/3/images/scene_01.jpg"), "img-v1", "the archive is untouched by later writes")

        try TakeArchive.restore(archive: rel, projectFolder: root)
        XCTAssertEqual(text("images/scene_01.jpg"), "img-v1")
        XCTAssertEqual(text("audio/clean_0.caf"), "clean-v1")
        XCTAssertEqual(text("narration.m4a"), "narr-v1")
        XCTAssertEqual(text("project.json"), "video", "only content files are replaced")
    }

    func testArchivingTwiceReplacesTheFolderInsteadOfNesting() throws {
        _ = try TakeArchive.archive(takeID: 1, projectFolder: root)
        try Data("img-v2".utf8).write(to: root.appendingPathComponent("images/scene_01.jpg"))
        _ = try TakeArchive.archive(takeID: 1, projectFolder: root)
        XCTAssertEqual(text("takes/1/images/scene_01.jpg"), "img-v2")
        XCTAssertFalse(fm.fileExists(atPath: root.appendingPathComponent("takes/1/images/images").path))
    }

    func testMissingContentIsSkippedNotAnError() throws {
        try fm.removeItem(at: root.appendingPathComponent("audio"))
        try fm.removeItem(at: root.appendingPathComponent("narration.m4a"))
        let rel = try TakeArchive.archive(takeID: 2, projectFolder: root)
        XCTAssertNotNil(text("\(rel)/images/scene_01.jpg"))
        XCTAssertFalse(fm.fileExists(atPath: root.appendingPathComponent("\(rel)/audio").path))
    }

    func testRestoringAMissingArchiveThrowsAndLeavesWorkingFilesAlone() {
        XCTAssertThrowsError(try TakeArchive.restore(archive: "takes/99", projectFolder: root))
        XCTAssertEqual(text("images/scene_01.jpg"), "img-v1")
    }

    func testRemoveDeletesTheFolder() throws {
        let rel = try TakeArchive.archive(takeID: 4, projectFolder: root)
        TakeArchive.remove(archive: rel, projectFolder: root)
        XCTAssertFalse(fm.fileExists(atPath: root.appendingPathComponent(rel).path))
    }

    func testAnArchiveSharedByTwoTakesSurvivesDeletingOne() {
        func take(_ id: Int, _ archive: String?) -> Take {
            var t = Take(id: id, label: "Take \(id)", created: Date(), file: "render/take_\(id).mp4"); t.archive = archive; return t
        }
        let shared = [take(1, "takes/2"), take(2, "takes/2"), take(3, "takes/3"), take(4, nil)]
        XCTAssertEqual(TakeArchive.orphanedArchives(takes: shared, deleting: 1), [], "take 2 still uses it")
        XCTAssertEqual(TakeArchive.orphanedArchives(takes: shared, deleting: 3), ["takes/3"])
        XCTAssertEqual(TakeArchive.orphanedArchives(takes: shared, deleting: 4), [], "an unarchived take owns no folder")
        XCTAssertEqual(TakeArchive.orphanedArchives(takes: [take(2, "takes/2")], deleting: 2), ["takes/2"])
    }
}

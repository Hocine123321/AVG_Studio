import XCTest
@testable import AVGStudio

final class RedactorTests: XCTestCase {
    func testStripsCredentialShapesButKeepsTheRest() {
        let cases: [(String, String)] = [
            ("Authorization: Bearer abc123def456", "Authorization: Bearer REDACTED"),
            ("x-goog-api-key: AIzaSyD-9tSrke72PouQMnMX-a7eZSW0jkFMBWY failed", "x-goog-api-key: REDACTED failed"),
            ("GET https://x.test/v1?key=AIzaSyD9tSrke72PouQ&alt=json", "GET https://x.test/v1?key=REDACTED&alt=json"),
            (#"{"api_key":"sk-live-0123456789abcdef","model":"m"}"#, #"{"api_key":"REDACTED","model":"m"}"#),
            ("token gsk_abcdefghijklmnop was rejected", "token REDACTED was rejected"),
            ("hf_AbCdEfGhIjKlMnOp in header", "REDACTED in header"),
        ]
        for (input, expected) in cases {
            XCTAssertEqual(Redactor.scrub(input), expected, input)
        }
        XCTAssertEqual(Redactor.scrub("Groq: model_not_supported (404)"), "Groq: model_not_supported (404)")
    }

    func testExactStoredSecretsAreCaughtEvenWithoutAPrefix() {
        let key = "0123456789abcdef0123456789abcdef"  // a bare-hex key has no telltale prefix
        XCTAssertEqual(Redactor.scrub("AssemblyAI said \(key) is invalid", secrets: [key]), "AssemblyAI said REDACTED is invalid")
        XCTAssertEqual(Redactor.scrub("short", secrets: ["abc"]), "short", "very short values are not treated as secrets")
        XCTAssertEqual(Redactor.scrub("nothing here", secrets: [""]), "nothing here")
    }
}

final class ErrorHistoryTests: XCTestCase {
    private var url: URL!

    override func setUp() {
        url = FileManager.default.temporaryDirectory.appendingPathComponent("errors-\(UUID().uuidString).json")
    }
    override func tearDown() { try? FileManager.default.removeItem(at: url) }

    private func record(_ message: String, _ t: TimeInterval) -> ErrorRecord {
        ErrorRecord(date: Date(timeIntervalSince1970: t), message: message, project: nil)
    }

    func testAppendIsNewestFirstAndSurvivesReload() {
        ErrorHistory.append(record("first", 1), to: url)
        ErrorHistory.append(record("second", 2), to: url)
        XCTAssertEqual(ErrorHistory.load(url).map(\.message), ["second", "first"])
    }

    func testTheCapDropsTheOldestNeverTheNewest() {
        for i in 1...7 { ErrorHistory.append(record("e\(i)", Double(i)), to: url, limit: 5) }
        XCTAssertEqual(ErrorHistory.load(url).map(\.message), ["e7", "e6", "e5", "e4", "e3"])
    }

    func testMissingOrCorruptFilesAreAnEmptyHistory() throws {
        XCTAssertTrue(ErrorHistory.load(url).isEmpty)
        try Data("not json".utf8).write(to: url)
        XCTAssertTrue(ErrorHistory.load(url).isEmpty)
        XCTAssertEqual(ErrorHistory.append(record("recovered", 1), to: url).count, 1, "a bad file never blocks recording")
    }

    func testAKeyNeverReachesTheFileBytes() throws {
        // The caller scrubs before appending (Store.record does); this checks the
        // raw bytes, not the parsed object - the secret must never land at all.
        let secret = "sk-proj-ABCDEFGHIJKLMNOP"
        let scrubbed = Redactor.scrub("OpenAI rejected Authorization: Bearer \(secret)")
        ErrorHistory.append(record(scrubbed, 1), to: url)
        let raw = try String(contentsOf: url, encoding: .utf8)
        XCTAssertFalse(raw.contains(secret))
        XCTAssertFalse(raw.contains("ABCDEFGH"))
        XCTAssertTrue(raw.contains("REDACTED"))
    }

    func testClearRemovesTheFile() {
        ErrorHistory.append(record("x", 1), to: url)
        ErrorHistory.clear(url)
        XCTAssertTrue(ErrorHistory.load(url).isEmpty)
    }

    func testReportScrubsAgainstOlderEntriesAndListsProjects() {
        var old = record("Gemini failed key=AIzaSyD9tSrke72PouQ", 5)
        old.project = "Octopus video"
        let text = ErrorHistory.report([old], appVersion: "1.0 (7)", device: "iPhone · iOS 26")
        XCTAssertTrue(text.contains("1.0 (7)"))
        XCTAssertTrue(text.contains("[Octopus video]"))
        XCTAssertFalse(text.contains("AIzaSyD9"), "an entry written before a redaction rule existed is still scrubbed on the way out")
    }
}

@MainActor
final class ModelPrefsTests: XCTestCase {
    private var dir: URL!

    override func setUp() {
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("prefs-\(UUID().uuidString)", isDirectory: true)
    }
    override func tearDown() { try? FileManager.default.removeItem(at: dir) }

    func testFavouritesToggleAndPersist() {
        let prefs = ModelPrefs(directory: dir)
        XCTAssertFalse(prefs.isFavourite("m1", in: "groq_text"))
        prefs.toggleFavourite("m1", in: "groq_text")
        XCTAssertTrue(prefs.isFavourite("m1", in: "groq_text"))
        XCTAssertFalse(prefs.isFavourite("m1", in: "gemini_text"), "favourites are per model list")
        XCTAssertTrue(ModelPrefs(directory: dir).isFavourite("m1", in: "groq_text"), "survives a relaunch")
        prefs.toggleFavourite("m1", in: "groq_text")
        XCTAssertFalse(ModelPrefs(directory: dir).isFavourite("m1", in: "groq_text"))
    }

    func testOrderingIsFavouritesThenWorkingThenUntestedThenFailed() {
        let entries = ["a", "b", "c", "d", "e"].map { CatalogEntry(id: $0, label: $0.uppercased(), sublabel: nil) }
        let now = Date()
        let probes: [String: ProbeResult] = [
            "b": ProbeResult(works: true, checked: now, note: nil),
            "c": ProbeResult(works: false, checked: now, note: "Over quota"),
            "e": ProbeResult(works: true, checked: now, note: nil),
        ]
        let ordered = ModelPrefs.order(entries, favourites: ["d"], probes: probes).map(\.id)
        XCTAssertEqual(ordered, ["d", "b", "e", "a", "c"])
    }

    func testAFailedFavouriteStaysOnTopBecauseTheUserChoseIt() {
        let entries = ["a", "b"].map { CatalogEntry(id: $0, label: $0, sublabel: nil) }
        let probes = ["b": ProbeResult(works: false, checked: Date(), note: "x")]
        XCTAssertEqual(ModelPrefs.order(entries, favourites: ["b"], probes: probes).map(\.id), ["b", "a"])
    }

    func testMergeKeepsUntouchedModelsAndLetsNewFindingsWin() {
        let old = ["a": ProbeResult(works: true, checked: Date(timeIntervalSince1970: 1), note: nil),
                   "b": ProbeResult(works: true, checked: Date(timeIntervalSince1970: 1), note: nil)]
        let fresh = ["b": ProbeResult(works: false, checked: Date(timeIntervalSince1970: 9), note: "Over quota")]
        let merged = ModelPrefs.merged(existing: old, fresh: fresh)
        XCTAssertEqual(merged["a"]?.works, true, "not retested, so unchanged")
        XCTAssertEqual(merged["b"]?.works, false)
        XCTAssertEqual(merged["b"]?.note, "Over quota")
    }

    func testProbeResultsPersistSeparatelyFromFavourites() throws {
        let prefs = ModelPrefs(directory: dir)
        prefs.toggleFavourite("x", in: "groq_text")
        XCTAssertTrue(FileManager.default.fileExists(atPath: dir.appendingPathComponent("model_prefs.json").path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: dir.appendingPathComponent("working_models.json").path),
                       "the app-owned results file is only written by a sweep")
    }

    func testOnlyTextListsCanBeProbed() {
        XCTAssertTrue(ModelProbe.isProbeable("groq_text"))
        XCTAssertTrue(ModelProbe.isProbeable("gemini_text"))
        XCTAssertTrue(ModelProbe.isProbeable("pollinations_text"))
        for paid in ["gemini_image", "pollinations_image", "stablehorde_image", "elevenlabs_voices", "elevenlabs_models", "gemini_voice_model"] {
            XCTAssertFalse(ModelProbe.isProbeable(paid), "\(paid): a probe is a real call and costs money there")
        }
    }
}

final class ModelProbeClassifyTests: XCTestCase {
    private func outcome(_ kind: ErrorKind) -> ModelProbe.Outcome {
        ModelProbe.classify(AVGError.provider(kind, provider: "groq", message: "boom"))
    }

    func testOnlyAHardRefusalMarksAModelAsFailed() {
        XCTAssertEqual(outcome(.modelMissing), .failed("Not available to this account"))
        XCTAssertEqual(outcome(.quota), .failed("Over quota"))
        XCTAssertEqual(outcome(.billing), .failed("Needs billing"))
    }

    func testTemporaryTroubleSaysNothingAboutTheModel() {
        for kind in [ErrorKind.rateLimit, .network, .timeout, .contentFilter, .dependency, .bug] {
            XCTAssertEqual(outcome(kind), .inconclusive("boom"), "\(kind) must not be recorded as the model failing")
        }
    }

    func testARejectedKeyStopsTheSweepInsteadOfFailingEveryModel() {
        XCTAssertEqual(outcome(.auth), .authFailed("The key was rejected."))
        XCTAssertEqual(ModelProbe.classify(AVGError.missingKey("Groq")), .authFailed("Add the key first."))
    }

    func testCancellationAndUnknownErrorsAreInconclusive() {
        XCTAssertEqual(ModelProbe.classify(CancellationError()), .inconclusive("Cancelled"))
        XCTAssertEqual(ModelProbe.classify(AVGError.message("odd")), .inconclusive("odd"))
        if case .inconclusive = ModelProbe.classify(URLError(.badServerResponse)) {} else { XCTFail("a foreign error is not a verdict on the model") }
    }
}

import XCTest
@testable import AVGStudio

final class MusicPromptTests: XCTestCase {
    func testTheBedIsInstrumentalAndNamesTheMood() {
        let p = MusicPrompt.background(mood: "calm", detail: "")
        XCTAssertTrue(p.contains("Mood: calm."))
        XCTAssertTrue(p.contains("No vocals and no lyrics"))
        XCTAssertFalse(p.contains("Style notes"), "blank notes add nothing")
    }

    func testStyleNotesAreIncludedWhenGiven() {
        XCTAssertTrue(MusicPrompt.background(mood: "warm", detail: "  lo-fi piano  ").contains("Style notes: lo-fi piano."))
    }

    func testEveryMoodIsASingleLowercaseWord() {
        XCTAssertFalse(MusicPrompt.moods.isEmpty)
        for mood in MusicPrompt.moods { XCTAssertEqual(mood, mood.lowercased()); XCTAssertFalse(mood.contains(" ")) }
    }
}

final class StarterSoundsTests: XCTestCase {
    private func sfx(_ id: String, tags: [String], kind: AudioItem.Kind = .sfx) -> AudioItem {
        var item = AudioItem(kind: kind, name: id, fileName: "\(id).mp3")
        item.id = id
        item.tags = tags
        return item
    }

    func testThereIsExactlyOneStarterSoundPerMoment() {
        XCTAssertEqual(StarterSounds.items.map(\.moment).sorted { $0.rawValue < $1.rawValue },
                       SFXMoment.allCases.sorted { $0.rawValue < $1.rawValue })
        for item in StarterSounds.items {
            XCTAssertTrue((0.5...22).contains(item.seconds), "ElevenLabs accepts 0.5 to 22 seconds")
            XCTAssertFalse(item.prompt.isEmpty)
        }
    }

    func testFillGivesEachEmptyMomentItsStarterSound() {
        let library = [sfx("a", tags: ["starter", "impact"]), sfx("b", tags: ["starter", "whoosh"]), sfx("c", tags: ["mine"])]
        let map = StarterSounds.fill([:], from: library)
        XCTAssertEqual(map, ["impact": "a", "whoosh": "b"])
    }

    func testFillNeverReplacesAChosenSound() {
        let library = [sfx("a", tags: ["starter", "impact"])]
        XCTAssertEqual(StarterSounds.fill(["impact": "mine"], from: library), ["impact": "mine"])
    }

    func testFillIgnoresMusicAndUntaggedSounds() {
        let library = [sfx("m", tags: ["starter", "impact"], kind: .music), sfx("u", tags: ["impact"])]
        XCTAssertTrue(StarterSounds.fill([:], from: library).isEmpty)
    }
}

final class GeminiMusicParsingTests: XCTestCase {
    private func reply(parts: [[String: Any]]) -> [String: Any] {
        ["candidates": [["content": ["parts": parts]]]]
    }

    func testTheAudioPartIsDecoded() {
        let bytes = Data([1, 2, 3, 4])
        let r = reply(parts: [["text": "lyrics"], ["inlineData": ["mimeType": "audio/mpeg", "data": bytes.base64EncodedString()]]])
        XCTAssertEqual(GeminiMusic.audio(in: r), bytes)
    }

    func testSnakeCaseKeysAreAccepted() {
        let bytes = Data([9, 9])
        let r = reply(parts: [["inline_data": ["mime_type": "audio/wav", "data": bytes.base64EncodedString()]]])
        XCTAssertEqual(GeminiMusic.audio(in: r), bytes)
    }

    func testAReplyWithoutAudioGivesNothing() {
        XCTAssertNil(GeminiMusic.audio(in: reply(parts: [["text": "only words"]])))
        XCTAssertNil(GeminiMusic.audio(in: ["candidates": []]))
        XCTAssertNil(GeminiMusic.audio(in: [:]))
    }

    func testANonAudioInlinePartIsIgnored() {
        let r = reply(parts: [["inlineData": ["mimeType": "image/png", "data": Data([1]).base64EncodedString()]]])
        XCTAssertNil(GeminiMusic.audio(in: r))
    }
}

@MainActor
final class AudioLibraryImportDataTests: XCTestCase {
    func testGeneratedBytesGoThroughTheSameChecksAsAnUpload() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("gen-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let tone = try makeTestTone(seconds: 1, in: dir.appendingPathComponent("src"))
        let bytes = try Data(contentsOf: tone)
        let library = AudioLibrary(directory: dir.appendingPathComponent("lib"))

        let item = try await library.importData(bytes, fileExtension: "wav", kind: .music, name: "Calm bed",
                                                origin: .generated, license: "Generated with Google Lyria", tags: ["calm"])
        XCTAssertEqual(item.origin, .generated)
        XCTAssertEqual(item.license, "Generated with Google Lyria")
        XCTAssertEqual(item.tags, ["calm"])
        XCTAssertNotNil(library.fileURL(for: item.id))
    }

    func testBytesThatAreNotAudioAreRefused() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("gen-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let library = AudioLibrary(directory: dir)
        do {
            _ = try await library.importData(Data("not audio".utf8), fileExtension: "mp3", kind: .sfx, name: "x",
                                             origin: .generated, license: "x")
            XCTFail("a text response must not be saved as a sound")
        } catch {
            XCTAssertTrue(library.items.isEmpty)
        }
    }
}

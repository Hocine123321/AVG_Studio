import AVFoundation
import XCTest
@testable import AVGStudio

/// A short mono sine wave written to a temporary .wav, for tests that need real audio.
func makeTestTone(seconds: Double, hz: Double = 440, in dir: URL, name: String = UUID().uuidString) throws -> URL {
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    let url = dir.appendingPathComponent("\(name).wav")
    let format = AVAudioFormat(standardFormatWithSampleRate: 44_100, channels: 1)!
    let file = try AVAudioFile(forWriting: url, settings: format.settings, commonFormat: .pcmFormatFloat32, interleaved: false)
    let frames = AVAudioFrameCount(seconds * 44_100)
    let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)!
    buffer.frameLength = frames
    let samples = buffer.floatChannelData![0]
    for i in 0..<Int(frames) { samples[i] = Float(0.3 * sin(2 * Double.pi * hz * Double(i) / 44_100)) }
    try file.write(from: buffer)
    return url
}

final class MusicEnvelopeTests: XCTestCase {
    private let ducked = SoundMix.linear(SoundMix.defaultGainDB)
    private var open: Double { min(1, SoundMix.linear(SoundMix.defaultGainDB + SoundMix.openLiftDB)) }

    private func keys(_ speech: [(start: Double, end: Double)], total: Double = 10, loops: [Double] = []) -> [GainKeyframe] {
        MusicEnvelope.keyframes(speech: speech, total: total, gainDB: SoundMix.defaultGainDB, loopPoints: loops)
    }

    func testDuckedWhileSomeoneSpeaksAndOpenInThePauses() {
        let k = keys([(start: 2, end: 5)])
        XCTAssertEqual(MusicEnvelope.gain(at: 3.5, in: k), ducked, accuracy: 0.0001)
        XCTAssertEqual(MusicEnvelope.gain(at: 6.5, in: k), open, accuracy: 0.0001)
        XCTAssertEqual(MusicEnvelope.gain(at: 1.5, in: k), open, accuracy: 0.0001)
    }

    func testFadesInFromSilenceAndOutToSilence() {
        let k = keys([(start: 2, end: 5)])
        XCTAssertEqual(MusicEnvelope.gain(at: 0, in: k), 0, accuracy: 0.0001)
        XCTAssertEqual(MusicEnvelope.gain(at: 10, in: k), 0, accuracy: 0.0001)
        XCTAssertEqual(MusicEnvelope.gain(at: 9, in: k), open * 0.5, accuracy: 0.001, "halfway through the 2 s fade-out")
    }

    func testNeverLouderThanTheOpenLevelAndTimesOnlyIncrease() {
        let k = keys([(start: 0.2, end: 3), (start: 4.5, end: 9.5)])
        XCTAssertTrue(k.allSatisfy { $0.gain >= 0 && $0.gain <= open + 1e-9 })
        for (a, b) in zip(k, k.dropFirst()) { XCTAssertLessThan(a.time, b.time) }
        XCTAssertEqual(k.first?.time, 0)
        XCTAssertEqual(k.last?.time, 10)
    }

    func testQuickSuccessiveSentencesDoNotPumpTheMusic() {
        let k = keys([(start: 1, end: 2), (start: 2.4, end: 3)])
        XCTAssertEqual(MusicEnvelope.gain(at: 2.2, in: k), ducked, accuracy: 0.0001, "a 0.4 s gap stays ducked")
        let wide = keys([(start: 1, end: 2), (start: 5, end: 6)])
        XCTAssertEqual(MusicEnvelope.gain(at: 3.5, in: wide), open, accuracy: 0.0001, "a 3 s gap comes back up")
    }

    func testNoSpeechMeansAnOpenBedWithFades() {
        let k = keys([])
        XCTAssertEqual(MusicEnvelope.gain(at: 5, in: k), open, accuracy: 0.0001)
    }

    func testSpeechOutsideTheVideoIsIgnoredAndTinyVideosGiveNothing() {
        XCTAssertEqual(MusicEnvelope.gain(at: 5, in: keys([(start: 20, end: 30)])), open, accuracy: 0.0001)
        XCTAssertTrue(MusicEnvelope.keyframes(speech: [], total: 0.05, gainDB: -20).isEmpty)
    }

    func testARepeatSeamDipsToSilence() {
        let k = keys([], loops: [4])
        XCTAssertEqual(MusicEnvelope.gain(at: 4, in: k), 0, accuracy: 0.0001)
        XCTAssertEqual(MusicEnvelope.gain(at: 4 - MusicEnvelope.loopDip, in: k), open, accuracy: 0.0001)
        XCTAssertEqual(MusicEnvelope.gain(at: 4 + MusicEnvelope.loopDip, in: k), open, accuracy: 0.0001)
    }

    func testAQuieterSettingLowersBothLevels() {
        let quiet = MusicEnvelope.keyframes(speech: [(start: 2, end: 5)], total: 10, gainDB: -30)
        XCTAssertLessThan(MusicEnvelope.gain(at: 3.5, in: quiet), ducked)
    }
}

@MainActor
final class AudioLibraryTests: XCTestCase {
    private var dir: URL!
    override func setUp() { dir = FileManager.default.temporaryDirectory.appendingPathComponent("library-\(UUID().uuidString)", isDirectory: true) }
    override func tearDown() { try? FileManager.default.removeItem(at: dir) }

    func testImportCopiesMeasuresAndPersists() async throws {
        let tone = try makeTestTone(seconds: 1.5, in: dir.appendingPathComponent("src"))
        let library = AudioLibrary(directory: dir.appendingPathComponent("lib"))
        let item = try await library.importFile(tone, kind: .music, name: "Warm bed", tags: ["calm"])
        XCTAssertEqual(item.name, "Warm bed")
        XCTAssertEqual(item.duration, 1.5, accuracy: 0.1)
        XCTAssertEqual(item.license, "Mine")
        XCTAssertNotNil(library.fileURL(for: item.id))
        XCTAssertNotEqual(library.fileURL(for: item.id), tone, "the library keeps its own copy")

        let again = AudioLibrary(directory: dir.appendingPathComponent("lib"))
        XCTAssertEqual(again.music.map(\.name), ["Warm bed"])
        XCTAssertEqual(again.item(item.id)?.tags, ["calm"])
        XCTAssertTrue(again.effects.isEmpty)
    }

    func testAFileThatIsNotAudioIsRefusedAndLeavesNothingBehind() async throws {
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let fake = dir.appendingPathComponent("not-audio.mp3")
        try Data("this is text, not sound".utf8).write(to: fake)
        let library = AudioLibrary(directory: dir.appendingPathComponent("lib"))
        do {
            _ = try await library.importFile(fake, kind: .music)
            XCTFail("a text file must not be accepted as music")
        } catch {
            XCTAssertTrue(library.items.isEmpty)
        }
        let leftovers = (try? FileManager.default.contentsOfDirectory(atPath: dir.appendingPathComponent("lib/Music").path)) ?? []
        XCTAssertTrue(leftovers.isEmpty, "the failed copy is removed")
    }

    func testAnUnsupportedExtensionIsRefused() async throws {
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let doc = dir.appendingPathComponent("song.pdf")
        try Data([1, 2, 3]).write(to: doc)
        let library = AudioLibrary(directory: dir.appendingPathComponent("lib"))
        do {
            _ = try await library.importFile(doc, kind: .music)
            XCTFail("pdf is not audio")
        } catch let error as AudioLibraryError {
            guard case .unsupportedType = error else { return XCTFail("wrong error: \(error)") }
        }
    }

    func testRenameTagAndRemove() async throws {
        let tone = try makeTestTone(seconds: 1, in: dir.appendingPathComponent("src"))
        let library = AudioLibrary(directory: dir.appendingPathComponent("lib"))
        let item = try await library.importFile(tone, kind: .sfx)
        library.rename(item.id, to: "  Whoosh  ")
        library.setTags(item.id, ["transition"])
        XCTAssertEqual(library.item(item.id)?.name, "Whoosh")
        XCTAssertEqual(library.effects.first?.tags, ["transition"])
        let url = try XCTUnwrap(library.fileURL(for: item.id))
        library.remove(item.id)
        XCTAssertNil(library.item(item.id))
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
    }

    func testAnItemSavedWithoutNewerFieldsStillLoads() throws {
        let json = #"[{"kind":"music","name":"Old","fileName":"x.mp3"}]"#
        let items = try JSONDecoder().decode([AudioItem].self, from: Data(json.utf8))
        XCTAssertEqual(items.first?.name, "Old")
        XCTAssertEqual(items.first?.license, "Mine")
        XCTAssertEqual(items.first?.origin, .uploaded)
    }
}

final class MixdownTests: XCTestCase {
    private var dir: URL!
    override func setUp() { dir = FileManager.default.temporaryDirectory.appendingPathComponent("mix-\(UUID().uuidString)", isDirectory: true) }
    override func tearDown() { try? FileManager.default.removeItem(at: dir) }

    private func duration(of url: URL) async throws -> Double {
        try await AVURLAsset(url: url).load(.duration).seconds
    }

    func testMixIsAsLongAsTheNarrationAndShortMusicRepeats() async throws {
        let voice = try makeTestTone(seconds: 3, hz: 220, in: dir)
        let music = try makeTestTone(seconds: 1.2, hz: 660, in: dir)
        let out = dir.appendingPathComponent("out/mix.m4a")
        let result = try await Mixdown.render(
            Mixdown.Input(voice: voice, speech: [(start: 0.2, end: 2.8)], music: music), to: out)
        XCTAssertTrue(FileManager.default.fileExists(atPath: out.path))
        XCTAssertEqual(result.loops, 2, "1.2 s of music needs two repeats to cover 3 s")
        let length = try await duration(of: out)
        XCTAssertEqual(length, 3, accuracy: 0.2)
    }

    func testLongMusicIsCutToTheVideo() async throws {
        let voice = try makeTestTone(seconds: 2, in: dir)
        let music = try makeTestTone(seconds: 6, hz: 330, in: dir)
        let out = dir.appendingPathComponent("mix.m4a")
        let result = try await Mixdown.render(Mixdown.Input(voice: voice, speech: [], music: music), to: out)
        XCTAssertEqual(result.loops, 0)
        let length = try await duration(of: out)
        XCTAssertEqual(length, 2, accuracy: 0.2)
    }

    func testWithoutMusicTheNarrationComesThroughUnchangedInLength() async throws {
        let voice = try makeTestTone(seconds: 2, in: dir)
        let out = dir.appendingPathComponent("mix.m4a")
        try await Mixdown.render(Mixdown.Input(voice: voice, speech: [], music: nil), to: out)
        let length = try await duration(of: out)
        XCTAssertEqual(length, 2, accuracy: 0.2)
    }

    func testAnEffectIsPlacedWithinTheMix() async throws {
        let voice = try makeTestTone(seconds: 3, in: dir)
        let hit = try makeTestTone(seconds: 0.5, hz: 880, in: dir)
        let out = dir.appendingPathComponent("mix.m4a")
        try await Mixdown.render(
            Mixdown.Input(voice: voice, speech: [], music: nil, effects: [Mixdown.Effect(url: hit, at: 1, gainDB: -6)]), to: out)
        let length = try await duration(of: out)
        XCTAssertEqual(length, 3, accuracy: 0.2)
    }

    func testUnreadableMusicThrowsInsteadOfProducingSilence() async throws {
        let voice = try makeTestTone(seconds: 1, in: dir)
        let bogus = dir.appendingPathComponent("bogus.wav")
        try Data("nope".utf8).write(to: bogus)
        do {
            try await Mixdown.render(Mixdown.Input(voice: voice, speech: [], music: bogus), to: dir.appendingPathComponent("m.m4a"))
            XCTFail("an unreadable track must surface as an error")
        } catch {
            // expected: the Render phase catches this and renders with narration only
        }
    }
}

final class ChannelMusicDecodingTests: XCTestCase {
    func testAChannelSavedBeforeMusicExistedKeepsNoTrackAndTheDefaultLevel() throws {
        let channel = try JSONDecoder().decode(Channel.self, from: Data(#"{"name":"Old channel"}"#.utf8))
        XCTAssertNil(channel.musicTrackID)
        XCTAssertEqual(channel.musicGainDB, SoundMix.defaultGainDB)
    }

    func testTheMusicChoiceSurvivesARoundTrip() throws {
        var channel = Channel(name: "Shorts")
        channel.musicTrackID = "abc"
        channel.musicGainDB = -24
        let back = try JSONDecoder().decode(Channel.self, from: JSONEncoder().encode(channel))
        XCTAssertEqual(back.musicTrackID, "abc")
        XCTAssertEqual(back.musicGainDB, -24)
    }
}

@MainActor
final class StoreMusicTests: XCTestCase {
    func testRemovingATrackClearsItFromEveryChannelThatUsedIt() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("storemusic-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let store = Store()
        let tone = try makeTestTone(seconds: 1, in: dir)
        let item = try await store.audioLibrary.importFile(tone, kind: .music, name: "Test bed")

        var using = Channel(name: "Uses music")
        using.musicTrackID = item.id
        var other = Channel(name: "Other")
        other.musicTrackID = "someone-elses-track"
        store.channels.append(contentsOf: [using, other])
        defer { store.channels.removeAll { $0.id == using.id || $0.id == other.id } }

        store.removeAudioItem(item.id)

        XCTAssertNil(store.audioLibrary.item(item.id))
        XCTAssertNil(store.channels.first { $0.id == using.id }?.musicTrackID)
        XCTAssertEqual(store.channels.first { $0.id == other.id }?.musicTrackID, "someone-elses-track",
                       "only channels that used the removed track are touched")
    }
}

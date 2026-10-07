import XCTest
@testable import AVGStudio

/// The assistant's estimate, built on `CostTable` (the one rate table). It
/// decides whether a video may start without a tap, so "free" must mean
/// exactly zero and a missing rate must never read as free.
final class CostRatesTests: XCTestCase {

    private func settings(text: TextProviderKind = .apple, image: ImageProviderKind = .pollinations,
                          voice: VoiceProviderKind = .apple) -> AppSettings {
        var s = AppSettings()
        s.text = text; s.image = image; s.voice = voice
        return s
    }

    private func channel(seconds: Int) -> Channel {
        var c = Channel(name: "Test")
        c.targetSeconds = seconds
        return c
    }

    func testBreakdownReadsTheTablesPhaseLineNames() {
        // CostBreakdown looks the four phases up by these names. If CostTable
        // renames one, a phase would silently price as $0 - so pin them.
        let e = CostTable.estimate(targetSeconds: 60, settings: settings(), channel: channel(seconds: 60),
                                   hasTranscriptionKey: true, poolSize: 0)
        XCTAssertEqual(e.lines.map { $0.phase }, ["Writing", "Images", "Voice", "Captions"])
    }

    func testBreakdownTotalMatchesTheTablesOwnEstimate() {
        let s = settings(text: .groq, image: .gemini, voice: .elevenlabs)
        let c = channel(seconds: 90)
        let table = CostTable.estimate(targetSeconds: 90, settings: s, channel: c, hasTranscriptionKey: true, poolSize: 3).total
        let mine = CostBreakdown.forVideo(settings: s, channel: c, hasAssemblyKey: true, poolSize: 3).total(from: .writing)
        XCTAssertEqual(mine, table, accuracy: 1e-12, "the assistant and the Create screen must quote the same number")
    }

    func testDefaultsAreFree() {
        let e = CostEstimate.forNewVideo(settings: settings(), channel: channel(seconds: 60), hasAssemblyKey: false)
        XCTAssertTrue(e.isFree)
        XCTAssertEqual(e.summary, "Free")
    }

    func testElevenLabsMakesItPaid() {
        let b = CostBreakdown.forVideo(settings: settings(voice: .elevenlabs), channel: channel(seconds: 60), hasAssemblyKey: false)
        XCTAssertEqual(b.voice, 60 * 15 * CostTable.voiceRate(.elevenlabs), accuracy: 1e-9)
        XCTAssertFalse(CostEstimate.forNewVideo(settings: settings(voice: .elevenlabs), channel: channel(seconds: 60),
                                                hasAssemblyKey: false).isFree)
    }

    func testTextCostGrowsWithLength() {
        let s = settings(text: .groq)
        let a = CostBreakdown.forVideo(settings: s, channel: channel(seconds: 60), hasAssemblyKey: false).text
        let b = CostBreakdown.forVideo(settings: s, channel: channel(seconds: 120), hasAssemblyKey: false).text
        XCTAssertGreaterThan(a, 0)
        XCTAssertGreaterThan(b, a)
    }

    func testSceneCountIsClamped() {
        let s = settings(image: .gemini)
        let per = CostTable.imageFlat(.gemini, quality: ImageQuality.forVideo(shortEdge: s.shortEdge))
        let short = CostBreakdown.forVideo(settings: s, channel: channel(seconds: 10), hasAssemblyKey: false).images
        let long = CostBreakdown.forVideo(settings: s, channel: channel(seconds: 600), hasAssemblyKey: false).images
        XCTAssertEqual(short, 5 * per, accuracy: 1e-9)
        XCTAssertEqual(long, 12 * per, accuracy: 1e-9)
    }

    func testRecycledImagesLowerTheImageCost() {
        let s = settings(image: .gemini)
        var c = channel(seconds: 60)
        c.recycleEnabled = true
        c.recycleRatio = 100
        let empty = CostBreakdown.forVideo(settings: s, channel: c, hasAssemblyKey: false, poolSize: 0).images
        let stocked = CostBreakdown.forVideo(settings: s, channel: c, hasAssemblyKey: false, poolSize: 20).images
        XCTAssertGreaterThan(empty, 0)
        XCTAssertEqual(stocked, 0, accuracy: 1e-12)
    }

    func testTranscriptionOnlyWithAKey() {
        let s = settings()
        let without = CostBreakdown.forVideo(settings: s, channel: channel(seconds: 60), hasAssemblyKey: false)
        let with = CostBreakdown.forVideo(settings: s, channel: channel(seconds: 60), hasAssemblyKey: true)
        XCTAssertEqual(without.transcription, 0)
        XCTAssertEqual(with.transcription, CostTable.transcriptionPerMinute, accuracy: 1e-9)
        XCTAssertFalse(CostEstimate(usd: with.total(from: .writing)).isFree)
    }

    func testSummaryReadsLikeTheCreateScreen() {
        XCTAssertEqual(CostEstimate(usd: 0.0312).summary, "about $0.03")
        XCTAssertEqual(CostEstimate(usd: 1.5).summary, "about $1.50")
        XCTAssertEqual(CostEstimate(usd: 0).summary, "Free")
    }

    func testPaidSubCentIsNotFree() {
        let tiny = CostEstimate(usd: 0.001)
        XCTAssertFalse(tiny.isFree)
        XCTAssertEqual(tiny.summary, "under $0.01", "sub-cent paid work must never read as free")
    }

    func testRedoFromRenderIsFree() {
        let s = settings(text: .groq, image: .gemini, voice: .elevenlabs)
        let e = CostEstimate.forRedo(from: .render, settings: s, channel: channel(seconds: 60), hasAssemblyKey: true)
        XCTAssertTrue(e.isFree)
    }

    func testRedoFromVoiceExcludesTextAndImages() {
        let s = settings(text: .groq, image: .gemini, voice: .elevenlabs)
        let c = channel(seconds: 60)
        let b = CostBreakdown.forVideo(settings: s, channel: c, hasAssemblyKey: false)
        let e = CostEstimate.forRedo(from: .voice, settings: s, channel: c, hasAssemblyKey: false)
        XCTAssertEqual(e.usd, b.voice, accuracy: 1e-12)
        XCTAssertLessThan(e.usd, CostEstimate.forNewVideo(settings: s, channel: c, hasAssemblyKey: false).usd)
    }

    func testRedoFromVisualsIsImagesPlusVoice() {
        let s = settings(text: .groq, image: .gemini, voice: .elevenlabs)
        let c = channel(seconds: 60)
        let b = CostBreakdown.forVideo(settings: s, channel: c, hasAssemblyKey: false)
        XCTAssertEqual(b.total(from: .visuals), b.images + b.voice, accuracy: 1e-12)
    }

    func testRedoFromWritingEqualsNewVideo() {
        let s = settings(text: .gemini, image: .openai, voice: .gemini)
        let c = channel(seconds: 90)
        XCTAssertEqual(CostEstimate.forRedo(from: .writing, settings: s, channel: c, hasAssemblyKey: true),
                       CostEstimate.forNewVideo(settings: s, channel: c, hasAssemblyKey: true))
    }

    func testZeroLengthChannelDoesNotCrashOrGoNegative() {
        let e = CostEstimate.forNewVideo(settings: settings(text: .groq), channel: channel(seconds: 0), hasAssemblyKey: false)
        XCTAssertGreaterThanOrEqual(e.usd, 0)
    }
}

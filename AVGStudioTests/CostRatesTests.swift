import XCTest
@testable import AVGStudio

/// The flat price table and the estimate built from it. The estimate decides
/// whether the assistant may start a video without a tap, so "free" must mean
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

    func testRatesMatchWhatPipelineHasAlwaysCharged() {
        XCTAssertEqual(CostRates.text(.gemini), 0.00000025)
        XCTAssertEqual(CostRates.text(.groq), 0.00000015)
        XCTAssertEqual(CostRates.image(.gemini), 0.02)
        XCTAssertEqual(CostRates.image(.openai), 0.04)
        XCTAssertEqual(CostRates.voice(.elevenlabs), 0.00003)
        XCTAssertEqual(CostRates.voice(.gemini), 0.00002)
        XCTAssertEqual(CostRates.transcriptionPerMinute, 0.01)
    }

    func testFreeProvidersHaveExactlyZeroRates() {
        XCTAssertEqual(CostRates.text(.apple), 0)
        XCTAssertEqual(CostRates.text(.pollinations), 0)
        XCTAssertEqual(CostRates.image(.pollinations), 0)
        XCTAssertEqual(CostRates.image(.stablehorde), 0)
        XCTAssertEqual(CostRates.voice(.apple), 0)
    }

    func testDefaultsAreFree() {
        let e = CostEstimate.forNewVideo(settings: settings(), channel: channel(seconds: 60), hasAssemblyKey: false)
        XCTAssertTrue(e.isFree)
        XCTAssertEqual(e.summary, "free")
    }

    func testElevenLabsMakesItPaid() {
        let b = CostBreakdown.forVideo(settings: settings(voice: .elevenlabs), channel: channel(seconds: 60),
                                       hasAssemblyKey: false)
        XCTAssertEqual(b.voice, 60 * 15 * 0.00003, accuracy: 60 * 15 * 0.00003 * 0.01)
        XCTAssertFalse(CostEstimate.forNewVideo(settings: settings(voice: .elevenlabs), channel: channel(seconds: 60),
                                                hasAssemblyKey: false).isFree)
    }

    func testTextCostScalesWithLength() {
        let s = settings(text: .groq)
        let a = CostBreakdown.forVideo(settings: s, channel: channel(seconds: 60), hasAssemblyKey: false).text
        let b = CostBreakdown.forVideo(settings: s, channel: channel(seconds: 120), hasAssemblyKey: false).text
        XCTAssertGreaterThan(a, 0)
        XCTAssertEqual(b, a * 2, accuracy: a * 0.001)
    }

    func testSceneCountIsClamped() {
        let s = settings(image: .gemini)
        let short = CostBreakdown.forVideo(settings: s, channel: channel(seconds: 10), hasAssemblyKey: false).images
        let long = CostBreakdown.forVideo(settings: s, channel: channel(seconds: 600), hasAssemblyKey: false).images
        XCTAssertEqual(short, 5 * 0.02, accuracy: 1e-9)
        XCTAssertEqual(long, 12 * 0.02, accuracy: 1e-9)
    }

    func testTranscriptionOnlyWithAKey() {
        let s = settings()
        let without = CostBreakdown.forVideo(settings: s, channel: channel(seconds: 60), hasAssemblyKey: false)
        let with = CostBreakdown.forVideo(settings: s, channel: channel(seconds: 60), hasAssemblyKey: true)
        XCTAssertEqual(without.transcription, 0)
        XCTAssertEqual(with.transcription, 0.01, accuracy: 1e-9)
        XCTAssertFalse(CostEstimate(usd: with.total(from: .writing)).isFree)
    }

    func testSummaryIsRounded() {
        XCTAssertEqual(CostEstimate(usd: 0.0312).summary, "≈ $0.03")
        XCTAssertEqual(CostEstimate(usd: 1.5).summary, "≈ $1.50")
        XCTAssertEqual(CostEstimate(usd: 0).summary, "free")
    }

    func testPaidSubCentIsNotFree() {
        let tiny = CostEstimate(usd: 0.001)
        XCTAssertFalse(tiny.isFree)
        XCTAssertEqual(tiny.summary, "< $0.01", "sub-cent paid work must never read as free")
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

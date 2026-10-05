import XCTest
import AVFoundation
@testable import AVGStudio

@MainActor
final class QualityOptionTests: XCTestCase {

    func testImageQualityFollowsTheVideoQuality() {
        XCTAssertEqual(ImageQuality.forVideo(shortEdge: 480), .low)
        XCTAssertEqual(ImageQuality.forVideo(shortEdge: 720), .medium)
        XCTAssertEqual(ImageQuality.forVideo(shortEdge: 1080), .high)
        XCTAssertEqual(ImageQuality.forVideo(shortEdge: 1440), .high)
        XCTAssertEqual(ImageQuality.high.openAI, "high")
    }

    func testPictureSizesScaleFromThePCsMediumSizeInMultiplesOf64() {
        let portrait = (768, 1344)
        XCTAssertTrue(ImageQuality.medium.scaled(portrait) == (768, 1344), "medium IS the PC's size")
        XCTAssertTrue(ImageQuality.high.scaled(portrait) == (1024, 1792))
        XCTAssertTrue(ImageQuality.low.scaled(portrait) == (576, 1024))
        XCTAssertTrue(ImageQuality.low.scaled((1024, 1024)) == (768, 768))
        let big = ImageQuality.high.scaled((1344, 768), cap: 1280)
        XCTAssertLessThanOrEqual(big.0, 1280)
        XCTAssertEqual(big.0 % 64, 0)
        XCTAssertGreaterThanOrEqual(ImageQuality.low.scaled((300, 300)).0, 256, "never absurdly small")
    }

    func testEncoderQualityAsksForAFixedBitrateOnlyWhenNotAutomatic() {
        XCTAssertNil(EncoderQuality.automatic.bitrate(width: 720, height: 1280, fps: 30, codec: .hevc))
        XCTAssertEqual(EncoderQuality.compact.bitrate(width: 720, height: 1280, fps: 30, codec: .hevc), 1_382_400)
        let hevc = EncoderQuality.high.bitrate(width: 1080, height: 1920, fps: 30, codec: .hevc)!
        let h264 = EncoderQuality.high.bitrate(width: 1080, height: 1920, fps: 30, codec: .h264)!
        XCTAssertEqual(Double(h264) / Double(hevc), 1.6, accuracy: 0.001, "H.264 needs more bits for the same look")
        XCTAssertGreaterThan(EncoderQuality.balanced.bitsPerPixel(codec: .hevc) ?? 0, EncoderQuality.compact.bitsPerPixel(codec: .hevc) ?? 0)
    }

    func testOpenAIImageCostsFollowTheQualityTier() {
        let low = CostTable.imageFlat(.openai, quality: .low)
        let medium = CostTable.imageFlat(.openai, quality: .medium)
        let high = CostTable.imageFlat(.openai, quality: .high)
        XCTAssertLessThan(low, medium)
        XCTAssertLessThan(medium, high)
        XCTAssertEqual(medium, 0.04, "the figure this table has always used")
        XCTAssertEqual(CostTable.imageFlat(.gemini, quality: .high), CostTable.imageFlat(.gemini, quality: .low), "Gemini has no quality tiers")
    }
}

@MainActor
final class VoiceOptionTests: XCTestCase {
    func testElevenLabsOnlySendsSpeedWhenItIsNotTheDefault() {
        let normal = ElevenLabsVoice.voiceSettings(stability: 0.5, speed: 1.0)
        XCTAssertEqual(normal["stability"] as? Double, 0.5)
        XCTAssertNil(normal["speed"], "a default channel makes the request it always did")
        let quick = ElevenLabsVoice.voiceSettings(stability: 0.8, speed: 1.1)
        XCTAssertEqual(quick["speed"] as? Double, 1.1)
        let clamped = ElevenLabsVoice.voiceSettings(stability: 5, speed: 9)
        XCTAssertEqual(clamped["stability"] as? Double, 1)
        XCTAssertEqual(clamped["speed"] as? Double, 1.2)
        XCTAssertEqual(ElevenLabsVoice.voiceSettings(stability: -1, speed: 0.1)["speed"] as? Double, 0.7)
    }

    func testAppleSpeechRateScalesTheDefaultWithinTheLegalRange() {
        XCTAssertEqual(AppleVoice.utteranceRate(speed: 1.0), AVSpeechUtteranceDefaultSpeechRate)
        XCTAssertGreaterThan(AppleVoice.utteranceRate(speed: 1.2), AppleVoice.utteranceRate(speed: 1.0))
        XCTAssertLessThan(AppleVoice.utteranceRate(speed: 0.7), AppleVoice.utteranceRate(speed: 1.0))
        for speed in [0.0, 0.7, 1.0, 1.2, 5.0] {
            let r = AppleVoice.utteranceRate(speed: speed)
            XCTAssertGreaterThanOrEqual(r, AVSpeechUtteranceMinimumSpeechRate)
            XCTAssertLessThanOrEqual(r, AVSpeechUtteranceMaximumSpeechRate)
        }
    }
}

@MainActor
final class PerformanceOptionTests: XCTestCase {
    func testCoolDownWaitsForTheDeviceThenSaysSo() {
        var calls = 0
        var messages: [String] = []
        var slept = 0.0
        VideoRenderer.coolDownIfNeeded(cancel: CancelFlag(), status: { messages.append($0) },
                                       state: { calls += 1; return calls <= 3 ? .serious : .nominal },
                                       sleep: { slept += $0 }, limit: 60)
        XCTAssertEqual(slept, 3, accuracy: 1e-9)
        XCTAssertEqual(messages, ["Letting the iPhone cool down…", "Rendering frames…"])
    }

    func testCoolDownDoesNothingWhenTheDeviceIsFine() {
        var messages: [String] = []
        VideoRenderer.coolDownIfNeeded(cancel: CancelFlag(), status: { messages.append($0) }, state: { .fair }, sleep: { _ in XCTFail("must not sleep") })
        XCTAssertTrue(messages.isEmpty)
    }

    func testCoolDownGivesUpAfterTheLimitAndStopsWhenCancelled() {
        var slept = 0.0
        VideoRenderer.coolDownIfNeeded(cancel: CancelFlag(), status: { _ in }, state: { .critical }, sleep: { slept += $0 }, limit: 5)
        XCTAssertEqual(slept, 5, accuracy: 1e-9, "slow beats never finishing")
        let cancel = CancelFlag(); cancel.raise()
        VideoRenderer.coolDownIfNeeded(cancel: cancel, status: { _ in }, state: { .critical }, sleep: { _ in XCTFail("cancelled") })
    }

    func testNewSettingsDefaultToTheOldBehaviour() throws {
        let s = try JSONDecoder().decode(AppSettings.self, from: Data("{}".utf8))
        XCTAssertEqual(s.renderEngine, .auto)
        XCTAssertEqual(s.videoCodec, .hevc)
        XCTAssertEqual(s.encoderQuality, .automatic)
        XCTAssertEqual(s.imageParallelism, 3, "the batch size that used to be fixed")
        XCTAssertTrue(s.pauseWhenHot)
        XCTAssertTrue(s.keepScreenAwake)
        XCTAssertTrue(s.allowBackgroundRendering)
    }
}

@MainActor
final class OverlayOptionTests: XCTestCase {
    func testDefaultsReproduceTheLookTheAppAlwaysHad() {
        let o = OverlayStyle()
        XCTAssertEqual(o.captionBackground, .none)
        XCTAssertEqual(o.captionOutline, 5)
        XCTAssertTrue(o.captionShadow)
        XCTAssertTrue(o.captionUppercase, "captions were always capitals")
        XCTAssertEqual(o.cardBackground, .dark)
        XCTAssertEqual(o.cardAlignment, .center)
        XCTAssertEqual(o.cardTextScale, 1)
        XCTAssertFalse(o.cardUppercase)
        XCTAssertTrue(o.cardAccentBars)
        XCTAssertEqual(o.cardAnimation, .none)
    }

    func testOlderChannelsAndPartialStylesGainDefaults() throws {
        let old = try JSONDecoder().decode(Channel.self, from: Data(#"{"name":"Old"}"#.utf8))
        XCTAssertEqual(old.overlay, OverlayStyle())
        XCTAssertEqual(old.voiceSpeed, 1.0)
        XCTAssertEqual(old.voiceStability, 0.5)
        XCTAssertFalse(old.chaptersEnabled)
        let partial = try JSONDecoder().decode(OverlayStyle.self, from: Data(#"{"captionBackground":"pill","cardTextScale":1.2}"#.utf8))
        XCTAssertEqual(partial.captionBackground, .pill)
        XCTAssertEqual(partial.cardTextScale, 1.2)
        XCTAssertEqual(partial.captionOutline, 5, "everything not mentioned keeps its default")
    }

    func testChannelRoundTripKeepsOverlayVoiceAndChapters() throws {
        var c = Channel(name: "c")
        c.chaptersEnabled = true; c.voiceSpeed = 1.1; c.voiceStability = 0.7
        c.overlay.cardBackground = .light; c.overlay.captionOutline = 0; c.overlay.captionTextColorHex = "FFD400"
        let back = try JSONDecoder().decode(Channel.self, from: JSONEncoder().encode(c))
        XCTAssertTrue(back.chaptersEnabled)
        XCTAssertEqual(back.voiceSpeed, 1.1)
        XCTAssertEqual(back.overlay.cardBackground, .light)
        XCTAssertEqual(back.overlay.captionOutline, 0)
        XCTAssertEqual(back.overlay.captionTextColorHex, "FFD400")
    }

    func testCardEntranceMotion() {
        var style = OverlayStyle()
        let card = TakeoverCard(scene: 1, text: "Why?", start: 10, end: 12)
        let still = VideoRenderer.cardMotion(card, t: 10, style: style)
        XCTAssertEqual(still.alpha, 1, "no animation by default")
        XCTAssertEqual(still.scale, 1)
        style.cardAnimation = .fade
        XCTAssertEqual(VideoRenderer.cardMotion(card, t: 10.09, style: style).alpha, 0.5, accuracy: 1e-6)
        XCTAssertEqual(VideoRenderer.cardMotion(card, t: 11, style: style).alpha, 1)
        style.cardAnimation = .pop
        XCTAssertEqual(VideoRenderer.cardMotion(card, t: 10, style: style).scale, 0.94, accuracy: 1e-6)
        XCTAssertEqual(VideoRenderer.cardMotion(card, t: 11, style: style).scale, 1, accuracy: 1e-6)
        XCTAssertEqual(VideoRenderer.cardMotion(card, t: 9, style: style).alpha, 0, "before the card starts it is invisible")
    }

    func testCardPaletteFollowsTheBackgroundChoice() {
        var style = OverlayStyle()
        var white: CGFloat = 0, alpha: CGFloat = 0
        let accent = UIColor(hue: 0.6, saturation: 0.8, brightness: 1, alpha: 1)
        style.cardBackground = .dark
        XCTAssertTrue(VideoRenderer.cardPalette(style, accent1: accent).text.getWhite(&white, alpha: &alpha))
        XCTAssertGreaterThan(white, 0.9, "light text on dark")
        style.cardBackground = .light
        _ = VideoRenderer.cardPalette(style, accent1: accent).text.getWhite(&white, alpha: &alpha)
        XCTAssertLessThan(white, 0.2, "dark text on light")
        style.cardBackground = .brand
        var h: CGFloat = 0, s: CGFloat = 0, b: CGFloat = 0
        VideoRenderer.cardPalette(style, accent1: accent).background.getHue(&h, saturation: &s, brightness: &b, alpha: &alpha)
        XCTAssertEqual(h, 0.6, accuracy: 0.01, "the brand colour's hue, deeply darkened")
        XCTAssertEqual(b, 0.16, accuracy: 0.01)
    }
}

final class OptionInvalidationTests: XCTestCase {
    private func inputs(_ phase: PhaseID, _ s: AppSettings = AppSettings(), _ ch: Channel = Channel(name: "c")) -> [String: String] {
        PhaseInputs.describe(phase, settings: s, channel: ch)
    }

    func testTurningChaptersOnReRunsFromWriting() {
        var on = Channel(name: "c"); on.chaptersEnabled = true
        for phase in [PhaseID.writing, .voice, .captions] {
            XCTAssertNotEqual(inputs(phase), inputs(phase, AppSettings(), on), "\(phase)")
        }
        for phase in [PhaseID.visuals, .render] {
            XCTAssertEqual(inputs(phase), inputs(phase, AppSettings(), on), "\(phase)")
        }
    }

    func testOverlayAndFormatChangesOnlyTouchRender() {
        var ch = Channel(name: "c"); ch.overlay.cardBackground = .light
        var s = AppSettings(); s.videoCodec = .h264
        for phase in [PhaseID.writing, .visuals, .voice, .captions] {
            XCTAssertEqual(inputs(phase), inputs(phase, s, ch))
        }
        XCTAssertNotEqual(inputs(.render), inputs(.render, AppSettings(), ch))
        XCTAssertNotEqual(inputs(.render), inputs(.render, s))
        var q = AppSettings(); q.encoderQuality = .high
        XCTAssertNotEqual(inputs(.render), inputs(.render, q))
        var perf = AppSettings(); perf.imageParallelism = 1; perf.pauseWhenHot = false; perf.renderEngine = .cpu
        XCTAssertEqual(inputs(.render), inputs(.render, perf), "speed options never change the video, so never make it stale")
    }

    func testImageQualityOnlyStalesPicturesWhenTheTierChanges() {
        var p720 = AppSettings(); p720.image = .pollinations; p720.shortEdge = 720
        var p1080 = p720; p1080.shortEdge = 1080
        var p1440 = p720; p1440.shortEdge = 1440
        XCTAssertNotEqual(inputs(.visuals, p720), inputs(.visuals, p1080))
        XCTAssertEqual(inputs(.visuals, p1080), inputs(.visuals, p1440), "same tier, same pictures")
        var g720 = p720; g720.image = .gemini
        var g1080 = g720; g1080.shortEdge = 1080
        XCTAssertEqual(inputs(.visuals, g720), inputs(.visuals, g1080), "Gemini has no quality control")
    }

    func testVoiceSettingsOnlyCountWhereTheVoiceHonoursThem() {
        var ch = Channel(name: "c"); ch.voiceStability = 0.9; ch.voiceSpeed = 1.1
        var eleven = AppSettings(); eleven.voice = .elevenlabs
        XCTAssertNotEqual(inputs(.voice, eleven), inputs(.voice, eleven, ch))
        var apple = AppSettings(); apple.voice = .apple
        let base = inputs(.voice, apple), changed = inputs(.voice, apple, ch)
        XCTAssertEqual(changed["voiceStability"], "", "Apple voices have no stability")
        XCTAssertNotEqual(base["voiceSpeed"], changed["voiceSpeed"])
        var gemini = AppSettings(); gemini.voice = .gemini
        XCTAssertEqual(inputs(.voice, gemini), inputs(.voice, gemini, ch), "Gemini takes neither")
    }

    func testSnapshotKeepsTheTimelineAndChapters() throws {
        var p = Project(id: "p", title: "T", topic: "t")
        p.audioSegments = [AudioSegment(start: 0, end: 2), AudioSegment(start: 3.8, end: 6, gapBefore: 1.8, gapKind: "chapter")]
        p.chapters = [Chapter(beforeScene: 3, title: "Deep", subtitle: "Down we go", duration: 1.8)]
        let snap = TakeSnapshot.capture(p)
        var other = Project(id: "p", title: "T", topic: "t")
        snap.apply(to: &other)
        XCTAssertEqual(other.audioSegments, p.audioSegments)
        XCTAssertEqual(other.chapters, p.chapters)
        let back = try JSONDecoder().decode(TakeSnapshot.self, from: JSONEncoder().encode(snap))
        XCTAssertEqual(back.chapters?.first?.title, "Deep")
    }
}

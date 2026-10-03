import XCTest
@testable import AVGStudio

/// The creative engine's pure parts: presets, option catalogue, prompt
/// builders, the scene-block parser and the story map. Fixtures are inline so
/// these do not depend on the bundled files - except the two tests that check
/// the bundled files themselves actually parse.
@MainActor
final class CreativeTests: XCTestCase {

    private let presetFile = """
    \u{FEFF}## PRESET: documentary
    ## DESCRIPTION: Authoritative cinematic documentary narration
    You are a seasoned documentary narrator.

    TONE: Measured.


    ## PRESET: News_Report
    ## DESCRIPTION: Neutral broadcast news
    You are a professional broadcast news reporter.

    ## PRESET: empty_one
    """

    func testPresetParsing() {
        let presets = Personality.parse(presetFile)
        XCTAssertEqual(presets.map(\.name), ["documentary", "news_report"], "names are lowercased; a preset with no body is dropped")
        XCTAssertEqual(presets[0].description, "Authoritative cinematic documentary narration")
        XCTAssertTrue(presets[0].body.hasPrefix("You are a seasoned documentary narrator."))
        XCTAssertTrue(presets[0].body.contains("TONE: Measured."))
        XCTAssertFalse(presets[0].body.contains("DESCRIPTION"), "the description line is not part of the instructions")
    }

    func testPresetDegradationLadder() {
        let presets = Personality.parse(presetFile)
        XCTAssertEqual(Personality.preset(named: "NEWS_REPORT", in: presets).name, "news_report")
        XCTAssertEqual(Personality.preset(named: "renamed_away", in: presets).name, "documentary", "unknown -> FIRST preset, never an empty persona")
        XCTAssertEqual(Personality.preset(named: "", in: presets).name, "documentary")
        XCTAssertEqual(Personality.preset(named: "x", in: []).name, "generic", "no catalogue -> built-in generic")
        XCTAssertFalse(Personality.generic.body.isEmpty)
    }

    func testBundledFilesParse() {
        XCTAssertGreaterThanOrEqual(Personality.all.count, 3, "personality.txt should bundle with the app")
        XCTAssertTrue(Personality.all.contains { $0.name == "documentary" })
        XCTAssertEqual(OptionCatalog.options("video_tone").count, 9)
        XCTAssertEqual(OptionCatalog.options("brand_presets").count, 10)
    }

    private let catalogFile = """
    ## CATEGORY: video_tone
    ## OPTION: realistic | Realistic / Photographic | true-to-life photographic imagery
    ## OPTION: custom | Custom... | type your own

    ## CATEGORY: brand_presets
    ## OPTION: neon_cyber | Neon Cyber | bg=#050014;accent1=#00F0FF;accent2=#FF00AA;highlight=#F9FF00;motion=very fast
    ## OPTION: plain | Plain
    """

    func testCatalogParsingAndPayload() {
        let catalog = OptionCatalog.parse(catalogFile)
        XCTAssertEqual(catalog["video_tone"]?.map(\.id), ["realistic", "custom"])
        let neon = catalog["brand_presets"]?.first
        XCTAssertEqual(neon?.payload["accent1"], "#00F0FF")
        XCTAssertEqual(neon?.payload["motion"], "very fast")
        XCTAssertEqual(catalog["brand_presets"]?.last?.hint, "")
        XCTAssertEqual(catalog["video_tone"]?.first?.payload, [:], "prose hints have no payload")
        XCTAssertEqual(OptionCatalog.payload("no equals here; a=1"), ["a": "1"])
    }

    func testVisualToneResolution() {
        let catalog = OptionCatalog.parse(catalogFile)
        XCTAssertEqual(Brand.resolveVisualTone("realistic", catalog: catalog), "Realistic / Photographic — true-to-life photographic imagery")
        XCTAssertEqual(Brand.resolveVisualTone("custom", catalog: catalog), "Custom... — type your own")
        XCTAssertEqual(Brand.resolveVisualTone("a moody noir look", catalog: catalog), "a moody noir look", "free text is already descriptive")
        XCTAssertEqual(Brand.resolveVisualTone("renamed_tone", catalog: catalog), "renamed_tone", "unknown id degrades, never blanks")
        XCTAssertEqual(Brand.resolveVisualTone("  ", catalog: catalog), "")
    }

    func testBrandStyleBibleAndAccents() {
        let catalog = OptionCatalog.parse(catalogFile)
        XCTAssertEqual(Brand.label("neon_cyber", catalog: catalog), "Neon Cyber")
        XCTAssertEqual(Brand.label("", catalog: catalog), "")
        XCTAssertEqual(Brand.label("mystery", catalog: catalog), "mystery")
        let neon = Brand.styleBible("neon_cyber", catalog: catalog)
        XCTAssertEqual(neon.motion, "very fast", "a catalogue payload overrides the built-in default")
        XCTAssertEqual(neon.transitions, "cut, whip, slide_left", "keys the payload doesn't carry keep the default")
        XCTAssertEqual(Brand.styleBible("nothing", catalog: catalog).grade, "none")
        XCTAssertEqual(Brand.accentHex("neon_cyber", "accent2", catalog: catalog), "#FF00AA")
        XCTAssertNil(Brand.accentHex("neon_cyber", "bg-missing", catalog: catalog))
        XCTAssertNil(Brand.accentHex("plain", "accent1", catalog: catalog))
    }

    private var inputs: CreativeInputs {
        CreativeInputs(topic: "Why octopuses have three hearts", contentShape: "documentary",
                       visualTone: "a moody noir look", styleLock: "ink wash style",
                       brandPreset: "", formLabel: "Short-form (9:16)", aspectRatio: "9:16", durationSeconds: 60)
    }
    private let personalities = [PersonalityPreset(name: "documentary", description: "Doc voice", body: "BODY-OF-DOC")]

    func testDurationPhrase() {
        XCTAssertEqual(CreativePrompts.durationPhrase(30), "30 seconds")
        XCTAssertEqual(CreativePrompts.durationPhrase(60), "1 minute")
        XCTAssertEqual(CreativePrompts.durationPhrase(90), "2 minute", "the PC only pluralises from 120s, so 90s reads \"2 minute\"")
        XCTAssertEqual(CreativePrompts.durationPhrase(150), "2 minutes", "round half to even, like Python's round()")
        XCTAssertEqual(CreativePrompts.durationPhrase(120), "2 minutes")
        XCTAssertEqual(CreativePrompts.durationPhrase(240), "4 minutes")
    }

    func testWordBudgetScalesWithLength() {
        XCTAssertEqual(CreativePrompts.wordBudget(seconds: 30).min, 60)
        XCTAssertEqual(CreativePrompts.wordBudget(seconds: 30).max, 75, "30s reproduces the PC's historical 60-75 words")
        XCTAssertEqual(CreativePrompts.wordBudget(seconds: 240).max, 600)
    }

    func testBriefPromptCarriesPersonalityAndTone() {
        let p = CreativePrompts.brief(inputs, personalities: personalities)
        XCTAssertTrue(p.contains("\"Why octopuses have three hearts\""))
        XCTAssertTrue(p.contains("Content personality: Doc voice"))
        XCTAssertTrue(p.contains("Visual tone: a moody noir look"))
        XCTAssertTrue(p.contains("1 minute video"))
        XCTAssertTrue(p.contains("\"emotional_arc\""))
    }

    func testNarrationPromptOpensWithThePersonalityAndAsksForQuestionMarkersOnly() {
        let p = CreativePrompts.narration(inputs, extraContext: "CTX", personalities: personalities)
        XCTAssertTrue(p.hasPrefix("BODY-OF-DOC"))
        XCTAssertTrue(p.contains("Total length: 120 to 150 words."))
        XCTAssertTrue(p.contains("CTX"))
        XCTAssertTrue(p.contains("Tone & style: a moody noir look"))
        XCTAssertTrue(p.contains("((But why?))"))
        XCTAssertTrue(p.contains("DYNAMIC PAUSES"))
        XCTAssertFalse(p.contains("[["), "focus markers aren't requested: nothing on iOS reads them")
        XCTAssertFalse(CreativePrompts.narration(inputs, pauseFrequency: nil, personalities: personalities).contains("DYNAMIC PAUSES"))
    }

    func testSegmentationPromptWithoutAPool() {
        let p = CreativePrompts.segmentation(inputs, narration: "One. Two.")
        XCTAssertTrue(p.contains("Append this style tag verbatim at the end: ink wash style"))
        XCTAssertTrue(p.contains("Visual tone: a moody noir look"))
        XCTAssertTrue(p.contains("between 3 and\n15 scenes"))
        XCTAssertTrue(p.contains("@@SCENE@@"))
        XCTAssertFalse(p.contains("RECYCLE"))
        XCTAssertTrue(p.hasSuffix("One. Two."))
        var noLock = inputs; noLock.styleLock = ""
        XCTAssertFalse(CreativePrompts.segmentation(noLock, narration: "x").contains("style tag"))
    }

    func testSegmentationPromptPoolModes() {
        let pool = [CreativePrompts.PoolEntry(index: 1, description: "a sunset"), CreativePrompts.PoolEntry(index: 2, description: "a city")]
        let partial = CreativePrompts.segmentation(inputs, narration: "x", pool: pool, recycleRatio: 40)
        XCTAssertTrue(partial.contains("#1: a sunset"))
        XCTAssertTrue(partial.contains("at most 40% of the scenes"))
        XCTAssertTrue(partial.contains("<the image prompt, or [RECYCLE #k]>"))
        let all = CreativePrompts.segmentation(inputs, narration: "x", pool: pool, recycleRatio: 100)
        XCTAssertTrue(all.contains("ALWAYS the token [RECYCLE #k]"))
        XCTAssertFalse(all.contains("at most"))
        let off = CreativePrompts.segmentation(inputs, narration: "x", pool: pool, recycleRatio: 0)
        XCTAssertFalse(off.contains("POOL"))
    }

    func testSceneBlockParsing() {
        let raw = """
        Here are the scenes:
        @@SCENE@@
        An octopus has
        three hearts.
        @@IMAGE@@
        An octopus on a reef.
        ink wash style
        @@LABEL@@
        Hook
        @@SCENE@@
        no image marker here
        @@SCENE@@
        Two work the gills. [pause] One the body.
        @@IMAGE@@
        [RECYCLE #2]
        @@LABEL@@
        """
        let scenes = CreativePrompts.parseSceneBlocks(raw)
        XCTAssertEqual(scenes.count, 2, "a block with no @@IMAGE@@ is skipped")
        XCTAssertEqual(scenes[0].text, "An octopus has three hearts.")
        XCTAssertEqual(scenes[0].imagePrompt, "An octopus on a reef. ink wash style")
        XCTAssertEqual(scenes[0].label, "Hook")
        XCTAssertEqual(scenes[1].imagePrompt, "[RECYCLE #2]")
        XCTAssertEqual(scenes[1].label, "Scene", "a missing label degrades")
        XCTAssertTrue(CreativePrompts.parseSceneBlocks("no markers at all").isEmpty)
    }

    func testSceneBlockParsingUnwrapsAFence() {
        let raw = "```\n@@SCENE@@\nHello there.\n@@IMAGE@@\nA hello.\n@@LABEL@@\nHook\n```"
        XCTAssertEqual(CreativePrompts.parseSceneBlocks(raw).map(\.text), ["Hello there."])
    }

    func testBriefParsingRequiresAnArc() {
        let ok = CreativePrompts.parseBrief("```json\n{\"emotional_arc\":\"hook to payoff\",\"tone\":\"warm\",\"angle\":\"x\",\"notes\":\"n\"}\n```")
        XCTAssertEqual(ok?.emotionalArc, "hook to payoff")
        XCTAssertEqual(ok?.tone, "warm")
        XCTAssertNil(CreativePrompts.parseBrief("{\"tone\":\"warm\"}"), "no emotional_arc is a failed generation")
        XCTAssertNil(CreativePrompts.parseBrief("plain prose"))
        let block = ok?.directionBlock ?? ""
        XCTAssertTrue(block.hasPrefix("CREATIVE DIRECTION"))
        XCTAssertTrue(block.contains("Emotional arc: hook to payoff"))
        XCTAssertEqual(ok?.displayText, "Emotional arc: hook to payoff\nTone: warm\nAngle: x")
    }

    func testStyleBibleIncludesBrandFeel() {
        var i = inputs; i.brandPreset = "neon_cyber"
        let bible = CreativePrompts.styleBible(i, emotionalArc: "rise and fall", personalities: personalities)
        XCTAssertTrue(bible.contains("- personality: Doc voice"))
        XCTAssertTrue(bible.contains("- emotional arc: rise and fall"))
        XCTAssertTrue(bible.contains("- brand motion feel: punchy fast pushes"))
        XCTAssertFalse(CreativePrompts.styleBible(inputs, personalities: personalities).contains("brand:"))
    }

    // MARK: Story map

    func testStoryMapNormalisesEverything() {
        let raw = """
        {"image_count": "99999", "notes": " ok ",
         "scenes": [
           {"scene": 1, "role": "HOOK", "beat": "hit", "visual_intent": "pop"},
           {"scene": "2", "role": "mystery", "beat": "WOBBLE", "visual_intent": "??"},
           {"scene": 0, "role": "hook"}, "junk"],
         "zoom_words": [{"scene": 1, "word": " never "}, {"scene": 2, "word": ""}]}
        """
        let map = StoryMap.parse(raw, sceneCount: 4)
        XCTAssertEqual(map.imageCount, StoryMap.maxImages, "clamped to the upper bound")
        XCTAssertEqual(map.scenes.count, 2, "scene 0 and a non-object entry are dropped")
        XCTAssertEqual(map.scenes[0], StoryMapScene(scene: 1, role: "hook", beat: "HIT", visualIntent: "POP"))
        XCTAssertEqual(map.scenes[1], StoryMapScene(scene: 2, role: "", beat: "none", visualIntent: "BACKGROUND"))
        XCTAssertEqual(map.zoomWords, [StoryMapZoomWord(scene: 1, word: "never")])
        XCTAssertEqual(map.notes, "ok")
    }

    func testStoryMapLowerBoundNeverInflatesAShortScript() {
        let map = StoryMap.parse("{\"image_count\": 1, \"scenes\": [{\"scene\": 1}]}", sceneCount: 2)
        XCTAssertEqual(map.imageCount, 2, "min(3, sceneCount): a 2-scene script is not pushed to 3")
    }

    func testStoryMapDegradesToNeutral() {
        let a = StoryMap.parse("not json", sceneCount: 3)
        XCTAssertEqual(a, StoryMap.neutral(sceneCount: 3))
        XCTAssertEqual(a.scenes.count, 3)
        XCTAssertEqual(StoryMap.parse("{\"image_count\": 5, \"scenes\": []}", sceneCount: 2).scenes.count, 2)
        XCTAssertEqual(StoryMap.neutral(sceneCount: 2).directorHints, "", "a neutral map says nothing to Director II")
    }

    func testDirectorHintsListOnlyInformativeScenes() {
        let map = StoryMap(imageCount: 3, scenes: [
            StoryMapScene(scene: 2, role: "reveal", beat: "HIT", visualIntent: "POP"),
            StoryMapScene(scene: 1, role: "hook", beat: "none", visualIntent: "BACKGROUND"),
            StoryMapScene(scene: 3, role: "", beat: "none", visualIntent: "BACKGROUND"),
        ], zoomWords: [], notes: "")
        XCTAssertEqual(map.directorHints, "[1] role=hook\n[2] role=reveal beat=HIT intent=POP")
    }

    // MARK: Pipeline helpers

    func testStyleLockIsAppendedOnceAndSkipsRecycledScenes() {
        var scenes = [
            VideoScene(index: 1, text: "a", imagePrompt: "An octopus on a reef."),
            VideoScene(index: 2, text: "b", imagePrompt: "A city, ink wash style"),
            VideoScene(index: 3, text: "c", imagePrompt: "[RECYCLE #2]"),
            VideoScene(index: 4, text: "d", imagePrompt: ""),
            VideoScene(index: 5, text: "e", imagePrompt: "A tide pool"),
        ]
        Pipeline.applyStyleLock(&scenes, lock: " ink wash style ")
        XCTAssertEqual(scenes[0].imagePrompt, "An octopus on a reef. ink wash style")
        XCTAssertEqual(scenes[1].imagePrompt, "A city, ink wash style", "already ends with the tag")
        XCTAssertEqual(scenes[2].imagePrompt, "[RECYCLE #2]")
        XCTAssertEqual(scenes[3].imagePrompt, "")
        XCTAssertEqual(scenes[4].imagePrompt, "A tide pool. ink wash style")
        var untouched = scenes
        Pipeline.applyStyleLock(&untouched, lock: "")
        XCTAssertEqual(untouched, scenes)
    }

    func testEmotionalArcIsReadBackFromTheStoredBrief() {
        XCTAssertEqual(Pipeline.emotionalArc(from: "Emotional arc: rise and fall\nTone: warm"), "rise and fall")
        XCTAssertEqual(Pipeline.emotionalArc(from: "free text brief"), "free text brief")
        XCTAssertEqual(Pipeline.emotionalArc(from: nil), "")
    }

    func testOlderChannelsGainDefaultsAndKeepTheirValues() throws {
        let old = #"{"name":"Old","niche":"space"}"#
        let c = try JSONDecoder().decode(Channel.self, from: Data(old.utf8))
        XCTAssertEqual(c.contentShape, "documentary")
        XCTAssertEqual(c.visualTone, "documentary_cinematic")
        XCTAssertEqual(c.brandPreset, "modern_dark")
        let kept = #"{"name":"New","styleLock":"ink wash","contentShape":"news_report"}"#
        let k = try JSONDecoder().decode(Channel.self, from: Data(kept.utf8))
        XCTAssertEqual(k.styleLock, "ink wash")
        XCTAssertEqual(k.contentShape, "news_report")
    }
}

import XCTest
@testable import AVGStudio

// Recipes: a saved look, sound and editing bundle that can be applied to any
// channel without changing who the channel is.

final class RecipeTests: XCTestCase {
    private func styledChannel() -> Channel {
        var ch = Channel(name: "Bold shorts", niche: "space facts")
        ch.language = "French"
        ch.tone = "punchy"
        ch.visualTone = "bold_graphic"
        ch.styleLock = "flat colours"
        ch.musicAuto = true
        ch.musicGainDB = -28
        ch.sfxEnabled = true
        ch.sfxMap = [SFXMoment.impact.rawValue: "kept-sound", SFXMoment.whoosh.rawValue: "deleted-sound"]
        ch.sfxGainDB = 2
        ch.editingMode = .auto
        ch.editLevel = .smart
        ch.reviewScript = true
        ch.targetSeconds = 45
        return ch
    }

    func testApplyingARecipeCopiesLookSoundAndEditingButNeverIdentity() {
        let recipe = Recipe(name: "Bold", capturing: styledChannel())
        var other = Channel(name: "Calm", niche: "sleep stories")
        other.language = "Arabic"
        other.imageProviderBackup = .gemini
        let id = other.id

        recipe.apply(to: &other, knownAudio: ["kept-sound", "deleted-sound"])

        XCTAssertEqual(other.visualTone, "bold_graphic")
        XCTAssertEqual(other.styleLock, "flat colours")
        XCTAssertEqual(other.tone, "punchy")
        XCTAssertTrue(other.musicAuto)
        XCTAssertEqual(other.musicGainDB, -28)
        XCTAssertTrue(other.sfxEnabled)
        XCTAssertEqual(other.editingMode, .auto)
        XCTAssertEqual(other.editLevel, .smart)
        XCTAssertTrue(other.reviewScript)
        XCTAssertEqual(other.targetSeconds, 45)

        XCTAssertEqual(other.id, id)
        XCTAssertEqual(other.name, "Calm")
        XCTAssertEqual(other.niche, "sleep stories")
        XCTAssertEqual(other.language, "Arabic", "who a channel is stays")
        XCTAssertEqual(other.imageProviderBackup, .gemini, "providers stay")
    }

    func testSoundsThatNoLongerExistAreDroppedWhenApplying() {
        var ch = styledChannel()
        ch.musicTrackID = "gone-track"
        let recipe = Recipe(name: "Bold", capturing: ch)
        var target = Channel(name: "T")
        recipe.apply(to: &target, knownAudio: ["kept-sound"])
        XCTAssertNil(target.musicTrackID)
        XCTAssertEqual(target.sfxMap, [SFXMoment.impact.rawValue: "kept-sound"])
    }

    func testARecipeSurvivesARoundTripAndAnOlderOneStillLoads() throws {
        let recipe = Recipe(name: "Bold", capturing: styledChannel())
        let back = try JSONDecoder().decode(Recipe.self, from: JSONEncoder().encode(recipe))
        XCTAssertEqual(back, recipe)

        let old = try JSONDecoder().decode(Recipe.self, from: Data(#"{"name":"Tiny","musicAuto":true}"#.utf8))
        XCTAssertEqual(old.name, "Tiny")
        XCTAssertTrue(old.musicAuto)
        XCTAssertEqual(old.targetSeconds, Channel(name: "").targetSeconds, "missing parts take a channel's defaults")
    }

    func testTheSummaryIsOneReadableLine() {
        let line = Recipe(name: "Bold", capturing: styledChannel()).summary
        XCTAssertTrue(line.contains("bold graphic"))
        XCTAssertTrue(line.contains("auto music"))
        XCTAssertTrue(line.contains("sound effects"))
    }
}

@MainActor
final class RecipeStoreTests: XCTestCase {
    private var store: Store!
    private var savedRecipes: [Recipe] = []
    private var savedChannels: [Channel] = []

    override func setUp() {
        store = Store()
        savedRecipes = store.recipes
        savedChannels = store.channels
        store.recipes = []
    }

    override func tearDown() {
        store.recipes = savedRecipes
        store.channels = savedChannels
    }

    func testSavingReplacesANameAlreadyInUseInsteadOfDuplicatingIt() {
        var ch = Channel(name: "A")
        ch.musicGainDB = -30
        XCTAssertNotNil(store.saveRecipe(name: "  Calm night  ", from: ch))
        ch.musicGainDB = -20
        store.saveRecipe(name: "calm NIGHT", from: ch)
        XCTAssertEqual(store.recipes.count, 1)
        XCTAssertEqual(store.recipes.first?.name, "calm NIGHT")
        XCTAssertEqual(store.recipes.first?.musicGainDB, -20)
        XCTAssertNil(store.saveRecipe(name: "   ", from: ch), "a blank name saves nothing")
        XCTAssertEqual(store.recipes.count, 1)
    }

    func testApplyingToAChannelChangesOnlyThatChannelAndDeletingWorks() throws {
        let source = styledChannel()
        let recipe = try XCTUnwrap(store.saveRecipe(name: "Bold", from: source))
        let target = Channel(name: "Target")
        let bystander = Channel(name: "Bystander")
        store.channels = [target, bystander]

        store.applyRecipe(recipe, toChannel: target.id)

        XCTAssertEqual(store.channels.first { $0.id == target.id }?.visualTone, "bold_graphic")
        XCTAssertEqual(store.channels.first { $0.id == bystander.id }?.visualTone, bystander.visualTone)

        store.deleteRecipe(recipe.id)
        XCTAssertTrue(store.recipes.isEmpty)
    }

    func testRemovingASoundAlsoClearsItFromRecipes() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("recipe-audio-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let tone = try makeTestTone(seconds: 1, in: dir)
        let item = try await store.audioLibrary.importFile(tone, kind: .sfx, name: "Recipe hit")

        var ch = Channel(name: "R")
        ch.sfxMap = [SFXMoment.impact.rawValue: item.id]
        ch.musicTrackID = item.id
        store.saveRecipe(name: "Uses it", from: ch)
        XCTAssertEqual(store.recipes.first?.sfxMap.count, 1)

        store.removeAudioItem(item.id)

        XCTAssertTrue(store.recipes.first?.sfxMap.isEmpty ?? false)
        XCTAssertNil(store.recipes.first?.musicTrackID)
    }

    private func styledChannel() -> Channel {
        var ch = Channel(name: "Bold shorts")
        ch.visualTone = "bold_graphic"
        ch.musicAuto = true
        return ch
    }
}

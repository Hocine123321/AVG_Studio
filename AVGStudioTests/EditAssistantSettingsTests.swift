import XCTest
@testable import AVGStudio

// Settings > Assistant > Editing assistant: the switch for the editor's button,
// its own optional model, what it may change, and its standing instructions.

final class EditAssistantSettingsTests: XCTestCase {
    func testDefaultsAndOlderFiles() throws {
        let s = AppSettings()
        XCTAssertTrue(s.editAssistantEnabled)
        XCTAssertFalse(s.editAssistantOwnModel)
        XCTAssertEqual(s.editAssistantInstructions, "")
        for capability in EditCapability.allCases { XCTAssertTrue(s[keyPath: capability.keyPath], capability.title) }

        let old = try JSONDecoder().decode(AppSettings.self, from: Data("{}".utf8))
        XCTAssertTrue(old.editAssistantEnabled)
        XCTAssertEqual(EditCapability.enabledCount(old), 5)
    }

    func testTheChoicesSurviveARoundTrip() throws {
        var s = AppSettings()
        s.editAssistantEnabled = false
        s.editAssistantOwnModel = true
        s.editAssistantProvider = .gemini
        s.editAssistantGeminiModel = "gemini-test"
        s.editAllowMusic = false
        s.editAssistantInstructions = "Sentence case."
        let back = try JSONDecoder().decode(AppSettings.self, from: JSONEncoder().encode(s))
        XCTAssertFalse(back.editAssistantEnabled)
        XCTAssertTrue(back.editAssistantOwnModel)
        XCTAssertEqual(back.editAssistantProvider, .gemini)
        XCTAssertEqual(back.editAssistantGeminiModel, "gemini-test")
        XCTAssertFalse(back.editAllowMusic)
        XCTAssertEqual(EditCapability.enabledCount(back), 4)
        XCTAssertEqual(back.editAssistantInstructions, "Sentence case.")
    }

    func testEveryChangingToolBelongsToExactlyOneSwitch() {
        let names = EditTools.specs.map { $0.name }
        let alwaysOn: Set<String> = ["get_edit", "list_captions", "undo"]
        for name in names where !alwaysOn.contains(name) {
            let owners = EditCapability.allCases.filter { $0.tools.contains(name) }
            XCTAssertEqual(owners.count, 1, "\(name) needs one switch")
        }
        for capability in EditCapability.allCases {
            for tool in capability.tools { XCTAssertTrue(names.contains(tool), "\(tool) is a real tool") }
        }
        for name in alwaysOn { XCTAssertNil(EditCapability.governing(tool: name), name) }
    }

    func testASwitchThatIsOffRemovesItsToolsFromTheOffer() {
        XCTAssertEqual(EditTools.offeredSpecs(for: AppSettings()).count, EditTools.specs.count)

        var s = AppSettings()
        s.editAllowMusic = false
        let offered = EditTools.offeredSpecs(for: s).map { $0.name }
        XCTAssertFalse(offered.contains("set_music"))
        XCTAssertTrue(offered.contains("set_sound_effects"))
        XCTAssertTrue(offered.contains("set_cuts"))

        for capability in EditCapability.allCases { s[keyPath: capability.keyPath] = false }
        XCTAssertEqual(Set(EditTools.offeredSpecs(for: s).map { $0.name }), ["get_edit", "list_captions", "undo"],
                       "reading and undo stay on")
    }

    func testThePromptSaysWhatIsOffAndCarriesTheInstructions() {
        XCTAssertEqual(EditTools.prompt(for: AppSettings()), EditTools.systemPrompt, "nothing extra by default")

        var s = AppSettings()
        s.editAllowCards = false
        s.editAssistantInstructions = "  Keep captions in sentence case.  "
        let prompt = EditTools.prompt(for: s)
        XCTAssertTrue(prompt.contains("title cards"))
        XCTAssertTrue(prompt.contains("switched off"))
        XCTAssertTrue(prompt.contains("Keep captions in sentence case."))

        s.editAssistantInstructions = String(repeating: "x", count: 1000)
        let clamped = EditTools.prompt(for: s)
        XCTAssertTrue(clamped.contains(String(repeating: "x", count: AgentSettingsRules.instructionsLimit)))
        XCTAssertFalse(clamped.contains(String(repeating: "x", count: AgentSettingsRules.instructionsLimit + 1)))
    }

    func testItUsesTheChatAssistantsModelUnlessItHasItsOwn() {
        var s = AppSettings()
        s.agentProvider = .groq
        s.agentGroqModel = "chat-model"
        XCTAssertEqual(EditTools.providerSettings(from: s).agentProvider, .groq)
        XCTAssertEqual(EditTools.providerSettings(from: s).agentGroqModel, "chat-model")

        s.editAssistantOwnModel = true
        s.editAssistantProvider = .gemini
        s.editAssistantGeminiModel = "edit-model"
        let own = EditTools.providerSettings(from: s)
        XCTAssertEqual(own.agentProvider, .gemini)
        XCTAssertEqual(own.agentGeminiModel, "edit-model")
        XCTAssertEqual(s.agentProvider, .groq, "the chat assistant's own choice is untouched")
    }

    func testTheEditingAssistantSettingsAreSearchable() {
        XCTAssertEqual(SettingsIndex.search("editing assistant").first?.title, "Editing assistant")
        XCTAssertTrue(SettingsIndex.search("punch").contains { $0.title == "Change cuts and punch-ins" })
        XCTAssertTrue(SettingsIndex.search("standing").contains { $0.title == "Editing instructions" })
    }
}

@MainActor
final class EditAssistantSwitchedOffTests: XCTestCase {
    private var store: Store!
    private var projectID = ""
    private var assistant: EditAssistant!
    private var saved: AppSettings!

    override func setUp() {
        store = Store()
        saved = store.settings
        projectID = "edit-switched-off-\(UUID().uuidString)"
        var p = Project(id: projectID, title: "Test video", topic: "t")
        p.sceneDurations = [5, 5, 5]
        store.projects.append(p)
        assistant = EditAssistant()
        assistant.attach(store, projectID: projectID)
    }

    override func tearDown() {
        store.settings = saved
        store.deleteProject(projectID)
    }

    private func run(_ name: String, _ json: String = "{}") -> String {
        assistant.run(toolCall(name, json))
    }

    func testACallToASwitchedOffToolIsRefusedAndChangesNothing() {
        store.settings.editAllowCuts = false
        let reply = run("set_cuts", #"{"level":"off"}"#)
        XCTAssertTrue(reply.contains("switched off in Settings"))
        XCTAssertTrue(reply.contains("cuts and punch-ins"))
        XCTAssertNil(store.project(projectID)?.edits)

        store.settings.editAllowMusic = false
        XCTAssertTrue(run("set_music", #"{"level_db":-15}"#).contains("switched off"))
        XCTAssertNil(store.project(projectID)?.edits)
    }

    func testTheOtherToolsKeepWorkingAndReadingIsAlwaysOn() {
        store.settings.editAllowMusic = false
        _ = run("set_cuts", #"{"level":"off"}"#)
        XCTAssertEqual(store.project(projectID)?.edits?.editLevel, .off)
        XCTAssertTrue(run("get_edit").contains("Cuts and punch-ins:"))
    }

    func testAChatTurnBuildsItsProviderFromTheEditingModel() async {
        struct Stop: Error {}
        store.settings.agentProvider = .groq
        store.settings.editAssistantOwnModel = true
        store.settings.editAssistantProvider = .gemini
        store.settings.editAssistantGeminiModel = "edit-model"
        var seen: AppSettings?
        assistant.makeProvider = { settings in
            seen = settings
            throw Stop()
        }
        await assistant.send("hello").value
        XCTAssertEqual(seen?.agentProvider, .gemini)
        XCTAssertEqual(seen?.agentGeminiModel, "edit-model")
    }
}

import XCTest
@testable import AVGStudio

// Hook Bench: ten opening lines from one call; the chosen one is carried to
// the script writer word for word. Everything is optional.

final class HookBenchTests: XCTestCase {
    func testAJSONReplyIsParsed() {
        let hooks = HookBench.parse(#"{"hooks": ["Why do octopuses have three hearts?", "Three hearts, one very strange reason."]}"#)
        XCTAssertEqual(hooks, ["Why do octopuses have three hearts?", "Three hearts, one very strange reason."])
    }

    func testCodeFencesAndAChattyIntroAreTolerated() {
        let reply = "Sure! Here you go:\n```json\n{\"hooks\": [\"One.\", \"Two.\"]}\n```\nEnjoy."
        XCTAssertEqual(HookBench.parse(reply), ["One.", "Two."])
    }

    func testABareArrayWorks() {
        XCTAssertEqual(HookBench.parse(#"["First line", "Second line"]"#), ["First line", "Second line"])
    }

    func testANumberedListIsTheFallback() {
        let reply = "1. “Stop scrolling.”\n2) What if you never slept?\n- A quiet claim\n\n"
        XCTAssertEqual(HookBench.parse(reply), ["Stop scrolling.", "What if you never slept?", "A quiet claim"])
    }

    func testLinesAreCleanedDedupedAndCappedAtTen() {
        let many = (1...14).map { "\"Line \($0)\"" }.joined(separator: ", ")
        let hooks = HookBench.parse("{\"hooks\": [\(many), \"line 1\"]}")
        XCTAssertEqual(hooks.count, 10)
        XCTAssertEqual(hooks.first, "Line 1", "surrounding quotes are removed")
        XCTAssertEqual(Set(hooks.map { $0.lowercased() }).count, hooks.count, "no repeats, even in another case")
        XCTAssertTrue(HookBench.parse("{\"hooks\": [\"\", \"  \"]}").isEmpty)
    }

    func testTheWriterIsToldToOpenWithTheChosenLineAndOnlyThen() {
        XCTAssertEqual(HookBench.directionBlock(hook: nil), "")
        XCTAssertEqual(HookBench.directionBlock(hook: "   "), "")
        let block = HookBench.directionBlock(hook: "Why do octopuses have three hearts?")
        XCTAssertTrue(block.contains("Why do octopuses have three hearts?"))
        XCTAssertTrue(block.contains("word for word"))
    }

    func testThePromptCarriesTheTopicAndAsksForTenAsJSON() {
        let prompt = HookBench.prompt(topic: "why octopuses have three hearts")
        XCTAssertTrue(prompt.contains("why octopuses have three hearts"))
        XCTAssertTrue(prompt.contains("\(HookBench.count)"))
        XCTAssertTrue(prompt.contains("\"hooks\""))
    }

    func testGenerateReturnsTheParsedHooksAndFailsClearlyWhenThereAreNone() async throws {
        struct Canned: TextProvider {
            let reply: String
            func complete(_ prompt: String, system: String?) async throws -> String { reply }
        }
        let hooks = try await HookBench.generate(topic: "t", provider: Canned(reply: #"{"hooks": ["A", "B"]}"#))
        XCTAssertEqual(hooks, ["A", "B"])
    }

    func testAnEmptyReplyIsAnError() async {
        struct Empty: TextProvider {
            func complete(_ prompt: String, system: String?) async throws -> String { "   " }
        }
        do {
            _ = try await HookBench.generate(topic: "t", provider: Empty())
            XCTFail("an empty reply is an error")
        } catch {
            XCTAssertTrue(error.localizedDescription.contains("did not return any opening lines"))
        }
    }
}

@MainActor
final class HookProjectTests: XCTestCase {
    func testTheChosenHookIsKeptOnTheNewProjectAndABlankOneIsNot() {
        let store = Store()
        let ch = Channel(name: "Hooks")
        let with = store.createProject(topic: "octopuses", title: "", channel: ch, aspect: .portrait, hook: "  Three hearts?  ")
        let without = store.createProject(topic: "octopuses", title: "", channel: ch, aspect: .portrait, hook: "   ")
        let plain = store.createProject(topic: "octopuses", title: "", channel: ch, aspect: .portrait)
        defer { for p in [with, without, plain] { store.deleteProject(p.id) } }
        XCTAssertEqual(store.project(with.id)?.hook, "Three hearts?")
        XCTAssertNil(store.project(without.id)?.hook)
        XCTAssertNil(store.project(plain.id)?.hook)
    }
}

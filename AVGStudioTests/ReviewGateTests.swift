import XCTest
@testable import AVGStudio

// Review gates: after the script is written a video can wait for the person to
// fix its scenes before anything paid is made. Off by default.

final class ReviewGateTests: XCTestCase {
    private func scenes() -> [VideoScene] {
        [VideoScene(index: 1, text: "Second line.", imagePrompt: "a lake"),
         VideoScene(index: 0, text: "First line.", imagePrompt: "a hill", imageFile: nil, label: "Hook")]
    }

    func testReviewIsOffByDefaultSoNothingStopsUnlessAskedFor() {
        let ch = Channel(name: "Plain")
        XCTAssertFalse(ch.reviewScript)
        XCTAssertFalse(ch.reviewStoryboard)
        XCTAssertFalse(ReviewGatePolicy.holds(ch))
    }

    func testEitherReviewHoldsAndTheTitleSaysWhich() {
        var ch = Channel(name: "C")
        ch.reviewScript = true
        XCTAssertTrue(ReviewGatePolicy.holds(ch))
        XCTAssertEqual(ReviewGatePolicy.title(ch), "Review the script")
        ch.reviewStoryboard = true
        XCTAssertEqual(ReviewGatePolicy.title(ch), "Review the script and storyboard")
        ch.reviewScript = false
        XCTAssertTrue(ReviewGatePolicy.holds(ch))
        XCTAssertEqual(ReviewGatePolicy.title(ch), "Review the storyboard")
    }

    func testChangesAreAppliedAndTheNarrationFollowsInSceneOrder() {
        let result = ReviewGatePolicy.apply([
            .init(index: 0, text: "  A better first line.  ", imagePrompt: "a misty hill"),
            .init(index: 1, text: "Second line.", imagePrompt: "a lake"),
        ], to: scenes())
        XCTAssertEqual(result.scenes.first { $0.index == 0 }?.text, "A better first line.")
        XCTAssertEqual(result.scenes.first { $0.index == 0 }?.imagePrompt, "a misty hill")
        XCTAssertEqual(result.scenes.first { $0.index == 0 }?.label, "Hook", "everything else about a scene is kept")
        XCTAssertEqual(result.narration, "A better first line. Second line.", "ordered by scene, not by array position")
    }

    func testAReviewNeverDeletesASceneOrIgnoresAnUnknownOne() {
        let result = ReviewGatePolicy.apply([
            .init(index: 0, text: "   ", imagePrompt: "x"),
            .init(index: 9, text: "Nobody", imagePrompt: "y"),
        ], to: scenes())
        XCTAssertEqual(result.scenes.count, 2)
        XCTAssertEqual(result.scenes.first { $0.index == 0 }?.text, "First line.", "an emptied line keeps its old text")
        XCTAssertEqual(result.narration, "First line. Second line.")
    }

    func testAnEmptiedImagePromptIsAllowed() {
        let result = ReviewGatePolicy.apply([.init(index: 1, text: "Second line.", imagePrompt: "")], to: scenes())
        XCTAssertEqual(result.scenes.first { $0.index == 1 }?.imagePrompt, "")
    }

    func testOlderChannelsAndProjectsLoadWithReviewOff() throws {
        var ch = Channel(name: "Old")
        ch.reviewScript = true
        ch.reviewStoryboard = true
        var json = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(ch)) as? [String: Any])
        json.removeValue(forKey: "reviewScript")
        json.removeValue(forKey: "reviewStoryboard")
        let old = try JSONDecoder().decode(Channel.self, from: JSONSerialization.data(withJSONObject: json))
        XCTAssertFalse(old.reviewScript)
        XCTAssertFalse(old.reviewStoryboard)

        var p = Project(id: "waiting", title: "T", topic: "t")
        XCTAssertNil(p.awaitingReview)
        p.awaitingReview = true
        let back = try JSONDecoder().decode(Project.self, from: JSONEncoder().encode(p))
        XCTAssertEqual(back.awaitingReview, true)
    }
}

@MainActor
final class ReviewSaveTests: XCTestCase {
    func testSavingAReviewUpdatesTheScenesAndTheNarration() {
        let store = Store()
        let id = "review-save-\(UUID().uuidString)"
        var p = Project(id: id, title: "Review me", topic: "t")
        p.narration = "Old one. Old two."
        p.scenes = [VideoScene(index: 0, text: "Old one.", imagePrompt: "a"), VideoScene(index: 1, text: "Old two.", imagePrompt: "b")]
        p.awaitingReview = true
        store.projects.append(p)
        defer { store.deleteProject(id) }

        store.saveReview(id, edits: [.init(index: 1, text: "New two.", imagePrompt: "bb")])

        let after = store.project(id)
        XCTAssertEqual(after?.scenes.first { $0.index == 1 }?.text, "New two.")
        XCTAssertEqual(after?.scenes.first { $0.index == 1 }?.imagePrompt, "bb")
        XCTAssertEqual(after?.narration, "Old one. New two.")
        XCTAssertEqual(after?.awaitingReview, true, "saving alone does not continue the video")
    }
}

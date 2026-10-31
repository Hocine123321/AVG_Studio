import XCTest
@testable import AVGStudio

final class SubjectFocusTests: XCTestCase {
    func testAStandardAnswerIsReadScenesBecomeZeroBased() {
        let raw = #"{"pictures":[{"picture":1,"x":0.4,"y":0.3},{"picture":2,"x":0.7,"y":0.5}]}"#
        XCTAssertEqual(SubjectFocus.parse(raw, valid: [0, 1]),
                       [FocusPoint(scene: 0, x: 0.4, y: 0.3), FocusPoint(scene: 1, x: 0.7, y: 0.5)])
    }

    func testPercentagesAreAcceptedAndEverythingIsClamped() {
        let raw = #"{"pictures":[{"picture":1,"x":40,"y":30},{"picture":2,"x":1.4,"y":-0.2}]}"#
        let points = SubjectFocus.parse(raw, valid: [0, 1])
        XCTAssertEqual(points[0].x, 0.4, accuracy: 1e-9)
        XCTAssertEqual(points[0].y, 0.3, accuracy: 1e-9)
        XCTAssertEqual(points[1].x, 1.0, accuracy: 1e-9)
        XCTAssertEqual(points[1].y, 0.0, accuracy: 1e-9)
    }

    func testPicturesThatWereNotSentAndRepeatsAreIgnored() {
        let raw = #"{"pictures":[{"picture":1,"x":0.1,"y":0.1},{"picture":1,"x":0.9,"y":0.9},{"picture":7,"x":0.5,"y":0.5}]}"#
        XCTAssertEqual(SubjectFocus.parse(raw, valid: [0, 1]), [FocusPoint(scene: 0, x: 0.1, y: 0.1)])
    }

    func testABareArrayAndTextAroundTheJSONAreBothFine() {
        XCTAssertEqual(SubjectFocus.parse(#"[{"picture":1,"x":0.2,"y":0.6}]"#, valid: [0]), [FocusPoint(scene: 0, x: 0.2, y: 0.6)])
        let wrapped = "Sure! ```json\n{\"pictures\":[{\"picture\":1,\"x\":0.5,\"y\":0.5}]}\n```"
        XCTAssertEqual(SubjectFocus.parse(wrapped, valid: [0]).count, 1)
    }

    func testNumbersAsStringsAndMissingFieldsAreHandled() {
        let raw = #"{"pictures":[{"picture":"1","x":"0.25","y":"0.75"},{"picture":2,"x":0.5}]}"#
        XCTAssertEqual(SubjectFocus.parse(raw, valid: [0, 1]), [FocusPoint(scene: 0, x: 0.25, y: 0.75)])
    }

    func testGarbageGivesNoPointsInsteadOfAnError() {
        XCTAssertTrue(SubjectFocus.parse("I cannot help with that.", valid: [0]).isEmpty)
        XCTAssertTrue(SubjectFocus.parse("", valid: [0]).isEmpty)
        XCTAssertTrue(SubjectFocus.parse(#"{"other":1}"#, valid: [0]).isEmpty)
    }

    func testTheSignatureIsStableAndChangesWithThePictures() {
        let a = SubjectFocus.signature(["0:a.png:100", "1:b.png:200"])
        XCTAssertEqual(a, SubjectFocus.signature(["0:a.png:100", "1:b.png:200"]))
        XCTAssertNotEqual(a, SubjectFocus.signature(["0:a.png:100", "1:b.png:201"]), "a regenerated picture changes the size")
        XCTAssertNotEqual(a, SubjectFocus.signature(["1:b.png:200", "0:a.png:100"]))
        XCTAssertEqual(SubjectFocus.signature([]), SubjectFocus.signature([]))
    }

    func testTheRequestListsEveryPictureAfterTheInstruction() {
        let body = GeminiVision.requestBody([(scene: 0, jpeg: Data([1, 2])), (scene: 4, jpeg: Data([3]))])
        let contents = body["contents"] as? [[String: Any]]
        let parts = contents?.first?["parts"] as? [[String: Any]]
        XCTAssertEqual(parts?.count, 5, "instruction + (label + image) for each of two pictures")
        XCTAssertEqual(parts?[1]["text"] as? String, "Picture 1:")
        XCTAssertEqual(parts?[3]["text"] as? String, "Picture 5:", "scene 4 is picture 5 to the model")
        let inline = parts?[2]["inlineData"] as? [String: Any]
        XCTAssertEqual(inline?["mimeType"] as? String, "image/jpeg")
        XCTAssertEqual(inline?["data"] as? String, Data([1, 2]).base64EncodedString())
        let config = body["generationConfig"] as? [String: Any]
        XCTAssertEqual(config?["responseMimeType"] as? String, "application/json")
    }

    func testTheReplyTextIsJoinedFromItsParts() {
        let reply: [String: Any] = ["candidates": [["content": ["parts": [["text": "{\"a\":"], ["text": "1}"]]]]]]
        XCTAssertEqual(GeminiVision.text(in: reply), "{\"a\":1}")
        XCTAssertNil(GeminiVision.text(in: [:]))
    }

    func testTheSmartLevelAndAProjectsSavedPointsRoundTrip() throws {
        XCTAssertTrue(EditLevel.allCases.contains(.smart))
        var project = Project(id: "p", title: "T", topic: "t")
        project.focus = SubjectFocus(signature: "abc", points: [FocusPoint(scene: 2, x: 0.3, y: 0.4)])
        let back = try JSONDecoder().decode(Project.self, from: JSONEncoder().encode(project))
        XCTAssertEqual(back.focus, project.focus)
        XCTAssertNil(try JSONDecoder().decode(Project.self, from: JSONEncoder().encode(Project(id: "q", title: "T", topic: "t"))).focus)
    }
}

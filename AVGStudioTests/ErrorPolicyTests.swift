import XCTest
@testable import AVGStudio

/// `classify()` and `POLICY` are the one place the app decides whether a
/// failure is retried, switched, or surfaced - the iOS twin of
/// `avg/providers/errors.py`, which the PC repo tests heavily.
final class ErrorPolicyTests: XCTestCase {

    func testStatusRulesComeFirst() {
        XCTAssertEqual(classify(status: 401, body: "", provider: "groq"), .auth)
        XCTAssertEqual(classify(status: 403, body: "anything", provider: "gemini"), .auth)
        XCTAssertEqual(classify(status: 500, body: "", provider: "groq"), .network)
        XCTAssertEqual(classify(status: 503, body: "overloaded", provider: "openai"), .network)
    }

    func testStatusDefaultsWhenTheBodySaysNothing() {
        XCTAssertEqual(classify(status: 429, body: "", provider: "groq"), .rateLimit)
        XCTAssertEqual(classify(status: 402, body: "", provider: "openai"), .billing)
        XCTAssertEqual(classify(status: 404, body: "", provider: "gemini"), .modelMissing)
    }

    func testUnknownFailureFailsSafeToBug() {
        XCTAssertEqual(classify(status: 418, body: "", provider: "groq"), .bug)
    }

    func testConnectionErrors() {
        XCTAssertEqual(classifyURLError(URLError(.timedOut)), .timeout)
        XCTAssertEqual(classifyURLError(URLError(.notConnectedToInternet)), .network)
        XCTAssertEqual(classifyURLError(URLError(.networkConnectionLost)), .network)
        XCTAssertEqual(classifyURLError(URLError(.badURL)), .bug)
    }

    func testEveryKindHasAPolicyAndTheRulesAreCoherent() {
        let all: [ErrorKind] = [.auth, .quota, .billing, .rateLimit, .modelMissing,
                                .contentFilter, .network, .timeout, .dependency, .bug]
        for kind in all {
            guard let rule = POLICY[kind] else { return XCTFail("no POLICY entry for \(kind)") }
            XCTAssertGreaterThanOrEqual(rule.maxAttempts, 1)
            if rule.retryable {
                XCTAssertGreaterThan(rule.maxAttempts, 1, "\(kind) is retryable so it must retry")
                XCTAssertGreaterThan(rule.backoffSeconds, 0)
            } else {
                XCTAssertEqual(rule.maxAttempts, 1, "\(kind) is not retryable, one attempt only")
            }
        }
        XCTAssertEqual(POLICY[.auth]?.onExhausted, .switchKey)
        XCTAssertEqual(POLICY[.modelMissing]?.onExhausted, .switchModel)
        XCTAssertEqual(POLICY[.contentFilter]?.onExhausted, .fail)
    }
}

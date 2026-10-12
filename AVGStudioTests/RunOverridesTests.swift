import XCTest
@testable import AVGStudio

/// A fallback belongs to one video's run and changes nothing global. The bug
/// these pin: a fallback used to overwrite the person's main model for good,
/// and the first fix still changed the global setting while the run lasted.
final class RunOverridesTests: XCTestCase {

    private func base() -> AppSettings {
        var s = AppSettings()
        s.geminiModel = "gemini-2.5-pro"
        s.text = .gemini
        return s
    }

    func testAnOverrideAppliesOnlyToItsOwnProject() {
        var o = RunOverrides()
        o.set(\.geminiModel, to: "lite", project: "a")
        XCTAssertEqual(o.applied(to: base(), project: "a").geminiModel, "lite")
        XCTAssertEqual(o.applied(to: base(), project: "b").geminiModel, "gemini-2.5-pro", "another video is unaffected")
    }

    func testTheBaseSettingsAreNeverChanged() {
        var o = RunOverrides()
        let s = base()
        o.set(\.geminiModel, to: "lite", project: "a")
        o.set(\.text, to: .pollinations, project: "a")
        _ = o.applied(to: s, project: "a")
        XCTAssertEqual(s.geminiModel, "gemini-2.5-pro")
        XCTAssertEqual(s.text, .gemini)
    }

    func testClearingEndsTheRun() {
        var o = RunOverrides()
        o.set(\.geminiModel, to: "lite", project: "a")
        XCTAssertTrue(o.has(project: "a"))
        XCTAssertTrue(o.clear(project: "a"))
        XCTAssertEqual(o.applied(to: base(), project: "a").geminiModel, "gemini-2.5-pro", "the person's model is back")
        XCTAssertFalse(o.has(project: "a"))
        XCTAssertFalse(o.clear(project: "a"), "clearing twice is harmless")
    }

    func testALaterOverrideOfTheSameSettingReplacesTheEarlierOne() {
        var o = RunOverrides()
        o.set(\.geminiModel, to: "lite", project: "a")
        o.set(\.geminiModel, to: "even-lighter", project: "a")
        XCTAssertEqual(o.applied(to: base(), project: "a").geminiModel, "even-lighter")
    }

    func testDifferentSettingsCombine() {
        var o = RunOverrides()
        o.set(\.geminiModel, to: "lite", project: "a")
        o.set(\.voice, to: .gemini, project: "a")
        let s = o.applied(to: base(), project: "a")
        XCTAssertEqual(s.geminiModel, "lite")
        XCTAssertEqual(s.voice, .gemini)
        XCTAssertEqual(s.image, base().image, "untouched settings stay as they are")
    }

    func testAnEditMadeDuringTheRunIsSeenForEverythingExceptTheOverriddenSetting() {
        var o = RunOverrides()
        o.set(\.geminiModel, to: "lite", project: "a")
        var edited = base()
        edited.fps = 60            // the person changes something else meanwhile
        edited.geminiModel = "x"   // ...and even the same setting: this run still uses its fallback
        let s = o.applied(to: edited, project: "a")
        XCTAssertEqual(s.fps, 60)
        XCTAssertEqual(s.geminiModel, "lite")
        XCTAssertEqual(o.applied(to: edited, project: "b").geminiModel, "x", "a different video follows the person's edit")
    }

    func testNoOverridesReturnsTheSettingsUnchanged() {
        let o = RunOverrides()
        XCTAssertEqual(o.applied(to: base(), project: "a").geminiModel, "gemini-2.5-pro")
        XCTAssertFalse(o.has(project: "a"))
    }

    func testTwoProjectsKeepSeparateOverrides() {
        var o = RunOverrides()
        o.set(\.geminiModel, to: "lite-a", project: "a")
        o.set(\.geminiModel, to: "lite-b", project: "b")
        o.clear(project: "a")
        XCTAssertEqual(o.applied(to: base(), project: "a").geminiModel, "gemini-2.5-pro")
        XCTAssertEqual(o.applied(to: base(), project: "b").geminiModel, "lite-b")
    }
}

/// The same behaviour through the real Store and Recovery.
@MainActor
final class StoreRunOverridesTests: XCTestCase {

    func testAFallbackForOneVideoLeavesTheGlobalSettingAlone() {
        let store = Store()
        store.settings.text = .gemini
        store.settings.geminiModel = "gemini-2.5-pro"
        store.overrideForRun(\.geminiModel, to: "gemini-2.5-flash-lite", project: "p1")
        XCTAssertEqual(store.settings.geminiModel, "gemini-2.5-pro", "the Settings screen still shows the person's model")
        XCTAssertEqual(store.effectiveSettings(for: "p1").geminiModel, "gemini-2.5-flash-lite")
        XCTAssertEqual(store.effectiveSettings(for: "p2").geminiModel, "gemini-2.5-pro", "a video started meanwhile is unaffected")
    }

    func testRecoveryAppliesTheFallbackToTheRunAndOnlyOnce() {
        let store = Store()
        store.settings.text = .gemini
        store.settings.geminiModel = "gemini-2.5-pro"
        store.settings.writerFallbackModel = ""
        let applied = Recovery.applyFallback(phase: .writing, store: store, project: "p1")
        XCTAssertEqual(applied, "gemini-2.5-flash-lite")
        XCTAssertEqual(store.settings.geminiModel, "gemini-2.5-pro")
        XCTAssertEqual(store.effectiveSettings(for: "p1").geminiModel, "gemini-2.5-flash-lite")
        XCTAssertNil(Recovery.applyFallback(phase: .writing, store: store, project: "p1"),
                     "already on the fallback: offering it again would just repeat the failing request")
        XCTAssertEqual(Recovery.applyFallback(phase: .writing, store: store, project: "p2"), "gemini-2.5-flash-lite",
                       "another video still gets its own fallback")
    }

    func testProviderSwitchesFromRecoveryAreAlsoPerRun() async {
        let store = Store()
        store.settings.text = .gemini
        let plan = RecoveryPlan(projectID: "p1", phase: .writing, title: "t", reason: "r", actions: [.freeProvider])
        _ = await Recovery.apply(.freeProvider, plan: plan, store: store)
        XCTAssertEqual(store.settings.text, .gemini, "the person's writer is unchanged")
        XCTAssertEqual(store.effectiveSettings(for: "p1").text, .pollinations)
    }
}

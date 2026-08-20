// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import XCTest
@testable import AICollab

/// The rail derives one next step from live state — these tests pin every
/// stage of the funnel, the blocked/transitional detours, and the once-per-
/// generation ready moment.
@MainActor
final class GuidanceRailTests: XCTestCase {

    private func project() -> ProjectRecord {
        ProjectRecord([
            "project_instance_id": "proj-1",
            "project_key": "edge-studio",
            "project_binding_digest": String(repeating: "a", count: 64),
            "product_contract_version": "3",
        ])!
    }

    private func scenario(_ observed: String, generation: Int = 3) -> ScenarioRecord {
        ScenarioRecord([
            "scenario_id": "room-1",
            "scenario_generation": generation,
            "state_revision": 7,
            "desired_state": "running",
            "observed_state": observed,
            "workspace_binding_id": "ws-1",
            "participant_ids": [String](),
        ])!
    }

    private func participant(
        _ observed: String, mode: String = "tui"
    ) -> ParticipantRecord {
        // Contract-valid: a ready TUI record always carries its presentation
        // binding (tui_ready_requires_presentation_binding); headless never.
        let presentation: Any = (mode == "tui" && observed == "ready")
            ? "presentation-1" : NSNull()
        return ParticipantRecord([
            "participant_id": "analyst",
            "participant_generation": 1,
            "state_revision": 3,
            "desired_state": "running",
            "observed_state": observed,
            "runtime_binding_id": "runtime-1",
            "presentation_binding_id": presentation,
            "interaction_mode": mode,
        ])!
    }

    private func model(
        room observed: String? = nil,
        generation: Int = 3,
        workspaceReady: Bool = false,
        participants: [ParticipantRecord] = [],
        defaults: UserDefaults = .standard
    ) -> HarnessViewModel {
        let model = HarnessViewModel(readyMomentDefaults: defaults)
        model.projects = [project()]
        model.selectedProjectID = "proj-1"
        if let observed {
            model.scenarios = [scenario(observed, generation: generation)]
            model.selectedScenarioID = "room-1"
        }
        model.workspaceReady = workspaceReady
        model.participants = participants
        return model
    }

    func testFunnelStages() {
        XCTAssertEqual(HarnessViewModel().guidance, .registerProject)
        XCTAssertEqual(model().guidance, .createRoom)
        XCTAssertEqual(model(room: "closed").guidance, .prepareWorkspace)
        XCTAssertEqual(
            model(room: "closed", workspaceReady: true).guidance, .addColleague
        )
        XCTAssertEqual(
            model(
                room: "closed", workspaceReady: true,
                participants: [participant("stopped")]
            ).guidance,
            .resumeRoom
        )
        XCTAssertEqual(
            model(
                room: "running", workspaceReady: true,
                participants: [participant("stopped")]
            ).guidance,
            .startColleagues
        )
        XCTAssertEqual(
            model(
                room: "running", workspaceReady: true,
                participants: [participant("ready")]
            ).guidance,
            .focusAndAssign
        )
    }

    func testFailClosedForUnknownAndInconsistentStates() {
        XCTAssertEqual(
            model(room: "recovering").guidance,
            .attend(S.Status.label("recovering")),
            "a state outside the Scenario contract must fail closed to attend"
        )
        XCTAssertEqual(
            model(room: "running", workspaceReady: false).guidance,
            .inconsistent,
            "running without workspace evidence must never offer Prepare"
        )
    }

    func testBlockedAndTransitionalDetours() {
        XCTAssertEqual(
            model(room: "degraded").guidance, .attend(S.Status.label("degraded"))
        )
        XCTAssertEqual(
            model(room: "provision_failed").guidance,
            .attend(S.Status.label("provision_failed"))
        )
        XCTAssertEqual(
            model(room: "provisioning").guidance,
            .working(S.Status.label("provisioning"))
        )
    }

    func testReadyMomentFiresOncePerGeneration() {
        let suite = "guidance-rail-tests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }

        let first = model(
            room: "running", workspaceReady: true,
            participants: [participant("ready")], defaults: defaults
        )
        XCTAssertFalse(first.showReadyMoment)
        first.updateReadyMoment()
        XCTAssertTrue(first.showReadyMoment)
        first.dismissReadyMoment()
        first.updateReadyMoment()
        XCTAssertFalse(first.showReadyMoment, "same generation must not repeat")

        let again = model(
            room: "running", workspaceReady: true,
            participants: [participant("ready")], defaults: defaults
        )
        again.updateReadyMoment()
        XCTAssertFalse(again.showReadyMoment, "a fresh launch must not repeat it")
    }

    /// codex review P1-1 root causes: the focus/ready moment requires a
    /// running room AND a focusable (TUI) ready colleague — never a closed
    /// room, never a headless-only roster.
    func testFocusRequiresRunningRoomAndFocusableColleague() {
        let suite = "guidance-focus-tests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }

        let closed = model(
            room: "closed", workspaceReady: true,
            participants: [participant("ready")], defaults: defaults
        )
        XCTAssertEqual(closed.guidance, .resumeRoom)
        closed.updateReadyMoment()
        XCTAssertFalse(closed.showReadyMoment, "closed room must not celebrate")

        let headless = model(
            room: "running", workspaceReady: true,
            participants: [participant("ready", mode: "headless")],
            defaults: defaults
        )
        XCTAssertEqual(
            headless.guidance, .addColleague,
            "a headless-only roster has no colleague to work with"
        )
        headless.updateReadyMoment()
        XCTAssertFalse(headless.showReadyMoment)

        let working = model(
            room: "running", workspaceReady: true,
            participants: [participant("ready")], defaults: defaults
        )
        XCTAssertEqual(working.guidance, .focusAndAssign)
        working.updateReadyMoment()
        XCTAssertTrue(working.showReadyMoment)
    }

    func testReadyMomentReturnsForANewGeneration() {
        let suite = "guidance-generation-tests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }

        let generationThree = model(
            room: "running", workspaceReady: true,
            participants: [participant("ready")], defaults: defaults
        )
        generationThree.updateReadyMoment()
        XCTAssertTrue(generationThree.showReadyMoment)

        let generationFour = model(
            room: "running", generation: 4, workspaceReady: true,
            participants: [participant("ready")], defaults: defaults
        )
        generationFour.updateReadyMoment()
        XCTAssertTrue(
            generationFour.showReadyMoment,
            "a new generation earns its own one-time moment"
        )
    }
}

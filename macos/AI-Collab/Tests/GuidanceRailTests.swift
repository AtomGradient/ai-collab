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
            "objective": "",
            "objective_history": [[String: Any]](),
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
        policyReadiness: PolicyReadiness = .current,
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
        model.policyReadiness = policyReadiness
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

    func testPolicyGatePrecedesStartAndFocus() {
        let stopped = [participant("stopped")]
        let ready = [participant("ready")]
        let suite = "guidance-policy-gate-tests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }

        XCTAssertEqual(
            model(
                room: "running", workspaceReady: true, participants: stopped,
                policyReadiness: .missing
            ).guidance,
            .configurePolicy
        )
        let drifted = model(
            room: "running", workspaceReady: true, participants: ready,
            policyReadiness: .replanRequired, defaults: defaults
        )
        XCTAssertEqual(
            drifted.guidance,
            .configurePolicy,
            "generation drift must suppress the false ready/focus state"
        )
        drifted.updateReadyMoment()
        XCTAssertFalse(
            drifted.showReadyMoment,
            "policy drift must not emit a false all-ready milestone"
        )
        XCTAssertEqual(
            model(
                room: "running", workspaceReady: true, participants: stopped,
                policyReadiness: .loading
            ).guidance,
            .working(S.Policy.loadingRules)
        )
        XCTAssertEqual(
            model(
                room: "running", workspaceReady: true, participants: ready,
                policyReadiness: .unavailable
            ).guidance,
            .inconsistent,
            "an unknown policy read failure must fail closed without an action"
        )
    }

    func testPolicyReadErrorsOnlyClassifyTypedAbsenceAsMissing() {
        let missing = HarnessIPCError.hostRejected(
            code: "target.delivery-not-found", category: "identity",
            message: "scenario policy is unavailable", retryable: false,
            mutationState: "not_started", repairAction: nil
        )
        XCTAssertEqual(
            HarnessViewModel.policyReadiness(afterPolicyReadError: missing), .missing
        )
        XCTAssertEqual(
            HarnessViewModel.policyReadiness(afterPolicyReadError: HarnessIPCError.hostUnavailable),
            .unavailable
        )
        let rejected = HarnessIPCError.hostRejected(
            code: "operation.precondition-failed", category: "operation",
            message: "failed", retryable: true,
            mutationState: "not_started", repairAction: nil
        )
        XCTAssertEqual(
            HarnessViewModel.policyReadiness(afterPolicyReadError: rejected), .unavailable
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

    /// The header must not offer a lifecycle action while guidance is blocked
    /// or transitional; and a room without an applied policy must not offer
    /// Start All, because it starts fine and then fails on the first message.
    func testBlockedAndTransitionalStatesPreemptLifecycleActions() {
        XCTAssertTrue(model(room: "degraded").lifecycleActionsPreempted)
        XCTAssertTrue(model(room: "provision_failed").lifecycleActionsPreempted)
        XCTAssertTrue(model(room: "provisioning").lifecycleActionsPreempted)
        XCTAssertTrue(model(room: "closing").lifecycleActionsPreempted)

        let ready = model(
            room: "running",
            workspaceReady: true,
            participants: [participant("ready")],
            policyReadiness: .current
        )
        XCTAssertEqual(ready.guidance, GuidanceStep.focusAndAssign)
        XCTAssertFalse(
            ready.lifecycleActionsPreempted,
            "an operational room must keep its lifecycle actions"
        )

        let noPolicy = model(
            room: "running",
            workspaceReady: true,
            participants: [participant("stopped")],
            policyReadiness: .missing
        )
        XCTAssertEqual(noPolicy.guidance, GuidanceStep.configurePolicy)
        XCTAssertFalse(
            noPolicy.lifecycleActionsPreempted,
            "a missing policy is actionable, not a preempting block"
        )
    }

    /// Readiness is unanimous. A room holding one ready and one stopped
    /// colleague still has work for Start All, and must not claim it is ready
    /// nor fire the once-per-generation all-green moment.
    func testMixedColleagueStatesNeverReportTheRoomReady() {
        let suite = "guidance-rail-tests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }

        let mixed = model(
            room: "running", workspaceReady: true,
            participants: [participant("ready"), participant("stopped")],
            defaults: defaults
        )
        XCTAssertEqual(
            mixed.guidance, GuidanceStep.startColleagues,
            "a startable colleague must win over the ready one"
        )
        mixed.updateReadyMoment()
        XCTAssertFalse(
            mixed.showReadyMoment, "a partly started room is not the ready moment"
        )

        // Nothing is startable and something is still transitioning: the Host
        // would refuse Start All, so guidance must offer no action at all.
        let starting = model(
            room: "running", workspaceReady: true,
            participants: [participant("ready"), participant("starting")],
            defaults: defaults
        )
        XCTAssertEqual(
            starting.guidance, GuidanceStep.working(S.Status.label("starting"))
        )
        XCTAssertNil(
            starting.guidePresentation().actionable,
            "transitional colleagues must not produce an action"
        )

        let unanimous = model(
            room: "running", workspaceReady: true,
            participants: [participant("ready"), participant("ready")],
            defaults: defaults
        )
        XCTAssertEqual(unanimous.guidance, GuidanceStep.focusAndAssign)
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

    /// codex deck review root causes: closed-room continuity, fail-closed
    /// states never yield a card action, and honest milestone positioning.
    func testGuidePresentationSeparatesPositioningFromActions() {
        let resume = model(
            room: "closed", workspaceReady: true,
            participants: [participant("stopped")]
        )
        XCTAssertEqual(resume.guidePresentation().index, 4)
        XCTAssertEqual(resume.guidePresentation().actionable, .resumeRoom)

        let configure = model(
            room: "running", workspaceReady: true,
            participants: [participant("stopped")], policyReadiness: .missing
        )
        XCTAssertEqual(configure.guidePresentation().index, 4)
        XCTAssertEqual(configure.guidePresentation().actionable, .configurePolicy)

        let start = model(
            room: "running", workspaceReady: true,
            participants: [participant("stopped")]
        )
        XCTAssertEqual(start.guidePresentation().index, 4)
        XCTAssertEqual(start.guidePresentation().actionable, .startColleagues)

        let inconsistent = model(room: "running", workspaceReady: false)
        XCTAssertEqual(inconsistent.guidePresentation().index, 2)
        XCTAssertNil(
            inconsistent.guidePresentation().actionable,
            "inconsistent must never offer a card action"
        )

        let degraded = model(
            room: "degraded", workspaceReady: true,
            participants: [participant("stopped")]
        )
        XCTAssertEqual(degraded.guidePresentation().index, 4)
        XCTAssertNil(degraded.guidePresentation().actionable)

        let transitional = model(room: "provisioning")
        XCTAssertEqual(
            transitional.guidePresentation().index, 2,
            "positioning follows completed milestones, never claims step 6"
        )
        XCTAssertNil(transitional.guidePresentation().actionable)
    }

    func testOpenGuideCardSurvivesALanguageSwitch() {
        let model = HarnessViewModel()
        model.guideStep = 4
        L10n.shared.preference = .english
        XCTAssertTrue(S.Guide.policySay.contains("no collaboration rules yet"))
        L10n.shared.preference = .simplifiedChinese
        XCTAssertEqual(model.guideStep, 4, "switching language must not close the card")
        XCTAssertTrue(S.Guide.policySay.contains("还没有协作规则"))
        L10n.shared.preference = .english
    }
}

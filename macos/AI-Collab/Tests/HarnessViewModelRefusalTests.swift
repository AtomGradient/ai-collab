// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import XCTest
@testable import AICollab

/// Covers the controls that used to fail silently. Every case here reaches its
/// decision before any IPC call, so no Host and no fake client are involved:
/// if one of these ever regressed into calling out, the test would hang or throw
/// rather than pass.
@MainActor
final class HarnessViewModelRefusalTests: XCTestCase {

    override func setUp() {
        super.setUp()
        // Copy assertions are language-exact; pin English regardless of the
        // machine locale, and restore the employee preference afterwards.
        previousLanguage = L10n.shared.preference
        L10n.shared.preference = .english
    }

    override func tearDown() {
        L10n.shared.preference = previousLanguage
        super.tearDown()
    }

    private var previousLanguage: AppLanguage = .system
    private func participant(
        _ id: String,
        observed: String,
        runtimeBinding: String? = "runtime-1"
    ) -> ParticipantRecord {
        guard
            let record = ParticipantRecord([
                "participant_id": id,
                "participant_generation": 1,
                "state_revision": 3,
                "desired_state": "running",
                "observed_state": observed,
                "runtime_binding_id": runtimeBinding as Any,
            ])
        else {
            fatalError("fixture participant could not be built")
        }
        return record
    }

    /// A model that has cleared the "no project selected" guard, so later guards
    /// in the same function are actually reachable.
    private func modelWithProject() -> HarnessViewModel {
        guard
            let project = ProjectRecord([
                "project_instance_id": "proj-1",
                "project_key": "edge-studio",
                "project_binding_digest": String(repeating: "a", count: 64),
                "product_contract_version": "3",
            ])
        else {
            fatalError("fixture project could not be built")
        }
        let model = HarnessViewModel()
        model.projects = [project]
        model.selectedProjectID = project.id
        return model
    }

    /// Clears both the project and the Scenario guards.
    private func modelWithScenario() -> HarnessViewModel {
        let model = modelWithProject()
        model.scenarios = [scenario("room-1")]
        model.selectedScenarioID = "room-1"
        return model
    }

    private func scenario(_ id: String, observed: String = "running") -> ScenarioRecord {
        guard
            let record = ScenarioRecord([
                "scenario_id": id,
                "scenario_generation": 1,
                "state_revision": 7,
                "desired_state": "running",
                "observed_state": observed,
                "workspace_binding_id": "ws-1",
                "participant_ids": [String](),
            ])
        else {
            fatalError("fixture scenario could not be built")
        }
        return record
    }

    // MARK: - Preconditions mirror the Host

    /// `begin_participant_start` in the Host store accepts only stopped/detached.
    func testCanStartMatchesTheHostAcceptedStates() {
        for state in ["stopped", "detached"] {
            XCTAssertTrue(participant("p", observed: state).canStart, "\(state) should start")
        }
        for state in ["ready", "degraded", "starting", "stopping", "replacing"] {
            XCTAssertFalse(participant("p", observed: state).canStart, "\(state) must not offer start")
        }
    }

    /// `begin_participant_stop` accepts only ready/degraded. "running" is the
    /// trap: it reads like a stoppable state but the Host rejects it, so offering
    /// Stop there would render a control that can only fail.
    func testCanStopExcludesRunning() {
        for state in ["ready", "degraded"] {
            XCTAssertTrue(participant("p", observed: state).canStop, "\(state) should stop")
        }
        XCTAssertFalse(
            participant("p", observed: "running").canStop,
            "the Host rejects stopping a running record; the control must not appear"
        )
        for state in ["stopped", "detached", "stopping"] {
            XCTAssertFalse(participant("p", observed: state).canStop, "\(state) must not offer stop")
        }
    }

    // MARK: - Nothing refuses silently

    func testCreateScenarioWithoutAProjectSaysWhy() async {
        let model = HarnessViewModel()
        await model.createScenario()
        XCTAssertEqual(
            model.validationMessage(for: .scenarioCreate),
            "Register or select a project first."
        )
        XCTAssertFalse(model.isBusy)
    }

    /// Uses a model that already has a project, otherwise the earlier guard fires
    /// and this never exercises the blank-name branch at all.
    func testCreateScenarioWithABlankNameSaysWhy() async {
        let model = modelWithProject()
        model.newScenarioID = "   "
        await model.createScenario()
        XCTAssertEqual(
            model.validationMessage(for: .scenarioCreate),
            "Give the task room a name."
        )
    }

    func testCreateScenarioRefusesADuplicateNameBeforeCallingOut() async {
        let model = modelWithProject()
        model.scenarios = [scenario("room-1")]
        model.newScenarioID = "room-1"
        await model.createScenario()
        XCTAssertEqual(
            model.validationMessage(for: .scenarioCreate),
            "This project already has a task room named “room-1”."
        )
    }

    func testStartIsRefusedWithAReasonWhenTheHostWouldReject() async {
        let model = modelWithScenario()
        await model.startParticipant(participant("analyst", observed: "ready"))
        let reason = model.validationMessage(for: .participantAction)
        XCTAssertEqual(
            reason,
            "analyst is “Ready”; only a stopped colleague can be started."
        )
        XCTAssertFalse(reason?.contains("_") ?? true, "the reason must not leak a machine state")
    }

    func testStopIsRefusedWithAReasonForAStoppedParticipant() async {
        let model = modelWithScenario()
        await model.stopParticipant(participant("analyst", observed: "stopped"))
        XCTAssertEqual(
            model.validationMessage(for: .participantAction),
            "analyst is “Stopped”; there is nothing to stop."
        )
    }

    func testForceStopWithoutAnOwnedProcessSaysWhy() async {
        let model = modelWithScenario()
        await model.forceStopParticipant(
            participant("analyst", observed: "ready", runtimeBinding: nil)
        )
        XCTAssertEqual(
            model.validationMessage(for: .participantAction),
            "analyst has no Harness-owned process to force stop."
        )
    }

    func testAddParticipantWithoutAScenarioSaysWhy() async {
        let model = HarnessViewModel()
        await model.addParticipant()
        XCTAssertEqual(
            model.validationMessage(for: .participantAdd),
            "Select a task room first."
        )
    }

    func testAddParticipantWithABlankNameSaysWhy() async {
        let model = modelWithScenario()
        guard
            let template = ParticipantTemplate([
                "template_id": "tmpl-claude",
                "display_name": "Claude",
                "launch_spec": ["runtime_profile_ref": "claude-cli"],
            ])
        else {
            return XCTFail("fixture template could not be built")
        }
        model.templates = [template]
        model.selectedTemplateID = template.id
        model.newParticipantID = "  "
        await model.addParticipant()
        XCTAssertEqual(
            model.validationMessage(for: .participantAdd),
            "Give the colleague a name."
        )
    }

    /// Reaches the preview guard specifically; a model without a Scenario would
    /// stop at the earlier guard and never exercise this branch.
    func testDestroyWithoutAPreviewSaysWhy() async {
        let model = modelWithScenario()
        XCTAssertFalse(model.destroyPreviewEligible)
        await model.destroyScenario()
        XCTAssertEqual(
            model.validationMessage(for: .scenarioLifecycle),
            "Load the destroy preview first so the exact effect is known."
        )
    }

    /// The branch that used to return silently while the button stayed enabled:
    /// the Scenario moved on after the plan preview was taken.
    func testApplyingAStalePlanExplainsThatItWentStale() async {
        let model = modelWithScenario()
        guard
            let template = PolicyTemplateRecord([
                "template_id": "tmpl-pair",
                "display_name": "Pair",
                "participant_ids": ["a", "b"],
            ]),
            let plan = PolicyPlanRecord([
                "template_snapshot": ["template_id": "tmpl-pair"],
                "plan_digest": String(repeating: "b", count: 64),
                // The Scenario fixture is at revision 7; this plan was taken at
                // an older one, which is exactly the stale case.
                "scenario": [
                    "scenario_generation": 1,
                    "scenario_state_revision": 999,
                ],
                "can_apply": true,
                "team": [Any](),
                "route_effects": [Any](),
                "blockers": [String](),
            ])
        else {
            return XCTFail("policy fixtures could not be built")
        }
        model.policyTemplates = [template]
        model.selectedPolicyTemplateID = template.id
        model.policyPlan = plan

        await model.applySelectedPolicyPlan()

        XCTAssertEqual(
            model.validationMessage(for: .policy),
            "The room changed after this plan was previewed. "
                + "Preview it again to pick up the current state."
        )
    }

    // MARK: - A refusal is scoped to the control that refused

    func testARefusalDoesNotSurfaceUnderAnUnrelatedControl() async {
        let model = HarnessViewModel()
        await model.addParticipant()
        XCTAssertNotNil(model.validationMessage(for: .participantAdd))
        XCTAssertNil(
            model.validationMessage(for: .scenarioCreate),
            "a participant refusal must not render beside the Scenario name field"
        )
        XCTAssertNil(model.validationMessage(for: .policy))
    }

    func testDismissClearsEachChannelIndependently() async {
        let model = HarnessViewModel()
        await model.addParticipant()
        XCTAssertNotNil(model.validationMessage(for: .participantAdd))
        model.dismissValidation()
        XCTAssertNil(model.validationMessage(for: .participantAdd))

        model.noteSuccess("done")
        model.dismissSuccess()
        XCTAssertNil(model.successMessage)

        model.errorMessage = "boom"
        model.dismissError()
        XCTAssertNil(model.errorMessage)
        XCTAssertNil(model.actionableError)
    }

    // MARK: - Human-facing copy

    /// Every state the Host store can emit must have plain-language copy; a
    /// missing case would otherwise reach the window as `provision_failed`.
    func testHumanStateCoversEveryHostStateWithoutSnakeCase() {
        let hostStates = [
            "ready", "running", "stopped", "detached", "closed", "closing",
            "opening", "degraded", "provision_failed", "provisioning",
            "starting", "stopping", "recovering", "repairing", "replacing",
            "destroying",
        ]
        for state in hostStates {
            let text = HarnessViewModel.humanState(state)
            XCTAssertFalse(text.contains("_"), "\(state) still reads as a machine identifier")
            XCTAssertFalse(text.isEmpty)
        }
    }

    func testPendingScenarioRecoveryIsExplicitlyVisible() {
        let model = HarnessViewModel()
        model.selectedScenarioID = "room-1"

        for (state, label) in [
            ("repairing", "Resuming repair"),
            ("destroying", "Resuming deletion"),
        ] {
            model.scenarios = [scenario("room-1", observed: state)]
            XCTAssertEqual(HarnessViewModel.humanState(state), label)
            XCTAssertEqual(model.scenarioHeadline, "\(label) · no colleagues yet")
        }
    }

    func testScenarioHeadlineReplacesTheDesiredObservedPair() {
        let model = HarnessViewModel()
        XCTAssertEqual(model.scenarioHeadline, "No Task Room selected")

        model.scenarios = [scenario("room-1", observed: "provision_failed")]
        model.selectedScenarioID = "room-1"
        model.participants = [
            participant("a", observed: "ready"),
            participant("b", observed: "stopped"),
        ]

        let headline = model.scenarioHeadline
        XCTAssertEqual(headline, "Workspace setup failed · 1 of 2 colleagues working")
        XCTAssertFalse(headline.contains("desired"))
        XCTAssertFalse(headline.contains("observed"))
        XCTAssertEqual(model.runningParticipantCount, 1)
    }

    func testHeadlineReadsNaturallyForTheCommonCases() {
        let model = HarnessViewModel()
        model.scenarios = [scenario("room-1", observed: "ready")]
        model.selectedScenarioID = "room-1"

        model.participants = []
        XCTAssertEqual(model.scenarioHeadline, "Ready · no colleagues yet")

        model.participants = [participant("a", observed: "ready")]
        XCTAssertEqual(model.scenarioHeadline, "Ready · 1 colleague working")

        model.participants = [
            participant("a", observed: "ready"),
            participant("b", observed: "ready"),
        ]
        XCTAssertEqual(model.scenarioHeadline, "Ready · all 2 colleagues working")

        model.participants = [participant("a", observed: "stopped")]
        XCTAssertEqual(model.scenarioHeadline, "Ready · 1 colleague, none working")
    }
}

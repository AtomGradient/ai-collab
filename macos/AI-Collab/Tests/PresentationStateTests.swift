// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import XCTest
@testable import AICollab

/// Pins the entity-aware six-class mapping from the Phase 1 redesign
/// (review 20260903-175908-nyr2wy / 20260903-181141-6gjonu /
/// 20260903-182157-vr9ppg). The two properties under test replaced a single
/// global `String -> Color` switch (`StateBadge`) precisely because that
/// switch got a real semantic wrong: it painted a `ready` Participant green,
/// the same colour as a `passed` Preflight check, even though `ready` means
/// the TUI is running and available — not settled. These tests exist to
/// keep that specific regression from coming back, not just to exercise the
/// happy path.
final class PresentationStateTests: XCTestCase {

    private func scenario(_ observed: String) -> ScenarioRecord {
        ScenarioRecord([
            "scenario_id": "room-1",
            "scenario_generation": 1,
            "state_revision": 1,
            "desired_state": "running",
            "observed_state": observed,
            "workspace_binding_id": "ws-1",
            "participant_ids": [String](),
            "objective": "",
            "objective_history": [[String: Any]](),
        ])!
    }

    private func participant(_ observed: String) -> ParticipantRecord {
        ParticipantRecord([
            "participant_id": "analyst",
            "participant_generation": 1,
            "state_revision": 1,
            "desired_state": "running",
            "observed_state": observed,
        ])!
    }

    // MARK: - Scenario

    func testScenarioTransitionalAndRunningStatesAreWorking() {
        for state in ["provisioning", "opening", "closing", "destroying", "running"] {
            XCTAssertEqual(
                scenario(state).presentationClass, .working,
                "\(state) must present as working"
            )
        }
    }

    func testScenarioDegradedIsAttentionNotFailed() {
        XCTAssertEqual(scenario("degraded").presentationClass, .attention)
    }

    func testScenarioProvisionFailedIsFailed() {
        XCTAssertEqual(scenario("provision_failed").presentationClass, .failed)
    }

    /// The regression this whole suite exists for: a closed room must read
    /// as neutral, never as a green "done" the product cannot actually back
    /// with a completed state.
    func testScenarioClosedIsInactiveNeverSuccess() {
        XCTAssertEqual(scenario("closed").presentationClass, .inactive)
    }

    func testUnrecognizedScenarioStateFailsClosedToAttention() {
        XCTAssertEqual(scenario("some-future-state").presentationClass, .attention)
    }

    // MARK: - Participant

    func testParticipantReadyIsWorkingNotSuccess() {
        XCTAssertEqual(
            participant("ready").presentationClass, .working,
            "ready means the TUI is running and available, not settled"
        )
    }

    func testParticipantTransitionalStatesAreWorking() {
        for state in ["starting", "stopping", "recovering", "repairing", "replacing"] {
            XCTAssertEqual(
                participant(state).presentationClass, .working,
                "\(state) must present as working"
            )
        }
    }

    func testParticipantDegradedIsAttentionRegardlessOfReason() {
        // A launch failure is the concrete m2 case from review vr9ppg: it
        // surfaces as `degraded`, never as a standalone `failed` Participant
        // state, because none exists.
        let launchFailed = ParticipantRecord([
            "participant_id": "reviewer",
            "participant_generation": 1,
            "state_revision": 1,
            "desired_state": "running",
            "observed_state": "degraded",
            "degraded": ["reason": "launch_failed"],
        ])!
        XCTAssertEqual(launchFailed.presentationClass, .attention)
        XCTAssertNotEqual(launchFailed.presentationClass, .failed)
    }

    func testParticipantStoppedAndDetachedAreInactiveNeverSuccess() {
        XCTAssertEqual(participant("stopped").presentationClass, .inactive)
        XCTAssertEqual(participant("detached").presentationClass, .inactive)
    }

    func testUnrecognizedParticipantStateFailsClosedToAttention() {
        XCTAssertEqual(participant("some-future-state").presentationClass, .attention)
    }

    /// Neither entity has a Host-verified positive outcome to report —
    /// `.success` is reserved for Delivery/Preflight/Permission/Resource.
    /// Asserted over the full known vocabulary so a future case added to
    /// either switch cannot silently start claiming success.
    func testNeitherScenarioNorParticipantEverClaimsSuccess() {
        let scenarioStates = [
            "provisioning", "opening", "running", "degraded", "provision_failed",
            "closed", "closing", "destroying",
        ]
        for state in scenarioStates {
            XCTAssertNotEqual(scenario(state).presentationClass, .success, state)
        }
        let participantStates = [
            "starting", "ready", "stopping", "degraded", "stopped", "detached",
            "recovering", "repairing", "replacing",
        ]
        for state in participantStates {
            XCTAssertNotEqual(participant(state).presentationClass, .success, state)
        }
    }
}

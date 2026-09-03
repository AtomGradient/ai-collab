// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import XCTest
@testable import AICollab

/// Pins the entity-aware six-class mapping from the Phase 1 redesign
/// (review 20260903-175908-nyr2wy / 20260903-181141-6gjonu /
/// 20260903-182157-vr9ppg / 20260903-183736-clqu6r). The two properties
/// under test replaced a single global `String -> Color` switch (`StateBadge`)
/// precisely because that switch got a real semantic wrong: it painted a
/// `ready` Participant green, the same colour as a `passed` Preflight check,
/// even though `ready` means the TUI is running and available — not settled.
///
/// codex review clqu6r P1-1: an earlier version of this file hand-copied a
/// state list that did not match the contract (missing Scenario `repairing`,
/// missing Participant `destroying`, and wrongly including `repairing` as a
/// valid Participant state). Every list below is transcribed directly from
/// `contracts/scenario_participant_state_v1.schema.json`
/// (`$defs/scenario_observed_state`, `$defs/participant_observed_state`) and
/// each test iterates the full enum, not a hand-picked subset, so a future
/// drift between this file and the schema fails loudly instead of silently.
final class PresentationStateTests: XCTestCase {

    /// `$defs/scenario_observed_state` — all 9 values.
    private static let scenarioObservedStates = [
        "provisioning", "provision_failed", "closed", "opening", "running",
        "degraded", "repairing", "closing", "destroying",
    ]

    /// `$defs/participant_observed_state` — all 9 values. Deliberately does
    /// NOT include "repairing": that token is Scenario-only in the contract.
    private static let participantObservedStates = [
        "detached", "stopped", "starting", "ready", "stopping", "recovering",
        "replacing", "destroying", "degraded",
    ]

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

    // MARK: - Scenario: exhaustive over the contract's full enum

    private static let expectedScenarioClass: [String: PresentationClass] = [
        "provisioning": .working,
        "opening": .working,
        "running": .working,
        "repairing": .working,
        "closing": .working,
        "destroying": .working,
        "degraded": .attention,
        "provision_failed": .failed,
        "closed": .inactive,
    ]

    func testEveryContractScenarioStateMapsToItsExpectedClass() {
        XCTAssertEqual(
            Set(Self.expectedScenarioClass.keys), Set(Self.scenarioObservedStates),
            "the expectation table and the transcribed contract enum must name the same states"
        )
        for state in Self.scenarioObservedStates {
            XCTAssertEqual(
                scenario(state).presentationClass, Self.expectedScenarioClass[state],
                state
            )
        }
    }

    /// The exact regression this suite exists for: a room the user just
    /// clicked Repair on must keep reading as in-progress, not flip back to
    /// looking freshly broken.
    func testScenarioRepairingIsWorkingNotAttention() {
        XCTAssertEqual(scenario("repairing").presentationClass, .working)
    }

    func testUnrecognizedScenarioStateFailsClosedToAttention() {
        XCTAssertFalse(Self.scenarioObservedStates.contains("some-future-state"))
        XCTAssertEqual(scenario("some-future-state").presentationClass, .attention)
    }

    // MARK: - Participant: exhaustive over the contract's full enum

    private static let expectedParticipantClass: [String: PresentationClass] = [
        "starting": .working,
        "ready": .working,
        "stopping": .working,
        "recovering": .working,
        "replacing": .working,
        "destroying": .working,
        "degraded": .attention,
        "stopped": .inactive,
        "detached": .inactive,
    ]

    func testEveryContractParticipantStateMapsToItsExpectedClass() {
        XCTAssertEqual(
            Set(Self.expectedParticipantClass.keys), Set(Self.participantObservedStates),
            "the expectation table and the transcribed contract enum must name the same states"
        )
        for state in Self.participantObservedStates {
            XCTAssertEqual(
                participant(state).presentationClass, Self.expectedParticipantClass[state],
                state
            )
        }
    }

    /// The exact regression this suite exists for: deleting a colleague must
    /// not flash it orange as if something had gone wrong.
    func testParticipantDestroyingIsWorkingNotAttention() {
        XCTAssertEqual(participant("destroying").presentationClass, .working)
    }

    func testParticipantReadyIsWorkingNotSuccess() {
        XCTAssertEqual(
            participant("ready").presentationClass, .working,
            "ready means the TUI is running and available, not settled"
        )
    }

    /// `repairing` is valid for a Scenario but was never valid for a
    /// Participant — codex review clqu6r P1-1 caught the old mapping
    /// accepting it anyway. It must now fail closed like any other token
    /// outside the Participant contract.
    func testRepairingIsNotAValidParticipantStateAndFailsClosed() {
        XCTAssertFalse(Self.participantObservedStates.contains("repairing"))
        XCTAssertEqual(participant("repairing").presentationClass, .attention)
    }

    func testParticipantDegradedIsAttentionRegardlessOfReason() {
        // A launch failure is the concrete m2 case from review vr9ppg: it
        // surfaces as `degraded`, never as a standalone `failed` Participant
        // state, because none exists in the contract.
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

    func testUnrecognizedParticipantStateFailsClosedToAttention() {
        XCTAssertFalse(Self.participantObservedStates.contains("some-future-state"))
        XCTAssertEqual(participant("some-future-state").presentationClass, .attention)
    }

    /// Neither entity has a Host-verified positive outcome to report —
    /// `.success` is reserved for Delivery/Preflight/Permission/Resource.
    /// Asserted over the full contract enum so a future case added to either
    /// switch cannot silently start claiming success.
    func testNeitherScenarioNorParticipantEverClaimsSuccess() {
        for state in Self.scenarioObservedStates {
            XCTAssertNotEqual(scenario(state).presentationClass, .success, state)
        }
        for state in Self.participantObservedStates {
            XCTAssertNotEqual(participant(state).presentationClass, .success, state)
        }
    }
}

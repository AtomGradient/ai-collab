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

    // MARK: - Delivery / Preflight / Permission / Topology / Lease / Policy
    // (v2 finishes the approved entity-aware table, claude reply
    // 20260903-182112-nymtlc: the evidence badges used the old global
    // colour switch until now.)

    private func delivery(_ state: String, degradedReason: String? = nil) -> DeliveryRecord {
        var value: [String: Any] = [
            "delivery_id": "delivery-1",
            "enqueue_sequence": 7,
            "message_kind": "collaboration.review-request",
            "sender": ["participant_id": "analyst", "participant_generation": 1],
            "receiver": ["participant_id": "reviewer", "participant_generation": 1],
            "thread_root_delivery_id": "delivery-1",
            "state": state,
            "event_sequence": 3,
            "last_event": ["event": "consumed", "attempt_number": 1],
            "retry_eligibility": ["eligible": false, "reason": "n/a"],
        ]
        if let degradedReason { value["degraded_reason"] = degradedReason }
        return DeliveryRecord(value)!
    }

    /// The App's own `S.Delivery.stateLabel` switch and the Host's
    /// delivery.state enum: queued, delivery_attempted, delivered, consumed,
    /// recipient_deleted (contracts/collaboration_policy_delivery_v1).
    private static let deliveryStates = [
        "queued", "delivery_attempted", "delivered", "consumed", "recipient_deleted",
    ]

    private static let expectedDeliveryClass: [String: PresentationClass] = [
        "queued": .working,
        "delivery_attempted": .working,
        "delivered": .success,
        "consumed": .success,
        "recipient_deleted": .attention,
    ]

    func testEveryDeliveryStateMapsToItsExpectedClass() {
        XCTAssertEqual(Set(Self.expectedDeliveryClass.keys), Set(Self.deliveryStates))
        for state in Self.deliveryStates {
            XCTAssertEqual(delivery(state).presentationClass, Self.expectedDeliveryClass[state], state)
        }
    }

    /// A delivered-then-degraded record is not a success story: the
    /// degraded reason wins over the state token.
    func testDegradedDeliveryIsAttentionEvenWhenDelivered() {
        XCTAssertEqual(delivery("delivered", degradedReason: "route_refused").presentationClass, .attention)
        XCTAssertEqual(delivery("consumed", degradedReason: "route_refused").presentationClass, .attention)
    }

    func testUnrecognizedDeliveryStateFailsClosedToAttention() {
        XCTAssertEqual(delivery("some-future-state").presentationClass, .attention)
    }

    /// `PreflightCheckRecord.status` / `ScenarioPreflightRecord.status`:
    /// ready | blocked | not_required (HarnessModels.swift guards).
    func testPreflightStatusesMapAcrossAllThreeClasses() {
        XCTAssertEqual(PresentationClass.preflight("ready"), .success)
        XCTAssertEqual(PresentationClass.preflight("blocked"), .attention)
        XCTAssertEqual(PresentationClass.preflight("not_required"), .inactive)
        XCTAssertEqual(PresentationClass.preflight("weird"), .attention)
    }

    /// `PermissionObservationRecord.status`: granted | denied |
    /// not_determined | restricted | unavailable | unknown — all six, and
    /// `not_determined` is the product's only `waiting`.
    func testPermissionStatusesCoverAllSixContractValues() {
        let expected: [String: PresentationClass] = [
            "granted": .success,
            "not_determined": .waiting,
            "denied": .failed,
            "restricted": .failed,
            "unavailable": .attention,
            "unknown": .attention,
        ]
        for (status, cls) in expected {
            XCTAssertEqual(PresentationClass.permission(status), cls, status)
        }
        XCTAssertEqual(
            expected.values.filter { $0 == .waiting }.count, 1,
            "not_determined is the single waiting state in the whole product"
        )
    }

    /// `PresentationTopologyRecord.health`: ready | degraded | not_running |
    /// not_required. A ready window is an operating state — working, never
    /// success — exactly like a ready Participant.
    func testTopologyHealthReadyIsWorkingNotSuccess() {
        XCTAssertEqual(PresentationClass.topologyHealth("ready"), .working)
        XCTAssertEqual(PresentationClass.topologyHealth("degraded"), .attention)
        XCTAssertEqual(PresentationClass.topologyHealth("not_running"), .inactive)
        XCTAssertEqual(PresentationClass.topologyHealth("not_required"), .inactive)
        XCTAssertEqual(PresentationClass.topologyHealth("weird"), .attention)
    }

    /// `ResourceLeaseRecord.status`: active | stale | released — released is
    /// over, not a success (same reading as a closed Scenario).
    func testResourceLeaseReleasedIsInactiveNotSuccess() {
        XCTAssertEqual(PresentationClass.resourceLease("active"), .working)
        XCTAssertEqual(PresentationClass.resourceLease("stale"), .attention)
        XCTAssertEqual(PresentationClass.resourceLease("released"), .inactive)
        XCTAssertEqual(PresentationClass.resourceLease("weird"), .attention)
    }

    func testPolicyStatusAndPlanClasses() {
        XCTAssertEqual(PresentationClass.policy(requiresReplan: false), .success)
        XCTAssertEqual(PresentationClass.policy(requiresReplan: true), .attention)
        XCTAssertEqual(PresentationClass.policyPlan(canApply: true), .success)
        XCTAssertEqual(PresentationClass.policyPlan(canApply: false), .attention)
    }
}

// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import CryptoKit
import XCTest
@testable import AICollab

final class HarnessContractTests: XCTestCase {
    func testCanonicalJSONMatchesHostOrderingAndEscaping() throws {
        let value: [String: Any] = ["z": "路径/ok", "a": [2, 1], "flag": true]
        let data = try HarnessIPCClient.canonicalJSON(value)
        XCTAssertEqual(String(decoding: data, as: UTF8.self), #"{"a":[2,1],"flag":true,"z":"路径/ok"}"#)
    }

    func testGeneratedRegistryIncludesAppOperations() {
        XCTAssertEqual(HarnessContract.operationRegistryDigest.count, 64)
        XCTAssertEqual(HarnessContract.capabilities["project.register"], "project.manage")
        XCTAssertEqual(HarnessContract.capabilities["project.unregister"], "project.manage")
        XCTAssertEqual(HarnessContract.capabilities["project.bootstrap"], "project.manage")
        XCTAssertEqual(HarnessContract.capabilities["participant.list"], "participant.read")
        XCTAssertEqual(HarnessContract.capabilities["participant.recover"], "participant.manage")
        XCTAssertEqual(HarnessContract.capabilities["participant.replace"], "participant.manage")
        XCTAssertEqual(HarnessContract.capabilities["participant.force-stop"], "participant.force-stop")
        XCTAssertEqual(HarnessContract.capabilities["scenario.repair"], "scenario.repair")
        XCTAssertEqual(HarnessContract.capabilities["scenario.destroy"], "scenario.destroy")
        XCTAssertEqual(HarnessContract.capabilities["scenario.force-destroy"], "scenario.destroy")
        XCTAssertEqual(HarnessContract.capabilities["scenario.preflight"], "scenario.read")
        XCTAssertEqual(HarnessContract.capabilities["resource.break"], "resource.break")
        XCTAssertEqual(HarnessContract.capabilities["policy.template.list"], "policy.read")
        XCTAssertEqual(HarnessContract.capabilities["policy.plan"], "policy.read")
        XCTAssertEqual(HarnessContract.capabilities["policy.apply-plan"], "policy.manage")
        XCTAssertEqual(HarnessContract.capabilities["delivery.list"], "delivery.read")
        XCTAssertEqual(HarnessContract.capabilities["delivery.retry"], "delivery.send")
        XCTAssertNil(HarnessContract.capabilities["message.send"])
        XCTAssertNil(HarnessContract.capabilities["message.send-self"])
        XCTAssertNil(HarnessContract.capabilities["message.reply-self"])
        XCTAssertNil(HarnessContract.capabilities["delivery.status"])
        XCTAssertNil(HarnessContract.capabilities["delivery.consume"])
        XCTAssertEqual(
            Set(HarnessContract.confirmationPolicies.keys),
            Set([
                "scenario.repair",
                "scenario.destroy",
                "scenario.force-destroy",
                "participant.force-stop",
                "resource.break",
            ])
        )
        XCTAssertTrue(
            HarnessContract.confirmationPolicies.values.allSatisfy {
                $0 == "confirmation.destructive-once"
            }
        )
    }

    func testLongRunningCallCanUseHostOperationBudget() {
        let regular = HarnessCall(operation: "host.status", target: ["scope": "host"])
        let longRunning = HarnessCall(
            operation: "workspace.provision",
            target: ["scope": "scenario"],
            responseTimeoutSeconds: 360
        )

        XCTAssertEqual(regular.responseTimeoutSeconds, 60)
        XCTAssertEqual(longRunning.responseTimeoutSeconds, 360)
    }

    func testPublicModelsDoNotNeedCanonicalProjectPath() {
        let project = ProjectRecord([
            "project_instance_id": "project-1",
            "project_key": "edgestudio",
            "project_binding_digest": String(repeating: "a", count: 64),
            "product_contract_version": "3.2",
        ])
        XCTAssertEqual(project?.id, "project-1")
    }

    func testServiceStatusLabelsAreEmployeeReadable() {
        XCTAssertEqual(HarnessServiceStatus.enabled.label, "enabled")
        XCTAssertEqual(HarnessServiceStatus.requiresApproval.label, "approval required")
        XCTAssertEqual(HarnessServiceStatus.notRegistered.label, "not registered")
    }

    func testPolicyPlanAndGenerationDriftModelsAreTyped() throws {
        let plan = try XCTUnwrap(PolicyPlanRecord([
            "template_snapshot": ["template_id": "team.peer-review"],
            "plan_digest": String(repeating: "a", count: 64),
            "can_apply": true,
            "blockers": [],
            "scenario": ["scenario_generation": 2, "scenario_state_revision": 7],
            "policy_pack": ["policy_version": 3],
            "team": [
                [
                    "participant_id": "analyst",
                    "participant_generation": 4,
                    "present": true,
                ],
                [
                    "participant_id": "reviewer",
                    "participant_generation": NSNull(),
                    "present": false,
                ],
            ],
            "route_effects": [[
                "rule_id": "analyst-to-reviewer",
                "message_kind": "collaboration.request",
                "effect": "allow",
                "sender_participants": ["analyst"],
                "receiver_participants": ["reviewer"],
                "retry_profile": ["max_attempts": 2],
            ]],
        ]))
        XCTAssertEqual(plan.templateID, "team.peer-review")
        XCTAssertEqual(plan.team.map(\.participantID), ["analyst", "reviewer"])
        XCTAssertEqual(plan.team[0].generation, 4)
        XCTAssertNil(plan.team[1].generation)
        XCTAssertEqual(plan.routeEffects[0].maxAttempts, 2)

        let status = try XCTUnwrap(PolicyStatusRecord([
            "policy": ["policy_id": "policy.peer-review", "policy_version": 3],
            "policy_health": [
                "requires_replan": true,
                "generation_drift": [[
                    "participant_id": "reviewer",
                    "policy_generation": 1,
                    "current_generation": 2,
                ]],
            ],
        ]))
        XCTAssertTrue(status.requiresReplan)
        XCTAssertEqual(status.generationDrift.first?.participantID, "reviewer")
    }

    func testDeliveryCollectionModelContainsOnlyControlPlaneProjection() throws {
        let delivery: [String: Any] = [
            "delivery_id": "delivery-one",
            "message_kind": "collaboration.request",
            "sender": ["participant_id": "analyst", "participant_generation": 1],
            "receiver": ["participant_id": "reviewer", "participant_generation": 2],
            "thread_root_delivery_id": "delivery-one",
            "reply_to_delivery_id": NSNull(),
            "state": "delivery_attempted",
            "degraded_reason": "transport.unavailable",
            "event_sequence": 2,
            "last_event": ["event": "attempt_failed"],
            "retry_eligibility": [
                "eligible": true,
                "event_sequence": 2,
                "reason": "delivery.retry-available",
            ],
        ]
        let collection = try XCTUnwrap(DeliveryCollectionRecord([
            "summary": ["total": 1, "states": ["delivery_attempted": 1]],
            "deliveries": [delivery],
            "next_page": [
                "after_delivery_id": "delivery-one",
                "collection_digest": String(repeating: "b", count: 64),
            ],
        ]))
        XCTAssertEqual(collection.total, 1)
        XCTAssertEqual(collection.states, ["delivery_attempted": 1])
        XCTAssertEqual(collection.deliveries.first?.sender.participantID, "analyst")
        XCTAssertTrue(collection.deliveries.first?.retryEligible == true)
        XCTAssertEqual(collection.nextPage?.afterDeliveryID, "delivery-one")
    }

    func testDegradedParticipantAndStaleResourceModelsExposeExactRepairActions() throws {
        let participant = try XCTUnwrap(ParticipantRecord([
            "participant_id": "reviewer",
            "participant_generation": 3,
            "state_revision": 8,
            "desired_state": "stopped",
            "observed_state": "degraded",
            "runtime_binding_id": "runtime-binding-3",
            "presentation_binding_id": NSNull(),
            "degraded": [
                "reason": "cleanup_pending",
                "cleanup_pending": true,
                "repair_action": "participant.recover",
            ],
        ]))
        XCTAssertTrue(participant.canRecover)
        XCTAssertTrue(participant.canForceStop)
        XCTAssertTrue(participant.cleanupPending)

        let resource = try XCTUnwrap(ResourceLeaseRecord([
            "lease_id": "lease-stale-one",
            "lease_revision": 4,
            "resource_class": "exclusive_runtime",
            "status": "stale",
            "stale_reason": "observation_failed",
            "holder": [
                "participant_id": "reviewer",
                "participant_generation": 3,
            ],
        ]))
        XCTAssertTrue(resource.canBreak)
        XCTAssertEqual(resource.participantID, "reviewer")
    }

    func testParticipantConfigurationShowsExactGenerationProfileAndModel() throws {
        let participant = try XCTUnwrap(ParticipantRecord(
            [
                "participant_id": "analyst",
                "participant_generation": 4,
                "state_revision": 9,
                "desired_state": "running",
                "observed_state": "ready",
                "runtime_binding_id": "runtime-binding-4",
                "presentation_binding_id": "presentation-binding-4",
                "degraded": NSNull(),
            ],
            configuration: [
                "participant_id": "analyst",
                "participant_generation": 4,
                "runtime_profile_ref": "runtime-profile.codex",
                "continuity_mode": "exact_resume",
                "model_binding": [
                    "provider_profile_ref": "provider.openai-local",
                    "model_ref": "model.codex-current",
                    "inference_profile_ref": "inference.research",
                ],
            ]
        ))
        XCTAssertEqual(participant.generation, 4)
        XCTAssertEqual(participant.runtimeProfileRef, "runtime-profile.codex")
        XCTAssertEqual(participant.continuityMode, "exact_resume")
        XCTAssertEqual(participant.modelBinding?.modelRef, "model.codex-current")
        XCTAssertEqual(
            participant.modelBinding?.providerProfileRef,
            "provider.openai-local"
        )
        XCTAssertEqual(
            participant.modelBinding?.inferenceProfileRef,
            "inference.research"
        )
        XCTAssertNil(ParticipantRecord(
            [
                "participant_id": "analyst",
                "participant_generation": 5,
                "state_revision": 10,
                "desired_state": "running",
                "observed_state": "ready",
            ],
            configuration: [
                "participant_id": "analyst",
                "participant_generation": 4,
                "runtime_profile_ref": "runtime-profile.codex",
                "continuity_mode": "exact_resume",
                "model_binding": NSNull(),
            ]
        ))
    }

    func testPreflightAndHostErrorModelsPreserveActionableSemantics() throws {
        let preflight = try XCTUnwrap(ScenarioPreflightRecord([
            "schema_version": 1,
            "scope": [
                "project_instance_id": "project-one",
                "scenario_id": "scenario-one",
                "scenario_generation": 1,
                "scenario_state_revision": 2,
            ],
            "status": "blocked",
            "captured_at_epoch_ms": 1_000,
            "checks": [[
                "check_id": "presentation.permission",
                "status": "blocked",
                "summary": "Interactive presentation permission needs attention.",
                "repair_action": "system-settings.automation",
            ]],
            "permission_observations": [[
                "permission_id": "permission.presentation-control",
                "provider_ref": "platform.test-presentation",
                "subject_ref": "presentation.test-window",
                "status": "denied",
                "evidence_digest": String(repeating: "a", count: 64),
                "provider_error_code": "presentation.permission-denied",
                "remediation_ref": "system-settings.automation",
                "prompt_requested": false,
            ]],
            "repair_actions": ["system-settings.automation"],
            "preflight_digest": String(repeating: "b", count: 64),
        ]))
        XCTAssertEqual(preflight.status, "blocked")
        XCTAssertEqual(preflight.checks.first?.repairAction, "system-settings.automation")
        XCTAssertEqual(preflight.permissions.first?.status, "denied")
        XCTAssertFalse(preflight.permissions.first?.promptRequested ?? true)

        let failure = ActionableErrorRecord(
            HarnessIPCError.hostRejected(
                code: "operation.external-failure",
                category: "operation",
                message: "Participant launch failed.",
                retryable: true,
                mutationState: "committed",
                repairAction: "participant.recover"
            )
        )
        XCTAssertEqual(failure.mutationState, "committed")
        XCTAssertEqual(failure.repairAction, "participant.recover")
        XCTAssertTrue(failure.retryable)
    }

    func testTopologyModelPreservesIndependentParticipantOutcomes() throws {
        let topology = try XCTUnwrap(ScenarioTopologyRecord([
            "schema_version": 1,
            "action": "focus",
            "participants": [[
                "participant_id": "analyst",
                "participant_generation": 2,
                "interaction_mode": "tui",
                "health": "ready",
                "focused": true,
                "restore_outcome": "applied_exact",
                "geometry": ["x": 10, "y": 20, "width": 1200, "height": 800],
                "display_topology_fingerprint": String(repeating: "a", count: 64),
                "error_code": NSNull(),
            ], [
                "participant_id": "batch-worker",
                "participant_generation": 1,
                "interaction_mode": "headless",
                "health": "not_required",
                "focused": false,
                "restore_outcome": "not_requested",
                "geometry": NSNull(),
                "display_topology_fingerprint": NSNull(),
                "error_code": NSNull(),
            ]],
            "summary_digest": String(repeating: "b", count: 64),
        ]))
        XCTAssertEqual(topology.action, "focus")
        XCTAssertEqual(topology.participants.count, 2)
        XCTAssertEqual(topology.participants.first?.geometryLabel, "1200×800 at 10,20")
        XCTAssertEqual(topology.participants.last?.health, "not_required")
    }

    func testCollaborationCollectionsRejectPartiallyMalformedRows() {
        XCTAssertNil(PolicyPlanRecord([
            "template_snapshot": ["template_id": "team.peer-review"],
            "plan_digest": String(repeating: "a", count: 64),
            "can_apply": false,
            "blockers": ["team.participant-missing:reviewer"],
            "scenario": ["scenario_generation": 1, "scenario_state_revision": 2],
            "policy_pack": NSNull(),
            "team": [["participant_id": "reviewer"]],
            "route_effects": [],
        ]))
        XCTAssertNil(DeliveryCollectionRecord([
            "summary": ["total": 1, "states": ["queued": 1]],
            "deliveries": [["delivery_id": "delivery-with-missing-fields"]],
            "next_page": NSNull(),
        ]))
    }

    func testProjectDirectoryAccessProbeReadsDirectoryInsteadOfOpeningItAsFile() throws {
        let directory = FileManager.default.temporaryDirectory.appending(
            path: "ai-collab-directory-probe-\(UUID().uuidString)",
            directoryHint: .isDirectory
        )
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: directory) }
        try "visible".write(
            to: directory.appending(path: "marker.txt"),
            atomically: true,
            encoding: .utf8
        )

        XCTAssertNoThrow(try HarnessIPCClient().grantProjectDirectoryAccess(directory))
    }

    func testLiveHostRoundTripWhenConfigured() async throws {
        let environment = ProcessInfo.processInfo.environment
        guard
            let stateRoot = environment["AI_COLLAB_LIVE_TEST_STATE_ROOT"],
            let socketPath = environment["AI_COLLAB_LIVE_TEST_SOCKET_PATH"],
            let projectPath = environment["AI_COLLAB_LIVE_TEST_PROJECT_PATH"]
        else { throw XCTSkip("Live Harness Host is not configured") }

        let client = HarnessIPCClient(
            stateRoot: URL(filePath: stateRoot, directoryHint: .isDirectory),
            socketPath: socketPath
        )
        let status = try await client.call(
            HarnessCall(operation: "host.status", target: ["scope": "host"])
        )
        XCTAssertEqual(status["status"] as? String, "ready")

        let registration = try await client.call(
            HarnessCall(
                operation: "project.register",
                target: ["scope": "host"],
                fence: ["operation_generation": 0],
                payload: ["canonical_project_path": projectPath]
            )
        )
        guard
            let projectValue = registration["project"] as? [String: Any],
            let project = ProjectRecord(projectValue)
        else { return XCTFail("Project registration reply differs") }
        XCTAssertNil(projectValue["canonical_root"])
        XCTAssertNil(projectValue["canonical_root_fingerprint"])

        let scenarioID = "swift-live-\(UUID().uuidString.lowercased())"
        let target: [String: Any] = [
            "scope": "scenario",
            "project_instance_id": project.id,
            "scenario_id": scenarioID,
        ]
        _ = try await client.call(
            HarnessCall(
                operation: "scenario.create",
                target: target,
                fence: ["operation_generation": 0],
                payload: ["project_binding_digest": project.bindingDigest]
            )
        )
        let participants = try await client.call(
            HarnessCall(operation: "participant.list", target: target)
        )
        XCTAssertEqual(dictionaries(participants["participants"]).count, 0)
        let templates = try await client.call(
            HarnessCall(operation: "participant.template.list", target: ["scope": "host"])
        )
        XCTAssertFalse(dictionaries(templates["templates"]).isEmpty)
    }
}

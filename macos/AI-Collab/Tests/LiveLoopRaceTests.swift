// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import XCTest
@testable import AICollab

/// codex reviews 20260904-030617-82zm9p and 20260904-032455-3mzo76 (both
/// P1): a live-loop read that began before a mutation must never overwrite
/// the mutation's own post-mutation reload — whether it returns while the
/// mutation is still in flight (`isBusy` true) or only after the mutation
/// has already finished (`isBusy` false again). The second case is the one
/// a bare `!isBusy` check cannot see; `mutationEpoch` advances on both
/// boundaries of `isBusy`, and every live read carries the epoch it was
/// issued under.
///
/// These tests drive `monitorRoom` with a fake client that holds one chosen
/// reply until the test releases it, flip `isBusy` in between (the same
/// transitions `performMutation` makes), and assert on what was written —
/// ordering no source-text assertion can prove. Each suppression case has a
/// control twin with identical plumbing and no mutation, so a "nothing was
/// written" result cannot pass for the wrong reason.
@MainActor
final class LiveLoopRaceTests: XCTestCase {

    /// Holds the reply to one operation until released; every reply carries
    /// a value that differs from the model's starting state, so a write is
    /// observable.
    private final class FakeHarnessClient: HarnessCalling, @unchecked Sendable {
        let held: String
        let failHeld: Bool
        let requested: AsyncStream<Void>
        let release: AsyncStream<Void>
        private let requestedContinuation: AsyncStream<Void>.Continuation
        private let releaseContinuation: AsyncStream<Void>.Continuation

        init(holding held: String, failHeld: Bool = false) {
            self.held = held
            self.failHeld = failHeld
            (requested, requestedContinuation) = AsyncStream<Void>.makeStream()
            (release, releaseContinuation) = AsyncStream<Void>.makeStream()
        }

        func releaseHeldReply() { releaseContinuation.yield() }

        func grantProjectDirectoryAccess(_ url: URL) throws {}

        func call(
            _ call: HarnessCall,
            progress: (@Sendable (HarnessProgress) -> Void)?
        ) async throws -> [String: Any] {
            if call.operation == held {
                requestedContinuation.yield()
                for await _ in release { break }
                if failHeld { throw HarnessIPCError.hostUnavailable }
            }
            switch call.operation {
            case "delivery.list":
                return LiveLoopRaceTests.deliveryPage()
            case "scenario.status":
                return ["scenario": LiveLoopRaceTests.room(observed: "degraded")]
            case "participant.list":
                return LiveLoopRaceTests.roster("ready")
            default:
                XCTFail("the live loop must not issue \(call.operation)")
                return [:]
            }
        }

        func cancelOperation(_ operationID: String) async throws -> [String: Any] { [:] }
    }

    // Fixture builders are `nonisolated` functions returning fresh values:
    // a `@MainActor` test class would otherwise isolate them, and the fake
    // client's `call` runs off the main actor.
    nonisolated private static func room(observed: String) -> [String: Any] {
        [
            "scenario_id": "room-1",
            "scenario_generation": 1,
            "state_revision": 7,
            "desired_state": "running",
            "observed_state": observed,
            "workspace_binding_id": "ws-1",
            "participant_ids": ["analyst"],
            "objective": "",
            "objective_history": [[String: Any]](),
        ]
    }

    nonisolated private static func participant(_ observed: String) -> [String: Any] {
        [
            "participant_id": "analyst",
            "participant_generation": 1,
            "state_revision": 3,
            "desired_state": "running",
            "observed_state": observed,
        ]
    }

    nonisolated private static func roster(_ observed: String) -> [String: Any] {
        [
            "participants": [participant(observed)],
            "participant_configurations": [[
                "participant_id": "analyst",
                "participant_generation": 1,
                "runtime_profile_ref": NSNull(),
                "continuity_mode": "explicit_recreate",
                "model_binding": NSNull(),
            ]],
        ]
    }

    /// One consumed delivery — the model starts with none.
    nonisolated private static func deliveryPage() -> [String: Any] {
        [
            "delivery_collection": [
                "summary": [
                    "total": 1,
                    "states": ["consumed": 1],
                    "kinds": ["collaboration.notice": 1],
                    "reply_expected_total": 0,
                    "reply_expected_closed": 0,
                    "delivered_with_reply": 0,
                    "attempted_total": 1,
                    "first_attempt_total": 1,
                    "degraded_total": 0,
                ],
                "deliveries": [[
                    "delivery_id": "delivery-1",
                    "enqueue_sequence": 1,
                    "message_kind": "collaboration.notice",
                    "sender": ["participant_id": "analyst", "participant_generation": 1],
                    "receiver": ["participant_id": "reviewer", "participant_generation": 1],
                    "thread_root_delivery_id": "delivery-1",
                    "reply_to_delivery_id": NSNull(),
                    "state": "consumed",
                    "event_sequence": 2,
                    "last_event": ["event": "consumed", "attempt_number": 1],
                    "retry_eligibility": ["eligible": false, "reason": "delivery.already-consumed"],
                ]],
            ],
        ]
    }

    private func model(client: FakeHarnessClient) -> HarnessViewModel {
        let model = HarnessViewModel(client: client)
        model.projects = [ProjectRecord([
            "project_instance_id": "proj-1",
            "project_key": "edge-studio",
            "project_binding_digest": String(repeating: "a", count: 64),
            "product_contract_version": "3",
        ])!]
        model.selectedProjectID = "proj-1"
        model.scenarios = [ScenarioRecord(Self.room(observed: "running"))!]
        model.selectedScenarioID = "room-1"
        model.participants = [ParticipantRecord(Self.participant("stopped"))!]
        return model
    }

    /// Runs one live-loop tick with `held` suspended. `whileHeld` runs on the
    /// main actor while that read is in flight; then the read is released
    /// and the loop given time to land it.
    private func tick(
        holding held: String,
        failHeld: Bool = false,
        whileHeld: (HarnessViewModel) -> Void
    ) async throws -> HarnessViewModel {
        let fake = FakeHarnessClient(holding: held, failHeld: failHeld)
        let model = model(client: fake)
        let loop = Task { await model.monitorRoom(for: "room-1") }
        defer { loop.cancel() }
        for await _ in fake.requested { break }
        whileHeld(model)
        fake.releaseHeldReply()
        try await Task.sleep(nanoseconds: 250_000_000)
        return model
    }

    /// The transitions `performMutation` makes: entry, then exit.
    private func completeMutation(_ model: HarnessViewModel) {
        model.isBusy = true
        model.isBusy = false
    }

    // MARK: - A mutation that began AND finished while the read was in flight

    func testStaleRosterAfterACompletedMutationIsNotWritten() async throws {
        let model = try await tick(holding: "participant.list", whileHeld: completeMutation)
        XCTAssertEqual(
            model.participants.map(\.observedState), ["stopped"],
            "a roster read issued before a mutation must not overwrite the post-mutation roster"
        )
    }

    func testStaleRoomRecordAfterACompletedMutationIsNotWritten() async throws {
        let model = try await tick(holding: "scenario.status", whileHeld: completeMutation)
        XCTAssertEqual(model.scenarios.first?.observedState, "running")
        // The roster read in the same observe cycle carries the same stale
        // epoch and is suppressed with it.
        XCTAssertEqual(model.participants.map(\.observedState), ["stopped"])
    }

    func testStaleDeliveryPageAfterACompletedMutationIsNotWritten() async throws {
        let model = try await tick(holding: "delivery.list", whileHeld: completeMutation)
        XCTAssertTrue(model.deliveries.isEmpty)
        XCTAssertEqual(model.deliveryMessage, S.Defaults.delivery, "the showing-N note is a write too")
    }

    func testStaleDeliveryFailureAfterACompletedMutationIsNotReported() async throws {
        let model = try await tick(
            holding: "delivery.list", failHeld: true, whileHeld: completeMutation
        )
        XCTAssertEqual(
            model.deliveryMessage, S.Defaults.delivery,
            "a live-refresh failure note from a stale read must not land either"
        )
    }

    // MARK: - A mutation still in flight when the read returns

    func testRosterReadThatReturnsDuringAMutationIsNotWritten() async throws {
        let model = try await tick(holding: "participant.list") { $0.isBusy = true }
        XCTAssertEqual(model.participants.map(\.observedState), ["stopped"])
    }

    // MARK: - Controls: identical plumbing, no mutation — the reads land

    func testRosterReadIsWrittenWhenNoMutationTouchedTheRoom() async throws {
        let model = try await tick(holding: "participant.list") { _ in }
        XCTAssertEqual(model.participants.map(\.observedState), ["ready"])
    }

    func testRoomRecordIsWrittenWhenNoMutationTouchedTheRoom() async throws {
        let model = try await tick(holding: "scenario.status") { _ in }
        XCTAssertEqual(model.scenarios.first?.observedState, "degraded")
    }

    func testDeliveryPageIsWrittenWhenNoMutationTouchedTheRoom() async throws {
        let model = try await tick(holding: "delivery.list") { _ in }
        XCTAssertEqual(model.deliveries.map(\.id), ["delivery-1"])
        XCTAssertNotEqual(model.deliveryMessage, S.Defaults.delivery)
    }

    func testDeliveryFailureIsReportedWhenNoMutationTouchedTheRoom() async throws {
        let model = try await tick(holding: "delivery.list", failHeld: true) { _ in }
        XCTAssertNotEqual(model.deliveryMessage, S.Defaults.delivery)
    }
}

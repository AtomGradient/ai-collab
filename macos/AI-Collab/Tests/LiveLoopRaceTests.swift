// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import XCTest
@testable import AICollab

/// codex review 20260904-030617-82zm9p P1: the live loop checked `isBusy`
/// once before `observeRoom`, but both reads inside cross an `await`, so a
/// mutation beginning mid-read could have its fresh post-mutation roster
/// overwritten by the older read landing late. These tests drive the loop
/// with a controlled fake client whose `participant.list` reply is held
/// until the test releases it, and flip `isBusy` in between — ordering that
/// no source-text assertion can prove.
@MainActor
final class LiveLoopRaceTests: XCTestCase {

    /// A `HarnessCalling` whose `participant.list` reply is whatever the test
    /// hands it — including one that suspends until released.
    private final class FakeHarnessClient: HarnessCalling, @unchecked Sendable {
        let participantList: @Sendable () async -> [String: Any]

        init(participantList: @escaping @Sendable () async -> [String: Any]) {
            self.participantList = participantList
        }

        func grantProjectDirectoryAccess(_ url: URL) throws {}

        func call(
            _ call: HarnessCall,
            progress: (@Sendable (HarnessProgress) -> Void)?
        ) async throws -> [String: Any] {
            switch call.operation {
            case "delivery.list":
                return [
                    "delivery_collection": [
                        "summary": [
                            "total": 0,
                            "states": [String: Int](),
                            "kinds": [String: Int](),
                            "reply_expected_total": 0,
                            "reply_expected_closed": 0,
                            "delivered_with_reply": 0,
                            "attempted_total": 0,
                            "first_attempt_total": 0,
                            "degraded_total": 0,
                        ],
                        "deliveries": [[String: Any]](),
                    ],
                ]
            case "scenario.status":
                return ["scenario": LiveLoopRaceTests.room()]
            case "participant.list":
                return await participantList()
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
    nonisolated private static func room() -> [String: Any] {
        [
            "scenario_id": "room-1",
            "scenario_generation": 1,
            "state_revision": 7,
            "desired_state": "running",
            "observed_state": "running",
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

    private func model(client: FakeHarnessClient) -> HarnessViewModel {
        let model = HarnessViewModel(client: client)
        model.projects = [ProjectRecord([
            "project_instance_id": "proj-1",
            "project_key": "edge-studio",
            "project_binding_digest": String(repeating: "a", count: 64),
            "product_contract_version": "3",
        ])!]
        model.selectedProjectID = "proj-1"
        model.scenarios = [ScenarioRecord(Self.room())!]
        model.selectedScenarioID = "room-1"
        model.participants = [ParticipantRecord(Self.participant("stopped"))!]
        return model
    }

    /// The race codex described: `participant.list` is in flight, a mutation
    /// begins, then the (now stale) read completes with a changed roster.
    /// The loop must not write it.
    func testRosterReadThatCompletesAfterAMutationBeganIsNotWritten() async throws {
        let (requested, requestedContinuation) = AsyncStream<Void>.makeStream()
        let (release, releaseContinuation) = AsyncStream<Void>.makeStream()
        let fake = FakeHarnessClient {
            requestedContinuation.yield()
            for await _ in release { break }
            return Self.roster("ready")
        }
        let model = model(client: fake)
        let loop = Task { await model.monitorRoom(for: "room-1") }
        defer { loop.cancel() }

        for await _ in requested { break }
        XCTAssertEqual(model.participants.map(\.observedState), ["stopped"])
        model.isBusy = true
        releaseContinuation.yield()
        try await Task.sleep(nanoseconds: 200_000_000)

        XCTAssertEqual(
            model.participants.map(\.observedState), ["stopped"],
            "a read that finished after the mutation began must not overwrite the roster"
        )
    }

    /// Control for the test above: identical plumbing, no mutation — the
    /// same late-completing read must be written, or the suppression test
    /// would pass for the wrong reason.
    func testRosterReadIsWrittenWhenNoMutationIsInFlight() async throws {
        let (requested, requestedContinuation) = AsyncStream<Void>.makeStream()
        let (release, releaseContinuation) = AsyncStream<Void>.makeStream()
        let fake = FakeHarnessClient {
            requestedContinuation.yield()
            for await _ in release { break }
            return Self.roster("ready")
        }
        let model = model(client: fake)
        let loop = Task { await model.monitorRoom(for: "room-1") }
        defer { loop.cancel() }

        for await _ in requested { break }
        releaseContinuation.yield()
        try await Task.sleep(nanoseconds: 200_000_000)

        XCTAssertEqual(model.participants.map(\.observedState), ["ready"])
    }
}

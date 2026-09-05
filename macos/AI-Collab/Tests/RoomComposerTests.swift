// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import XCTest
@testable import AICollab

/// Answers a room creation where the Host refuses one seat.
private final class ScriptedClient: HarnessCalling, @unchecked Sendable {
    private let refusing: String
    private let lock = NSLock()
    private var log: [String] = []

    init(refusing: String) { self.refusing = refusing }

    func operations() -> [String] {
        lock.lock(); defer { lock.unlock() }
        return log
    }

    func grantProjectDirectoryAccess(_ url: URL) throws {}

    nonisolated private static func room() -> [String: Any] {
        [
            "scenario_id": "room-1", "scenario_generation": 1, "state_revision": 1,
            "desired_state": "closed", "observed_state": "closed",
            "workspace_binding_id": "ws-1", "participant_ids": [String](), "objective": "",
            "objective_history": [[String: Any]](), "playbook": "pairing",
        ]
    }

    func call(
        _ call: HarnessCall,
        progress: (@Sendable (HarnessProgress) -> Void)?
    ) async throws -> [String: Any] {
        lock.withLock { log.append(call.operation) }
        switch call.operation {
        case "scenario.create":
            return ["scenario": Self.room()]
        case "participant.add":
            if call.target["participant_id"] as? String == refusing {
                throw HarnessIPCError.hostRejected(
                    code: "participant.note-invalid", category: "validation",
                    message: "note is too long", retryable: false,
                    mutationState: "not_started", repairAction: nil
                )
            }
            return ["participant": [String: Any]()]
        case "scenario.list":
            return ["scenarios": [Self.room()]]
        default:
            XCTFail("unexpected operation \(call.operation)")
            return [:]
        }
    }

    func cancelOperation(_ operationID: String) async throws -> [String: Any] { [:] }
}

/// The v3 new-room form: a pair by default, any names, unique in the room,
/// and the records the Host now carries for it (playbook, note, room-wide).
@MainActor
final class RoomComposerTests: XCTestCase {

    private func template(_ profile: String, _ name: String, headless: Bool = false) -> ParticipantTemplate {
        guard
            let template = ParticipantTemplate([
                "template_id": "runtime-profile.\(profile)",
                "display_name": name,
                "launch_spec": [
                    "runtime_profile_ref": "runtime-profile.\(profile)",
                    "interaction_mode": headless ? "headless" : "tui",
                ],
            ])
        else { fatalError("fixture template could not be built") }
        return template
    }

    func testDefaultSeatsAreClaudeThenCodexWithTheirCLINames() {
        let seats = HarnessViewModel.defaultSeats(
            from: [template("inert", "Inert", headless: true), template("codex", "Codex"), template("claude", "Claude")]
        )
        XCTAssertEqual(seats.map(\.name), ["claude", "codex"])
        XCTAssertEqual(seats.map(\.templateID), ["runtime-profile.claude", "runtime-profile.codex"])
        XCTAssertEqual(seats.map(\.note), ["", ""])
    }

    func testOneCLIStillMakesAPair() {
        let seats = HarnessViewModel.defaultSeats(from: [template("codex", "Codex")])
        XCTAssertEqual(seats.map(\.name), ["codex", "codex-2"])
        XCTAssertTrue(HarnessViewModel.defaultSeats(from: []).isEmpty)
    }

    func testAddedSeatsTakeTheNextFreeName() {
        let model = HarnessViewModel()
        model.templates = [template("claude", "Claude"), template("codex", "Codex")]
        model.resetRoomComposer()
        model.addSeat()
        XCTAssertEqual(model.newRoomSeats.map(\.name), ["claude", "codex", "codex-2"])
        model.removeSeat(model.newRoomSeats[1].id)
        model.addSeat()
        XCTAssertEqual(
            model.newRoomSeats.map(\.name), ["claude", "codex-2", "codex"],
            "a freed name is the next free name again"
        )
    }

    func testSeatProblemsAreNamedPerSeat() {
        let claude = template("claude", "Claude")
        let model = HarnessViewModel()
        model.templates = [claude]
        model.newRoomSeats = [
            RoomSeat(id: UUID(), name: "claude", templateID: claude.id, note: ""),
            RoomSeat(id: UUID(), name: "claude", templateID: claude.id, note: ""),
            RoomSeat(id: UUID(), name: " ", templateID: claude.id, note: ""),
        ]
        XCTAssertNil(model.seatRowProblem(model.newRoomSeats[0]))
        XCTAssertEqual(model.seatRowProblem(model.newRoomSeats[1]), S.Create.seatNameTaken("claude"))
        XCTAssertEqual(model.seatRowProblem(model.newRoomSeats[2]), S.Create.seatNeedsName)

        XCTAssertEqual(
            HarnessViewModel.seatProblem(
                [RoomSeat(id: UUID(), name: "codex", templateID: nil, note: "")], templates: [claude]
            ),
            S.Create.seatNeedsCLI("codex")
        )
        XCTAssertNil(
            HarnessViewModel.seatProblem(
                [RoomSeat(id: UUID(), name: "codex", templateID: claude.id, note: "x")], templates: [claude]
            )
        )
    }

    func testSeatProblemsCoverNameSyntaxAndNoteLength() {
        let claude = template("claude", "Claude")
        let seats = [
            RoomSeat(id: UUID(), name: "-lead", templateID: claude.id, note: ""),
            RoomSeat(id: UUID(), name: "a b", templateID: claude.id, note: ""),
            RoomSeat(id: UUID(), name: "ok.name:1", templateID: claude.id, note: String(repeating: "备", count: 501)),
            RoomSeat(id: UUID(), name: "fine_2", templateID: claude.id, note: String(repeating: "x", count: 500)),
        ]
        XCTAssertEqual(HarnessViewModel.seatProblem(seats[0], among: seats), S.Create.seatNameInvalid("-lead"))
        XCTAssertEqual(HarnessViewModel.seatProblem(seats[1], among: seats), S.Create.seatNameInvalid("a b"))
        XCTAssertEqual(HarnessViewModel.seatProblem(seats[2], among: seats), S.Create.seatNoteTooLong("ok.name:1"))
        XCTAssertNil(HarnessViewModel.seatProblem(seats[3], among: seats))
        XCTAssertEqual(
            HarnessViewModel.seatProblem(seats, templates: [claude]),
            S.Create.seatNameInvalid("-lead"),
            "every seat is checked before the room is created"
        )
    }

    /// A seat the Host refuses after the room exists must not hide the room:
    /// the person sees the room with the colleagues that were created and
    /// the refusal, and adds the missing one from the room's own row.
    func testASeatRefusedAfterCreationExposesTheRoom() async throws {
        let client = ScriptedClient(refusing: "codex")
        let model = HarnessViewModel(client: client)
        model.projects = [
            try XCTUnwrap(ProjectRecord([
                "project_instance_id": "proj-1",
                "project_key": "edge-studio",
                "project_binding_digest": String(repeating: "a", count: 64),
                "product_contract_version": "3",
            ]))
        ]
        model.selectedProjectID = "proj-1"
        let claude = template("claude", "Claude")
        model.templates = [claude]
        model.newScenarioID = "room-1"
        model.newRoomSeats = [
            RoomSeat(id: UUID(), name: "claude", templateID: claude.id, note: ""),
            RoomSeat(id: UUID(), name: "codex", templateID: claude.id, note: ""),
        ]
        model.isComposingRoom = true

        await model.createScenario()

        XCTAssertEqual(client.operations(), ["scenario.create", "participant.add", "participant.add", "scenario.list"])
        XCTAssertEqual(model.scenarios.map(\.id), ["room-1"])
        XCTAssertEqual(model.selectedScenarioID, "room-1")
        XCTAssertFalse(model.isComposingRoom)
        XCTAssertNotNil(model.validationMessage(for: .scenarioCreate))
    }

    func testSuggestedColleagueNameFollowsTheCLIAndTheRoom() {
        let model = HarnessViewModel()
        model.templates = [template("claude", "Claude"), template("codex", "Codex")]
        model.selectedTemplateID = "runtime-profile.codex"
        XCTAssertEqual(model.suggestedParticipantName, "codex")
        model.participants = [
            ParticipantRecord([
                "participant_id": "codex", "participant_generation": 1, "state_revision": 1,
                "desired_state": "running", "observed_state": "ready",
            ])!,
        ]
        XCTAssertEqual(model.suggestedParticipantName, "codex-2")
    }

    func testRecordsCarryPlaybookNoteAndRoomWide() throws {
        let scenario = try XCTUnwrap(ScenarioRecord([
            "scenario_id": "room-1", "scenario_generation": 1, "state_revision": 1,
            "desired_state": "running", "observed_state": "running",
            "workspace_binding_id": "ws-1", "participant_ids": [], "objective": "",
            "playbook": "peer-review",
        ]))
        XCTAssertEqual(scenario.playbook, "peer-review")
        let participant = try XCTUnwrap(ParticipantRecord([
            "participant_id": "claude", "participant_generation": 2, "state_revision": 1,
            "desired_state": "running", "observed_state": "ready", "note": "先补测试",
        ]))
        XCTAssertEqual(participant.note, "先补测试")
        let open = try XCTUnwrap(PolicyStatusRecord([
            "policy": ["policy_id": "team.room-open", "policy_version": 4],
            "policy_health": ["requires_replan": false, "generation_drift": []],
        ]))
        XCTAssertTrue(open.isRoomWide)
        let project = try XCTUnwrap(PolicyStatusRecord([
            "policy": ["policy_id": "team.peer-review", "policy_version": 1],
            "policy_health": ["requires_replan": false, "generation_drift": []],
        ]))
        XCTAssertFalse(project.isRoomWide)
    }
}

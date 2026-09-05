// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import XCTest
@testable import AICollab

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

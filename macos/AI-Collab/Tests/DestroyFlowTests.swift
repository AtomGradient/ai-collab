// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import XCTest
@testable import AICollab

/// Pins `DestroyFlowDecision` against the four scenarios review
/// 20260903-191042-y57u0q's "测试要求" section named explicitly, plus the
/// exact P0 this whole file exists for: a failed preview read must never be
/// read as the Host having reported the target blocked.
final class DestroyFlowTests: XCTestCase {

    private let target = DestroyFlowTarget(
        projectID: "project-1", scenarioID: "room-1", generation: 2
    )

    // MARK: 1. A failed preview read

    func testFailedReadIsFailedNeverBlockedAndOffersNeitherDeleteAction() {
        let phase = DestroyFlowDecision.phaseAfterLoad(
            target: target,
            currentSelection: target,
            errorMessage: "Harness Host returned an invalid reply.",
            eligible: false
        )
        XCTAssertEqual(
            phase, .failed("Harness Host returned an invalid reply."),
            "a failed read must be its own phase, not silently collapse into .blocked"
        )
        XCTAssertFalse(
            DestroyFlowDecision.canForceDelete(phase),
            "P0: a failed read must never unlock Force Delete"
        )
        XCTAssertFalse(DestroyFlowDecision.canDestroy(phase))
    }

    /// The exact P0 shape: `eligible` still carries a stale `false` (the
    /// view model's default, or left over from a previous, unrelated
    /// target) while the read itself failed. The error must win.
    func testFailedReadWinsOverAStaleEligibleFlag() {
        let phase = DestroyFlowDecision.phaseAfterLoad(
            target: target,
            currentSelection: target,
            errorMessage: "timed out",
            eligible: false
        )
        guard case .failed = phase else {
            return XCTFail("expected .failed, got \(phase)")
        }
    }

    // MARK: 2. blocked vs eligible offer exactly one action each

    func testBlockedOffersOnlyForceDelete() {
        let phase = DestroyFlowDecision.phaseAfterLoad(
            target: target, currentSelection: target, errorMessage: nil, eligible: false
        )
        XCTAssertEqual(phase, .blocked)
        XCTAssertTrue(DestroyFlowDecision.canForceDelete(phase))
        XCTAssertFalse(DestroyFlowDecision.canDestroy(phase))
    }

    func testEligibleOffersOnlyNormalDelete() {
        let phase = DestroyFlowDecision.phaseAfterLoad(
            target: target, currentSelection: target, errorMessage: nil, eligible: true
        )
        XCTAssertEqual(phase, .eligible)
        XCTAssertTrue(DestroyFlowDecision.canDestroy(phase))
        XCTAssertFalse(DestroyFlowDecision.canForceDelete(phase))
    }

    // MARK: 3. target generation changed mid-flow

    func testDriftedGenerationIsStaleRegardlessOfWhatTheReadReported() {
        let recreated = DestroyFlowTarget(
            projectID: target.projectID,
            scenarioID: target.scenarioID,
            generation: 3
        )
        // Even an eligible-looking answer must not be trusted once the
        // selection has moved to a different incarnation of the same name.
        let phase = DestroyFlowDecision.phaseAfterLoad(
            target: target, currentSelection: recreated, errorMessage: nil, eligible: true
        )
        XCTAssertEqual(phase, .stale)
        XCTAssertFalse(DestroyFlowDecision.canDestroy(phase))
        XCTAssertFalse(DestroyFlowDecision.canForceDelete(phase))
    }

    func testNoCurrentSelectionAtAllIsStale() {
        let phase = DestroyFlowDecision.phaseAfterLoad(
            target: target, currentSelection: nil, errorMessage: nil, eligible: true
        )
        XCTAssertEqual(phase, .stale)
    }

    func testSameScenarioIdentityInAnotherProjectIsStale() {
        let otherProject = DestroyFlowTarget(
            projectID: "project-2",
            scenarioID: target.scenarioID,
            generation: target.generation
        )
        let phase = DestroyFlowDecision.phaseAfterLoad(
            target: target, currentSelection: otherProject, errorMessage: nil, eligible: true
        )
        XCTAssertEqual(phase, .stale)
    }

    // MARK: 4. an action failure must not be read as success

    func testActionFailureDoesNotDismiss() {
        XCTAssertFalse(DestroyFlowDecision.shouldDismissAfterAction(succeeded: false))
    }

    func testActionSuccessDoesDismiss() {
        XCTAssertTrue(DestroyFlowDecision.shouldDismissAfterAction(succeeded: true))
    }
}

// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import Foundation

/// The destroy panel's local phase. Deliberately not a bare `Bool` (`loaded`)
/// — that collapsed "the read failed" and "the Host said blocked" into the
/// same state, which is exactly the bug review 20260903-191042-y57u0q P0
/// found: a failed preview request silently unlocked Force Delete, because
/// "no eligible answer" was read as "Host explicitly said not eligible".
enum DestroyPreviewPhase: Equatable {
    case loading
    case eligible
    case blocked
    case failed(String)
    /// The panel's target Scenario (id + generation) no longer matches what
    /// is actually selected — it was destroyed and recreated, or something
    /// else changed selection, while this panel was open.
    case stale
}

/// Identity for freshness checks: id alone is not enough because Scenario
/// names are project-scoped, and a destroyed/recreated Scenario is a different
/// incarnation with a different fence (review 20260903-191042-y57u0q P1 —
/// the same class of bug this project's own scenario_generation fix,
/// ae5cbbe, exists for).
struct DestroyFlowTarget: Equatable {
    let projectID: String
    let scenarioID: String
    let generation: Int
}

/// Pure decision logic, extracted from `DestroyPanel` (a View) so it can be
/// unit tested directly — a SwiftUI `@State` mutation needs a real hosting
/// context to observe, a plain function does not. Every branch here is
/// exactly one of the four scenarios review 20260903-191042-y57u0q's
/// "测试要求" section named.
enum DestroyFlowDecision {
    /// What phase a completed load attempt resolves to. Order matters: a
    /// target that drifted during the load is `.stale` even if the load
    /// itself happened to succeed — the answer it got is not for the room
    /// the user is looking at anymore. A failed read is `.failed`
    /// regardless of whatever stale `eligible` value is still sitting on
    /// the view model from a previous, unrelated call — it is never read as
    /// a Host-reported `.blocked`.
    static func phaseAfterLoad(
        target: DestroyFlowTarget,
        currentSelection: DestroyFlowTarget?,
        errorMessage: String?,
        eligible: Bool
    ) -> DestroyPreviewPhase {
        guard currentSelection == target else { return .stale }
        if let errorMessage { return .failed(errorMessage) }
        return eligible ? .eligible : .blocked
    }

    /// Force Delete is offered once a load has explicitly, successfully
    /// reported the target blocked — never while loading, never after a
    /// failed read, never for an eligible or stale target.
    static func canForceDelete(_ phase: DestroyPreviewPhase) -> Bool {
        phase == .blocked
    }

    /// Normal delete is offered only once a load has explicitly,
    /// successfully reported the target eligible.
    static func canDestroy(_ phase: DestroyPreviewPhase) -> Bool {
        phase == .eligible
    }

    /// A destructive action (either path) must only close the panel once it
    /// is confirmed to have actually gone through — an error leaves the
    /// panel open, with its preview/blocker context intact, rather than
    /// vanishing and leaving the failure to a background banner.
    static func shouldDismissAfterAction(succeeded: Bool) -> Bool {
        succeeded
    }
}

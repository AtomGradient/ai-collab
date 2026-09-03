// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import SwiftUI

/// The six-class visual vocabulary the redesigned Scenario/Participant chrome
/// encodes state with. Colour is never the only carrier: every class also
/// owns a fixed SF Symbol, and the badge text stays the entity's own existing
/// `S.Status`/`humanState` label — never a bucket name.
///
/// `inactive` and `success` are deliberately distinct classes rather than one
/// "settled/green" bucket. `inactive` means "not currently doing anything,
/// no verdict either way" (a closed Scenario, a stopped/detached
/// Participant). `success` means a real positive outcome was reached (a
/// delivered/consumed Delivery, a passed Preflight check, a granted
/// permission, a released lease). The product has no "Scenario completed"
/// state at all — collapsing the two loses exactly that distinction.
///
/// Mapping is per entity on purpose (codex review 20260903-181141-6gjonu
/// point 2): the same raw token means different things on different
/// entities, so there is deliberately no single `String -> PresentationClass`
/// table shared across them.
enum PresentationClass: Equatable {
    case working
    case waiting
    case attention
    case failed
    case success
    case inactive

    var color: Color {
        switch self {
        case .working: .blue
        case .waiting: Color(red: 0.62, green: 0.47, blue: 0.09)
        case .attention: .orange
        case .failed: .red
        case .success: .green
        case .inactive: .secondary
        }
    }

    /// SF Symbol paired with every badge so colour is never the sole carrier.
    var symbolName: String {
        switch self {
        case .working: "circle.fill"
        case .waiting: "clock.fill"
        case .attention: "exclamationmark.triangle.fill"
        case .failed: "xmark.circle.fill"
        case .success: "checkmark.circle.fill"
        case .inactive: "circle"
        }
    }
}

extension ScenarioRecord {
    /// Covers the full `scenario_observed_state` enum in
    /// `contracts/scenario_participant_state_v1.schema.json`: provisioning,
    /// provision_failed, closed, opening, running, degraded, repairing,
    /// closing, destroying — 9 values, all listed explicitly below rather
    /// than relying on a fail-closed default to carry one of them. `repairing`
    /// is a legitimate in-flight state (the Scenario the user just clicked
    /// Repair on) and must read as `working`, not `attention` — landing it in
    /// `attention` would make a room the user is actively fixing look like it
    /// just broke again (review 20260903-183736-clqu6r P1-1).
    ///
    /// A Scenario has no "success" state at all — `closed` is neutral
    /// (`inactive`), never a completion the Host actually reports. Only a
    /// token truly outside the contract falls to the `attention` default.
    var presentationClass: PresentationClass {
        switch observedState {
        case "provisioning", "opening", "closing", "destroying", "repairing", "running":
            .working
        case "degraded":
            .attention
        case "provision_failed":
            .failed
        case "closed":
            .inactive
        default:
            .attention
        }
    }
}

extension ParticipantRecord {
    /// Covers the full `participant_observed_state` enum in
    /// `contracts/scenario_participant_state_v1.schema.json`: detached,
    /// stopped, starting, ready, stopping, recovering, replacing, destroying,
    /// degraded — 9 values, all listed explicitly. Note `repairing` is
    /// **not** in this enum (it is Scenario-only) and `destroying` **is**
    /// (a Participant being deleted) — review 20260903-183736-clqu6r P1-1
    /// caught both getting mixed up between the two entities.
    ///
    /// `ready` means the TUI is running and available — that is the
    /// operating state, so it maps to `working` like every other in-flight
    /// state, never to `success`/`inactive`. A Participant has no standalone
    /// failed state: a launch failure surfaces as `degraded` with
    /// `degradedReason == "launch_failed"`, so `failed` is unused here.
    var presentationClass: PresentationClass {
        switch observedState {
        case "starting", "stopping", "recovering", "replacing", "destroying", "ready":
            .working
        case "degraded":
            .attention
        case "stopped", "detached":
            .inactive
        default:
            .attention
        }
    }
}

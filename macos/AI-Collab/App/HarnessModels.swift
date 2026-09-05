// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import Foundation

struct PingAgentCommandEntry: Codable, Equatable, Sendable {
    let name: String
    let path: String
    let kind: String
    let state: String
    let replaceable: Bool
}

struct PingAgentInstallationResult: Codable, Equatable, Sendable {
    let status: String
    let bundle: String?
    let entries: [PingAgentCommandEntry]
    let conflicts: [PingAgentCommandEntry]
    let reason: String?
    let backupDirectory: String?

    enum CodingKeys: String, CodingKey {
        case status, bundle, entries, conflicts, reason
        case backupDirectory = "backup_directory"
    }

    var needsAttention: Bool { !["ready", "skipped"].contains(status) }
    var canReplace: Bool { !conflicts.isEmpty && conflicts.allSatisfy(\.replaceable) }
    static let development = Self(
        status: "skipped", bundle: "unverified", entries: [], conflicts: [],
        reason: nil, backupDirectory: nil
    )
}

struct PingAgentInstallationError: LocalizedError {
    let result: PingAgentInstallationResult
    var errorDescription: String? {
        ([S.Installation.needsAttention] + result.conflicts.map(\.path)
         + [result.reason, result.backupDirectory].compactMap { $0 }).joined(separator: "\n")
    }
}

extension Collection {
    var only: Element? { count == 1 ? first : nil }
}

struct ProjectRecord: Identifiable, Equatable {
    let id: String
    let key: String
    let bindingDigest: String
    let productContractVersion: String

    init?(_ value: [String: Any]) {
        guard
            let id = value["project_instance_id"] as? String,
            let key = value["project_key"] as? String,
            let bindingDigest = value["project_binding_digest"] as? String,
            let productContractVersion = value["product_contract_version"] as? String
        else { return nil }
        self.id = id
        self.key = key
        self.bindingDigest = bindingDigest
        self.productContractVersion = productContractVersion
    }
}

struct ProjectRepositoryChange: Identifiable, Equatable {
    let repoKey: String
    let path: String
    let classification: String
    let status: String
    let reasons: [String]

    var id: String { "\(status):\(path):\(repoKey)" }

    init?(_ value: [String: Any]) {
        guard
            let repoKey = value["repo_key"] as? String,
            let path = value["path"] as? String,
            let classification = value["classification"] as? String,
            let status = value["status"] as? String
        else { return nil }
        self.repoKey = repoKey
        self.path = path
        self.classification = classification
        self.status = status
        self.reasons = value["reasons"] as? [String] ?? []
    }
}

struct ProjectReconciliationRecord: Equatable {
    let status: String
    let bindingChanged: Bool
    let fingerprint: String
    let changes: [ProjectRepositoryChange]
    let warnings: [String]

    init?(_ value: [String: Any]) {
        guard
            let status = value["status"] as? String,
            let bindingChanged = value["binding_changed"] as? Bool,
            let fingerprint = value["availability_fingerprint"] as? String,
            let warnings = value["warnings"] as? [String]
        else { return nil }
        let rawChanges = dictionaries(value["changes"])
        let changes = rawChanges.compactMap(ProjectRepositoryChange.init)
        guard changes.count == rawChanges.count else { return nil }
        self.status = status
        self.bindingChanged = bindingChanged
        self.fingerprint = fingerprint
        self.changes = changes
        self.warnings = warnings
    }
}

struct ScenarioObjectiveRevision: Identifiable, Equatable {
    let revision: Int
    let objective: String
    let acceptanceCriteria: String

    var id: Int { revision }

    init?(_ value: [String: Any]) {
        guard
            let revision = value["revision"] as? Int,
            revision > 0,
            let objective = value["objective"] as? String,
            let acceptanceCriteria = value["acceptance_criteria"] as? String
        else { return nil }
        self.revision = revision
        self.objective = objective
        self.acceptanceCriteria = acceptanceCriteria
    }
}

struct ScenarioRecord: Identifiable, Equatable {
    let id: String
    let generation: Int
    let stateRevision: Int
    let desiredState: String
    let observedState: String
    let workspaceBindingID: String
    let participantIDs: [String]
    let objective: String
    let objectiveHistory: [ScenarioObjectiveRevision]
    /// The opening written into each colleague's startup prompt: "none",
    /// "pairing" or "peer-review". Text, never a rule.
    let playbook: String

    var objectiveRevision: Int { objectiveHistory.last?.revision ?? 0 }
    var acceptanceCriteria: String {
        objectiveHistory.last?.acceptanceCriteria ?? ""
    }

    init?(_ value: [String: Any]) {
        guard
            let id = value["scenario_id"] as? String,
            let generation = value["scenario_generation"] as? Int,
            let stateRevision = value["state_revision"] as? Int,
            let desiredState = value["desired_state"] as? String,
            let observedState = value["observed_state"] as? String,
            let workspaceBindingID = value["workspace_binding_id"] as? String,
            let participantIDs = value["participant_ids"] as? [String],
            let objective = value["objective"] as? String
        else { return nil }
        let rawObjectiveHistory = dictionaries(value["objective_history"])
        let objectiveHistory = rawObjectiveHistory.compactMap(
            ScenarioObjectiveRevision.init
        )
        guard
            objectiveHistory.count == rawObjectiveHistory.count,
            objectiveHistory.enumerated().allSatisfy({ offset, item in
                item.revision == offset + 1
            }),
            objectiveHistory.last?.objective ?? "" == objective
        else { return nil }
        self.id = id
        self.generation = generation
        self.stateRevision = stateRevision
        self.desiredState = desiredState
        self.observedState = observedState
        self.workspaceBindingID = workspaceBindingID
        self.participantIDs = participantIDs
        self.objective = objective
        self.objectiveHistory = objectiveHistory
        self.playbook = value["playbook"] as? String ?? "none"
    }
}

struct ParticipantRecord: Identifiable, Equatable {
    let id: String
    let generation: Int
    let stateRevision: Int
    let desiredState: String
    let observedState: String
    let runtimeBindingID: String?
    let presentationBindingID: String?
    let degradedReason: String?
    let cleanupPending: Bool
    let repairAction: String?
    let runtimeProfileRef: String?
    let continuityMode: String?
    let modelBinding: ModelBindingRecord?
    /// "tui" or "headless", as projected by the Host record. Only a TUI
    /// colleague has a window an employee can focus and assign work in.
    let interactionMode: String?
    let issuedObjectiveRevision: Int
    /// The person's optional note for this colleague, shown as is.
    let note: String

    var isInteractive: Bool { interactionMode == "tui" }

    /// Mirrors `begin_participant_start` in the Host store, which accepts only a
    /// stopped or detached record. The Host additionally gates on the enclosing
    /// Scenario, so this stays a necessary — not sufficient — condition.
    var canStart: Bool {
        ["stopped", "detached"].contains(observedState)
    }

    /// Mirrors `begin_participant_stop` in the Host store. Note that "running" is
    /// deliberately absent: the store rejects it, so offering Stop there would
    /// render a control that can only fail.
    var canStop: Bool {
        ["ready", "degraded"].contains(observedState)
    }

    var canRecover: Bool {
        observedState == "degraded" && repairAction == "participant.recover"
    }

    var canForceStop: Bool {
        ["ready", "degraded"].contains(observedState) && runtimeBindingID != nil
    }

    var canRecreateWithHandoff: Bool {
        observedState == "degraded" && !cleanupPending
            && continuityMode == "exact_resume"
    }

    init?(
        _ value: [String: Any],
        configuration: [String: Any]? = nil
    ) {
        guard
            let id = value["participant_id"] as? String,
            let generation = value["participant_generation"] as? Int,
            let stateRevision = value["state_revision"] as? Int,
            let desiredState = value["desired_state"] as? String,
            let observedState = value["observed_state"] as? String
        else { return nil }
        self.id = id
        self.generation = generation
        self.stateRevision = stateRevision
        self.desiredState = desiredState
        self.observedState = observedState
        self.runtimeBindingID = value["runtime_binding_id"] as? String
        self.interactionMode = value["interaction_mode"] as? String
        self.issuedObjectiveRevision = value["issued_objective_revision"] as? Int ?? 0
        self.note = value["note"] as? String ?? ""
        self.presentationBindingID = value["presentation_binding_id"] as? String
        let degraded = value["degraded"] as? [String: Any]
        self.degradedReason = degraded?["reason"] as? String
        self.cleanupPending = degraded?["cleanup_pending"] as? Bool ?? false
        self.repairAction = degraded?["repair_action"] as? String
        if let configuration {
            guard
                Set(configuration.keys) == Set([
                    "participant_id",
                    "participant_generation",
                    "runtime_profile_ref",
                    "continuity_mode",
                    "model_binding",
                ]),
                configuration["participant_id"] as? String == id,
                configuration["participant_generation"] as? Int == generation,
                configuration["runtime_profile_ref"] is NSNull
                    || configuration["runtime_profile_ref"] is String,
                let continuityMode = configuration["continuity_mode"] as? String,
                ["explicit_recreate", "exact_resume"].contains(continuityMode)
            else { return nil }
            self.runtimeProfileRef = configuration["runtime_profile_ref"] as? String
            self.continuityMode = continuityMode
            if configuration["model_binding"] is NSNull {
                self.modelBinding = nil
            } else if let rawModel = configuration["model_binding"] as? [String: Any],
                      let model = ModelBindingRecord(rawModel) {
                self.modelBinding = model
            } else {
                return nil
            }
        } else {
            self.runtimeProfileRef = nil
            self.continuityMode = nil
            self.modelBinding = nil
        }
    }
}

struct ModelBindingRecord: Equatable {
    let providerProfileRef: String
    let modelRef: String
    let inferenceProfileRef: String?

    init?(_ value: [String: Any]) {
        guard
            Set(value.keys) == Set([
                "provider_profile_ref",
                "model_ref",
                "inference_profile_ref",
            ]),
            let providerProfileRef = value["provider_profile_ref"] as? String,
            let modelRef = value["model_ref"] as? String
        else { return nil }
        if !(value["inference_profile_ref"] is NSNull)
            && !(value["inference_profile_ref"] is String) {
            return nil
        }
        self.providerProfileRef = providerProfileRef
        self.modelRef = modelRef
        self.inferenceProfileRef = value["inference_profile_ref"] as? String
    }
}

struct ResourceLeaseRecord: Identifiable, Equatable {
    let id: String
    let revision: Int
    let resourceClass: String
    let status: String
    let staleReason: String?
    let participantID: String
    let participantGeneration: Int

    var canBreak: Bool { status == "stale" }

    init?(_ value: [String: Any]) {
        guard
            let id = value["lease_id"] as? String,
            let revision = value["lease_revision"] as? Int,
            let resourceClass = value["resource_class"] as? String,
            let status = value["status"] as? String,
            let holder = value["holder"] as? [String: Any],
            let participantID = holder["participant_id"] as? String,
            let participantGeneration = holder["participant_generation"] as? Int
        else { return nil }
        self.id = id
        self.revision = revision
        self.resourceClass = resourceClass
        self.status = status
        self.staleReason = value["stale_reason"] as? String
        self.participantID = participantID
        self.participantGeneration = participantGeneration
    }
}

struct PreflightCheckRecord: Identifiable, Equatable {
    let id: String
    let status: String
    let summary: String
    let repairAction: String?

    init?(_ value: [String: Any]) {
        guard
            let id = value["check_id"] as? String,
            let status = value["status"] as? String,
            ["ready", "blocked", "not_required"].contains(status),
            let summary = value["summary"] as? String
        else { return nil }
        self.id = id
        self.status = status
        self.summary = summary
        self.repairAction = value["repair_action"] as? String
    }
}

struct PermissionObservationRecord: Identifiable, Equatable {
    let permissionID: String
    let providerRef: String
    let subjectRef: String
    let status: String
    let providerErrorCode: String?
    let remediationRef: String?
    let promptRequested: Bool

    var id: String { "\(permissionID):\(subjectRef)" }

    init?(_ value: [String: Any]) {
        guard
            let permissionID = value["permission_id"] as? String,
            let providerRef = value["provider_ref"] as? String,
            let subjectRef = value["subject_ref"] as? String,
            let status = value["status"] as? String,
            [
                "granted", "denied", "not_determined", "restricted", "unavailable",
                "unknown",
            ].contains(status),
            value["evidence_digest"] as? String != nil,
            let promptRequested = value["prompt_requested"] as? Bool
        else { return nil }
        self.permissionID = permissionID
        self.providerRef = providerRef
        self.subjectRef = subjectRef
        self.status = status
        self.providerErrorCode = value["provider_error_code"] as? String
        self.remediationRef = value["remediation_ref"] as? String
        self.promptRequested = promptRequested
    }
}

struct EnvironmentObservationRecord: Identifiable, Equatable {
    let subjectRef: String
    let displayName: String
    let status: String
    let observedVersion: String?
    let providerErrorCode: String?
    let remediationRef: String?

    var id: String { subjectRef }

    init?(_ value: [String: Any]) {
        guard
            let subjectRef = value["subject_ref"] as? String,
            let displayName = value["display_name"] as? String,
            let status = value["status"] as? String,
            ["available", "missing", "unknown"].contains(status),
            value["evidence_digest"] as? String != nil
        else { return nil }
        self.subjectRef = subjectRef
        self.displayName = displayName
        self.status = status
        self.observedVersion = value["observed_version"] as? String
        self.providerErrorCode = value["provider_error_code"] as? String
        self.remediationRef = value["remediation_ref"] as? String
    }
}

struct ScenarioPreflightRecord: Equatable {
    let status: String
    let capturedAtEpochMS: Int
    let checks: [PreflightCheckRecord]
    let permissions: [PermissionObservationRecord]
    let repairActions: [String]

    init?(_ value: [String: Any]) {
        guard
            value["schema_version"] as? Int == 1,
            let status = value["status"] as? String,
            ["ready", "blocked"].contains(status),
            let capturedAtEpochMS = value["captured_at_epoch_ms"] as? Int,
            value["scope"] as? [String: Any] != nil,
            value["preflight_digest"] as? String != nil,
            let repairActions = value["repair_actions"] as? [String]
        else { return nil }
        let rawChecks = dictionaries(value["checks"])
        let checks = rawChecks.compactMap(PreflightCheckRecord.init)
        let rawPermissions = dictionaries(value["permission_observations"])
        let permissions = rawPermissions.compactMap(PermissionObservationRecord.init)
        guard checks.count == rawChecks.count, permissions.count == rawPermissions.count else {
            return nil
        }
        self.status = status
        self.capturedAtEpochMS = capturedAtEpochMS
        self.checks = checks
        self.permissions = permissions
        self.repairActions = repairActions
    }
}

struct PresentationTopologyRecord: Identifiable, Equatable {
    let id: String
    let generation: Int
    let interactionMode: String
    let health: String
    let focused: Bool
    let restoreOutcome: String
    let geometryLabel: String?
    let displayTopologyFingerprint: String?
    let errorCode: String?

    init?(_ value: [String: Any]) {
        guard
            let id = value["participant_id"] as? String,
            let generation = value["participant_generation"] as? Int,
            let interactionMode = value["interaction_mode"] as? String,
            ["tui", "headless"].contains(interactionMode),
            let health = value["health"] as? String,
            ["ready", "degraded", "not_running", "not_required"].contains(health),
            let focused = value["focused"] as? Bool,
            let restoreOutcome = value["restore_outcome"] as? String,
            ["not_requested", "not_available", "applied_exact", "applied_adjusted"]
                .contains(restoreOutcome)
        else { return nil }
        let geometry = value["geometry"] as? [String: Any]
        if let geometry {
            guard
                let x = geometry["x"] as? Int,
                let y = geometry["y"] as? Int,
                let width = geometry["width"] as? Int,
                let height = geometry["height"] as? Int,
                width > 0,
                height > 0
            else { return nil }
            self.geometryLabel = "\(width)×\(height) at \(x),\(y)"
        } else {
            self.geometryLabel = nil
        }
        self.id = id
        self.generation = generation
        self.interactionMode = interactionMode
        self.health = health
        self.focused = focused
        self.restoreOutcome = restoreOutcome
        self.displayTopologyFingerprint = value["display_topology_fingerprint"] as? String
        self.errorCode = value["error_code"] as? String
    }
}

struct ScenarioTopologyRecord: Equatable {
    let action: String
    let participants: [PresentationTopologyRecord]

    init?(_ value: [String: Any]) {
        guard
            value["schema_version"] as? Int == 1,
            let action = value["action"] as? String,
            ["inspect", "focus"].contains(action),
            value["summary_digest"] as? String != nil
        else { return nil }
        let raw = dictionaries(value["participants"])
        let participants = raw.compactMap(PresentationTopologyRecord.init)
        guard participants.count == raw.count else { return nil }
        self.action = action
        self.participants = participants
    }
}

struct WorkspaceComponentProgress: Identifiable, Equatable {
    let index: Int
    var componentID: String
    var kind: String
    var state: String

    var id: Int { index }
}

struct ActionableErrorRecord: Equatable {
    let code: String
    let category: String
    let retryable: Bool
    let mutationState: String
    let repairAction: String?
    /// Raw Host evidence (verbatim) for Host-rejected operations.
    private let hostMessage: String?
    /// App-generated copy, rendered at read time so a language switch
    /// retranslates instantly. Raw Host evidence is never re-rendered.
    private let localRender: (() -> String)?

    var message: String { localRender?() ?? hostMessage ?? "" }

    static func == (left: ActionableErrorRecord, right: ActionableErrorRecord) -> Bool {
        left.code == right.code
            && left.category == right.category
            && left.retryable == right.retryable
            && left.mutationState == right.mutationState
            && left.repairAction == right.repairAction
            && left.hostMessage == right.hostMessage
    }

    init(_ error: Error) {
        if let installationError = error as? PingAgentInstallationError {
            self.code = "installation.commands"
            self.category = "client"
            self.hostMessage = nil
            self.localRender = { installationError.localizedDescription }
            self.retryable = true
            self.mutationState = "not_started"
            self.repairAction = "installation.commands.repair"
        } else if let ipcError = error as? HarnessIPCError, case let .hostRejected(
            code, category, message, retryable, mutationState, repairAction
        ) = ipcError {
            self.code = code
            self.category = category
            self.hostMessage = message
            self.localRender = nil
            self.retryable = retryable
            self.mutationState = mutationState
            self.repairAction = repairAction
        } else if let ipcError = error as? HarnessIPCError {
            self.code = "availability.host-unavailable"
            self.category = "availability"
            self.hostMessage = nil
            self.localRender = { ipcError.localizedDescription }
            self.retryable = true
            self.mutationState = ipcError.isOperationTimeout ? "unknown" : "not_started"
            switch ipcError {
            case .invalidProjectDirectory:
                self.repairAction = "project.register"
            case .operationTimedOut:
                self.repairAction = "scenario.refresh"
            case .contractMismatch:
                self.repairAction = "host.update"
            default:
                self.repairAction = "host.retry"
            }
        } else {
            self.code = "client.failure"
            self.category = "client"
            self.hostMessage = nil
            self.localRender = { error.localizedDescription }
            self.retryable = false
            self.mutationState = "not_started"
            self.repairAction = nil
        }
    }
}

private extension HarnessIPCError {
    var isOperationTimeout: Bool {
        if case .operationTimedOut = self { return true }
        return false
    }
}

struct ParticipantTemplate: Identifiable {
    let id: String
    let displayName: String
    let launchSpec: [String: Any]
    let presentationDriverID: String?

    /// A headless template gets no window, so nobody can work with it the way the
    /// product intends. Today that is only the inert test fixture, and its
    /// display name says so; the grouping is kept on this one property so both
    /// the add form and the replace menu decide the same way.
    var isHeadless: Bool {
        (launchSpec["interaction_mode"] as? String) == "headless"
    }

    init?(_ value: [String: Any]) {
        guard
            let id = value["template_id"] as? String,
            let displayName = value["display_name"] as? String,
            let launchSpec = value["launch_spec"] as? [String: Any]
        else { return nil }
        self.id = id
        self.displayName = displayName
        self.launchSpec = launchSpec
        self.presentationDriverID = value["presentation_driver_id"] as? String
    }
}

struct PolicyTemplateRecord: Identifiable, Equatable {
    let id: String
    let displayName: String
    let participantIDs: [String]

    init?(_ value: [String: Any]) {
        guard
            let id = value["template_id"] as? String,
            let displayName = value["display_name"] as? String,
            let participantIDs = value["participant_ids"] as? [String]
        else { return nil }
        self.id = id
        self.displayName = displayName
        self.participantIDs = participantIDs
    }
}

struct PolicyTeamMember: Identifiable, Equatable {
    let participantID: String
    let generation: Int?
    let isPresent: Bool

    var id: String { participantID }

    init?(_ value: [String: Any]) {
        guard
            let participantID = value["participant_id"] as? String,
            let isPresent = value["present"] as? Bool
        else { return nil }
        self.participantID = participantID
        self.generation = value["participant_generation"] as? Int
        self.isPresent = isPresent
    }
}

struct PolicyRouteEffect: Identifiable, Equatable {
    let id: String
    let messageKind: String
    let effect: String
    let senderParticipantIDs: [String]
    let receiverParticipantIDs: [String]
    let maxAttempts: Int?

    init?(_ value: [String: Any]) {
        guard
            let id = value["rule_id"] as? String,
            let messageKind = value["message_kind"] as? String,
            let effect = value["effect"] as? String,
            let senderParticipantIDs = value["sender_participants"] as? [String],
            let receiverParticipantIDs = value["receiver_participants"] as? [String]
        else { return nil }
        self.id = id
        self.messageKind = messageKind
        self.effect = effect
        self.senderParticipantIDs = senderParticipantIDs
        self.receiverParticipantIDs = receiverParticipantIDs
        self.maxAttempts = (value["retry_profile"] as? [String: Any])?["max_attempts"] as? Int
    }
}

struct PolicyPlanRecord: Equatable {
    let templateID: String
    let planDigest: String
    let canApply: Bool
    let blockers: [String]
    let team: [PolicyTeamMember]
    let routeEffects: [PolicyRouteEffect]
    let policyVersion: Int?
    let scenarioGeneration: Int
    let scenarioStateRevision: Int

    init?(_ value: [String: Any]) {
        guard
            let snapshot = value["template_snapshot"] as? [String: Any],
            let templateID = snapshot["template_id"] as? String,
            let planDigest = value["plan_digest"] as? String,
            let canApply = value["can_apply"] as? Bool,
            let blockers = value["blockers"] as? [String],
            let scenario = value["scenario"] as? [String: Any],
            let scenarioGeneration = scenario["scenario_generation"] as? Int,
            let scenarioStateRevision = scenario["scenario_state_revision"] as? Int
        else { return nil }
        self.templateID = templateID
        self.planDigest = planDigest
        self.canApply = canApply
        self.blockers = blockers
        let rawTeam = dictionaries(value["team"])
        let team = rawTeam.compactMap(PolicyTeamMember.init)
        let rawRouteEffects = dictionaries(value["route_effects"])
        let routeEffects = rawRouteEffects.compactMap(PolicyRouteEffect.init)
        guard team.count == rawTeam.count, routeEffects.count == rawRouteEffects.count else {
            return nil
        }
        self.team = team
        self.routeEffects = routeEffects
        self.policyVersion = (value["policy_pack"] as? [String: Any])?["policy_version"] as? Int
        self.scenarioGeneration = scenarioGeneration
        self.scenarioStateRevision = scenarioStateRevision
    }
}

struct PolicyGenerationDrift: Identifiable, Equatable {
    let participantID: String
    let policyGeneration: Int
    let currentGeneration: Int?

    var id: String { participantID }

    init?(_ value: [String: Any]) {
        guard
            let participantID = value["participant_id"] as? String,
            let policyGeneration = value["policy_generation"] as? Int,
            let rawCurrentGeneration = value["current_generation"],
            rawCurrentGeneration is NSNull || rawCurrentGeneration is Int
        else { return nil }
        self.participantID = participantID
        self.policyGeneration = policyGeneration
        self.currentGeneration = rawCurrentGeneration as? Int
    }
}

struct PolicyStatusRecord: Equatable {
    static let roomWidePolicyID = "team.room-open"

    let policyID: String
    let policyVersion: Int
    let requiresReplan: Bool
    let generationDrift: [PolicyGenerationDrift]

    /// The Host-maintained default: everyone may send every kind to everyone.
    var isRoomWide: Bool { policyID == Self.roomWidePolicyID }

    init?(_ value: [String: Any]) {
        guard
            let policy = value["policy"] as? [String: Any],
            let policyID = policy["policy_id"] as? String,
            let policyVersion = policy["policy_version"] as? Int,
            let health = value["policy_health"] as? [String: Any],
            let requiresReplan = health["requires_replan"] as? Bool
        else { return nil }
        self.policyID = policyID
        self.policyVersion = policyVersion
        self.requiresReplan = requiresReplan
        let rawGenerationDrift = dictionaries(health["generation_drift"])
        let generationDrift = rawGenerationDrift.compactMap(PolicyGenerationDrift.init)
        guard generationDrift.count == rawGenerationDrift.count else { return nil }
        self.generationDrift = generationDrift
    }
}

struct DeliveryParticipantRef: Equatable {
    let participantID: String
    let generation: Int

    init?(_ value: [String: Any]?) {
        guard
            let value,
            let participantID = value["participant_id"] as? String,
            let generation = value["participant_generation"] as? Int
        else { return nil }
        self.participantID = participantID
        self.generation = generation
    }
}

struct DeliveryRecord: Identifiable, Equatable {
    let id: String
    let enqueueSequence: Int
    let messageKind: String
    let sender: DeliveryParticipantRef
    let receiver: DeliveryParticipantRef
    let threadRootDeliveryID: String
    let replyToDeliveryID: String?
    let state: String
    let degradedReason: String?
    let eventSequence: Int
    let lastEvent: String
    let attemptNumber: Int
    let errorCode: String?
    let retryEligible: Bool
    let retryReason: String

    init?(_ value: [String: Any]) {
        guard
            let id = value["delivery_id"] as? String,
            let enqueueSequence = value["enqueue_sequence"] as? Int,
            enqueueSequence > 0,
            let messageKind = value["message_kind"] as? String,
            let sender = DeliveryParticipantRef(value["sender"] as? [String: Any]),
            let receiver = DeliveryParticipantRef(value["receiver"] as? [String: Any]),
            let threadRootDeliveryID = value["thread_root_delivery_id"] as? String,
            let state = value["state"] as? String,
            let eventSequence = value["event_sequence"] as? Int,
            let lastEvent = value["last_event"] as? [String: Any],
            let lastEventName = lastEvent["event"] as? String,
            let retry = value["retry_eligibility"] as? [String: Any],
            let retryEligible = retry["eligible"] as? Bool,
            let retryReason = retry["reason"] as? String
        else { return nil }
        self.id = id
        self.enqueueSequence = enqueueSequence
        self.messageKind = messageKind
        self.sender = sender
        self.receiver = receiver
        self.threadRootDeliveryID = threadRootDeliveryID
        self.replyToDeliveryID = value["reply_to_delivery_id"] as? String
        self.state = state
        self.degradedReason = value["degraded_reason"] as? String
        self.eventSequence = eventSequence
        self.lastEvent = lastEventName
        self.attemptNumber = lastEvent["attempt_number"] as? Int ?? 1
        self.errorCode = lastEvent["error_code"] as? String
        self.retryEligible = retryEligible
        self.retryReason = retryReason
    }
}

struct DeliveryAttentionRecord: Identifiable, Equatable {
    enum Reason: Equatable {
        case degraded(String)
        case repeatedAttempt(Int)
    }

    let delivery: DeliveryRecord
    let reason: Reason

    var id: String { delivery.id }

    static func items(from deliveries: [DeliveryRecord]) -> [DeliveryAttentionRecord] {
        Array(deliveries.compactMap { delivery in
            if let reason = delivery.degradedReason {
                return DeliveryAttentionRecord(
                    delivery: delivery,
                    reason: .degraded(reason)
                )
            }
            if delivery.attemptNumber > 1 {
                return DeliveryAttentionRecord(
                    delivery: delivery,
                    reason: .repeatedAttempt(delivery.attemptNumber)
                )
            }
            return nil
        }.prefix(3))
    }
}

/// Room-wide attention counts.
///
/// The Host's `delivery_collection.summary` is collection scoped; the
/// `deliveries` array beside it is only one bounded page. Deciding "nothing
/// needs attention" from that page would answer a room-wide question with a
/// sample, so the all-clear is decided here and the page supplies examples
/// only.
struct DeliveryAttentionTotals: Equatable {
    let degraded: Int
    let retried: Int

    init(summary: DeliverySummaryRecord) {
        degraded = summary.degradedTotal
        retried = summary.attemptedTotal - summary.firstAttemptTotal
    }

    /// Deliberately no union count. The two categories overlap — a delivery
    /// whose repeated last attempt failed is degraded *and* retried — and the
    /// aggregate summary carries no intersection, so no unique total can be
    /// derived from it without new IPC. Clear is still exact: both zero means
    /// neither category holds anything.
    var isClear: Bool { degraded == 0 && retried == 0 }
}

struct DeliverySummaryRecord: Equatable {
    let total: Int
    let states: [String: Int]
    let kinds: [String: Int]
    let replyExpectedTotal: Int
    let replyExpectedClosed: Int
    let deliveredWithReply: Int
    let attemptedTotal: Int
    let firstAttemptTotal: Int
    let degradedTotal: Int

    init?(_ value: [String: Any]) {
        guard
            let total = value["total"] as? Int,
            let states = value["states"] as? [String: Int],
            let kinds = value["kinds"] as? [String: Int],
            let replyExpectedTotal = value["reply_expected_total"] as? Int,
            let replyExpectedClosed = value["reply_expected_closed"] as? Int,
            let deliveredWithReply = value["delivered_with_reply"] as? Int,
            let attemptedTotal = value["attempted_total"] as? Int,
            let firstAttemptTotal = value["first_attempt_total"] as? Int,
            let degradedTotal = value["degraded_total"] as? Int,
            total >= 0,
            states.values.allSatisfy({ $0 >= 0 }),
            kinds.values.allSatisfy({ $0 >= 0 }),
            states.values.reduce(0, +) == total,
            kinds.values.reduce(0, +) == total,
            replyExpectedTotal >= 0,
            (0 ... replyExpectedTotal).contains(replyExpectedClosed),
            (0 ... total).contains(replyExpectedTotal),
            (0 ... (states["delivered"] ?? 0)).contains(deliveredWithReply),
            (0 ... total).contains(attemptedTotal),
            (0 ... attemptedTotal).contains(firstAttemptTotal),
            (0 ... total).contains(degradedTotal)
        else { return nil }
        self.total = total
        self.states = states
        self.kinds = kinds
        self.replyExpectedTotal = replyExpectedTotal
        self.replyExpectedClosed = replyExpectedClosed
        self.deliveredWithReply = deliveredWithReply
        self.attemptedTotal = attemptedTotal
        self.firstAttemptTotal = firstAttemptTotal
        self.degradedTotal = degradedTotal
    }
}

struct CollaborationHealthRatio: Equatable {
    let value: Int
    let total: Int

    var displayValue: String { "\(value)/\(total)" }

    var state: CollaborationHealthRatioState {
        guard total > 0 else { return .unobserved }
        return value == total ? .complete : .incomplete
    }
}

enum CollaborationHealthRatioState: Equatable {
    case unobserved
    case complete
    case incomplete
}

struct CollaborationHealthRecord: Equatable {
    let teamReady: CollaborationHealthRatio
    let requestsClosed: CollaborationHealthRatio
    let endToEndEvidence: CollaborationHealthRatio
    let firstAttemptDelivery: CollaborationHealthRatio
    let degradedTotal: Int

    init(
        summary: DeliverySummaryRecord,
        readyParticipants: Int,
        totalParticipants: Int
    ) {
        let settledDeliveries = (summary.states["consumed"] ?? 0)
            + (summary.states["delivered"] ?? 0)
            + (summary.states["recipient_deleted"] ?? 0)
        teamReady = CollaborationHealthRatio(
            value: readyParticipants,
            total: totalParticipants
        )
        requestsClosed = CollaborationHealthRatio(
            value: summary.replyExpectedClosed,
            total: summary.replyExpectedTotal
        )
        endToEndEvidence = CollaborationHealthRatio(
            value: (summary.states["consumed"] ?? 0) + summary.deliveredWithReply,
            total: settledDeliveries
        )
        firstAttemptDelivery = CollaborationHealthRatio(
            value: summary.firstAttemptTotal,
            total: summary.attemptedTotal
        )
        degradedTotal = summary.degradedTotal
    }
}

struct DeliveryKindCount: Identifiable, Equatable {
    let id: String
    let count: Int
}

enum DeliveryFinalStateCategory: Equatable {
    case consumptionAcknowledged
    case repliedWithoutConsumptionAck
    case noConsumptionAckOrReply
    case recipientDeleted
}

struct DeliveryFinalStateCount: Equatable {
    let category: DeliveryFinalStateCategory
    let count: Int
}

struct DeliveryDistributionRecord: Equatable {
    static let preferredKinds = [
        "collaboration.review-request",
        "collaboration.review-response",
        "collaboration.notice",
        "collaboration.pushback",
        "collaboration.response",
        "collaboration.question",
        "collaboration.request",
        "collaboration.done",
    ]

    let consumptionAcknowledged: Int
    let repliedWithoutConsumptionAck: Int
    let noConsumptionAckOrReply: Int
    let recipientDeleted: Int
    let settledTotal: Int
    let kinds: [DeliveryKindCount]

    var finalStates: [DeliveryFinalStateCount] {
        var values = [
            DeliveryFinalStateCount(
                category: .consumptionAcknowledged,
                count: consumptionAcknowledged
            ),
            DeliveryFinalStateCount(
                category: .repliedWithoutConsumptionAck,
                count: repliedWithoutConsumptionAck
            ),
            DeliveryFinalStateCount(
                category: .noConsumptionAckOrReply,
                count: noConsumptionAckOrReply
            ),
        ]
        if recipientDeleted > 0 {
            values.append(
                DeliveryFinalStateCount(
                    category: .recipientDeleted,
                    count: recipientDeleted
                )
            )
        }
        return values
    }

    init(summary: DeliverySummaryRecord) {
        let consumed = summary.states["consumed"] ?? 0
        let delivered = summary.states["delivered"] ?? 0
        let deleted = summary.states["recipient_deleted"] ?? 0
        consumptionAcknowledged = consumed
        repliedWithoutConsumptionAck = summary.deliveredWithReply
        noConsumptionAckOrReply = delivered - summary.deliveredWithReply
        recipientDeleted = deleted
        settledTotal = consumed + delivered + deleted
        let preferred = Self.preferredKinds.filter { (summary.kinds[$0] ?? 0) > 0 }
        let preferredSet = Set(preferred)
        let additional = summary.kinds.keys.filter {
            $0 != "collaboration.message"
                && !preferredSet.contains($0)
                && (summary.kinds[$0] ?? 0) > 0
        }.sorted()
        kinds = (preferred + additional + ["collaboration.message"]).map {
            DeliveryKindCount(id: $0, count: summary.kinds[$0] ?? 0)
        }
    }
}

struct DeliveryCollectionRecord: Equatable {
    /// The "collaboration activity" list in the workbench shows this many of
    /// the room's most recent deliveries (the Host sorts by enqueue_sequence
    /// descending and accepts 1...256). Still one page, never paginated —
    /// the raw ledger stays in Evidence & Diagnostics.
    static let rawActivityLimit = 30

    let deliveries: [DeliveryRecord]
    let summary: DeliverySummaryRecord

    init?(_ value: [String: Any]) {
        guard
            let rawSummary = value["summary"] as? [String: Any],
            let summary = DeliverySummaryRecord(rawSummary)
        else { return nil }
        let rawDeliveries = dictionaries(value["deliveries"])
        let deliveries = rawDeliveries.compactMap(DeliveryRecord.init)
        guard deliveries.count == rawDeliveries.count else { return nil }
        self.deliveries = Array(deliveries.prefix(Self.rawActivityLimit))
        self.summary = summary
    }
}

func dictionaries(_ value: Any?) -> [[String: Any]] {
    value as? [[String: Any]] ?? []
}

func prettyJSON(_ value: Any?) -> String {
    guard let value, JSONSerialization.isValidJSONObject(value) else { return S.Common.notAvailable }
    guard
        let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]),
        let text = String(data: data, encoding: .utf8)
    else { return S.Common.notAvailable }
    return text
}

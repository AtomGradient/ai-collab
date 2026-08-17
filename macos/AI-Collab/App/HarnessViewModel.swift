import AppKit
import Foundation
import SwiftUI

/// Where a refusal should be rendered. Each case names the control that declined
/// to act, so the View can put the reason next to it.
enum ValidationScope: String {
    case scenarioCreate
    case workspace
    case scenarioLifecycle
    case participantAdd
    case participantAction
    case policy
    case delivery
    case resource
    case project
}

struct ValidationNotice: Identifiable, Equatable {
    let scope: ValidationScope
    let message: String

    var id: String { "\(scope.rawValue):\(message)" }
}

@MainActor
final class HarnessViewModel: ObservableObject {
    @Published var hostStatus = "Connecting…"
    @Published var projects: [ProjectRecord] = []
    @Published var selectedProjectID: String?
    @Published var scenarios: [ScenarioRecord] = []
    @Published var selectedScenarioID: String?
    @Published var participants: [ParticipantRecord] = []
    @Published var resources: [ResourceLeaseRecord] = []
    @Published var preflight: ScenarioPreflightRecord?
    @Published var topology: ScenarioTopologyRecord?
    @Published var templates: [ParticipantTemplate] = []
    @Published var selectedTemplateID: String?
    @Published var policyTemplates: [PolicyTemplateRecord] = []
    @Published var selectedPolicyTemplateID: String?
    @Published var policyPlan: PolicyPlanRecord?
    @Published var policyStatus: PolicyStatusRecord?
    @Published var deliveries: [DeliveryRecord] = []
    @Published var deliveryTotal = 0
    @Published var deliveryStates: [String: Int] = [:]
    @Published var nextDeliveryPage: DeliveryNextPage?
    @Published var policyMessage = "Select a Scenario to inspect its collaboration policy."
    @Published var deliveryMessage = "Select a Scenario to inspect delivery health."
    @Published var newScenarioID = "research-\(HarnessViewModel.shortTimestamp())"
    @Published var newParticipantID = "analyst"
    /// Set only while a mutation is in flight. Read-only refreshes deliberately
    /// leave it false so browsing never disables the window.
    @Published var isBusy = false
    @Published var activityText: String?
    @Published var activeOperationID: String?
    @Published var operationProgressText: String?
    @Published var operationCanCancel = false
    @Published var errorMessage: String?
    @Published var actionableError: ActionableErrorRecord?
    /// Confirms a completed mutation. Cleared automatically; see `noteSuccess`.
    @Published var successMessage: String?
    /// Explains why a request could not even be attempted, so no control can
    /// fail silently. Scoped so the reason renders next to the control that
    /// refused, not in a single ambiguous global slot.
    @Published var validation: ValidationNotice?
    @Published var diagnosticText = "Select a Scenario to inspect diagnostics."
    @Published var resourceText = "Select a Scenario to inspect resources."
    @Published var policyText = "Select a Scenario to inspect policy."
    @Published var receiptText = "A provision receipt will appear here."
    @Published var resumeText = "Resume a closed Scenario to restore its previous running participants."
    @Published var destroyPreviewText = ""
    @Published var destroyPreviewEligible = false

    let client: HarnessIPCClient
    private let serviceController: HarnessServiceController?
    private var activeProgressSessionID: UUID?
    private var successToken: UUID?

    init(
        client: HarnessIPCClient = HarnessIPCClient(),
        serviceController: HarnessServiceController? = nil
    ) {
        self.client = client
        self.serviceController = serviceController
    }

    var selectedProject: ProjectRecord? {
        projects.first { $0.id == selectedProjectID }
    }

    var selectedScenario: ScenarioRecord? {
        scenarios.first { $0.id == selectedScenarioID }
    }

    var selectedTemplate: ParticipantTemplate? {
        templates.first { $0.id == selectedTemplateID }
    }

    var selectedPolicyTemplate: PolicyTemplateRecord? {
        policyTemplates.first { $0.id == selectedPolicyTemplateID }
    }

    /// Templates an employee is meant to pick from. A headless template has no
    /// window and cannot take part in the collaboration the product exists for,
    /// so it does not belong beside the real agents. Both the add form and the
    /// replace menu read this, so the two can never drift apart.
    var interactiveTemplates: [ParticipantTemplate] {
        templates.filter { !$0.isHeadless }
    }

    /// Diagnostic templates, for an explicitly advanced surface only.
    var diagnosticTemplates: [ParticipantTemplate] {
        templates.filter(\.isHeadless)
    }

    var runningParticipantCount: Int {
        participants.filter { $0.observedState == "ready" }.count
    }

    /// One line of plain language for the Scenario header, in place of the
    /// `desired X · observed Y` protocol pair.
    var scenarioHeadline: String {
        guard let scenario = selectedScenario else { return "No Scenario selected" }
        let people = participants.count
        let running = runningParticipantCount
        let team: String
        switch (people, running) {
        case (0, _):
            team = "no participants yet"
        case (_, 0):
            team = people == 1 ? "1 participant, none running" : "\(people) participants, none running"
        case (running, _) where running == people:
            team = people == 1 ? "1 participant running" : "all \(people) participants running"
        default:
            team = "\(running) of \(people) participants running"
        }
        return "\(Self.humanState(scenario.observedState)) · \(team)"
    }

    /// Machine state to plain language. Presentation only: the Host keeps
    /// emitting machine states, and the App does the translating.
    static func humanState(_ state: String) -> String {
        switch state {
        case "ready": "Ready"
        case "running": "Running"
        case "stopped": "Stopped"
        case "detached": "Detached"
        case "closed": "Closed"
        case "degraded": "Needs attention"
        case "provision_failed": "Workspace setup failed"
        case "provisioning": "Setting up workspace"
        case "opening": "Resuming"
        case "closing": "Closing"
        case "starting": "Starting"
        case "stopping": "Stopping"
        case "recovering": "Recovering"
        case "repairing": "Repairing"
        case "replacing": "Replacing"
        case "destroying": "Deleting"
        default: state.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    func bootstrap() async {
        await performRead {
            if let serviceController = self.serviceController {
                let serviceStatus = try await serviceController.ensureRegistered()
                self.hostStatus = "starting · \(serviceStatus.label)"
                try await self.waitForHost()
            }
            let status = try await self.client.call(
                HarnessCall(operation: "host.status", target: ["scope": "host"])
            )
            self.hostStatus = status["status"] as? String ?? "ready"
            try await self.reloadProjects()
            try await self.reloadTemplates()
            try await self.reloadPolicyTemplates()
        }
    }

    func retryHostService() async {
        await bootstrap()
    }

    func chooseAndRegisterProject() async {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Register Project"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        await performMutation(
            activity: "Registering \(url.lastPathComponent)…",
            scope: .project,
            success: "Registered \(url.lastPathComponent)."
        ) {
            try self.client.grantProjectDirectoryAccess(url)
            let result = try await self.client.call(
                HarnessCall(
                    operation: "project.register",
                    target: ["scope": "host"],
                    fence: ["operation_generation": 0],
                    payload: ["canonical_project_path": url.path]
                )
            )
            guard
                let raw = result["project"] as? [String: Any],
                let project = ProjectRecord(raw)
            else { throw HarnessIPCError.invalidReply }
            try await self.reloadProjects()
            self.selectedProjectID = project.id
            try await self.reloadScenarios()
            try await self.reloadPolicyTemplates()
        }
    }

    func unregisterProject(_ project: ProjectRecord) async {
        await performMutation(
            activity: "Unregistering \(project.key)…",
            scope: .project,
            success: "Unregistered \(project.key)."
        ) {
            _ = try await self.client.call(
                HarnessCall(
                    operation: "project.unregister",
                    target: ["scope": "host"],
                    fence: ["operation_generation": 0],
                    payload: ["project_instance_id": project.id]
                )
            )
            if self.selectedProjectID == project.id {
                self.selectedProjectID = nil
                self.selectedScenarioID = nil
                self.scenarios = []
                self.participants = []
                self.resources = []
                self.preflight = nil
                self.topology = nil
                self.clearDestroyPreview()
                self.clearCollaborationValues()
            }
            try await self.reloadProjects()
        }
    }

    func selectProject(_ id: String?) async {
        selectedProjectID = id
        selectedScenarioID = nil
        participants = []
        resources = []
        preflight = nil
        topology = nil
        clearDestroyPreview()
        clearCollaborationValues()
        await performRead {
            try await self.reloadScenarios()
            try await self.reloadPolicyTemplates()
        }
    }

    func selectScenario(_ id: String?) async {
        selectedScenarioID = id
        clearDestroyPreview()
        await refreshSelectedScenario()
    }

    func runPreflight() async {
        await performRead {
            try await self.reloadPreflight()
        }
    }

    func focusScenario() async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.scenarioLifecycle, "Select a Scenario first.")
        }
        await performMutation(
            activity: "Focusing and restoring Scenario windows…",
            scope: .scenarioLifecycle,
            success: "Restored the Scenario windows."
        ) {
            let result = try await self.client.call(
                HarnessCall(
                    operation: "scenario.focus",
                    target: self.scenarioTarget(
                        projectID: project.id, scenarioID: scenario.id
                    ),
                    fence: ["operation_generation": scenario.stateRevision],
                    payload: self.scenarioFencePayload(scenario)
                )
            )
            try self.applyTopologyResult(result)
        }
    }

    func performRepairAction(_ action: String) async {
        switch action {
        case "host.retry":
            await retryHostService()
        case "project.register":
            await chooseAndRegisterProject()
        case "scenario.refresh":
            await refreshSelectedScenario()
        case "scenario.preflight":
            await runPreflight()
        case "workspace.prepare":
            await prepareWorkspace()
        case "participant.recover":
            if let participant = participants.filter(\.canRecover).only {
                await recoverParticipant(participant)
            }
        case "system-settings.automation":
            if let url = URL(
                string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"
            ) {
                NSWorkspace.shared.open(url)
            }
        default:
            break
        }
    }

    func repairActionLabel(_ action: String) -> String {
        switch action {
        case "host.retry": "Retry Host"
        case "project.register": "Register Project Again"
        case "scenario.refresh": "Refresh Scenario"
        case "scenario.preflight": "Run Preflight Again"
        case "workspace.prepare": "Prepare Workspace"
        case "participant.recover": "Recover Participant"
        case "scenario.repair": "Use Repair Scenario Below"
        case "system-settings.automation": "Open Automation Settings"
        case "participant.driver-configure": "Configure Presentation Driver"
        case "host.update": "Update or Reinstall AI Collab"
        case "iterm-presentation.enable-python-api": "Enable Presentation Automation"
        case "iterm-presentation.remove-authentication-bypass":
            "Restore Authenticated Presentation API"
        default: "Follow \(action)"
        }
    }

    func canPerformRepairAction(_ action: String) -> Bool {
        switch action {
        case "host.retry", "project.register", "scenario.refresh", "scenario.preflight",
             "workspace.prepare", "system-settings.automation":
            true
        case "participant.recover":
            participants.filter(\.canRecover).count == 1
        default:
            false
        }
    }

    func createScenario() async {
        guard let project = selectedProject else {
            return refuse(.scenarioCreate, "Register or select a project first.")
        }
        let scenarioID = newScenarioID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !scenarioID.isEmpty else {
            return refuse(.scenarioCreate, "Give the Scenario a name.")
        }
        guard !scenarios.contains(where: { $0.id == scenarioID }) else {
            return refuse(.scenarioCreate, "This project already has a Scenario named “\(scenarioID)”.")
        }
        await performMutation(
            activity: "Creating Scenario \(scenarioID)…",
            scope: .scenarioCreate,
            success: "Created Scenario \(scenarioID)."
        ) {
            let result = try await self.client.call(
                HarnessCall(
                    operation: "scenario.create",
                    target: self.scenarioTarget(projectID: project.id, scenarioID: scenarioID),
                    fence: ["operation_generation": 0],
                    payload: ["project_binding_digest": project.bindingDigest]
                )
            )
            guard result["scenario"] as? [String: Any] != nil else {
                throw HarnessIPCError.invalidReply
            }
            try await self.reloadScenarios()
            self.selectedScenarioID = scenarioID
            self.newScenarioID = "research-\(Self.shortTimestamp())"
            try await self.refreshSelectedScenarioValues()
        }
    }

    func prepareWorkspace() async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.workspace, "Select a Scenario first.")
        }
        await performMutation(
            activity: "Planning the isolated Workspace…",
            scope: .workspace,
            success: "Workspace is ready."
        ) {
            let target = self.scenarioTarget(projectID: project.id, scenarioID: scenario.id)
            let planned = try await self.client.call(
                HarnessCall(
                    operation: "workspace.plan",
                    target: target,
                    fence: ["operation_generation": scenario.stateRevision],
                    payload: [
                        "scenario_generation": scenario.generation,
                        "scenario_state_revision": scenario.stateRevision,
                        "requested_component_ids": [],
                        "project_payload": ["environment_mode": "minimal-editable"],
                    ],
                    responseTimeoutSeconds: 360
                )
            )
            guard
                let workspace = planned["workspace"] as? [String: Any],
                let planDigest = workspace["plan_digest"] as? String
            else { throw HarnessIPCError.invalidReply }
            self.activityText =
                "Cloning repositories into the isolated Workspace… "
                + "This is the long step and can take a few minutes."
            let provisioned = try await self.client.call(
                HarnessCall(
                    operation: "workspace.provision",
                    target: target,
                    fence: ["operation_generation": scenario.stateRevision],
                    payload: [
                        "scenario_generation": scenario.generation,
                        "scenario_state_revision": scenario.stateRevision,
                        "plan_digest": planDigest,
                    ],
                    responseTimeoutSeconds: 360
                )
            )
            self.receiptText = prettyJSON(
                (provisioned["workspace"] as? [String: Any])?["receipt"]
            )
            try await self.refreshSelectedScenarioValues()
        }
    }

    func openScenario() async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.scenarioLifecycle, "Select a Scenario first.")
        }
        // The Host allows this up to 360s while it restores each participant's
        // previous conversation, so it must never run without something on screen.
        await performMutation(
            activity: "Resuming \(scenario.id)… restoring each participant's previous session.",
            scope: .scenarioLifecycle,
            success: "Resumed \(scenario.id)."
        ) {
            let result = try await self.client.call(
                HarnessCall(
                    operation: "scenario.open",
                    target: self.scenarioTarget(projectID: project.id, scenarioID: scenario.id),
                    fence: ["operation_generation": scenario.stateRevision],
                    payload: self.scenarioFencePayload(scenario),
                    responseTimeoutSeconds: 360
                )
            )
            self.resumeText = prettyJSON(result["resume_summary"])
            try await self.reloadScenarios()
            try await self.refreshSelectedScenarioValues()
        }
    }

    func closeScenario() async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.scenarioLifecycle, "Select a Scenario first.")
        }
        let progressSessionID = UUID()
        await performMutation(
            activity: "Closing \(scenario.id) safely…",
            scope: .scenarioLifecycle,
            success: "Closed \(scenario.id)."
        ) {
            self.activeProgressSessionID = progressSessionID
            defer {
                if self.activeProgressSessionID == progressSessionID {
                    self.activeProgressSessionID = nil
                    self.activeOperationID = nil
                    self.operationCanCancel = false
                    self.operationProgressText = nil
                }
            }
            do {
                _ = try await self.client.call(
                    HarnessCall(
                        operation: "scenario.close",
                        target: self.scenarioTarget(
                            projectID: project.id, scenarioID: scenario.id
                        ),
                        fence: ["operation_generation": scenario.stateRevision],
                        payload: [
                            "scenario_generation": scenario.generation,
                            "scenario_state_revision": scenario.stateRevision,
                            "drain_timeout_ms": 30_000,
                        ],
                        responseTimeoutSeconds: 360
                    ),
                    progress: { [weak self] progress in
                        Task { @MainActor in
                            self?.applyProgress(
                                progress, progressSessionID: progressSessionID
                            )
                        }
                    }
                )
            } catch {
                try? await self.reloadScenarios()
                try? await self.refreshSelectedScenarioValues()
                throw error
            }
            try await self.reloadScenarios()
            try await self.refreshSelectedScenarioValues()
        }
    }

    func cancelActiveOperation() async {
        guard let operationID = activeOperationID, operationCanCancel else { return }
        do {
            _ = try await client.cancelOperation(operationID)
            operationCanCancel = false
            operationProgressText = "Cancellation accepted · finishing the current safe boundary"
        } catch {
            let actionable = ActionableErrorRecord(error)
            actionableError = actionable
            errorMessage = actionable.message
        }
    }

    func repairScenario() async {
        await mutateScenario(
            operation: "scenario.repair",
            activity: "Repairing the Scenario… WIP and history are preserved.",
            success: "Repair finished.",
            extraPayload: [:]
        )
    }

    func addParticipant() async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.participantAdd, "Select a Scenario first.")
        }
        guard let template = selectedTemplate else {
            return refuse(.participantAdd, "Choose a template for the new participant.")
        }
        let participantID = newParticipantID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !participantID.isEmpty else {
            return refuse(.participantAdd, "Give the participant a name.")
        }
        guard !participants.contains(where: { $0.id == participantID }) else {
            return refuse(
                .participantAdd,
                "This Scenario already has a participant named “\(participantID)”."
            )
        }
        await performMutation(
            activity: "Adding \(participantID)…",
            scope: .participantAdd,
            success: "Added \(participantID)."
        ) {
            _ = try await self.client.call(
                HarnessCall(
                    operation: "participant.add",
                    target: self.participantTarget(
                        projectID: project.id,
                        scenarioID: scenario.id,
                        participantID: participantID
                    ),
                    fence: ["operation_generation": 0, "participant_generation": 0],
                    payload: [
                        "scenario_generation": scenario.generation,
                        "scenario_state_revision": scenario.stateRevision,
                        "launch_spec": template.launchSpec,
                        "presentation_driver_id": template.presentationDriverID ?? NSNull(),
                    ]
                )
            )
            try await self.reloadParticipants(project: project, scenario: scenario)
        }
    }

    func startParticipant(_ participant: ParticipantRecord) async {
        guard participant.canStart else {
            return refuse(
                .participantAction,
                "\(participant.id) is \(Self.humanState(participant.observedState).lowercased()); "
                    + "only a stopped participant can be started."
            )
        }
        await mutateParticipant(
            operation: "participant.start",
            participant: participant,
            activity: "Starting \(participant.id)…",
            success: "\(participant.id) is running."
        )
    }

    func stopParticipant(_ participant: ParticipantRecord) async {
        guard participant.canStop else {
            return refuse(
                .participantAction,
                "\(participant.id) is \(Self.humanState(participant.observedState).lowercased()); "
                    + "there is nothing to stop."
            )
        }
        await mutateParticipant(
            operation: "participant.stop",
            participant: participant,
            activity: "Stopping \(participant.id)…",
            success: "\(participant.id) is stopped."
        )
    }

    func recoverParticipant(_ participant: ParticipantRecord) async {
        guard participant.canRecover else {
            return refuse(.participantAction, "\(participant.id) has nothing to recover.")
        }
        await mutateParticipant(
            operation: "participant.recover",
            participant: participant,
            activity: "Recovering \(participant.id)…",
            success: "Recovered \(participant.id)."
        )
    }

    func forceStopParticipant(_ participant: ParticipantRecord) async {
        guard participant.canForceStop else {
            return refuse(
                .participantAction,
                "\(participant.id) has no Harness-owned process to force stop."
            )
        }
        await mutateParticipant(
            operation: "participant.force-stop",
            participant: participant,
            activity: "Force stopping \(participant.id)…",
            success: "Force stopped \(participant.id)."
        )
    }

    /// The template is passed in explicitly. It used to be read from
    /// `selectedTemplate`, which belongs to the unrelated "add participant" form,
    /// so Replace could silently rebuild a participant from whichever template
    /// that picker happened to be showing.
    func replaceParticipant(
        _ participant: ParticipantRecord,
        template: ParticipantTemplate
    ) async {
        await mutateParticipant(
            operation: "participant.replace",
            participant: participant,
            activity: "Replacing \(participant.id) with \(template.displayName)…",
            success: "Replaced \(participant.id) with \(template.displayName).",
            extraPayload: [
                "launch_spec": template.launchSpec,
                "presentation_driver_id": template.presentationDriverID ?? NSNull(),
            ]
        )
    }

    func recreateParticipantWithHandoff(_ participant: ParticipantRecord) async {
        guard participant.canRecreateWithHandoff else {
            return refuse(
                .participantAction,
                "\(participant.id) is not waiting on a failed conversation recovery."
            )
        }
        // Deliberately no fallback to `selectedTemplate`: recreating against an
        // unrelated template would hand the participant a different runtime.
        guard
            let template = templates.first(where: {
                ($0.launchSpec["runtime_profile_ref"] as? String)
                    == participant.runtimeProfileRef
            })
        else {
            return refuse(
                .participantAction,
                "No installed template matches \(participant.id)'s runtime "
                    + "(\(participant.runtimeProfileRef ?? "driver default")), so it cannot be recreated."
            )
        }
        var launchSpec = template.launchSpec
        launchSpec["continuity_mode"] = "explicit_recreate"
        launchSpec["continuity_binding_ref"] = NSNull()
        if let modelBinding = participant.modelBinding {
            let modelBindingValue: [String: Any] = [
                "provider_profile_ref": modelBinding.providerProfileRef,
                "model_ref": modelBinding.modelRef,
                "inference_profile_ref": modelBinding.inferenceProfileRef as Any?
                    ?? NSNull(),
            ]
            launchSpec["model_binding"] = modelBindingValue
        } else {
            launchSpec["model_binding"] = NSNull()
        }
        await mutateParticipant(
            operation: "participant.replace",
            participant: participant,
            activity: "Recreating \(participant.id) with a new conversation…",
            success: "\(participant.id) now has a new Agent conversation.",
            extraPayload: [
                "launch_spec": launchSpec,
                "presentation_driver_id": template.presentationDriverID ?? NSNull(),
            ]
        )
    }

    func breakResource(_ resource: ResourceLeaseRecord) async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.resource, "Select a Scenario first.")
        }
        guard resource.canBreak else {
            return refuse(.resource, "This lease is not stale, so it will not be released.")
        }
        await performMutation(
            activity: "Releasing the stale \(resource.resourceClass) lease…",
            scope: .resource,
            success: "Released the stale \(resource.resourceClass) lease."
        ) {
            _ = try await self.client.call(
                HarnessCall(
                    operation: "resource.break",
                    target: self.scenarioTarget(
                        projectID: project.id, scenarioID: scenario.id
                    ),
                    fence: ["operation_generation": scenario.stateRevision],
                    payload: [
                        "scenario_generation": scenario.generation,
                        "scenario_state_revision": scenario.stateRevision,
                        "lease_id": resource.id,
                        "lease_revision": resource.revision,
                    ]
                )
            )
            try await self.reloadScenarios()
            try await self.refreshSelectedScenarioValues()
        }
    }

    func loadDestroyPreview() async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.scenarioLifecycle, "Select a Scenario first.")
        }
        await performRead {
            let result = try await self.client.call(
                HarnessCall(
                    operation: "scenario.destroy.preview",
                    target: self.scenarioTarget(projectID: project.id, scenarioID: scenario.id),
                    payload: self.scenarioFencePayload(scenario)
                )
            )
            guard
                let preview = result["effect_preview"] as? [String: Any],
                let eligible = preview["eligible"] as? Bool
            else { throw HarnessIPCError.invalidReply }
            self.destroyPreviewText = prettyJSON(result)
            self.destroyPreviewEligible = eligible
        }
    }

    func destroyScenario() async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.scenarioLifecycle, "Select a Scenario first.")
        }
        guard destroyPreviewEligible else {
            return refuse(
                .scenarioLifecycle,
                "Load the destroy preview first so the exact effect is known."
            )
        }
        await performMutation(
            activity: "Deleting \(scenario.id)…",
            scope: .scenarioLifecycle,
            success: "Deleted \(scenario.id)."
        ) {
            _ = try await self.client.call(
                HarnessCall(
                    operation: "scenario.destroy",
                    target: self.scenarioTarget(projectID: project.id, scenarioID: scenario.id),
                    fence: ["operation_generation": scenario.stateRevision],
                    payload: self.scenarioFencePayload(scenario)
                )
            )
            self.destroyPreviewText = ""
            self.destroyPreviewEligible = false
            self.selectedScenarioID = nil
            self.participants = []
            self.resources = []
            self.clearCollaborationValues()
            try await self.reloadScenarios()
        }
    }

    func forceDestroyScenario(_ scenario: ScenarioRecord) async {
        guard let project = selectedProject else {
            return refuse(.scenarioLifecycle, "Select a project first.")
        }
        await performMutation(
            activity: "Force deleting \(scenario.id)…",
            scope: .scenarioLifecycle,
            success: "Deleted \(scenario.id) and its isolated Workspace."
        ) {
            _ = try await self.client.call(
                HarnessCall(
                    operation: "scenario.force-destroy",
                    target: self.scenarioTarget(
                        projectID: project.id, scenarioID: scenario.id
                    ),
                    fence: ["operation_generation": scenario.stateRevision],
                    payload: self.scenarioFencePayload(scenario)
                )
            )
            if self.selectedScenarioID == scenario.id {
                self.clearDestroyPreview()
                self.selectedScenarioID = nil
                self.participants = []
                self.resources = []
                self.clearCollaborationValues()
            }
            try await self.reloadScenarios()
        }
    }

    func refreshSelectedScenario() async {
        await performRead { try await self.refreshSelectedScenarioValues() }
    }

    func selectPolicyTemplate(_ id: String?) {
        selectedPolicyTemplateID = id
        policyPlan = nil
        policyMessage = "Preview the selected template before applying it."
    }

    func planSelectedPolicy() async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.policy, "Select a Scenario first.")
        }
        guard let template = selectedPolicyTemplate else {
            return refuse(.policy, "Choose a team template to preview.")
        }
        await performMutation(
            activity: "Previewing the \(template.displayName) plan…",
            scope: .policy
        ) {
            let result = try await self.client.call(
                HarnessCall(
                    operation: "policy.plan",
                    target: self.scenarioTarget(
                        projectID: project.id, scenarioID: scenario.id
                    ),
                    fence: ["operation_generation": scenario.stateRevision],
                    payload: [
                        "scenario_generation": scenario.generation,
                        "scenario_state_revision": scenario.stateRevision,
                        "template_id": template.id,
                    ]
                )
            )
            guard
                let raw = result["policy_plan"] as? [String: Any],
                let plan = PolicyPlanRecord(raw)
            else { throw HarnessIPCError.invalidReply }
            self.policyPlan = plan
            self.policyMessage = plan.canApply
                ? "Plan is current and ready for explicit apply."
                : "Plan is blocked. Resolve the listed team requirements and plan again."
        }
    }

    func applySelectedPolicyPlan() async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.policy, "Select a Scenario first.")
        }
        guard let template = selectedPolicyTemplate else {
            return refuse(.policy, "Choose a team template first.")
        }
        guard let plan = policyPlan else {
            return refuse(.policy, "Preview the plan before applying it.")
        }
        guard plan.templateID == template.id else {
            return refuse(
                .policy,
                "The previewed plan is for a different template. Preview \(template.displayName) again."
            )
        }
        guard plan.canApply else {
            return refuse(.policy, "This plan is blocked. Resolve the listed requirements, then preview again.")
        }
        // This is the branch that used to fail silently while the button stayed
        // enabled: the Scenario moved on after the preview was taken.
        guard
            plan.scenarioGeneration == scenario.generation,
            plan.scenarioStateRevision == scenario.stateRevision
        else {
            return refuse(
                .policy,
                "The Scenario changed after this plan was previewed. Preview it again to pick up the current state."
            )
        }
        await performMutation(
            activity: "Applying the \(template.displayName) plan…",
            scope: .policy,
            success: "Applied the \(template.displayName) policy."
        ) {
            _ = try await self.client.call(
                HarnessCall(
                    operation: "policy.apply-plan",
                    target: self.scenarioTarget(
                        projectID: project.id, scenarioID: scenario.id
                    ),
                    fence: ["operation_generation": scenario.stateRevision],
                    payload: [
                        "scenario_generation": scenario.generation,
                        "scenario_state_revision": scenario.stateRevision,
                        "template_id": template.id,
                        "plan_digest": plan.planDigest,
                    ]
                )
            )
            self.policyPlan = nil
            try await self.reloadPolicy(project: project, scenario: scenario)
            self.policyMessage = "Policy applied. Agents can use their own PingAgent send/reply commands."
        }
    }

    func loadMoreDeliveries() async {
        guard
            let project = selectedProject,
            let scenario = selectedScenario,
            let cursor = nextDeliveryPage
        else { return refuse(.delivery, "There are no further delivery records to load.") }
        await performRead {
            let page = try await self.fetchDeliveries(
                project: project,
                scenario: scenario,
                afterDeliveryID: cursor.afterDeliveryID,
                collectionDigest: cursor.collectionDigest
            )
            self.deliveries.append(contentsOf: page.deliveries)
            self.deliveryTotal = page.total
            self.deliveryStates = page.states
            self.nextDeliveryPage = page.nextPage
        }
    }

    func monitorDeliveries(for scenarioID: String) async {
        while !Task.isCancelled {
            guard
                selectedScenarioID == scenarioID,
                let project = selectedProject,
                let scenario = selectedScenario
            else { return }
            do {
                let page = try await fetchDeliveries(
                    project: project,
                    scenario: scenario
                )
                guard selectedScenarioID == scenarioID else { return }
                deliveries = page.deliveries
                deliveryTotal = page.total
                deliveryStates = page.states
                nextDeliveryPage = page.nextPage
                deliveryMessage = page.total == 0
                    ? "No Agent delivery has been recorded for this Scenario yet."
                    : "Showing \(page.deliveries.count) of \(page.total) delivery records."
            } catch is CancellationError {
                return
            } catch {
                guard selectedScenarioID == scenarioID else { return }
                deliveryMessage =
                    "Live delivery refresh is temporarily unavailable. \(error.localizedDescription)"
            }
            do {
                try await Task.sleep(nanoseconds: 2_000_000_000)
            } catch {
                return
            }
        }
    }

    func retryDelivery(_ delivery: DeliveryRecord) async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.delivery, "Select a Scenario first.")
        }
        guard delivery.retryEligible else {
            return refuse(.delivery, "This delivery is not eligible for a retry.")
        }
        await performMutation(
            activity: "Retrying delivery \(String(delivery.id.prefix(12)))…",
            scope: .delivery,
            success: "Delivery retry accepted."
        ) {
            _ = try await self.client.call(
                HarnessCall(
                    operation: "delivery.retry",
                    target: self.scenarioTarget(
                        projectID: project.id, scenarioID: scenario.id
                    ),
                    fence: ["operation_generation": delivery.eventSequence],
                    payload: [
                        "delivery_id": delivery.id,
                        "event_sequence": delivery.eventSequence,
                    ]
                )
            )
            try await self.reloadDeliveries(project: project, scenario: scenario)
        }
    }

    private func mutateScenario(
        operation: String,
        activity: String,
        success: String,
        extraPayload: [String: Any]
    ) async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.scenarioLifecycle, "Select a Scenario first.")
        }
        await performMutation(
            activity: activity,
            scope: .scenarioLifecycle,
            success: success
        ) {
            var payload = self.scenarioFencePayload(scenario)
            payload.merge(extraPayload) { _, new in new }
            _ = try await self.client.call(
                HarnessCall(
                    operation: operation,
                    target: self.scenarioTarget(projectID: project.id, scenarioID: scenario.id),
                    fence: ["operation_generation": scenario.stateRevision],
                    payload: payload
                )
            )
            try await self.reloadScenarios()
            try await self.refreshSelectedScenarioValues()
        }
    }

    private func mutateParticipant(
        operation: String,
        participant: ParticipantRecord,
        activity: String,
        success: String,
        extraPayload: [String: Any] = [:]
    ) async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.participantAction, "Select a Scenario first.")
        }
        await performMutation(
            activity: activity,
            scope: .participantAction,
            success: success
        ) {
            var payload: [String: Any] = [
                "scenario_generation": scenario.generation,
                "scenario_state_revision": scenario.stateRevision,
                "participant_state_revision": participant.stateRevision,
            ]
            for (key, value) in extraPayload {
                payload[key] = value
            }
            _ = try await self.client.call(
                HarnessCall(
                    operation: operation,
                    target: self.participantTarget(
                        projectID: project.id,
                        scenarioID: scenario.id,
                        participantID: participant.id
                    ),
                    fence: [
                        "operation_generation": participant.stateRevision,
                        "participant_generation": participant.generation,
                    ],
                    payload: payload
                )
            )
            try await self.reloadScenarios()
            try await self.refreshSelectedScenarioValues()
        }
    }

    private func reloadProjects() async throws {
        let result = try await client.call(
            HarnessCall(operation: "project.list", target: ["scope": "host"])
        )
        projects = dictionaries(result["projects"]).compactMap(ProjectRecord.init)
        if selectedProjectID == nil || !projects.contains(where: { $0.id == selectedProjectID }) {
            selectedProjectID = projects.first?.id
        }
        try await reloadScenarios()
    }

    private func reloadTemplates() async throws {
        let result = try await client.call(
            HarnessCall(operation: "participant.template.list", target: ["scope": "host"])
        )
        templates = dictionaries(result["templates"]).compactMap(ParticipantTemplate.init)
        if selectedTemplateID == nil { selectedTemplateID = templates.first?.id }
    }

    private func reloadPolicyTemplates() async throws {
        guard let project = selectedProject else {
            policyTemplates = []
            selectedPolicyTemplateID = nil
            policyPlan = nil
            return
        }
        let result = try await client.call(
            HarnessCall(
                operation: "policy.template.list",
                target: ["scope": "project", "project_instance_id": project.id]
            )
        )
        let rawTemplates = dictionaries(result["templates"])
        let parsedTemplates = rawTemplates.compactMap(PolicyTemplateRecord.init)
        guard parsedTemplates.count == rawTemplates.count else {
            throw HarnessIPCError.invalidReply
        }
        policyTemplates = parsedTemplates
        if selectedPolicyTemplateID == nil
            || !policyTemplates.contains(where: { $0.id == selectedPolicyTemplateID })
        {
            selectedPolicyTemplateID = policyTemplates.first?.id
            policyPlan = nil
        }
    }

    private func reloadScenarios() async throws {
        guard let project = selectedProject else {
            scenarios = []
            return
        }
        let result = try await client.call(
            HarnessCall(
                operation: "scenario.list",
                target: ["scope": "project", "project_instance_id": project.id]
            )
        )
        scenarios = dictionaries(result["scenarios"]).compactMap(ScenarioRecord.init)
        if let selectedScenarioID,
           !scenarios.contains(where: { $0.id == selectedScenarioID }) {
            self.selectedScenarioID = nil
        }
    }

    private func refreshSelectedScenarioValues() async throws {
        clearDestroyPreview()
        guard let project = selectedProject, let scenarioID = selectedScenarioID else {
            participants = []
            resources = []
            preflight = nil
            topology = nil
            clearCollaborationValues()
            return
        }
        let target = scenarioTarget(projectID: project.id, scenarioID: scenarioID)
        let status = try await client.call(
            HarnessCall(operation: "scenario.status", target: target)
        )
        if let raw = status["scenario"] as? [String: Any], let current = ScenarioRecord(raw) {
            if let index = scenarios.firstIndex(where: { $0.id == current.id }) {
                scenarios[index] = current
            }
            try await reloadParticipants(project: project, scenario: current)
            if let plan = policyPlan,
               plan.scenarioGeneration != current.generation
                || plan.scenarioStateRevision != current.stateRevision
            {
                policyPlan = nil
            }
        }
        try await reloadPreflight()
        try await reloadTopology()
        let diagnosticResult = try await client.call(
            HarnessCall(operation: "scenario.diagnostic", target: target)
        )
        diagnosticText = prettyJSON(diagnosticResult)
        if
            let diagnostic = diagnosticResult["diagnostic"] as? [String: Any],
            let workspace = diagnostic["workspace"] as? [String: Any],
            let receipt = workspace["receipt"] as? [String: Any]
        {
            receiptText = prettyJSON(receipt)
        }
        let resourceResult = try await client.call(
            HarnessCall(operation: "resource.list", target: target)
        )
        let rawResources = dictionaries(resourceResult["resources"])
        let parsedResources = rawResources.compactMap(ResourceLeaseRecord.init)
        guard parsedResources.count == rawResources.count else {
            throw HarnessIPCError.invalidReply
        }
        resources = parsedResources
        resourceText = prettyJSON(resourceResult)
        guard let current = selectedScenario else { return }
        try await reloadPolicyTemplates()
        await reloadCollaboration(project: project, scenario: current)
    }

    private func reloadPreflight() async throws {
        guard let project = selectedProject, let scenarioID = selectedScenarioID else {
            preflight = nil
            return
        }
        let result = try await client.call(
            HarnessCall(
                operation: "scenario.preflight",
                target: scenarioTarget(projectID: project.id, scenarioID: scenarioID)
            )
        )
        guard
            let raw = result["preflight"] as? [String: Any],
            let parsed = ScenarioPreflightRecord(raw)
        else { throw HarnessIPCError.invalidReply }
        preflight = parsed
    }

    private func reloadTopology() async throws {
        guard let project = selectedProject, let scenarioID = selectedScenarioID else {
            topology = nil
            return
        }
        let result = try await client.call(
            HarnessCall(
                operation: "scenario.topology",
                target: scenarioTarget(projectID: project.id, scenarioID: scenarioID)
            )
        )
        try applyTopologyResult(result)
    }

    private func applyTopologyResult(_ result: [String: Any]) throws {
        guard
            let raw = result["topology"] as? [String: Any],
            let parsed = ScenarioTopologyRecord(raw)
        else { throw HarnessIPCError.invalidReply }
        topology = parsed
    }

    private func reloadCollaboration(project: ProjectRecord, scenario: ScenarioRecord) async {
        do {
            try await reloadPolicy(project: project, scenario: scenario)
        } catch {
            policyStatus = nil
            policyText = "No policy is active.\n\(error.localizedDescription)"
            policyMessage = "No active policy. Preview a team template to continue."
        }
        do {
            try await reloadDeliveries(project: project, scenario: scenario)
            deliveryMessage = deliveryTotal == 0
                ? "No Agent delivery has been recorded for this Scenario yet."
                : "Showing \(deliveries.count) of \(deliveryTotal) delivery records."
        } catch {
            deliveries = []
            deliveryTotal = 0
            deliveryStates = [:]
            nextDeliveryPage = nil
            deliveryMessage = "Delivery health is unavailable. \(error.localizedDescription)"
        }
    }

    private func reloadPolicy(project: ProjectRecord, scenario: ScenarioRecord) async throws {
        let result = try await client.call(
            HarnessCall(
                operation: "policy.show",
                target: scenarioTarget(projectID: project.id, scenarioID: scenario.id)
            )
        )
        guard let status = PolicyStatusRecord(result) else {
            throw HarnessIPCError.invalidReply
        }
        policyStatus = status
        policyText = prettyJSON(result)
        policyMessage = status.requiresReplan
            ? "Participant generations changed. Create and explicitly apply a repair plan."
            : "Active policy matches the current participant generations."
    }

    private func reloadDeliveries(project: ProjectRecord, scenario: ScenarioRecord) async throws {
        let page = try await fetchDeliveries(project: project, scenario: scenario)
        deliveries = page.deliveries
        deliveryTotal = page.total
        deliveryStates = page.states
        nextDeliveryPage = page.nextPage
    }

    private func fetchDeliveries(
        project: ProjectRecord,
        scenario: ScenarioRecord,
        afterDeliveryID: String? = nil,
        collectionDigest: String? = nil
    ) async throws -> DeliveryCollectionRecord {
        var payload: [String: Any] = ["limit": 100]
        if let afterDeliveryID, let collectionDigest {
            payload["after_delivery_id"] = afterDeliveryID
            payload["collection_digest"] = collectionDigest
        }
        let result = try await client.call(
            HarnessCall(
                operation: "delivery.list",
                target: scenarioTarget(projectID: project.id, scenarioID: scenario.id),
                payload: payload
            )
        )
        guard
            let raw = result["delivery_collection"] as? [String: Any],
            let page = DeliveryCollectionRecord(raw)
        else { throw HarnessIPCError.invalidReply }
        return page
    }

    private func clearCollaborationValues() {
        policyStatus = nil
        policyPlan = nil
        policyTemplates = []
        selectedPolicyTemplateID = nil
        deliveries = []
        deliveryTotal = 0
        deliveryStates = [:]
        nextDeliveryPage = nil
        policyMessage = "Select a Scenario to inspect its collaboration policy."
        deliveryMessage = "Select a Scenario to inspect delivery health."
    }

    private func clearDestroyPreview() {
        destroyPreviewText = ""
        destroyPreviewEligible = false
    }

    private func reloadParticipants(project: ProjectRecord, scenario: ScenarioRecord) async throws {
        let result = try await client.call(
            HarnessCall(
                operation: "participant.list",
                target: scenarioTarget(projectID: project.id, scenarioID: scenario.id)
            )
        )
        guard
            let participantValues = result["participants"] as? [Any],
            let configurationValues = result["participant_configurations"] as? [Any]
        else { throw HarnessIPCError.invalidReply }
        let rawParticipants = dictionaries(participantValues)
        let rawConfigurations = dictionaries(configurationValues)
        guard
            rawParticipants.count == participantValues.count,
            rawConfigurations.count == configurationValues.count
        else { throw HarnessIPCError.invalidReply }
        var configurationByParticipant: [String: [String: Any]] = [:]
        for configuration in rawConfigurations {
            guard
                let participantID = configuration["participant_id"] as? String,
                configurationByParticipant[participantID] == nil
            else { throw HarnessIPCError.invalidReply }
            configurationByParticipant[participantID] = configuration
        }
        guard rawParticipants.count == rawConfigurations.count else {
            throw HarnessIPCError.invalidReply
        }
        let parsed = rawParticipants.compactMap { value -> ParticipantRecord? in
            guard let participantID = value["participant_id"] as? String else {
                return nil
            }
            return ParticipantRecord(
                value,
                configuration: configurationByParticipant[participantID]
            )
        }
        guard parsed.count == rawParticipants.count else {
            throw HarnessIPCError.invalidReply
        }
        participants = parsed
    }

    private func applyProgress(
        _ progress: HarnessProgress,
        progressSessionID: UUID
    ) {
        guard activeProgressSessionID == progressSessionID else { return }
        activeOperationID = progress.operationID
        operationCanCancel = progress.cancellable
        let unitText = progress.totalUnits > 0
            ? "\(min(progress.completedUnits, progress.totalUnits))/\(progress.totalUnits)"
            : "0/0"
        let participantText = progress.participantID.map { " · \($0)" } ?? ""
        operationProgressText = "\(progress.state) · \(unitText)\(participantText)"
    }

    /// Read-only refreshes. Deliberately does not take the mutation lock and does
    /// not set `isBusy`, so browsing stays responsive while a mutation runs.
    private func performRead(
        _ work: @escaping @MainActor () async throws -> Void
    ) async {
        do {
            try await work()
            hostStatus = "ready"
        } catch {
            report(error)
        }
    }

    /// Mutations. `activity` is required rather than optional so that every
    /// mutation is structurally guaranteed to put something on screen while it
    /// runs — the long ones included. A second mutation is refused with a visible
    /// reason instead of being swallowed.
    private func performMutation(
        activity: String,
        scope: ValidationScope,
        success: String? = nil,
        _ work: @escaping @MainActor () async throws -> Void
    ) async {
        guard !isBusy else {
            refuse(
                scope,
                "\(activityText ?? "Another operation") is still running. "
                    + "Wait for it to finish, then try again."
            )
            return
        }
        isBusy = true
        activityText = activity
        errorMessage = nil
        actionableError = nil
        validation = nil
        defer {
            activityText = nil
            isBusy = false
        }
        do {
            try await work()
            hostStatus = "ready"
            if let success { noteSuccess(success) }
        } catch {
            report(error)
        }
    }

    private func report(_ error: Error) {
        let actionable = ActionableErrorRecord(error)
        actionableError = actionable
        errorMessage = actionable.message
        if case HarnessIPCError.hostUnavailable = error { hostStatus = "unavailable" }
    }

    /// Explains why a control could not act. Every branch that used to `return`
    /// silently now routes through here.
    private func refuse(_ scope: ValidationScope, _ reason: String) {
        validation = ValidationNotice(scope: scope, message: reason)
    }

    func dismissError() {
        actionableError = nil
        errorMessage = nil
    }

    func dismissSuccess() {
        successMessage = nil
    }

    func dismissValidation() {
        validation = nil
    }

    /// The refusal for a scope, if that is the one currently showing.
    func validationMessage(for scope: ValidationScope) -> String? {
        guard let validation, validation.scope == scope else { return nil }
        return validation.message
    }

    private func noteSuccess(_ message: String) {
        successMessage = message
        let token = UUID()
        successToken = token
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(4))
            guard let self, self.successToken == token else { return }
            self.successMessage = nil
        }
    }

    private func waitForHost() async throws {
        var lastError: Error = HarnessIPCError.hostUnavailable
        for attempt in 0..<40 {
            do {
                _ = try await client.call(
                    HarnessCall(operation: "host.status", target: ["scope": "host"])
                )
                return
            } catch {
                lastError = error
                if attempt < 39 {
                    try await Task.sleep(for: .milliseconds(250))
                }
            }
        }
        throw lastError
    }

    private func scenarioTarget(projectID: String, scenarioID: String) -> [String: Any] {
        ["scope": "scenario", "project_instance_id": projectID, "scenario_id": scenarioID]
    }

    private func participantTarget(
        projectID: String, scenarioID: String, participantID: String
    ) -> [String: Any] {
        [
            "scope": "participant",
            "project_instance_id": projectID,
            "scenario_id": scenarioID,
            "participant_id": participantID,
        ]
    }

    private func scenarioFencePayload(_ scenario: ScenarioRecord) -> [String: Any] {
        [
            "scenario_generation": scenario.generation,
            "scenario_state_revision": scenario.stateRevision,
        ]
    }

    private static func shortTimestamp() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMdd-HHmm"
        return formatter.string(from: Date())
    }
}

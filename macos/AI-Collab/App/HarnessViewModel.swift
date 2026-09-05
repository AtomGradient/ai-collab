// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import AppKit
import Darwin
import Foundation
import SwiftUI

/// Where a refusal should be rendered. Each case names the control that declined
/// to act, so the View can put the reason next to it.
enum ValidationScope: String {
    case scenarioCreate
    case workspace
    case scenarioLifecycle
    case objective
    case participantAdd
    case participantAction
    case policy
    case delivery
    case resource
    case project
}

struct ValidationNotice: Identifiable {
    let scope: ValidationScope
    let build: () -> String
    let id = UUID()

    /// Rendered at read time so a language switch retranslates instantly.
    var message: String { build() }
}

/// The rail's one next step, derived from live state on every render —
/// nothing is stored, so it can never drift from reality.
enum GuidanceStep: Equatable {
    case registerProject
    case createRoom
    case prepareWorkspace
    case addColleague
    case resumeRoom
    case configurePolicy
    case startColleagues
    case focusAndAssign
    case attend(String)
    case working(String)
    case inconsistent
}

/// Whether the Host's workspace receipt has actually been observed.
///
/// A Bool conflated "not read yet" with "read, and there is none". Selecting a
/// running room sets the record before the diagnostic returns, so the boolean
/// spent every bootstrap saying the workspace was missing — which the rail
/// fail-closed into a NEEDS YOU banner on a healthy room. Mirrors
/// `PolicyReadiness`: loading is transitional, unavailable is a failed read.
enum WorkspaceEvidence: Equatable {
    case loading
    case present
    case absent
    case unavailable
}

enum PolicyReadiness: Equatable {
    case loading
    case missing
    case current
    case replanRequired
    case unavailable
}

@MainActor
final class HarnessViewModel: ObservableObject {
    @Published var hostStatus = "connecting"

    /// Employee-facing host status, rendered at read time from the machine
    /// token so a language switch retranslates instantly.
    var hostStatusDisplay: String { S.Chrome.hostPhaseDisplay(hostStatus) }
    var hostReady: Bool { hostStatus == "ready" }
    @Published var projects: [ProjectRecord] = []
    @Published var projectReconciliations: [String: ProjectReconciliationRecord] = [:]
    @Published var selectedProjectID: String?
    @Published var pendingRegistrationURL: URL?
    @Published var scenarios: [ScenarioRecord] = []
    @Published var selectedScenarioID: String?
    @Published var participants: [ParticipantRecord] = []
    @Published var resources: [ResourceLeaseRecord] = []
    @Published var preflight: ScenarioPreflightRecord?
    @Published var presentationPermissionStatus: String?
    @Published var environmentObservations: [EnvironmentObservationRecord] = []
    @Published var topology: ScenarioTopologyRecord?
    @Published var templates: [ParticipantTemplate] = []
    @Published var selectedTemplateID: String?
    @Published var policyTemplates: [PolicyTemplateRecord] = []
    @Published var selectedPolicyTemplateID: String?
    @Published var policyPlan: PolicyPlanRecord?
    @Published var policyStatus: PolicyStatusRecord?
    /// Explicit because `nil` policyStatus used to conflate an absent policy
    /// with a failed read, which could not safely drive the setup flow.
    @Published var policyReadiness: PolicyReadiness = .loading
    @Published var deliveries: [DeliveryRecord] = []
    @Published var deliverySummary: DeliverySummaryRecord?
    @Published private var policyNote: (() -> String)?
    @Published private var deliveryNote: (() -> String)?
    var policyMessage: String { policyNote?() ?? S.Defaults.policy }
    var deliveryMessage: String { deliveryNote?() ?? S.Defaults.delivery }
    @Published var newScenarioID = "research-\(HarnessViewModel.shortTimestamp())"
    @Published var newScenarioObjective = ""
    @Published var objectiveDraft = ""
    @Published var acceptanceCriteriaDraft = ""
    @Published var newParticipantID = "analyst"
    /// Set only while a mutation is in flight. Read-only refreshes deliberately
    /// leave it false so browsing never disables the window.
    @Published var isBusy = false {
        didSet {
            // Every mutation boundary — entry and exit — advances the epoch,
            // so a live read that began before a mutation and returns after
            // it finished (busy already false again) is still recognised as
            // stale (codex review 20260904-032455-3mzo76 P1).
            if isBusy != oldValue { mutationEpoch &+= 1 }
        }
    }
    /// Monotonic count of `isBusy` transitions. Live-loop reads capture it
    /// before their await and may write only if it is unchanged.
    private(set) var mutationEpoch = 0
    @Published private var activityBuilder: (() -> String)?
    var activityText: String? { activityBuilder?() }
    @Published var activeOperationID: String?
    @Published private var progressBuilder: (() -> String)?
    var operationProgressText: String? { progressBuilder?() }
    @Published var operationCanCancel = false
    @Published var errorMessage: String?
    @Published var actionableError: ActionableErrorRecord?
    @Published private(set) var commandInstallationIssue: PingAgentInstallationResult?
    @Published var pendingCommandReplacement: PingAgentInstallationResult?
    var commandInstallationError: ActionableErrorRecord? {
        commandInstallationIssue.map { ActionableErrorRecord(PingAgentInstallationError(result: $0)) }
    }
    /// Confirms a completed mutation. Cleared automatically; see `noteSuccess`.
    @Published private var successBuilder: (() -> String)?
    var successMessage: String? { successBuilder?() }
    /// Live per-repository preparation rows (workspace-component-v1).
    @Published var workspaceProgress: [WorkspaceComponentProgress] = []
    var workspaceProgressHasFailure: Bool {
        workspaceProgress.contains { $0.state == "failed" }
    }

    /// True once the selected room's isolated workspace has a publish receipt.
    /// Internal so state-machine tests can stage it directly.
    @Published var workspaceEvidence: WorkspaceEvidence = .loading
    /// One-time "the room is ready" moment, keyed per room generation.
    @Published private(set) var showReadyMoment = false
    /// The getting-started card's open step. Lives on the model so a language
    /// switch (which rebuilds the view tree) never closes an open card.
    @Published var guideStep: Int?
    private let readyMomentDefaults: UserDefaults
    /// Explains why a request could not even be attempted, so no control can
    /// fail silently. Scoped so the reason renders next to the control that
    /// refused, not in a single ambiguous global slot.
    @Published var validation: ValidationNotice?
    @Published private var diagnosticOverride: String?
    @Published private var resourceOverride: String?
    @Published private var policyTextOverride: String?
    @Published private var receiptOverride: String?
    @Published private var resumeOverride: String?
    var diagnosticText: String { diagnosticOverride ?? S.Defaults.diagnostics }
    var resourceText: String { resourceOverride ?? S.Defaults.resources }
    var policyText: String { policyTextOverride ?? S.Defaults.policyText }
    var receiptText: String { receiptOverride ?? S.Defaults.receipt }
    var resumeText: String { resumeOverride ?? S.Defaults.resume }
    @Published var destroyPreviewText = ""
    @Published var destroyPreviewEligible = false
    @Published var destroyPreviewBlockers: [String] = []
    var destroyPreviewLoaded: Bool { !destroyPreviewText.isEmpty }
    var destroyPreviewBlocked: Bool { destroyPreviewLoaded && !destroyPreviewEligible }

    let client: any HarnessCalling
    private let serviceController: HarnessServiceController?
    private let installationController: PingAgentInstallationController?
    /// Internal so progress-session behavior tests can stage a live session.
    var activeProgressSessionID: UUID?
    private var successToken: UUID?

    init(
        client: any HarnessCalling = HarnessIPCClient(),
        serviceController: HarnessServiceController? = nil,
        installationController: PingAgentInstallationController? = nil,
        readyMomentDefaults: UserDefaults = .standard
    ) {
        self.client = client
        self.serviceController = serviceController
        self.installationController = installationController
        self.readyMomentDefaults = readyMomentDefaults
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

    /// The resources overview shows current holds only; released history
    /// stays in the Technical Details ledger.
    var visibleResources: [ResourceLeaseRecord] {
        resources.filter { $0.status != "released" }
    }

    var runningParticipantCount: Int {
        participants.filter { $0.observedState == "ready" }.count
    }

    var collaborationHealth: CollaborationHealthRecord? {
        guard let deliverySummary else { return nil }
        return CollaborationHealthRecord(
            summary: deliverySummary,
            readyParticipants: runningParticipantCount,
            totalParticipants: participants.count
        )
    }

    /// Room-wide attention counts, or nil while the collection summary is
    /// unavailable. Never derived from `deliveries`: that is one bounded page.
    var deliveryAttentionTotals: DeliveryAttentionTotals? {
        deliverySummary.map(DeliveryAttentionTotals.init(summary:))
    }

    /// Concrete examples drawn from the loaded page. This is a sample, not the
    /// set — `deliveryAttentionTotals` owns how many there actually are.
    var deliveryAttention: [DeliveryAttentionRecord] {
        DeliveryAttentionRecord.items(from: deliveries)
    }

    /// One line of plain language for the Scenario header, in place of the
    /// `desired X · observed Y` protocol pair.
    var scenarioHeadline: String {
        guard let scenario = selectedScenario else { return S.Rooms.noSelection }
        let people = participants.count
        let running = runningParticipantCount
        let team: String
        switch (people, running) {
        case (0, _):
            team = S.Headline.noColleagues
        case (_, 0):
            team = S.Headline.noneRunning(people)
        case (running, _) where running == people:
            team = S.Headline.allRunning(people)
        default:
            team = S.Headline.someRunning(running, people)
        }
        return "\(Self.humanState(scenario.observedState)) · \(team)"
    }

    /// The single next step for the guidance rail. Fail-closed over the
    /// exact Scenario contract: only states the Host declares are handled,
    /// every offered action satisfies its Host precondition, and anything
    /// unknown or inconsistent renders guidance without a button.
    var guidance: GuidanceStep {
        guard selectedProject != nil else { return .registerProject }
        guard let scenario = selectedScenario else { return .createRoom }
        // Only TUI colleagues have a window to work in; a headless-only room
        // has not completed the "add AI colleagues" stage.
        let interactive = participants.filter(\.isInteractive)
        switch scenario.observedState {
        case "degraded", "provision_failed":
            return .attend(Self.humanState(scenario.observedState))
        case "provisioning", "opening", "closing", "repairing", "destroying":
            return .working(Self.humanState(scenario.observedState))
        case "closed":
            // workspace.plan requires a closed Scenario, so Prepare is only
            // ever offered here — and only once the receipt has actually been
            // read. Offering it while the diagnostic is still in flight would
            // propose recreating a workspace that already exists.
            switch workspaceEvidence {
            case .loading: return .working(S.Guide.checkingWorkspace)
            case .unavailable: return .inconsistent
            case .absent: return .prepareWorkspace
            case .present: break
            }
            if interactive.isEmpty { return .addColleague }
            return .resumeRoom
        case "running":
            switch workspaceEvidence {
            case .loading:
                // Not yet read is not a disagreement. Saying so was the whole
                // bug: every bootstrap of a healthy running room rendered
                // NEEDS YOU with no action until the user pressed Refresh.
                return .working(S.Guide.checkingWorkspace)
            case .absent, .unavailable:
                // A running room whose workspace evidence is missing or
                // unreadable is inconsistent; never offer an action the Host
                // must refuse.
                return .inconsistent
            case .present:
                break
            }
            if interactive.isEmpty { return .addColleague }
            switch policyReadiness {
            case .loading:
                return .working(S.Policy.loadingRules)
            case .missing, .replanRequired:
                return .configurePolicy
            case .unavailable:
                return .inconsistent
            case .current:
                break
            }
            // Readiness is unanimous, not "at least one". A room holding one
            // ready and one stopped colleague still has work for Start All,
            // and must not report itself ready or fire the all-green moment.
            if interactive.allSatisfy({ $0.observedState == "ready" }) {
                return .focusAndAssign
            }
            if interactive.contains(where: \.canStart) {
                return .startColleagues
            }
            // Some colleague is mid-transition and none is startable: the
            // Host would refuse Start All, so offer nothing.
            let transitional = interactive.first { $0.observedState != "ready" }
            return .working(
                Self.humanState(transitional?.observedState ?? "starting")
            )
        default:
            return .attend(Self.humanState(scenario.observedState))
        }
    }

    /// Blocked and transitional guidance preempts the flow: while it holds,
    /// no surface may offer a competing lifecycle action, because the Host
    /// would refuse it. Derived from `guidance` so there is exactly one
    /// source of flow truth.
    var lifecycleActionsPreempted: Bool {
        switch guidance {
        case .attend, .working, .inconsistent: true
        default: false
        }
    }

    /// Deck positioning and action gating, separated: `index` is where the
    /// card opens (by completed milestone for non-actionable states), and
    /// `actionable` is the exact live step whose real action the card may
    /// embed — attend/working/inconsistent never yield one.
    func guidePresentation() -> (index: Int, actionable: GuidanceStep?) {
        switch guidance {
        case .registerProject: return (0, .registerProject)
        case .createRoom: return (1, .createRoom)
        case .prepareWorkspace: return (2, .prepareWorkspace)
        case .addColleague: return (3, .addColleague)
        case .resumeRoom: return (4, .resumeRoom)
        case .configurePolicy: return (4, .configurePolicy)
        case .startColleagues: return (4, .startColleagues)
        case .focusAndAssign: return (5, .focusAndAssign)
        case .attend, .working, .inconsistent:
            return (completedMilestoneIndex, nil)
        }
    }

    /// Honest positioning while blocked or in transition: the furthest step
    /// whose prerequisite is actually complete — never a claim of step 6.
    private var completedMilestoneIndex: Int {
        guard selectedProject != nil else { return 0 }
        guard selectedScenario != nil else { return 1 }
        if workspaceEvidence != .present { return 2 }
        if participants.filter(\.isInteractive).isEmpty { return 3 }
        return 4
    }

    /// Show the one-time ready moment when the room first goes all-green.
    func updateReadyMoment() {
        guard let project = selectedProject, let scenario = selectedScenario,
              guidance == .focusAndAssign
        else { return }
        let key = "AICollabReadyMoment.\(project.id).\(scenario.id).g\(scenario.generation)"
        guard !readyMomentDefaults.bool(forKey: key) else { return }
        readyMomentDefaults.set(true, forKey: key)
        showReadyMoment = true
    }

    func dismissReadyMoment() {
        showReadyMoment = false
    }

    /// Machine state to plain language. Presentation only: the Host keeps
    /// emitting machine states, and the App does the translating.
    static func humanState(_ state: String) -> String {
        // The bilingual dictionary lives in `S.Status`; unknown states surface
        // as a localized "unknown" while the raw value stays in technical detail.
        S.Status.label(state)
    }

    static func humanDegradedReason(_ reason: String) -> String {
        S.Status.degradedReason(reason)
    }

    func bootstrap() async {
        await checkCommandInstallation()
        await performRead {
            if let serviceController = self.serviceController {
                let serviceStatus = try await serviceController.ensureRegistered()
                self.hostStatus = "starting:\(serviceStatus.label)"
                try await self.waitForHost()
            }
            let status = try await self.client.call(
                HarnessCall(operation: "host.status", target: ["scope": "host"])
            )
            let runtime = Bundle.main.bundleURL.appending(path: "Contents/Resources/HarnessService/runtime", directoryHint: .isDirectory)
            var runtimeDetails = stat()
            let identity = status["host_runtime_identity"] as? [String: NSNumber]
            let fresh = lstat(runtime.path, &runtimeDetails) == 0
                && identity?["dev"]?.int64Value == Int64(runtimeDetails.st_dev)
                && identity?["ino"]?.uint64Value == UInt64(runtimeDetails.st_ino)
            self.hostStatus = fresh ? (status["status"] as? String ?? "ready") : "stale-bundle"
            try await self.reloadProjects()
            await self.refreshProjectReconciliations()
            try await self.reloadTemplates()
            try await self.reloadPolicyTemplates()
            await self.refreshPresentationPermission()
            await self.refreshEnvironmentReport()
        }
    }

    func retryHostService() async {
        await bootstrap()
    }

    func checkCommandInstallation() async {
        guard let installationController else { return }
        do {
            let result = try await installationController.check()
            commandInstallationIssue = result.needsAttention ? result : nil
        } catch {
            recordCommandInstallationFailure(error)
        }
    }

    func repairCommandInstallation() async {
        await checkCommandInstallation()
        if let issue = commandInstallationIssue, issue.canReplace {
            pendingCommandReplacement = issue
        }
    }

    func confirmCommandReplacement(_ pending: PingAgentInstallationResult) async {
        guard pending.canReplace, let installationController else { return }
        guard !isBusy else { return refuse(.scenarioLifecycle, S.Msg.busy(self.activityText)) }
        pendingCommandReplacement = nil
        isBusy = true
        activityBuilder = { S.Installation.repairing }
        defer { activityBuilder = nil; isBusy = false }
        do {
            let result = try await installationController.reconcile(replacing: pending.conflicts)
            commandInstallationIssue = result.needsAttention ? result : nil
            if !result.needsAttention { noteSuccess(S.Installation.repaired) }
        } catch {
            recordCommandInstallationFailure(error)
        }
    }

    private func recordCommandInstallationFailure(_ error: Error) {
        commandInstallationIssue = PingAgentInstallationResult(
            status: "error", bundle: nil, entries: [], conflicts: [],
            reason: error.localizedDescription, backupDirectory: nil
        )
    }

    func chooseAndRegisterProject() async {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = S.Chrome.registerProject
        guard panel.runModal() == .OK, let url = panel.url else { return }
        pendingRegistrationURL = url
    }

    func confirmProjectRegistration(_ url: URL) async {
        await performMutation(
            activity: S.Msg.registering(url.lastPathComponent),
            scope: .project,
            success: S.Msg.registered(url.lastPathComponent)
        ) {
            try self.client.grantProjectDirectoryAccess(url)
            try await self.registerGrantedProject(url)
        }
    }

    @MainActor
    private func registerGrantedProject(_ url: URL) async throws {
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
        await self.reconcileProject(project.id)
        try await self.reloadScenarios()
        try await self.reloadPolicyTemplates()
    }

    func unregisterProject(_ project: ProjectRecord) async {
        await performMutation(
            activity: S.Msg.unregistering(project.key),
            scope: .project,
            success: S.Msg.unregistered(project.key)
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
        if let id { await reconcileProject(id) }
    }

    func reconcileProject(_ projectID: String, surfaceErrors: Bool = false) async {
        do {
            let result = try await client.call(
                HarnessCall(
                    operation: "project.reconcile",
                    target: ["scope": "project", "project_instance_id": projectID],
                    fence: ["operation_generation": 0],
                    payload: [:]
                )
            )
            guard
                let raw = result["project"] as? [String: Any],
                let refreshed = ProjectRecord(raw),
                let reconciliationRaw = result["reconciliation"] as? [String: Any],
                let reconciliation = ProjectReconciliationRecord(reconciliationRaw)
            else { throw HarnessIPCError.invalidReply }
            if let index = projects.firstIndex(where: { $0.id == projectID }) {
                projects[index] = refreshed
            }
            projectReconciliations[projectID] = reconciliation
        } catch {
            // Registration remains usable with its last-good render. A typed
            // reconciliation failure is surfaced only when the employee asks
            // to refresh this project explicitly.
            if surfaceErrors { report(error) }
        }
    }

    func refreshProjectReconciliations() async {
        for project in projects {
            await reconcileProject(project.id)
        }
    }

    func acceptProjectReconciliation(_ projectID: String) async {
        guard let reconciliation = projectReconciliations[projectID],
              reconciliation.bindingChanged else { return }
        await performMutation(
            activity: S.Msg.applyingUpdate,
            scope: .project,
            success: S.Msg.updateApplied
        ) {
            let result = try await self.client.call(
                HarnessCall(
                    operation: "project.accept-reconciliation",
                    target: ["scope": "project", "project_instance_id": projectID],
                    fence: ["operation_generation": 0],
                    payload: ["availability_fingerprint": reconciliation.fingerprint]
                )
            )
            if let raw = result["project"] as? [String: Any],
               let refreshed = ProjectRecord(raw) {
                if let index = self.projects.firstIndex(where: { $0.id == projectID }) {
                    self.projects[index] = refreshed
                }
            }
            if let raw = result["reconciliation"] as? [String: Any],
               let accepted = ProjectReconciliationRecord(raw) {
                self.projectReconciliations[projectID] = accepted
            }
        }
    }

    func selectScenario(_ id: String?) async {
        workspaceEvidence = .loading
        showReadyMoment = false
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
            return refuse(.scenarioLifecycle, S.Msg.selectRoomFirst)
        }
        await performMutation(
            activity: S.Msg.focusingWindows,
            scope: .scenarioLifecycle,
            success: S.Msg.windowsRestored
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
        case "installation.commands.repair":
            await repairCommandInstallation()
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
        case "scenario.open":
            await openScenario()
        case "participant.recover":
            var attempted = Set<String>()
            guard participants.contains(where: \.canRecover) else {
                return refuse(.participantAction, S.Msg.noColleaguesNeedRecovery)
            }
            while let participant = participants.first(
                where: { $0.canRecover && !attempted.contains($0.id) }
            ) {
                attempted.insert(participant.id)
                await recoverParticipant(participant)
                if actionableError != nil { break }
            }
        case "system-settings.automation":
            if let url = URL(
                string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"
            ) {
                NSWorkspace.shared.open(url)
            }
        case "presentation.permission-request":
            await requestPresentationPermission()
        case "iterm-presentation.launch-target":
            openIterm2()
        case "iterm-presentation.enable-python-api":
            copyItermPythonAPISetupCommand()
            noteSuccess(S.Msg.itermPythonAPICommandCopied)
        case "iterm-presentation.restart-after-python-api",
             "iterm-presentation.reset-private-api-socket":
            openIterm2()
            noteSuccess(S.Msg.restartIterm2Required)
        default:
            break
        }
    }

    private func openIterm2() {
        if let url = NSWorkspace.shared.urlForApplication(
            withBundleIdentifier: "com.googlecode.iterm2"
        ) {
            NSWorkspace.shared.openApplication(
                at: url,
                configuration: NSWorkspace.OpenConfiguration(),
                completionHandler: nil
            )
        }
    }

    private func copyItermPythonAPISetupCommand() {
        let command = """
        /usr/bin/nohup /bin/zsh <<'AICOLLAB_ENABLE_ITERM_API' >/dev/null 2>&1 &
        /usr/bin/osascript -e 'tell application id "com.googlecode.iterm2" to quit'
        /bin/sleep 2
        /usr/bin/defaults write com.googlecode.iterm2 EnableAPIServer -bool true
        /usr/bin/defaults write com.googlecode.iterm2 NoSyncEnableAPIServer -bool true
        /usr/bin/open -b com.googlecode.iterm2
        AICOLLAB_ENABLE_ITERM_API
        """
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(command, forType: .string)
    }

    func repairActionDetail(_ action: String) -> String? {
        S.Repair.detail(action)
    }

    func shouldShowRepairDetail(_ action: String) -> Bool {
        S.Repair.detail(action) != nil
    }

    func textOnlyRepairAction(_ action: String) -> String? {
        switch action {
        case "scenario.force-destroy":
            S.Repair.detail(action)
        default:
            nil
        }
    }

    func repairActionLabel(_ action: String) -> String {
        S.Repair.label(action)
    }

    /// Explicit user gesture: let macOS show its Automation consent prompt
    /// through the Host service, then refresh the observed permission state.
    func requestPresentationPermission() async {
        await performRead {
            let result = try await self.client.call(
                HarnessCall(
                    operation: "presentation.permission-request",
                    target: ["scope": "host"],
                    // The Host summons the real consent dialog and waits for
                    // the user's decision; give them time to read it.
                    responseTimeoutSeconds: 180
                )
            )
            self.presentationPermissionStatus = Self.permissionStatus(result)
        }
        if selectedScenario != nil {
            await runPreflight()
        }
    }

    func refreshPresentationPermission() async {
        let result = try? await client.call(
            HarnessCall(
                operation: "presentation.permission-probe",
                target: ["scope": "host"]
            )
        )
        presentationPermissionStatus = result.flatMap(Self.permissionStatus)
    }

    /// Machine-readiness report for the Diagnostics page. Read-only and
    /// best-effort: any failure — connection, operation, or a reply that does
    /// not decode cleanly — renders as an empty report, fail closed, without
    /// breaking bootstrap. (A Host that predates the operation never gets
    /// here: the handshake's registry-digest pin fails every call first.)
    func refreshEnvironmentReport() async {
        let result = try? await client.call(
            HarnessCall(operation: "environment.probe", target: ["scope": "host"])
        )
        let raw = dictionaries(result?["environment_observations"])
        let parsed = raw.compactMap(EnvironmentObservationRecord.init)
        environmentObservations = parsed.count == raw.count ? parsed : []
    }

    static var appVersionText: String {
        let short =
            Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString")
            as? String ?? "—"
        let build =
            Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion")
            as? String ?? "—"
        return "\(short) (\(build))"
    }

    static var contractVersionText: String {
        "v\(HarnessContract.version) · \(HarnessContract.operationRegistryDigest.prefix(12))"
    }

    private static func permissionStatus(_ result: [String: Any]) -> String? {
        let observations = result["permission_observations"] as? [[String: Any]]
        return observations?.first?["status"] as? String
    }

    /// The performable repair button for one error, honoring the approved
    /// retryable gate: a retry-semantic action is only clickable when the
    /// Host marked the failure retryable; otherwise the recommendation stays
    /// text-only.
    func performableRepairAction(_ error: ActionableErrorRecord) -> String? {
        guard let action = error.repairAction, canPerformRepairAction(action) else {
            return nil
        }
        if action == "host.retry" && !error.retryable { return nil }
        return action
    }

    func canPerformRepairAction(_ action: String) -> Bool {
        switch action {
        case "installation.commands.repair", "host.retry", "project.register", "scenario.refresh", "scenario.preflight",
             "scenario.open",
             "workspace.prepare", "system-settings.automation",
             "presentation.permission-request", "iterm-presentation.launch-target",
             "iterm-presentation.enable-python-api",
             "iterm-presentation.restart-after-python-api",
             "iterm-presentation.reset-private-api-socket":
            true
        case "participant.recover":
            participants.filter(\.canRecover).count >= 1
        default:
            false
        }
    }

    func createScenario() async {
        guard let project = selectedProject else {
            return refuse(.scenarioCreate, S.Msg.registerOrSelectProject)
        }
        let scenarioID = newScenarioID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !scenarioID.isEmpty else {
            return refuse(.scenarioCreate, S.Msg.nameTheRoom)
        }
        guard !scenarios.contains(where: { $0.id == scenarioID }) else {
            return refuse(.scenarioCreate, S.Msg.roomNameTaken(scenarioID))
        }
        let objective = newScenarioObjective.trimmingCharacters(
            in: .whitespacesAndNewlines
        )
        await performMutation(
            activity: S.Msg.creatingRoom(scenarioID),
            scope: .scenarioCreate,
            success: S.Msg.createdRoom(scenarioID)
        ) {
            let result = try await self.client.call(
                HarnessCall(
                    operation: "scenario.create",
                    target: self.scenarioTarget(projectID: project.id, scenarioID: scenarioID),
                    fence: ["operation_generation": 0],
                    payload: [
                        "project_binding_digest": project.bindingDigest,
                        "objective": objective,
                        "acceptance_criteria": "",
                    ]
                )
            )
            guard result["scenario"] as? [String: Any] != nil else {
                throw HarnessIPCError.invalidReply
            }
            try await self.reloadScenarios()
            self.selectedScenarioID = scenarioID
            self.newScenarioID = "research-\(Self.shortTimestamp())"
            self.newScenarioObjective = ""
            try await self.refreshSelectedScenarioValues()
        }
    }

    func appendScenarioObjective() async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.objective, S.Msg.selectRoomFirst)
        }
        let objective = objectiveDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !objective.isEmpty else {
            return refuse(.objective, S.Msg.objectiveRequired)
        }
        let acceptanceCriteria = acceptanceCriteriaDraft.trimmingCharacters(
            in: .whitespacesAndNewlines
        )
        await performMutation(
            activity: S.Msg.updatingObjective,
            scope: .objective,
            success: S.Msg.objectiveUpdated
        ) {
            let result = try await self.client.call(
                HarnessCall(
                    operation: "scenario.objective.append",
                    target: self.scenarioTarget(
                        projectID: project.id, scenarioID: scenario.id
                    ),
                    fence: ["operation_generation": scenario.stateRevision],
                    payload: [
                        "scenario_generation": scenario.generation,
                        "scenario_state_revision": scenario.stateRevision,
                        "objective": objective,
                        "acceptance_criteria": acceptanceCriteria,
                    ]
                )
            )
            guard
                let raw = result["scenario"] as? [String: Any],
                let current = ScenarioRecord(raw)
            else { throw HarnessIPCError.invalidReply }
            if let index = self.scenarios.firstIndex(where: { $0.id == current.id }) {
                self.scenarios[index] = current
            }
            self.syncObjectiveDraft(from: current)
        }
    }

    func prepareWorkspace() async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.workspace, S.Msg.selectRoomFirst)
        }
        let progressSessionID = UUID()
        await performMutation(
            activity: S.Msg.planningWorkspace,
            scope: .workspace,
            success: S.Msg.workspaceReady
        ) {
            self.activeProgressSessionID = progressSessionID
            defer {
                if self.activeProgressSessionID == progressSessionID {
                    self.activeProgressSessionID = nil
                    self.activeOperationID = nil
                    self.operationCanCancel = false
                    self.progressBuilder = nil
                }
            }
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
            self.activityBuilder = { S.Msg.cloningRepos }
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
                ),
                progress: { progress in
                    Task { @MainActor in
                        self.applyProgress(progress, progressSessionID: progressSessionID)
                    }
                }
            )
            self.receiptOverride = prettyJSON(
                (provisioned["workspace"] as? [String: Any])?["receipt"]
            )
            try await self.refreshSelectedScenarioValues()
            await self.reconcileProject(project.id)
        }
    }

    func openScenario() async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.scenarioLifecycle, S.Msg.selectRoomFirst)
        }
        // The Host allows this up to 360s while it restores each participant's
        // previous conversation, so it must never run without something on screen.
        await performMutation(
            activity: S.Msg.resuming(scenario.id),
            scope: .scenarioLifecycle,
            success: S.Msg.resumed(scenario.id)
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
            self.resumeOverride = prettyJSON(result["resume_summary"])
            try await self.reloadScenarios()
            try await self.refreshSelectedScenarioValues()
        }
    }

    func closeScenario() async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.scenarioLifecycle, S.Msg.selectRoomFirst)
        }
        let progressSessionID = UUID()
        await performMutation(
            activity: S.Msg.closing(scenario.id),
            scope: .scenarioLifecycle,
            success: S.Msg.closed(scenario.id)
        ) {
            self.activeProgressSessionID = progressSessionID
            defer {
                if self.activeProgressSessionID == progressSessionID {
                    self.activeProgressSessionID = nil
                    self.activeOperationID = nil
                    self.operationCanCancel = false
                    self.progressBuilder = nil
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

    func startAllParticipants() async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.participantAction, S.Msg.selectRoomFirst)
        }
        let startable = participants.filter(\.canStart)
        guard !startable.isEmpty else {
            return refuse(
                .participantAction,
                S.Msg.nothingStartable
            )
        }
        let progressSessionID = UUID()
        await performMutation(
            activity: S.Msg.startingCount(startable.count),
            scope: .participantAction
        ) {
            self.activeProgressSessionID = progressSessionID
            defer {
                if self.activeProgressSessionID == progressSessionID {
                    self.activeProgressSessionID = nil
                    self.activeOperationID = nil
                    self.operationCanCancel = false
                    self.progressBuilder = nil
                }
            }
            let result: [String: Any]
            do {
                result = try await self.client.call(
                    HarnessCall(
                        operation: "scenario.start-participants",
                        target: self.scenarioTarget(
                            projectID: project.id, scenarioID: scenario.id
                        ),
                        fence: ["operation_generation": scenario.stateRevision],
                        payload: self.scenarioFencePayload(scenario),
                        responseTimeoutSeconds: max(360, startable.count * 60)
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
            let counts =
                (result["start_summary"] as? [String: Any])?["counts"]
                as? [String: Any] ?? [:]
            func count(_ key: String) -> Int { counts[key] as? Int ?? 0 }
            let started = count("started")
            let alreadyRunning = count("already_running")
            let failed = count("failed")
            let skipped = count("skipped")
            if failed > 0 {
                self.refuse(
                    .participantAction,
                    S.Msg.startSummary(started, failed, skipped)
                )
            } else if started == 0 && skipped == 0 {
                self.noteSuccess(S.Msg.everyoneAlreadyWorking)
            } else {
                self.noteSuccess(
                    S.Msg.startAllSummary(started, alreadyRunning, skipped)
                )
            }
        }
    }

    func cancelActiveOperation() async {
        guard let operationID = activeOperationID, operationCanCancel else { return }
        do {
            _ = try await client.cancelOperation(operationID)
            operationCanCancel = false
            progressBuilder = { S.Msg.cancellationAccepted }
        } catch {
            let actionable = ActionableErrorRecord(error)
            actionableError = actionable
            errorMessage = actionable.message
        }
    }

    func repairScenario() async {
        await mutateScenario(
            operation: "scenario.repair",
            activity: S.Msg.repairingRoom,
            success: S.Msg.repairFinished,
            extraPayload: [:]
        )
    }

    func addParticipant() async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.participantAdd, S.Msg.selectRoomFirst)
        }
        guard let template = selectedTemplate else {
            return refuse(.participantAdd, S.Msg.chooseTemplate)
        }
        let participantID = newParticipantID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !participantID.isEmpty else {
            return refuse(.participantAdd, S.Msg.nameTheColleague)
        }
        guard !participants.contains(where: { $0.id == participantID }) else {
            return refuse(
                .participantAdd,
                S.Msg.colleagueNameTaken(participantID)
            )
        }
        await performMutation(
            activity: S.Msg.adding(participantID),
            scope: .participantAdd,
            success: S.Msg.added(participantID)
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
                S.Msg.onlyStoppedCanStart(participant.id, Self.humanState(participant.observedState))
            )
        }
        await mutateParticipant(
            operation: "participant.start",
            participant: participant,
            activity: S.Msg.starting(participant.id),
            success: S.Msg.isRunning(participant.id),
            responseTimeoutSeconds: 480
        )
    }

    func stopParticipant(_ participant: ParticipantRecord) async {
        guard participant.canStop else {
            return refuse(
                .participantAction,
                S.Msg.nothingToStop(participant.id, Self.humanState(participant.observedState))
            )
        }
        await mutateParticipant(
            operation: "participant.stop",
            participant: participant,
            activity: S.Msg.stopping(participant.id),
            success: S.Msg.isStopped(participant.id)
        )
    }

    /// Delete a stopped AI colleague (R1). The Host is the authority — it
    /// re-proves stopped state, absent bindings, and released resources; the
    /// App only offers the action where it can succeed and passes the
    /// employee's explicit confirmation.
    func deleteParticipant(_ participant: ParticipantRecord) async {
        guard participant.observedState == "stopped" else {
            return refuse(
                .participantAction,
                S.Colleagues.deleteRequiresStopped(
                    participant.id,
                    Self.humanState(participant.observedState)
                )
            )
        }
        await mutateParticipant(
            operation: "participant.destroy",
            participant: participant,
            activity: S.Colleagues.deleteActivity(participant.id),
            success: S.Colleagues.deleteSuccess(participant.id),
            extraPayload: ["confirmed": true]
        )
    }

    func recoverParticipant(_ participant: ParticipantRecord) async {
        guard participant.canRecover else {
            return refuse(.participantAction, S.Msg.nothingToRecover(participant.id))
        }
        await mutateParticipant(
            operation: "participant.recover",
            participant: participant,
            activity: S.Msg.recovering(participant.id),
            success: S.Msg.recovered(participant.id)
        )
    }

    func forceStopParticipant(_ participant: ParticipantRecord) async {
        guard participant.canForceStop else {
            return refuse(
                .participantAction,
                S.Msg.noProcessToForceStop(participant.id)
            )
        }
        await mutateParticipant(
            operation: "participant.force-stop",
            participant: participant,
            activity: S.Msg.forceStopping(participant.id),
            success: S.Msg.forceStopped(participant.id),
            responseTimeoutSeconds: 360
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
            activity: S.Msg.replacing(participant.id, template.displayName),
            success: S.Msg.replaced(participant.id, template.displayName),
            extraPayload: [
                "launch_spec": template.launchSpec,
                "presentation_driver_id": template.presentationDriverID ?? NSNull(),
            ],
            responseTimeoutSeconds: 480
        )
    }

    func recreateParticipantWithHandoff(_ participant: ParticipantRecord) async {
        guard participant.canRecreateWithHandoff else {
            return refuse(
                .participantAction,
                S.Msg.notAwaitingRecreate(participant.id)
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
                S.Msg.noTemplateForRuntime(participant.id, participant.runtimeProfileRef ?? S.Msg.runtimeDriverDefault)
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
            activity: S.Msg.recreating(participant.id),
            success: S.Msg.newConversation(participant.id),
            extraPayload: [
                "launch_spec": launchSpec,
                "presentation_driver_id": template.presentationDriverID ?? NSNull(),
            ],
            responseTimeoutSeconds: 480
        )
    }

    func breakResource(_ resource: ResourceLeaseRecord) async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.resource, S.Msg.selectRoomFirst)
        }
        guard resource.canBreak else {
            return refuse(.resource, S.Msg.leaseNotStale)
        }
        await performMutation(
            activity: S.Msg.releasingLease(resource.resourceClass),
            scope: .resource,
            success: S.Msg.leaseReleased(resource.resourceClass)
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
                    ],
                    responseTimeoutSeconds: 360
                )
            )
            try await self.reloadScenarios()
            try await self.refreshSelectedScenarioValues()
        }
    }

    func loadDestroyPreview() async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.scenarioLifecycle, S.Msg.selectRoomFirst)
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
            self.destroyPreviewBlockers = preview["blockers"] as? [String] ?? []
        }
    }

    @discardableResult
    func destroyScenario() async -> Bool {
        guard let project = selectedProject, let scenario = selectedScenario else {
            refuse(.scenarioLifecycle, S.Msg.selectRoomFirst)
            return false
        }
        guard destroyPreviewEligible else {
            refuse(
                .scenarioLifecycle,
                self.destroyPreviewLoaded
                    ? S.Msg.destroyPreviewBlocked(self.destroyPreviewBlockers)
                    : S.Msg.loadPreviewFirst
            )
            return false
        }
        return await performMutation(
            activity: S.Msg.deletingRoom(scenario.id),
            scope: .scenarioLifecycle,
            success: S.Msg.deletedRoom(scenario.id)
        ) {
            _ = try await self.client.call(
                HarnessCall(
                    operation: "scenario.destroy",
                    target: self.scenarioTarget(projectID: project.id, scenarioID: scenario.id),
                    fence: ["operation_generation": scenario.stateRevision],
                    payload: self.scenarioFencePayload(scenario),
                    responseTimeoutSeconds: 360
                )
            )
            self.clearDestroyPreview()
            self.selectedScenarioID = nil
            self.participants = []
            self.resources = []
            self.clearCollaborationValues()
            try await self.reloadScenarios()
        }
    }

    @discardableResult
    func forceDestroyScenario(_ scenario: ScenarioRecord) async -> Bool {
        guard let project = selectedProject else {
            refuse(.scenarioLifecycle, S.Msg.selectProjectFirst)
            return false
        }
        return await performMutation(
            activity: S.Msg.forceDeletingRoom(scenario.id),
            scope: .scenarioLifecycle,
            success: S.Msg.forceDeletedRoom(scenario.id)
        ) {
            _ = try await self.client.call(
                HarnessCall(
                    operation: "scenario.force-destroy",
                    target: self.scenarioTarget(
                        projectID: project.id, scenarioID: scenario.id
                    ),
                    fence: ["operation_generation": scenario.stateRevision],
                    payload: self.scenarioFencePayload(scenario),
                    responseTimeoutSeconds: 360
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
        policyNote = { S.PolicyNote.previewBeforeApply }
    }

    func planSelectedPolicy() async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.policy, S.Msg.selectRoomFirst)
        }
        guard let template = selectedPolicyTemplate else {
            return refuse(.policy, S.Msg.chooseTeamTemplateToPreview)
        }
        await performMutation(
            activity: S.Msg.previewingPlan(template.displayName),
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
            let canApply = plan.canApply
            self.policyNote = {
                canApply ? S.PolicyNote.planReady : S.PolicyNote.planBlocked
            }
        }
    }

    /// One employee decision, while preserving the Host's two-step
    /// plan/apply fence. Clearing the preview first ensures a failed plan can
    /// never fall through and apply stale UI state.
    func applyRecommendedPolicy() async {
        policyPlan = nil
        await planSelectedPolicy()
        guard policyPlan?.canApply == true else { return }
        await applySelectedPolicyPlan()
    }

    func applySelectedPolicyPlan() async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.policy, S.Msg.selectRoomFirst)
        }
        guard let template = selectedPolicyTemplate else {
            return refuse(.policy, S.Msg.chooseTeamTemplateFirst)
        }
        guard let plan = policyPlan else {
            return refuse(.policy, S.Msg.previewBeforeApply)
        }
        guard plan.templateID == template.id else {
            return refuse(
                .policy,
                S.Msg.planForDifferentTemplate(template.displayName)
            )
        }
        guard plan.canApply else {
            return refuse(.policy, S.Msg.planBlocked)
        }
        // This is the branch that used to fail silently while the button stayed
        // enabled: the Scenario moved on after the preview was taken.
        guard
            plan.scenarioGeneration == scenario.generation,
            plan.scenarioStateRevision == scenario.stateRevision
        else {
            return refuse(
                .policy,
                S.Msg.planStale
            )
        }
        await performMutation(
            activity: S.Msg.applyingPlan(template.displayName),
            scope: .policy,
            success: S.Msg.policyApplied(template.displayName)
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
            self.policyNote = { S.PolicyNote.applied }
        }
    }

    /// The room's live loop: deliveries every 2 seconds as before, plus a
    /// read-only look at the room record and its participants on the same
    /// tick. Colleague state used to refresh only on select / manual refresh
    /// / after a mutation, so a TUI that died stayed "ready" on screen until
    /// the user thought to click Refresh. Reads only — never a mutation, and
    /// skipped entirely while one is in flight so it cannot race the
    /// post-mutation refresh.
    func monitorRoom(for scenarioID: String) async {
        while !Task.isCancelled {
            guard
                selectedScenarioID == scenarioID,
                let project = selectedProject,
                let scenario = selectedScenario
            else { return }
            // Captured before the await; compared again right before each
            // write. Any mutation boundary in between makes this read stale.
            let readEpoch = mutationEpoch
            do {
                let page = try await fetchDeliveries(
                    project: project,
                    scenario: scenario
                )
                guard selectedScenarioID == scenarioID else { return }
                if liveLoopMayWrite(scenarioID, readEpoch: readEpoch) {
                    deliveries = page.deliveries
                    deliverySummary = page.summary
                    let shown = page.deliveries.count
                    let total = page.summary.total
                    deliveryNote = {
                        total == 0 ? S.DeliveryNote.none : S.DeliveryNote.showing(shown, total)
                    }
                }
            } catch is CancellationError {
                return
            } catch {
                guard selectedScenarioID == scenarioID else { return }
                // The failure note is a live-loop write like any other.
                if liveLoopMayWrite(scenarioID, readEpoch: readEpoch) {
                    presentDeliveryFailure(error, live: true)
                }
            }
            if !isBusy {
                do {
                    try await observeRoom(
                        project: project, scenarioID: scenarioID, readEpoch: mutationEpoch
                    )
                } catch is CancellationError {
                    return
                } catch {
                    // Keep the last known participants/room record; the
                    // delivery read above already reports live failures.
                }
            }
            do {
                try await Task.sleep(nanoseconds: 2_000_000_000)
            } catch {
                return
            }
        }
    }

    /// The live loop may write only while the room it was started for is
    /// still selected, no mutation is in flight, and no mutation boundary
    /// has passed since the read was issued (`readEpoch`). Evaluated
    /// synchronously on the main actor immediately before each write, with
    /// no `await` in between, so a mutation cannot begin between the check
    /// and the write; a mutation that began — or began and finished — while
    /// the read was in flight wins, its own post-mutation reload being the
    /// fresher data. Monitor path only — the explicit refresh path is never
    /// gated by this.
    private func liveLoopMayWrite(_ scenarioID: String, readEpoch: Int) -> Bool {
        selectedScenarioID == scenarioID && !isBusy && mutationEpoch == readEpoch
    }

    /// One read of `scenario.status` + `participant.list`, written back only
    /// when something actually changed so the 2-second tick does not re-render
    /// an unchanged roster or disturb objective editing (`syncObjectiveDraft`
    /// is deliberately not called here — that stays with the explicit
    /// refresh path). Every write re-checks `liveLoopMayWrite` after the
    /// await that preceded it (codex review 20260904-030617-82zm9p P1).
    private func observeRoom(
        project: ProjectRecord, scenarioID: String, readEpoch: Int
    ) async throws {
        let target = scenarioTarget(projectID: project.id, scenarioID: scenarioID)
        let status = try await client.call(
            HarnessCall(operation: "scenario.status", target: target)
        )
        guard liveLoopMayWrite(scenarioID, readEpoch: readEpoch) else { return }
        if let raw = status["scenario"] as? [String: Any],
           let current = ScenarioRecord(raw),
           let index = scenarios.firstIndex(where: { $0.id == current.id }),
           scenarios[index] != current
        {
            scenarios[index] = current
        }
        guard let scenario = selectedScenario else { return }
        let parsed = try await fetchParticipants(project: project, scenario: scenario)
        guard liveLoopMayWrite(scenarioID, readEpoch: readEpoch) else { return }
        if participants != parsed {
            participants = parsed
        }
    }

    func retryDelivery(_ delivery: DeliveryRecord) async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.delivery, S.Msg.selectRoomFirst)
        }
        guard delivery.retryEligible else {
            return refuse(.delivery, S.Msg.deliveryNotRetryable)
        }
        await performMutation(
            activity: S.Msg.retryingDelivery(String(delivery.id.prefix(12))),
            scope: .delivery,
            success: S.Msg.deliveryRetryAccepted
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
            return refuse(.scenarioLifecycle, S.Msg.selectRoomFirst)
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
                    payload: payload,
                    responseTimeoutSeconds: 360
                )
            )
            try await self.reloadScenarios()
            try await self.refreshSelectedScenarioValues()
        }
    }

    private func mutateParticipant(
        operation: String,
        participant: ParticipantRecord,
        activity: @autoclosure @escaping () -> String,
        success: @autoclosure @escaping () -> String,
        extraPayload: [String: Any] = [:],
        responseTimeoutSeconds: Int = 360
    ) async {
        guard let project = selectedProject, let scenario = selectedScenario else {
            return refuse(.participantAction, S.Msg.selectRoomFirst)
        }
        await performMutation(
            activity: activity(),
            scope: .participantAction,
            success: success()
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
                    payload: payload,
                    responseTimeoutSeconds: responseTimeoutSeconds
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
            workspaceEvidence = .loading
            clearCollaborationValues()
            return
        }
        // A read that throws before the workspace diagnostic observed nothing.
        // Resolve to unavailable rather than leaving a first load stuck on
        // "checking" forever; an already-observed receipt is left alone so a
        // routine refresh does not flicker back through the transitional state.
        var observedWorkspace = false
        defer {
            if !observedWorkspace, workspaceEvidence == .loading {
                workspaceEvidence = .unavailable
            }
        }
        let target = scenarioTarget(projectID: project.id, scenarioID: scenarioID)
        let status = try await client.call(
            HarnessCall(operation: "scenario.status", target: target)
        )
        if let raw = status["scenario"] as? [String: Any], let current = ScenarioRecord(raw) {
            if let index = scenarios.firstIndex(where: { $0.id == current.id }) {
                scenarios[index] = current
            }
            syncObjectiveDraft(from: current)
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
        diagnosticOverride = prettyJSON(diagnosticResult)
        if
            let diagnostic = diagnosticResult["diagnostic"] as? [String: Any],
            let workspace = diagnostic["workspace"] as? [String: Any],
            let receipt = workspace["receipt"] as? [String: Any]
        {
            receiptOverride = prettyJSON(receipt)
            workspaceEvidence = .present
        } else {
            workspaceEvidence = .absent
        }
        observedWorkspace = true
        let resourceResult = try await client.call(
            HarnessCall(operation: "resource.list", target: target)
        )
        let rawResources = dictionaries(resourceResult["resources"])
        let parsedResources = rawResources.compactMap(ResourceLeaseRecord.init)
        guard parsedResources.count == rawResources.count else {
            throw HarnessIPCError.invalidReply
        }
        resources = parsedResources
        resourceOverride = prettyJSON(resourceResult)
        guard let current = selectedScenario else { return }
        try await reloadPolicyTemplates()
        await reloadCollaboration(project: project, scenario: current)
        updateReadyMoment()
    }

    /// Leaves objective editing with the drafts mirroring what is actually
    /// committed, so a later Edit opens on the current text instead of turning
    /// into an accidental replace-from-empty that drops acceptance criteria.
    func resetObjectiveDrafts() {
        guard let scenario = selectedScenario else {
            objectiveDraft = ""
            acceptanceCriteriaDraft = ""
            return
        }
        syncObjectiveDraft(from: scenario)
    }

    private func syncObjectiveDraft(from scenario: ScenarioRecord) {
        objectiveDraft = scenario.objective
        acceptanceCriteriaDraft = scenario.acceptanceCriteria
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
        policyReadiness = .loading
        do {
            try await reloadPolicy(project: project, scenario: scenario)
        } catch {
            policyStatus = nil
            policyTextOverride = error.localizedDescription
            let readiness = Self.policyReadiness(afterPolicyReadError: error)
            policyReadiness = readiness
            policyNote = {
                readiness == .missing ? S.PolicyNote.noActive : S.PolicyNote.unavailable
            }
        }
        do {
            try await reloadDeliveries(project: project, scenario: scenario)
            let shown = deliveries.count
            let total = deliverySummary?.total ?? 0
            deliveryNote = {
                total == 0 ? S.DeliveryNote.none : S.DeliveryNote.showing(shown, total)
            }
        } catch {
            deliveries = []
            deliverySummary = nil
            presentDeliveryFailure(error, live: false)
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
        policyReadiness = status.requiresReplan ? .replanRequired : .current
        policyTextOverride = prettyJSON(result)
        let requiresReplan = status.requiresReplan
        policyNote = {
            requiresReplan ? S.PolicyNote.replanRequired : S.PolicyNote.activeMatches
        }
    }

    private func reloadDeliveries(project: ProjectRecord, scenario: ScenarioRecord) async throws {
        let page = try await fetchDeliveries(project: project, scenario: scenario)
        deliveries = page.deliveries
        deliverySummary = page.summary
    }

    private func fetchDeliveries(
        project: ProjectRecord,
        scenario: ScenarioRecord
    ) async throws -> DeliveryCollectionRecord {
        let result = try await client.call(
            HarnessCall(
                operation: "delivery.list",
                target: scenarioTarget(projectID: project.id, scenarioID: scenario.id),
                payload: ["limit": DeliveryCollectionRecord.rawActivityLimit]
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
        policyReadiness = .loading
        policyPlan = nil
        policyTemplates = []
        selectedPolicyTemplateID = nil
        deliveries = []
        deliverySummary = nil
        policyNote = nil
        deliveryNote = nil
    }

    // This classification assumes policy.show is the sole source of the Host's
    // overloaded target.delivery-not-found code; revisit it if that operation grows.
    static func policyReadiness(afterPolicyReadError error: Error) -> PolicyReadiness {
        guard case let HarnessIPCError.hostRejected(
            code, _, _, _, _, _
        ) = error else { return .unavailable }
        return code == "target.delivery-not-found" ? .missing : .unavailable
    }

    private func clearDestroyPreview() {
        destroyPreviewText = ""
        destroyPreviewEligible = false
        destroyPreviewBlockers = []
    }

    /// Explicit-refresh path: reads and assigns unconditionally (apart from
    /// the equality gate). The live loop uses `fetchParticipants` directly so
    /// it can apply its own write guard after the await.
    private func reloadParticipants(project: ProjectRecord, scenario: ScenarioRecord) async throws {
        let parsed = try await fetchParticipants(project: project, scenario: scenario)
        // Equality-gated: an unchanged roster must not re-render.
        if participants != parsed {
            participants = parsed
        }
    }

    /// Pure read of `participant.list` — no writes to published state.
    private func fetchParticipants(
        project: ProjectRecord, scenario: ScenarioRecord
    ) async throws -> [ParticipantRecord] {
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
        return parsed
    }

    func applyProgress(
        _ progress: HarnessProgress,
        progressSessionID: UUID
    ) {
        guard activeProgressSessionID == progressSessionID else { return }
        activeOperationID = progress.operationID
        operationCanCancel = progress.cancellable
        if progress.progressKind == "workspace-component-v1" {
            applyWorkspaceComponentProgress(progress)
        }
        let unitText = progress.totalUnits > 0
            ? "\(min(progress.completedUnits, progress.totalUnits))/\(progress.totalUnits)"
            : "0/0"
        let capturedState = progress.state
        let capturedParticipant = progress.participantID
        progressBuilder = {
            S.Msg.progressLine(
                S.Status.label(capturedState), unitText, capturedParticipant
            )
        }
    }

    /// One row per repository (and one for the environment), in plan order.
    func applyWorkspaceComponentProgress(_ progress: HarnessProgress) {
        if progress.phase == "workspace.prepare",
           progress.componentState == "complete" {
            for index in workspaceProgress.indices {
                workspaceProgress[index].state = "ready"
            }
            return
        }
        guard
            let componentID = progress.componentID,
            let kind = progress.componentKind,
            let index = progress.componentIndex,
            let state = progress.componentState,
            index >= 0, index < 4096
        else { return }
        while workspaceProgress.count <= index {
            workspaceProgress.append(
                WorkspaceComponentProgress(
                    index: workspaceProgress.count,
                    componentID: "",
                    kind: "repository",
                    state: "waiting"
                )
            )
        }
        workspaceProgress[index].componentID = componentID
        workspaceProgress[index].kind = kind
        workspaceProgress[index].state = state
    }

    /// Read-only refreshes. Deliberately does not take the mutation lock and does
    /// not set `isBusy`, so browsing stays responsive while a mutation runs.
    private func performRead(
        _ work: @escaping @MainActor () async throws -> Void
    ) async {
        do {
            try await work()
            if hostStatus != "stale-bundle" { hostStatus = "ready" }
        } catch {
            report(error)
        }
    }

    /// Mutations. `activity` is required rather than optional so that every
    /// mutation is structurally guaranteed to put something on screen while it
    /// runs — the long ones included. A second mutation is refused with a visible
    /// reason instead of being swallowed.
    @discardableResult
    private func performMutation(
        activity: @autoclosure @escaping () -> String,
        scope: ValidationScope,
        success: @autoclosure @escaping () -> String? = nil,
        _ work: @escaping @MainActor () async throws -> Void
    ) async -> Bool {
        guard !isBusy else {
            refuse(scope, S.Msg.busy(self.activityText))
            return false
        }
        isBusy = true
        activityBuilder = activity
        // Preparation rows belong to one operation only; a new mutation of
        // any kind must never display a previous Prepare's repositories.
        workspaceProgress = []
        errorMessage = nil
        actionableError = nil
        validation = nil
        defer {
            activityBuilder = nil
            isBusy = false
        }
        do {
            try await work()
            if hostStatus != "stale-bundle" { hostStatus = "ready" }
            if success() != nil { noteSuccess(success() ?? "") }
            return true
        } catch {
            reportMutationFailure(error, scope: scope)
            return false
        }
    }

    /// A refused mutation has to say so where the control is.
    ///
    /// `report` alone paints `errorBanner`, which is a top overlay
    /// (`ContentView:198`). When the control is a participant row that overlay
    /// is off-screen, so a Host refusal read as the button doing nothing — the
    /// m2 `/exit` report. The top banner still carries the repair action; this
    /// adds the same reason under the control that was clicked.
    func reportMutationFailure(_ error: Error, scope: ValidationScope) {
        report(error)
        let actionable = ActionableErrorRecord(error)
        validation = ValidationNotice(scope: scope) { actionable.message }
    }

    private func report(_ error: Error) {
        let actionable = ActionableErrorRecord(error)
        actionableError = actionable
        errorMessage = actionable.message
        if case HarnessIPCError.hostUnavailable = error { hostStatus = "unavailable" }
        // A registration-stage failure must not leave the header on the
        // initial "Connecting…" — that reads as still in progress.
        if error is HarnessServiceError { hostStatus = "registration-failed" }
    }

    /// Explains why a control could not act. Every branch that used to `return`
    /// silently now routes through here.
    private func refuse(
        _ scope: ValidationScope, _ reason: @autoclosure @escaping () -> String
    ) {
        validation = ValidationNotice(scope: scope, build: reason)
    }

    func dismissError() {
        actionableError = nil
        errorMessage = nil
    }

    func dismissSuccess() {
        successBuilder = nil
    }

    func dismissValidation() {
        validation = nil
    }

    /// The refusal for a scope, if that is the one currently showing.
    func validationMessage(for scope: ValidationScope) -> String? {
        guard let validation, validation.scope == scope else { return nil }
        return validation.message
    }

    /// Persist a delivery failure as its Error so the sentence — and a
    /// local error's own description — both render in the current language.
    func presentDeliveryFailure(_ error: Error, live: Bool) {
        deliveryNote = {
            live
                ? S.DeliveryNote.liveRefreshUnavailable(error.localizedDescription)
                : S.DeliveryNote.unavailable(error.localizedDescription)
        }
    }

    func noteSuccess(_ message: @autoclosure @escaping () -> String) {
        successBuilder = message
        let token = UUID()
        successToken = token
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(4))
            guard let self, self.successToken == token else { return }
            self.successBuilder = nil
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

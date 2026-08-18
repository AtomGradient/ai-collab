// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import SwiftUI

private enum HighRiskIntent: Identifiable {
    case repairScenario
    case forceStop(ParticipantRecord)
    case recreateParticipantWithHandoff(ParticipantRecord)
    case breakResource(ResourceLeaseRecord)
    case destroyScenario
    case forceDestroyScenario(ScenarioRecord)
    case unregisterProject(ProjectRecord)

    var id: String {
        switch self {
        case .repairScenario: "scenario.repair"
        case let .forceStop(participant): "participant.force-stop:\(participant.id)"
        case let .recreateParticipantWithHandoff(participant):
            "participant.recreate-with-handoff:\(participant.id)"
        case let .breakResource(resource): "resource.break:\(resource.id)"
        case .destroyScenario: "scenario.destroy"
        case let .forceDestroyScenario(scenario):
            "scenario.force-destroy:\(scenario.id)"
        case let .unregisterProject(project):
            "project.unregister:\(project.id)"
        }
    }

    var title: String {
        switch self {
        case .repairScenario: "Request Scenario repair?"
        case .forceStop: "Request forced Participant stop?"
        case .recreateParticipantWithHandoff: "Start a new Agent conversation?"
        case .breakResource: "Request stale resource release?"
        case .destroyScenario: "Request Scenario destruction?"
        case .forceDestroyScenario: "Force delete this Scenario?"
        case .unregisterProject: "Unregister this project?"
        }
    }

    var message: String {
        switch self {
        case .repairScenario:
            "Repair preserves Scenario WIP and audit history. The Host will independently verify the exact degraded state, workspace fence, permissions, effect preview, and trusted single-use authorization."
        case let .forceStop(participant):
            "Force Stop may terminate the exact Harness-owned process for \(participant.id) generation \(participant.generation). The Host will independently revalidate its binding and require trusted single-use authorization."
        case let .recreateParticipantWithHandoff(participant):
            "Exact conversation recovery for \(participant.id) generation \(participant.generation) did not complete. Continuing creates a new Participant generation and a new Agent conversation. Code and WIP stay in the Scenario workspace, and the new Agent receives the current Harness identity, peers, policy, and reply rules; the previous Agent conversation is not restored."
        case let .breakResource(resource):
            "Break Lease releases only stale \(resource.resourceClass) lease \(String(resource.id.prefix(12))) after the Host proves the exact owned process is absent and obtains trusted single-use authorization."
        case .destroyScenario:
            "The Harness Host will independently verify the current target, fences, permissions, effect preview, and trusted single-use authorization."
        case let .forceDestroyScenario(scenario):
            "This permanently deletes Scenario \(scenario.id), its isolated Workspace and uncommitted Scenario WIP. Exact Harness-owned Agent windows, processes, and leases are force-cleaned first. The registered project source is never deleted; any unproven ownership or changed fence stops the operation."
        case let .unregisterProject(project):
            "This removes only the registration record for \(project.key). The Host refuses while the project still owns any Scenario, nothing on disk is touched, and the project can simply be registered again."
        }
    }
}

// MARK: - ContentView

struct ContentView: View {
    @EnvironmentObject private var model: HarnessViewModel
    @State private var highRiskIntent: HighRiskIntent?

    var body: some View {
        NavigationSplitView {
            projectsSidebar
        } content: {
            scenariosList
        } detail: {
            scenarioDetail
        }
        .disabled(model.isBusy)
        .confirmationDialog(
            highRiskIntent?.title ?? "",
            isPresented: Binding(
                get: { highRiskIntent != nil },
                set: { if !$0 { highRiskIntent = nil } }
            ),
            titleVisibility: .visible
        ) {
            if let intent = highRiskIntent {
                Button("Continue to Host confirmation", role: .destructive) {
                    Task { await performHighRiskIntent(intent) }
                }
                Button("Cancel", role: .cancel) {}
            }
        } message: {
            if let intent = highRiskIntent {
                Text(intent.message)
            }
        }
        .overlay(alignment: .top) { errorBanner }
        .overlay(alignment: .bottom) { successToast }
        .overlay { activityOverlay }
        .task { await model.bootstrap() }
        .frame(minWidth: 1100, minHeight: 720)
    }

    // MARK: - Sidebar: Projects

    private var projectsSidebar: some View {
        List(selection: Binding(
            get: { model.selectedProjectID },
            set: { id in Task { await model.selectProject(id) } }
        )) {
            Section("Projects") {
                ForEach(model.projects) { project in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(project.key)
                        Text("Contract \(project.productContractVersion)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .tag(project.id)
                    .contextMenu {
                        Button("Unregister Project…", role: .destructive) {
                            highRiskIntent = .unregisterProject(project)
                        }
                    }
                }
            }
        }
        .navigationTitle("AI Collab")
        .toolbar {
            Button("Register Project", systemImage: "plus") {
                Task { await model.chooseAndRegisterProject() }
            }
        }
        .confirmationDialog(
            "Prepare this project?",
            isPresented: Binding(
                get: { model.pendingBootstrap != nil },
                set: { if !$0 { model.pendingBootstrap = nil } }
            ),
            titleVisibility: .visible
        ) {
            if let url = model.pendingBootstrap {
                Button("Draft project files and register") {
                    model.pendingBootstrap = nil
                    Task { await model.bootstrapAndRegisterProject(url) }
                }
                Button("Cancel", role: .cancel) {}
            }
        } message: {
            if let url = model.pendingBootstrap {
                Text(
                    "\(url.lastPathComponent) has no project declaration files yet. "
                        + "AI Collab drafts project_descriptor.yaml, repo_manifest.yaml, "
                        + "a gate registry, and starter collaboration templates from the "
                        + "directory's Git repositories, then registers it. Existing "
                        + "files are never overwritten."
                )
            }
        }
        .safeAreaInset(edge: .bottom) {
            HStack {
                Circle()
                    .fill(model.hostStatus == "ready" ? .green : .orange)
                    .frame(width: 8, height: 8)
                Text("Host: \(model.hostStatus)")
                    .font(.caption)
                Spacer()
                if model.hostStatus != "ready" {
                    Button("Retry") { Task { await model.retryHostService() } }
                        .controlSize(.small)
                }
            }
            .padding(10)
            .background(.bar)
        }
    }

    // MARK: - Content: Scenarios list

    private var scenariosList: some View {
        VStack(spacing: 0) {
            HStack {
                TextField("Scenario identity", text: $model.newScenarioID)
                    .onSubmit {
                        guard model.selectedProject != nil, !model.isBusy else { return }
                        Task { await model.createScenario() }
                    }
                Button("Create") { Task { await model.createScenario() } }
                    .disabled(model.selectedProject == nil || model.isBusy)
            }
            .padding()
            validationBanner(for: .scenarioCreate)
                .padding(.horizontal)
            List(selection: Binding(
                get: { model.selectedScenarioID },
                set: { id in Task { await model.selectScenario(id) } }
            )) {
                ForEach(model.scenarios) { scenario in
                    ScenarioRoomCard(scenario: scenario)
                        .tag(scenario.id)
                        .listRowInsets(EdgeInsets(top: 3, leading: 6, bottom: 3, trailing: 6))
                        .listRowSeparator(.hidden)
                        .contextMenu {
                            Button("Force Delete Scenario…", role: .destructive) {
                                highRiskIntent = .forceDestroyScenario(scenario)
                            }
                        }
                }
            }
            .listStyle(.plain)
        }
        .navigationTitle("Scenarios")
    }

    // MARK: - Detail: Scenario

    private var scenarioDetail: some View {
        Group {
            if let scenario = model.selectedScenario {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        scenarioHeader(scenario)
                        validationBanner(for: .scenarioLifecycle)
                        participantsSection
                        preflightSection
                        topologySection
                        policySection
                        deliveriesSection
                        inspectorSection
                        highRiskSection(scenario)
                    }
                    .padding(20)
                }
                .task(id: scenario.id) {
                    await model.monitorDeliveries(for: scenario.id)
                }
            } else {
                ContentUnavailableView(
                    "Select a Scenario",
                    systemImage: "square.stack.3d.up",
                    description: Text("Register a project, create a Scenario, and operate it through the typed Harness Host.")
                )
            }
        }
    }

    // MARK: - Scenario header

    private func scenarioHeader(_ scenario: ScenarioRecord) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        Text(scenario.id).font(.title2.bold())
                        StateBadge(state: scenario.observedState)
                    }
                    Text(model.scenarioHeadline)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                HStack(spacing: 8) {
                    Button("Refresh", systemImage: "arrow.clockwise") {
                        Task { await model.refreshSelectedScenario() }
                    }
                    .labelStyle(.iconOnly)
                    Button("Prepare Workspace") {
                        Task { await model.prepareWorkspace() }
                    }
                    Button("Resume") { Task { await model.openScenario() } }
                        .disabled(
                            model.isBusy
                                || !["closed", "degraded"].contains(scenario.observedState)
                        )
                    Button("Close") { Task { await model.closeScenario() } }
                }
            }
            HStack(spacing: 16) {
                Label(
                    "\(model.runningParticipantCount) running",
                    systemImage: "person.2.fill"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                Label(
                    "\(model.deliveryTotal) deliveries",
                    systemImage: "envelope.fill"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                if !model.deliveryStates.isEmpty {
                    Text(
                        model.deliveryStates.keys.sorted().map {
                            "\($0) \(model.deliveryStates[$0] ?? 0)"
                        }.joined(separator: " · ")
                    )
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                }
            }
        }
        .padding(.bottom, 4)
    }

    // MARK: - Validation helpers

    @ViewBuilder
    private func validationBanner(for scope: ValidationScope) -> some View {
        if let message = model.validationMessage(for: scope) {
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
                Text(message)
                    .font(.callout)
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.orange.opacity(0.1), in: RoundedRectangle(cornerRadius: 8))
        }
    }

    // MARK: - Participants (primary section)

    private var participantsSection: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    TextField("Participant identity", text: $model.newParticipantID)
                        .onSubmit {
                            Task { await model.addParticipant() }
                        }
                    Picker("Template", selection: $model.selectedTemplateID) {
                        ForEach(model.interactiveTemplates) { template in
                            Text(template.displayName).tag(Optional(template.id))
                        }
                        if !model.diagnosticTemplates.isEmpty {
                            Divider()
                            Section("Advanced") {
                                ForEach(model.diagnosticTemplates) { template in
                                    Text(template.displayName).tag(Optional(template.id))
                                }
                            }
                        }
                    }
                    .frame(minWidth: 180)
                    Button("Add") { Task { await model.addParticipant() } }
                }
                validationBanner(for: .participantAdd)
                validationBanner(for: .participantAction)
                if model.participants.isEmpty {
                    Text("No participants yet. Add one above to get started.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.vertical, 12)
                } else {
                    ForEach(model.participants) { participant in
                        participantRow(participant)
                        if participant.id != model.participants.last?.id {
                            Divider()
                        }
                    }
                }
            }
            .padding(6)
        } label: {
            Label("Participants", systemImage: "person.3.fill")
                .font(.headline)
        }
    }

    private func participantRow(_ participant: ParticipantRecord) -> some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(participant.id).font(.headline)
                    StateBadge(state: participant.observedState)
                }
                if let runtimeProfileRef = participant.runtimeProfileRef {
                    Text(runtimeProfileRef)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if let modelBinding = participant.modelBinding {
                    Text(modelBinding.modelRef)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if participant.cleanupPending {
                    Text(participant.degradedReason ?? "repair required")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
            }
            Spacer()
            participantActions(participant)
        }
    }

    @ViewBuilder
    private func participantActions(_ participant: ParticipantRecord) -> some View {
        let state = participant.observedState
        HStack(spacing: 6) {
            if participant.canStart {
                Button("Start") {
                    Task { await model.startParticipant(participant) }
                }
                .controlSize(.small)
            }
            if participant.canStop {
                Button("Stop") {
                    Task { await model.stopParticipant(participant) }
                }
                .controlSize(.small)
            }
            if participant.canRecover {
                Button("Recover") {
                    Task { await model.recoverParticipant(participant) }
                }
                .controlSize(.small)
            }
            Menu {
                if participant.canForceStop {
                    Button("Force Stop", role: .destructive) {
                        highRiskIntent = .forceStop(participant)
                    }
                }
                if participant.canRecreateWithHandoff {
                    Button("Recreate + Handoff") {
                        highRiskIntent = .recreateParticipantWithHandoff(participant)
                    }
                }
                if ["stopped", "ready", "degraded", "detached"].contains(state) {
                    Menu("Replace with") {
                        ForEach(model.interactiveTemplates) { template in
                            Button(template.displayName) {
                                Task {
                                    await model.replaceParticipant(
                                        participant, template: template
                                    )
                                }
                            }
                        }
                        if !model.diagnosticTemplates.isEmpty {
                            Divider()
                            Section("Advanced") {
                                ForEach(model.diagnosticTemplates) { template in
                                    Button(template.displayName) {
                                        Task {
                                            await model.replaceParticipant(
                                                participant, template: template
                                            )
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            } label: {
                Image(systemName: "ellipsis.circle")
                    .font(.body)
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
        }
    }

    // MARK: - Collapsible sections

    @State private var showPreflight = false
    @State private var showTopology = false
    @State private var showPolicy = false
    @State private var showDeliveries = false
    @State private var showInspector = false

    private var preflightSection: some View {
        DisclosureGroup(isExpanded: $showPreflight) {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    if let preflight = model.preflight {
                        StateBadge(state: preflight.status)
                        Text(
                            preflight.status == "ready"
                                ? "All readiness checks passed."
                                : "Resolve blocked checks before starting affected work."
                        )
                        .font(.callout)
                    } else {
                        Text("Run preflight to check permissions and readiness.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Run Preflight") {
                        Task { await model.runPreflight() }
                    }
                    .controlSize(.small)
                }
                if let preflight = model.preflight {
                    ForEach(preflight.checks) { check in
                        HStack(alignment: .top) {
                            StateBadge(state: check.status)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(check.id).font(.callout.bold())
                                Text(check.summary)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            if let action = check.repairAction {
                                if action == "scenario.repair" {
                                    Button(model.repairActionLabel(action)) {
                                        highRiskIntent = .repairScenario
                                    }
                                    .controlSize(.small)
                                } else if model.canPerformRepairAction(action) {
                                    Button(model.repairActionLabel(action)) {
                                        Task { await model.performRepairAction(action) }
                                    }
                                    .controlSize(.small)
                                } else {
                                    Text(model.repairActionLabel(action))
                                        .font(.caption)
                                        .foregroundStyle(.orange)
                                }
                            }
                        }
                    }
                    ForEach(preflight.permissions) { permission in
                        HStack {
                            Image(systemName: "lock.shield")
                            Text(permission.permissionID)
                            Spacer()
                            Text(permission.status)
                                .font(.caption.bold())
                            if let code = permission.providerErrorCode {
                                Text(code)
                                    .font(.caption2.monospaced())
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            .padding(.vertical, 6)
        } label: {
            HStack {
                Label("Preflight", systemImage: "checkmark.shield")
                    .font(.headline)
                Spacer()
                if let preflight = model.preflight {
                    StateBadge(state: preflight.status)
                }
            }
        }
        .padding(10)
        .background(.secondary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
    }

    private var topologySection: some View {
        DisclosureGroup(isExpanded: $showTopology) {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("Exact window and topology-scoped geometry per interactive Participant.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Focus & Restore") {
                        Task { await model.focusScenario() }
                    }
                    .controlSize(.small)
                }
                if let topology = model.topology {
                    if topology.participants.isEmpty {
                        Text("No topology entries.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .center)
                            .padding(.vertical, 8)
                    } else {
                        ForEach(topology.participants) { item in
                            HStack {
                                StateBadge(state: item.health)
                                Text(item.id).font(.callout.bold())
                                Spacer()
                                if let geometry = item.geometryLabel {
                                    Text(geometry).font(.caption.monospaced())
                                }
                                if item.restoreOutcome != "not_requested" {
                                    Text(item.restoreOutcome)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                if let error = item.errorCode {
                                    Text(error)
                                        .font(.caption2.monospaced())
                                        .foregroundStyle(.orange)
                                }
                            }
                        }
                    }
                } else {
                    Text("No topology data. Resume or refresh the Scenario first.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.vertical, 8)
                }
            }
            .padding(.vertical, 6)
        } label: {
            Label("Window Topology", systemImage: "macwindow.on.rectangle")
                .font(.headline)
        }
        .padding(10)
        .background(.secondary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
    }

    private var policySection: some View {
        DisclosureGroup(isExpanded: $showPolicy) {
            VStack(alignment: .leading, spacing: 12) {
                if let status = model.policyStatus {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(status.policyID).font(.callout.bold())
                            Text("Version \(status.policyVersion)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        StateBadge(
                            state: status.requiresReplan ? "re-plan required" : "current"
                        )
                    }
                    if !status.generationDrift.isEmpty {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Participant generation changed")
                                .font(.callout.bold())
                            ForEach(status.generationDrift) { drift in
                                Text(
                                    "\(drift.participantID): policy g\(drift.policyGeneration) → current g\(drift.currentGeneration)"
                                )
                                .font(.caption)
                            }
                        }
                        .padding(8)
                        .background(.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
                    }
                } else {
                    Text("No active policy. Choose a team template below.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }

                HStack {
                    Picker(
                        "Team template",
                        selection: Binding(
                            get: { model.selectedPolicyTemplateID },
                            set: { model.selectPolicyTemplate($0) }
                        )
                    ) {
                        ForEach(model.policyTemplates) { template in
                            Text(template.displayName).tag(Optional(template.id))
                        }
                    }
                    .frame(minWidth: 240)
                    Button(
                        model.policyStatus?.requiresReplan == true
                            ? "Create Repair Plan"
                            : "Preview Plan"
                    ) {
                        Task { await model.planSelectedPolicy() }
                    }
                    .controlSize(.small)
                    .disabled(model.selectedPolicyTemplate == nil)
                    Button("Apply Plan") {
                        Task { await model.applySelectedPolicyPlan() }
                    }
                    .controlSize(.small)
                    .disabled(
                        model.policyPlan?.canApply != true
                            || model.policyPlan?.templateID
                                != model.selectedPolicyTemplateID
                    )
                }

                if let template = model.selectedPolicyTemplate {
                    Text("Team: \(template.participantIDs.joined(separator: ", "))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                if let plan = model.policyPlan {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Text("Plan preview").font(.callout.bold())
                            Spacer()
                            StateBadge(state: plan.canApply ? "ready" : "blocked")
                        }
                        ForEach(plan.team) { member in
                            HStack {
                                Image(
                                    systemName: member.isPresent
                                        ? "checkmark.circle.fill"
                                        : "xmark.circle.fill"
                                )
                                .foregroundStyle(member.isPresent ? .green : .red)
                                Text(member.participantID)
                                Spacer()
                                Text(
                                    member.generation.map { "g\($0)" } ?? "missing"
                                )
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            }
                        }
                        ForEach(plan.routeEffects) { route in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(
                                    "\(route.senderParticipantIDs.joined(separator: ", ")) → \(route.receiverParticipantIDs.joined(separator: ", "))"
                                )
                                .font(.callout.bold())
                                Text(
                                    "\(route.messageKind) · \(route.effect)"
                                        + (route.maxAttempts.map { " · up to \($0) attempts" } ?? "")
                                )
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            }
                        }
                        ForEach(plan.blockers, id: \.self) { blocker in
                            Text(blocker)
                                .font(.caption)
                                .foregroundStyle(.red)
                        }
                    }
                    .padding(10)
                    .background(.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
                }
            }
            .padding(.vertical, 6)
        } label: {
            HStack {
                Label("Collaboration Policy", systemImage: "shared.with.you")
                    .font(.headline)
                Spacer()
                if let status = model.policyStatus {
                    StateBadge(
                        state: status.requiresReplan ? "re-plan required" : "current"
                    )
                }
            }
        }
        .padding(10)
        .background(.secondary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
    }

    private var deliveriesSection: some View {
        DisclosureGroup(isExpanded: $showDeliveries) {
            VStack(alignment: .leading, spacing: 10) {
                if model.deliveries.isEmpty {
                    Text("No deliveries recorded yet.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.vertical, 8)
                } else {
                    ForEach(model.deliveries) { delivery in
                        VStack(alignment: .leading, spacing: 5) {
                            HStack {
                                Text(delivery.isThreadRoot ? "Thread" : "Reply")
                                    .font(.caption.bold())
                                Text(String(delivery.id.prefix(12)))
                                    .font(.system(.caption, design: .monospaced))
                                Text(delivery.messageKind)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Spacer()
                                StateBadge(state: delivery.state)
                                if delivery.retryEligible {
                                    Button("Retry") {
                                        Task { await model.retryDelivery(delivery) }
                                    }
                                    .controlSize(.small)
                                }
                            }
                            Text(
                                "\(delivery.sender.participantID) → \(delivery.receiver.participantID)"
                            )
                            .font(.callout)
                            Text(
                                "Last: \(delivery.lastEvent) · seq \(delivery.eventSequence)"
                            )
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            if let reason = delivery.degradedReason {
                                Text(reason)
                                    .font(.caption)
                                    .foregroundStyle(.orange)
                            }
                        }
                        .padding(.leading, delivery.isThreadRoot ? 0 : 18)
                        Divider()
                    }
                    if model.nextDeliveryPage != nil {
                        Button("Load More") {
                            Task { await model.loadMoreDeliveries() }
                        }
                        .controlSize(.small)
                    }
                }
            }
            .padding(.vertical, 6)
        } label: {
            HStack {
                Label("Agent Deliveries", systemImage: "envelope.fill")
                    .font(.headline)
                Spacer()
                if model.deliveryTotal > 0 {
                    Text("\(model.deliveryTotal)")
                        .font(.caption.bold())
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(10)
        .background(.secondary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
    }

    private var inspectorSection: some View {
        DisclosureGroup(isExpanded: $showInspector) {
            TabView {
                InspectorText(title: "Diagnostics", text: model.diagnosticText)
                    .tabItem { Text("Diagnostics") }
                InspectorText(title: "Resources", text: model.resourceText)
                    .tabItem { Text("Resources") }
                InspectorText(title: "Policy", text: model.policyText)
                    .tabItem { Text("Policy") }
                InspectorText(title: "Receipt", text: model.receiptText)
                    .tabItem { Text("Receipt") }
                InspectorText(title: "Resume", text: model.resumeText)
                    .tabItem { Text("Resume") }
            }
            .frame(minHeight: 220)
        } label: {
            Label("Inspector", systemImage: "terminal")
                .font(.headline)
        }
        .padding(10)
        .background(.secondary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
    }

    private func highRiskSection(_ scenario: ScenarioRecord) -> some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 10) {
                Text("The Host presents its trusted native single-use confirmation; this App cannot bypass it.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                HStack {
                    if ["provision_failed", "degraded"].contains(scenario.observedState) {
                        Button("Repair Scenario") {
                            highRiskIntent = .repairScenario
                        }
                        .controlSize(.small)
                    }
                    Button("Load Destroy Preview") {
                        Task { await model.loadDestroyPreview() }
                    }
                    .controlSize(.small)
                    Button("Destroy Scenario", role: .destructive) {
                        highRiskIntent = .destroyScenario
                    }
                    .controlSize(.small)
                    .disabled(!model.destroyPreviewEligible)
                }
                ForEach(model.resources.filter(\.canBreak)) { resource in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Stale \(resource.resourceClass) lease")
                                .font(.callout.bold())
                            Text(
                                "\(String(resource.id.prefix(12))) · \(resource.participantID) g\(resource.participantGeneration) · \(resource.staleReason ?? "stale")"
                            )
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Break Lease", role: .destructive) {
                            highRiskIntent = .breakResource(resource)
                        }
                        .controlSize(.small)
                    }
                }
                if !model.destroyPreviewText.isEmpty {
                    Text(model.destroyPreviewText)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                }
            }
            .padding(.vertical, 6)
        } label: {
            Label("High-risk Actions", systemImage: "exclamationmark.triangle")
                .font(.headline)
                .foregroundStyle(.red)
        }
        .padding(10)
        .background(.secondary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
    }

    // MARK: - Overlays

    @ViewBuilder
    private var errorBanner: some View {
        if let error = model.actionableError {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(error.message).font(.callout.bold())
                    Text(
                        "\(error.code) · \(error.category) · mutation \(error.mutationState)"
                            + (error.retryable ? " · retryable" : "")
                    )
                    .font(.caption.monospaced())
                    if let action = error.repairAction {
                        HStack {
                            Text("Recommended: \(model.repairActionLabel(action))")
                                .font(.caption)
                            if action == "scenario.repair" {
                                Button("Review Repair") { highRiskIntent = .repairScenario }
                                    .controlSize(.small)
                            } else if model.canPerformRepairAction(action) {
                                Button(model.repairActionLabel(action)) {
                                    Task { await model.performRepairAction(action) }
                                }
                                .controlSize(.small)
                            }
                        }
                    }
                }
                Spacer()
                Button {
                    model.dismissError()
                } label: {
                    Image(systemName: "xmark")
                        .font(.caption.bold())
                }
                .buttonStyle(.plain)
            }
            .padding(12)
            .background(.red.opacity(0.9), in: RoundedRectangle(cornerRadius: 8))
            .foregroundStyle(.white)
            .padding()
            .transition(.move(edge: .top).combined(with: .opacity))
        }
    }

    @ViewBuilder
    private var successToast: some View {
        if let message = model.successMessage {
            HStack(spacing: 8) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                Text(message)
                    .font(.callout)
                Spacer()
                Button {
                    model.dismissSuccess()
                } label: {
                    Image(systemName: "xmark")
                        .font(.caption.bold())
                }
                .buttonStyle(.plain)
            }
            .padding(12)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
            .padding()
            .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }

    @ViewBuilder
    private var activityOverlay: some View {
        if let activityText = model.activityText {
            VStack(spacing: 12) {
                ProgressView()
                Text(activityText)
                    .font(.callout)
                    .multilineTextAlignment(.center)
                if let progress = model.operationProgressText {
                    Text(progress)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                }
                if model.operationCanCancel {
                    Button("Cancel safely") {
                        Task { await model.cancelActiveOperation() }
                    }
                }
            }
            .padding(24)
            .frame(maxWidth: 420)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
            .shadow(radius: 12)
        }
    }

    // MARK: - Actions

    private func performHighRiskIntent(_ intent: HighRiskIntent) async {
        switch intent {
        case .repairScenario:
            await model.repairScenario()
        case let .forceStop(participant):
            await model.forceStopParticipant(participant)
        case let .recreateParticipantWithHandoff(participant):
            await model.recreateParticipantWithHandoff(participant)
        case let .breakResource(resource):
            await model.breakResource(resource)
        case .destroyScenario:
            await model.destroyScenario()
        case let .forceDestroyScenario(scenario):
            await model.forceDestroyScenario(scenario)
        case let .unregisterProject(project):
            await model.unregisterProject(project)
        }
    }
}

// MARK: - StateBadge (semantic color)

private struct StateBadge: View {
    let state: String

    private var color: Color {
        switch state {
        case "ready", "running", "delivered", "consumed", "current", "passed":
            .green
        case "stopped", "detached", "not_requested":
            .gray
        case "starting", "stopping", "recovering", "repairing", "replacing",
             "queued", "pending":
            .blue
        case "degraded", "re-plan required", "blocked":
            .orange
        case "provision_failed", "failed", "rejected":
            .red
        default:
            .secondary
        }
    }

    var body: some View {
        Text(HarnessViewModel.humanState(state))
            .font(.caption.bold())
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .foregroundStyle(color)
            .background(color.opacity(0.12), in: Capsule())
    }
}

// MARK: - ScenarioRoomCard

private struct ScenarioRoomCard: View {
    let scenario: ScenarioRecord

    private var stateColor: Color {
        switch scenario.observedState {
        case "ready", "running":
            .green
        case "degraded", "blocked":
            .orange
        case "provision_failed", "failed":
            .red
        default:
            .gray
        }
    }

    private var isLive: Bool {
        ["ready", "running"].contains(scenario.observedState)
    }

    private var needsAttention: Bool {
        ["degraded", "provision_failed", "failed", "blocked"].contains(scenario.observedState)
    }

    private var statusSummary: String {
        let count = scenario.participantIDs.count
        switch scenario.observedState {
        case "ready", "running":
            return "\(count) participant\(count == 1 ? "" : "s")"
        case "closed":
            return "closed · \(count) participant\(count == 1 ? "" : "s")"
        case "degraded":
            return "\(count) participant\(count == 1 ? "" : "s") · degraded"
        case "provision_failed":
            return "workspace setup failed"
        default:
            return HarnessViewModel.humanState(scenario.observedState)
        }
    }

    var body: some View {
        HStack(spacing: 0) {
            RoundedRectangle(cornerRadius: 2)
                .fill(stateColor)
                .frame(width: 4)
                .padding(.vertical, 2)
                .opacity(needsAttention ? 1.0 : 1.0)

            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Text(scenario.id)
                        .font(.system(.body, weight: .semibold))
                        .lineLimit(1)
                    Spacer()
                    StateBadge(state: scenario.observedState)
                }
                HStack(spacing: 8) {
                    HStack(spacing: -4) {
                        ForEach(
                            Array(scenario.participantIDs.prefix(4).enumerated()),
                            id: \.offset
                        ) { _, participantID in
                            ParticipantInitialsView(id: participantID)
                        }
                        if scenario.participantIDs.count > 4 {
                            Text("+\(scenario.participantIDs.count - 4)")
                                .font(.system(size: 9, weight: .bold))
                                .foregroundStyle(.secondary)
                                .frame(width: 20, height: 20)
                                .background(.secondary.opacity(0.15), in: Circle())
                                .padding(.leading, -2)
                        }
                    }

                    if isLive {
                        Circle()
                            .fill(.green)
                            .frame(width: 6, height: 6)
                            .modifier(PulseModifier())
                    }

                    Text(statusSummary)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
        }
        .background(.secondary.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
    }
}

// MARK: - ParticipantInitialsView

private struct ParticipantInitialsView: View {
    let id: String

    private var initials: String {
        let cleaned = id.replacingOccurrences(of: "-", with: " ")
        let words = cleaned.split(separator: " ")
        if words.count >= 2 {
            return String(words[0].prefix(1) + words[1].prefix(1)).uppercased()
        }
        return String(id.prefix(2)).uppercased()
    }

    private var color: Color {
        let hash = id.unicodeScalars.reduce(0) { $0 &+ Int($1.value) }
        let colors: [Color] = [
            .purple, .orange, .green, .pink, .cyan, .indigo, .mint, .teal
        ]
        return colors[abs(hash) % colors.count]
    }

    var body: some View {
        Text(initials)
            .font(.system(size: 9, weight: .bold))
            .foregroundStyle(.white)
            .frame(width: 20, height: 20)
            .background(color, in: Circle())
            .overlay(Circle().stroke(.background, lineWidth: 2))
    }
}

// MARK: - PulseModifier

private struct PulseModifier: ViewModifier {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var isPulsing = false

    func body(content: Content) -> some View {
        if reduceMotion {
            content
        } else {
            content
                .scaleEffect(isPulsing ? 1.0 : 0.7)
                .opacity(isPulsing ? 1.0 : 0.5)
                .animation(
                    .easeInOut(duration: 2.0).repeatForever(autoreverses: true),
                    value: isPulsing
                )
                .onAppear { isPulsing = true }
        }
    }
}

// MARK: - InspectorText

private struct InspectorText: View {
    let title: String
    let text: String

    var body: some View {
        ScrollView {
            Text(text)
                .font(.system(.caption, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .topLeading)
                .padding()
        }
        .accessibilityLabel(title)
    }
}

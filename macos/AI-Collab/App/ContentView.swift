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
        case .repairScenario: S.HighRisk.repairTitle
        case .forceStop: S.HighRisk.forceStopTitle
        case .recreateParticipantWithHandoff: S.HighRisk.recreateTitle
        case .breakResource: S.HighRisk.breakResourceTitle
        case .destroyScenario: S.HighRisk.destroyTitle
        case .forceDestroyScenario: S.HighRisk.forceDestroyTitle
        case .unregisterProject: S.HighRisk.unregisterTitle
        }
    }

    var message: String {
        switch self {
        case .repairScenario:
            S.HighRisk.repairMessage
        case let .forceStop(participant):
            S.HighRisk.forceStopMessage(participant.id, participant.generation)
        case let .recreateParticipantWithHandoff(participant):
            S.HighRisk.recreateMessage(participant.id, participant.generation)
        case let .breakResource(resource):
            S.HighRisk.breakResourceMessage(
                resource.resourceClass, String(resource.id.prefix(12))
            )
        case .destroyScenario:
            S.HighRisk.destroyMessage
        case let .forceDestroyScenario(scenario):
            S.HighRisk.forceDestroyMessage(scenario.id)
        case let .unregisterProject(project):
            S.HighRisk.unregisterMessage(project.key)
        }
    }
}

// MARK: - ContentView

struct ContentView: View {
    @EnvironmentObject private var model: HarnessViewModel
    @State private var highRiskIntent: HighRiskIntent?
    @State private var pendingDeletion: ParticipantRecord?

    var body: some View {
        NavigationSplitView {
            projectsSidebar
        } content: {
            scenariosList
        } detail: {
            scenarioDetail
        }
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button(S.Chrome.registerProject, systemImage: "plus") {
                    Task { await model.chooseAndRegisterProject() }
                }
                .help(S.Chrome.registerProject)
            }
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
                Button(S.Common.continueToHostConfirmation, role: .destructive) {
                    Task { await performHighRiskIntent(intent) }
                }
                Button(S.Common.cancel, role: .cancel) {}
            }
        } message: {
            if let intent = highRiskIntent {
                Text(intent.message)
            }
        }
        .confirmationDialog(
            S.Colleagues.deleteConfirmTitle(pendingDeletion?.id ?? ""),
            isPresented: Binding(
                get: { pendingDeletion != nil },
                set: { if !$0 { pendingDeletion = nil } }
            ),
            titleVisibility: .visible
        ) {
            if let participant = pendingDeletion {
                Button(S.Common.delete, role: .destructive) {
                    pendingDeletion = nil
                    Task { await model.deleteParticipant(participant) }
                }
                Button(S.Common.cancel, role: .cancel) {}
            }
        } message: {
            if let participant = pendingDeletion {
                Text(S.Colleagues.deleteConfirmMessage(participant.id))
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
            Section(S.Projects.sectionTitle) {
                ForEach(model.projects) { project in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(project.key)
                        Text(S.Projects.contractVersion(project.productContractVersion))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        if let reconciliation = model.projectReconciliations[project.id],
                           reconciliation.status == "attention" {
                            Text(
                                reconciliation.bindingChanged
                                    ? S.Projects.updateAvailable
                                    : reconciliation.changes.isEmpty
                                        ? S.Projects.needsAttention
                                        : S.Projects.repositoryChanges(
                                            reconciliation.changes.count
                                        )
                            )
                            .font(.caption)
                            .foregroundStyle(.orange)
                            if reconciliation.bindingChanged {
                                Button(S.Projects.applyUpdate) {
                                    Task {
                                        await model.acceptProjectReconciliation(project.id)
                                    }
                                }
                                .buttonStyle(.link)
                                .font(.caption)
                            }
                        }
                    }
                    .tag(project.id)
                    .contextMenu {
                        Button(S.Projects.checkUpdates) {
                            Task { await model.reconcileProject(project.id, surfaceErrors: true) }
                        }
                        if let reconciliation = model.projectReconciliations[project.id],
                           reconciliation.bindingChanged {
                            Button(S.Projects.applyDetectedUpdate) {
                                Task { await model.acceptProjectReconciliation(project.id) }
                            }
                        }
                        Button(S.Projects.unregister, role: .destructive) {
                            highRiskIntent = .unregisterProject(project)
                        }
                    }
                }
            }
        }
        .navigationTitle(S.Chrome.appTitle)
        .confirmationDialog(
            S.Register.confirmTitle,
            isPresented: Binding(
                get: { model.pendingRegistrationURL != nil },
                set: { if !$0 { model.pendingRegistrationURL = nil } }
            ),
            titleVisibility: .visible
        ) {
            if let url = model.pendingRegistrationURL {
                Button(S.Register.confirmAction) {
                    model.pendingRegistrationURL = nil
                    Task { await model.confirmProjectRegistration(url) }
                }
                Button(S.Common.cancel, role: .cancel) {}
            }
        } message: {
            if let url = model.pendingRegistrationURL {
                Text(S.Register.confirmMessage(url.lastPathComponent))
            }
        }
        .safeAreaInset(edge: .bottom) {
            HStack {
                Circle()
                    .fill(model.hostReady ? .green : .orange)
                    .frame(width: 8, height: 8)
                Text(S.Chrome.hostStatusLine(model.hostStatusDisplay))
                    .font(.caption)
                Spacer()
                if !model.hostReady {
                    Button(S.Common.retry) { Task { await model.retryHostService() } }
                        .controlSize(.small)
                } else if let permission = model.presentationPermissionStatus,
                          permission != "granted" {
                    Button(S.Chrome.grantITermAccess) {
                        Task { await model.requestPresentationPermission() }
                    }
                    .controlSize(.small)
                    .help(S.Chrome.grantITermHelp)
                }
                SettingsLink {
                    Label(S.Settings.diagnosticsTab, systemImage: "stethoscope")
                        .labelStyle(.iconOnly)
                }
                .controlSize(.small)
                .help(S.Chrome.diagnosticsHelp)
            }
            .padding(10)
            .background(.bar)
        }
    }

    // MARK: - Content: Scenarios list

    private var scenariosList: some View {
        VStack(spacing: 0) {
            HStack {
                TextField(S.Rooms.identityPlaceholder, text: $model.newScenarioID)
                    .onSubmit {
                        guard model.selectedProject != nil, !model.isBusy else { return }
                        Task { await model.createScenario() }
                    }
                Button(S.Rooms.createButton) { Task { await model.createScenario() } }
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
                            Button(S.Rooms.forceDelete, role: .destructive) {
                                highRiskIntent = .forceDestroyScenario(scenario)
                            }
                        }
                }
            }
            .listStyle(.plain)
        }
        .navigationTitle(S.Rooms.listTitle)
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
                    S.Rooms.selectTitle,
                    systemImage: "square.stack.3d.up",
                    description: Text(S.Rooms.selectDescription)
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
                    Button(S.Detail.refresh, systemImage: "arrow.clockwise") {
                        Task { await model.refreshSelectedScenario() }
                    }
                    .labelStyle(.iconOnly)
                    Button(S.Detail.prepareWorkspace) {
                        Task { await model.prepareWorkspace() }
                    }
                    Button(S.Detail.resume) { Task { await model.openScenario() } }
                        .disabled(
                            model.isBusy
                                || !["closed", "degraded"].contains(scenario.observedState)
                        )
                    Button(S.Detail.startAll) {
                        Task { await model.startAllParticipants() }
                    }
                    .disabled(
                        model.isBusy
                            || !model.participants.contains(where: \.canStart)
                            || scenario.desiredState != "running"
                            || !["running", "opening", "degraded"].contains(
                                scenario.observedState
                            )
                    )
                    .help(
                        S.Detail.startAllHelp
                    )
                    Button(S.Detail.close) { Task { await model.closeScenario() } }
                }
            }
            HStack(spacing: 16) {
                Label(
                    S.Colleagues.runningCount(model.runningParticipantCount),
                    systemImage: "person.2.fill"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                Label(
                    S.Colleagues.deliveryCount(model.deliveryTotal),
                    systemImage: "envelope.fill"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                if !model.deliveryStates.isEmpty {
                    Text(
                        model.deliveryStates.keys.sorted().map {
                            "\(S.Delivery.stateLabel($0)) \(model.deliveryStates[$0] ?? 0)"
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
                    TextField(S.Colleagues.identityPlaceholder, text: $model.newParticipantID)
                        .onSubmit {
                            Task { await model.addParticipant() }
                        }
                    Picker(S.Colleagues.templatePicker, selection: $model.selectedTemplateID) {
                        ForEach(model.interactiveTemplates) { template in
                            Text(template.displayName).tag(Optional(template.id))
                        }
                        if !model.diagnosticTemplates.isEmpty {
                            Divider()
                            Section(S.Colleagues.advanced) {
                                ForEach(model.diagnosticTemplates) { template in
                                    Text(template.displayName).tag(Optional(template.id))
                                }
                            }
                        }
                    }
                    .frame(minWidth: 180)
                    Button(S.Colleagues.add) { Task { await model.addParticipant() } }
                }
                validationBanner(for: .participantAdd)
                validationBanner(for: .participantAction)
                if model.participants.isEmpty {
                    Text(S.Colleagues.emptyHint)
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
            Label(S.Colleagues.sectionTitle, systemImage: "person.3.fill")
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
                    Text(participant.degradedReason ?? S.Colleagues.repairRequired)
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
                Button(S.Colleagues.start) {
                    Task { await model.startParticipant(participant) }
                }
                .controlSize(.small)
            }
            if participant.canStop {
                Button(S.Colleagues.stop) {
                    Task { await model.stopParticipant(participant) }
                }
                .controlSize(.small)
            }
            if participant.canRecover {
                Button(S.Colleagues.recover) {
                    Task { await model.recoverParticipant(participant) }
                }
                .controlSize(.small)
            }
            Menu {
                if participant.canForceStop {
                    Button(S.Colleagues.forceStop, role: .destructive) {
                        highRiskIntent = .forceStop(participant)
                    }
                }
                if participant.canRecreateWithHandoff {
                    Button(S.Colleagues.recreateHandoff) {
                        highRiskIntent = .recreateParticipantWithHandoff(participant)
                    }
                }
                if state == "stopped" {
                    Divider()
                    Button(S.Colleagues.deleteMenu, role: .destructive) {
                        pendingDeletion = participant
                    }
                }
                if ["stopped", "ready", "degraded", "detached"].contains(state) {
                    Menu(S.Colleagues.replaceWith) {
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
                            Section(S.Colleagues.advanced) {
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
                                ? S.Preflight.allPassed
                                : S.Preflight.resolveBlocked
                        )
                        .font(.callout)
                    } else {
                        Text(S.Preflight.runHint)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(S.Preflight.runButton) {
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
                            Text(S.Preflight.permissionStatus(permission.status))
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
                Label(S.Preflight.sectionTitle, systemImage: "checkmark.shield")
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
                    Text(S.Topology.description)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button(S.Topology.focusRestore) {
                        Task { await model.focusScenario() }
                    }
                    .controlSize(.small)
                }
                if let topology = model.topology {
                    if topology.participants.isEmpty {
                        Text(S.Topology.noEntries)
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
                    Text(S.Topology.noData)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.vertical, 8)
                }
            }
            .padding(.vertical, 6)
        } label: {
            Label(S.Topology.sectionTitle, systemImage: "macwindow.on.rectangle")
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
                            Text(S.Policy.version(status.policyVersion))
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
                            Text(S.Policy.generationChanged)
                                .font(.callout.bold())
                            ForEach(status.generationDrift) { drift in
                                Text(
                                    S.Policy.driftRow(drift.participantID, drift.policyGeneration, drift.currentGeneration)
                                )
                                .font(.caption)
                            }
                        }
                        .padding(8)
                        .background(.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
                    }
                } else {
                    Text(S.Policy.noActivePolicy)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }

                HStack {
                    Picker(
                        S.Policy.teamTemplate,
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
                            ? S.Policy.createRepairPlan
                            : S.Policy.previewPlan
                    ) {
                        Task { await model.planSelectedPolicy() }
                    }
                    .controlSize(.small)
                    .disabled(model.selectedPolicyTemplate == nil)
                    Button(S.Policy.applyPlan) {
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
                    Text(S.Policy.teamLine(template.participantIDs.joined(separator: ", ")))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                if let plan = model.policyPlan {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Text(S.Policy.planPreview).font(.callout.bold())
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
                                    member.generation.map { "g\($0)" } ?? S.Policy.memberMissing
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
                                        + (route.maxAttempts.map { S.Policy.upToAttempts($0) } ?? "")
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
                Label(S.Policy.sectionTitle, systemImage: "shared.with.you")
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
                    Text(S.Deliveries.empty)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.vertical, 8)
                } else {
                    ForEach(model.deliveries) { delivery in
                        VStack(alignment: .leading, spacing: 5) {
                            HStack {
                                Text(delivery.isThreadRoot ? S.Deliveries.thread : S.Deliveries.reply)
                                    .font(.caption.bold())
                                Text(String(delivery.id.prefix(12)))
                                    .font(.system(.caption, design: .monospaced))
                                Text(delivery.messageKind)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Spacer()
                                StateBadge(state: delivery.state)
                                if delivery.retryEligible {
                                    Button(S.Common.retry) {
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
                                S.Deliveries.lastEvent(delivery.lastEvent, delivery.eventSequence)
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
                        Button(S.Deliveries.loadMore) {
                            Task { await model.loadMoreDeliveries() }
                        }
                        .controlSize(.small)
                    }
                }
            }
            .padding(.vertical, 6)
        } label: {
            HStack {
                Label(S.Deliveries.sectionTitle, systemImage: "envelope.fill")
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
                InspectorText(title: S.Settings.diagnosticsTab, text: model.diagnosticText)
                    .tabItem { Text(S.Settings.diagnosticsTab) }
                InspectorText(title: S.Inspector.resources, text: model.resourceText)
                    .tabItem { Text(S.Inspector.resources) }
                InspectorText(title: S.Inspector.policy, text: model.policyText)
                    .tabItem { Text(S.Inspector.policy) }
                InspectorText(title: S.Inspector.receipt, text: model.receiptText)
                    .tabItem { Text(S.Inspector.receipt) }
                InspectorText(title: S.Inspector.resume, text: model.resumeText)
                    .tabItem { Text(S.Inspector.resume) }
            }
            .frame(minHeight: 220)
        } label: {
            Label(S.Inspector.sectionTitle, systemImage: "terminal")
                .font(.headline)
        }
        .padding(10)
        .background(.secondary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
    }

    private func highRiskSection(_ scenario: ScenarioRecord) -> some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 10) {
                Text(S.Risk.hostConfirmNote)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                HStack {
                    if ["provision_failed", "degraded"].contains(scenario.observedState) {
                        Button(S.Risk.repairScenario) {
                            highRiskIntent = .repairScenario
                        }
                        .controlSize(.small)
                    }
                    Button(S.Risk.loadDestroyPreview) {
                        Task { await model.loadDestroyPreview() }
                    }
                    .controlSize(.small)
                    Button(S.Risk.destroyScenario, role: .destructive) {
                        highRiskIntent = .destroyScenario
                    }
                    .controlSize(.small)
                    .disabled(!model.destroyPreviewEligible)
                }
                ForEach(model.resources.filter(\.canBreak)) { resource in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(S.Risk.staleLease(resource.resourceClass))
                                .font(.callout.bold())
                            Text(
                                "\(String(resource.id.prefix(12))) · \(resource.participantID) g\(resource.participantGeneration) · \(resource.staleReason ?? S.Risk.staleDefault)"
                            )
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button(S.Risk.breakLease, role: .destructive) {
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
            Label(S.Risk.sectionTitle, systemImage: "exclamationmark.triangle")
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
                        S.Banner.machineLine(
                            error.code, error.category, error.mutationState,
                            retryable: error.retryable
                        )
                    )
                    .font(.caption.monospaced())
                    if let action = error.repairAction {
                        HStack {
                            Text(S.Banner.recommended(model.repairActionLabel(action)))
                                .font(.caption)
                            if action == "scenario.repair" {
                                Button(S.Banner.reviewRepair) { highRiskIntent = .repairScenario }
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
                    Button(S.Banner.cancelSafely) {
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

struct DiagnosticsView: View {
    @EnvironmentObject private var model: HarnessViewModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                GroupBox(S.Diagnostics.about) {
                    VStack(alignment: .leading, spacing: 6) {
                        aboutRow(S.Diagnostics.appVersion, HarnessViewModel.appVersionText)
                        aboutRow(
                            S.Diagnostics.harnessContract,
                            HarnessViewModel.contractVersionText
                        )
                        HStack {
                            Text(S.Diagnostics.host)
                                .foregroundStyle(.secondary)
                            Spacer()
                            HStack(spacing: 6) {
                                Circle()
                                    .fill(model.hostReady ? .green : .orange)
                                    .frame(width: 8, height: 8)
                                Text(model.hostStatusDisplay)
                                    .font(.caption.bold())
                            }
                        }
                    }
                    .padding(6)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                GroupBox(S.Diagnostics.machineReadiness) {
                    VStack(alignment: .leading, spacing: 8) {
                        if model.environmentObservations.isEmpty {
                            Text(S.Diagnostics.noReport)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                        }
                        ForEach(model.environmentObservations) { observation in
                            environmentRow(observation)
                        }
                    }
                    .padding(6)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                GroupBox(S.Diagnostics.automationPermission) {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(S.Diagnostics.itermControl)
                            Text(S.Diagnostics.requiredBefore)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        StateBadge(
                            state: model.presentationPermissionStatus ?? "unknown"
                        )
                        if model.presentationPermissionStatus != "granted" {
                            Button(S.Diagnostics.requestPermission) {
                                Task {
                                    await model.requestPresentationPermission()
                                }
                            }
                            .controlSize(.small)
                        }
                    }
                    .padding(6)
                }
                HStack {
                    Spacer()
                    Button(S.Detail.refresh, systemImage: "arrow.clockwise") {
                        Task {
                            await model.refreshEnvironmentReport()
                            await model.refreshPresentationPermission()
                        }
                    }
                }
            }
            .padding(20)
        }
        .frame(minWidth: 560, minHeight: 460)
        .navigationTitle(S.Settings.diagnosticsTab)
        .task {
            await model.refreshEnvironmentReport()
            await model.refreshPresentationPermission()
        }
    }

    private func aboutRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value).font(.callout.monospaced())
        }
    }

    private func environmentRow(
        _ observation: EnvironmentObservationRecord
    ) -> some View {
        HStack(alignment: .firstTextBaseline) {
            StateBadge(state: observation.status)
            VStack(alignment: .leading, spacing: 2) {
                Text(observation.displayName)
                Text(observation.subjectRef)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                if observation.status != "available",
                   let remediation = observation.remediationRef {
                    Text(S.Diagnostics.installHint(remediation))
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
            }
            Spacer()
            if let version = observation.observedVersion {
                Text(version)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
            }
        }
    }
}

private struct StateBadge: View {
    let state: String

    private var color: Color {
        switch state {
        case "ready", "running", "delivered", "consumed", "current", "passed",
             "available", "granted":
            .green
        case "stopped", "detached", "not_requested":
            .gray
        case "starting", "stopping", "recovering", "repairing", "replacing",
             "destroying", "queued", "pending":
            .blue
        case "degraded", "re-plan required", "blocked", "not_determined":
            .orange
        case "provision_failed", "failed", "rejected", "missing", "denied":
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
        case "repairing", "destroying":
            .blue
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
            return S.Rooms.memberCount(count)
        case "closed":
            return S.Rooms.closedSummary(count)
        case "degraded":
            return S.Rooms.degradedSummary(count)
        case "provision_failed":
            return S.Status.label("provision_failed")
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

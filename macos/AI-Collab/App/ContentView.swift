import SwiftUI

private enum HighRiskIntent: Identifiable {
    case repairScenario
    case forceStop(ParticipantRecord)
    case recreateParticipantWithHandoff(ParticipantRecord)
    case breakResource(ResourceLeaseRecord)
    case destroyScenario
    case forceDestroyScenario(ScenarioRecord)

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
        }
    }
}

struct ContentView: View {
    @EnvironmentObject private var model: HarnessViewModel
    @State private var highRiskIntent: HighRiskIntent?

    var body: some View {
        NavigationSplitView {
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
                    }
                }
            }
            .navigationTitle("AI Collab")
            .toolbar {
                Button("Register Project", systemImage: "folder.badge.plus") {
                    Task { await model.chooseAndRegisterProject() }
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
        } content: {
            VStack(spacing: 0) {
                HStack {
                    TextField("Scenario identity", text: $model.newScenarioID)
                    Button("Create") { Task { await model.createScenario() } }
                        .disabled(model.selectedProject == nil || model.isBusy)
                }
                .padding()
                List(selection: Binding(
                    get: { model.selectedScenarioID },
                    set: { id in Task { await model.selectScenario(id) } }
                )) {
                    ForEach(model.scenarios) { scenario in
                        HStack {
                            VStack(alignment: .leading) {
                                Text(scenario.id)
                                Text("desired \(scenario.desiredState) · observed \(scenario.observedState)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            StateBadge(text: scenario.observedState)
                        }
                        .tag(scenario.id)
                        .contextMenu {
                            Button("Force Delete Scenario…", role: .destructive) {
                                highRiskIntent = .forceDestroyScenario(scenario)
                            }
                        }
                    }
                }
            }
            .navigationTitle("Scenarios")
        } detail: {
            if let scenario = model.selectedScenario {
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        HStack {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(scenario.id).font(.title2.bold())
                                Text("Generation \(scenario.generation) · Revision \(scenario.stateRevision)")
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("Refresh", systemImage: "arrow.clockwise") {
                                Task { await model.refreshSelectedScenario() }
                            }
                            Button("Prepare Workspace") {
                                Task { await model.prepareWorkspace() }
                            }
                            Button("Resume") { Task { await model.openScenario() } }
                                .disabled(
                                    model.isBusy
                                        || !["closed", "degraded"].contains(
                                            scenario.observedState
                                        )
                                )
                            Button("Close") { Task { await model.closeScenario() } }
                        }

                        GroupBox("Preflight") {
                            VStack(alignment: .leading, spacing: 10) {
                                HStack {
                                    if let preflight = model.preflight {
                                        StateBadge(text: preflight.status)
                                        Text(
                                            preflight.status == "ready"
                                                ? "Current permissions and readiness checks passed."
                                                : "Resolve the blocked checks before starting affected work."
                                        )
                                        .font(.callout)
                                    } else {
                                        Text("Run fresh, no-prompt permission and readiness checks.")
                                            .font(.callout)
                                    }
                                    Spacer()
                                    Button("Run Preflight") {
                                        Task { await model.runPreflight() }
                                    }
                                }
                                if let preflight = model.preflight {
                                    ForEach(preflight.checks) { check in
                                        HStack(alignment: .top) {
                                            StateBadge(text: check.status)
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
                                                } else if model.canPerformRepairAction(action) {
                                                    Button(model.repairActionLabel(action)) {
                                                        Task { await model.performRepairAction(action) }
                                                    }
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
                            .padding(6)
                        }

                        GroupBox("Window Topology") {
                            VStack(alignment: .leading, spacing: 8) {
                                HStack {
                                    Text(
                                        "Each interactive Participant keeps its own exact window and topology-scoped geometry."
                                    )
                                    .font(.callout)
                                    Spacer()
                                    Button("Focus & Restore") {
                                        Task { await model.focusScenario() }
                                    }
                                }
                                if let topology = model.topology {
                                    ForEach(topology.participants) { item in
                                        HStack {
                                            StateBadge(text: item.health)
                                            Text(item.id).font(.callout.bold())
                                            Text("generation \(item.generation)")
                                                .font(.caption)
                                                .foregroundStyle(.secondary)
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
                            }
                            .padding(6)
                        }

                        GroupBox("Participants") {
                            VStack(alignment: .leading, spacing: 10) {
                                HStack {
                                    TextField("Participant identity", text: $model.newParticipantID)
                                    Picker("Template", selection: $model.selectedTemplateID) {
                                        ForEach(model.templates) { template in
                                            Text(template.displayName).tag(Optional(template.id))
                                        }
                                    }
                                    .frame(minWidth: 180)
                                    Button("Add") { Task { await model.addParticipant() } }
                                }
                                ForEach(model.participants) { participant in
                                    HStack {
                                        VStack(alignment: .leading) {
                                            Text(participant.id).font(.headline)
                                            Text("\(participant.desiredState) / \(participant.observedState)")
                                                .font(.caption)
                                                .foregroundStyle(.secondary)
                                            Text("Generation \(participant.generation)")
                                                .font(.caption2)
                                                .foregroundStyle(.secondary)
                                            Text(
                                                "Profile \(participant.runtimeProfileRef ?? "driver default")"
                                            )
                                            .font(.caption2.monospaced())
                                            .foregroundStyle(.secondary)
                                            if let continuityMode = participant.continuityMode {
                                                Text("Continuity \(continuityMode)")
                                                    .font(.caption2.monospaced())
                                                    .foregroundStyle(.secondary)
                                            }
                                            if let modelBinding = participant.modelBinding {
                                                Text(
                                                    "Model \(modelBinding.modelRef) · Provider \(modelBinding.providerProfileRef)"
                                                )
                                                .font(.caption2.monospaced())
                                                .foregroundStyle(.secondary)
                                                if let inference = modelBinding.inferenceProfileRef {
                                                    Text("Inference \(inference)")
                                                        .font(.caption2.monospaced())
                                                        .foregroundStyle(.secondary)
                                                }
                                            } else {
                                                Text("Model binding: profile default")
                                                    .font(.caption2)
                                                    .foregroundStyle(.secondary)
                                            }
                                            if participant.cleanupPending {
                                                Text("Cleanup pending · \(participant.degradedReason ?? "repair required")")
                                                    .font(.caption2)
                                                    .foregroundStyle(.orange)
                                            }
                                        }
                                        Spacer()
                                        StateBadge(text: participant.observedState)
                                        Button("Start") {
                                            Task { await model.startParticipant(participant) }
                                        }
                                        Button("Stop") {
                                            Task { await model.stopParticipant(participant) }
                                        }
                                        if participant.canRecover {
                                            Button("Recover") {
                                                Task { await model.recoverParticipant(participant) }
                                            }
                                        }
                                        if participant.canForceStop {
                                            Button("Force Stop", role: .destructive) {
                                                highRiskIntent = .forceStop(participant)
                                            }
                                        }
                                        if participant.canRecreateWithHandoff {
                                            Button("Recreate + Handoff") {
                                                highRiskIntent = .recreateParticipantWithHandoff(
                                                    participant
                                                )
                                            }
                                        }
                                        Button("Replace") {
                                            Task { await model.replaceParticipant(participant) }
                                        }
                                        .disabled(
                                            !["stopped", "ready", "degraded"].contains(
                                                participant.observedState
                                            )
                                        )
                                    }
                                    Divider()
                                }
                            }
                            .padding(6)
                        }

                        GroupBox("Collaboration policy") {
                            VStack(alignment: .leading, spacing: 12) {
                                if let status = model.policyStatus {
                                    HStack {
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(status.policyID).font(.headline)
                                            Text("Policy version \(status.policyVersion)")
                                                .font(.caption)
                                                .foregroundStyle(.secondary)
                                        }
                                        Spacer()
                                        StateBadge(
                                            text: status.requiresReplan ? "re-plan required" : "current"
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
                                    Text("No active collaboration policy. Choose a team template and preview its exact effect.")
                                        .font(.callout)
                                }

                                Text(model.policyMessage)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)

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
                                    .disabled(model.selectedPolicyTemplate == nil)
                                    Button("Apply Plan") {
                                        Task { await model.applySelectedPolicyPlan() }
                                    }
                                    .disabled(
                                        model.policyPlan?.canApply != true
                                            || model.policyPlan?.templateID
                                                != model.selectedPolicyTemplateID
                                    )
                                }

                                if let template = model.selectedPolicyTemplate {
                                    Text("Declared team: \(template.participantIDs.joined(separator: ", "))")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }

                                if let plan = model.policyPlan {
                                    VStack(alignment: .leading, spacing: 8) {
                                        HStack {
                                            Text("Plan preview").font(.headline)
                                            Spacer()
                                            Text(plan.canApply ? "ready" : "blocked")
                                                .font(.caption.bold())
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
                                                Text(member.generation.map { "generation \($0)" } ?? "missing")
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
                            .padding(6)
                        }

                        GroupBox("Agent deliveries") {
                            VStack(alignment: .leading, spacing: 10) {
                                HStack {
                                    Text(model.deliveryMessage)
                                        .font(.callout)
                                    Spacer()
                                    if !model.deliveryStates.isEmpty {
                                        Text(
                                            model.deliveryStates.keys.sorted().map {
                                                "\($0) \(model.deliveryStates[$0] ?? 0)"
                                            }.joined(separator: " · ")
                                        )
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                    }
                                }
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
                                            StateBadge(text: delivery.state)
                                            if delivery.retryEligible {
                                                Button("Retry") {
                                                    Task { await model.retryDelivery(delivery) }
                                                }
                                            }
                                        }
                                        Text(
                                            "\(delivery.sender.participantID) g\(delivery.sender.generation) → \(delivery.receiver.participantID) g\(delivery.receiver.generation)"
                                        )
                                        .font(.callout)
                                        Text(
                                            "Last event: \(delivery.lastEvent) · sequence \(delivery.eventSequence) · \(delivery.retryReason)"
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
                                }
                            }
                            .padding(6)
                        }

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
                        .frame(minHeight: 270)

                        GroupBox("High-risk actions") {
                            VStack(alignment: .leading, spacing: 10) {
                                Text("Preview the exact effect first. The Host then presents its trusted native single-use confirmation; this App cannot bypass it.")
                                    .font(.callout)
                                HStack {
                                    if ["provision_failed", "degraded"].contains(
                                        scenario.observedState
                                    ) {
                                        Button("Repair Scenario") {
                                            highRiskIntent = .repairScenario
                                        }
                                    }
                                    Button("Load Destroy Preview") {
                                        Task { await model.loadDestroyPreview() }
                                    }
                                    Button("Destroy Scenario", role: .destructive) {
                                        highRiskIntent = .destroyScenario
                                    }
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
                                    }
                                }
                                if !model.destroyPreviewText.isEmpty {
                                    Text(model.destroyPreviewText)
                                        .font(.system(.caption, design: .monospaced))
                                        .textSelection(.enabled)
                                }
                            }
                            .padding(6)
                        }
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
        .disabled(model.isBusy)
        .alert(item: $highRiskIntent) { intent in
            Alert(
                title: Text(intent.title),
                message: Text(intent.message),
                primaryButton: .destructive(Text("Continue to Host confirmation")) {
                    Task { await performHighRiskIntent(intent) }
                },
                secondaryButton: .cancel()
            )
        }
        .overlay(alignment: .top) {
            if let error = model.actionableError {
                VStack(alignment: .leading, spacing: 6) {
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
                    .padding(10)
                    .background(.red.opacity(0.9), in: RoundedRectangle(cornerRadius: 8))
                    .foregroundStyle(.white)
                    .padding()
            }
        }
        .overlay {
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
        .task { await model.bootstrap() }
        .frame(minWidth: 1100, minHeight: 720)
    }

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
        }
    }
}

private struct StateBadge: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.caption.bold())
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(.blue.opacity(0.12), in: Capsule())
    }
}

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

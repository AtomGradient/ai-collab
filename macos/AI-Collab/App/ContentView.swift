// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import AppKit
import SwiftUI

/// Instrument palette for *categorical* chart series only.  Semantic colours
/// (good / warning / unobserved) stay system colours so they keep their
/// conventional meaning and accessibility behaviour.
extension Color {
    fileprivate static func instrument(
        light: (Double, Double, Double),
        dark: (Double, Double, Double)
    ) -> Color {
        Color(
            nsColor: NSColor(name: nil) { appearance in
                let channels = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
                    ? dark
                    : light
                return NSColor(
                    srgbRed: channels.0,
                    green: channels.1,
                    blue: channels.2,
                    alpha: 1
                )
            }
        )
    }

    /// Deep petrol: the strongest evidence tier.
    fileprivate static let evidenceStrong = instrument(
        light: (0.165, 0.365, 0.384),
        dark: (0.435, 0.702, 0.714)
    )
    /// Muted petrol: evidence present but weaker.
    fileprivate static let evidenceSoft = instrument(
        light: (0.498, 0.690, 0.702),
        dark: (0.239, 0.420, 0.427)
    )
    /// Ochre: settled without evidence.
    fileprivate static let evidenceAbsent = instrument(
        light: (0.647, 0.439, 0.110),
        dark: (0.827, 0.643, 0.361)
    )

    /// Foreground for a label drawn on top of the matching series colour.
    /// Picked per series because the palette deliberately mixes light and
    /// dark tiers, so one shared "white" would fail contrast on half of them.
    fileprivate static let onEvidenceStrong = instrument(
        light: (1, 1, 1),
        dark: (0.055, 0.086, 0.098)
    )
    fileprivate static let onEvidenceSoft = instrument(
        light: (0.055, 0.145, 0.153),
        dark: (0.906, 0.941, 0.945)
    )
    fileprivate static let onEvidenceAbsent = instrument(
        light: (1, 1, 1),
        dark: (0.129, 0.098, 0.043)
    )
}

private enum HighRiskIntent: Identifiable {
    case repairScenario
    case forceStop(ParticipantRecord)
    case recreateParticipantWithHandoff(ParticipantRecord)
    case breakResource(ResourceLeaseRecord)
    case forceDestroyScenario(ScenarioRecord)
    case unregisterProject(ProjectRecord)

    var id: String {
        switch self {
        case .repairScenario: "scenario.repair"
        case let .forceStop(participant): "participant.force-stop:\(participant.id)"
        case let .recreateParticipantWithHandoff(participant):
            "participant.recreate-with-handoff:\(participant.id)"
        case let .breakResource(resource): "resource.break:\(resource.id)"
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
        case let .forceDestroyScenario(scenario):
            S.HighRisk.forceDestroyMessage(scenario.id)
        case let .unregisterProject(project):
            S.HighRisk.unregisterMessage(project.key)
        }
    }
}

// MARK: - Destroy panel (the one entry point into the delete flow)

/// `.sheet(item:)` payload — identity is the Scenario's own id, so retargeting
/// the sheet at a different room (another row's "Delete…") is a normal state
/// change, not a reuse of stale identity.
private struct DestroyPanelTarget: Identifiable {
    let scenario: ScenarioRecord
    var id: String { scenario.id }
}

/// The single UI component behind every "delete a task room" entry point —
/// the room board's row menu, the mission bar's "…" menu, and Evidence &
/// Diagnostics' high-risk tab all open this same sheet instead of each
/// rolling their own preview/blockers/confirm mechanics.
///
/// Review 20260903-185641-e6nznb: the room a user right-clicks is not
/// necessarily `model.selectedScenario` — `loadDestroyPreview()` reads
/// whichever Scenario is selected, so this panel explicitly selects its own
/// target first and only then loads the preview, rather than trusting
/// whatever happened to be open already.
private struct DestroyPanel: View {
    let scenario: ScenarioRecord
    /// Delegates to the caller's own `highRiskIntent = .forceDestroyScenario`
    /// — the Host confirmation for that path is unchanged, just reached from
    /// here once a loaded preview says it is actually blocked.
    let onForceDelete: (ScenarioRecord) -> Void

    @EnvironmentObject private var model: HarnessViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var loaded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(S.Risk.destroyPanelTitle(scenario.id))
                .font(.title3.bold())
            Text(S.HighRisk.destroyMessage)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(S.Risk.hostConfirmNote)
                .font(.caption)
                .foregroundStyle(.secondary)

            if !loaded {
                HStack {
                    ProgressView().controlSize(.small)
                    Text(S.Risk.destroyPreviewLoading)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            } else if model.destroyPreviewEligible {
                Label(S.Risk.destroyPreviewOK, systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
            } else {
                Label(
                    S.Risk.destroyPreviewBlocked(model.destroyPreviewBlockers),
                    systemImage: "exclamationmark.triangle.fill"
                )
                .foregroundStyle(.orange)
            }

            if loaded, !model.destroyPreviewText.isEmpty {
                ScrollView {
                    Text(model.destroyPreviewText)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 180)
            }

            HStack {
                Button(S.Common.cancel) { dismiss() }
                Spacer()
                Button(S.Common.retry, systemImage: "arrow.clockwise") {
                    Task { await load() }
                }
                .disabled(model.isBusy)
                if loaded, model.destroyPreviewEligible {
                    Button(S.Risk.destroyScenario, role: .destructive) {
                        Task {
                            await model.destroyScenario()
                            dismiss()
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(model.isBusy)
                } else if loaded {
                    Button(S.Rooms.forceDelete, role: .destructive) {
                        let target = scenario
                        dismiss()
                        onForceDelete(target)
                    }
                    .disabled(model.isBusy)
                }
            }
        }
        .padding(20)
        .frame(width: 460)
        .task { await load() }
    }

    private func load() async {
        loaded = false
        if model.selectedScenarioID != scenario.id {
            await model.selectScenario(scenario.id)
        }
        await model.loadDestroyPreview()
        loaded = true
    }
}

// MARK: - ContentView

struct ContentView: View {
    @EnvironmentObject private var model: HarnessViewModel
    @AppStorage("AICollabGuideSeen") private var guideSeen = false
    @State private var highRiskIntent: HighRiskIntent?
    @State private var pendingDeletion: ParticipantRecord?
    @State private var destroyPanelTarget: DestroyPanelTarget?

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
            ToolbarItem {
                Button(S.Guide.reopenHelp, systemImage: "questionmark.circle") {
                    model.guideStep = model.guidePresentation().index
                }
                .help(S.Guide.reopenHelp)
            }
        }
        .onAppear {
            if !guideSeen {
                guideSeen = true
                model.guideStep = model.guidePresentation().index
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
        .sheet(item: $destroyPanelTarget) { target in
            DestroyPanel(scenario: target.scenario) { scenario in
                highRiskIntent = .forceDestroyScenario(scenario)
            }
            .environmentObject(model)
        }
        .overlay(alignment: .top) { errorBanner }
        .overlay(alignment: .bottomTrailing) { readyMomentCard }
        .overlay { guideCard }
        .overlay(alignment: .bottom) { successToast }
        .overlay { activityOverlay }
        .onChange(of: model.selectedScenarioID) { _, _ in endObjectiveEditing() }
        .task { await model.bootstrap() }
        .frame(minWidth: 1100, minHeight: 720)
    }

    // MARK: - Getting-started guide (centered card deck)

    /// The six teachable steps, matching the manual's happy path. Each step
    /// carries its say and, where meaningful, the existing action it teaches.
    private struct GuideStep {
        let say: String
    }

    private var guideSteps: [GuideStep] {
        [
            GuideStep(say: S.Guide.registerSay),
            GuideStep(say: S.Guide.createSay),
            GuideStep(say: S.Guide.prepareSay),
            GuideStep(say: S.Guide.addSay),
            GuideStep(say: S.Guide.policySay),
            GuideStep(say: S.Guide.focusSay),
        ]
    }

    /// The live next step, rendered as the Scenario's own primary action
    /// rather than a dismissible card. `HarnessViewModel.guidance` stays the
    /// only source of flow truth; this view never re-derives it.
    @ViewBuilder
    private var scenarioFlowSection: some View {
        if case .focusAndAssign = model.guidance {
            // Operational: the flow collapses to one line so Collaboration
            // Health and the team keep the first viewport.
            HStack(spacing: 9) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                Text(S.Flow.ready)
                    .font(.callout.weight(.medium))
                Spacer(minLength: 8)
                if let action = liveGuideAction() {
                    Button(action.label, action: action.perform)
                        .controlSize(.small)
                        .disabled(model.isBusy)
                }
            }
        } else {
            VStack(alignment: .leading, spacing: 9) {
                Text(flowEyebrow)
                    .font(.system(size: 10, weight: .semibold))
                    .textCase(.uppercase)
                    .foregroundStyle(model.lifecycleActionsPreempted ? .orange : .teal)
                Text(liveGuideSay)
                    .font(.title3.weight(.medium))
                    .fixedSize(horizontal: false, vertical: true)
                if let action = liveGuideAction() {
                    Button(action.label, action: action.perform)
                        .buttonStyle(.borderedProminent)
                        .disabled(model.isBusy)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// Leaves the objective editor and restores the drafts to what is
    /// committed, so a later Edit opens on the current objective rather than
    /// on an empty form.
    private func endObjectiveEditing() {
        editingObjective = false
        model.resetObjectiveDrafts()
    }

    private var flowEyebrow: String {
        switch model.guidance {
        case .attend, .inconsistent: S.Flow.attention
        case .working: S.Flow.inProgress
        default: S.Flow.nextStep
        }
    }

    private var liveGuideSay: String {
        switch model.guidance {
        case .registerProject: S.Guide.registerSay
        case .createRoom: S.Guide.createSay
        case .prepareWorkspace: S.Guide.prepareSay
        case .addColleague: S.Guide.addSay
        case .resumeRoom: S.Guide.resumeSay
        case .configurePolicy: S.Guide.policySay
        case .startColleagues: S.Guide.startSay
        case .focusAndAssign: S.Guide.focusSay
        case .attend(let state): S.Guide.attendSay(state)
        case .working(let state): S.Guide.workingSay(state)
        case .inconsistent: S.Guide.inconsistentSay
        }
    }

    /// The real action for the exact live step. Blocked and transitional
    /// states never produce one.
    private func liveGuideAction() -> (label: String, perform: () -> Void)? {
        switch model.guidance {
        case .registerProject:
            return (S.Guide.registerAction, { Task { await model.chooseAndRegisterProject() } })
        case .createRoom:
            return (S.Guide.createAction, { Task { await model.createScenario() } })
        case .prepareWorkspace:
            return (S.Guide.prepareAction, { Task { await model.prepareWorkspace() } })
        case .addColleague:
            return (S.Guide.addAction, { Task { await model.addParticipant() } })
        case .resumeRoom:
            return (S.Guide.resumeAction, { Task { await model.openScenario() } })
        case .configurePolicy:
            return (
                S.Guide.configurePolicyAction,
                { Task { await model.applyRecommendedPolicy() } }
            )
        case .startColleagues:
            return (S.Guide.startAction, { Task { await model.startAllParticipants() } })
        case .focusAndAssign:
            return (S.Guide.focusAction, { Task { await model.focusScenario() } })
        case .inconsistent:
            // Refresh is a read, not a lifecycle mutation, so it cannot be an
            // action the Host would refuse. This state named refreshing in its
            // own copy while rendering no button at all.
            return (S.Guide.recheckAction, { Task { await model.refreshSelectedScenario() } })
        case .attend, .working:
            return nil
        }
    }

    /// The embedded real action for the exact live actionable step, if the
    /// open card is showing that step. Blocked/transitional states never
    /// produce a card action.
    private func guideAction(at index: Int) -> (label: String, perform: () -> Void)? {
        let presentation = model.guidePresentation()
        guard presentation.index == index, let live = presentation.actionable else {
            return nil
        }
        switch live {
        case .registerProject:
            return (S.Guide.registerAction, { Task { await model.chooseAndRegisterProject() } })
        case .createRoom:
            return (S.Guide.createAction, { Task { await model.createScenario() } })
        case .prepareWorkspace:
            return (S.Guide.prepareAction, { Task { await model.prepareWorkspace() } })
        case .addColleague:
            return (S.Guide.addAction, { Task { await model.addParticipant() } })
        case .resumeRoom:
            return (S.Guide.resumeAction, { Task { await model.openScenario() } })
        case .configurePolicy:
            return (
                S.Guide.configurePolicyAction,
                { Task { await model.applyRecommendedPolicy() } }
            )
        case .startColleagues:
            return (S.Guide.startAction, { Task { await model.startAllParticipants() } })
        case .focusAndAssign:
            return (S.Guide.focusAction, { Task { await model.focusScenario() } })
        case .attend, .working, .inconsistent:
            return nil
        }
    }

    @ViewBuilder
    private var guideCard: some View {
        if let index = model.guideStep, guideSteps.indices.contains(index) {
            let step = guideSteps[index]
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text(S.Guide.stepOf(index + 1, guideSteps.count))
                        .font(.caption.bold())
                        .textCase(.uppercase)
                        .foregroundStyle(.teal)
                    Spacer()
                    Button {
                        model.guideStep = nil
                    } label: {
                        Image(systemName: "xmark").font(.caption.bold())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(S.Common.close)
                    .help(S.Common.close)
                }
                Text(step.say)
                    .font(.title3.weight(.medium))
                    .fixedSize(horizontal: false, vertical: true)
                if let action = guideAction(at: index) {
                    Button(action.label, action: action.perform)
                        .buttonStyle(.borderedProminent)
                        .disabled(model.isBusy)
                }
                HStack {
                    if index > 0 {
                        Button(S.Guide.previous) { model.guideStep = index - 1 }
                    }
                    Spacer()
                    if index < guideSteps.count - 1 {
                        Button(S.Guide.next) { model.guideStep = index + 1 }
                            .buttonStyle(.bordered)
                    } else {
                        Button(S.Guide.done) { model.guideStep = nil }
                            .buttonStyle(.bordered)
                    }
                }
            }
            .padding(22)
            .frame(width: 440)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
            .shadow(radius: 18)
            .transition(.opacity)
        }
    }

    private var readyMomentCard: some View {
        Group {
            if model.showReadyMoment {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "checkmark.seal.fill")
                        .foregroundStyle(.green)
                        .font(.title3)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(S.Guide.readyMomentTitle).font(.headline)
                        Text(S.Guide.readyMomentBody)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        Button(S.Guide.focusAction) {
                            model.dismissReadyMoment()
                            Task { await model.focusScenario() }
                        }
                        .controlSize(.small)
                        .padding(.top, 3)
                    }
                    Button {
                        model.dismissReadyMoment()
                    } label: {
                        Image(systemName: "xmark").font(.caption.bold())
                    }
                    .buttonStyle(.plain)
                }
                .padding(14)
                .frame(maxWidth: 380)
                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
                .shadow(radius: 10)
                .padding()
                .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
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
            VStack(spacing: 8) {
                TextField(S.Rooms.objectivePlaceholder, text: $model.newScenarioObjective)
                TextField(S.Rooms.identityPlaceholder, text: $model.newScenarioID)
                    .onSubmit {
                        guard model.selectedProject != nil, !model.isBusy else { return }
                        Task { await model.createScenario() }
                    }
                HStack {
                    Spacer()
                    Button(S.Rooms.createButton) { Task { await model.createScenario() } }
                        .disabled(model.selectedProject == nil || model.isBusy)
                }
            }
            .padding()
            validationBanner(for: .scenarioCreate)
                .padding(.horizontal)
            List(selection: Binding(
                get: { model.selectedScenarioID },
                set: { id in Task { await model.selectScenario(id) } }
            )) {
                ForEach(scenarioGroups, id: \.label) { group in
                    Section(group.label) {
                        ForEach(group.scenarios) { scenario in
                            ScenarioRoomCard(scenario: scenario)
                                .tag(scenario.id)
                                .listRowInsets(EdgeInsets(top: 3, leading: 6, bottom: 3, trailing: 6))
                                .listRowSeparator(.hidden)
                                .contextMenu {
                                    Button(S.Rooms.deleteMenu, role: .destructive) {
                                        destroyPanelTarget = DestroyPanelTarget(scenario: scenario)
                                    }
                                }
                        }
                    }
                }
            }
            .listStyle(.plain)
        }
        .navigationTitle(S.Rooms.listTitle)
    }

    /// Needs Attention (attention ∪ failed) → In Progress (working, including
    /// every transitional sub-state) → Closed (inactive) — three stable
    /// groups, review 20260903-181141-6gjonu point 6. Order within a group is
    /// exactly the Host's own `model.scenarios` order: the Store is
    /// clock-free, so there is no timestamp to re-sort by, and pretending
    /// otherwise would be the same kind of invented data the review's point 1
    /// already ruled out. An unrecognized `presentationClass` (there is none
    /// today — `.working`/`.attention` are the only two `default:` targets in
    /// the entity mapping) still falls into Needs Attention, never silently
    /// into Closed.
    private var scenarioGroups: [(label: String, scenarios: [ScenarioRecord])] {
        var attention: [ScenarioRecord] = []
        var active: [ScenarioRecord] = []
        var closed: [ScenarioRecord] = []
        for scenario in model.scenarios {
            switch scenario.presentationClass {
            case .attention, .failed:
                attention.append(scenario)
            case .inactive:
                closed.append(scenario)
            default:
                active.append(scenario)
            }
        }
        var groups: [(String, [ScenarioRecord])] = []
        if !attention.isEmpty { groups.append((S.NeedsAttention.sectionTitle, attention)) }
        if !active.isEmpty { groups.append((S.Rooms.activeGroupLabel, active)) }
        if !closed.isEmpty { groups.append((S.Status.label("closed"), closed)) }
        return groups
    }

    // MARK: - Detail: Scenario

    private var scenarioDetail: some View {
        Group {
            if let scenario = model.selectedScenario {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        // Phase 1 IA (review 20260903-175908-nyr2wy): mission
                        // (who/why/next) → health → team → evidence, folded.
                        // Nothing removed from the pre-redesign order below —
                        // deliveries/preflight/topology/policy/resources moved
                        // inside `technicalSection`, now the collapsed
                        // Evidence & Diagnostics drawer, with their own
                        // internals untouched.
                        missionBar(scenario)
                        healthCard(scenario)
                        collaborationHealthSection
                        needsAttentionSection
                        deliveryDistributionSection
                        participantsSection
                        technicalSection(scenario)
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

    /// The mission bar: who/why this room exists and what to do next, in one
    /// visual group. Every piece below is the pre-redesign view, unmodified —
    /// this only changes what groups with what. Deliberately not a card of
    /// its own (no extra background/border): `objectiveSection` already owns
    /// one, and stacking a second around it is exactly the "cards inside
    /// cards" the review flagged elsewhere.
    private func missionBar(_ scenario: ScenarioRecord) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            scenarioHeader(scenario)
            validationBanner(for: .scenarioLifecycle)
            Divider()
            scenarioFlowSection
            Divider()
            objectiveSection(scenario)
        }
        .padding(14)
        .background(.secondary.opacity(0.04), in: RoundedRectangle(cornerRadius: 10))
    }

    private func objectiveSection(_ scenario: ScenarioRecord) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack {
                Label(S.Objective.sectionTitle, systemImage: "scope")
                    .font(.headline)
                Spacer()
                if !editingObjective {
                    Button(
                        scenario.objective.isEmpty
                            ? S.Objective.setObjective
                            : S.Objective.edit
                    ) { editingObjective = true }
                    .controlSize(.small)
                    .disabled(model.isBusy)
                }
                if scenario.objectiveRevision > 0 {
                    Text(S.Objective.revision(scenario.objectiveRevision))
                        .font(.caption.monospacedDigit())
                        .padding(.horizontal, 7)
                        .padding(.vertical, 3)
                        .background(.secondary.opacity(0.12), in: RoundedRectangle(cornerRadius: 6))
                }
            }
            if scenario.objective.isEmpty {
                Text(S.Objective.notSet)
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                Text(scenario.objective)
                    .font(.title3)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                if !scenario.acceptanceCriteria.isEmpty {
                    Text(S.Objective.acceptanceCriteria)
                        .font(.caption.bold())
                        .foregroundStyle(.secondary)
                    Text(scenario.acceptanceCriteria)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }
            if editingObjective {
                HStack {
                    TextField(S.Objective.objectivePlaceholder, text: $model.objectiveDraft)
                        .controlSize(.small)
                    TextField(
                        S.Objective.acceptancePlaceholder,
                        text: $model.acceptanceCriteriaDraft
                    )
                    .controlSize(.small)
                    Button(S.Objective.addRevision, systemImage: "plus") {
                        Task {
                            // Close only on a committed revision. A blank draft
                            // or a Host refusal returns normally, and closing
                            // here would take away both the field and the
                            // scoped explanation the user needs to correct it.
                            let before = model.selectedScenario?.objectiveRevision
                            await model.appendScenarioObjective()
                            if model.selectedScenario?.objectiveRevision != before {
                                endObjectiveEditing()
                            }
                        }
                    }
                    .controlSize(.small)
                    .disabled(model.isBusy)
                    Button(S.Common.cancel) { endObjectiveEditing() }
                        .controlSize(.small)
                }
                .font(.callout)
                validationBanner(for: .objective)
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
                        PresentationBadge(
                            cls: scenario.presentationClass,
                            label: HarnessViewModel.humanState(scenario.observedState)
                        )
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
                    // Repair is gated on the exact Host precondition
                    // (`scenario.repair` accepts only provision_failed or
                    // degraded) — the same test `highRiskSection` already
                    // used, now the single canonical place this control
                    // appears (removed from `healthCard` and
                    // `highRiskSection`, both now redundant with this one).
                    // It is deliberately its own button, not the guidance
                    // action below: `model.guidance` offers no action at all
                    // for .attend/.working/.inconsistent, and this must not
                    // pretend otherwise.
                    if ["provision_failed", "degraded"].contains(scenario.observedState) {
                        Button(S.Risk.repairScenario) {
                            highRiskIntent = .repairScenario
                        }
                        .tint(.orange)
                    }
                    // Prepare / Resume / Start All now live in the
                    // persistent flow section, which offers exactly the one
                    // step whose Host precondition currently holds. Keeping
                    // header duplicates meant offering operations the Host
                    // refuses: workspace.plan needs a closed Scenario,
                    // scenario.open needs a ready workspace, and close accepts
                    // only opening/running/degraded.
                    Button(S.Detail.close) { Task { await model.closeScenario() } }
                        .disabled(
                            model.isBusy
                                || model.lifecycleActionsPreempted
                                || !["opening", "running", "degraded"].contains(
                                    scenario.observedState
                                )
                        )
                    // The one UI entry point for the delete flow, alongside
                    // the room board's own row menu — both open the same
                    // `DestroyPanel`. Force Delete is never offered here
                    // directly; the panel decides eligible-vs-blocked after
                    // loading a real preview (review 20260903-185641-e6nznb).
                    Menu {
                        Button(S.Rooms.deleteMenu) {
                            destroyPanelTarget = DestroyPanelTarget(scenario: scenario)
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                    .menuStyle(.borderlessButton)
                    .fixedSize()
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
                    S.Colleagues.deliveryCount(model.deliverySummary?.total ?? 0),
                    systemImage: "envelope.fill"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                if let states = model.deliverySummary?.states, !states.isEmpty {
                    Text(
                        states.keys.sorted().map {
                            "\(S.Delivery.stateLabel($0)) \(states[$0] ?? 0)"
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
                    PresentationBadge(
                        cls: participant.presentationClass,
                        label: HarnessViewModel.humanState(participant.observedState)
                    )
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
                if let scenario = model.selectedScenario,
                   scenario.objectiveRevision > 0 {
                    let issued = participant.issuedObjectiveRevision
                        >= scenario.objectiveRevision
                    Text(issued ? S.Objective.issued : S.Objective.pendingIssuance)
                        .font(.caption)
                        .foregroundStyle(issued ? Color.secondary : Color.orange)
                        .help(issued ? "" : S.Objective.pendingIssuanceHelp)
                }
                if participant.cleanupPending {
                    Text(
                        participant.degradedReason.map(
                            HarnessViewModel.humanDegradedReason
                        ) ?? S.Colleagues.repairRequired
                    )
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

    @State private var editingObjective = false

    /// One evidence domain selected at a time inside the drawer — review
    /// 20260903-183736-clqu6r P1-4: nesting a `DisclosureGroup` per domain
    /// inside the outer drawer's own `DisclosureGroup` added a second fold
    /// nobody asked for. A single selection replaces all five of the old
    /// per-section `show*` bools.
    private enum EvidenceTab: Hashable {
        case deliveries, preflight, topology, policy, resources, inspector, highRisk
    }
    @State private var evidenceTab: EvidenceTab = .deliveries

    /// Just the content — no DisclosureGroup, no label. Selection among
    /// evidence domains is the tab strip's job now; wrapping this in its own
    /// collapsible would recreate the double-fold review 20260903-183736-
    /// clqu6r's P1-4 flagged (drawer-inside-drawer).
    private var preflightSection: some View {
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
                                } else if let instruction = model.textOnlyRepairAction(action) {
                                    Text(instruction)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
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
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: "lock.shield")
                                .foregroundStyle(
                                    permission.status == "granted" ? Color.secondary : Color.orange
                                )
                            VStack(alignment: .leading, spacing: 2) {
                                Text(permission.permissionID)
                                    .font(.callout.bold())
                                if let code = permission.providerErrorCode {
                                    Text(code)
                                        .font(.caption2.monospaced())
                                        .foregroundStyle(.secondary)
                                }
                                if let remediation = permission.remediationRef {
                                    Text(
                                        model.repairActionDetail(remediation)
                                            ?? model.repairActionLabel(remediation)
                                    )
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                }
                            }
                            Spacer(minLength: 8)
                            VStack(alignment: .trailing, spacing: 4) {
                                Text(S.Preflight.permissionStatus(permission.status))
                                    .font(.caption.bold())
                                if let remediation = permission.remediationRef,
                                   model.canPerformRepairAction(remediation) {
                                    Button(model.repairActionLabel(remediation)) {
                                        Task { await model.performRepairAction(remediation) }
                                    }
                                    .controlSize(.small)
                                }
                            }
                        }
                    }
                }
            }
            .padding(.vertical, 6)
    }

    private var topologySection: some View {
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
    }

    private var policySection: some View {
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
    }

    private var deliveriesSection: some View {
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
                        Divider()
                    }
                }
            }
            .padding(.vertical, 6)
    }

    private var needsAttentionSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(S.NeedsAttention.sectionTitle, systemImage: "exclamationmark.triangle.fill")
                .font(.headline)
            // The all-clear is a room-wide claim, so it is decided from the
            // collection summary. `model.deliveryAttention` is one bounded
            // page and can only ever supply examples.
            if let totals = model.deliveryAttentionTotals, totals.isClear {
                Label(S.NeedsAttention.allClear, systemImage: "checkmark.circle.fill")
                    .font(.callout)
                    .foregroundStyle(.green)
            } else if let totals = model.deliveryAttentionTotals {
                // One line per category, never a sum: a delivery whose
                // repeated last attempt failed is counted in both, and the
                // summary carries no intersection to subtract.
                if totals.degraded > 0 {
                    Text(S.NeedsAttention.degradedCount(totals.degraded))
                        .font(.callout)
                        .foregroundStyle(.orange)
                }
                if totals.retried > 0 {
                    Text(S.NeedsAttention.retriedCount(totals.retried))
                        .font(.callout)
                        .foregroundStyle(.orange)
                }
                if model.deliveryAttention.isEmpty {
                    Text(
                        S.NeedsAttention.noExamplesOnPage(
                            DeliveryCollectionRecord.rawActivityLimit
                        )
                    )
                    .font(.caption)
                    .foregroundStyle(.secondary)
                } else {
                    Text(S.NeedsAttention.examplesHeading)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                ForEach(model.deliveryAttention) { item in
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "exclamationmark.circle.fill")
                            .foregroundStyle(.orange)
                        VStack(alignment: .leading, spacing: 2) {
                            Button(String(item.delivery.id.prefix(12))) {
                                // Both halves matter: opening only the inner
                                // tab while the outer drawer stays collapsed
                                // is invisible to the user — review
                                // 20260903-183736-clqu6r P1-2.
                                showTechnical = true
                                evidenceTab = .deliveries
                            }
                            .buttonStyle(.link)
                            .font(.system(.callout, design: .monospaced).bold())
                            Text(S.NeedsAttention.reason(item.reason))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            } else {
                // No collection summary means the question is unanswered.
                // Never render an all-clear from an absent measurement.
                Label(S.NeedsAttention.unavailable, systemImage: "questionmark.circle")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(10)
        .background(.secondary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
    }

    private var collaborationHealthSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(S.CollaborationHealth.sectionTitle, systemImage: "waveform.path.ecg")
                .font(.headline)
            if let health = model.collaborationHealth {
                LazyVGrid(
                    columns: [
                        GridItem(.adaptive(minimum: 128, maximum: 240), spacing: 10)
                    ],
                    alignment: .leading,
                    spacing: 10
                ) {
                    CollaborationHealthMetricTile(
                        title: S.CollaborationHealth.teamReady,
                        value: health.teamReady.value,
                        total: health.teamReady.total,
                        color: healthColor(for: health.teamReady)
                    )
                    CollaborationHealthMetricTile(
                        title: S.CollaborationHealth.requestsClosed,
                        value: health.requestsClosed.value,
                        total: health.requestsClosed.total,
                        color: healthColor(for: health.requestsClosed)
                    )
                    CollaborationHealthMetricTile(
                        title: S.CollaborationHealth.endToEndEvidence,
                        value: health.endToEndEvidence.value,
                        total: health.endToEndEvidence.total,
                        color: healthColor(for: health.endToEndEvidence)
                    )
                    CollaborationHealthMetricTile(
                        title: S.CollaborationHealth.firstAttemptDelivery,
                        value: health.firstAttemptDelivery.value,
                        total: health.firstAttemptDelivery.total,
                        color: healthColor(for: health.firstAttemptDelivery)
                    )
                    CollaborationHealthMetricTile(
                        title: S.CollaborationHealth.degraded,
                        value: health.degradedTotal,
                        total: nil,
                        color: health.degradedTotal == 0 ? .green : .orange
                    )
                }
            } else {
                Text(S.CollaborationHealth.loading)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.vertical, 12)
            }
        }
    }

    private func healthColor(for ratio: CollaborationHealthRatio) -> Color {
        switch ratio.state {
        case .unobserved: .secondary
        case .complete: .green
        case .incomplete: .orange
        }
    }

    @ViewBuilder
    private var deliveryDistributionSection: some View {
        if let summary = model.deliverySummary {
            let distribution = DeliveryDistributionRecord(summary: summary)
            LazyVGrid(
                columns: [GridItem(.adaptive(minimum: 300), spacing: 12)],
                alignment: .leading,
                spacing: 12
            ) {
                DeliveryStateDistributionPanel(distribution: distribution)
                DeliveryKindDistributionPanel(kinds: distribution.kinds)
            }
        }
    }

    private var inspectorSection: some View {
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
    }

    private func highRiskSection(_ scenario: ScenarioRecord) -> some View {
            VStack(alignment: .leading, spacing: 10) {
                Text(S.Risk.hostConfirmNote)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                // Repair moved to the mission bar header (one canonical
                // place). Delete opens the one shared `DestroyPanel` — the
                // same component the room board's row menu and the mission
                // bar's "…" menu open; it loads its own preview rather than
                // this section keeping a second, possibly-stale copy.
                Button(S.Rooms.deleteMenu, role: .destructive) {
                    destroyPanelTarget = DestroyPanelTarget(scenario: scenario)
                }
                .controlSize(.small)
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
            }
            .padding(.vertical, 6)
    }

    // MARK: - Workspace preparation rows

    private var workspaceProgressRows: some View {
        let failed = model.workspaceProgressHasFailure
        return ScrollView {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(model.workspaceProgress) { component in
                    HStack(spacing: 8) {
                        progressGlyph(component.state, afterFailure: failed)
                        Text(
                            component.kind == "environment"
                                ? S.Prepare.environmentRow
                                : component.componentID
                        )
                        .font(.caption)
                        .lineLimit(1)
                        Spacer()
                        Text(S.Prepare.rowState(component.state, afterFailure: failed))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
        .frame(maxHeight: 180)
    }

    @ViewBuilder
    private func progressGlyph(_ state: String, afterFailure: Bool) -> some View {
        switch state {
        case "ready":
            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case "failed":
            Image(systemName: "xmark.circle.fill").foregroundStyle(.red)
        case "cloning", "building":
            ProgressView().controlSize(.small)
        default:
            Image(systemName: "circle.dotted")
                .foregroundStyle(afterFailure ? .quaternary : .secondary)
        }
    }

    // MARK: - Health card (durable degraded state, always visible)

    @ViewBuilder
    private func healthCard(_ scenario: ScenarioRecord) -> some View {
        if ["degraded", "provision_failed"].contains(scenario.observedState) {
            GroupBox {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "cross.case.fill")
                        .foregroundStyle(.orange)
                        .font(.title3)
                    VStack(alignment: .leading, spacing: 6) {
                        Text(
                            S.Sections.healthNeedsRepair(
                                HarnessViewModel.humanState(scenario.observedState)
                            )
                        )
                        .font(.callout)
                        // Repair itself now lives once, in the mission bar's
                        // header row — this card keeps the reassurance
                        // sentence plus the one action that isn't a
                        // duplicate of it.
                        HStack {
                            Button(S.Preflight.runButton) {
                                Task { await model.runPreflight() }
                            }
                            .controlSize(.small)
                        }
                    }
                    Spacer()
                }
                .padding(6)
            } label: {
                Label(S.Sections.health, systemImage: "heart.text.square")
                    .font(.headline)
                    .foregroundStyle(.orange)
            }
        }
    }

    // MARK: - Resources (read-only overview)

    private var resourcesSection: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 6) {
                if model.visibleResources.isEmpty {
                    Text(S.Sections.noResources)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.vertical, 6)
                } else {
                    // Released history stays in Technical Details; the
                    // overview never claims a released lease is still held.
                    ForEach(model.visibleResources) { resource in
                        HStack {
                            StateBadge(state: resource.status)
                            Text(
                                S.Sections.resourceRow(
                                    resource.resourceClass, resource.participantID
                                )
                            )
                            .font(.callout)
                            Spacer()
                            Text(String(resource.id.prefix(12)))
                                .font(.caption.monospaced())
                                .foregroundStyle(.tertiary)
                        }
                    }
                }
            }
            .padding(6)
        } label: {
            Label(S.Sections.resources, systemImage: "cpu")
                .font(.headline)
        }
    }

    // MARK: - Technical fold (machine view, complete and collapsed)

    @State private var showTechnical = false

    /// The "Evidence & Diagnostics" drawer (review 20260903-175908-nyr2wy):
    /// deliveries, Preflight, window topology, collaboration policy,
    /// resources, the raw inspector and high-risk actions used to each be
    /// separate top-level disclosures with equal visual weight — now they are
    /// one collapsed-by-default group. Every section's own body is untouched;
    /// only where it is called from changed. Collapsing this never happens
    /// automatically on a fault — see `needsAttentionSection`'s links, which
    /// are the only things that open it (review 20260903-181141-6gjonu
    /// point 7).
    private func technicalSection(_ scenario: ScenarioRecord) -> some View {
        DisclosureGroup(isExpanded: $showTechnical) {
            HStack(alignment: .top, spacing: 12) {
                evidenceNav
                Divider()
                Group {
                    switch evidenceTab {
                    case .deliveries: deliveriesSection
                    case .preflight: preflightSection
                    case .topology: topologySection
                    case .policy: policySection
                    case .resources: resourcesSection
                    case .inspector: inspectorSection
                    case .highRisk: highRiskSection(scenario)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.top, 8)
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                Label(S.Sections.evidenceAndDiagnostics, systemImage: "terminal")
                    .font(.headline)
                    .foregroundStyle(.secondary)
                if !showTechnical, !evidenceSummaryLine.isEmpty {
                    Text(evidenceSummaryLine)
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
            }
        }
        .padding(10)
        .background(.secondary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
    }

    /// A fixed-width left nav, not a horizontally-scrolling strip — review
    /// 20260903-185641-e6nznb P2: a scrolling row can hide the high-risk tab
    /// off the visible edge with no indicator on a narrow window, and hand-
    /// rolled capsules skip native selected/keyboard semantics. All seven
    /// domains are always fully visible at once here, same fixed column
    /// regardless of window width, high-risk included — nothing to scroll to
    /// discover it.
    private var evidenceNav: some View {
        VStack(alignment: .leading, spacing: 1) {
            evidenceNavRow(.deliveries, S.Deliveries.rawActivity, "envelope.fill")
            evidenceNavRow(.preflight, S.Preflight.sectionTitle, "checkmark.shield")
            evidenceNavRow(.topology, S.Topology.sectionTitle, "macwindow.on.rectangle")
            evidenceNavRow(.policy, S.Policy.sectionTitle, "shared.with.you")
            evidenceNavRow(.resources, S.Sections.resources, "cpu")
            evidenceNavRow(.inspector, S.Inspector.sectionTitle, "terminal")
            evidenceNavRow(.highRisk, S.Risk.sectionTitle, "exclamationmark.triangle", tint: .red)
        }
        .frame(width: 140, alignment: .leading)
    }

    private func evidenceNavRow(
        _ tab: EvidenceTab, _ title: String, _ symbol: String, tint: Color = .accentColor
    ) -> some View {
        let selected = evidenceTab == tab
        return Button {
            evidenceTab = tab
        } label: {
            Label(title, systemImage: symbol)
                .font(.caption.weight(selected ? .bold : .regular))
                .foregroundStyle(selected ? tint : Color.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 8)
                .padding(.vertical, 6)
                .background(
                    selected ? tint.opacity(0.12) : Color.clear,
                    in: RoundedRectangle(cornerRadius: 6)
                )
        }
        .buttonStyle(.plain)
    }

    /// One line, shown only while the drawer is collapsed, composed from
    /// fragments its own sections already localize (same " · " idiom
    /// `scenarioHeader` and `highRiskSection` already use) — not a new
    /// hardcoded sentence.
    private var evidenceSummaryLine: String {
        var parts: [String] = []
        if let preflight = model.preflight {
            parts.append(
                "\(S.Preflight.sectionTitle) \(HarnessViewModel.humanState(preflight.status))"
            )
        }
        if let policy = model.policyStatus {
            parts.append(
                "\(S.Policy.sectionTitle) "
                    + S.Status.label(policy.requiresReplan ? "re-plan required" : "current")
            )
        }
        if let total = model.deliverySummary?.total, total > 0 {
            parts.append("\(S.Deliveries.rawActivity) \(total)")
        }
        if !model.visibleResources.isEmpty {
            parts.append("\(S.Sections.resources) \(model.visibleResources.count)")
        }
        return parts.joined(separator: " · ")
    }

    // MARK: - Overlays

    @ViewBuilder
    private var errorBanner: some View {
        if let error = model.actionableError {
            let sentence = S.Fix.sentence(error.code)
                ?? S.Fix.categoryFallback(error.category)
            let actionable = error.repairAction != nil
            HStack(alignment: .top, spacing: 10) {
                Image(
                    systemName: actionable
                        ? "wrench.and.screwdriver.fill"
                        : "exclamationmark.octagon.fill"
                )
                .foregroundStyle(actionable ? Color.orange : Color.red)
                .font(.title3)
                VStack(alignment: .leading, spacing: 4) {
                    Text(sentence).font(.callout.bold())
                    if let action = error.repairAction {
                        HStack {
                            Text(S.Banner.recommended(model.repairActionLabel(action)))
                                .font(.caption)
                            if action == "scenario.repair" {
                                Button(S.Banner.reviewRepair) { highRiskIntent = .repairScenario }
                                    .controlSize(.small)
                            } else if let instruction = model.textOnlyRepairAction(action) {
                                Text(instruction)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            } else if let performable = model.performableRepairAction(error) {
                                Button(model.repairActionLabel(performable)) {
                                    Task { await model.performRepairAction(performable) }
                                }
                                .controlSize(.small)
                            }
                        }
                    }
                    DisclosureGroup(S.Common.details) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(error.message)
                                .font(.caption)
                                .textSelection(.enabled)
                            Text(
                                S.Banner.machineLine(
                                    error.code, error.category, error.mutationState,
                                    retryable: error.retryable
                                )
                            )
                            .font(.caption.monospaced())
                            .textSelection(.enabled)
                        }
                        .padding(.top, 2)
                    }
                    .font(.caption)
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
            .frame(maxWidth: 560)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10))
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(
                        actionable ? Color.orange.opacity(0.5) : Color.red.opacity(0.5)
                    )
            )
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
                if !model.workspaceProgress.isEmpty {
                    workspaceProgressRows
                } else if let progress = model.operationProgressText {
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

private struct DeliveryStateDistributionPanel: View {
    let distribution: DeliveryDistributionRecord

    private var segments: [(title: String, count: Int, color: Color, onColor: Color)] {
        distribution.finalStates.map { value in
            switch value.category {
            case .consumptionAcknowledged:
                (
                    S.DeliveryDistribution.consumptionAcknowledged,
                    value.count, .evidenceStrong, .onEvidenceStrong
                )
            case .repliedWithoutConsumptionAck:
                (
                    S.DeliveryDistribution.repliedWithoutConsumptionAck,
                    value.count, .evidenceSoft, .onEvidenceSoft
                )
            case .noConsumptionAckOrReply:
                (
                    S.DeliveryDistribution.noConsumptionAckOrReply,
                    value.count, .evidenceAbsent, .onEvidenceAbsent
                )
            case .recipientDeleted:
                (S.DeliveryDistribution.recipientDeleted, value.count, .gray, .white)
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(S.DeliveryDistribution.finalState)
                .font(.subheadline.weight(.semibold))
            if distribution.settledTotal == 0 {
                RoundedRectangle(cornerRadius: 4)
                    .fill(.quaternary)
                    .frame(height: 28)
                Text(S.DeliveryDistribution.noSettled)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                GeometryReader { proxy in
                    HStack(spacing: 0) {
                        ForEach(Array(segments.enumerated()), id: \.offset) { _, segment in
                            let width = proxy.size.width
                                * CGFloat(segment.count)
                                / CGFloat(distribution.settledTotal)
                            ZStack {
                                Rectangle().fill(segment.color)
                                if width >= 30 {
                                    Text(String(segment.count))
                                        .font(.caption.weight(.semibold))
                                        .monospacedDigit()
                                        .foregroundStyle(segment.onColor)
                                }
                            }
                            .frame(width: width)
                        }
                    }
                    .clipShape(RoundedRectangle(cornerRadius: 4))
                }
                .frame(height: 28)
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(Array(segments.enumerated()), id: \.offset) { _, segment in
                        HStack(spacing: 8) {
                            RoundedRectangle(cornerRadius: 2)
                                .fill(segment.color)
                                .frame(width: 10, height: 10)
                            Text(segment.title)
                                .font(.caption)
                            Spacer(minLength: 8)
                            Text(String(segment.count))
                                .font(.caption.weight(.semibold))
                                .monospacedDigit()
                        }
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, minHeight: 220, alignment: .topLeading)
        .padding(12)
        .background(.secondary.opacity(0.05), in: RoundedRectangle(cornerRadius: 6))
        .accessibilityElement(children: .contain)
    }
}

private struct DeliveryKindDistributionPanel: View {
    let kinds: [DeliveryKindCount]

    private var maximumCount: Int {
        kinds.map(\.count).max() ?? 0
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(S.DeliveryDistribution.messageKind)
                .font(.subheadline.weight(.semibold))
            ForEach(kinds) { kind in
                // A message with no declared kind is called out as an absence,
                // in the same ochre this screen already uses for "settled
                // without evidence". It used to be painted `.green`, which is
                // this screen's healthy colour everywhere else — so the one row
                // singled out as a quality signal read as the good one. Raw
                // system colours also bypass the theme-aware instrument palette
                // the rest of the chart is drawn from.
                let generic = kind.id == "collaboration.message"
                HStack(spacing: 8) {
                    Text(S.DeliveryDistribution.kind(kind.id))
                        .font(.caption.weight(generic ? .semibold : .regular))
                        .foregroundStyle(generic ? Color.evidenceAbsent : .secondary)
                        .frame(width: 104, alignment: .trailing)
                        .lineLimit(1)
                    GeometryReader { proxy in
                        let fraction = maximumCount > 0
                            ? CGFloat(kind.count) / CGFloat(maximumCount)
                            : 0
                        ZStack(alignment: .leading) {
                            RoundedRectangle(cornerRadius: 3)
                                .fill(.quaternary)
                            RoundedRectangle(cornerRadius: 3)
                                .fill(generic ? Color.evidenceAbsent : Color.evidenceStrong)
                                .frame(width: proxy.size.width * fraction)
                        }
                    }
                    .frame(height: 8)
                    Text(String(kind.count))
                        .font(.caption.weight(.semibold))
                        .monospacedDigit()
                        .foregroundStyle(generic ? Color.evidenceAbsent : .primary)
                        .frame(width: 28, alignment: .trailing)
                }
                .frame(minHeight: 18)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(S.DeliveryDistribution.kind(kind.id))
                .accessibilityValue(String(kind.count))
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, minHeight: 220, alignment: .topLeading)
        .padding(12)
        .background(.secondary.opacity(0.05), in: RoundedRectangle(cornerRadius: 6))
    }
}

private struct CollaborationHealthMetricTile: View {
    let title: String
    let value: Int
    let total: Int?
    let color: Color

    private var accessibilityValue: String {
        guard let total else { return String(value) }
        return "\(value)/\(total)"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(String(value))
                    .font(.system(size: 26, weight: .semibold))
                    .monospacedDigit()
                    .foregroundStyle(color)
                if let total {
                    Text("/\(total)")
                        .font(.system(size: 15, weight: .regular))
                        .monospacedDigit()
                        .foregroundStyle(.tertiary)
                }
            }
            HStack(spacing: 5) {
                Circle()
                    .fill(color)
                    .frame(width: 6, height: 6)
                Text(title)
                    .font(.system(size: 10, weight: .medium))
                    .textCase(.uppercase)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 66, alignment: .leading)
        .padding(10)
        .background(.secondary.opacity(0.06), in: RoundedRectangle(cornerRadius: 6))
        .overlay {
            RoundedRectangle(cornerRadius: 6)
                .stroke(color.opacity(0.24), lineWidth: 1)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(title)
        .accessibilityValue(accessibilityValue)
    }
}

private struct StateBadge: View {
    let state: String

    private var color: Color {
        switch state {
        case "ready", "running", "delivered", "consumed", "current", "passed",
             "available", "granted", "active":
            .green
        case "stopped", "detached", "not_requested", "released":
            .gray
        case "starting", "stopping", "recovering", "repairing", "replacing",
             "destroying", "queued", "pending":
            .blue
        case "degraded", "re-plan required", "blocked", "not_determined", "stale":
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

/// The entity-aware replacement for `StateBadge` on Scenario and Participant
/// rows (room board, mission bar, team roster). Unlike `StateBadge`'s single
/// global string table, the six-class `PresentationClass` is computed per
/// entity (`ScenarioRecord.presentationClass` / `ParticipantRecord.
/// presentationClass`) — the same raw token can land in a different class on
/// a different entity, e.g. Participant `ready` is `working`, never
/// `success`. The label text is still always the entity's own existing
/// `humanState`/`S.Status` word; only colour and icon come from the class.
private struct PresentationBadge: View {
    let cls: PresentationClass
    let label: String

    var body: some View {
        Label {
            Text(label)
        } icon: {
            Image(systemName: cls.symbolName)
        }
        .font(.caption.bold())
        .labelStyle(.titleAndIcon)
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .foregroundStyle(cls.color)
        .background(cls.color.opacity(0.12), in: Capsule())
    }
}

// MARK: - ScenarioRoomCard

private struct ScenarioRoomCard: View {
    let scenario: ScenarioRecord

    /// Entity-aware (`ScenarioRecord.presentationClass`), not a local re-guess
    /// of the same five-way split `StateBadge` already does globally.
    /// Not `presentationClass == .working`: that class also covers
    /// provisioning/opening/closing/destroying/repairing, and the live pulse
    /// must mean exactly "running" — a closing or destroying room pulsing
    /// green reads as a lie next to its own orange/blue badge (review
    /// 20260903-183736-clqu6r P1-3). Those transitional states are already
    /// carried by the badge; the dot adds nothing by also claiming them.
    private var isLive: Bool {
        scenario.observedState == "running"
    }

    private var needsAttention: Bool {
        [.attention, .failed].contains(scenario.presentationClass)
    }

    private var statusSummary: String {
        let count = scenario.participantIDs.count
        switch scenario.observedState {
        case "running":
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
                .fill(scenario.presentationClass.color)
                .frame(width: 4)
                .padding(.vertical, 2)
                .opacity(needsAttention ? 1.0 : 1.0)

            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Text(scenario.id)
                        .font(.system(.body, weight: .semibold))
                        .lineLimit(1)
                    Spacer()
                    PresentationBadge(
                        cls: scenario.presentationClass,
                        label: HarnessViewModel.humanState(scenario.observedState)
                    )
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

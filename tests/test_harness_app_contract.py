# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from ai_collab.protocol import OPERATION_BY_ID, OPERATION_REGISTRY_DIGEST


ROOT = Path(__file__).resolve().parents[1]
SWIFT_CONTRACT = (
    ROOT
    / "macos"
    / "AI-Collab"
    / "App"
    / "HarnessContract.generated.swift"
)
APP_ROOT = ROOT / "macos" / "AI-Collab" / "App"
APP_PROJECT = ROOT / "macos" / "AI-Collab" / "project.yml"
HOST_AGENT = ROOT / "macos" / "AI-Collab" / "HostAgent" / "main.swift"
LAUNCH_AGENT = (
    ROOT
    / "macos"
    / "AI-Collab"
    / "LaunchAgents"
    / "com.atomgradient.aicollab.host.plist"
)


def test_swift_client_binding_matches_python_operation_registry() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate_harness_swift_contract.py"), "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    source = SWIFT_CONTRACT.read_text(encoding="utf-8")
    digest = re.search(r'operationRegistryDigest = "([0-9a-f]{64})"', source)
    assert digest is not None
    assert digest.group(1) == OPERATION_REGISTRY_DIGEST
    declared = dict(re.findall(r'"([a-z.-]+)": "([a-z.]+)"', source))
    assert declared
    assert declared == {
        operation: OPERATION_BY_ID[operation]["required_capability"]
        for operation in declared
    }


def test_every_app_invoked_operation_is_registered_in_the_capability_map() -> None:
    """The App computes capability proofs from HarnessContract.capabilities.

    An operation the Swift sources invoke but the generator's APP_OPERATIONS
    allowlist omits fails at runtime only (HarnessIPC throws before the call
    reaches the Host) — exactly the silent gap that shipped the v0.1.5
    permission buttons dead. Catch it at test time instead.
    """

    invoked: set[str] = set()
    for path in sorted(APP_ROOT.glob("*.swift")):
        invoked.update(
            re.findall(
                r'operation:\s*"([a-z][a-z0-9.-]*)"',
                path.read_text(encoding="utf-8"),
            )
        )
    assert invoked, "no operation literals found — scan regex is broken"
    contract_source = SWIFT_CONTRACT.read_text(encoding="utf-8")
    declared = set(re.findall(r'"([a-z.-]+)": "[a-z.-]+",', contract_source))
    missing = sorted(invoked - declared)
    assert not missing, (
        "App-invoked operations missing from generator APP_OPERATIONS: "
        + ", ".join(missing)
    )


def test_app_is_vendor_neutral_and_does_not_shell_out() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(APP_ROOT.glob("*.swift"))
    )
    assert "Codex" not in source
    assert "Claude" not in source
    assert "NSTask" not in source
    assert "Process()" not in source
    assert "canonical_project_path" not in source.replace(
        '"canonical_project_path": url.path', ""
    )


def test_app_exposes_scenario_resume_and_participant_reports() -> None:
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    assert 'Button(S.Detail.resume)' in content
    assert 'InspectorText(title: S.Inspector.resume, text: model.resumeText)' in content
    assert 'operation: "scenario.open"' in view_model
    assert 'result["resume_summary"]' in view_model


def test_app_live_refreshes_selected_scenario_deliveries_without_full_page_polling() -> None:
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    assert ".task(id: scenario.id)" in content
    assert "await model.monitorDeliveries(for: scenario.id)" in content
    assert "func monitorDeliveries(for scenarioID: String) async" in view_model
    monitor = view_model.split(
        "func monitorDeliveries(for scenarioID: String) async", 1
    )[1].split("func retryDelivery", 1)[0]
    assert "fetchDeliveries(" in monitor
    assert "refreshSelectedScenarioValues" not in monitor
    assert "Task.sleep(nanoseconds: 2_000_000_000)" in monitor


def test_app_exposes_participant_replace_without_detach() -> None:
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    # Asserts the affordance, not the control that renders it: Replace has been a
    # plain button and is now a template submenu, and either satisfies the
    # guarantee. Naming the call also pins the explicit-template signature, so a
    # regression back to reading the unrelated "add participant" picker fails here.
    assert "model.replaceParticipant(" in content
    # The template is a parameter, so Replace cannot silently rebuild a
    # participant from whichever template the unrelated "add" picker was showing.
    assert "template: ParticipantTemplate" in view_model
    assert 'operation: "participant.replace"' in view_model
    assert '"launch_spec": template.launchSpec' in view_model
    # Detach ended a participant permanently while offering nothing stop does
    # not, so the App must not surface it again.
    assert 'Button("Detach")' not in content
    assert "participant.detach" not in view_model


def test_app_requires_explicit_confirmation_before_recreate_handoff() -> None:
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    models = (APP_ROOT / "HarnessModels.swift").read_text(encoding="utf-8")
    assert 'Button(S.Colleagues.recreateHandoff)' in content
    assert "case recreateParticipantWithHandoff(ParticipantRecord)" in content
    strings = (APP_ROOT / "Strings.swift").read_text(encoding="utf-8")
    assert "the previous AI conversation is not restored" in strings
    assert "func recreateParticipantWithHandoff" in view_model
    assert 'launchSpec["continuity_mode"] = "explicit_recreate"' in view_model
    assert 'launchSpec["continuity_binding_ref"] = NSNull()' in view_model
    assert "var canRecreateWithHandoff" in models


def test_app_exposes_scenario_focus_and_topology_without_vendor_logic() -> None:
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    models = (APP_ROOT / "HarnessModels.swift").read_text(encoding="utf-8")
    ipc = (APP_ROOT / "HarnessIPC.swift").read_text(encoding="utf-8")
    # The section must exist and be labelled; whether it renders as a GroupBox or
    # a collapsible section is layout, not contract.
    assert 'Label(S.Topology.sectionTitle' in content
    assert 'Button(S.Topology.focusRestore)' in content
    assert 'operation: "scenario.topology"' in view_model
    assert 'operation: "scenario.focus"' in view_model
    assert "func cancelActiveOperation() async" in view_model
    assert "func cancelOperation(_ operationID: String)" in ipc
    assert "struct HarnessProgress" in ipc
    assert 'Button(S.Banner.cancelSafely)' in content
    assert "struct ScenarioTopologyRecord" in models
    assert "struct PresentationTopologyRecord" in models


def test_app_exposes_degraded_and_high_risk_repair_actions() -> None:
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    models = (APP_ROOT / "HarnessModels.swift").read_text(encoding="utf-8")
    generator = (ROOT / "scripts" / "generate_harness_swift_contract.py").read_text(
        encoding="utf-8"
    )
    for operation in ("scenario.repair", "participant.force-stop", "resource.break"):
        assert f'operation: "{operation}"' in view_model
        assert f'"{operation}"' in generator
    assert 'Button(S.Risk.repairScenario)' in content
    assert 'Button(S.Colleagues.forceStop, role: .destructive)' in content
    assert 'Button(S.Risk.breakLease, role: .destructive)' in content
    strings = (APP_ROOT / "Strings.swift").read_text(encoding="utf-8")
    assert "Continue to Host confirmation" in strings
    assert "participant_fault" in strings
    assert "AI colleague needs recovery" in strings
    assert "humanDegradedReason" in content
    assert 'action == "scenario.force-destroy"' in content
    assert ".forceDestroyScenario(scenario)" in content
    assert "Force Delete Task Room" in strings
    assert r"participants.filter(\.canRecover).count >= 1" in view_model
    assert "cleanupPending" in models
    assert "ResourceLeaseRecord" in models


def test_app_gives_high_risk_confirmation_operations_long_timeout() -> None:
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    for operation in ("scenario.destroy", "scenario.force-destroy", "resource.break"):
        assert re.search(
            rf'operation: "{re.escape(operation)}".*?responseTimeoutSeconds: 360',
            view_model,
            re.S,
        ), operation
    assert re.search(
        r'operation: "participant.force-stop".*?responseTimeoutSeconds: 360',
        view_model,
        re.S,
    )
    assert "responseTimeoutSeconds: Int = 480" in view_model
    for operation in ("participant.start", "participant.replace"):
        assert f'operation: "{operation}"' in view_model


def test_iterm_python_api_setup_runs_detached_before_quitting_iterm() -> None:
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    strings = (APP_ROOT / "Strings.swift").read_text(encoding="utf-8")
    command = view_model.split("let command = \"\"\"", 1)[1].split("\"\"\"", 1)[0]
    first_line = command.strip().splitlines()[0]
    assert first_line.startswith(
        "/usr/bin/nohup /bin/zsh <<'AICOLLAB_ENABLE_ITERM_API'"
    )
    assert first_line.endswith(">/dev/null 2>&1 &")
    quit_index = command.index("tell application id \"com.googlecode.iterm2\" to quit")
    write_index = command.index("defaults write com.googlecode.iterm2 EnableAPIServer")
    open_index = command.index("open -b com.googlecode.iterm2")
    assert quit_index < write_index < open_index
    action_branch = view_model.split(
        'case "iterm-presentation.enable-python-api":', 1
    )[1].split('case "iterm-presentation.restart-after-python-api"', 1)[0]
    assert "copyItermPythonAPISetupCommand()" in action_branch
    assert "openIterm2()" not in action_branch
    assert "Terminal.app" in strings


def test_app_exposes_single_entry_context_menu_force_destroy() -> None:
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    assert '.contextMenu {' in content
    assert 'Button(S.Rooms.forceDelete, role: .destructive)' in content
    assert "case forceDestroyScenario(ScenarioRecord)" in content
    strings = (APP_ROOT / "Strings.swift").read_text(encoding="utf-8")
    assert "registered project source is never deleted" in strings
    assert 'operation: "scenario.force-destroy"' in view_model
    assert "func forceDestroyScenario(_ scenario: ScenarioRecord)" in view_model


def test_app_exposes_preflight_and_structured_actionable_errors() -> None:
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    models = (APP_ROOT / "HarnessModels.swift").read_text(encoding="utf-8")
    ipc = (APP_ROOT / "HarnessIPC.swift").read_text(encoding="utf-8")
    assert 'operation: "scenario.preflight"' in view_model
    assert 'Button(S.Preflight.runButton)' in content
    assert "ScenarioPreflightRecord" in models
    assert "repairAction" in models
    assert "mutationState" in ipc
    assert "category" in ipc


def test_app_surfaces_presentation_permission_remediation_actions() -> None:
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    strings = (APP_ROOT / "Strings.swift").read_text(encoding="utf-8")
    assert "permission.remediationRef" in content
    assert "model.repairActionDetail(remediation)" in content
    assert "await model.performRepairAction(remediation)" in content
    for remediation in (
        "iterm-presentation.enable-python-api",
        "iterm-presentation.restart-after-python-api",
        "iterm-presentation.reset-private-api-socket",
    ):
        assert remediation in view_model
        assert remediation in strings
    assert "Settings -> General -> Magic" in strings
    assert "auth.confirmation-timeout" in strings


def test_app_surfaces_destroy_preview_blockers_and_force_delete_escape_hatch() -> None:
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    strings = (APP_ROOT / "Strings.swift").read_text(encoding="utf-8")
    assert "destroyPreviewBlockers" in view_model
    assert "destroyPreviewBlocked" in view_model
    assert 'preview["blockers"] as? [String]' in view_model
    assert "S.Msg.destroyPreviewBlocked(self.destroyPreviewBlockers)" in view_model
    assert "model.destroyPreviewBlocked" in content
    assert "S.Risk.destroyPreviewBlocked(model.destroyPreviewBlockers)" in content
    assert ".forceDestroyScenario(scenario)" in content
    assert "Destroy preview is blocked" in strings
    assert "The destroy preview is blocked" in strings


def test_app_embeds_separate_current_user_host_agent_contract() -> None:
    project = APP_PROJECT.read_text(encoding="utf-8")
    helper = HOST_AGENT.read_text(encoding="utf-8")
    launch_agent = LAUNCH_AGENT.read_text(encoding="utf-8")
    assert "AICollabHostAgent" in project
    assert "Contents/Library/LaunchServices" in project
    assert "Contents/Library/LaunchAgents" in project
    assert "ai_collab.service" in helper
    assert "execv(" in helper
    assert "BundleProgram" in launch_agent
    assert "Contents/Library/LaunchServices/AICollabHostAgent" in launch_agent
    assert "<key>RunAtLoad</key>" in launch_agent
    assert "<key>KeepAlive</key>" in launch_agent
    assert "root" not in launch_agent.lower()


def test_host_agent_preserves_user_path_when_launched_by_service_management() -> None:
    helper = HOST_AGENT.read_text(encoding="utf-8")
    assert 'ProcessInfo.processInfo.environment["PATH"]' in helper
    assert "userPaths + inheritedPaths" in helper
    assert 'setenv("PATH", searchPaths.joined(separator: ":"), 1)' in helper
    assert 'setenv("PATH", userPaths.joined(separator: ":"), 0)' not in helper


def test_host_agent_resolves_user_supplied_adapter_configs() -> None:
    """The agent looks in Application Support first and treats the project and
    security adapter configs as optional, so a build without an embedded
    integration payload still starts the Host and a user-supplied config can
    replace a bundled one without touching the signed bundle."""
    helper = HOST_AGENT.read_text(encoding="utf-8")
    assert "Application Support/AI Collab" in helper
    assert "resolvedConfiguration" in helper
    assert "if let adapter" in helper
    assert "if let security" in helper
    # The participant driver ships in every build; its absence still means the
    # bundle itself is broken.
    assert "guard let participant" in helper


def test_app_reregisters_changed_embedded_service() -> None:
    controller = (APP_ROOT / "HarnessServiceController.swift").read_text(
        encoding="utf-8"
    )
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    assert "AICollabServiceBuildDigest" in controller
    assert "service-registration.json" in controller
    assert "registrationMatches(buildDigest: buildDigest)" in controller
    assert 'value["app_bundle_path"]' in controller
    assert "try await service.unregister()" in controller
    assert "try service.register()" in controller
    assert "try await serviceController.ensureRegistered()" in view_model
    app = (APP_ROOT / "AICollabApp.swift").read_text(encoding="utf-8")
    assert "--unregister-host-service" in app
    assert "try await HarnessServiceController().unregister()" in app


def test_app_offers_colleague_deletion_only_for_stopped_with_confirmation() -> None:
    """R1 presentation pins: stopped-only affordance, approved copy, exact
    operation, and the explicit confirmed flag must survive later re-layouts."""

    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    strings = (APP_ROOT / "Strings.swift").read_text(encoding="utf-8")
    assert 'if state == "stopped" {' in content
    assert "Button(S.Colleagues.deleteMenu, role: .destructive)" in content
    assert "pendingDeletion = participant" in content
    assert "Button(S.Common.delete, role: .destructive)" in content
    assert 'guard participant.observedState == "stopped"' in view_model
    assert 'operation: "participant.destroy"' in view_model
    assert '"confirmed": true' in view_model
    assert "will disappear from this room" in strings
    assert "将从这个房间消失" in strings
    assert "正在工作的成员需要先停止" in strings


def test_prepare_workspace_wires_the_progress_session() -> None:
    """R7: the real provision call must carry the session-bound progress
    callback, and rows must reset at every mutation start."""

    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    prepare = view_model.split("func prepareWorkspace", 1)[1].split("func ", 1)[0]
    assert "let progressSessionID = UUID()" in view_model.split(
        "func prepareWorkspace", 1
    )[1][:400]
    assert 'operation: "workspace.provision"' in prepare
    assert "progress: { progress in" in prepare
    assert "self.applyProgress(progress, progressSessionID: progressSessionID)" in prepare
    mutation = view_model.split("private func performMutation", 1)[1].split("func ", 1)[0]
    assert "workspaceProgress = []" in mutation


def test_degraded_room_shows_a_visible_repair_entry() -> None:
    """R6: a durable degraded/provision_failed room must offer repair in the
    always-visible Health card, not only inside the collapsed technical fold."""

    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    card = content.split("private func healthCard", 1)[1].split("private ", 1)[0]
    assert '"degraded", "provision_failed"' in card
    assert "Button(S.Risk.repairScenario)" in card
    assert "highRiskIntent = .repairScenario" in card
    assert "Button(S.Preflight.runButton)" in card
    detail = content.split("private var scenarioDetail", 1)[1].split("private ", 1)[0]
    assert "healthCard(scenario)" in detail
    assert "Label(S.Sections.health" in card
    assert "Label(S.Sections.activity" in content


def test_guide_is_a_dismissable_centered_card_deck() -> None:
    """User decision 2026-08-21: guidance is a centered card the employee
    steps through and can always close — never a persistent overlay bar."""

    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    assert ".safeAreaInset(edge: .top" not in content, "no top overlay bar layouts"
    assert "guidanceRail" not in content, "the rail is gone"
    deck = content.split("private var guideCard", 1)[1].split("// MARK: ", 1)[0]
    assert "Button(S.Guide.next)" in deck
    assert "Button(S.Guide.previous)" in deck
    assert "Button(S.Guide.done)" in deck
    assert "guideStep = nil" in deck, "close is always available"
    assert 'Button(S.Guide.reopenHelp, systemImage: "questionmark.circle")' in content
    assert 'AppStorage("AICollabGuideSeen")' in content, "first launch shows once"
    assert "model.guideStep" in deck, "open state lives on the model, surviving language switches"
    assert "@State private var guideStep" not in content
    assert "guideAction(at: index)" in deck, "actions flow through the gated presentation model"
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    presentation = view_model.split("func guidePresentation", 1)[1].split("private var completedMilestoneIndex", 1)[0]
    assert "case .attend, .working, .inconsistent:" in presentation
    assert "(completedMilestoneIndex, nil)" in presentation

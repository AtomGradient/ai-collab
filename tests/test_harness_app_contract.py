# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

from __future__ import annotations

import ast
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
STORE = ROOT / "src" / "ai_collab" / "store.py"
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
    # Starting the Host is the App's sole permitted external process.
    assert "Process()" not in source.replace(
        'let process = Process()\n        process.executableURL = URL(filePath: "/bin/launchctl")',
        "",
        1,
    )
    assert "canonical_project_path" not in source.replace(
        '"canonical_project_path": url.path', ""
    )


def test_app_exposes_scenario_resume_and_participant_reports() -> None:
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    # Resume moved out of the header and into the persistent flow section,
    # which offers exactly the one step whose Host precondition holds. The
    # contract is that the App still exposes resume from the UI, not that it
    # sits in any particular control.
    assert "await model.openScenario()" in content
    assert 'InspectorText(title: S.Inspector.resume, text: model.resumeText)' in content
    assert 'operation: "scenario.open"' in view_model
    assert 'result["resume_summary"]' in view_model


def test_stale_host_bundle_status_survives_successful_refreshes_and_mutations() -> None:
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    assert 'status["host_runtime_identity"]' in view_model
    assert "lstat(runtime.path, &runtimeDetails)" in view_model
    assert 'self.hostStatus = fresh ?' in view_model
    assert 'else { self.hostStatus = "stale-bundle"; return }' not in view_model
    assert view_model.count(
        'if hostStatus != "stale-bundle" { hostStatus = "ready" }'
    ) == 2



def test_app_live_refreshes_selected_scenario_deliveries_without_full_page_polling() -> None:
    """The room's live loop (v2 `monitorRoom`, formerly `monitorDeliveries`)
    polls deliveries every 2 seconds and, on the same tick, takes a read-only
    look at the room record and its participants — never the full
    `refreshSelectedScenarioValues` (preflight, topology, diagnostic,
    resources, policy) which is the explicit-refresh path."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    assert ".task(id: scenario.id)" in content
    assert "await model.monitorRoom(for: scenario.id)" in content
    assert "func monitorRoom(for scenarioID: String) async" in view_model
    monitor = view_model.split(
        "func monitorRoom(for scenarioID: String) async", 1
    )[1].split("private func observeRoom", 1)[0]
    assert "fetchDeliveries(" in monitor
    assert "refreshSelectedScenarioValues" not in monitor
    assert "Task.sleep(nanoseconds: 2_000_000_000)" in monitor
    # The participant/room read is skipped while a mutation is in flight so
    # it cannot race the post-mutation refresh.
    assert "if !isBusy {" in monitor
    assert "observeRoom(project: project, scenarioID: scenarioID)" in monitor



def test_harness_app_raw_activity_has_no_pagination_callpoints() -> None:
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    models = (APP_ROOT / "HarnessModels.swift").read_text(encoding="utf-8")
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")

    assert '["limit": DeliveryCollectionRecord.rawActivityLimit]' in view_model
    # v2: the workbench's activity stream shows the 30 most recent
    # deliveries (Host accepts 1...256). Still exactly one page.
    assert "static let rawActivityLimit = 30" in models
    for removed in (
        "nextDeliveryPage",
        "loadMoreDeliveries",
        "DeliveryNextPage",
        "afterDeliveryID",
        "collectionDigest",
        "S.Deliveries.thread",
        "S.Deliveries.reply",
        "S.Deliveries.loadMore",
        ".padding(.leading, delivery.isThreadRoot",
    ):
        assert removed not in view_model + models + content


def test_app_offers_a_read_when_the_room_reads_as_inconsistent() -> None:
    """The one blocked state whose copy names re-reading must render it.

    `.inconsistent` used to produce no button while telling the user to
    "use the repair guidance below" — a section that can be empty until
    preflight is run. Refresh is a read, so it has no Host precondition to
    violate and does not weaken the rule that every offered lifecycle
    action is one the Host would accept.
    """
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    strings = (APP_ROOT / "Strings.swift").read_text(encoding="utf-8")

    assert "case .inconsistent:" in content
    assert "S.Guide.recheckAction" in content
    assert "await model.refreshSelectedScenario()" in content
    assert "case .attend, .working:" in content

    for stale in ("repair guidance below", "\u4e0b\u65b9\u7684\u4fee\u590d\u6307\u5f15"):
        assert stale not in strings


def test_message_kind_callout_uses_the_instrument_palette_not_healthy_green() -> None:
    """Green is this screen's healthy colour; the callout is not a compliment.

    `generic msg` is the one row deliberately emphasised as a quality
    signal. Drawing it in `.green` put it in the same colour as TEAM READY,
    DEGRADED 0 and the all-clear check, and bypassed the theme-aware
    instrument palette the rest of the chart uses.
    """
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")

    assert "generic ? Color.evidenceAbsent : Color.evidenceStrong" in content
    for healthy in (
        "generic ? .green :",
        "generic ? Color.green :",
    ):
        assert healthy not in content


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
    # The section must exist and be labelled; whether it renders as a GroupBox,
    # a collapsible section, or an Evidence & Diagnostics nav row is layout,
    # not contract.
    assert 'case .topology: S.Topology.sectionTitle' in content
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
    # DestroyPanel calls the ViewModel method directly now, not through a
    # HighRiskIntent case (review 20260903-191042-y57u0q P1).
    assert "await model.forceDestroyScenario(current)" in content
    assert 'case "scenario.open":' in view_model
    assert "await openScenario()" in view_model
    assert "Resume Task Room" in strings
    assert "participants.contains(where: \\.canRecover)" in view_model
    assert "cleanupPending" in models
    assert "ResourceLeaseRecord" in models


def _repair_action_literals(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value} if "." in node.value else set()
    if isinstance(node, ast.IfExp):
        return _repair_action_literals(node.body) | _repair_action_literals(node.orelse)
    if isinstance(node, ast.Dict):
        values: set[str] = set()
        for value in node.values:
            values.update(_repair_action_literals(value))
        return values
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
    ):
        values = _repair_action_literals(node.func.value)
        for default in node.args[1:]:
            values.update(_repair_action_literals(default))
        return values
    return set()


def test_durable_repair_actions_have_app_exits() -> None:
    tree = ast.parse(STORE.read_text(encoding="utf-8"))
    durable_actions: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "repair_action":
                durable_actions.update(_repair_action_literals(value))

    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    assert "model.textOnlyRepairAction(action)" in content
    covered = set(re.findall(r'case "([^"]+)":', view_model))
    covered.update(re.findall(r'action == "([^"]+)"', content))

    assert durable_actions
    assert durable_actions <= covered


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
    assert "responseTimeoutSeconds: Int = 360" in view_model
    for operation in ("participant.start", "participant.replace"):
        assert re.search(
            rf'operation: "{re.escape(operation)}".*?responseTimeoutSeconds: 480',
            view_model,
            re.S,
        ), operation


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


def test_app_exposes_context_menu_delete_through_the_destroy_panel() -> None:
    """Superseded by DestroyPanel (review 20260903-185641-e6nznb, then
    20260903-191042-y57u0q): the row context menu no longer force-destroys
    directly, and `HighRiskIntent` no longer carries a force-destroy case at
    all — the panel calls `model.forceDestroyScenario` itself once its own
    `.confirmationDialog` confirms. See
    test_destroy_flow_has_one_panel_entry_point_not_a_direct_force_delete and
    test_destroy_panel_force_confirmation_is_self_contained_not_cross_modal.
    This still pins that the row menu exists and that Force Delete, reached
    only via the panel now, still calls scenario.force-destroy underneath."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    assert '.contextMenu {' in content
    assert "struct DestroyPanel: View" in content
    assert "func forceDestroyScenario(_ scenario: ScenarioRecord)" in view_model
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
    # DestroyPanel branches on the primitive `destroyPreviewEligible` flag
    # directly rather than through the `destroyPreviewBlocked` convenience —
    # that computed property still exists in HarnessViewModel, just unused
    # from ContentView now.
    assert "model.destroyPreviewEligible" in content
    assert "S.Risk.destroyPreviewBlocked(model.destroyPreviewBlockers)" in content
    assert "await model.forceDestroyScenario(current)" in content
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
    assert 'setenv("PYTHONNOUSERSITE", "1", 1)' in helper


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
    """R6: a durable degraded/provision_failed room must offer repair from an
    always-visible surface, not only inside the collapsed technical fold.

    Phase 1 redesign (review 20260903-183736-clqu6r) moved the one canonical
    Repair control into the mission bar's own header row (folded directly
    into `missionBar` as of review 20260903-203219-kq79nn's compact-header
    pass — there is no separate `scenarioHeader` function anymore), which is
    unconditionally visible for every Scenario — a strictly more always-
    visible surface than the Health card, which only renders at all while
    degraded/provision_failed. Health card keeps the explanatory sentence
    and Run Preflight, not a second Repair button.
    """

    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    header = content.split("private func missionBar", 1)[1].split(
        "private func objectiveInline", 1
    )[0]
    assert '["provision_failed", "degraded"].contains(scenario.observedState)' in header
    assert "Button(S.Risk.repairScenario)" in header
    assert "highRiskIntent = .repairScenario" in header
    card = content.split("private func healthCard", 1)[1].split("private ", 1)[0]
    assert '"degraded", "provision_failed"' in card
    assert "Button(S.Risk.repairScenario)" not in card, (
        "repair must not be duplicated back into the Health card"
    )
    assert "Button(S.Preflight.runButton)" in card
    # v2: the alert row is rendered inside the mission bar (the Artifact's
    # mission-alert position), directly under the objective.
    assert "healthCard(scenario)" in header
    assert "S.Sections.health" in card
    assert "case .deliveries: S.Deliveries.rawActivity" in content


def test_needs_attention_delivery_link_opens_the_drawer_it_expands() -> None:
    """codex review 20260903-183736-clqu6r P1-2: the inner tab used to expand
    while the outer drawer disclosure stayed collapsed, so the click looked
    like it did nothing. Both must be set together."""

    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    attention = content.split("private var needsAttentionSection", 1)[1].split(
        "private ", 1
    )[0]
    assert "showTechnical = true" in attention
    assert "evidenceTab = .deliveries" in attention
    # The per-section disclosure bools P1-4 replaced must not come back.
    for removed_state in (
        "showPreflight",
        "showTopology",
        "showPolicy",
        "showDeliveries",
        "showInspector",
    ):
        assert removed_state not in content



def test_evidence_nav_is_a_fixed_column_not_a_scrolling_strip() -> None:
    """codex review 20260903-185641-e6nznb P2: every evidence domain, high-
    risk included, must be visible at once at any width — never a
    horizontally-scrolling strip. v2 keeps that guarantee with a native
    segmented `Picker` over `EvidenceTab.allCases` (icon-only, Xcode's
    inspector bar) instead of hand-drawn nav rows."""

    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    picker = content.split("private var evidenceDomainPicker", 1)[1].split(
        "\n    // MARK:", 1
    )[0]
    assert "ScrollView(.horizontal" not in picker
    assert "ForEach(EvidenceTab.allCases" in picker
    assert ".pickerStyle(.segmented)" in picker
    enum_body = content.split("private enum EvidenceTab", 1)[1].split("\n    }\n", 1)[0]
    assert "CaseIterable" in content.split("private enum EvidenceTab", 1)[1][:80]
    for tab in (
        "deliveries",
        "preflight",
        "topology",
        "policy",
        "resources",
        "inspector",
        "analytics",
        "highRisk",
    ):
        assert f"case .{tab}:" in content.split("private enum EvidenceTab", 1)[1].split(
            "@State private var evidenceTab", 1
        )[0], tab
    assert "evidenceNavRow" not in content, "hand-drawn nav rows replaced by the picker"



def test_workbench_is_two_column_team_primary_with_narrow_fallback() -> None:
    """v2 (design re-review 2026-09-04): the detail column is a native
    `List` (team first, then the activity stream) beside a 300pt progress
    column, chosen by the real available width — a `GeometryReader` as the
    detail root, reading width only — not `ViewThatFits`, whose decision
    depended on the children's ideal widths (≈771pt with the old evidence
    pane, so the 1100pt default window never got two columns). Below the
    width the progress groups fold into the List's last section, Team still
    first."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    detail = content.split("private var scenarioDetail", 1)[1].split(
        "@ToolbarContentBuilder", 1
    )[0]
    assert "GeometryReader { geo in" in detail
    assert "geo.size.width >= Self.twoColumnMinimumWidth" in detail
    assert "geo.size.height" not in detail, "width only — never a height estimate"
    assert "roomList(scenario, wide: wide)" in detail
    assert "progressColumn(scenario)" in detail
    assert "ViewThatFits(" not in content
    room_list = content.split("private func roomList", 1)[1].split(
        "\n    /// ", 1
    )[0]
    assert "List {" in room_list
    assert ".listStyle(.inset)" in room_list
    assert "ScrollView" not in room_list, "a List is the scrolling container, never nested in a ScrollView"
    assert room_list.index("teamSection") < room_list.index("activitySection")
    narrow = room_list.split("if !wide {", 1)[1]
    for group in ("stageTimeline(scenario)", "collaborationHealthSection(", "needsAttentionSection"):
        assert group in narrow, group



def test_progress_panel_merges_timeline_metrics_and_attention() -> None:
    """codex review 20260903-201119-r9tf2j: the secondary column is one
    unit — lifecycle stage, then the four facts, then the attention list.
    v2 renders it as `progressColumn` at its natural height (no card fill,
    no `maxHeight: .infinity` stretch), still one column with all three."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    column = content.split("private func progressColumn", 1)[1].split(
        "\n    /// review", 1
    )[0]
    assert "stageTimeline(scenario)" in column
    assert "collaborationHealthSection(" in column
    assert "needsAttentionSection" in column
    assert ".frame(width: 300)" in column
    assert "maxHeight: .infinity" not in column
    assert ".background(" not in column, "hairlines between groups, no card fill"


def test_current_stage_checks_workspace_and_staffing_evidence_before_closed() -> None:
    """codex review 20260903-203219-kq79nn P1: `observed_state == "closed"`
    alone marked a brand-new room (also closed through prepare-workspace and
    add-colleague, per GuidanceRailTests) as every stage complete. Evidence
    order must be workspace readiness, then whether any interactive
    colleague exists, and only then closed/closing — not a bare switch on
    `observedState` alone."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    fn = content.split("private func currentStage", 1)[1].split(
        "\n    private func stageTimeline", 1
    )[0]
    workspace_check = fn.index("model.workspaceEvidence == .present")
    staffing_check = fn.index("model.participants.contains(where: \\.isInteractive)")
    closed_check = fn.index('["closed", "closing"].contains(scenario.observedState)')
    assert workspace_check < staffing_check < closed_check, (
        "must check workspace evidence, then staffing, before ever reading closed as done"
    )


def test_project_row_has_no_nested_interactive_control() -> None:
    """codex review 20260903-203219-kq79nn P1: a `Button` inside another
    `Button`'s own label has ambiguous activation/accessibility semantics.
    The whole project row is a `Button` (selects the project); the
    reconciliation status line inside it must be plain text, with the apply
    action reachable only through the row's context menu."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    # Bounded by the row's own `.buttonStyle(.plain)`, which comes right
    # after the row Button's label closure and before its context menu.
    row = content.split("ForEach(model.projects)", 1)[1].split(".buttonStyle(.plain)", 1)[0]
    # Count actual construction sites — `Button(` (label-string form) or
    # `Button {` (trailing-closure form) — not just the word "Button", which
    # also appears in this same row's own explanatory comment prose.
    button_sites = len(re.findall(r"Button\(|Button \{", row))
    assert button_sites == 1, "the row itself must be the only Button before its context menu"
    menu = content.split(".buttonStyle(.plain)", 1)[1].split(".contextMenu {", 1)[1].split(
        "\n                }\n            }\n        }", 1
    )[0]
    assert "S.Projects.applyDetectedUpdate" in menu



def test_evidence_uses_primary_column_slack_with_a_pinned_bottom_disclosure() -> None:
    """v2 (design re-review 2026-09-04): Evidence & Diagnostics is a native
    macOS 14 `.inspector` toggled from the toolbar — it no longer competes
    with the workbench for height. The bottom drawer, the in-column pane,
    the `GeometryReader`-minus-190 estimate and the nested ScrollView it
    needed are gone. The open state is a persisted preference whose first
    default is closed: the delivery stream the user asked to see without
    clicking is the workbench's own activity section now, so review
    20260903-181141-6gjonu point 7 (never auto-open on a fault) holds
    again."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    assert '@AppStorage("AICollabEvidenceInspectorShown") private var showTechnical = false' in content
    assert "@State private var showTechnical" not in content
    detail = content.split("private var scenarioDetail", 1)[1].split(
        "@ToolbarContentBuilder", 1
    )[0]
    assert ".inspector(isPresented: $showTechnical)" in detail
    assert "evidenceInspector(scenario)" in detail
    assert ".inspectorColumnWidth(min: 300, ideal: 360, max: 560)" in detail
    # (the project rail's own bottom inset — "register project" — is a
    # different, unrelated inset; only the detail column is asserted here)
    assert ".safeAreaInset(edge: .bottom" not in detail
    for gone in (
        "evidenceBar(",
        "evidencePane(",
        "geo.size.height - 190",
        "technicalSection",
    ):
        assert gone not in content, gone
    inspector = content.split("private func evidenceInspector", 1)[1].split(
        "private var evidenceDomainPicker", 1
    )[0]
    assert "evidenceDomainPicker" in inspector
    for case in (
        "case .deliveries: deliveriesSection",
        "case .preflight: preflightSection",
        "case .topology: topologySection",
        "case .policy: policySection",
        "case .resources: resourcesSection",
        "case .inspector: inspectorSection",
        "case .analytics: deliveryDistributionSection",
        "case .highRisk: highRiskSection(scenario)",
    ):
        assert case in inspector, case
    toolbar = content.split("private func detailToolbar", 1)[1].split(
        "private func roomList", 1
    )[0]
    assert "showTechnical.toggle()" in toolbar
    assert 'systemImage: "sidebar.trailing"' in toolbar



def test_progress_metrics_are_exactly_four_not_five() -> None:
    """codex review 20260903-203219-kq79nn P1 visual: exactly four
    collaboration-health metrics (team ready, requests closed, first-attempt
    delivery, degraded); end-to-end evidence lives in Analytics. v2 renders
    them as label/value rows, not 2×2 big-number tiles, and neutralises the
    ratio rows in an inactive (closed) room so an expected "0/2 team ready"
    is never painted as needing attention."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    rows = content.split("private func collaborationHealthSection(neutral: Bool)", 1)[1].split(
        "\n    private func healthClass", 1
    )[0]
    assert rows.count("CollaborationHealthMetricRow(") == 4
    assert "S.CollaborationHealth.endToEndEvidence" not in rows
    assert "CollaborationHealthMetricTile" not in content
    row_struct = content.split("private struct CollaborationHealthMetricRow", 1)[1].split(
        "private struct StateBadge", 1
    )[0]
    assert "textCase(.uppercase)" not in row_struct
    assert ".monospacedDigit()" in row_struct
    classifier = content.split("private func healthClass", 1)[1].split("\n    }\n", 1)[0]
    assert "case .incomplete: neutral ? nil : .attention" in classifier
    assert content.count(
        "collaborationHealthSection(neutral: scenario.presentationClass == .inactive)"
    ) == 2, "both the wide column and the narrow fallback must neutralise closed rooms"
    distribution = content.split("private var deliveryDistributionSection", 1)[1].split(
        "\n    private var inspectorSection", 1
    )[0]
    assert "S.CollaborationHealth.endToEndEvidence" in distribution


def test_mission_bar_is_sticky_outside_the_scroll_view() -> None:
    """codex review 20260903-194506-9xgiml P1, refined by 20260903-201119-
    r9tf2j: MissionBar as a plain VStack sibling above the ScrollView did
    keep it from scrolling away, but a bare sibling does not get the same
    toolbar safe-area accounting a ScrollView gets automatically as
    NavigationSplitView detail content — content rendered up under the
    unified title bar on a real build. `.safeAreaInset(edge: .top)` on the
    ScrollView is the documented pattern for a pinned header that
    participates correctly in that layout; a `.layoutPriority(1)`
    workaround was tried and explicitly rejected (see review
    20260903-201119-r9tf2j's own note not to keep it) because it did not
    address the actual cause."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    detail = content.split("private var scenarioDetail", 1)[1].split(
        "@ToolbarContentBuilder", 1
    )[0]
    assert ".safeAreaInset(edge: .top" in detail
    assert "missionBar(scenario)" in detail
    assert ".layoutPriority(1)" not in detail
    # v2: the inset hangs on the List (the scrolling view), which is what
    # participates in the unified title bar's inset accounting.
    assert "roomList(scenario, wide: wide)\n                            .safeAreaInset(edge: .top" in detail
    bar = content.split("private func missionBar", 1)[1].split("private func", 1)[0]
    assert "RoundedRectangle" not in bar
    assert ".background(.bar)" in bar
    assert ".layoutPriority" not in bar


def test_empty_project_shows_first_use_canvas_not_the_generic_placeholder() -> None:
    """codex review 20260903-194506-9xgiml P1: a registered project with zero
    Task Rooms rendered the same generic "select a room" placeholder as
    "rooms exist, none selected" — indistinguishable states with different
    next steps. A project with no rooms yet must get its own canvas with the
    create composer embedded directly, reusing the same fields and
    `createScenario()` call the room board's own composer already uses."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    canvas = content.split("private var emptyDetailCanvas", 1)[1].split(
        "\n    private ", 1
    )[0]
    assert "model.selectedProject != nil, model.scenarios.isEmpty" in canvas
    assert "$model.newScenarioObjective" in canvas
    assert "$model.newScenarioID" in canvas
    assert "await model.createScenario()" in canvas
    assert "ContentUnavailableView(" in canvas, "the generic placeholder stays for the other case"


def test_onboarding_registered_detail_actually_interpolates_the_project_name() -> None:
    """A `\\(project)` string-interpolation typo shipped as the literal text
    "(project) is registered…" once already — the parameter was accepted
    and never used, so every project's first-use canvas would have shown
    the same placeholder-looking sentence regardless of its real name."""
    strings = (APP_ROOT / "Strings.swift").read_text(encoding="utf-8")
    fn = strings.split("static func onboardingRegisteredDetail", 1)[1].split(
        "\n        static ", 1
    )[0]
    # Both language literals must actually interpolate — not just mention
    # the fixed bug in a comment, which is why this checks the `t(...)`
    # call's own two string arguments rather than the whole function text.
    call = fn.split("t(", 1)[1].split(")\n        }", 1)[0]
    assert call.count("\\(project)") == 2


def test_brand_accent_is_applied_at_the_window_root_not_system_blue() -> None:
    """codex review 20260903-194506-9xgiml P1 visual: the primary action and
    selected-project material were still plain system blue — the agreed
    stone/petrol accent was designed but never actually wired up."""
    app = (APP_ROOT / "AICollabApp.swift").read_text(encoding="utf-8")
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    assert ".tint(.brandAccent)" in app
    assert "static let brandAccent = instrument(" in content


def test_delivery_distribution_charts_moved_into_the_evidence_drawer() -> None:
    """codex review 20260903-194506-9xgiml P1 visual: two large distribution
    charts used to sit at the top of the first viewport, unconditionally,
    ahead of the team roster. They belong in Evidence & Diagnostics as their
    own tab, not the primary workbench."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    workbench = content.split("private func roomList", 1)[1].split(
        "\n    /// review", 1
    )[0]
    assert "deliveryDistributionSection" not in workbench
    assert "case .analytics: deliveryDistributionSection" in content


def test_needs_attention_header_is_compact_and_neutral_only_when_clear() -> None:
    """codex review 20260903-194506-9xgiml P1: a healthy room still opened
    with a prominent orange/headline "Needs attention" + alert-triangle
    label directly above its own green all-clear line — the icon and the
    words directly under it disagreed. The alert-styled headline must only
    render once there is something to actually flag."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    section = content.split("private var needsAttentionSection", 1)[1].split(
        "\n    private ", 1
    )[0]
    assert "let isClear = model.deliveryAttentionTotals?.isClear ?? false" in section
    # The alert headline is not unconditional — it lives in an `else` branch
    # keyed on `isClear`, with the neutral all-clear line in the `if`.
    assert "if isClear {" in section
    clear_branch, rest = section.split("if isClear {", 1)[1].split("} else {", 1)
    assert "Label(S.NeedsAttention.allClear" in clear_branch
    assert ".foregroundStyle(.secondary)" in clear_branch
    assert "Label(S.NeedsAttention.sectionTitle" in rest
    assert ".foregroundStyle(.orange)" in rest


def test_participant_activity_line_is_derived_only_from_delivery_metadata() -> None:
    """codex review 20260903-194506-9xgiml P1: 'Do not invent message
    semantics; use only observed participant state and delivery metadata as
    previously agreed.' The projection must read sender/receiver/state off
    a real DeliveryRecord — never any payload/content field, which
    DeliveryRecord does not even carry (HarnessModels.swift), so there is
    nothing to invent from even by accident."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    models = (APP_ROOT / "HarnessModels.swift").read_text(encoding="utf-8")
    fn = content.split("private func recentActivityLine", 1)[1].split(
        "\n    @ViewBuilder", 1
    )[0]
    assert "model.deliveries.filter" in fn
    assert ".sender.participantID" in fn
    assert ".receiver.participantID" in fn
    assert fn.count(".generation == participant.generation") == 2
    assert "enqueueSequence" in fn
    assert "S.Delivery.stateLabel(latest.state)" in fn
    for forbidden in ("payload", "content", "message_body", "text"):
        assert forbidden not in fn.lower()
    delivery_record = models.split("struct DeliveryRecord", 1)[1].split(
        "struct ", 1
    )[0]
    for forbidden in ("payload", "content", "body"):
        assert forbidden not in delivery_record.lower()
    assert "recentActivityLine(for: participant)" in content.split(
        "private func participantRow", 1
    )[1].split("private func", 1)[0]


def test_first_use_canvas_is_the_only_visible_room_composer_for_an_empty_project() -> None:
    """The first-use canvas and room-list composer share the same bindings;
    showing both at once creates mirrored fields and competing primary actions."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    scenarios_list = content.split("private var scenariosList", 1)[1].split(
        "private var scenarioGroups", 1
    )[0]
    assert "if model.selectedProject == nil || !model.scenarios.isEmpty" in scenarios_list


def test_destroy_flow_has_one_panel_entry_point_not_a_direct_force_delete() -> None:
    """codex review 20260903-185641-e6nznb: the room board's row menu must not
    jump straight to Force Delete — every delete entry point (row menu,
    mission bar, Evidence & Diagnostics) opens the same DestroyPanel, which
    loads a real preview for its explicit target before offering normal
    delete (eligible) or Force Delete (only once blocked)."""

    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    assert "struct DestroyPanel: View" in content
    # The room list's own row menu: no direct forceDestroyScenario shortcut.
    scenarios_list = content.split("private var scenariosList", 1)[1].split(
        "private var scenarioGroups", 1
    )[0]
    assert "forceDestroyScenario" not in scenarios_list
    assert "destroyPanelTarget = DestroyPanelTarget(" in scenarios_list
    # At least three independent entry points open the same panel: the room
    # board row menu, the mission bar's "…" menu, and Evidence & Diagnostics.
    assert content.count("DestroyPanelTarget(") >= 3
    # The panel itself explicitly (re)selects its target before loading a
    # preview, rather than trusting whatever scenario happened to already be
    # selected — the row a user right-clicks is not necessarily the one open
    # in the detail pane.
    panel = content.split("private struct DestroyPanel", 1)[1].split(
        "// MARK: - ContentView", 1
    )[0]
    assert "model.selectedScenarioID != scenario.id" in panel
    assert "await model.selectScenario(scenario.id)" in panel
    assert "await model.loadDestroyPreview()" in panel
    # Force Delete only appears once loaded and only for the blocked branch.
    assert 'Button(S.Rooms.forceDelete, role: .destructive)' in panel


def test_destroy_panel_target_identity_includes_generation() -> None:
    """codex review 20260903-191042-y57u0q P1: a same-named Scenario that was
    destroyed and recreated is a different incarnation with a different
    fence. `.sheet(item:)` identity keyed on id alone could reuse a stale
    sheet already showing the old incarnation's preview."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    target_struct = content.split("private struct DestroyPanelTarget", 1)[1].split(
        "private struct DestroyPanel", 1
    )[0]
    assert "scenario.generation" in target_struct


def test_destroy_panel_never_reads_a_failed_load_as_blocked() -> None:
    """codex review 20260903-191042-y57u0q P0: the panel must use a typed
    phase derived from an explicit success/failure outcome, never infer
    "blocked" from the mere absence of an "eligible" answer. The actual
    branch logic is pinned executable-state-test style in
    DestroyFlowTests.swift (testFailedReadIsFailedNeverBlockedAndOffersNeitherDeleteAction,
    mutated back to the bug and confirmed to fail for the right reason); this
    only pins the structural wiring stays in place."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    assert "enum DestroyPreviewPhase" not in content, (
        "the phase type lives in DestroyFlow.swift, not duplicated in ContentView"
    )
    decision = (APP_ROOT / "DestroyFlow.swift").read_text(encoding="utf-8")
    assert "case failed(String)" in decision
    assert "case stale" in decision
    panel = content.split("private struct DestroyPanel", 1)[1].split(
        "// MARK: - ContentView", 1
    )[0]
    assert "model.dismissError()" in panel
    assert "DestroyFlowDecision.phaseAfterLoad(" in panel
    assert "errorMessage: model.errorMessage" in panel
    # Every load and every destructive action re-checks identity against the
    # panel's own target — not just once when the sheet opened.
    assert panel.count("currentSelection == target") >= 2


def test_destroy_panel_force_confirmation_is_self_contained_not_cross_modal() -> None:
    """codex review 20260903-191042-y57u0q P1: a `dismiss()` paired with the
    parent view setting `highRiskIntent` in the same action is two modal
    presentations racing in one turn, which risked losing the confirmation
    on real hardware. Force Delete's confirmation must be DestroyPanel's own
    `.confirmationDialog`, not a hand-off to the parent's `highRiskIntent`."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    # HighRiskIntent no longer has a destroy-scenario case at all — both the
    # eligible and blocked paths call the ViewModel directly from the panel.
    high_risk_intent = content.split("private enum HighRiskIntent", 1)[1].split(
        "private struct DestroyPanelTarget", 1
    )[0]
    assert "forceDestroyScenario" not in high_risk_intent
    assert "case destroyScenario" not in high_risk_intent
    panel = content.split("private struct DestroyPanel", 1)[1].split(
        "// MARK: - ContentView", 1
    )[0]
    assert ".confirmationDialog(" in panel
    assert "confirmingForceDelete" in panel
    # No hand-off to the parent's highRiskIntent from inside the panel's own
    # actions — only mentioned in the doc comment explaining what this
    # replaced, never assigned.
    assert "highRiskIntent =" not in panel


def test_destroy_panel_only_dismisses_after_a_confirmed_success() -> None:
    """codex review 20260903-191042-y57u0q P1: a Host rejection must keep the
    panel open with its preview/blocker context intact, not vanish and leave
    the failure to a background banner."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    panel = content.split("private struct DestroyPanel", 1)[1].split(
        "// MARK: - ContentView", 1
    )[0]
    assert "DestroyFlowDecision.shouldDismissAfterAction(succeeded: succeeded)" in panel
    assert "let succeeded = await model.destroyScenario()" in panel
    assert "let succeeded = await model.forceDestroyScenario(current)" in panel
    # dismiss() must not appear unconditionally right after either destroy
    # call — both are gated by the shouldDismissAfterAction check above,
    # never a bare `await model.destroyScenario(); dismiss()`.
    assert "await model.destroyScenario()\n        dismiss()" not in panel
    assert "await model.forceDestroyScenario" in panel


def test_guide_is_a_dismissable_centered_card_deck() -> None:
    """User decision 2026-08-21: guidance is a centered card the employee
    steps through and can always close — never a persistent overlay bar."""

    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    assert "guidanceRail" not in content, "the rail is gone"
    deck = content.split("private var guideCard", 1)[1].split("// MARK: ", 1)[0]
    # Scoped to the guide deck itself, not banned file-wide: review
    # 20260903-201119-r9tf2j legitimately uses `.safeAreaInset(edge: .top)`
    # elsewhere for the (unrelated) sticky mission bar — a real, documented
    # SwiftUI pattern, not the persistent-overlay-bar anti-pattern this test
    # actually guards against.
    assert ".safeAreaInset(edge: .top" not in deck, "no top overlay bar layouts"
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


def test_objective_workbench_and_issuance_status_use_authoritative_revisions() -> None:
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    models = (APP_ROOT / "HarnessModels.swift").read_text(encoding="utf-8")
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    strings = (APP_ROOT / "Strings.swift").read_text(encoding="utf-8")

    assert 'operation: "scenario.objective.append"' in view_model
    assert '"objective": objective' in view_model
    assert "scenario.objectiveRevision" in content
    assert re.search(
        r"participant\.issuedObjectiveRevision\s*>=\s*scenario\.objectiveRevision",
        content,
    )
    assert 'value["issued_objective_revision"] as? Int ?? 0' in models
    assert 't("Issued", "已下发")' in strings
    assert 't("Pending issuance", "待下发")' in strings


def test_activity_stream_is_a_workbench_section_not_an_evidence_tab() -> None:
    """v2 (design re-review 2026-09-04, from the user's own m2 screenshot of a
    room with 78 deliveries): the delivery stream is the room's main content
    and renders as the workbench List's second section, newest first — not
    only as the first tab of the diagnostics. Still delivery metadata only:
    the Host's message_kind token as a noun, sender, receiver, state."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    strings = (APP_ROOT / "Strings.swift").read_text(encoding="utf-8")
    room_list = content.split("private func roomList", 1)[1].split(
        "private func progressColumn", 1
    )[0]
    assert "activitySection" in room_list
    section = content.split("private var activitySection", 1)[1].split(
        "private func activityRow", 1
    )[0]
    assert "$0.enqueueSequence > $1.enqueueSequence" in section, "newest first"
    assert "model.deliveryMessage" in section, "the showing-N-of-M footnote is rendered"
    assert "S.Deliveries.activityEmptyBody" in section
    row = content.split("private func activityRow", 1)[1].split("\n    /// ", 1)[0]
    assert "S.Deliveries.kindNoun(delivery.messageKind)" in row
    assert "delivery.presentationClass" in row
    assert "S.Delivery.stateLabel(delivery.state)" in row
    for forbidden in ("payload", "content", "message_body"):
        assert forbidden not in row.lower()
    for kind in (
        '"collaboration.review-request"',
        '"collaboration.question"',
        '"collaboration.pushback"',
        '"collaboration.done"',
    ):
        assert kind in strings.split("static func kindNoun", 1)[1].split("\n        }\n", 1)[0], kind
    # The raw ledger keeps its place in the inspector.
    assert "case .deliveries: deliveriesSection" in content


def test_stopped_colleague_issuance_reads_neutral_not_attention() -> None:
    """From the user's closed-room screenshot: every stopped colleague wore
    an orange "pending issuance" although a stopped colleague cannot hold a
    new revision until its next start. Only a running colleague on an old
    revision is attention-coloured."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    strings = (APP_ROOT / "Strings.swift").read_text(encoding="utf-8")
    meta = content.split("private func participantMetaLine", 1)[1].split(
        "\n    /// ", 1
    )[0]
    assert "participant.presentationClass == .inactive" in meta
    assert "S.Objective.pendingIssuanceInactive" in meta
    assert "issued || inactive ? Color.secondary : Color.orange" in meta
    assert 't("Issued at next start", "下次启动时下发")' in strings


def test_room_actions_live_in_the_toolbar_not_the_mission_bar() -> None:
    """v2: refresh / close / the delete menu / the inspector toggle are
    window-toolbar items over the detail column; the mission bar keeps only
    the contextual pair — the guide's single next step and the Host-gated
    Repair — so its height no longer depends on which buttons happen to
    apply."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    toolbar = content.split("private func detailToolbar", 1)[1].split(
        "private func roomList", 1
    )[0]
    assert "ToolbarItemGroup" in toolbar
    assert "S.Detail.refresh" in toolbar
    assert "S.Detail.close" in toolbar
    assert "S.Rooms.deleteMenu" in toolbar
    assert "DestroyPanelTarget(" in toolbar
    assert '["opening", "running", "degraded"].contains(scenario.observedState)' in toolbar
    header = content.split("private func missionBar", 1)[1].split(
        "private func objectiveInline", 1
    )[0]
    for moved in ("S.Detail.refresh", "S.Detail.close", "S.Rooms.deleteMenu", "ellipsis.circle"):
        assert moved not in header, moved
    assert "liveGuideAction()" in header
    # Prominent only while the step advances the room; the steady state's
    # focus action is a plain button.
    assert "model.guidance == .focusAndAssign" in header
    assert header.count(".buttonStyle(.borderedProminent)") == 1


def test_evidence_badges_use_entity_aware_presentation_classes() -> None:
    """The approved entity-aware table (claude reply 20260903-182112-nymtlc)
    was only wired to Scenario/Participant in Phase 1; the delivery,
    preflight, permission, topology, policy and lease badges kept the old
    global colour switch. v2 finishes it: `StateBadge` survives only inside
    the Diagnostics settings view."""
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    presentation = (APP_ROOT / "PresentationState.swift").read_text(encoding="utf-8")
    main_view = content.split("struct DiagnosticsView", 1)[0]
    assert "StateBadge(" not in main_view
    for call in (
        "cls: .preflight(preflight.status)",
        "cls: .preflight(check.status)",
        "cls: .permission(permission.status)",
        "cls: .topologyHealth(item.health)",
        "cls: .policy(requiresReplan: status.requiresReplan)",
        "cls: .policyPlan(canApply: plan.canApply)",
        "cls: delivery.presentationClass",
        "cls: .resourceLease(resource.status)",
    ):
        assert call in main_view, call
    assert "extension DeliveryRecord" in presentation
    assert 'case "not_determined": .waiting' in presentation


def test_window_opens_at_the_two_column_workbench_size() -> None:
    """The 1100×720 floor stayed the first-launch size, which never reached
    the two-column width. v2 sets a first-launch default; the floor is
    unchanged."""
    app = (APP_ROOT / "AICollabApp.swift").read_text(encoding="utf-8")
    content = (APP_ROOT / "ContentView.swift").read_text(encoding="utf-8")
    assert ".defaultSize(width: 1440, height: 900)" in app
    assert ".frame(minWidth: 1100, minHeight: 720)" in content


def test_live_loop_observes_participants_read_only() -> None:
    """v2: colleague state used to refresh only on select / manual refresh /
    after a mutation, so a dead TUI stayed "ready" on screen. The live loop
    now reads `scenario.status` + `participant.list` each tick — reads only,
    equality-gated so an unchanged roster does not re-render, and the
    objective draft is never touched from the loop."""
    view_model = (APP_ROOT / "HarnessViewModel.swift").read_text(encoding="utf-8")
    observe = view_model.split("private func observeRoom", 1)[1].split(
        "\n    func retryDelivery", 1
    )[0]
    assert 'operation: "scenario.status"' in observe
    assert "reloadParticipants(project: project, scenario: scenario)" in observe
    assert "scenarios[index] != current" in observe
    for forbidden in ("performMutation", "syncObjectiveDraft", "reloadPreflight", "reloadTopology"):
        assert forbidden not in observe, forbidden
    reload = view_model.split("private func reloadParticipants", 1)[1].split(
        "\n    func applyProgress", 1
    )[0]
    assert "if participants != parsed {" in reload

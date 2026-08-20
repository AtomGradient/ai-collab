# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

"""CLI surface for the executable AI Collaboration Harness Host."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir

from .client import HarnessClient, HarnessClientError
from .delivery import DeliveryError
from .host import HarnessHost
from .participant import ParticipantError
from .project import ProjectError
from .security import SecurityError
from .store import StoreError
from .workspace import WorkspaceError


def default_state_root() -> Path:
    override = os.environ.get("AI_COLLAB_STATE_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(user_data_dir("AI Collab")).expanduser().resolve()


def default_workspace_root(state_root: Path) -> Path:
    override = os.environ.get("AI_COLLAB_WORKSPACE_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    default_control_root = Path(user_data_dir("AI Collab")).expanduser().resolve()
    if state_root != default_control_root:
        return state_root / "workspaces"
    return (Path.home() / "Documents" / "Scenarios").resolve()


def add_harness_parser(subparsers: Any) -> None:
    harness = subparsers.add_parser(
        "harness",
        help="Run and control the local AI Collaboration Harness",
    )
    commands = harness.add_subparsers(dest="harness_command", required=True)

    host = commands.add_parser("host", help="Run the current-user Harness Host in the foreground")
    _add_connection_options(host, include_json=False)
    host.add_argument("--adapter-config", type=Path, default=None)
    host.add_argument("--participant-driver-config", type=Path, default=None)
    host.add_argument("--security-adapter-config", type=Path, default=None)
    host.add_argument("--workspace-root", type=Path, default=None)

    health = commands.add_parser("status", help="Read Host health and durable Scenario count")

    _add_connection_options(health)

    doctor = commands.add_parser(
        "doctor",
        help="Report machine readiness for every registry-declared dependency",
    )
    _add_connection_options(doctor)

    operation = commands.add_parser(
        "operation", help="Control a live cooperative Harness operation"
    )
    operation_commands = operation.add_subparsers(
        dest="operation_command", required=True
    )
    cancel = operation_commands.add_parser(
        "cancel", help="Request cooperative cancellation by exact operation identity"
    )
    cancel.add_argument("operation_id")
    _add_connection_options(cancel)

    project = commands.add_parser("project", help="Register and inspect Harness projects")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    project_register = project_commands.add_parser(
        "register", help="Validate and register one canonical project root"
    )
    project_register.add_argument("canonical_project_path", type=Path)
    project_register.add_argument("--request-id")
    _add_connection_options(project_register)
    project_list = project_commands.add_parser("list", help="List registered projects")
    _add_connection_options(project_list)
    project_reconcile = project_commands.add_parser(
        "reconcile", help="Refresh one project render and report repository drift"
    )
    project_reconcile.add_argument("project_instance_id")
    project_reconcile.add_argument("--request-id")
    _add_connection_options(project_reconcile)
    project_accept = project_commands.add_parser(
        "accept-update",
        help="Apply one exact pending project configuration update",
    )
    project_accept.add_argument("project_instance_id")
    project_accept.add_argument("availability_fingerprint")
    project_accept.add_argument("--request-id")
    _add_connection_options(project_accept)
    project_unregister = project_commands.add_parser(
        "unregister",
        help="Remove one registered project that owns no remaining Scenarios",
    )
    project_unregister.add_argument("project_instance_id")
    project_unregister.add_argument("--request-id")
    _add_connection_options(project_unregister)
    project_bootstrap = project_commands.add_parser(
        "bootstrap",
        help="Generate an owner-private .aicollab/project.yaml proposal",
    )
    project_bootstrap.add_argument("canonical_project_path", type=Path)
    project_bootstrap.add_argument("--request-id")
    _add_connection_options(project_bootstrap)

    scenario = commands.add_parser("scenario", help="Manage durable Harness Scenarios")
    scenario_commands = scenario.add_subparsers(dest="scenario_command", required=True)

    create = scenario_commands.add_parser("create", help="Create one closed durable Scenario")
    create.add_argument("scenario_id")
    create.add_argument("--project-instance-id", required=True)
    create.add_argument("--project-binding-digest", required=True)
    create.add_argument("--request-id")
    _add_connection_options(create)

    open_command = scenario_commands.add_parser("open", help="Open a closed Scenario")
    open_command.add_argument("scenario_id")
    open_command.add_argument("--project-instance-id", required=True)
    open_command.add_argument("--scenario-generation", required=True, type=int)
    open_command.add_argument("--state-revision", required=True, type=int)
    open_command.add_argument("--request-id")
    _add_connection_options(open_command)

    close_command = scenario_commands.add_parser(
        "close", help="Safely close a running Scenario without automatic force-stop"
    )
    close_command.add_argument("scenario_id")
    close_command.add_argument("--project-instance-id", required=True)
    close_command.add_argument("--scenario-generation", required=True, type=int)
    close_command.add_argument("--state-revision", required=True, type=int)
    close_command.add_argument("--drain-timeout-ms", type=int, default=10_000)
    close_command.add_argument("--request-id")
    close_command.add_argument(
        "--progress", action="store_true", help="Emit progress events to stderr"
    )
    _add_connection_options(close_command)

    start_all = scenario_commands.add_parser(
        "start-participants",
        help="Start every startable participant in one running Scenario",
    )
    start_all.add_argument("scenario_id")
    start_all.add_argument("--project-instance-id", required=True)
    start_all.add_argument("--scenario-generation", required=True, type=int)
    start_all.add_argument("--state-revision", required=True, type=int)
    start_all.add_argument("--request-id")
    start_all.add_argument(
        "--progress", action="store_true", help="Emit progress events to stderr"
    )
    _add_connection_options(start_all)

    for command_name, help_text in (
        ("repair", "Confirm conservative repair from durable Scenario state"),
        ("destroy-preview", "Preview exact Scenario destroy effects and blockers"),
        ("destroy", "Confirm destroy of one closed exact Scenario generation"),
        (
            "force-destroy",
            "Confirm exact owned-resource cleanup and destroy one Scenario",
        ),
    ):
        command = scenario_commands.add_parser(command_name, help=help_text)
        command.add_argument("scenario_id")
        command.add_argument("--project-instance-id", required=True)
        command.add_argument("--scenario-generation", required=True, type=int)
        command.add_argument("--state-revision", required=True, type=int)
        if command_name != "destroy-preview":
            command.add_argument("--request-id")
        _add_connection_options(command)

    status = scenario_commands.add_parser("status", help="Read one Scenario")
    status.add_argument("scenario_id")
    status.add_argument("--project-instance-id", required=True)
    _add_connection_options(status)

    diagnostic = scenario_commands.add_parser(
        "diagnostic", help="Emit a machine-readable Scenario diagnostic"
    )
    diagnostic.add_argument("scenario_id")
    diagnostic.add_argument("--project-instance-id", required=True)
    _add_connection_options(diagnostic)

    preflight = scenario_commands.add_parser(
        "preflight", help="Run fresh permission and readiness checks without prompting"
    )
    preflight.add_argument("scenario_id")
    preflight.add_argument("--project-instance-id", required=True)
    _add_connection_options(preflight)

    topology = scenario_commands.add_parser(
        "topology", help="Inspect current Participant window topology"
    )
    topology.add_argument("scenario_id")
    topology.add_argument("--project-instance-id", required=True)
    _add_connection_options(topology)

    focus = scenario_commands.add_parser(
        "focus", help="Focus and restore current Scenario Participant windows"
    )
    focus.add_argument("scenario_id")
    focus.add_argument("--project-instance-id", required=True)
    focus.add_argument("--scenario-generation", required=True, type=int)
    focus.add_argument("--state-revision", required=True, type=int)
    focus.add_argument("--request-id")
    _add_connection_options(focus)

    list_command = scenario_commands.add_parser("list", help="List Scenarios for one project")
    list_command.add_argument("--project-instance-id", required=True)
    _add_connection_options(list_command)

    resource = commands.add_parser(
        "resource", help="Inspect supervised Scenario resource leases"
    )
    resource_commands = resource.add_subparsers(
        dest="resource_command", required=True
    )
    resource_list = resource_commands.add_parser(
        "list", help="List redacted active, stale, and released resource leases"
    )
    resource_list.add_argument("--scenario-id", required=True)
    resource_list.add_argument("--project-instance-id", required=True)
    _add_connection_options(resource_list)
    resource_break = resource_commands.add_parser(
        "break", help="Confirm release of one exact stale resource lease"
    )
    resource_break.add_argument("lease_id")
    resource_break.add_argument("--scenario-id", required=True)
    resource_break.add_argument("--project-instance-id", required=True)
    resource_break.add_argument("--scenario-generation", required=True, type=int)
    resource_break.add_argument("--scenario-state-revision", required=True, type=int)
    resource_break.add_argument("--lease-revision", required=True, type=int)
    resource_break.add_argument("--request-id")
    _add_connection_options(resource_break)

    workspace = commands.add_parser(
        "workspace", help="Plan, provision, and inspect a Scenario workspace/environment"
    )
    workspace_commands = workspace.add_subparsers(
        dest="workspace_command", required=True
    )

    plan = workspace_commands.add_parser("plan", help="Freeze an exact-revision plan")
    _add_workspace_identity_options(plan)
    plan.add_argument("--component", action="append", default=[])
    plan.add_argument("--project-payload-json", default="{}")
    plan.add_argument("--request-id")
    _add_connection_options(plan)

    prepare = workspace_commands.add_parser(
        "prepare", help="Plan and provision an isolated Workspace in one command"
    )
    _add_workspace_identity_options(prepare)
    prepare.add_argument("--component", action="append", default=[])
    prepare.add_argument("--project-payload-json", default="{}")
    prepare.add_argument(
        "--progress", action="store_true", help="Emit progress events to stderr"
    )
    _add_connection_options(prepare)

    provision = workspace_commands.add_parser(
        "provision", help="Materialize and atomically publish a frozen plan"
    )
    _add_workspace_identity_options(provision)
    provision.add_argument("--plan-digest", required=True)
    provision.add_argument("--request-id")
    provision.add_argument(
        "--progress", action="store_true", help="Emit progress events to stderr"
    )
    _add_connection_options(provision)

    workspace_status = workspace_commands.add_parser(
        "status", help="Observe exact workspace/environment bindings"
    )
    _add_workspace_identity_options(workspace_status)
    workspace_status.add_argument("--receipt-digest", required=True)
    workspace_status.add_argument("--request-id")
    _add_connection_options(workspace_status)

    participant = commands.add_parser(
        "participant", help="Manage generic runtime/presentation participants"
    )
    participant_commands = participant.add_subparsers(
        dest="participant_command", required=True
    )
    participant_list = participant_commands.add_parser(
        "list", help="List participants declared in one Scenario"
    )
    participant_list.add_argument("--scenario-id", required=True)
    participant_list.add_argument("--project-instance-id", required=True)
    _add_connection_options(participant_list)
    participant_templates = participant_commands.add_parser(
        "templates", help="List participant templates from the configured driver"
    )
    _add_connection_options(participant_templates)
    add = participant_commands.add_parser(
        "add", help="Freeze one participant launch generation"
    )
    _add_participant_identity_options(add, existing=False)
    add.add_argument("--launch-spec-json", required=True)
    add.add_argument("--presentation-driver-id")
    add.add_argument("--request-id")
    _add_connection_options(add)
    replace = participant_commands.add_parser(
        "replace", help="Replace one participant with a validated new launch generation"
    )
    _add_participant_identity_options(replace, existing=True)
    replace.add_argument("--launch-spec-json", required=True)
    replace.add_argument("--presentation-driver-id")
    replace.add_argument("--request-id")
    _add_connection_options(replace)
    for command_name, help_text in (
        ("start", "Start one exact participant generation"),
        ("status", "Inspect one exact participant generation"),
        ("stop", "Stop one exact participant generation"),
        ("recover", "Recover one degraded participant into a new stopped generation"),
        ("force-stop", "Confirm force-stop of one exact owned participant"),
        ("delete", "Delete one exact stopped participant"),
    ):
        command = participant_commands.add_parser(command_name, help=help_text)
        _add_participant_identity_options(command, existing=True)
        if command_name != "status":
            command.add_argument("--request-id")
        if command_name == "delete":
            command.add_argument(
                "--confirm",
                action="store_true",
                help="Confirm that this stopped participant identity will be deleted",
            )
        _add_connection_options(command)

    policy = commands.add_parser("policy", help="Apply and inspect Scenario routing policy")
    policy_commands = policy.add_subparsers(dest="policy_command", required=True)
    policy_templates = policy_commands.add_parser(
        "templates", help="List project-provided team/policy templates"
    )
    policy_templates.add_argument("--project-instance-id", required=True)
    _add_connection_options(policy_templates)
    policy_plan = policy_commands.add_parser(
        "plan", help="Resolve one template against current Participant generations"
    )
    _add_scenario_identity_options(policy_plan, include_revision=True)
    policy_plan.add_argument("--template-id", required=True)
    _add_connection_options(policy_plan)
    policy_apply_plan = policy_commands.add_parser(
        "apply-plan", help="Apply one unchanged explicit policy plan"
    )
    _add_scenario_identity_options(policy_apply_plan, include_revision=True)
    policy_apply_plan.add_argument("--template-id", required=True)
    policy_apply_plan.add_argument("--plan-digest", required=True)
    policy_apply_plan.add_argument("--request-id")
    _add_connection_options(policy_apply_plan)
    policy_apply = policy_commands.add_parser("apply", help="Apply one exact policy version")
    _add_scenario_identity_options(policy_apply, include_revision=True)
    policy_apply.add_argument("--policy-json", required=True)
    policy_apply.add_argument("--request-id")
    _add_connection_options(policy_apply)
    policy_show = policy_commands.add_parser("show", help="Read the active policy snapshot")
    _add_scenario_identity_options(policy_show, include_revision=False)
    _add_connection_options(policy_show)

    message = commands.add_parser("message", help="Send one typed participant message")
    message_commands = message.add_subparsers(dest="message_command", required=True)
    send = message_commands.add_parser("send", help="Route and deliver one exact message")
    _add_scenario_identity_options(send, include_revision=True)
    send.add_argument("--sender-participant-id", required=True)
    send.add_argument("--sender-generation", required=True, type=int)
    send.add_argument("--sender-state-revision", required=True, type=int)
    send.add_argument("--receiver-intent-json", required=True)
    send.add_argument("--message-id", required=True)
    send.add_argument("--message-kind", required=True)
    send.add_argument("--message", required=True)
    send.add_argument("--request-id")
    _add_connection_options(send)

    delivery = commands.add_parser("delivery", help="Inspect or resume exact delivery state")
    delivery_commands = delivery.add_subparsers(dest="delivery_command", required=True)
    delivery_list = delivery_commands.add_parser(
        "list", help="List a bounded redacted Scenario delivery collection"
    )
    _add_scenario_identity_options(delivery_list, include_revision=False)
    delivery_list.add_argument("--limit", type=int, default=100)
    delivery_list.add_argument("--after-delivery-id")
    delivery_list.add_argument("--collection-digest")
    delivery_list.add_argument("--thread-root-delivery-id")
    _add_connection_options(delivery_list)
    for command_name in ("status", "retry", "consume"):
        command = delivery_commands.add_parser(command_name)
        _add_scenario_identity_options(command, include_revision=False)
        command.add_argument("delivery_id")
        if command_name in {"retry", "consume"}:
            command.add_argument("--event-sequence", required=True, type=int)
        if command_name == "consume":
            command.add_argument("--consumption-ack-json", required=True)
        _add_connection_options(command)


def _add_connection_options(parser: argparse.ArgumentParser, *, include_json: bool = True) -> None:
    parser.add_argument("--state-root", type=Path, default=None)
    parser.add_argument("--socket-path", type=Path, default=None)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    if include_json:
        parser.add_argument("--json", action="store_true")


def _add_workspace_identity_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("scenario_id")
    parser.add_argument("--project-instance-id", required=True)
    parser.add_argument("--scenario-generation", required=True, type=int)
    parser.add_argument("--state-revision", required=True, type=int)


def _add_participant_identity_options(
    parser: argparse.ArgumentParser, *, existing: bool
) -> None:
    parser.add_argument("participant_id")
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--project-instance-id", required=True)
    parser.add_argument("--scenario-generation", required=True, type=int)
    parser.add_argument("--scenario-state-revision", required=True, type=int)
    if existing:
        parser.add_argument("--participant-generation", required=True, type=int)
        parser.add_argument("--participant-state-revision", required=True, type=int)


def _add_scenario_identity_options(
    parser: argparse.ArgumentParser, *, include_revision: bool
) -> None:
    parser.add_argument("--project-instance-id", required=True)
    parser.add_argument("--scenario-id", required=True)
    if include_revision:
        parser.add_argument("--scenario-generation", required=True, type=int)
        parser.add_argument("--scenario-state-revision", required=True, type=int)


def run_harness_command(args: argparse.Namespace) -> int:
    state_root = args.state_root or default_state_root()
    if args.harness_command == "host":
        try:
            host = HarnessHost(
                state_root,
                args.socket_path,
                args.adapter_config,
                args.participant_driver_config,
                args.security_adapter_config,
                args.workspace_root or default_workspace_root(state_root),
            )
            host.bind()
            print(
                json.dumps(
                    {
                        "status": "ready",
                        "host_generation": host.host_generation,
                        "socket_path": str(host.socket_path),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            host.serve_forever()
            return 0
        except KeyboardInterrupt:
            return 0
        except (
            StoreError,
            WorkspaceError,
            ParticipantError,
            DeliveryError,
            SecurityError,
            ProjectError,
            OSError,
        ) as exc:
            print(json.dumps({"status": "failed", "reason": str(exc)}, sort_keys=True))
            return 1

    if args.timeout_seconds <= 0:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "cli.invalid-command",
                    "reason": "timeout must be positive",
                    "retryable": False,
                },
                sort_keys=True,
            )
        )
        return 1
    client = HarnessClient(
        state_root, args.socket_path, timeout_seconds=args.timeout_seconds
    )
    try:
        if args.harness_command == "status":
            result = client.host_status()
        elif args.harness_command == "doctor":
            result = client.environment_probe()
        elif args.harness_command == "operation":
            result = client.cancel_operation(args.operation_id)
        elif args.harness_command == "project":
            if args.project_command == "register":
                result = client.register_project(
                    canonical_project_path=str(args.canonical_project_path),
                    request_id=args.request_id,
                )
            elif args.project_command == "unregister":
                result = client.unregister_project(
                    project_instance_id=args.project_instance_id,
                    request_id=args.request_id,
                )
            elif args.project_command == "reconcile":
                result = client.reconcile_project(
                    project_instance_id=args.project_instance_id,
                    request_id=args.request_id,
                )
            elif args.project_command == "accept-update":
                result = client.accept_project_reconciliation(
                    project_instance_id=args.project_instance_id,
                    availability_fingerprint=args.availability_fingerprint,
                    request_id=args.request_id,
                )
            elif args.project_command == "bootstrap":
                result = client.bootstrap_project(
                    canonical_project_path=str(args.canonical_project_path),
                    request_id=args.request_id,
                )
            else:
                result = client.list_projects()
        elif args.harness_command == "scenario":
            if args.scenario_command == "create":
                result = client.create_scenario(
                    project_instance_id=args.project_instance_id,
                    scenario_id=args.scenario_id,
                    project_binding_digest=args.project_binding_digest,
                    request_id=args.request_id,
                )
            elif args.scenario_command == "open":
                result = client.open_scenario(
                    project_instance_id=args.project_instance_id,
                    scenario_id=args.scenario_id,
                    scenario_generation=args.scenario_generation,
                    scenario_state_revision=args.state_revision,
                    request_id=args.request_id,
                )
            elif args.scenario_command == "close":
                result = client.close_scenario(
                    project_instance_id=args.project_instance_id,
                    scenario_id=args.scenario_id,
                    scenario_generation=args.scenario_generation,
                    scenario_state_revision=args.state_revision,
                    drain_timeout_ms=args.drain_timeout_ms,
                    request_id=args.request_id,
                    progress_callback=(
                        lambda event: print(
                            json.dumps(event, ensure_ascii=False, sort_keys=True),
                            file=sys.stderr,
                            flush=True,
                        )
                        if args.progress
                        else None
                    ),
                )
            elif args.scenario_command == "start-participants":
                result = client.start_scenario_participants(
                    project_instance_id=args.project_instance_id,
                    scenario_id=args.scenario_id,
                    scenario_generation=args.scenario_generation,
                    scenario_state_revision=args.state_revision,
                    request_id=args.request_id,
                    progress_callback=(
                        lambda event: print(
                            json.dumps(event, ensure_ascii=False, sort_keys=True),
                            file=sys.stderr,
                            flush=True,
                        )
                        if args.progress
                        else None
                    ),
                )
            elif args.scenario_command in {
                "repair",
                "destroy-preview",
                "destroy",
                "force-destroy",
            }:
                common = {
                    "project_instance_id": args.project_instance_id,
                    "scenario_id": args.scenario_id,
                    "scenario_generation": args.scenario_generation,
                    "scenario_state_revision": args.state_revision,
                }
                if args.scenario_command == "repair":
                    result = client.repair_scenario(
                        **common, request_id=args.request_id
                    )
                elif args.scenario_command == "destroy-preview":
                    result = client.preview_destroy_scenario(**common)
                elif args.scenario_command == "destroy":
                    result = client.destroy_scenario(
                        **common, request_id=args.request_id
                    )
                else:
                    result = client.force_destroy_scenario(
                        **common, request_id=args.request_id
                    )
            elif args.scenario_command == "status":
                result = client.scenario_status(
                    project_instance_id=args.project_instance_id,
                    scenario_id=args.scenario_id,
                )
            elif args.scenario_command == "diagnostic":
                result = client.scenario_diagnostic(
                    project_instance_id=args.project_instance_id,
                    scenario_id=args.scenario_id,
                )
            elif args.scenario_command == "preflight":
                result = client.scenario_preflight(
                    project_instance_id=args.project_instance_id,
                    scenario_id=args.scenario_id,
                )
            elif args.scenario_command == "topology":
                result = client.scenario_topology(
                    project_instance_id=args.project_instance_id,
                    scenario_id=args.scenario_id,
                )
            elif args.scenario_command == "focus":
                result = client.focus_scenario(
                    project_instance_id=args.project_instance_id,
                    scenario_id=args.scenario_id,
                    scenario_generation=args.scenario_generation,
                    scenario_state_revision=args.state_revision,
                    request_id=args.request_id,
                )
            elif args.scenario_command == "list":
                result = client.list_scenarios(project_instance_id=args.project_instance_id)
            else:
                raise HarnessClientError(
                    "cli.invalid-command", "Harness Scenario command is unavailable"
                )
        elif args.harness_command == "workspace" and args.workspace_command in {
            "plan",
            "prepare",
        }:
            try:
                project_payload = json.loads(args.project_payload_json)
            except json.JSONDecodeError as exc:
                raise HarnessClientError(
                    "cli.invalid-command", "project payload is not valid JSON"
                ) from exc
            if not isinstance(project_payload, dict):
                raise HarnessClientError(
                    "cli.invalid-command", "project payload must be a JSON object"
                )
            planned = client.plan_workspace(
                project_instance_id=args.project_instance_id,
                scenario_id=args.scenario_id,
                scenario_generation=args.scenario_generation,
                scenario_state_revision=args.state_revision,
                requested_component_ids=args.component,
                project_payload=project_payload,
                request_id=(args.request_id if args.workspace_command == "plan" else None),
            )
            if args.workspace_command == "plan":
                result = planned
            else:
                workspace = planned.get("workspace")
                plan_digest = (
                    workspace.get("plan_digest")
                    if isinstance(workspace, dict)
                    else None
                )
                if not isinstance(plan_digest, str):
                    raise HarnessClientError(
                        "cli.invalid-reply", "workspace plan digest is unavailable"
                    )
                provisioned = client.provision_workspace(
                    project_instance_id=args.project_instance_id,
                    scenario_id=args.scenario_id,
                    scenario_generation=args.scenario_generation,
                    scenario_state_revision=args.state_revision,
                    plan_digest=plan_digest,
                    progress_callback=(
                        lambda event: print(
                            json.dumps(event, ensure_ascii=False, sort_keys=True),
                            file=sys.stderr,
                            flush=True,
                        )
                        if args.progress
                        else None
                    ),
                )
                result = {
                    "plan": workspace,
                    "workspace": provisioned.get("workspace"),
                }
        elif args.harness_command == "resource":
            if args.resource_command == "list":
                result = client.list_resources(
                    project_instance_id=args.project_instance_id,
                    scenario_id=args.scenario_id,
                )
            else:
                result = client.break_resource(
                    project_instance_id=args.project_instance_id,
                    scenario_id=args.scenario_id,
                    scenario_generation=args.scenario_generation,
                    scenario_state_revision=args.scenario_state_revision,
                    lease_id=args.lease_id,
                    lease_revision=args.lease_revision,
                    request_id=args.request_id,
                )
        elif args.harness_command == "workspace" and args.workspace_command == "provision":
            result = client.provision_workspace(
                project_instance_id=args.project_instance_id,
                scenario_id=args.scenario_id,
                scenario_generation=args.scenario_generation,
                scenario_state_revision=args.state_revision,
                plan_digest=args.plan_digest,
                request_id=args.request_id,
                progress_callback=(
                    lambda event: print(
                        json.dumps(event, ensure_ascii=False, sort_keys=True),
                        file=sys.stderr,
                        flush=True,
                    )
                    if args.progress
                    else None
                ),
            )
        elif args.harness_command == "workspace" and args.workspace_command == "status":
            result = client.workspace_status(
                project_instance_id=args.project_instance_id,
                scenario_id=args.scenario_id,
                scenario_generation=args.scenario_generation,
                scenario_state_revision=args.state_revision,
                receipt_digest=args.receipt_digest,
                request_id=args.request_id,
            )
        elif args.harness_command == "participant":
            if args.participant_command == "list":
                result = client.list_participants(
                    project_instance_id=args.project_instance_id,
                    scenario_id=args.scenario_id,
                )
            elif args.participant_command == "templates":
                result = client.list_participant_templates()
            elif args.participant_command == "add":
                try:
                    launch_spec = json.loads(args.launch_spec_json)
                except json.JSONDecodeError as exc:
                    raise HarnessClientError(
                        "cli.invalid-command", "launch spec is not valid JSON"
                    ) from exc
                if not isinstance(launch_spec, dict):
                    raise HarnessClientError(
                        "cli.invalid-command", "launch spec must be a JSON object"
                    )
                result = client.add_participant(
                    project_instance_id=args.project_instance_id,
                    scenario_id=args.scenario_id,
                    participant_id=args.participant_id,
                    scenario_generation=args.scenario_generation,
                    scenario_state_revision=args.scenario_state_revision,
                    launch_spec=launch_spec,
                    presentation_driver_id=args.presentation_driver_id,
                    request_id=args.request_id,
                )
            else:
                common = {
                    "project_instance_id": args.project_instance_id,
                    "scenario_id": args.scenario_id,
                    "participant_id": args.participant_id,
                    "scenario_generation": args.scenario_generation,
                    "scenario_state_revision": args.scenario_state_revision,
                    "participant_generation": args.participant_generation,
                    "participant_state_revision": args.participant_state_revision,
                }
                if args.participant_command == "start":
                    result = client.start_participant(
                        **common, request_id=args.request_id
                    )
                elif args.participant_command == "status":
                    result = client.participant_status(**common)
                elif args.participant_command == "stop":
                    result = client.stop_participant(
                        **common, request_id=args.request_id
                    )
                elif args.participant_command == "recover":
                    result = client.recover_participant(
                        **common, request_id=args.request_id
                    )
                elif args.participant_command == "replace":
                    try:
                        launch_spec = json.loads(args.launch_spec_json)
                    except json.JSONDecodeError as exc:
                        raise HarnessClientError(
                            "cli.invalid-command", "launch spec is not valid JSON"
                        ) from exc
                    if not isinstance(launch_spec, dict):
                        raise HarnessClientError(
                            "cli.invalid-command", "launch spec must be a JSON object"
                        )
                    result = client.replace_participant(
                        **common,
                        launch_spec=launch_spec,
                        presentation_driver_id=args.presentation_driver_id,
                        request_id=args.request_id,
                    )
                elif args.participant_command == "force-stop":
                    result = client.force_stop_participant(
                        **common, request_id=args.request_id
                    )
                elif args.participant_command == "delete":
                    if not args.confirm:
                        raise HarnessClientError(
                            "cli.confirmation-required",
                            "participant deletion requires --confirm",
                        )
                    result = client.destroy_participant(
                        **common, request_id=args.request_id
                    )
                else:
                    raise HarnessClientError(
                        "cli.invalid-command", "Harness participant command is unavailable"
                    )
        elif args.harness_command == "policy":
            if args.policy_command == "templates":
                result = client.list_policy_templates(
                    project_instance_id=args.project_instance_id
                )
            elif args.policy_command == "plan":
                result = client.plan_policy(
                    project_instance_id=args.project_instance_id,
                    scenario_id=args.scenario_id,
                    scenario_generation=args.scenario_generation,
                    scenario_state_revision=args.scenario_state_revision,
                    template_id=args.template_id,
                )
            elif args.policy_command == "apply-plan":
                result = client.apply_policy_plan(
                    project_instance_id=args.project_instance_id,
                    scenario_id=args.scenario_id,
                    scenario_generation=args.scenario_generation,
                    scenario_state_revision=args.scenario_state_revision,
                    template_id=args.template_id,
                    plan_digest=args.plan_digest,
                    request_id=args.request_id,
                )
            elif args.policy_command == "show":
                result = client.show_policy(
                    project_instance_id=args.project_instance_id,
                    scenario_id=args.scenario_id,
                )
            elif args.policy_command == "apply":
                policy_pack = _json_object(args.policy_json, "policy")
                result = client.apply_policy(
                    project_instance_id=args.project_instance_id,
                    scenario_id=args.scenario_id,
                    scenario_generation=args.scenario_generation,
                    scenario_state_revision=args.scenario_state_revision,
                    policy_pack=policy_pack,
                    request_id=args.request_id,
                )
            else:
                raise HarnessClientError(
                    "cli.invalid-command", "Harness policy command is unavailable"
                )
        elif args.harness_command == "message":
            receiver_intent = _json_object(
                args.receiver_intent_json, "receiver intent"
            )
            result = client.send_message(
                project_instance_id=args.project_instance_id,
                scenario_id=args.scenario_id,
                scenario_generation=args.scenario_generation,
                scenario_state_revision=args.scenario_state_revision,
                sender_participant_id=args.sender_participant_id,
                sender_participant_generation=args.sender_generation,
                sender_participant_state_revision=args.sender_state_revision,
                receiver_intent=receiver_intent,
                message_id=args.message_id,
                message_kind=args.message_kind,
                message=args.message,
                request_id=args.request_id,
            )
        elif args.harness_command == "delivery":
            if args.delivery_command == "list":
                result = client.list_deliveries(
                    project_instance_id=args.project_instance_id,
                    scenario_id=args.scenario_id,
                    limit=args.limit,
                    after_delivery_id=args.after_delivery_id,
                    collection_digest=args.collection_digest,
                    thread_root_delivery_id=args.thread_root_delivery_id,
                )
            else:
                common = {
                    "project_instance_id": args.project_instance_id,
                    "scenario_id": args.scenario_id,
                    "delivery_id": args.delivery_id,
                }
                if args.delivery_command == "status":
                    result = client.delivery_status(**common)
                elif args.delivery_command == "retry":
                    result = client.retry_delivery(
                        **common, event_sequence=args.event_sequence
                    )
                elif args.delivery_command == "consume":
                    consumption_ack = _json_object(
                        args.consumption_ack_json, "consumption ACK"
                    )
                    result = client.consume_delivery(
                        **common,
                        event_sequence=args.event_sequence,
                        consumption_ack=consumption_ack,
                    )
        else:
            raise HarnessClientError("cli.invalid-command", "Harness command is unavailable")
    except HarnessClientError as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": exc.code,
                    "category": exc.category,
                    "reason": str(exc),
                    "retryable": exc.retryable,
                    "mutation_state": exc.mutation_state,
                    "repair_action": exc.repair_action,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _json_object(text: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HarnessClientError(
            "cli.invalid-command", f"{label} is not valid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise HarnessClientError(
            "cli.invalid-command", f"{label} must be a JSON object"
        )
    return value


def main(argv: list[str] | None = None) -> int:
    """Run the extracted CLI while preserving the existing harness grammar."""

    parser = argparse.ArgumentParser(prog="ai-collab")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_harness_parser(subparsers)
    arguments = parser.parse_args(argv)
    return run_harness_command(arguments)


if __name__ == "__main__":
    raise SystemExit(main())

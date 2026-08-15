# AI Collab macOS App

This directory contains the native SwiftUI thin client for the AI Collaboration Harness.
It talks directly to the same versioned Host IPC contract as the Python CLI; it does not
shell out to the CLI, identify EdgeStudio projects itself, or contain vendor-specific
Codex/Claude lifecycle logic.

## Current vertical slice

- register a user-selected canonical project through `project.register`;
- list/create/resume/close Scenarios and prepare their isolated Workspace;
- resume the exact Participants that were running before the last clean close,
  with per-Participant outcomes and Host-restart continuation;
- list driver-provided participant templates and add/start/stop/recover/replace/detach Participants;
- surface Participant cleanup-pending state and stale resource leases, with explicit
  Scenario repair, exact Participant force-stop and stale-lease break actions;
- run fresh no-prompt Scenario preflight checks, show provider-neutral permission
  observations, and preserve Host error category/retry/mutation/repair guidance as
  employee-readable next actions;
- choose a project-provided team template, preview exact participant generations plus
  route/retry effects, and explicitly apply its digest-fenced collaboration policy;
- display policy generation drift and the redacted Scenario delivery/thread health,
  with retry only when the Host marks the exact delivery event eligible;
- display diagnostics, resources, raw policy and persisted Workspace receipts;
- preview destructive effects, require an App intent confirmation, and hand every
  high-risk request to the Host's independent trusted native single-use confirmation chain.
- right-click a Scenario for one-step force delete: the Host first cleans only exact
  Harness-owned Participant resources, then removes the isolated Scenario Workspace;
  unproven ownership fails closed and the registered project source is never a target.

The App now embeds a minimal Python Harness runtime plus the EdgeStudio integration
plugins. On launch it registers the separate `AICollabHostAgent` as a current-user
`SMAppService` LaunchAgent, waits for the typed Host to become ready, and then uses the
same direct IPC client as the CLI. The App may surface macOS Login Items or Files &
Folders approval when the system requires it; it never bypasses those decisions.
Agent content remains in the independently owned TUI sessions: the App exposes no
participant send/reply operation and cannot impersonate an Agent.

This is a signed internal/dogfood installation slice. The App records the embedded
service build identity and re-registers the LaunchAgent when that identity changes.
Developer ID distribution, notarization and protected-directory authorization across
all recovery cases remain release work and are not implied by a successful source build.

## Build and test

```bash
python scripts/generate_harness_swift_contract.py --check
cd macos/AI-Collab
xcodegen generate
xcodebuild -project AICollab.xcodeproj -scheme AICollab \
  -derivedDataPath build/DerivedData CODE_SIGNING_ALLOWED=NO test
```

From the AI Collab repository, build a fresh signed internal App bundle with the
embedded Host and an explicit project integration plugin root:

```bash
python scripts/build_ai_collab_app.py \
  --integration-root /path/to/registered-project-integration \
  --output /private/tmp/AI-Collab-build/AI\ Collab.app

# Internal install or upgrade. Quit an already installed App first.
python scripts/install_ai_collab_app.py \
  --candidate /private/tmp/AI-Collab-build/AI\ Collab.app

# Stop and unregister the current-user Host before removing the App.
python scripts/install_ai_collab_app.py --unregister
```

The installer verifies the bundle and signing team, atomically swaps an existing App,
waits for a typed Host health check, and restores the previous App/Host on upgrade
failure. Its default per-user install location is `~/Applications/AI Collab.app`.

`LiveTest/LiveHarnessRoundTrip.swift` provides a cross-language smoke runner for an
isolated real Host. The App bundle, generated Xcode project and DerivedData are build
artifacts; only source, tests and `project.yml` are tracked.

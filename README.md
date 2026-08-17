# AI Collab

Local Multi-Agent Scenario Harness

AI Collab turns local development work into durable, isolated Scenario rooms.
Each Scenario owns its workspace, participants, collaboration routes, lifecycle
state, delivery journal, and recovery evidence. The same Host semantics serve the
macOS control plane, the automation-oriented CLI, and participant transport.

![The AI Collab App: a running Scenario with two participants, its collaboration policy, and the delivery journal](ai-collab.png)

Two agents in the same Scenario messaging each other through the Host — the
analyst sends with its Host-issued `ai-ping`, the reviewer receives a typed,
tracked delivery, and the App shows every thread with its delivery state:

![Two participant TUIs exchanging typed deliveries next to the App](ai-collab-with-tuis.png)

The product is designed around five constraints:

- long-lived task rooms instead of terminal management;
- parallel Scenarios without workspace, identity, process, or message crossing;
- first-class bidirectional collaboration between multiple agents;
- equivalent Host semantics for interactive and automated workflows;
- provider-neutral identity, recovery, policy, and evidence.

MIT licensed (see `LICENSE`). macOS only: participant windows are driven through
iTerm2.

## Getting it

The primary way to use AI Collab is the macOS App, distributed as a signed
`.dmg` on this repository's GitHub Releases. Install it, then describe your
project to it (next section). Everything below the App — the CLI, the Host as a
foreground service, the test suite — is available from a source checkout:

```bash
git clone https://github.com/AtomGradient/ai-collab.git
cd ai-collab
python3 -m venv .venv            # Python 3.11 or newer
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest       # no network, no App required
```

Three console entry points are installed:

| Command | What it is |
|---|---|
| `ai-collab harness …` | automation and diagnostics CLI |
| `ai-collab-host` | the Host as a foreground service |
| `ai-collab-participant` | the participant-side client |

When you start a Host by hand, treat `--state-root` as the one identity of that
Host: the IPC socket and the authorization secret both live inside it. Always
pass the same `--state-root` to the Host and to every client command aimed at
it; a client pointed at one Host's socket with another Host's state root is
rejected.

## Describing your project

The Host is deliberately ignorant of your project. What it knows how to do —
run Scenarios, isolate them, launch participants, route messages, recover — it
learns *about your project* from a **project adapter**. A generic, config-driven
adapter ships in the box, so for a typical multi-repo Git project you write no
adapter code at all. You add four small files to your project's canonical root:

`project_descriptor.yaml` — the project's identity and contract wiring:

```yaml
schema_version: 1
project_key: myproject
product_contract_version: "1.0"
workspace_adapter: ai-collab-workspace-v1
repo_manifest: repo_manifest.yaml
environment_adapter: ai-collab-environment-v1
gate_registry: gates.yaml
participant_driver_contract: 2
collaboration_policy_schema: 1
```

`repo_manifest.yaml` — every repository the workspace materializes. Exactly one
row is the `project_root` and its `repo_key` must equal `project_key`:

```yaml
schema_version: 1
project_key: myproject
repos:
  - repo_key: myproject
    classification: required
    placement: project_root
    path: .
    remote: git@github.com:example/myproject.git
    base_branch: main
    provision_order: 0
    provision_after: []
    acceptance_layer: base
    smoke_policy: required
    dependency_lock: requirements.lock      # optional
    python_source_path: src                 # optional, with python_import_name
    python_import_name: mypackage
  - repo_key: helper-lib
    classification: required
    placement: bundle_sibling
    path: helper-lib
    remote: https://github.com/example/helper-lib.git
    base_branch: main
    provision_order: 10
    provision_after: [myproject]
    acceptance_layer: base
    smoke_policy: optional
```

Each repository's `origin` must be configured to exactly the declared `remote`.
The optional fields declare how the Scenario's Python environment binds to a
repository: `dependency_lock` names the file whose digest identifies the
dependency set, and `python_source_path` / `python_import_name` put a source
directory on the Scenario venv's import path and name the module whose import
proves the binding works. Rows without them are materialized but not bound.

`gates.yaml` — the gate registry the descriptor points at. A minimal one is two
lines; the id encodes your project key and contract version:

```yaml
schema_version: 1
registry_id: ai-collab-scenario-harness-myproject-v1.0-20260817
```

`ai_collab_team_policies.json` — the collaboration templates your project
offers when a Scenario is created (participant roles, routes, retry policy).
The copy shipped at this repository's root is a working example to start from.

With those four files in place, registering the project in the App (or
`ai-collab harness project register <path>`) succeeds, and Scenarios provision
isolated clones of every managed repository plus a bound venv.

One more step makes participants start *inside* your project instead of one
level above it. Inside a provisioned Scenario the project materializes at
`bundle/<your canonical directory name>`, but the shipped runtime profiles
launch vendor CLIs in `bundle/` — the registry is product-generic and cannot
know your directory name, and a CLI started in `bundle/` will not see the
`AGENTS.md` / `CLAUDE.md` at your project root. Copy the shipped profile rows
into the overlay described below and set their `working_directory` to
`bundle/myproject`.

`scripts/ai_collab_project_descriptor.py --repo-root <path>` and
`scripts/ai_collab_repo_manifest.py --repo-root <path>` validate the two YAML
files standalone and print exact reasons on failure.

## How the App finds its configuration

The App's embedded Host reads three adapter configurations. Each is resolved
user-first: a file with the same name in
`~/Library/Application Support/AI Collab/` replaces the bundled one.

| File | Role |
|---|---|
| `ai_collab_harness_adapter.json` | project/workspace/environment adapter — the generic adapter by default |
| `ai_collab_participant_driver.json` | participant runtime driver |
| `ai_collab_security_adapter.json` | local permission observer and confirmation dialogs |

The three configs share one shape:

```json
{
  "schema_version": 1,
  "adapter_id": "my-project-adapter-v1",
  "command": [".venv/bin/python", "my_adapter.py"],
  "working_directory": "."
}
```

**Every path in `command` and `working_directory` is resolved relative to the
directory holding the config file, and absolute paths and `..` are rejected.**
This is what keeps the Host from being pointed at arbitrary programs elsewhere
on the machine: a config in Application Support can only name programs placed
under Application Support, and the config and every named program must be
writable by you alone. Replacing the project adapter with your own program —
for a project whose shape the generic adapter cannot describe — means placing
the program and such a config there. The adapter protocol is a single
stdin/stdout JSON exchange; `contracts/workspace_environment_v1.schema.json`
is the authoritative shape and `scripts/ai_collab_project_adapter.py` is a
complete reference implementation.

## Changing how a participant is launched

`ai_collab_runtime_profiles.json` defines each runtime profile: the executable,
its arguments, the working directory, how to recognise its process, whether it
accepts typed delivery, and whether it can resume a previous vendor session.

The shipped `runtime-profile.codex` and `runtime-profile.claude` profiles pass
`--dangerously-bypass-approvals-and-sandbox` and `--dangerously-skip-permissions`.
That is a deliberate default for unattended collaboration, and it means the
participant will not stop to ask you for approval. Their `working_directory`
is `bundle`, the one path that exists in every provisioned Scenario; an
overlay row pointing it at `bundle/<your project directory>` is part of normal
project setup (see the previous section).

To change it, do not edit the file in an installed App — it lives inside the
signed bundle and editing it there breaks the signature. Write an overlay
instead:

```
~/Library/Application Support/AI Collab/runtime_profiles.overlay.json
```

```json
{
  "schema_version": 1,
  "profiles": [
    { "profile_id": "runtime-profile.codex", "…": "a complete profile row" }
  ]
}
```

A row replaces the shipped profile with the same `profile_id`, or adds a new one
— which is how you drive a CLI this repository does not ship. Rows are whole
profiles, not patches, and they are validated exactly as strictly as the shipped
registry. A malformed overlay fails the launch rather than being ignored, so a
typo can never silently give you back the approval-bypass flags you were trying
to remove. `AI_COLLAB_RUNTIME_PROFILES_OVERLAY` overrides the location for
automation and tests.

## Building the App yourself

Xcode command-line tools and `xcodegen` are required, plus a codesigning
identity (any Apple Development certificate):

```bash
.venv/bin/python scripts/build_ai_collab_app.py \
  --output /tmp/AICollab.app \
  --dmg /tmp/AICollab.dmg
```

This produces the same App the Releases page distributes: the generic adapters
embedded, nothing project-specific inside. The optional `--integration-root`
flag replaces the embedded adapters with a consuming project's own adapter
payload; a build *without* it additionally asserts, from the finished payload,
that nothing from any integration project leaked into the bundle.

## Layout

- `src/ai_collab/` — provider- and project-neutral Host, state, policy, delivery,
  participant, workspace, security, and client implementation.
- `contracts/` — versioned machine-readable contracts. The core depends on these
  and nothing else; adapters and drivers are validated against them.
- `macos/AI-Collab/` — the macOS control plane and Host agent.
- `scripts/` — the generic project adapter and its validators, the default
  security adapter, participant runtime driver, App build tooling.
- `pingagent/` — participant-facing transport and the concise `ai-ping` command.
- `tests/` — core, contract, and adapter tests, runnable from a bare clone.

## Design documentation

This repository keeps one README on purpose. The architecture long-form, the
implementation history, and the integration design for the project we develop
against are maintained in that project's own repository, so that one consuming
project's repository names and delivery ledger cannot drift into the product
core. `contracts/` is the interface those documents describe, and it is the part
you actually need in order to build against this.

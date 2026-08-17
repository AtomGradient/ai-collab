# AI Collab

Local Multi-Agent Scenario Harness

AI Collab turns local development work into durable, isolated Scenario rooms.
Each Scenario owns its workspace, participants, collaboration routes, lifecycle
state, delivery journal, and recovery evidence. The same Host semantics serve the
macOS control plane, the automation-oriented CLI, and participant transport.

The product is designed around five constraints:

- long-lived task rooms instead of terminal management;
- parallel Scenarios without workspace, identity, process, or message crossing;
- first-class bidirectional collaboration between multiple agents;
- equivalent Host semantics for interactive and automated workflows;
- provider-neutral identity, recovery, policy, and evidence.

MIT licensed (see `LICENSE`). macOS only: participant windows are driven through
iTerm2.

## What you get, and what you still have to supply

The Host is deliberately ignorant of your project. It knows how to run
Scenarios, isolate them, launch participants, route messages, and recover; it
knows nothing about your repositories, your language environment, or how a
workspace for your project should be laid out. That knowledge lives in a
**project adapter** you supply.

**No adapter ships in this repository yet.** That has two consequences you will
hit immediately:

- A bare clone can run the test suite and start a Host, but `project.register`
  returns `project.adapter-unavailable`, and without a registered project there
  are no Scenarios.
- `scripts/build_ai_collab_app.py` requires `--integration-root` pointing at a
  directory that provides the adapter and its validators, so `AI Collab.app`
  cannot be built from this repository alone.

An adapter is a single executable that answers a small set of typed commands
(`register`, `collaboration_templates`, `plan`, `provision`, `status`, `repair`,
`destroy`) on stdin/stdout. `contracts/workspace_environment_v1.schema.json` is
the authoritative shape. Writing one is the current path to using this against
your own project; a worked example is the next thing to be added here.

## Install

Python 3.11 or newer.

```bash
git clone https://github.com/AtomGradient/ai-collab.git
cd ai-collab
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest          # 262 tests, no network, no App required
```

Three console entry points are installed:

| Command | What it is |
|---|---|
| `ai-collab harness …` | automation and diagnostics CLI |
| `ai-collab-host` | the Host as a foreground service |
| `ai-collab-participant` | the participant-side client |

The macOS App additionally needs Xcode command-line tools and `xcodegen`, plus
the `--integration-root` described above.

## Configuration

Four files. The first three are passed to the Host; the fourth is read by the
participant driver.

| File | Passed as | Required by |
|---|---|---|
| project/workspace/environment adapter config | `--adapter-config` | optional for `ai-collab harness host`, required by `ai-collab-host` and the App |
| participant driver config | `--participant-driver-config` | same |
| security adapter config | `--security-adapter-config` | same |
| `ai_collab_runtime_profiles.json` | not passed; read from this repository | the participant driver |

The three adapter configs share one shape:

```json
{
  "schema_version": 1,
  "adapter_id": "my-project-workspace-v1",
  "command": [".venv/bin/python", "scripts/my_project_adapter.py"],
  "working_directory": "."
}
```

**Every path in `command` and `working_directory` is resolved relative to the
directory holding the config file, and absolute paths and `..` are rejected.**
This is not incidental: it is what keeps a Scenario from reaching outside the
project it was registered for. In practice it means the config file lives at the
root of your project and names things beneath it. Put it elsewhere and the paths
will not resolve.

`adapter_id` is yours to choose. The Host does not interpret it; it only checks
that the adapter reports back the same id it was configured with.

### Changing how a participant is launched

`ai_collab_runtime_profiles.json` defines each runtime profile: the executable,
its arguments, the working directory, how to recognise its process, whether it
accepts typed delivery, and whether it can resume a previous vendor session.

The shipped Codex and Claude profiles pass `--dangerously-bypass-approvals-and-sandbox`
and `--dangerously-skip-permissions`. That is a deliberate default for
unattended collaboration, and it means the participant will not stop to ask you
for approval.

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
    { "profile_id": "runtime-profile.codex-dogfood", "…": "a complete profile row" }
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

## Layout

- `src/ai_collab/` — provider- and project-neutral Host, state, policy, delivery,
  participant, workspace, security, and client implementation.
- `contracts/` — versioned machine-readable contracts. The core depends on these
  and nothing else; adapters and drivers are validated against them.
- `macos/AI-Collab/` — the macOS control plane and Host agent.
- `scripts/` — participant runtime driver, App build/install, contract tooling.
- `pingagent/` — participant-facing transport and the concise `ai-ping` command.
- `tests/` — core and contract tests, runnable from a bare clone.

## Design documentation

This repository keeps one README on purpose. The architecture long-form, the
implementation history, and the integration design for the project we develop
against are maintained in that project's own repository, so that one consuming
project's repository names and delivery ledger cannot drift into the product
core. `contracts/` is the interface those documents describe, and it is the part
you actually need in order to build against this.

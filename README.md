# AI Collab

Local Multi-Agent Scenario Harness

AI Collab turns local development work into durable, isolated Scenario rooms.
Each Scenario owns its workspace, participants, collaboration routes, lifecycle
state, delivery journal, and recovery evidence. The same Host semantics serve
the macOS employee control plane, automation-oriented CLI, and participant
transport.

The product is designed around five constraints:

- long-lived task rooms instead of terminal management;
- parallel Scenarios without workspace, identity, process, or message crossing;
- first-class bidirectional collaboration between multiple agents;
- equivalent Host semantics for employee and automation workflows;
- provider-neutral identity, recovery, policy, and evidence.

## Repository status

Licensed under the MIT License (see `LICENSE`).

Not yet usable standalone. A bare clone can run this repository's own test
suite and start a Host through the CLI, but it cannot register a project or
build `AI Collab.app`, because both require a project integration adapter and
the only adapter that exists today lives in a private repository. Making the
Harness usable against any project — not just its first one — is tracked work,
not a finished property.

## Layout

- `src/ai_collab/` — provider- and project-neutral Host, state, policy,
  delivery, participant, workspace, security, and client implementation.
- `macos/AI-Collab/` — the macOS collaboration control plane and Host agent.
- `scripts/` — participant runtime, App build/install, and contract tooling.
- `pingagent/` — participant-facing transport and concise `ai-ping` command.
- `tests/` — core and product contract tests.
- `docs/ai-collab-harness/` — architecture, contracts, employee guide, and
  migration-era evidence pending public-document curation.

## Development

Python 3.11 or newer is required.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
```

The macOS App additionally requires Xcode command-line tools and `xcodegen`.

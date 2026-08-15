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

This repository is currently a private extraction staging area. It is not yet
licensed for redistribution or ready for public use. Public release requires a
separate license decision, source-header audit, secret scan, documentation
review, and the complete two-agent Harness regression gate.

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

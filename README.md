# AI Collab

Local multi-agent Scenario harness for macOS. Turn any Git project into
durable task rooms where multiple AI agents work in isolated workspaces and
talk to each other through a typed, audited Host.

![The AI Collab App: a running Scenario with two participants, its collaboration policy, and the delivery journal](ai-collab.png)

Two agents in one Scenario messaging each other through the Host:

![Two participant TUIs exchanging typed deliveries next to the App](ai-collab-with-tuis.png)

## Quick start

1. Download `AICollab.dmg` from [Releases](../../releases), drag it into
   Applications. First launch: right-click → **Open** (signed, not notarized).
2. Install [iTerm2](https://iterm2.com) — agents run in iTerm2 windows the
   Host owns and recovers.
3. Click **Register Project** and pick any Git directory. If the project has
   never met AI Collab, the App offers to draft its declaration files from
   the repositories it finds and registers it in one step.
4. Create a Scenario, add participants (Codex and Claude CLI profiles ship in
   the box), and they can message each other immediately — each participant
   receives its own Host-issued `ai-ping` command.

## How it works

- The **Host** is the single authority: identity, isolated workspaces, message
  delivery, lifecycle, recovery, and permissions all go through it. The App
  and the CLI (`ai-collab harness …`) are two views of the same Host.
- A project describes itself in four small files at its root
  (`project_descriptor.yaml`, `repo_manifest.yaml`, a gate registry, and
  collaboration templates). The App drafts them for you; edit and re-register
  to change what a Scenario provisions.
- Each Scenario gets exact-revision clones of the declared repositories plus a
  bound Python environment, with drift detection, WIP-preserving repair, and
  fail-closed destroy.

## Customize

- **Agent launch flags / other CLIs**: write whole-profile rows to
  `~/Library/Application Support/AI Collab/runtime_profiles.overlay.json`.
  Rows replace shipped profiles by `profile_id` or add new ones, and are
  validated as strictly as the shipped registry. (The shipped Codex/Claude
  profiles bypass approval prompts on purpose — unattended collaboration.)
- **Adapters**: a config with the same name in
  `~/Library/Application Support/AI Collab/` replaces the bundled one
  (`ai_collab_harness_adapter.json`, `ai_collab_participant_driver.json`,
  `ai_collab_security_adapter.json`). Paths inside a config resolve relative
  to the config's own directory only — that confinement is what keeps the
  Host from being pointed at arbitrary programs.
- **Your own project adapter**: the machine-readable contracts live in
  `contracts/`; `scripts/ai_collab_project_adapter.py` is a complete
  reference implementation.

## Build from source

Python 3.11+; Xcode command-line tools, `xcodegen`, and a codesigning
identity for the App:

```bash
git clone https://github.com/AtomGradient/ai-collab.git && cd ai-collab
python3 -m venv .venv && .venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest                          # full suite, no network
.venv/bin/python scripts/build_ai_collab_app.py \
  --output /tmp/AICollab.app --dmg /tmp/AICollab.dmg
```

MIT licensed. macOS only.

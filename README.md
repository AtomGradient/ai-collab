English | [简体中文](README.zh-CN.md)

# AI Collab

Local multi-agent task room harness for macOS. Turn any Git project into
durable task rooms where AI colleagues work in isolated workspaces and talk
to each other through a typed, audited Host. The interface speaks English
and Simplified Chinese (Settings → General → Language) and uses the task
room vocabulary throughout: a Scenario appears as a **Task Room**, a
Participant as an **AI Colleague**.

Read the story behind it on our blog:
[AI Collab — open-source multi-AI collaboration](https://www.atomgradient.com/en/blog/ai-collab-open-source-multi-ai-collaboration)
([中文](https://www.atomgradient.com/zh/blog/ai-collab-open-source-multi-ai-collaboration))

![The AI Collab App: a running task room with two AI colleagues, its collaboration policy, and the delivery journal](ai-collab.png)

Four AI colleagues across two task rooms messaging each other through the
Host, without disturbing each other:

![Four colleague TUIs across two task rooms exchanging typed deliveries next to the App](ai-collab-with-tuis.png)

## Quick start

1. Download `AICollab.dmg` from [Releases](../../releases), drag it into
   Applications, and open it. Release builds are signed and notarized.
2. Install [iTerm2](https://iterm2.com) — agents run in iTerm2 windows the
   Host owns and recovers.
3. Click **Register Project** and pick any Git directory. Fileless projects and
   older AI Collab declarations both register directly; registration never
   writes the selected checkout.
4. Create a task room and click **Prepare Workspace**. With that explicit
   action, the Host clones any missing declared repositories, checks out exact
   revisions, and verifies the isolated Workspace — one live progress row per
   repository. Credential, network, shallow-clone, branch, and disk failures
   stay typed and actionable; only transient failures are offered for
   immediate retry.
5. Add AI colleagues (Codex and Claude CLI profiles ship in the box). Added
   the wrong one? Stop it, then delete it from its ⋯ menu.
6. Click **Resume** — a freshly created room starts out closed, and the
   collaboration policy can only be previewed or applied while the room is
   open. Then pick a team template under Collaboration Policy,
   **Preview Plan** → **Apply Plan**, and click **Start All**. Once your
   colleagues are working, focus a window and assign the task — from here
   they can message each other, each through its own Host-issued `ai-ping`
   command. A centered getting-started card deck walks you through these
   steps and can be reopened anytime from the toolbar **?**.

## How it works

- The **Host** is the single authority: identity, isolated workspaces, message
  delivery, lifecycle, recovery, and permissions all go through it. The App
  and the CLI (`ai-collab harness …`) are two views of the same Host.
- A simple Git root needs no project files. Teams that need stable multi-repo
  intent can commit `.aicollab/project.yaml`; it contains semantic project
  intent, not AICollab runtime or adapter pins. Historical
  `project_descriptor.yaml` / `repo_manifest.yaml` checkouts remain readable
  but are no longer generated or rewritten.
- The Host stores a resolved runtime contract privately and copies the complete
  snapshot into every new Scenario. On launch, project
  selection, post-provision, or manual refresh, the App detects missing,
  undeclared, and drifted repositories. Semantic changes wait for the visible
  **Apply project update** action. Tool-owned compatibility pins refresh
  automatically, while an App upgrade can never rewrite an existing
  Scenario's self-contained contract.
- See [Project intent and zero-touch onboarding](docs/project-intent.md) for
  the tracked intent schema and upgrade behavior.
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

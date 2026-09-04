English | [简体中文](README.zh-CN.md)

# AI Collab

AI Collab runs multiple AI coding agents on one Git project, on one Mac.
A native macOS app and a local Host put agent CLIs — Codex, Claude Code, or
any CLI you add — into task rooms. Each room has its own isolated workspace,
objective and message journal. Each agent runs in an iTerm2 window the Host
owns. Agents message each other through the Host; the Host routes, retries
and journals every message.

[![Latest release](https://img.shields.io/github/v/release/AtomGradient/ai-collab?label=release)](https://github.com/AtomGradient/ai-collab/releases/latest)
![macOS 14+](https://img.shields.io/badge/macOS-14%2B-black)
![Signed & notarized](https://img.shields.io/badge/Apple-signed%20%26%20notarized-black)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

![Task room workbench: colleague list, collaboration activity, progress column](docs/images/readme-workbench.png)

*Task room workbench (v2 design). Left: projects and rooms. Center: the
room's AI colleagues and the deliveries between them, newest first. Right:
lifecycle stage, four collaboration-health counts, and the needs-attention
list. The UI ships in English and Simplified Chinese; the captures in this
README show the Chinese UI.*

Blog post: [AI Collab — open-source multi-AI collaboration](https://www.atomgradient.com/en/blog/ai-collab-open-source-multi-ai-collaboration)
([中文](https://www.atomgradient.com/zh/blog/ai-collab-open-source-multi-ai-collaboration))

## Vocabulary

| In the app | Underneath |
|---|---|
| Project | A Git root you own. Registration writes nothing into the checkout. |
| Task room | A Scenario: exact-revision clones of the declared repositories, a bound Python environment, an objective with acceptance criteria, and a delivery journal. Rooms close and reopen. |
| AI colleague | A Participant: an agent CLI process launched by the Host in an iTerm2 window the Host owns, checked every 5 seconds, and recoverable. |
| Collaboration activity | Deliveries between colleagues: review request, review response, question, response, pushback, notice, done. Each is routed by the room's policy and journaled from `queued` to `consumed`. |
| Evidence & Diagnostics | Raw deliveries, preflight checks, window topology, policy, resource leases, inspector JSON, delivery analytics, high-risk actions. Opens as an inspector column. |

## What the Host does

- **Single authority.** Identity, workspaces, deliveries, lifecycle,
  recovery and permissions go through the Host. The app and the CLI
  (`ai-collab harness …`) call the same Host. Each operation carries a
  capability proof bound to the Host generation. Destroy, force-stop, break
  lease and repair require the Host's own single-use native confirmation.
- **Isolated workspaces.** Preparing a room clones the declared
  repositories at exact revisions and binds a Python environment, with one
  progress row per repository. The Host detects drift, repairs while keeping
  work in progress, and refuses to destroy when preconditions fail.
- **Agent startup without a person.** Codex and Claude Code profiles are
  included; other CLIs are added through an overlay file. The Host answers
  vendor startup prompts (workspace trust, update screens) only when the
  full screen matches a known pattern inside a Host-verified workspace. An
  unknown prompt is not answered; the colleague is marked *Needs attention*
  with the screen as evidence.
- **Policy-routed deliveries.** A team template (analyst + reviewer, or
  analyst + reviewer + synthesizer) lists which message kinds each colleague
  may send to which, and the retry profile. A delivery moves through
  `queued → delivery_attempted → delivered → consumed`; `consumed` is
  recorded only when the receiving agent replies with that delivery's
  consumption token.
- **State without a clock.** The store keeps no wall-clock time; the journal
  is ordered by sequence number. Seven JSON-schema contracts in
  [`contracts/`](contracts/) define IPC, state, policy and delivery,
  permission confirmation, drivers, gates and workspace environment.
  627 Python tests and 111 Swift tests run without network access.

## Screens

![Needs-attention state with the Evidence & Diagnostics inspector open on Health Checks](docs/images/readme-attention.png)

*Needs-attention state (v2 design). A colleague failed to launch because an
iTerm2 permission is missing. The mission bar shows the reason and the
Repair action; the inspector on the right is open on Health Checks, where
the blocked check and the pending permission each carry their own action.
When the room column is narrower than 760 pt — here, because the inspector
is open — the progress column folds into the list, as shown.*

![Empty room right after creation](docs/images/readme-empty-room.png)

*A room right after creation (v2 design): no colleagues yet, the composer
row to add one, and the empty activity section stating what will appear
there.*

![First-use canvas: register a project, create the first room](docs/images/readme-first-use.png)

*A registered project with no rooms (v2 design): the create form sits in
the canvas, with the next steps listed under it.*

![Four colleague terminal windows across two rooms exchanging deliveries](docs/images/readme-with-tuis.png)

*Colleague windows (screenshot of a running build). Each colleague receives
a delivery as text — sender, kind, payload, reply instruction, consumption
token — and sends its own through the `ai-ping` command the Host issued to
it.*

## Quick start

1. Download `AICollab.dmg` from [Releases](../../releases), drag it into
   Applications, open it. Builds are signed and notarized; the Host and its
   Python runtime are inside the bundle.
2. Install [iTerm2](https://iterm2.com) and enable its Python API
   (Settings → General → Magic → **Python API**, then restart iTerm2), or run
   `defaults write com.googlecode.iterm2 EnableAPIServer -bool true` and
   `defaults write com.googlecode.iterm2 NoSyncEnableAPIServer -bool true`.
3. **Register Project** — choose a Git directory.
4. Create a task room, set an objective, click **Prepare Workspace**.
5. Add colleagues, click **Resume Room**, apply a team template under
   *Collaboration Policy*. Without a policy, deliveries are refused.
6. **Start All**. Focus a colleague's window and assign the task. The room's
   *Collaboration activity* lists each delivery; *Progress* lists what needs
   a person. The getting-started cards can be reopened from the toolbar **?**.

## How it works

```
                 you
                  │
   ┌──────────────┴──────────────┐
   │  AICollab.app (SwiftUI)     │      ai-collab harness … (CLI)
   └──────────────┬──────────────┘                 │
                  │  typed IPC · capability proofs · native confirmations
   ┌──────────────┴──────────────────────────────────────────────┐
   │  Host                                                       │
   │  projects · rooms · colleagues · policy routing ·           │
   │  delivery journal · supervision (5 s) · recovery · permissions │
   └───┬──────────────────┬──────────────────────┬───────────────┘
       │                  │                      │
  isolated workspaces   iTerm2 windows        store
  exact-revision clones (Host-owned)          sequence-ordered journal
  bound Python env      one per colleague     no wall clock

   colleague ──ai-ping──▶ Host ──delivery──▶ colleague
                    (routed, retried, journaled, consumption-acked)
```

- The Host stores the resolved runtime contract and copies a snapshot into
  each new room; an app upgrade does not rewrite an existing room's
  contract. Missing, undeclared and drifted repositories are detected on
  launch, on selection and after provisioning; semantic changes wait for
  **Apply project update**.
- A Git root needs no project file. For a multi-repository contract, commit
  `.aicollab/project.yaml` (team intent, no runtime pins). See
  [Project intent and zero-touch onboarding](docs/project-intent.md).
- [PingAgent](pingagent/) is the same message-passing idea without the Host:
  a filesystem mailbox plus iTerm2 injection between two agent panes. It is
  in this repository and is used to develop it: a Codex session and a Claude
  session review each other's commits through it.

## Customize

- **Other agent CLIs / launch flags**: add profile rows to
  `~/Library/Application Support/AI Collab/runtime_profiles.overlay.json`.
  Rows replace shipped profiles by `profile_id` or add new ones, validated
  like the shipped registry. The shipped Codex and Claude profiles bypass
  approval prompts.
- **Team policies**: `ai_collab_team_policies.json` holds the shipped team
  templates, roles, route rules and retry profiles.
- **Adapters**: a file with the same name in
  `~/Library/Application Support/AI Collab/` replaces the bundled one
  (`ai_collab_harness_adapter.json`, `ai_collab_participant_driver.json`,
  `ai_collab_security_adapter.json`). Paths inside a config resolve relative
  to the config's directory only.
- **Project adapters**: contracts in [`contracts/`](contracts/);
  [`scripts/ai_collab_project_adapter.py`](scripts/ai_collab_project_adapter.py)
  is the reference implementation.

## Repository layout

| Path | Contents |
|---|---|
| `src/ai_collab/` | Host, store, workspace, participant, delivery and policy engines; the `ai-collab` CLI |
| `macos/AI-Collab/` | SwiftUI app (`xcodegen` project), Swift tests, embedded Host service payload |
| `contracts/` | Seven JSON-schema contracts shared by Host, app, drivers and adapters |
| `pingagent/` | PingAgent: messaging between agents in iTerm2 panes; usable on its own |
| `scripts/` | Participant driver, project adapter, preflight, build / install / notarize tooling |
| `tests/` | Python suite, including the app-contract tests that pin UI decisions to source |
| `docs/` | Project intent schema, design notes, README images |

## Build from source

Python 3.11+; Xcode command-line tools, `xcodegen`, and a codesigning
identity for the app.

```bash
git clone https://github.com/AtomGradient/ai-collab.git && cd ai-collab
python3 -m venv .venv && .venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest                          # full suite, no network
cd macos/AI-Collab && xcodegen generate && xcodebuild -scheme AICollab -destination 'platform=macOS' test
cd ../.. && .venv/bin/python scripts/build_ai_collab_app.py \
  --output /tmp/AICollab.app --dmg /tmp/AICollab.dmg
```

## Next

- A "last active N s ago" line per colleague from the runtime heartbeat the
  Host already records in resource leases.
- Native list selection in the project and room columns that follows the
  system accent colour.
- Objective and acceptance-criteria revision history in the room header.

MIT licensed. macOS only.

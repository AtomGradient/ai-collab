**English** | [中文](README.md)

# PingAgent

> Make two AI agents living in two iTerm2 panes (e.g. Codex + Claude Code) talk to each other on demand: one writes code, the other reviews — without you copy-pasting between panes.

```
┌─ iTerm2 Pane A: Codex ─────────┐    ┌─ iTerm2 Pane B: Claude Code ───┐
│  > finished src/auth.ts        │    │                                │
│  > $ ai-ping claude --file ... │    │  ← osascript injects 1 line:   │
│                                │    │  [ai-collab inbox] from=codex  │
│                                │    │   please Read .ai-mailbox/     │
│                                │    │   inbox/claude/...md           │
│                                │    │  > Read .ai-mailbox/...        │
│                                │    │  > review done                 │
│  ← osascript injects reply     │    │  > $ ai-ping codex --reply-to  │
│  [ai-collab inbox] from=claude │    │                                │
└────────────────────────────────┘    └────────────────────────────────┘
                          ↓                          ↑
                    .ai-mailbox/inbox/<role>/<msg>.md
            (message bodies live in the filesystem;
             cross-pane injection only carries a short pointer)
```

## Core idea

- **Message bodies live in the filesystem**: markdown + YAML frontmatter — no shell-escape hell, no multi-line content loss, full audit history
- **Cross-pane keystroke injection only carries a short pointer**: `[ai-collab inbox] ... please Read <path>` — the AI then reads the file itself
- **One watcher per pane**: fswatch monitors that pane's inbox; new message → `osascript` injects into the same pane
- **The user can intervene at any time**: both panes are still ordinary terminals — type freely, Ctrl-C as usual

## Requirements

- macOS + iTerm2 (uses `osascript` to control sessions; **macOS-only** for now)
- bash 3.2+ (system bash on macOS works)
- `jq` (preinstalled on macOS, or `brew install jq`)
- `fswatch` (optional — falls back to 1 s polling otherwise; recommended: `brew install fswatch`)

## Install

```bash
git clone git@github.com:AtomGradient/PingAgent.git
cd PingAgent
./install.sh                  # symlink into ~/.local/bin/ (default)
# or ./install.sh --copy      # copy instead (independent of repo path)
```

`install.sh` will:
- Symlink `bin/ai-pane-register`, `bin/ai-pane-unregister`, `bin/ai-pane-doctor`, `bin/ai-ping`, `bin/ai-collab-watch`, `bin/ai-harness-transport` into `~/.local/bin/`
- Symlink `AGENTS.md` to `~/.config/ai-collab/AGENTS-template.md` (so you can copy it into any project from a stable location)
- Check `$PATH`; suggest `brew install fswatch` if missing

Verify:

```bash
which ai-ping ai-pane-register ai-pane-doctor ai-collab-watch ai-harness-transport
ai-ping --help
```

## Usage (per project)

### One-time init

```bash
cd <your-project>

# 1) Drop the protocol doc into the project (so both AIs read it)
cp ~/.config/ai-collab/AGENTS-template.md ./AGENTS.md
# If Claude Code reads CLAUDE.md, also:
ln -sf AGENTS.md CLAUDE.md

# 2) gitignore the working dir
echo '.ai-mailbox/' >> .gitignore
```

### Every time you open a new iTerm2 pane

**Pane A (Codex):**
```bash
cd <your-project>
ai-pane-register codex
codex                # or your actual launch command
```

**Pane B (Claude Code):**
```bash
cd <your-project>
ai-pane-register claude
claude
```

`ai-pane-register` does three things:
1. Saves the current pane's `$ITERM_SESSION_ID` UUID into `.ai-mailbox/.panes/<role>.json`
2. Starts `ai-collab-watch <role>` in the background (log: `.ai-mailbox/.watch-<role>.log`)
3. Cleans up any old watcher for the same role+mailbox, then starts a fresh watcher

Run `ai-pane-register` from the project root. If an ancestor directory already has `.ai-mailbox/`, `ai-pane-register` warns that the current command will use or create a nested mailbox; in most cases you should `cd` back to the ancestor project root instead.

**When you're done with a pane** (optional cleanup):

```bash
ai-pane-unregister           # auto-detects role from current pane
ai-pane-unregister codex     # or pass explicitly
```

It does three things: kills the watcher (and its `fswatch` child), removes the PID file, deletes `.panes/<role>.json`. Inbox/sent/dispatched history is preserved. If you forget to run it, the next `ai-pane-register` will reuse the slot.

### Verify

After both panes are registered, run this in the codex pane:

```bash
ai-ping claude "test: do you see this?"
```

Expected: the claude pane's input area auto-receives:

```
[ai-collab inbox] from=codex kind=msg id=... | please Read .ai-mailbox/inbox/claude/...md and follow its instructions; reply with ai-ping codex --reply-to <id> --file <your-reply.md>
```

> **Note**: the actual notification text is currently in Chinese (`[ai-collab 收信]` …). Both Codex and Claude Code handle the bilingual flow fluently. If you want it fully English, edit the notification template near the bottom of `bin/ai-collab-watch`.

claude should `Read` the file, then reply via `ai-ping codex --reply-to <id> "..."` — the codex pane will then receive its own injected notification.

## CLI cheat sheet

```bash
ai-pane-register <role>                                    # at pane startup
ai-pane-unregister [<role>]                                # at pane shutdown (optional)
ai-ping doctor                                             # read-only diagnostics
ai-ping <to> <message>                                     # one-line message
ai-ping <to> --file <path>                                 # long content (recommended)
ai-ping <to> --kind review-request --file ...              # specify kind
ai-ping <to> --reply-to <id> --file ...                    # reply
ai-ping <to> --wait --timeout 600 --file ...               # block until reply
echo "..." | ai-ping <to>                                  # stdin
```

Full kind table, parameter reference, error catalog, end-to-end review example: see [`docs/ai-ping.md`](docs/ai-ping.md).

## Project layout (when PingAgent is in use)

```
<your-project>/
├── AGENTS.md                       # protocol doc you cp'd in
├── .gitignore                      # contains .ai-mailbox/
└── .ai-mailbox/                    # gitignored working dir
    ├── .panes/
    │   ├── codex.json              # role + iTerm session UUID + cwd
    │   └── claude.json
    ├── inbox/
    │   ├── codex/<msg-id>.md       # messages addressed to codex
    │   └── claude/<msg-id>.md      # messages addressed to claude
    ├── sent/<msg-id>.md            # audit log of sent messages
    ├── .watch-codex.pid            # watcher PID
    ├── .watch-codex.log
    ├── .watch-claude.pid
    └── .watch-claude.log
```

## Design choices / known limitations

- **Notifications are pointers, not content**: avoids osascript escaping/wrapping issues; AI reads the full message itself
- **`.dispatched` sidecar dedup**: created only after osascript explicitly returns `ok`; `session not found`, Automation `-1743`, and unknown responses remain undispatched
- **A sidecar is not a consumption receipt**: it only means iTerm2 accepted the injection. Use a peer reply or explicit receipt to prove that the AI read and handled it. Empty sidecars created by older versions cannot retroactively prove delivery
- **Harness typed transport**: `ai-harness-transport` accepts only an exact iTerm session already resolved and fenced by Harness. It does not resolve roles, choose a mailbox, or write `.dispatched`; it returns redacted structured transport evidence only after exact-session injection returns `ok`. Harness Host still validates policy routing, delivery ACK, and consumption ACK.
- **Harness participant mode**: A Harness-launched participant keeps using `ai-ping <to>` and `--reply-to <delivery-id>`, but the intent returns to Host through a Host-issued scoped context. Host authenticates the sender with the local IPC peer PID and the exact owned descendant process chain. Once Host durably persists the route/envelope, the command returns a compact `accepted` result; Host independently supervises dispatch, delivery/consumption ACKs, retry, and restart recovery. Normal ACKs stay in machine state/UI/audit instead of being reinjected as “peer received it” Agent messages, triggering model inference, or creating ACK loops. This mode rejects `--from`, never writes or falls back to a legacy mailbox, and leaves explicit waiting/querying to the Host delivery read model instead of legacy `--wait`.
- **Atomic write**: `mktemp + mv`, watcher never sees half-written files
- **`sent/` is the audit log**: senders always have a copy
- **`--wait` defaults to 300 s**: under Claude Code's Bash tool ceiling of 600 s
- **Watcher is per-cwd**: switching projects requires re-registering (each project has its own mailbox)
- **Nested mailbox avoidance**: when run from a subdirectory, `ai-ping` / `ai-pane-unregister` prefer the mailbox where the current pane is registered and both roles belong to the same mailbox; they fall back to the nearest `.ai-mailbox/` only when no registration match exists
- **`--wait` polls (every 2 s)**: not event-driven — sufficient in practice
- **Watcher cleanup**: `ai-pane-unregister` kills the watcher and its `fswatch` child via `pkill -P`. The watcher itself also installs a `trap` to clean up children on signal
- **macOS-only**: `osascript` is Apple-specific. Linux would need `tmux send-keys` or similar — PRs welcome

## Troubleshooting

**Registered but the other side never gets notifications:**
- Check `.ai-mailbox/.watch-<role>.log` for `dispatching` lines
- `ps aux | grep ai-collab-watch` — is the watcher alive?
- If the watcher died: re-run `ai-pane-register <role>`
- If it is running but not injecting, inspect the log's `reason=` value. `automation_denied_-1743` means macOS denied Automation access; check the Apple Events sender's permission in System Settings

**`session not found`**:
- iTerm2 was restarted, or the pane changed. No `.dispatched` sidecar is written; re-run `ai-pane-register <role>` in the new pane to retry

**A message gets re-dispatched repeatedly:**
- Inspect the watcher log first. Failed delivery intentionally leaves no `.dispatched` sidecar; the polling fallback (when fswatch is unavailable) will retry. Do not bulk-delete existing sidecars—operate only on a message you have confirmed should be resent

**`ai-ping` says `Cannot auto-detect --from`:**
- You're running it from a pane that was never registered. Either run from a registered pane, or pass `--from <role>` explicitly

**It says `target '<role>' is not registered in selected mailbox`, but another mailbox has that role:**
- The two panes were likely registered from different project directories. In both panes, `cd` to the same project root and run `ai-pane-register <role>` again

**It says `sending as --from '<role>'`, but this pane is registered as another role:**
- Replies will go to the `--from` role's inbox, not the inbox watched by this pane. Unless you are intentionally relaying/testing, send as the role registered in this pane

**It says `Role must be lowercase...`:**
- `ai-pane-unregister <role>` only accepts lowercase letters, digits, underscores, and dashes

**You are unsure whether mailbox / watcher / inbox state is healthy:**
- Run `ai-ping doctor`. It is read-only and checks nested mailboxes, current pane registration, watchers, blackhole inboxes, spoofed sender history, injection errors, fswatch, and gitignore

**Watcher process won't die after `ai-pane-unregister`:**
- Nuclear option: `pkill -f ai-collab-watch`

## Tests

```bash
python -m pytest -q \
  tests/test-ai-harness-transport.py tests/test-ai-ping-harness.py
./tests/test-ai-collab-watch.sh
```

The regression suite covers successful injection, a missing session, Automation `-1743`, pre-upgrade sidecar deduplication, and fail-closed handling of an unknown successful response. It uses local osascript/fswatch test doubles and never injects text into a real iTerm2 session.

## Protocol doc (for the AIs)

Full protocol lives in [`AGENTS.md`](AGENTS.md) (currently Chinese). Copy it into your project root; both AIs will pick it up at startup.

## License

MIT

[English](README.en.md) | **中文**

# PingAgent

> 让两个 iTerm2 pane 里的 AI 助手（如 Codex 与 Claude Code）按需自动互发消息：一边写代码、一边审代码，不用你手动 copy-paste。

```
┌─ iTerm2 Pane A: Codex ─────────┐    ┌─ iTerm2 Pane B: Claude Code ───┐
│  > 写完 src/auth.ts            │    │                                │
│  > $ ai-ping claude --file ... │    │  ← osascript 注入一行通知      │
│                                │    │  [ai-collab 收信] from=codex   │
│                                │    │   请 Read .ai-mailbox/inbox/   │
│                                │    │   claude/...md 并审核           │
│                                │    │  > Read .ai-mailbox/...        │
│                                │    │  > 审核完毕                    │
│  ← osascript 注入回执通知      │    │  > $ ai-ping codex --reply-to  │
│  [ai-collab 收信] from=claude  │    │                                │
└────────────────────────────────┘    └────────────────────────────────┘
                          ↓                          ↑
                    .ai-mailbox/inbox/<role>/<msg>.md
                    （消息正文走文件系统，跨 pane 注入只是短通知）
```

## 核心思路

- **消息内容走文件系统**：markdown 文件 + YAML frontmatter，无 shell 转义地狱、无多行内容丢失、有完整历史
- **跨 pane 注入只发短通知**：`[ai-collab 收信] ... 请 Read <path>`，AI 看到后自己读文件
- **每个 pane 一个 watcher**：用 fswatch 监听本 pane 的 inbox，新消息到达就 osascript 注入本 pane
- **用户随时可介入**：两个 pane 都还是普通 terminal，user 直接打字 / Ctrl-C 都正常

## 依赖

- macOS + iTerm2（用 `osascript` 控制 session，因此目前**仅 macOS**）
- bash 3.2+（macOS 自带即可）
- `jq`（macOS 自带 / `brew install jq`）
- `fswatch`（可选，没有就回退到 1s 轮询；推荐 `brew install fswatch`）

## 安装

```bash
git clone git@github.com:AtomGradient/PingAgent.git
cd PingAgent
./install.sh                  # 默认 symlink 到 ~/.local/bin/
# 或 ./install.sh --copy      # 复制（不依赖 repo 路径）
```

`install.sh` 会：
- 把 `bin/ai-pane-register`、`bin/ai-pane-unregister`、`bin/ai-pane-doctor`、`bin/ai-ping`、`bin/ai-collab-watch`、`bin/ai-harness-transport` 链接到 `~/.local/bin/`
- 把 `AGENTS.md` 链接到 `~/.config/ai-collab/AGENTS-template.md`（方便从任意目录拷贝到项目里）
- 检查 PATH，提示装 fswatch（如未装）

确认安装：

```bash
which ai-ping ai-pane-register ai-pane-doctor ai-collab-watch ai-harness-transport
ai-ping --help
```

## 用法（每个项目）

### 一次性初始化

```bash
cd <你的项目>

# 1) 把协议说明放进项目（让两个 AI 都读到）
cp ~/.config/ai-collab/AGENTS-template.md ./AGENTS.md
# 如果 Claude Code 读 CLAUDE.md，可以再 ln：
ln -sf AGENTS.md CLAUDE.md

# 2) 加 .gitignore
echo '.ai-mailbox/' >> .gitignore
```

### 每次开新 iTerm2 pane

**Pane A（跑 Codex 的）：**
```bash
cd <你的项目>
ai-pane-register codex
codex                # 或 codex chat、或你的实际启动命令
```

**Pane B（跑 Claude Code 的）：**
```bash
cd <你的项目>
ai-pane-register claude
claude               # 启动 Claude Code
```

`ai-pane-register` 做三件事：
1. 把当前 pane 的 `$ITERM_SESSION_ID` UUID 存到 `.ai-mailbox/.panes/<role>.json`
2. 启动 `ai-collab-watch <role>` 后台进程（log 在 `.ai-mailbox/.watch-<role>.log`）
3. 清理同一 role+mailbox 的旧 watcher，再启动新的 watcher

建议在项目根目录执行 `ai-pane-register`。如果当前目录的上层已经有 `.ai-mailbox/`，`ai-pane-register` 会提示你正在使用或创建一套嵌套 mailbox；这通常意味着你应该先 `cd` 回上层项目根目录。

**关闭 pane 之前**（可选清理）：

```bash
ai-pane-unregister           # 从当前 pane 的 session id 自动反查 role
ai-pane-unregister codex     # 也可以显式传
```

会做三件事：杀掉 watcher（连同子进程 fswatch）、清掉 PID 文件、删掉 `.panes/<role>.json`。inbox/sent/dispatched 历史不动。忘了跑也没关系——下次 `ai-pane-register` 会复用同一个 role slot。

### 验证

启动两个 pane 后，在 codex 的 pane 里手动跑一下：

```bash
ai-ping claude "测试消息：你能看到这条吗？"
```

预期：claude 那个 pane 的输入框里**自动出现并提交**：

```
[ai-collab 收信] from=codex kind=msg id=... | 请 Read .ai-mailbox/inbox/claude/...md 并按其中说明处理...
```

claude 应该 Read 那个文件、然后用 `ai-ping codex --reply-to <id> "..."` 回执，codex pane 也会收到通知。

## CLI 速查

```bash
ai-pane-register <role>                                    # 在每个 pane 启动时跑一次
ai-pane-unregister [<role>]                                # 关闭 pane 时清理（可选）
ai-ping doctor                                             # 只读排障自检
ai-ping <to> <message>                                     # 简单消息
ai-ping <to> --file <path>                                 # 长内容（推荐）
ai-ping <to> --kind review-request --file ...              # 指定 kind
ai-ping <to> --reply-to <id> --file ...                    # 回复
ai-ping <to> --wait --timeout 600 --file ...               # 阻塞等回复
echo "..." | ai-ping <to>                                  # stdin
```

完整 kind 表、参数详解、错误排查、完整 review 往返示例见 [`docs/ai-ping.md`](docs/ai-ping.md)。

## 目录结构（每个使用了 PingAgent 的项目）

```
<your-project>/
├── AGENTS.md                       # 协议说明（你 cp 进来的）
├── .gitignore                      # 包含 .ai-mailbox/
└── .ai-mailbox/                    # gitignore 的工作目录
    ├── .panes/
    │   ├── codex.json              # role + iTerm session UUID + cwd
    │   └── claude.json
    ├── inbox/
    │   ├── codex/<msg-id>.md       # 给 codex 的消息
    │   └── claude/<msg-id>.md      # 给 claude 的消息
    ├── sent/<msg-id>.md            # 自己发出消息的副本（audit log）
    ├── .watch-codex.pid            # watcher PID
    ├── .watch-codex.log            # watcher 日志
    ├── .watch-claude.pid
    └── .watch-claude.log
```

## 设计选择 / 已知限制

- **通知内容只放路径不放正文**：避免 osascript 转义/换行问题，AI 自己 Read 文件读全文
- **`.dispatched` sidecar 去重**：只有 osascript 返回明确的 `ok` 后才创建；`session not found`、Automation `-1743` 或未知返回都保持未派发
- **sidecar 不是消费回执**：它只表示 iTerm2 接受了注入；AI 是否读取、处理仍以 peer 回复或显式 receipt 为准。升级前留下的空 sidecar 不能追溯证明真实送达
- **Harness typed transport**：`ai-harness-transport` 只接受 Harness 已解析并 fencing 的 exact iTerm session，不解析 role、不选择 mailbox、不写 `.dispatched`。它只在 exact session 注入得到明确 `ok` 后返回结构化、去除 raw session 的 transport evidence；policy route、delivery ACK 与 consumption ACK 仍由 Harness Host 验证。
- **Harness participant mode**：Harness 启动的 participant 仍使用相同 `ai-ping <to>` / `--reply-to <delivery-id>` 命令，但 intent 经 Host-issued scoped context 返回 Host；Host 结合本地 IPC peer PID 与 exact owned descendant process chain 核验 sender。Host 将 route/envelope 持久化后，命令立即返回紧凑的 `accepted` 结果；dispatch、delivery/consumption ACK、retry 与 restart recovery 由 Host 独立监督。正常 ACK 只进入机器状态/UI/审计，不作为“对方已收到”消息重新注入 Agent、触发模型推理或形成 ACK 循环。该模式禁止 `--from`，不会写入或回退到 legacy mailbox；显式查询/等待使用 Host read model，不复用 legacy `--wait`。
- **atomic write**：`mktemp + mv`，watcher 不会读到半截写入的文件
- **`sent/` 是 audit log**：发件人那边永远有副本，方便追溯
- **`--wait` 默认 300s**：低于 Claude Code Bash tool 上限 600s，避免误超时
- **watcher 是 per-cwd 的**：换项目要重新 register（每个项目独立 mailbox）
- **嵌套 mailbox 自动避让**：`ai-ping` / `ai-pane-unregister` 从子目录执行时，会优先选择当前 pane 注册过、且双方 role 在同一套里的 mailbox；只有找不到注册匹配时才退回到最近的 `.ai-mailbox/`
- **`--wait` 是轮询不是事件**（2s 一次）：要事件级可改成 socket-based，目前没需要
- **macOS only**：osascript 是 macOS 特有；Linux 要换成 tmux send-keys 之类，欢迎 PR

## 排错

**注册了但对方收不到通知**：
- 检查 `.ai-mailbox/.watch-<role>.log` 里有没有 `dispatching` 这一行
- `ps aux | grep ai-collab-watch` 看 watcher 进程是否还活着
- 若 watcher 死了：再跑一次 `ai-pane-register <role>`
- 若 watcher 活着但没注入：看日志里的 `reason=`；`automation_denied_-1743` 表示 macOS Automation 权限拒绝，应在系统隐私设置中检查发送 Apple Events 的宿主权限

**`session not found`**：
- iTerm2 重启或换 pane 后 UUID 失效。消息不会写 `.dispatched`；在新 pane 重跑 `ai-pane-register <role>` 后会重新尝试

**消息被重复触发**：
- 先看 watcher 日志。失败投递不会创建 `.dispatched`；没有 fswatch、使用轮询回退时会继续尝试。不要为“修复”而批量删除已有 sidecar，只对已确认需要重发的单条消息操作

**`ai-ping` 提示 `Cannot auto-detect --from`**：
- 你不在已注册的 pane 里。要么去注册过的 pane 里跑，要么 `ai-ping ... --from <role>` 显式传

**提示 `target '<role>' is not registered in selected mailbox`，但又说另一个 mailbox 里注册了该 role**：
- 两个 pane 很可能注册到了不同项目目录。分别在两个 pane 里 `cd` 到同一个项目根目录，重新执行 `ai-pane-register <role>`

**提示 `sending as --from '<role>'`，但当前 pane 注册成另一个 role**：
- 回信会进入 `--from` 指定 role 的 inbox，不会进入当前 pane 正在监听的 inbox。除非你在做 relay/测试，否则去当前 pane 注册的 role 下发送

**提示 `Role must be lowercase...`**：
- `ai-pane-unregister <role>` 的 role 只能包含小写字母、数字、下划线、短横线

**不确定 mailbox / watcher / inbox 状态是否正常**：
- 跑 `ai-ping doctor`。它只读检查嵌套 mailbox、当前 pane 注册、watcher、黑洞 inbox、伪装发送历史、注入错误、fswatch、gitignore

## 测试

```bash
python -m pytest -q \
  tests/test-ai-harness-transport.py tests/test-ai-ping-harness.py
./tests/test-ai-collab-watch.sh
```

回归覆盖正常注入、session 缺失、Automation `-1743`、升级前 sidecar 去重，以及未知成功输出的 fail-closed 行为。测试使用本地 osascript/fswatch test double，不会向真实 iTerm2 session 注入文本。

## 协议说明（给 AI 看）

完整协议见 [`AGENTS.md`](AGENTS.md)。把它 cp 到项目根目录，两个 AI 启动时会自动读到。

## License

MIT

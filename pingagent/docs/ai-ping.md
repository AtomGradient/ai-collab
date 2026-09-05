# ai-ping 完全指南

同一个 Agent-facing CLI 支持两种明确隔离的运行模式：普通项目中写入 legacy `.ai-mailbox/`，watcher 再注入对方 pane；由 EdgeStudio Harness 启动的 Scenario participant 中，将 send/reply intent 交回权威 Host，不创建或回退到 legacy mailbox。

## 命令签名

```
ai-ping <to> [options] [<message-words...>]
ai-ping doctor
```

## Harness Scenario 模式

Harness 只在它拥有的 participant process chain 中注入 scoped context locator、产品 client locator 与 `ai-ping`。此时命令形式保持不变：

```bash
ai-ping reviewer --kind review-request --file review.md
ai-ping analyst --kind review-response --reply-to <delivery-id> --file response.md
```

sender identity 不来自 `<to>`、`--from` 或环境变量字符串，而由 Host-issued scoped capability、Unix socket peer PID、exact participant generation 和 Harness-owned descendant process chain 共同核验。Host 持久化 route/envelope 后，`ai-ping` 立即输出紧凑的 `accepted` 结果；调用 shell/Agent 无须等待 receiver 消费。dispatch、delivery/consumption ACK、retry 与 Host restart recovery 独立继续，显式查询/等待走 Host read model。

接收端的完整正文写入该 Scenario 中 participant 工作目录下的 `.ai-mailbox/inbox/<role>/<delivery-id>.md`。若项目尚未忽略该目录，Workspace adapter 会在 Scenario 克隆的 `.gitignore` 中追加一行 `.ai-mailbox/`；用户注册的原始 checkout 不会被修改。exact-session transport 只向 TUI 注入一行 `[ai-collab 收信] ... 请 Read <path>` 通知。通知里的回复命令是裸的 `ai-ping <receiver> ...`：`~/.local/bin/ai-ping` 由 AI Collab 安装器和 App 管理，始终指向已安装 App 内置的 PingAgent 入口；发送方身份来自 Host 签发的 scoped context、peer PID 与 participant generation，与运行的是哪份 `ai-ping` 无关。因此长消息不经过 TUI 输入链路，不会因输入长度被截断；文件同时保留 frontmatter、回复命令和 consumption marker。这个 mailbox 仅是 Host 权威投递的正文载体，不使用 legacy watcher、不参与 role 发现，也不改变 Host 对 policy、route、journal、ACK 和 retry 的所有权。

正常 ACK 是机器 receipt，不重新注入 Agent 会话、不触发模型推理，也不要求 Agent 输出“对方已收到”。`--from` 会被拒绝；Harness 中的 `--wait` 也会被拒绝，避免与 legacy“等待业务回复”语义混淆。若 scoped context 存在但无效，命令 fail closed，不回退 legacy mailbox。PingAgent 只是简洁入口和 exact-session transport；policy、route、journal、delivery/consumption state 始终由 Host 持有。

## 速查（5 个最常用 pattern）

```bash
# 短消息（仅适合一句话；带特殊字符要 quote）
ai-ping claude "审一下 src/auth.ts"

# 长内容 / 带代码块 —— 推荐
ai-ping claude --kind review-request --file /tmp/req.md

# 回复某条消息（必须带 --reply-to，对方才能闭环）
ai-ping codex --reply-to 20260511-153000-abc123 --file /tmp/reply.md

# 阻塞等回复（codex 写完想直接拿到 review 再继续）
ai-ping claude --wait --timeout 600 --file /tmp/q.md

# stdin pipe
cat req.md | ai-ping claude --kind review-request

# 只读排障自检
ai-ping doctor
```

## 参数详解

| 参数 | 必需 | 说明 |
|---|---|---|
| `<to>` | ✓ | 目标 role，以 `.ai-mailbox/.panes/` 里的实际注册名为准 |
| `--file <path>` | | 从文件读消息正文。**推荐**：避免 shell 转义，保留代码块/换行 |
| `--kind <kind>` | | 消息类型，默认 `msg`。完整表见下面 |
| `--reply-to <id>` | | 这是对某条消息的回复。`<id>` 来自被回复消息的 frontmatter 里 `id:` 字段 |
| `--from <role>` | | 显式指定发送者。默认从当前 pane 的 `$ITERM_SESSION_ID` 自动反查 |
| `--wait` | | 阻塞直到收到 `reply_to=本次msg_id` 的回复（每 2s 轮询） |
| `--timeout <sec>` | | `--wait` 的最大等待秒数。默认 300 |
| 位置参数 | | 短消息可直接作命令行参数；与 `--file` / stdin 三选一 |

## kind 表

| kind | 含义 | 应否回复 | 典型场景 |
|---|---|---|---|
| `msg` | 普通消息 | 看情况 | 闲聊、简短问题、知会 |
| `review-request` | 请求审核 | **必须** 回 `review-response` | 完成一个功能、修非平凡 bug、改架构 |
| `review-response` | 审核结论 | 一般不必（除非有追问） | review-request 的回执 |
| `question` | 提问 | 必须 | 设计选择、技术疑问 |
| `pushback` | 反对/异议 | 必须，对方应停下重评估 | 收到的请求依赖错误前提 / 有更好方案 |
| `notice` | 知会 | 否 | "我开始改 X 了，注意冲突" |
| `done` | 完成通知 | 一般不必 | "我这边搞完了" |

## 工作原理

```
[发送方 pane]                  [文件系统]                  [接收方 pane]

ai-ping claude "..."   ─►   inbox/claude/<id>.md
                                  │
                                  │  watcher (fswatch) 监听 inbox/claude/
                                  │
                                  ▼
                            osascript 用 .panes/claude.json 里的
                            session UUID 注入到对方 pane
                                  │
                                  ▼
                          [ai-collab 收信] from=... id=... 自动出现 + 提交
```

消息文件 = YAML frontmatter + markdown 正文。frontmatter 至少有 `id` `from` `to` `kind` `created`，回复时还有 `reply_to`。

上图只描述 legacy mailbox 模式。Harness Scenario 模式是 `participant ai-ping → scoped product client → Host policy/delivery → Scenario 工作目录消息文件 → exact-session 短通知`；目录外形与 legacy mailbox 一致，但不启动 watcher，路由与状态仍由 Host 管理。

## mailbox 选择规则

`ai-ping` 会从当前目录向上扫描 `.ai-mailbox/`，但不会盲目使用最近的一套：

1. 默认自动 `--from` 时，优先选择“当前 pane 的 `$ITERM_SESSION_ID` 已注册，且目标 `<to>` 也注册在同一套”的 mailbox
2. 如果目标还没注册，则选择当前 pane 已注册的那套 mailbox，保留“消息先排队、对方稍后注册”的语义
3. 显式传 `--from <role>` 时，仍会优先选择当前 session 对应 role 所在的 mailbox；没有 iTerm2 session 时，优先选择 `<from>` 和 `<to>` 都注册过的 mailbox
4. 只有找不到任何注册匹配时，才退回到最近的 `.ai-mailbox/`

如果当前子目录里有残留或独立的 `.ai-mailbox/`，但当前 pane 实际注册在上层项目，`ai-ping` 会打印 `skipped nearer mailbox` 并把消息写到已注册 session 的那套 mailbox。

如果显式 `--from` 和当前 pane 在选中 mailbox 中注册的 role 不一致，`ai-ping` 会警告：对方按 frontmatter 回复时，回信会进入 `--from` 指定 role 的 inbox，而不是当前 pane 监听的 inbox。

## doctor 诊断

`ai-ping doctor` 是只读自检，不 kill、不删、不写。它会检查向上的 mailbox、当前 iTerm session 注册、watcher pid/orphan 状态、重复/分裂注册、无 watcher 的未派发 inbox、`sent/` 里的伪装 sender 历史、watcher log 注入错误、fswatch、`.gitignore`。全绿 exit 0；任何 WARN/FAIL exit 1。

## delivery 与 `.dispatched` 语义

watcher 只有在 osascript 以 0 退出且返回精确 `ok` 时才写 `<msg>.md.dispatched`。`session not found` 会分类为 `session_not_found`，Apple Event `-1743` 会分类为 `automation_denied_-1743`；二者以及其他未知返回都不写 sidecar，日志明确保留 `message remains undispatched`。

`.dispatched` 只是 legacy 注入去重标记，不是 AI 消费回执。AI 是否读取和完成处理必须由带 `reply_to` 的 peer 回复或更高层显式 receipt 证明；旧版本留下的空 sidecar 也不能追溯证明曾经真实注入。

## --wait 语义

`ai-ping claude --wait --timeout 600 --file /tmp/q.md`：

1. 写消息文件，记下本次 `msg_id`
2. watcher 立即注入对方 pane
3. **阻塞当前进程**，每 2s 扫描 `inbox/<本方-from>/` 找 `reply_to: <msg_id>` 的消息
4. 找到 → cat 回复内容、exit 0
5. 超时 → exit 2，消息留在对方 inbox（对方仍可异步回）

**重要**：
- 对方必须用 `--reply-to <id>` 回，否则解锁不了
- 如果在 Claude Code Bash tool 里用 `--wait`，记得把 Bash tool 的 `timeout` 也设大（最大 600000 ms）
- **不要双方同时 `--wait` —— 会互锁**

## 常见错误

| 错误信息 | 原因 | 解决 |
|---|---|---|
| `Cannot auto-detect --from` | 当前 pane 没注册 / `ITERM_SESSION_ID` 缺失 | 跑 `ai-pane-register <role>` 或显式 `--from <role>` |
| `No .ai-mailbox/ found upward from ...` | 当前目录不在已 register 过的项目下 | `cd <project>` 或确认 register 跑过 |
| `<to> must be lowercase...` / `--from must be lowercase...` | role 名不合法 | 只使用小写字母、数字、下划线、短横线 |
| `Role must be lowercase...` | `ai-pane-unregister <role>` 的 role 名不合法 | 只使用小写字母、数字、下划线、短横线 |
| `role 'doctor' is reserved` | `doctor` 是 `ai-ping doctor` 保留字 | 换一个 role 名 |
| `File not found: <path>` | `--file` 路径错 | 用绝对路径或确认文件存在 |
| `Cannot ping yourself (from=to=...)` | `--from` 和 `<to>` 同一个 role | 检查参数 |
| `target '<role>' not registered yet` | 对方还没 `ai-pane-register` | 让对方先 register |
| `target '<role>' is not registered in selected mailbox` | 双方 role 注册到了不同 mailbox | 两个 pane 都 `cd` 到同一项目根目录后重新 `ai-pane-register` |
| 对方收到但没自动提交（卡输入框） | watcher 还在用旧代码 | `pkill -f ai-collab-watch` 然后重新 `ai-pane-register <role>` |
| `Timeout after Ns — no reply yet` | `--wait` 等过头了，对方还没回 | 检查对方 pane / 调大 `--timeout` |

## 常见误区

1. **正文别放命令行参数里**：除非真是一句话。带特殊字符、代码块、换行的内容 **永远用 `--file`**
2. **每条消息只发一次**：watcher 只为已确认注入的消息写 `.dispatched` 并据此去重；重跑 ai-ping 会生成新 msg_id，不是覆盖。只对已确认需要重发的单条消息处理 sidecar，禁止不加判断地批量删除
3. **`--reply-to` 决定能不能闭环**：发起方 `--wait` 严格匹配 `reply_to == 自己的 msg_id`；不带 reply_to 的消息只是新一条而已
4. **kind 决定对方行为**：`pushback` 应让对方停下，`msg` 可能被随手处理。**选对 kind 很重要**
5. **不要 ai-ping 自己**：脚本直接拒绝 from == to
6. **`--wait` 不会自动重传**：超时只是不再等，消息已经躺在对方 inbox
7. **嵌套项目要确认 mailbox**：从子目录执行 `ai-ping` 没问题，但两个 pane 必须注册到同一套 `.ai-mailbox/`；看到 `skipped nearer mailbox` 是正常的自动避让提示

## 完整示例：一次 review 往返

**发起方（codex）：**

```bash
cat > /tmp/review-req.md <<'EOF'
请审核 commit abc123 的并发部分。

重点：
- src/auth.ts 的 login() 异步化是否有 race condition
- src/session.ts 的清理逻辑

测试：
- tests/auth_test.swift 已加新 case，跑通

设计权衡：
- 用了 actor 而不是 NSLock，因为 actor 在 swift 6 strict concurrency 下更安全
EOF

ai-ping claude --kind review-request --file /tmp/review-req.md
# 输出：Sent: 20260511-153000-abc123  (codex -> claude, kind=review-request)
```

**接收方（claude）** 看到通知 → Read inbox 文件 → 实际审核（可能 Read 相关源码 + Bash 跑测试）→

```bash
cat > /tmp/review.md <<'EOF'
## 结论：通过 with comments

### 严重问题
（无）

### 建议
1. `login()` 第 42 行：错误恢复路径漏了 `invalidate session()`，会泄漏 session
2. 测试没覆盖 race condition 场景，建议加 `testConcurrentLogin()`

### 思路
actor 选择合理。NSLock 版会有 reentrancy 风险，actor 自动序列化更安全。
EOF

ai-ping codex --kind review-response --reply-to 20260511-153000-abc123 --file /tmp/review.md
```

**codex 那边**自动收到通知 → Read 文件 → 看 review → 决定改还是讨论。

## 工作目录文件结构（参考）

```
<project>/.ai-mailbox/
├── .panes/
│   ├── codex.json              # 注册信息：session UUID + cwd + 时间戳
│   └── claude.json
├── inbox/
│   ├── codex/<msg-id>.md       # 给 codex 的消息
│   ├── codex/<msg-id>.md.dispatched   # watcher 去重标记
│   └── claude/<msg-id>.md
├── sent/<msg-id>.md            # 自己发出的副本（audit log）
├── .watch-codex.pid            # watcher PID
├── .watch-codex.log            # watcher 日志（osascript ok/error 都在这）
├── .watch-claude.pid
└── .watch-claude.log
```

调试时最有用的两个文件：`.watch-<role>.log`（看 `delivery confirmed` 或分类后的 `reason=`）+ `inbox/<role>/<id>.md`（看消息内容是否对）。运行 `./tests/test-ai-collab-watch.sh` 可验证 delivery failure-path，不会触碰真实 iTerm2 session。

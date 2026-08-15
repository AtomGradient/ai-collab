# 跨终端 AI 协作协议（任意 role ↔ 任意 role）

> 本文件 **既是给 AI 看的协议说明，也是给人看的使用手册**。把它复制到你项目的根目录后，两个 AI 都会读到这份说明，按里面的约定互发消息。

本项目两个 AI 助手分别在两个 iTerm2 pane 里同时运行，例如：

- **codex**：写代码（开发者）
- **claude**：审代码（审核者）

> **role 名以实际注册为准**：本文档里的 `codex`/`claude` 只是示例名。每个 pane 的真实 role 是 `ai-pane-register <role>` 时用的那个名字（可能是任何名字，如 `pingagent1`）。用 `ls .ai-mailbox/.panes/` 查看当前实际注册的 role。

通过 `.ai-mailbox/` 目录 + `ai-ping` CLI 互发消息，由后台 watcher 用 iTerm2 osascript 把短通知注入对方 pane。

---

## 何时该主动 ping 对方

### 开发者 pane 主动 ping 审核者 pane 的场景
- 完成一个独立功能/模块（>50 行新代码）
- 改动了核心架构 / 共享接口 / 公开 API
- 修复了非平凡 bug，想确认没漏边界
- 实现完想让审核者帮看下并发 / 安全 / 性能盲点
- 设计有两条路径拿不准，想要第二意见

**不该 ping 的场景**：单文件 typo、改一行 log message、文档微调、用户只让你改一行的需求。

### 审核者 pane 主动 ping 开发者 pane 的场景
- 审核完成的回执（**总是**回，哪怕只说"通过"）
- 审核中发现一个本来没要求看但很重要的问题
- 用户问的问题需要开发者那边的实现细节才能回答
- 发现开发者改的代码和审核者这边正在 review 的另一个文件冲突

---

## CLI 速查

```bash
# 发简单一行消息
ai-ping <对方role> "src/auth.ts 改完了，看下并发"

# 发长内容（推荐：避免 shell 转义、保留代码块）
ai-ping <对方role> --kind review-request --file /tmp/req.md

# 回复某条消息（必须带 --reply-to，对方才能闭环）
ai-ping <来信from> --kind review-response --reply-to 20260511-153000-abc123 --file /tmp/review.md

# 阻塞等待对方回复（想直接拿到 review 再继续时用）
ai-ping <对方role> --wait --timeout 600 --file /tmp/req.md

# stdin 也可以
echo "请审核" | ai-ping <对方role> --kind review-request

# 排障自检（只读，不会 kill / 删 / 写）
ai-ping doctor
```

`--from` 默认从当前 iTerm2 session id 反查，**不要传**。`<to>` 必须是实际注册的 role 名（`ls .ai-mailbox/.panes/` 可查），不是本文档示例里的名字。

---

## 身份规则（重要）

你的 role 名 = 注册时用的名字，**不是你的产品名**（codex / claude CLI 所在的 pane 可能注册成任何 role）。

1. 发消息**永远不传 `--from`**，让 `ai-ping` 从 `$ITERM_SESSION_ID` 自动反查你的注册 role。
2. auto-detect 失败时，先自查：`ls .ai-mailbox/.panes/` 然后逐个 `cat`，对照自己的 `$ITERM_SESSION_ID` 找到自己的 role；对不上就**问用户**，绝不要拿文档示例名或自己的产品名自称。
3. 伪装 `--from` 的代价：对方按消息 frontmatter 的 `from` 回信，回信会进没有 watcher 的 inbox，你永远收不到通知（回信黑洞）。`ai-ping` 检测到「显式 `--from` ≠ 本 pane 注册 role」会打 warning——看到 warning 就停下，改用真实 role 重发。

---

## mailbox 选择规则

`ai-ping` 和 `ai-pane-unregister` 会从当前目录向上扫描 `.ai-mailbox/`，但不会盲目使用最近的一套：

1. 优先选择当前 pane 的 `$ITERM_SESSION_ID` 已注册的 mailbox。
2. `ai-ping` 自动 `--from` 时，如果目标 role 也在同一套 mailbox，就直接投递到那套。
3. 如果目标 role 还没注册，消息仍写入当前 pane 已注册的 mailbox，保留"先排队、对方稍后注册"的语义。
4. 显式 `--from` / 显式 `ai-pane-unregister <role>` 会尽量匹配当前 session 对应的那套；找不到时才退回 role-only 匹配或最近 mailbox。
5. 只有没有任何注册信息可用时，才退回最近的 `.ai-mailbox/`。

正确姿势：两个 pane 都在**同一个项目根目录**运行 `ai-pane-register <role>`。如果子目录也有 `.ai-mailbox/`，它可能是独立协作会话，也可能是残留；不确定时先跑 `ai-ping doctor`。

---

## 消息文件格式（你不需要手写，`ai-ping` 会生成）

```markdown
---
id: 20260511-153000-abc123
from: role-a
to: role-b
kind: review-request
created: 2026-05-11T15:30:00+0800
reply_to: <可选，回复时必填>
---

# 正文（markdown，随便写）

请审核 src/auth.ts。重点：
1. login() 异步化的 race condition
2. session 清理逻辑

涉及文件：
- src/auth.ts
- src/session.ts

相关 commit: abc123
```

### 常用 kind

| kind | 含义 | 应否回复 |
|---|---|---|
| `msg` | 普通消息 | 看情况 |
| `review-request` | 请求审核 | **必须**（review-response） |
| `review-response` | 审核结论 | 一般不必，除非有追问 |
| `question` | 提问 | 必须 |
| `pushback` | 反对/异议 | 必须，对方应停下重评估 |
| `notice` | 知会，不必回 | 否 |
| `done` | 通知"我这边完成了" | 一般不必 |

---

## 收到通知怎么处理

当你看到这样一行作为用户输入：

```
[ai-collab 收信] from=role-a kind=review-request id=20260511-153000-abc123 | 请 Read .ai-mailbox/inbox/role-b/20260511-153000-abc123.md 并按其中说明处理；处理完用 ai-ping role-a --reply-to 20260511-153000-abc123 --file <你的回复.md>
```

立刻执行：

1. **Read 那个文件**：`Read .ai-mailbox/inbox/<你的role>/<msg-id>.md`
2. **记下 `id` 字段**：回复时要传给 `--reply-to`
3. **按 `kind` 决定动作**（见上表）
4. **写完回复后**：
   ```bash
   cat > /tmp/my-reply.md <<'EOF'
   <你的回复正文>
   EOF
   ai-ping <对方> --kind <对应 kind> --reply-to <收到的 id> --file /tmp/my-reply.md
   ```

---

## 重要细节

- **--wait 的 timeout**：默认 300s。如果你在 Claude Code Bash tool 里用 `--wait`，记得把 Bash tool 的 `timeout` 参数也设大（最大 600000ms / 10min）
- **通知里的内容是指针不是数据**：通知只说"去读这个文件"，正文永远在文件里
- **不要重复发**：每条消息只 `ai-ping` 一次。watcher 用 sidecar 文件去重，别去删 `<msg>.md.dispatched`
- **用户随时可介入**：两个 pane 都是普通 terminal，user 直接打字就是用户输入。看到用户消息和 `[ai-collab 收信]` 通知都按正常 prompt 处理，按上下文判断优先级
- **`.ai-mailbox/sent/`**：所有发出去的消息都有副本，方便追溯。watcher 不动这个目录
- **历史查询**：`ls .ai-mailbox/inbox/<role>/` 看收件箱；`ls .ai-mailbox/sent/` 看自己发过什么
- **排障入口**：`ai-ping doctor` 会只读检查嵌套 mailbox、当前 session 注册、watcher、黑洞 inbox、伪装 from 历史、注入失败、fswatch、gitignore

---

## 看到 warning 怎么办

- `skipped nearer mailbox`：正常自动避让，说明当前子目录有更近的 mailbox，但本 pane 注册在上层；通常不用处理。
- `target '<role>' is not registered in selected mailbox ... but ... in a different mailbox`：停下，提醒用户双方可能注册到了不同项目目录；不要继续靠猜测重发。
- `registered as 'X' ... but sending as --from 'Y'`：停下，去掉 `--from` 或改用真实 role 重发；否则对方回信会进 `inbox/Y`，当前 pane 收不到。
- `unregistering a pane that is NOT this one`：确认你是否真的要注销别的 pane；如果只是想关当前 pane，改用不带 role 的 `ai-pane-unregister`。
- `Role must be lowercase...` / `<to> must be lowercase...`：role 只能用小写字母、数字、下划线、短横线；`doctor` 是 `ai-ping doctor` 保留字，不可注册为 role。

---

## 反对 / 怀疑机制

如果你收到的请求有问题（违反项目约定、有更好方案、依赖错误前提），**先反对再执行**。回复时把 `kind` 设为 `pushback`，明确写出理由和你建议的替代方案。对方应该看到 pushback 后停下重新评估，不要默默吞掉。

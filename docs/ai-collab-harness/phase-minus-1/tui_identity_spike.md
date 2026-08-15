# TUI process / presentation identity feasibility spike

Gate：`SPIKE-TUI-ID-001`

裁决来源：`user_decision`（2026-08-09）——按“最小供应商依赖、vendor session identity 为可选 capability”重定义当前 gate；`user_decision`（2026-08-12）——以 Harness-owned descendant process chain 修正 foreground binding，并重新完成九层 evidence rebuild。

## 本 gate 证明什么

本 gate 只证明以下 required binding chain：

1. Host 按 versioned capability registry 遍历动态 participant plan，不按 Codex、Claude 或其他产品名分支；
2. 每个 participant/generation 使用 private launch token 准备 Harness-owned supervisor；
3. presentation driver 通过 iTerm2 官方 `Window.async_create()` 为每个交互式 participant 创建独立顶层 window，并只采用 create response 返回的 exact window/tab/session identity；
4. supervisor 在该 terminal session 内启动真实 vendor CLI launch-root child，并通过 private structured ACK 回报 participant、generation、driver、launch token、supervisor PID、launch-root PID 与 owned process group；
5. Host 要求 ACK token/binding 完全匹配、supervisor 与 launch-root 仍存活且 parent/process-group 关系精确成立；exact iTerm session 的 `jobPid` 必须是该 launch-root 本身，或通过内核 PPID chain 证明的 live descendant，并与 supervisor/launch-root 属于同一 owned process group；
6. 同一 process group 但不在该 launch-root descendant chain 内的进程不能建立 ownership；process name、cwd、recent/delta、窗口位置或供应商私有 session 数据同样不能替代 exact chain；
7. receipt 只保存覆盖 supervisor、launch-root、foreground descendant 与 owned process group 的 process identity SHA-256、presentation identity SHA-256、generation 与布尔 witness，不保存原始 PID/PGID、launch token、window/tab/session ID、credential 或本机路径；
8. teardown 先停止本次 supervisor 明确启动的 launch-root，并独立确认 accepted foreground job 与 launch-root 均 absent；由 supervisor 保持 exact terminal session，再只关闭 create response 与 ownership marker 共同证明 owned 的 window，最后按已披露路径结束 supervisor。

`explicit_recreate` 是 required baseline，并在 binding/receipt 中明确显示为 recreate。vendor session identity 与 `exact_resume` 在本 gate 均为 optional、未声明、未使用；Host 不读取或猜测它们。

## Driver capability 与失败语义

`RuntimeLaunchCapability` 必须声明：

- Harness process binding；
- `explicit_recreate` baseline；
- vendor session identity 是否可用；
- `exact_resume` 是否可用；若为 true，同时必须声明 vendor session identity surface 与 maturity。

请求未声明的 `exact_resume` 必须在 durable desired state 变化前返回 `unsupported`。禁止在 CAS 后静默降级，也禁止 role、cwd、window/position、最近 session、`--last`、picker、private store scan 等 fallback。某一 driver capability 因升级失效时，只降级该 driver 的 participant，不阻断 Host 或其他 driver。

本轮真实 witness 的 Codex 与 Claude driver 都只声明 normal CLI + Harness supervisor；Host 不依赖 Codex App Server、`--remote`、Claude `--session-id` 或 SessionStart hook。supervisor 在启动新 generation 前剥离调用方 ambient `CODEX_*`、`CLAUDE_*` 与旧 `AI_COLLAB_*` session 环境，防止把外层 pane identity 串入新 participant；子进程 `PATH` 只由已验证 runtime executable 的父目录加系统目录重建，既保留 npm wrapper 的 Node 依赖，也不继承调用方任意 PATH。vendor invocation flags 仍封装在各自 driver 内：Codex 使用 invocation-only hooks/MCP/update-check isolation；Claude 使用 invocation-only empty settings sources/settings。它们是可版本化 driver 细节，不进入 Host contract。

## Exact presentation cleanup witness

来源：`evidence`（fixed target `bcc11bbc4c338575e02d8592bea903cde5845568`；回归入口 `tests/test_ai_collab_tui_identity_spike.py` 的 supervisor hold/release、child absence、native identity/convergence、cleanup orchestration、retained scene 与 producer/verifier schema 用例；正式 run/review 和历史 receipt 记录于 [IMPLEMENTATION_PROGRESS.md](../IMPLEMENTATION_PROGRESS.md)）。

cleanup 的 primary witness 必须是：

1. exact launch-root child 已 quiesce 且 private status 已落盘，Host 独立观察到 launch-root 与 accepted foreground job 均 absent；supervisor 此时仍存活并保持 exact terminal session；
2. exact Python window 在 close action 前仍可观察，且 ownership marker round-trip 与创建时记录一致；
3. iTerm Python `Window.async_close(force=True)` 对该 exact window 成功返回；
4. Python model 中该 exact window 消失；
5. 独立、只读的 AppleScript audit 只检查创建时已证明等值的 exact native pair，确认它已 absent，或仅剩 `visible=false`、`tabs=0` 的非可见 wrapper；
6. supervisor 若仍存活，必须观察 release 并写 private ACK 后退出；若它已在成功的 exact presentation close 后退出，只能披露为该路径，不能记成 release。
7. Host 必须独立观测 ACK 中 exact launch-root PID 和 accepted foreground PID 在 quiesce 后与最终 teardown 后均 absent；cleanup entry 的 presentation identity hash 必须与 accepted binding 的 hash 等值。

第 5 项只能 corroborate，不能 substitute 第 2—3 项。session 自然退出也可能形成 `visible=false`、`tabs=0` wrapper，且 wrapper 可能延迟回收；因此 Python/native model absence、wrapper 最终消失或 `v2 marker=0` 都不能单独证明 Harness 执行过 close。无成功 exact close RPC 时必须 unresolved/fail closed，supervisor 的 unexplained absence 同理。

Python model 已确认 exact window 消失后，native wrapper 的 visibility/tab topology 允许在既有 8 秒 close deadline 内收敛；audit 只轮询创建时已证明等值的 exact native pair，不扩大 identity 搜索范围。deadline 内未达到 absent 或 `visible=false, tabs=0` 仍 fail closed，并保留具体 portable cleanup reason。

native identity capture/inspect 通过 `asyncio.to_thread` 执行 AppleScript subprocess，避免在等待 exact native convergence 时阻塞 adapter event loop；thread offload 不改变 subprocess timeout、exact identity 参数或 audit-only 边界。

AppleScript 在这里属于 iTerm presentation verifier 的 audit-only 平面：它不关闭 window、不进入 Host 或 runtime capability contract、不读取 vendor runtime session store、不使用 name/recent/delta，也不把 native window/session identity 写进 receipt。create 阶段无法证明 Python exact session UUID 与 AppleScript session id 的唯一等值关系时，初始化必须关闭 exact create response 并 fail closed，不能跳过 audit。

owned-process quiesce 有 bounded ACK/status deadline。若 quiesce 失败且 Host 仍观察到 exact launch-root 或 accepted foreground job 存活，presentation close 必须被拒绝，并分别披露 `presentation_close_attempted=false`、`presentation_close_rpc_issued=false`、portable refusal reason、driver teardown failure reason，以及 presentation/launch-root/foreground/supervisor retention；不能以“最终可能由 terminal 带走”为由继续破坏性关闭。进入 exact close action 但窗口已不可观测时，`presentation_close_attempted=true` 而 `presentation_close_rpc_issued=false`，与拒绝 close 和已发 RPC 两种状态保持可区分。create response id 若与 before/preexisting id 冲突，同样不关闭 ambiguous handle，直接 unresolved/fail closed。初始化中其他已取得唯一 exact create response 的失败路径，生产编排必须先 quiesce owned processes，再调用 exact response close。

`presentation_close_rpc_issued=true` 只表示 Harness 已发起 `async_close` 调用；该调用仍可能 timeout 或失败，不能据此推导 iTerm 已接收动作，更不能替代 `exact_close_rpc_succeeded=true`。

cleanup orchestration 通过可注入 driver/presentation 的回归证明该编排代码执行 `quiesce → close → finish`，而不是检查源码字符串；测试产出的 cleanup entry 必须实际通过 verifier。binding、observation、cleanup、cleanup entry 与 driver cleanup 都按 exact key set 校验，防止 producer/verifier schema 静默漂移；关键 cleanup verifier 子句另有参数化负测。

verifier 只有在 private ACK/binding 中的 owned supervisor、launch-root 与 accepted foreground job 都已消失时才移除 disposable install root。若 bounded wait 后仍有 live owned process，或 process observation 无法安全解析，必须保留 private run root 并写 0600 `retained-scene.json`，其中列出具名原因、live owned PID 和 unresolved 状态；下一次 preflight 必须识别该 marker，提示人工处理 owned process 与清理裁决，而不是把现场当成普通可删残留。

producer 与 verifier 共享唯一的 `SUPERVISOR_CONFIG_FILENAME`。只要 producer 已写入该 config、但对应 private ACK 尚未出现，process observation 就必须判定为 unresolved 并保留现场；测试直接使用 producer 的真实文件名构造该分支，防止两侧字符串漂移把 live/unresolved scene 当成可删除残留。

该 remediation 改变 identity producer/verifier material 后，已在 fixed target `b7496fa9476cdc2bf14c2b53efad6a21e19c70e8` 重签正式 evidence：run `spike-tui-id-20260810T020958Z-d1360e5ca931`；evidence `b8089f75f372552d95896726c9226509482e5eb23ef5a2d3eb6602e03b24263b`；fingerprint `d1360e5ca931c12541b06d4f27a62fe628c89eb653add15c4b7df61eebbde566`。implementation review `20260810-100859-5i73il` 与 closeout review `20260810-102004-m3y4ob` 均为 P0=0、P1=0、`can_commit_push`；tracked refresh 条件 1–6 已闭合。

## 与 2026-08-09 create timeout 的关系

先前 exact-session prototype 在 experimental Codex remote/App Server attach 流程中 fail closed 于 `runtime.codex.iterm_window_create_timeout` / `iterm_window_not_created`。本次裁决没有移除 exact presentation window，也没有把调大 timeout 或重试描述成 root fix。

新 witness 改为由 iTerm create command 直接启动 Harness supervisor，再由 supervisor 启动普通 vendor TUI；因此它不再行使旧的 App Server/remote-resume create flow。这是用户裁决后的**显式覆盖变化**，不是对旧 timeout 根因的事实修复。窗口 create、exact create-response identity、ownership marker 与 Harness-owned foreground descendant binding 仍须真实通过，否则 gate 继续 fail closed。

来源：`evidence`（[IMPLEMENTATION_PROGRESS.md](../IMPLEMENTATION_PROGRESS.md) 的 `04ed4e73...` 首次正式 witness 记录；回归入口 `tests/test_ai_collab_tui_identity_spike.py::test_iterm_owned_close_retries_only_exact_marked_window`）。对已经取得 exact create response 且 ownership marker round-trip 成功的窗口，cleanup 可以在一次 iTerm RPC 异常后对**同一个 exact owned handle**做一次 bounded close retry，并在 result 中披露 `presentation_close_retry_used`。这是资源清理 mitigation，不是 create-timeout root fix，也不授权用窗口差集、位置或 participant 名猜测未返回 identity 的窗口。旧 `444f9fe6...` 与 `699c47d3...` receipt 都含有 model-plane absence⇒closed 的缺口；`db230da2...` 与 `09e10779...` receipt 虽是有效机器观测，却分别因后续 peer review 的 P1 未关闭而不能完成 gate。其 run、digest、fingerprint 与 review 留痕均见进度账本。

## 验证入口

只读 preflight，不启动 runtime、不创建窗口：

```bash
python scripts/verify_ai_collab_tui_identity_spike.py --preflight-only
```

纯 contract/receipt 测试，不连接 iTerm2：

```bash
python -m pytest tests/test_ai_collab_tui_identity_spike.py -q
```

固定并 push commit 后运行正式 witness：

```bash
python scripts/verify_ai_collab_tui_identity_spike.py \
  --expected-edgestudio-sha <40-char-pushed-sha>
```

adapter 使用与 `SPIKE-ITERM-001` 相同的 pinned `iterm2` wheel lock 和 private disposable environment。正常 cleanup 证明 owned process 已消失后，private config、launcher、ACK、PID 和 invocation settings 整体移入 Trash；若 owned process 仍存活或状态 unresolved，则保留私有现场与 marker，等待人工裁决。immutable receipt 不包含这些值。

## 明确不证明

- 任何 vendor session identity capability；
- Codex/Claude prior-history exact resume、context continuity 或 crash/restart recovery；
- mailbox/business delivery ACK、retry 或 cross-delivery；
- runtime process tree 的正式 drain/kill/fencing 与完整 close semantics；
- display geometry、Spaces 或多显示器 restore；
- 两个真实 driver 是产品支持数量上限；
- 模型质量、效率或协作收益已经改善。

公开接口边界：

- vendor 普通 CLI 是 driver-local launch surface，不把 experimental/private session lifecycle API提升为 Host dependency；
- [iTerm2 Window API](https://iterm2.com/python-api/window.html) 提供独立顶层 window create/close 与 stable window identity；
- iTerm session `jobPid` 只用于观察 exact create-response session 的当前 foreground job；ownership 必须由 supervisor ACK、launch-root parent chain 与 owned process group 联合证明，`jobPid` 本身不作为 participant identity。

## 2026-08-12 foreground binding 事实修正

来源：`evidence`（在 fixed target `adfb83e2a2f77eb6ffd88e345cd7e364afa5f999` 上从 Layer 1 两次重建，Layers 1–6 均通过，Layer 7 两次稳定失败于 `runtime.codex.iterm_job_pid_supervisor_mismatch`；第二次运行已包含 12 秒 exact-PID convergence polling，因此否定单纯 observation race）；`user_decision`（2026-08-12，批准 descendant-chain 修正与完整九层重建）。

iTerm 官方变量语义中 `jobPid` 是当前 foreground job PID，不是 terminal session 的 Harness supervisor PID。Codex 当前普通 CLI 入口还是会继续 spawn native TUI 的 Node launcher，构成 `supervisor → launch-root wrapper → foreground native descendant` 的实际反例；Claude 或其他 driver 的打包层数可以不同，不能成为 Host 分支。旧 `jobPid == supervisor PID` 约束因此是事实模型错误；延长 timeout 不能修复它，且此前按该字段签出的历史 receipt 不得用于本次 current rebuild。

本修正不改变“由 Harness 启动并拥有 participant process”的产品目标，也不引入 vendor session/API dependency。它把 Phase -1 contract 对齐到已运行的 M3/M4 driver 原则：Host 绑定 Harness-owned process chain 与 exact presentation，供应商 wrapper/descendant 层级属于 driver-private observation。

fixed implementation `fdc74bf68410f679631e23617b2b09dbc2b2167d` 已由 review `20260812-095045-0fx3vm` 以 P0=0、P1=0、P2=0、`can_commit_push` 闭合并进入 `main`。正式 TUI identity run `spike-tui-id-20260812T021100Z-8fff6d0e265b` 通过，evidence `0408c524884f92b9aa573a475b85ac2801b84ea28ba00aaa3848087baacdcef5`，fingerprint `8fff6d0e265b77d5fcb46288b14c1c12adcbeb7cccd833997f82cbd1be0e6f06`；其 downstream lifecycle、topology、close、delivery 与 11-gate aggregate 均在同一九层 rebuild 中通过。完整索引和隔离验证环境边界见 [IMPLEMENTATION_PROGRESS.md](../IMPLEMENTATION_PROGRESS.md)。

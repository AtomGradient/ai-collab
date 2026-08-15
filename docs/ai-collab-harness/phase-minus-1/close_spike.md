# SPIKE-CLOSE-001 — Exact process close fencing feasibility

> 状态：`completed`；fixed implementation、正式 machine receipt、独立复核、tracked closeout ledger 与 terminal peer review 已全部闭环
>
> Scope 来源：`user_decision`（2026-08-10）。用户批准 Codex/Claude 的共同推荐：真实 disposable local processes + deterministic state/journal；短 deterministic timeout 只作 witness driver、不定义产品 SLA；明确确认由独立 `force_stop` 调用表达；WIP 覆盖 HEAD、refs、index、staged、unstaged 与 untracked 内容。

## Gate 语义

registry 将本 gate 定义为 `close_fencing_feasibility`。它验证的是 close 的 process ownership、participant generation、operation ID、fencing token、bounded drain 与 fail-closed state semantics，不重复把真实 iTerm presentation 或任一 model CLI 当成 required dependency。

本轮使用 6 个真实 disposable Python worker，加一个 deterministic `CloseStateJournal`。真实进程负责证明 signal、存活、drain progress、exact stop 与 sibling isolation；state/journal adapter 负责证明 CAS/fencing、closed-world transition 和 stale controller 无副作用退出。它不是 Phase 2/4 的正式 Host/database，也不定义 UI 或产品级时间 SLA。

## Direct dependencies

receipt 的 direct dependency keys 必须精确为：

1. `SPIKE-TUI-ID-001`：提供 Harness-owned process identity、exact targeting 与真实 process/presentation cleanup 边界；
2. `SPIKE-TUI-LIFE-001`：提供 participant generation、跨 generation cleanup ordering 与 identity dependency binding。

`SPIKE-ITERM-001` 已由 TUI-ID 的既有链承接；delivery、storage、topology、Host、IPC 与 `DOD-SCOPE-001` 不向 close producer 提供直接输入，因此不能被自动收编为 direct dependency。CLOSE 不发送 mailbox 消息，也不调用真实 presentation 或 supplier API。

## 必须证明的 cases

### Idle

- exact operation identity 同时绑定 scenario、participant ref、generation、operation ID、fencing-token digest 与 process-identity digest；
- 只向 exact owned PID 发 graceful stop；
- 只有独立观测该 exact process absent 后，state 才能从 `closing` finalize 为 `closed`。

### Busy / bounded drain

- 真实 busy worker 接收 drain 请求并产生可观测 progress；
- witness 使用固定 step count 驱动 bounded wait，elapsed 不参与 pass/fail；
- drain complete 且 exact process absent 后才进入 `closed`。

### Timeout → explicit force stop

- timeout boundary 前后必须独立证明 target 仍存活，且没有自动 kill；
- 错误 operation ID 或 fencing token 的 finalize 都在 state/journal mutation 前拒绝；
- separate `force_stop` 调用携带正确 operation/generation/fencing 与 explicit confirmation 后，才可精确停止 target；
- sibling 必须保持存活且 event digest 不变。

本短 deterministic timeout 仅让测试可复现，不是产品 SLA、全局常量或用户等待时长承诺。

### Unknown

- 无法可靠观察 process state 时，不发送猜测性 signal；
- target 保持存活，state 进入 `degraded`；
- receipt 必须显式 `close_success_claimed=false`，不得由 absence、timeout 或 producer 自报推导成功。

### Stale generation / controller

- generation-1 请求指向 generation-2 participant 时必须在 durable state/journal/process event mutation 前拒绝；
- stale controller 自动退出，当前 generation process 保持存活；
- state adapter 对 generation、operation、fencing token 与 process identity 做 exact equality，不按 role、cwd、最近 process 或 supplier session 猜测。

## WIP preservation

producer 在 private run root 内创建一个真实 Git worktree，先形成并验证：

- 已提交 base 与额外 branch/ref；
- unstaged tracked modification；
- staged file；
- untracked file。

所有 worker 都以该 dirty worktree 为 cwd。close、drain、timeout 与 force-stop 全部结束后，producer 与 verifier 分别重算 HEAD、porcelain-v2 status、完整 refs、index digest 和 worktree file-content digest；staged/unstaged/untracked 三类 WIP 都必须仍存在且摘要完全相等。同时 current EdgeStudio canonical repo 的同类 snapshot 也必须不变。

## Private process evidence 与 cleanup

- state root/run root/process artifacts 使用 `0700/0600`；private plan/state 可以短暂保存 PID、start signature、owner token 和绝对路径；
- public result/receipt 只保存 process identity digest、布尔 witness、closed-world state、计数与摘要，不保存 PID、token、raw path 或 credential；
- producer timeout/interruption 使用独立 process group 终止整组 disposable workers；正常成功后 verifier 从 private state 独立重算 real process liveness 与 event sequence；
- 正式验证结束后，verifier 只凭 exact parent/name/nonce marker 和 PID/start/token identity 清理仍存活的 unknown/stale/sibling workers及 owned run root；identity 不匹配或 cleanup 不完整时 fail closed，不写 passed receipt。

## 明确不证明

本 spike 不证明真实 Host durable database/journal 或 crash recovery、production workload busy detection、literal human confirmation UI、real concurrent multi-participant/cross-scenario close coordination、resource lease/heartbeat/boot-ID/physical-device fencing、真实 presentation close、destroy、rollback 或 Phase 4 acceptance。真实 presentation ordering 与 generation cleanup 只通过已重验的 direct dependency evidence 输入，不在本 gate 重跑 supplier CLI。

## 正式 witness（2026-08-10）

- fixed implementation：`ea035701ad5bda87ee2ad5fe22fb763cb390ed6c`，包含初始 target `b0027d1716b0b9d6a2d20f96802f0e564437c716`；`main` clean 且已 push；
- implementation review：Claude 初审 `20260810-165530-q8nvwr` 给出 P0=0、P1=1、`needs_fix`；supplier-token source guard 修复后，复审 `20260810-170530-w3nfqr` 给出 P0=0、P1=0、`can_commit_push`；
- closeout ledger：`5ad2d9add61562363abfeeec4ab259e81c2ddca2`；terminal review `20260810-171530-r4nwqp` 独立重算 evidence digest/fingerprint、current view、权限、checkout 与 dependency set，给出 P0=0、P1=0、P2=0、`can_commit_push`，明确确认条件 6 成立；
- run：`spike-close-20260810T102109Z-e0945ad742a2`；evidence SHA-256 `d301226995f55989da2fc95f5bee1dd9e4f6af8a8b344e7a9f61ad103c41ff82`；input fingerprint `e0945ad742a2dcac41c711e7f3b755571c59402d936f4a3d85dead280e7ef013`；
- formal result 记录 6 个真实 process、9 条 contiguous journal events（digest `5136a3b80917c3978b377720d881fab9afa34745b70d941ff17722353d2542e8`）；idle/busy/timeout exact target 最终 absent，unknown/stale/sibling 在 verifier 独立重算时仍存活，随后 cleanup 精确清除；
- timeout case 证明 force-stop 前 target alive、`auto_kill_before_confirmation=false`，错误 operation/fencing token 均无 state mutation，separate confirmed force-stop 后只有 target absent、sibling event digest 不变；unknown 明确 `close_success_claimed=false`，stale generation 在 journal/process mutation 前拒绝；
- producer/verifier 双重重算确认 canonical snapshot 与 dirty worktree HEAD/refs/index/staged/unstaged/untracked 全部不变；source guard 的 supplier-specific token count=0；receipt 中没有 PID、owner token、绝对路径、credential 或 supplier API；
- 独立复核确认 current view 对 immutable evidence 的 path/digest/fingerprint 引用闭合，receipt/current 为 `0600`、state/receipt parents 为 `0700`，checkout commit=remote_commit，dependency keys 精确为 `[SPIKE-TUI-ID-001, SPIKE-TUI-LIFE-001]`，`verifier.params={}`，owned worker/root 均 absent，retained process/bytes=0。

implementation review 的非阻塞 P2 保留为 acceptance/hardening 输入：PID/start observation 存在低概率 TOCTOU，部分布尔/limitation 是 survival-implies-true，formal witness 不证明真实并发 Host close、durable DB、resource lease、destroy 或 rollback。这些残余风险不改变本次 verifier 的独立 process/WIP/journal 重算或 P0/P1 verdict。

## Closeout 条件

只有以下条件全部满足后才能标记 `completed`：

1. implementation 固定、clean、push：已完成；
2. Claude 对固定 SHA 给出 `P0=0`、`P1=0`、`can_commit_push`：已完成；
3. 在同一 fixed SHA 上运行正式 real-process witness 并写入 immutable evidence/current view：已完成；
4. 独立复核 evidence/current digest、fingerprint、dependency freshness、real process event/liveness、journal、WIP、`0700/0600` 权限与 retained=0 cleanup：已完成；
5. tracked progress/handoff 记录实现 SHA、review、run/evidence/fingerprint 与下一 required gate：由 closeout ledger `5ad2d9add61562363abfeeec4ab259e81c2ddca2` 完成；
6. closeout target 再取得无阻塞 terminal peer review：已由 `20260810-171530-r4nwqp` 完成。

## 固定 commit 后运行

只读 preflight，不启动 process、不发送 signal、不创建 dirty worktree：

```bash
python scripts/verify_ai_collab_close_spike.py --preflight-only
```

正式 witness：

```bash
python scripts/verify_ai_collab_close_spike.py \
  --expected-edgestudio-sha <40-char-sha>
```

contract/mutation/real-process integration tests：

```bash
python -m pytest tests/test_ai_collab_close_spike.py -q
```

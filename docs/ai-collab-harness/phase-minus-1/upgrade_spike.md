# SPIKE-UPGRADE-001 — Disposable component and schema migration feasibility

> 状态：`completed`；fixed implementation、正式 machine receipt、独立复核、tracked closeout ledger 与 terminal peer review 已全部闭环
>
> Scope 来源：`user_decision`（2026-08-10）。用户批准 real disposable v1/v2 local components、Unix domain socket typed protocol、representative closed-world schema、zero direct dependency、failure rollback、committed rollback with monotonic epoch/new generation、dry-run、WIP preservation 与总架构 evidence hygiene 固化。

## Gate 语义

registry 将本 gate 定义为 `upgrade_migration_feasibility`。它验证运行中的本地 component set 能否在明确版本、schema、operation 与 fencing 边界内从 v1 迁移到 v2、拒绝旧 component/generation、从 journal 重建，并在失败或显式 committed rollback 后恢复到可运行的 v1-compatible release；它不是只修改 `schema_version` 的文件转换测试。

本 gate 仍属于 Phase -1 disposable feasibility。v1/v2 是 Harness 自有、结构独立的 local component fixtures，不是真实 macOS App package、SMAppService background item、NSXPC product protocol 或已冻结的 production schema。receipt 必须显式保存该 evidence tier，禁止把通过结果外推为正式产品升级已经可用。

## Direct dependencies

receipt 的 direct dependency keys 必须精确为 `[]`：

- probe 不消费任何既有 gate receipt 作为输入；component handoff、typed request、generation fencing、migration、rollback、WIP 与 cleanup 都由本 witness 独立观察；
- CLOSE/IPC/HOST 的实现经验可以作为设计输入，但不是 evidence dependency；尤其不能通过 CLOSE→TUI-LIFE→TUI-ID→ITERM 把 Codex、Claude 或 iTerm material 纳入 UPGRADE freshness；
- 空 direct dependency 不改变 workflow 顺序。`DOD-SPIKES-001` 仍独立要求所有 Phase -1 required gates 当前通过。

## Artifact independence 与 anti-self-proof

v1/v2 必须满足：

1. entry point 来自不同 source path，分别 materialize 为独立 executable artifact；不得由同一 runtime entry 用 `--version`、环境变量或布尔分支切换；
2. source/artifact SHA-256 均不同，manifest 记录 source→artifact equality；同一 source 重复 materialize 的 digest 必须相等；
3. 两版嵌入不同的固定 release、protocol 与 state schema，v1 validator 必须拒绝 v2 state；
4. 共享代码仅允许无版本语义的标准库/底层工具，verifier 必须枚举 source set、拒绝两个版本互相 import；
5. producer 与 verifier 分离。verifier 从 private raw artifacts、client request/reply、state、journal、process identity/liveness 与 Git snapshot 重算，不把 producer bool 当作事实；
6. mutation/meta tests 必须杀死 version-only migration、old Host retained、stale v1 accepted、epoch/generation regression、partial activation、WIP drift、privacy leak 与 producer lie。

## Real local component witness

fixture 使用真实 OS processes，但不注册系统 service：

- v1/v2 Host-role process 分别监听 run-scoped Unix socket；
- v1/v2 CLI-role 与 App-role client 以独立 process 发出 version-aware typed request；
- participant worker 携带 migration epoch、participant generation 与 owner identity；
- 所有长期进程位于同一 owned process group 和 disposable dirty Git workspace，verifier 使用 PID start identity + owner token 精确观测和清理。

必须覆盖：

### Dry-run 与 failure rollback

- 从 explicit v1 schema 生成 deterministic migration plan；dry-run 前后 current state byte-identical；
- 在 v2 candidate state 已写入的注入失败点执行 rollback；恢复后的 v1 state 与 pre-migration backup byte-identical；
- failure rollback 不启动 v2 Host/participant，不改变 v1 process binding。

### Forward migration 与 reconcile

- v1 Host、CLI/App client 与 generation-1 participant 先真实运行；
- backup 先于任何 committed v2 state，atomic activation 不暴露 partial current pointer；
- v2 Host 与 generation-2 participant 真实启动，v1 Host/participant 最终 exact absent；
- v2 CLI/App request 成功；v1 protocol request、旧 migration epoch 与旧 participant generation 均在 state/journal mutation 前拒绝；
- 清空 producer in-memory projection 后，从 raw current state + journal 重建相同 digest，证明 reconcile 不是复用旧对象。

### Committed rollback

- 已激活且可服务的 v2 必须能 rollback 到 v1-compatible state/component set；
- rollback 不恢复旧 epoch/generation：migration epoch 单调递增，建立新的 participant generation，并保留 v1→v2→v1 binding history；
- v2 Host/participant exact absent，新的 v1 Host/participant live；旧 v1/v2 client、epoch 或 generation 均 fail closed；
- rollback 后再次重启 Host-role process并从 disk/journal 重建，不能依靠进程内缓存。

## Representative schema 与 WIP

fixture schema 至少表达 scenario identity、participant identity、component release/protocol、migration epoch、participant generation、desired/observed state、binding history 与 operation journal。它必须有真实 v1/v2 structural difference，但明确不是 Phase 0 frozen production schema。

producer 在 private run root 创建独立 Git repository，形成 HEAD/extra refs/index/staged/unstaged/untracked 内容；所有 Host/participant 以它为 cwd。producer 与 verifier 分别重算完整 snapshot，同时重算 canonical EdgeStudio repo 的只读 snapshot。任何 ref、index、内容或 status drift 都 fail closed。

## Pollution、privacy 与 cleanup

- run root/artifacts/state/journal 使用唯一 owner marker，directories `0700`、files `0600`；为满足 macOS Unix socket path 长度限制，socket 使用 `/tmp` 下单独的短 run-owned root，并由相同 owner token、权限和 exact-cleanup 约束；不使用 `.ai-mailbox`、真实 Harness project/scenario registry、legacy pane state、系统 background item 或 canonical Git worktree；
- public result/receipt 不保存 PID、owner token、socket/path、credential、消息正文或 raw session identity，只保存 digest、计数、portable enum 与独立 claim；
- verifier source guard 禁止 SMAppService、NSXPC、iTerm/AppleScript、Codex/Claude/vendor session surface 与 private store scan；
- producer timeout/interruption 清理整个 owned process group；正常路径由 verifier exact-cleanup。身份不匹配或残留进程时 fail closed，并保留隔离 scene，不扫描或终止其他进程。

receipt 必须显式记录：

```text
witness_kind=disposable_local_component
real_macos_app_upgrade_invoked=false
production_schema_used=false
vendor_api_invoked=false
```

## 明确不证明

本 spike 不证明真实 App UI、installer/App bundle replacement、SMAppService/launchd/NSXPC upgrade、签名轮换/notarization、production DB/schema、并发 upgrade controller、真实 clock drift、disk-full/crash-at-every-instruction、in-flight delivery、runtime/presentation continuity、canonical workspace migration、跨机器 migration、`ACC-MIGRATION-001` 或 `ACC-ROLLBACK-001`。这些不得由本 receipt 推导。

## 正式 witness（2026-08-10）

- fixed implementation：`238715cee8c7c31863db399b0a7bf2c52a816efb`；`main` clean、已 push，且 local/remote commit 相等；
- implementation review：Claude `20260810-185530-r3nvqr` 独立复核结构迁移、artifact independence、zero dependency、process handoff、rollback、WIP 与 evidence hygiene，给出 P0=0、P1=0、`can_commit_push`；
- closeout ledger：`a53a58857113241eed74d9006d00981e8023b177`；terminal review `20260810-191530-t5nvqr` 独立重算 evidence digest/fingerprint、current view、权限、checkout 与 dependency set，给出 P0=0、P1=0、P2=0、`can_commit_push`，明确确认条件 6 成立；
- run：`spike-upgrade-20260810T114426Z-3b6b424742a3`；evidence SHA-256 `8998cda5d12dba076448e66b68345d6e8b2319ad285229dcb013404737fcfb68`；input fingerprint `3b6b424742a378783d1d57e6f3a66f736ab8db5f1bb2ed847eb9a5ecb8affde0`；
- formal result 记录两个独立 source/artifact digests、7 个真实长期 processes、14 个真实 clients 与 13 条 migration journal events；v1→v2→v1 binding history 的 epoch/generation 精确为 1→2→3，旧 component 最终 exact absent；
- verifier 从 raw artifact、request/reply + Host event join、state、journal、PID start identity/liveness 和 Git snapshot 独立重算；dry-run mutation-free、failure rollback byte-identical、committed rollback 与 restart reconcile 均通过；
- canonical repo 与 disposable dirty repo 的 HEAD/refs/index/staged/unstaged/untracked snapshot 前后相等；receipt direct dependencies 精确为 `[]`、`verifier.params={}`，并显式记录未调用 vendor API、未注册 platform service、未使用 production schema；
- 独立复核确认 current view 对 immutable evidence 的 run/digest/fingerprint 引用闭合，fingerprint 可由 canonical `fingerprint_inputs` 重算，receipt/current 为 `0600`、state/receipt parents 为 `0700`，public evidence 不含 PID、owner token、socket/绝对路径或 mailbox 内容；7 个长期 processes、14 个 clients、run root 与短 socket root retained 均为 0；
- 验证：升级专项 29 passed，完整 AI-collab Harness 563 passed，`py_compile`、`git diff --check` 与只读 preflight 均通过；preflight 未启动 process、创建 socket/dirty worktree 或调用 vendor/platform service。

implementation review 的非阻塞 P2 保留为后续 hardening 输入：component response 中 `state_mutated=false` 是静态字段，但 verifier 已用 before/after state digest 独立判定；成功 producer 会暂留最终两个进程供 verifier 观察，异常路径的 best-effort cleanup 仍有极低概率保留隔离现场。PID `lstart` 的秒级精度、并发 client、producer timeout、socket cleanup failure 与更完整 crash matrix 不由本 witness 证明。

## Closeout 条件

只有以下条件全部满足后才能标记 `completed`：

1. implementation 固定、clean、push：已完成；
2. Claude 对固定 SHA 给出 `P0=0`、`P1=0`、`can_commit_push`：已完成；
3. 同一 fixed SHA 运行正式 real-process witness 并写入 immutable evidence/current view：已完成；
4. 独立复核 evidence/current digest、fingerprint、zero dependency、artifact independence、raw state/journal/process/WIP、`0700/0600`、privacy 与 retained=0 cleanup：已完成；
5. tracked progress/handoff 记录实现 SHA、review、run/evidence/fingerprint 与 Phase -1 下一状态：由 closeout ledger `a53a58857113241eed74d9006d00981e8023b177` 完成；
6. closeout target 再取得无阻塞 terminal peer review：已由 `20260810-191530-t5nvqr` 完成。

## 固定 commit 后运行

```bash
python scripts/verify_ai_collab_upgrade_spike.py --preflight-only
python scripts/verify_ai_collab_upgrade_spike.py \
  --expected-edgestudio-sha <40-char-pushed-sha>
python -m pytest tests/test_ai_collab_upgrade_spike.py -q
```

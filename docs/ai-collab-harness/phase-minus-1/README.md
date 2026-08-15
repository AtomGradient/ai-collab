# Phase -1 Disposable Feasibility Spikes

Phase -1 在 EdgeStudio 内保存可丢弃 prototype、verifier 和 immutable receipt，不创建独立产品仓库，不修改 canonical workspace。本文档只描述入口与证据边界；每个 gate 的 current 状态以本机 `receipts/gates/<gate-id>.json` 为准，文档或测试输出不等于 passed。

## 当前入口

- [host_install_spike.md](host_install_spike.md)：`SPIKE-HOST-001`，验证稳定签名的当前用户级 macOS LaunchAgent 可被 `SMAppService` 注册、由 launchd 监督重启、异步注销并完整清理。
- [ipc_spike.md](ipc_spike.md)：`SPIKE-IPC-001`，验证 launchd Mach service 上的 typed NSXPC request/reply、双向 code-signing requirement、当前用户校验、operation allowlist、scenario capability 与 generation fencing。
- [iterm_presentation_spike.md](iterm_presentation_spike.md)：`SPIKE-ITERM-001`，验证 iTerm2 官方 Python API、动态 participant 到独立顶层 window 的一对一 binding、headless 零新增窗口、双进程无提示权限预检、local-only authenticated transport、稳定 window/tab/session identity、ownership marker 与非空既有窗口隔离见证。
- [runtime_driver_contract_spike.md](runtime_driver_contract_spike.md)：`SPIKE-RUNTIME-DRIVER-001`，验证 Host 仅按 versioned capability 与 registry 调度动态 participant，且 model binding 对 participant generation 不可变。
- [tui_identity_spike.md](tui_identity_spike.md)：`SPIKE-TUI-ID-001`，验证真实 Codex/Claude Code CLI 由 Harness-owned supervisor 启动，exact foreground job 是 launch-root 或其 PPID-chain-verified descendant，并与 iTerm create response 的 exact 顶层 window/tab/session 绑定；vendor session identity 与 exact resume 是可声明的 optional driver capability，当前 witness 只行使 explicit recreate baseline，receipt 仅保存 fingerprint。
- [tui_lifecycle_spike.md](tui_lifecycle_spike.md)：`SPIKE-TUI-LIFE-001`，验证相同 logical participant 在 generation 1 完整 cleanup 后可通过 `explicit_recreate` 建立 generation 2 的全新 process/presentation binding；`exact_resume` 仍是依赖 vendor session identity 声明的 optional capability，本 spike 不包含完整 add/detach/replace 或 crash/fault lifecycle。
- [delivery_spike.md](delivery_spike.md)：`SPIKE-DELIVERY-001`，以 deterministic disposable protocol witness 验证 canonical delivery states、exact binding/generation ACK、artifact≠delivered、restart reload、bounded retry/backoff 与同 runtime 多实例隔离；binding 来源取自已签 TUI evidence，不执行真实 pane 注入。
- [window_topology_spike.md](window_topology_spike.md)：`SPIKE-WINDOW-TOPOLOGY-001`，以公开 NSScreen arrangement fingerprint、真实 disposable iTerm exact integer Frame round-trip 与 deterministic synthetic reconcile 验证 geometry / presentation identity 分层、ephemeral composite-key isolation、lost-display fail-closed、单窗失败隔离和动态 N；不读取硬件 identity，不操作 Spaces 或物理 display。
- [storage_spike.md](storage_spike.md)：`SPIKE-STORAGE-001`，以 EdgeStudio/onboarding/edge-studio-dev 三仓 exact-SHA no-local clone 和 disposable cold/warm Python environment 验证 canonical isolation、editable path、private immutable wheelhouse 与 `<100 GiB` 峰值预算；elapsed 只测量，不调用 runtime vendor API，也不外推完整 manifest 或 Host database。
- [close_spike.md](close_spike.md)：`SPIKE-CLOSE-001`，以真实 disposable local processes 与 deterministic state/journal 验证 idle/busy/timeout/unknown、separate force-stop confirmation、generation/operation/fencing、exact target/sibling isolation 和 staged/unstaged/untracked WIP preservation；不调用真实 presentation 或 supplier API，也不外推 Host DB/resource/destroy/rollback acceptance。
- [upgrade_spike.md](upgrade_spike.md)：`SPIKE-UPGRADE-001`，以结构独立的 v1/v2 disposable local components、Unix socket typed protocol、representative schema、真实 process handoff、failure/committed rollback、journal reconcile 和完整 WIP preservation 验证 upgrade transaction feasibility；direct dependencies 为空，不调用 Codex/Claude/iTerm/vendor API，也不外推真实 macOS package 或 production migration acceptance。
- [spikes_aggregate.md](spikes_aggregate.md)：`DOD-SPIKES-001`，fixed evaluator 已从 registry 声明的 11 个 SPIKE current→immutable evidence projection 正式重算出 passed derived current view；不新增 producer、run ID 或 immutable pass evidence，dependency failure/drift 必须覆盖旧 passed current view。2026-08-12 descendant-chain rebuild 的 11/11 dependencies 均 passed+fresh，derived fingerprint 为 `63bad1d4644fa3c89cf5b74f32e6695ba11c5ac60469f49826ac87100e88e217`。

upgrade spike 是独立、自包含 gate，不由 Host 安装、IPC、iTerm presentation、runtime-driver、process/presentation identity、lifecycle、delivery、topology、storage 或 close witness 代替，也不把它们收编为 receipt direct dependency。`DOD-SPIKES-001` 仍从所有 Phase -1 gate 的 current state 独立聚合。

## 共同约束

- workflow phase prerequisite 仍由 registry/aggregate 检查；单个 spike 只把实际消费的 evidence 写入 direct dependency，`SPIKE-UPGRADE-001` 的精确 direct dependency set 为 `[]`；
- 只记录 logical path、版本、hash 和结构化结果，不记录 credential、聊天原文、完整 session identity 或本机绝对路径；
- 固定并 push 的 commit 才能生成 evidence；
- immutable run evidence 使用 `0600`，current view 使用原子替换，state root 使用 `0700`；
- 三个同时运行 participant 只是本轮资源预算，不是产品基数上限。

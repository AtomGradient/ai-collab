# Delivery ACK protocol feasibility spike

Gate：`SPIKE-DELIVERY-001`

裁决来源：`user_decision`（2026-08-10）——批准 Codex 与 Claude 的共同建议：本 gate 使用 deterministic disposable protocol witness，binding/generation 必须来自已签 TUI evidence；不新增真实 Codex/Claude pane 注入，并在 delivery closeout 前冻结 identity/lifecycle evidence material。

## Scope

本 gate 只证明 reliable-delivery protocol 的 feasibility。它不把 legacy PingAgent `.dispatched`、文件存在或一次真实 pane 注入冒充目标 delivery ACK，也不提前实现完整 Host acceptance。

registry 中本 gate 没有 `verifier_params`。verifier 必须精确镜像七行 gate block，并显式拒绝新增的未消费 params；同时独立 pin registry 顶层 `delivery_contract.canonical_states`。本 gate 的 required witness 来自该 canonical enum 与 `product_architecture.md` §8：

1. 状态闭世界且只允许 `queued → delivery_attempted → delivered → consumed` 与 bounded retry 所需的自环；其余迁移 fail closed；
2. artifact/sidecar 存在不等于 delivered，缺匹配 ACK 时必须停在 `delivery_attempted`；
3. target exact schema 包含 scenario、from/to participant、runtime binding、presentation binding 与 generation；
4. ACK 任一 target 字段不匹配时拒绝且状态不前进；
5. deterministic restart reload 后，恢复集合精确等于此前的 `queued ∪ delivery_attempted`；
6. retry 有上限、相对 monotonic backoff 非递减；超限只把目标 participant delivery 标为 degraded，兄弟 participant 仍可正常投递；
7. source guard 与运行期 witness 都禁止 role、recent session、other mailbox 或 cross-scenario fallback；
8. 同 runtime 多实例保持 exact target 隔离，A 的 ACK 不能满足 B；旧 generation 的真实 binding 也不能满足新 generation target。

每项必须同时有正向 witness 与负向 fail-closed 测试。第一条核心反模式测试是：artifact 与 sidecar 齐全但 ACK 缺失时，状态仍不得成为 `delivered`。

## Witness form and dependencies

主体是 deterministic disposable store/clock/transport witness，不启动真实 TUI、不创建窗口、不发送 model turn。protocol target 的 process/presentation fingerprint 与 generation 必须从当前机器已签且 source-fresh 的 `SPIKE-TUI-ID-001` / `SPIKE-TUI-LIFE-001` receipt 读取，不得自行编造。`SPIKE-HOST-001` 与 `SPIKE-IPC-001` receipt 作为 state-root 与 typed-peer feasibility 依赖复用；`LEGACY-DELIVERY-001` 只作迁移历史，不进入 target contract dependency 链。

正式 verifier 仍必须在 clean、已 push fixed target 上生成 immutable run evidence 与 current view，并复核摘要、`0700`/`0600` 权限、checkout/tree/remote equality 和 disposable store cleanup。deterministic 不等于无 receipt。

## Frozen material during this gate

从本裁决落地 commit 起，到 `SPIKE-DELIVERY-001` closeout 条件 1–6 闭合为止，禁止修改以下 identity/lifecycle evidence material：

- `docs/ai-collab-harness/edgestudio_gates.yaml`；
- `scripts/ai_collab_tui_identity_contract.py`；
- `scripts/ai_collab_tui_identity_spike.py`；
- `scripts/verify_ai_collab_tui_identity_spike.py`；
- `scripts/ai_collab_tui_lifecycle_spike.py`；
- `scripts/verify_ai_collab_tui_lifecycle_spike.py`；
- `scripts/ai_collab_iterm_adapter_lock.json`；
- `scripts/ai_collab_bootstrap_evidence.py`；
- `scripts/ai_collab_macos_automation_preflight.py`；
- `scripts/verify_ai_collab_iterm_spike.py`。

若安全修复必须触碰这些文件，应先停止 delivery 正式 evidence 流程、说明三张 receipt 的失效范围，并重新排期 identity 与 lifecycle 两次真机 witness；不得静默沿用旧 receipt。delivery closeout 后自动解除本开发期冻结，后续改动仍按正常 freshness 规则处理。

## Guard coverage order

delivery schema 会产生 `target`、`ack`、`entry`、`attempt` 与 `snapshot` 等嵌套局部 guard。通用 AST meta-test 必须扫描选定局部变量的 literal `.get("field")`，并要求每个字段存在实际执行的负向 mutation fixture；新增 guard 却未新增 mutation 时测试必须变红。该基础设施先于 delivery producer/verifier 落地。

## Fixed implementation and formal evidence

当前 closeout 状态为 `completed`：

- guard foundation `d4c4b3525aa338aeed2208ece8730574690d9cf5` 先于状态机落地；local receiver 自动发现 hardening 为 `b2af11afa508c4feb82a24888464021a9b825167`，Claude review `20260810-130215-r7kxmq` 给出 P0=0、P1=0、`can_commit_push`；
- fixed implementation `ad4dbc32fa0b8ed6546656493ad5eebee9f39ade`；Claude implementation review `20260810-132845-d7rmpq` 给出 P0=0、P1=0、`can_commit_push`，独立确认 7/7 mutation attacks killed；
- formal run `spike-delivery-20260810T050333Z-4fba2e8623e6`；evidence SHA-256 `0db3af4082ce79292ffb93985c04064e3142d811114b033fcceb9bc340696399`；input fingerprint `4fba2e8623e670fcb0db9d1006aeaa42362283b39488ffc068fbb83918e9b3db`；
- receipt/current digest、`0600` 文件、`0700` state 目录、checkout/tree/remote equality、四依赖 freshness、verifier material、完整 journal witness 与 disposable cleanup 均已独立复核；
- delivery 146 tests 与 Harness 八文件 384 tests 通过；formal dry-run 与正式 run 的 input fingerprint 相同；adapter 双运行 result/environment/binding digest 相等；
- receipt dependency keys 精确为 HOST/IPC/TUI-ID/TUI-LIFE，`verifier.params={}`，且 `real_pane_or_vendor_api_invoked=false`。

closeout 条件 1–5 由 tracked target `e574391c534e23156a2278c2e5702cf4cecc4e06` 记录；Claude review `20260810-134215-k8cvpn` 以 P0=0、P1=0、P2=0、`can_commit_push` 终止式闭合条件 6。gate 已标记 `completed`，delivery 开发期冻结已解除，下一 required gate 为 `SPIKE-WINDOW-TOPOLOGY-001`。

Claude 的两个非阻塞 P2 明确保留：dependency validator 的 direct-get structural meta coverage 作为后续通用 acceptance verifier hardening；producer 的 `backoff_nondecreasing` 字段不作为独立证据，结论只依赖 verifier 对 journal `BACKOFF_SCHEDULE_NS` 与 strict elapsed ordering 的独立重算。

## 明确不证明

- 向真实 pane 注入文本或 vendor CLI 已实际收到内容；
- `consumed` 由真实 agent 消费；
- 全量 cross-delivery matrix 或所有 declared target；
- collaboration policy routing 或 ready gating；
- 真实 Host crash/kill 后 reconcile；
- 真实 IPC/网络故障、吞吐、延迟或效率收益。

这些分别留给 `ACC-DELIVERY-001`、`ACC-CROSS-DELIVERY-001`、`ACC-POLICY-ROUTING-001`、`ACC-READY-001` 与后续 fault/implementation acceptance。

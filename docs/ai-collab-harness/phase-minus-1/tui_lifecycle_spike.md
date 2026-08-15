# TUI continuity / explicit-recreate feasibility spike

Gate：`SPIKE-TUI-LIFE-001`

裁决来源：`user_decision`（2026-08-09）——按“最小供应商依赖、vendor session identity 为可选 capability”定义当前 continuity gate。

## Scope clarification

本 gate 的 `gate_id` 与 verifier 名称含有 `LIFE` / `runtime_lifecycle`，但 registry 参数只定义 continuity 语义。它是 Phase -1 的 `ACC-RESUME-001` 可行性孪生，不是完整 participant lifecycle/fault acceptance 的提前实现。

本 spike 只证明：同一个 logical participant 可以在 generation 1 的 Harness-owned process/exact presentation 完整清理后，以 `explicit_recreate` 创建 generation 2 的全新 process/presentation binding。它不声称恢复 prior conversation history，也不把“新建”冒充 `exact_resume`。

## Registry hard requirements

以下七项逐字来自 [edgestudio_gates.yaml](../edgestudio_gates.yaml)，属于本 gate 的 hard-required claims：

1. required continuity mode 包含 `explicit_recreate`；
2. `exact_resume` 只是 optional capability mode；
3. 声明 `exact_resume` 时必须同时声明 vendor session identity capability；
4. unsupported request 必须在 durable desired-state mutation 前拒绝；
5. 禁止 implicit recent-session fallback；
6. 禁止把失败的 resume 静默降级为 recreate 或其他 fallback；
7. 禁止 role、cwd、window、position 或 recent-session heuristic。

producer 把这组结论放在独立的 `registry_claims` 对象中；verifier 对 exact key/value 做 closed-world 比对，并把 `EXPECTED_GATE_BLOCK` 与 `VERIFIER_PARAMS` 双向镜像到当前 registry。当前实现不修改 gate registry 语义。

## Real witness

真实 witness 复用已审的 `SPIKE-TUI-ID-001` runner，而不复制另一套 vendor launch/cleanup 逻辑：

1. 用真实 Codex 与 Claude Code 普通 CLI driver 创建 generation 1；
2. 为两者建立 participant/generation + Harness process + exact iTerm presentation binding；
3. quiesce exact launch-root 与 accepted foreground job、成功执行 exact presentation close、确认 native presentation 不可见，并确认 launch-root/foreground/supervisor 均 absent；
4. generation 1 runner 返回后，lifecycle producer 先独立复核 cleanup exact schema、两条 binding/cleanup join、exact close、native absence 与 child/supervisor absence 等完整嵌套守卫；全部通过后才允许 generation 2 launch-started；
5. 对相同 participant/driver 以 `explicit_recreate` 创建 generation 2；
6. generation 2 重复同样 binding 与 cleanup proof；
7. verifier 要求两代共四个 process fingerprint 与四个 presentation fingerprint 均唯一，且同一 driver 的两代 process/presentation fingerprint 都不同。

事件账本必须严格为：`generation-1 launch → generation-1 cleanup → generation-2 launch → generation-2 cleanup`。每条事件携带从 probe 起点实测的相对 `monotonic_ns`；verifier 要求四个时间严格递增，并单独要求 generation 1 cleanup 时间早于 generation 2 launch 时间。ordinal 只描述预期序列，不能替代这条独立时间观测。每一代的完整子结果仍交给 identity verifier 的 closed-world schema、ACK、exact close、native audit、redaction 与 cleanup 守卫复核；lifecycle verifier 只在此基础上增加跨 generation continuity 判定。

receipt 只保存 SHA-256 fingerprint、generation、布尔 witness 与 portable metadata，不保存 PID、launch token、window/tab/session ID、vendor session identity、credential 或本机绝对路径。

## Optional exact-resume capability witness

纯 contract witness 使用两个 opaque capability descriptor：

- baseline descriptor 只声明 `explicit_recreate`；Host 必须在 desired-state mutation 前拒绝其 `exact_resume` 请求；
- optional descriptor 同时声明 vendor session identity 与 `exact_resume`，Host 可以接受该 request declaration；
- 缺 vendor session identity 的 `exact_resume` descriptor 在构造阶段 fail closed。

这只证明 capability admission 规则可表达、可拒绝，不调用 Codex/Claude session API，不读取 vendor session store，也不证明 exact resume 实际可用。当前两个真实 driver 仍只声明 `explicit_recreate`、`vendor_lifecycle_surface: none`。

## Strengthened but non-registry claims

以下内容来自 v3.2 product architecture 与本轮 evidence design，不冒充 registry 参数：

- 两个真实 driver 是 witness coverage，不是产品数量上限；
- `explicit_recreate` 必须建立新 generation、新 process binding 与新 presentation binding；
- generation 1 cleanup 必须发生在 generation 2 launch 之前；
- 禁止扫描 vendor private session store。

producer 将它们放在独立的 `strengthened_claims` 对象中。`forbid_implicit_recent_session_fallback` 当前存在于本 spike、但未出现在 acceptance 孪生 `ACC-RESUME-001`；这里只记录该 tracked 差异，不修改 acceptance registry，任何语义变更仍需用户裁决。

当前 witness 没有独立观察用户界面是否把这次操作标注或呈现为 “recreate”；因此该内容明确记录为 `user_facing_recreate_label_not_proven`，不再作为 producer 常量冒充已证明结论。

## Dependency freshness and retained scene

本 gate 必须消费当前机器 `SPIKE-TUI-ID-001` 的 passed evidence，并逐项重算 identity producer、verifier material、params 与整个 registry digest。dependency receipt 的 checkout commit 可以是当前 target 的祖先，只要该 target 是其后代且所有被 fingerprint 的 identity material 与 registry digest 仍逐项相同；这不是按 docs/tests-only 路径名放行。source/material digest 任一变化都会 fail closed 并要求重签 identity witness。

两代 private run root 都沿用 identity retained-scene 观测。verifier 只有在两代 private ACK/binding 中可解析的 owned launch-root/foreground/supervisor 均 absent 时才把 disposable root 移入 Trash；live 或 unresolved process 会保留 0700 private scene，并写 0600、closed-world 的 `retained-scene.json`，等待人工清理裁决。

## Formal evidence

来源：`evidence`（fixed target `b7496fa9476cdc2bf14c2b53efad6a21e19c70e8`；implementation review `20260810-100859-5i73il` 为 P0=0、P1=0、`can_commit_push`）。

- identity refresh：run `spike-tui-id-20260810T020958Z-d1360e5ca931`；evidence `b8089f75f372552d95896726c9226509482e5eb23ef5a2d3eb6602e03b24263b`；fingerprint `d1360e5ca931c12541b06d4f27a62fe628c89eb653add15c4b7df61eebbde566`；
- lifecycle witness：run `spike-tui-life-20260810T021123Z-58284f177941`；evidence `8307220b6dad7d568f9546d70bf23a7d2db2f3c6f418b19fd86e7beb207fea5f`；fingerprint `58284f1779416dd8fad398c7af6e86c4ea2703e3246c800a6a60e4c2f665ee04`。

两张 current/evidence 摘要、权限、checkout/tree/remote equality 与 private-root cleanup 已复核。lifecycle receipt 消费上述 source-fresh identity dependency，并证明四事件单调时序、两代全新 binding 与四条 owned cleanup。closeout target `9784c2cee09b13ec2683e8ebd806defd9727c6f9` 已由 review `20260810-102004-m3y4ob` 的 P0=0、P1=0、`can_commit_push` 终止式闭合，gate 状态为 `completed`。

事件中的 generation 1 cleanup 与 generation 2 launch 时间戳只相隔约 5.3 微秒，不能解读为资源清理和重建同时发生：cleanup runner 与完整嵌套守卫都在前一个时间戳前完成，下一次真实 launch 则发生在后一个时间戳之后。时间戳只证明 producer 观察到的顺序；资源已释放的实质证据仍来自 cleanup 守卫，两者互不替代。

## 明确不证明

- Codex/Claude prior-history exact resume、conversation context continuity 或 vendor session identity 可用性；
- crash/restart recovery 或 crash reconcile；
- participant add/detach/replace、desired/observed 七态、generation-fenced compound replace；
- participant fault isolation、degraded detach retry；
- delivery ACK/retry、window topology restore、storage、完整 close fencing 或 upgrade；
- 用户界面是否把 explicit recreate 标注或呈现为 recreate；
- 正式 Host/App/public API 已实现；
- 模型质量、协作效率或产品效果已改善。

完整 add/detach/replace/recover 由 `ACC-PARTICIPANT-LIFECYCLE-001` 证明；fault isolation 由 `ACC-PARTICIPANT-FAULT-001` 证明；最终 continuity acceptance 由 `ACC-RESUME-001` 证明。本 Phase -1 spike 不替代这些 gate。

## 验证入口

只读 preflight，不启动 runtime、不创建窗口：

```bash
python scripts/verify_ai_collab_tui_lifecycle_spike.py --preflight-only
```

纯 contract/receipt 测试，不连接 iTerm2：

```bash
python -m pytest \
  tests/test_ai_collab_tui_identity_spike.py \
  tests/test_ai_collab_tui_lifecycle_spike.py -q
```

固定并 push commit 后运行正式 witness：

```bash
python scripts/verify_ai_collab_tui_lifecycle_spike.py \
  --expected-edgestudio-sha <40-char-pushed-sha>
```

正式 run 会先后创建两组各两个 disposable 顶层窗口，不发送 model turn。失败时只按 portable reason 与 exact owned-resource evidence 处理，禁止从最近窗口、cwd、role 或 vendor private store 猜测。

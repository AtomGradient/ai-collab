# DOD-SPIKES-001 — Phase -1 derived aggregate

> 状态：`completed`；fixed evaluator、implementation review、正式 derived current view、独立复核、tracked closeout ledger 与 terminal peer review 已全部闭环
>
> Contract 来源：`evidence`。`edgestudio_gates.yaml` 已定义 `class=derived`、`evaluation=aggregate`、11 个 direct dependencies 与 `aggregate_policy=recompute_from_current_dependencies`；`product_architecture.md` 已规定 derived aggregate 不保存独立 pass evidence。

## Gate 语义

`DOD-SPIKES-001` 不是第 12 个 feasibility witness。它每次从 registry 声明的 11 个 Phase -1 SPIKE current views 重新计算当前聚合状态：只有所有 direct dependency current views 都指向摘要一致的 immutable evidence、状态为 `passed`，且 receipt 的 registry、producer、verifier material、params、fingerprint 与已记录 dependency evidence 仍一致时，aggregate current view 才能为 `passed`。

本 gate 不生成 `run_id`、`evidence_path`、`evidence_sha256` 或 `receipts/runs/.../DOD-SPIKES-001.json`。它只原子替换 `receipts/gates/DOD-SPIKES-001.json`；dependency 缺失、失败或漂移时，新的 `failed` current view 必须覆盖旧 `passed`，禁止保留陈旧成功投影。

## Exact direct dependencies

direct dependency set 与 registry 顺序精确为：

1. `SPIKE-HOST-001`
2. `SPIKE-IPC-001`
3. `SPIKE-ITERM-001`
4. `SPIKE-RUNTIME-DRIVER-001`
5. `SPIKE-TUI-ID-001`
6. `SPIKE-TUI-LIFE-001`
7. `SPIKE-DELIVERY-001`
8. `SPIKE-WINDOW-TOPOLOGY-001`
9. `SPIKE-STORAGE-001`
10. `SPIKE-CLOSE-001`
11. `SPIKE-UPGRADE-001`

Gate 0、legacy delivery、pre-implementation snapshot 与 PingAgent 不是本 aggregate 的新增 direct dependency。各 SPIKE receipt 已记录的 dependency evidence SHA 必须仍与对应 current view 一致；这提供传递性引用检查，但不会改变 registry dependency graph。额外 root audit 只能作为 supplementary diagnostic，不能进入 `DOD-SPIKES-001` required input 或 freshness fingerprint。

## Recompute 与 current view

evaluator 每次执行必须：

- exact parse aggregate policy、gate class/evaluation 与 11 个 dependency IDs；未知、重复、缺失或额外 dependency fail closed；
- 对每个 dependency 校验 current/evidence identity、`0600`、logical path、evidence SHA-256、status/run/fingerprint/timestamp equality；
- canonical 重算 receipt `input_fingerprint`，并要求 receipt registry digest 等于当前 registry；
- 从 closed source map 重算当前 EdgeStudio producer 与 receipt 枚举的 verifier files/digest/params，拒绝 source、params 或 material drift；
- 要求 receipt 已记录的 dependency evidence SHA 仍等于对应 dependency current view，且该 view 仍为 `passed`；
- 生成稳定 `aggregate_fingerprint = SHA-256(canonical(registry_digest, aggregate_policy, dependency_states))`；`computed_at` 不进入 fingerprint；
- 原子写入 mode `0600` 的 derived current view；state/receipt directories 继续要求 `0700`。

current view 至少包含：

```text
gate_id / gate_class=derived / evaluation=aggregate
status / computed_at / registry_digest
dependency_states[gate_id].status
dependency_states[gate_id].run_id
dependency_states[gate_id].evidence_sha256
dependency_states[gate_id].input_fingerprint
dependency_states[gate_id].source_material_fresh
aggregate_fingerprint / failure_reasons[]
```

`source_material_fresh` 只表示该 SPIKE receipt 枚举的 EdgeStudio producer/verifier source material 与 params 仍匹配，不表示 aggregate 重新执行 macOS/iTerm/vendor runtime/live process probe。动态平台或 runtime input 是否仍可复用，仍由对应 gate 的 fingerprint/invalidation 与 gate-specific verifier 负责；本 aggregate 不调用 Codex、Claude、iTerm API，也不把版本探测偷偷升级为 direct dependency。

## Formal recomputation

- fixed evaluator：`071bf3a75446f8e220269e65ef6d4d479f00e540`，clean、已 push，且 local/remote commit 相等；
- implementation review：Claude `20260810-194530-v7nvqr` 给出 P0=0、P1=0、P2=0、`can_commit_push`；独立运行 aggregate tests 32 passed，并确认 exact 11 dependencies、无 vendor API、只写 current view；
- closeout ledger：`ecefe3485d820f70f3ccd4eb58250c257f126723`；terminal review `20260810-200530-x9nvqr` 独立复核 current view、11/11 dependency projection、aggregate fingerprint、derived/no-evidence contract、权限与 registry debt 表述，给出 P0=0、P1=0、P2=0、`can_commit_push`，明确确认条件 6 成立；
- 正式重算：11/11 dependency states 均为 `passed` 且 `source_material_fresh=true`；`aggregate_fingerprint=4ac3c45f5d205b06790adc068165e5d9bcc79d5dceda0c765f865fd400ca0db0`；
- 独立复核：canonical fingerprint 重算一致，current view mode `0600`、state/receipts/gates directories mode `0700`，checkout 仍固定在 `071bf3a...` 且 clean；没有 `DOD-SPIKES-001` immutable run evidence，也没有 top-level `run_id`、`evidence_path` 或 `evidence_sha256`；
- 完整 Harness 在 fixed implementation 上 595 passed；formal recomputation 没有调用 vendor API 或平台服务。

## Phase 0 registry normalization history

2026-08-10 的 receipt-level 审计发现：`DOD-SCOPE-001` 以及除 `SPIKE-UPGRADE-001` 外的 10 个 Phase -1 SPIKE verifier/receipt 都实际消费了一个或多个 `dependency_evidence`，但 cutover 前相应 registry block 尚未声明 `depends_on`。当时的 DOD 结论未因此失真：每个 producer/verifier 已验证并记录这些 dependency SHA，本 aggregate 又要求已记录 SHA 与 dependency current view 一致且仍为 `passed`。Claude 在 `20260810-195530-w8nvqr` 对 SPIKE 层判断给出 P0=0、P1=0，明确同意 formal recomputation；DOD-SCOPE 的新增审计发现已通过 notice `20260810-204341-yvg5lv` 同步，纳入 Phase 0 fixed audit review。

用户随后于 2026-08-10 批准 direct cutover：补齐与 recorded evidence 精确一致的 11 个 gate declarations，同步 exact verifier mirrors/tests，保留 whole-registry digest，并在固定实现 P0/P1=0 后按 9 层顺序重建 13 immutable + 1 derived。cutover 前的正式 receipt 与 aggregate 保留为历史证据；新 registry 下必须重建，不能复制或继续投影为 current truth。

2026-08-11，fixed implementation `ec9a9b4e77c895803d4d8b63bb59145355a2537c` 经 review `20260811-102603-ck9asp` 的 P0=0、P1=0 verdict 后 fast-forward `main`；13 个 immutable gates 已按 9 层 DAG 重建，本 aggregate 随后重算为 11/11 `passed + source_material_fresh`，fingerprint `2f06b9703d9096ab20a1f1913a1f09d68ebd5188f490e90e16e72f5d8995197e`，仍只有 derived current view、无 immutable DOD evidence。machine audit 复核 registry mismatch 0、13 immutable + 1 derived、`state_mutated=false`；tracked Phase 0 closeout 尚待 terminal review。

## Mutation 与 fail-closed

tests 至少覆盖：

- 11 个 dependency 分别 missing；
- dependency status 非 passed、current/evidence digest 或 fingerprint 不一致；
- registry、producer、verifier material、params 与 recorded dependency evidence drift；
- current/evidence 权限错误、unsafe logical path、symlink；
- registry dependency 缺失、额外、重复；
- aggregate fingerprint lie、dependency projection 不完整；
- 旧 aggregate `passed` 在新一轮 dependency failure 后被 `failed` 覆盖；
- derived current view 不能被 verifier-evidence loader 当作 evidence pair，recompute 不新增 immutable run file。

## 明确不证明

本 gate 只证明本机 11 个 Phase -1 dependency current states 在该次 recomputation 时满足上述 integrity、status 与 source-material checks。它不重新执行各 SPIKE 的真实 process/window/environment witness，不证明当前任意 vendor CLI/API 版本、Phase 0 frozen schema、真实 App/Host、acceptance、release readiness 或跨机器状态；换机器必须重建该机器的 dependency evidence/current views 后再聚合。

## Closeout 条件

只有以下条件全部满足后才能标记 `completed`：

1. derived evaluator、tests 与本文形成 fixed clean/pushed commit；
2. Claude 对 fixed SHA 给出 `P0=0`、`P1=0`、`can_commit_push`；
3. 同一 fixed SHA 正式 recompute，仅写 derived current view、无 immutable DOD evidence；
4. 独立复核 aggregate fingerprint、11 个 dependency projections、`0700/0600`、privacy、failure overwrite 与 no-run-evidence；
5. tracked progress/handoff 记录 evaluator SHA、review、aggregate fingerprint 与 Phase -1 下一状态；
6. closeout target 再取得无阻塞 terminal peer review。

当前状态：条件 1–6 全部完成；条件 5 由 closeout ledger `ecefe3485d820f70f3ccd4eb58250c257f126723` 完成，条件 6 由 terminal review `20260810-200530-x9nvqr` 闭合。Phase -1 至此完成，下一 milestone 是由用户裁决 scope 与 registry normalization 后开始 Phase 0 contract freeze。

## 固定 commit 后运行

```bash
python scripts/verify_ai_collab_spikes_aggregate.py \
  --expected-edgestudio-sha <40-char-pushed-sha> \
  --preflight-only
python scripts/verify_ai_collab_spikes_aggregate.py \
  --expected-edgestudio-sha <40-char-pushed-sha>
python -m pytest tests/test_ai_collab_spikes_aggregate.py -q
```

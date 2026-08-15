# Phase 0 — Contract freeze

> 状态：`frozen`；registry normalization 与 A/B/C/D/E/F/G/H/I/J slices 已完整 closeout；M1–M4 working vertical slice 已完成；当前按用户裁决回收此前延期到 M4 dogfood 后的 fingerprint/hardening debt
>
> 授权来源：`user_decision`（2026-08-10 registry normalization；2026-08-11 批准统一 10-surface 方案并要求全速前进），历史审计与 cutover 记录见 [phase0_registry_normalization_20260810.json](../decisions/phase0_registry_normalization_20260810.json) / [phase0_registry_cutover_20260810.json](../decisions/phase0_registry_cutover_20260810.json)

Phase 0 按历史 v3 与 active v3.2 的同一主线冻结 10 个 contract surfaces：product contract envelope/version、project descriptor、typed IPC/capability/error、Scenario/Participant state/lifecycle/generation、Runtime/Presentation driver interface/registry/model binding、Collaboration Policy/routing、repo manifest、Workspace/Environment adapter plan/receipt、composed gate loader/digest/phase/projection，以及 permission/high-risk confirmation matrix。不得从本阶段外推独立产品仓、canonical workspace 迁移、真实 Host/App 实现或 acceptance/release。

## Contract surface map 与直接实施路线

来源：`user_decision`（2026-08-11）。用户要求回到最初 Harness 架构全速前进，批准取消 standalone inventory/audit milestone，覆盖与依赖矩阵只作为本 tracked 实施账本；真实 contract 分 slice 固定。`project_descriptor.yaml` 只保存稳定 project capability 与 artifact reference，不携带 `current_status`、target slice 或其他开发进度。

| ID | Phase 0 contract surface | 当前事实 | 候选后续顺序 |
|---|---|---|---|
| A | Product contract envelope/version | root descriptor 的 `3.2` machine-readable anchor 已由 `f1c4fc3baec001b0a4094a322a58b7de1b2e2648` / review `20260811-123709-8ov3rg` 闭合 | completed |
| B | Project descriptor | root `project_descriptor.yaml`、structural/cross-reference validator 与 tests 已由同一 fixed SHA/review 闭合；G slice 仅把 manifest reference 从 candidate 改为 frozen root artifact | completed |
| C | App/Host/CLI typed IPC、capability、error schema | fixed implementation `ee803a7046470f5ab1e4a7c62c1e011c87e4b7fd` / review `20260811-131310-1xnems` 已 P0=0、P1=0、P2=0 闭合并进入 `main` | completed |
| D | Scenario/Participant state、lifecycle、operation/generation | fixed implementation `2e5c1a274531625ef06f16d3210d66574f86b410` / superseding review `20260811-145313-5oxcxr` 已 P0=0、P1=0、P2=0 闭合并进入 `main` | completed |
| E | Runtime/Presentation driver interface、registry、model binding | fixed implementation `d544d38edec1d811cb6a4a0be4d66e7b572f4d67` / final review `20260811-133903-gf6yvb` 已 P0=0、P1=0、P2=0 闭合并进入 `main` | completed |
| F | Collaboration Policy/routing + reliable delivery | fixed implementation `fb480b314d408089ab119a41a6379e24ef5697ea` / review `20260811-152909-kqzrsx` 已 P0=0、P1=0、P2=0 闭合并进入 `main` | completed |
| G | Repo manifest | fixed implementation `130061b504d77ae78df58f4f20b9ba3d2aca6140` / review `20260811-125143-tutyow` 已 P0=0、P1=0、P2=0 闭合并进入 `main` | completed |
| H | Workspace/Environment adapter plan/receipt | fixed implementation `d1a86d72c560233bd1d8ec5a54f63809e5563d00` / review `20260811-163606-he0ysb` 已 P0=0、P1=0、P2=0 闭合并进入 `main` | completed |
| I | Gate registry loader/digest/phase/projection | fixed implementation `9fd883a2d968b5628e060e80a5fb8e60d72a5b7d` / review `20260811-171023-bw4xz8` 已 P0=0、P1=0、P2=0 闭合并进入 `main` | completed |
| J | Permission/high-risk confirmation matrix | fixed implementation `3bc693da01d5da7154563749fb9aa73857792e50` / review `20260811-174835-oveydb` 已 P0=0、P1=0、P2=0 闭合并进入 `main`；真实系统权限、trusted App UI 与 high-risk mechanics/acceptance 仍在 Phase 4 | completed |

批准的 contract freeze 顺序 `G → C → E → D → F → H → I → J` 已完成。用户于 2026-08-11 随后批准暂缓一次性 fingerprint cutover/hardening，先按 `M1 Host → M2 Workspace/Environment → M3 generic runtime+iTerm → M4 typed delivery+dogfood` 形成可运行产品路径；四个切片现均已完成，M4 三仓实现与 review 锚点见 [IMPLEMENTATION_PROGRESS.md](../IMPLEMENTATION_PROGRESS.md)。当前按同一裁决回收延期的 fingerprint/hardening debt，随后续接 active architecture Phase 4。12 个现有 verifier placeholder 与历史 receipt 在正式 cutover 前仍不修改；该执行节奏不把 Phase 0 schema conformance 或 M4 单次 dogfood 外推为后续 acceptance，也不改变 §14 的阶段/gate 语义。

## Project descriptor slice closeout（2026-08-11）

- fixed implementation `f1c4fc3baec001b0a4094a322a58b7de1b2e2648` 已 fast-forward/push `main`；Claude review `20260811-123709-8ov3rg` 独立复核 Scope/Verification 后给出 P0=0、P1=0、P2=0、`can_commit_push`，明确该回复终止式闭合 slice、无需递归 closeout commit；
- 该 fixed SHA 的 root `project_descriptor.yaml` 精确包含已批准的 9 个稳定字段；`product_contract_version` 为字符串 `3.2`，adapter 是 versioned project-owned command references，所有 artifact paths 都是 canonical-root-relative；
- validator `scripts/validate_ai_collab_project_descriptor.py` 使用无新增运行时依赖的 constrained YAML parser，校验 exact fields/types、path escape/symlink escape、unresolved placeholder、existing manifest/registry references，以及 registry project/version identity；只输出 path-redacted canonical JSON，不写 source、receipt 或 machine state；
- reviewed descriptor canonical digest：`6ea7a58f5445286a040480c4796b49555bd208b36a5c41e3265658ed3e04417c`；registry digest 保持 `c5b4d7ae227e86c57ba70e338419fd83c5183f24de9a5bb8c678cc269891050d`；G slice 将 `repo_manifest` reference 从 candidate concretize 为 root tracked artifact，因此当前 descriptor digest 预期随该 public reference 改为 `40e302548d9b5989f2f6d243cd0d03e545371f686add06cb9c3c00370f2a07f7`，随 G fixed SHA 一起 review；
- 专项 `18 passed`；完整 `test_ai_collab_*.py` Harness regression `628 passed`；`py_compile` 与 `git diff --check` 通过；
- composed registry 无 diff，12 个现有 verifier placeholder 保持不变，`DOD-SCOPE-001` recorder 仍无 descriptor slot；未读取或修改 Harness state root，未生成 receipt，未触发 evidence rebuild。

## Repo manifest slice verification（2026-08-11）

- fixed implementation `130061b504d77ae78df58f4f20b9ba3d2aca6140` 已 fast-forward/push `main`；Claude review `20260811-125143-tutyow` 给出 P0=0、P1=0、P2=0、`can_commit_push` 并终止式闭合 G；
- root `repo_manifest.yaml` 是 EdgeStudio tracked、machine-independent Harness contract；它不依赖根仓 ignored 的 `onboarding/` checkout。onboarding 自己的 manifest 仍是独立 operational inventory，不是 Harness truth source，本 slice 无跨仓改动；
- manifest 固定 17 个 identity rows：4 required（保持现有 onboarding base acceptance 集合）、11 optional（保持其余 managed 集合）以及用户 2026-08-06 明确排除的 2 unmanaged；`PingAgent` 以 `bundle_sibling/PingAgent` 类型化表达，不使用 `../PingAgent`，manifest 不包含 `workspace_root` 或机器绝对路径；
- managed row 精确表达 canonical SSH remote、逐仓 base branch、provision order/edge、acceptance layer 与 smoke policy；unmanaged row 禁止 remote、branch、provision 字段，避免默认检查或收编。当前逐仓 base branch 恰好均为 `main`，schema/tests 明确允许安全的不同 branch，禁止实现外推全局 `main`；
- validator `scripts/validate_ai_collab_repo_manifest.py` fail closed 校验 duplicate YAML key/alias/tag、exact schema、classification、typed path/overlap、remote/ref、order/prerequisite 与 unmanaged no-probe boundary；只验证 committed contract，不扫描本机 inventory，不读取 Git 状态，不 provision、不写状态；
- canonical manifest digest：`544bcffec958919631f487f1f8fd3ab2c2e44a69eeceebc54d1d5bd97f2a2a88`；raw file SHA-256：`a96f38366fc5dd10e39fc3bf655170a5b44c990bb4510c94101e5dc4c3de2476`；current descriptor digest：`40e302548d9b5989f2f6d243cd0d03e545371f686add06cb9c3c00370f2a07f7`；
- 专项 manifest + descriptor `58 passed`；完整 Harness regression `668 passed`；`py_compile` 与 `git diff --check` 通过。composed registry、12 verifier placeholders 与 Harness state/receipts 保持不变，不触发 rebuild。

## Host IPC contract slice verification（2026-08-11）

- fixed implementation `ee803a7046470f5ab1e4a7c62c1e011c87e4b7fd` 已 fast-forward/push `main`；Claude review `20260811-131310-1xnems` 给出 P0=0、P1=0、P2=0、`can_commit_push`，并明确 C 是必要 product contract、不是 Harness meta-work；
- [host_ipc_v1.schema.json](../contracts/host_ipc_v1.schema.json) 使用 JSON Schema 2020-12 固定 12 个 logical message variants：handshake request/accepted/rejected、operation request/accepted/completed/rejected/failed、progress event、cancel request/accepted/rejected；unknown envelope fields fail closed；
- contract core 只要求 local bidirectional typed transport、authenticated peer identity、current non-root owner、interruption/invalidation observation；真实 binding 是 platform plugin。macOS XPC 是 Phase -1 已验证的实现候选，不进入 logical schema，也没有 Codex/Claude/vendor API 或 runtime identity；
- operation payload/result 不在 C 中伪装为已冻结。C 固定 allowlisted operation descriptor meta-contract：operation/schema identity、request/result schema digest、required capability、target scope、required generation fences、mutation class 与 confirmation policy reference；后续 D/E/F/J surface 提供具体 schema，Host 必须在 durable/external mutation 前完成 registry digest、allowlist、payload schema、capability、target、fence 与 confirmation 校验；
- error contract 固定 6 categories、7 reserved namespaces 与 28 common codes；rejected response 强制 `mutation_state=not_started`，pre-mutation error 不得报告 mutated outcome；driver/adapter 后续可使用非保留 namespace 的 operation-specific code，不能占用 common namespace；
- arbitrary generic `shell.exec` / `host.exec` / `process.exec` / `argv.exec` 明确禁止；request ID 是 idempotency key，progress sequence 单调，cancel 是 cooperative 且不暗示 rollback；capability proof 与 error detail 必须 redacted；
- validator `scripts/validate_ai_collab_ipc_contract.py` 校验 duplicate JSON key、only-local `$ref`、受支持 JSON Schema subset、closed objects、contract metadata/invariants/error taxonomy，并可验证 logical message 与 resolved operation descriptor/registry/fence binding；只读、不连接 Host/XPC、不读取 state；
- canonical contract digest：`74d6fffd9842d7d0a77fbc6623d0ef2d5bc5f80189c3f1c5d54514f84c4ba4a1`；raw SHA-256：`479d5b3a3ceede2551ce0191d67a9c293acd0b83b124ca2f1f6fd7c0908fd990`；C+A/B/G focused `99 passed`，完整 Harness regression `709 passed`，`py_compile` / validator CLI / `git diff --check` 通过；
- 本 slice 不把 `SPIKE-IPC-001` 的单一 `status` probe 提升为 production operation registry，不实现 Host/App/CLI，不修改 registry/12 verifier placeholders/state/receipts，也不触发 fingerprint cutover/rebuild。

## Participant driver contract slice verification（2026-08-11）

- fixed implementation `d544d38edec1d811cb6a4a0be4d66e7b572f4d67` 已 fast-forward/push `main`；初始 review `20260811-133201-04jnad` 与 self-review 并发，amendment `20260811-133230-wo9t89` 将缺少跨 exchange join 修正为 P1/`needs_fix`；final review `20260811-133903-gf6yvb` 独立复核修复后给出 P0=0、P1=0、P2=0、`can_commit_push` 并终止式闭合 E；
- [participant_drivers_v2.schema.json](../contracts/participant_drivers_v2.schema.json) 与 root descriptor 的 `participant_driver_contract: 2` 对齐，固定 Runtime/Presentation descriptor、typed call/ACK value、composed registry、launch/model/process/presentation binding；真实 driver population 与 platform/vendor implementation 不在 Phase 0 contract 中伪装为已完成；
- Runtime baseline 固定 `explicit_recreate`、Harness-owned process binding、ready/delivery ACK、session drift 与 repair；vendor lifecycle surface/operations、vendor session identity 与 `exact_resume` 是可选能力。请求 `exact_resume` 时必须同时有 vendor identity、resume/bind capability 与 exact continuity binding；请求不支持的 interaction/continuity 在 desired-state mutation 前拒绝且不得静默降级；
- `model_binding` 只允许非敏感 profile/model references，participant generation 内 immutable；model/provider 变化必须由后续 D lifecycle 建立新 generation。runtime create/start exchange 必须把 registry/context、launch spec、prepared runtime instance、requested continuity 与 ready ACK 全链等值 join，禁止 descriptor 同时支持两种 continuity 时发生 silent downgrade；raw/private binding 只保留 Host-private reference，普通输出只允许 SHA-256 fingerprint；
- Presentation 固定 permission probe、create/focus/exact close/health、geometry capture/restore 与 display topology；create exchange 等值 join context、driver、runtime binding 与 topology，并区分未请求、exact、adjusted geometry restore。TUI participant 必须有一个与 runtime binding 等值联结的 exact top-level binding，headless participant 禁止占位 presentation。position/window title/role 不是 identity，Accessibility UI scripting 不是 required capability；
- registry 只按 `(driver_id, contract_version)` 与 capability dispatch，canonical digest 为 SHA-256 canonical JSON；空 registry 合法、population implementation-owned，因此 schema 本身不写 Codex/Claude/iTerm/macOS 条件分支。driver-specific error namespace 不得占用 C 的 7 个 reserved Host IPC namespaces；
- validator `scripts/validate_ai_collab_driver_contract.py` 校验 duplicate JSON key、regular artifact/size、only-local `$ref`、closed schema subset、metadata/invariants/interfaces、descriptor capability combination、registry key/ref/namespace uniqueness、launch admission、runtime/presentation ACK equality 与 TUI/headless composition；只读、不启动 driver、不读 vendor store/state；
- reviewed canonical contract digest：`e2f9f2bfead9a6f8fd6702438939896418ea9fa01848fd32c2ccd35fc0a80e90`；raw SHA-256：`f5a6a845ba6f99e7b66a636076b51874f1a03efeea5790a9cace35e1f9d78270`；专项 `67 passed`、A/B/C/E/G focused `166 passed`、完整 Harness regression `776 passed`，`py_compile` / validator CLI / `git diff --check` 通过；
- 本 slice 不提前固定 D Scenario/Participant durable state/lifecycle operation registry、F policy、J permission/high-risk matrix，不修改 composed gate registry、12 verifier placeholders 或 Harness state/receipts，也不触发 fingerprint wiring/rebuild。

## Scenario/Participant state contract slice verification（2026-08-11）

- [scenario_participant_state_v1.schema.json](../contracts/scenario_participant_state_v1.schema.json) 固定 Scenario 与 Participant 分离的 desired/observed state、scenario/participant generation、state revision、active operation、degraded evidence、binding references 与 journal head；`unregistered` 是 record absence，detach 保留历史，destroy 只移除 current state 且 audit history retained；
- 18 条 Scenario transitions 与 18 条 Participant transitions machine-readable/fail-closed，并为每条 transition 固定 `desired_after` 规则：只有显式 initiating operation 可改变 desired，completion/failure transition 必须 preserve。create/add 从 record absence 建立 generation/revision 1，participant destroy 从 detached 移除 current record；open/repair/destroy failure 均可进入 degraded，destroy repair 可恢复到 destroying。2026-08-14 用户批准的 recover 修正把失败 generation 严格 fenced 为 `degraded → recovering → stopped`：只有 exact graceful cleanup 或 pre-binding absence proof 成功后才递增 generation，旧 generation 与 journal/history 保留，歧义失败且不得自动 force-stop；后续运行必须显式 Start 新 generation。单 participant degraded 投影 scenario degraded，但不得改变其他 ready participant binding；running scenario 要求所有 desired-running participants ready；
- 12 个 allowlisted lifecycle operation kinds 使用 request idempotency、immutable plan digest、host/scenario/participant generation、scenario state revision 与 participant state revision precondition fence；不存在的 target 必须使用 null generation/revision fence。journal 明确记录 target-record revision并要求从 precondition 连续推进；desired commit revision 与 resulting generation 共同成为 callback finalize fence。操作协议固定“validate → CAS desired with generation/revision fence → release lock external action → fence-match finalize”；external/finalize journal 顺序 fail-closed，stale callback journal/reject，unknown outcome degraded/repair；不注册 C operation registry、不接受 generic exec；
- replace 在修改旧 binding 前验证新 launch spec；replace 与 recovery success 都让 participant generation 精确 `previous + 1`：replace 建立新 launch identity，recover 则先证明旧 generation 外部资源已清理/不存在，再建立 stopped generation，绝不复用失败 identity。其他 transition generation 不变；pre-CAS failure 保留旧 generation/binding，post-CAS failure degraded/repairable。detach 先 durable commit desired detached/停止新投递，再 cleanup；cleanup pending 保留 owned-resource evidence，并允许 stop/detach/repair retry；
- state contract 只保存 E contract 的 canonical launch spec digest 与 Harness-owned `runtime_binding_id` / presentation binding reference，不复制 model/driver payload。TUI ready 必须 runtime+presentation binding，headless 禁止 presentation；stopped/detached 禁止 live binding；
- validator `scripts/validate_ai_collab_state_contract.py` 校验 contract + tracked E dependency、records、desired/observed transition、exact generation/revision、operation target/continuity/result semantics、CAS journal revision chain/event order 与 scenario aggregate；只读、不实现 Host storage/transactions、不执行 external action；
- fixed implementation `2e5c1a274531625ef06f16d3210d66574f86b410` 已 fast-forward/push `main`；superseding review `20260811-145313-5oxcxr` 对完整 D slice 给出 P0=0、P1=0、P2=0、`can_commit_push`，并明确旧 SHA `ffff8370...` 的 PASS 作废；
- 原始 Phase 0 review 的 canonical digest 为 `4cadc27242fb6524b40536b938efac1a1f82aa95bfccf291182692154a58d4f6`、raw SHA-256 为 `9775124aa2dd5e6ee3afea285ccec7edcd2f3271e44cfb0a3a4caf3ce623e7ef`；2026-08-14 用户批准 recover 语义修正后的 current canonical digest 为 `75c277c2b96e1365bb782e6ebc7acd2d12897bd0e6b35ffb2df39eef1f2116b8`、raw SHA-256 为 `9780a6f5c601f8e16e1086e734cefc0f6789f81bebe4831da1e9cd0348023ae8`，E dependency digest 仍为 `e2f9f2bfead9a6f8fd6702438939896418ea9fa01848fd32c2ccd35fc0a80e90`；原始 review 不承担 amended contract 的准入，fixed-SHA review 与实施证据由 current progress 账本记录；
- 本 slice 不提前固定 F policy、H workspace/environment payload、J confirmation matrix 或 Phase 4 force/repair/destroy mechanics，不修改 gate registry、12 verifier placeholders、state/receipts 或 fingerprint wiring/rebuild。

## Collaboration Policy + reliable-delivery contract slice verification（2026-08-11）

- [collaboration_policy_delivery_v1.schema.json](../contracts/collaboration_policy_delivery_v1.schema.json) 把 policy pack/snapshot、route request/decision、delivery record、delivery ACK 与 consumption ACK 固定为一个供应商/项目中立逻辑 surface，并引用 C `ai-collab-host-ipc-v1` 与 D `ai-collab-scenario-participant-state-v1`；
- policy rule 按声明顺序 first-match，默认 deny；sender/receiver 只能是 exact participant generation 或显式 namespaced assignment。assignment 是 policy data，不是 identity；Host 不自行推导 role、broadcast、quorum 或 escalation。每个 queued delivery 固定 policy version/digest、route request/decision digest、payload digest、receiver runtime/presentation binding 与 policy-owned retry profile snapshot，后续 policy update 不得重投既有 delivery；
- canonical delivery 只允许 `queued → delivery_attempted → delivered → consumed`。文件或 `.dispatched` sidecar 不构成 delivery evidence；只有 exact target/payload/attempt delivery ACK 才能进入 `delivered`，只有绑定该 delivery ACK digest 的 consumption ACK 才能进入 `consumed`。事件 append-only，retry 有显式上限和非递减 backoff；Host restart 恢复全部非终态 delivery，其中 queued/delivery_attempted 继续 exact dispatch，delivered 只恢复 consumption supervision、不得重复注入；超限只标记 exact target delivery degraded；
- TUI target 精确固定 presentation binding；headless target 明确没有 presentation binding。vendor session identity、Codex/Claude API、private vendor store、role/recent-session/other-mailbox fallback 都不是 correctness dependency；EdgeStudio `AGENTS.md` 仍是后续 project policy pack 的集成层来源，未进入产品 core enum；
- validator `scripts/validate_ai_collab_policy_delivery_contract.py` fail closed 校验 contract + C/D dependencies、policy update、deterministic route result、exact enqueue、append-only delivery transition/ACK 与 restart resume；它不实现 Host/store/transport、不读取 mailbox/Harness state，也不发送真实消息；
- fixed implementation `fb480b314d408089ab119a41a6379e24ef5697ea` 已 fast-forward/push `main`；review `20260811-152909-kqzrsx` 独立复核完整 F slice 后给出 P0=0、P1=0、P2=0、`can_commit_push` 并明确无需递归 closeout commit；
- reviewed canonical digest：`ec753f884630f63cbf19d1273ed22ae2cfb1105d8ce3db17e6a12f0471d8c5b7`；current raw SHA-256：`372e9116bd4240c5cce70a1ef7828f6361c2ce9c3e59af7dab8829902532ae0e`；原始 review 使用 D dependency digest `4cadc27242fb6524b40536b938efac1a1f82aa95bfccf291182692154a58d4f6`，2026-08-14 amended D current digest 为 `75c277c2b96e1365bb782e6ebc7acd2d12897bd0e6b35ffb2df39eef1f2116b8`；F 的 participant identity/routing join fields 未变，依赖 validator 已对 amended D 重新通过；
- 本 slice 不提前实现真实 Host/storage/transport/agent consumption，不固定 H workspace/environment payload、I shared gate loader 或 J confirmation matrix，不修改 composed gate registry、12 verifier placeholders、machine state/receipts 或 fingerprint wiring/rebuild。

## Workspace/Environment adapter contract slice closeout（2026-08-11）

- [workspace_environment_v1.schema.json](../contracts/workspace_environment_v1.schema.json) 固定 workspace/environment adapter descriptor、generation-scoped immutable plan、typed operation journal、ready receipt 与 status observation；product core 只认识 logical component/revision、adapter/payload digest、isolated writable binding 与 structured drift，不按 Git/worktree/Python/venv/container 分支；
- plan 固定 project descriptor/repo manifest digest、scenario generation、adapter refs、requested components、依赖/顺序、source identity、exact revision、logical target ref、environment spec/lock/source-binding digest、source WIP snapshot 与精确空间估计。required component 不可省略、unmanaged 不可 materialize、source mutation/shared mutable storage 禁止，physical path/private binding 不进入 public value；
- `plan | provision | status | repair | destroy` 五种 operation 具有显式 journal phase contract。status/repair/destroy 必须使用新的 operation identity，并以 base receipt digest、ready revision、workspace/environment identity + binding 精确 fencing；首个 committed event 绑定完整 operation intent。journal sequence/step state 只能前进，failure/unknown 禁止继续 normal phase 或生成 ready receipt；provision/repair ready receipt 必须精确 join plan + journal + optional base receipt，包含 components/environment verify evidence、atomic publish evidence、单调 registry CAS/ready revision，并证明 source WIP before=after、residual owned resources=0；
- observation 将 aligned/degraded/missing 分开，并精确 join 已完成的 receipt-fenced status/destroy journal；destroy 只能生成 missing observation。aligned 精确匹配 workspace/component revision/environment binding，dirty WIP 作为可见状态保留而非清理；degraded/missing 必须提供 structured drift。binding registry 禁止 scenario/workspace/environment identity 或 binding 重用；
- generic validator 不观察 inventory、不执行 adapter。EdgeStudio integration validator 再把 component 精确 join 到 root manifest，强制 Git exact SHA kind、`workspace.no-local-clone` materialization 与每仓 project guard digest；真实 remote/ref/clone/guard/venv 行为仍由 Phase 1 project adapter execution/acceptance 证明，contract 结构本身不冒充执行 evidence；
- fixed implementation `d1a86d72c560233bd1d8ec5a54f63809e5563d00` 已 fast-forward/push `main`；Claude review `20260811-163606-he0ysb` 对完整 H slice 给出 P0=0、P1=0、P2=0、`can_commit_push`，明确无需递归 closeout commit；
- reviewed canonical digest：`85f4b4164186209464ff771767a1c7572182176f007feabc2fd3f05e297cf880`；raw SHA-256：`8811e40d94a54a42fe8386e1b2ae90f24598cfa9f653f47ae5467c236c078f1b`；descriptor digest：`40e302548d9b5989f2f6d243cd0d03e545371f686add06cb9c3c00370f2a07f7`；manifest digest：`544bcffec958919631f487f1f8fd3ab2c2e44a69eeceebc54d1d5bd97f2a2a88`；专项 `101 passed`、完整 Harness regression `1060 passed`；
- 本 slice 不 provision/clone/create environment，不读取 canonical Git/WIP 或本机路径，不实现 Host storage/transaction，不固定 I/J，不修改 composed registry、12 verifier placeholders、machine state/receipts 或 fingerprint wiring/rebuild。

## Gate registry loader/digest/phase/projection slice closeout（2026-08-11）

- [gate_registry_v2.schema.json](../contracts/gate_registry_v2.schema.json) 固定三个只读输出：registry snapshot、单 gate projection、单 workflow phase projection；shared loader `scripts/ai_collab_gate_registry.py` 直接消费 tracked `edgestudio_gates.yaml`，不复制第二份 gate inventory；
- constrained YAML 对 duplicate key、alias/anchor/tag、non-JSON scalar、未知/缺失字段 fail closed；gate identity/class/evaluation、revocable live semantics、direct/related references、DAG、exactly-one phase assignment、dependency 不指向后续 phase 与 `must_precede` 均由同一 loader 校验。加载时隔离 caller input，加载后任何已验证内容 mutation 都拒绝 projection；
- fingerprint 中的 `registry_digest` 继续严格等于完整 YAML source bytes 的 SHA-256 `c5b4d7ae227e86c57ba70e338419fd83c5183f24de9a5bb8c678cc269891050d`。canonical registry digest `2bc25a180b07c98cd49a652cb94462eb24cd6c02a04439f43ff6b08b9d5f8405` 仅用于 semantic comparison，per-gate definition digest 仅用于 projection identity；本 slice 不迁移 sliced/per-gate fingerprint；
- snapshot 固定 57 个 required gates、10 个 workflow phases、group/class/evaluation counts、workflow/gate-graph/receipt/delivery digests。gate/phase projections 都不包含 `passed`、freshness 或 current status；validator 明确输出 `inventory_observed=false`、`state_mutated=false`，避免把结构 conformance 冒充 machine evidence；
- fixed implementation `9fd883a2d968b5628e060e80a5fb8e60d72a5b7d` 已 fast-forward/push `main`；Claude review `20260811-171023-bw4xz8` 对完整 I slice 给出 P0=0、P1=0、P2=0、`can_commit_push`，明确无需递归 closeout commit；
- reviewed contract canonical digest：`1fe4f3185eada2e4cde6003c6da9238dd715b24bb017ae2b308b5484264cf9f3`；raw SHA-256：`08af9e0467cd9f6b9ab354b788897b301476ac0ba2df840815a8709c3c041876`；workflow digest：`f9a03ad516bbcabfe19b280c6d7ee42d492e34a63b469f9b1f5f410a68789b01`；gate graph digest：`fdfa84293fd37c70234923b30e25b07a9c5cec1e7e897a1c3773733d41f9119a`；专项 `71 passed`、完整 Harness regression `1131 passed`；
- 本 slice 不修改 `edgestudio_gates.yaml`、现有 verifier loader/fingerprint wiring、12 个 verifier placeholders、machine state/receipts；current status/freshness/evidence loading、one-shot cutover/rebuild、J confirmation matrix 与 Host storage implementation 都保持 deferred。

## Permission/high-risk confirmation contract slice verification（2026-08-11）

- [permission_confirmation_v1.schema.json](../contracts/permission_confirmation_v1.schema.json) 固定六类 machine-readable artifacts：permission/confirmation matrix、permission snapshot、challenge、decision、authorization 与 consumption；matrix 必须对传入的 C Host IPC operation descriptor 集合做 exact coverage/join，并分别绑定 operation registry、permission catalog 与 confirmation policy set digest；空 operation population 与空 permission catalog 合法，避免 Phase 0 伪造 production entries；
- permission 与 capability 分离。snapshot 只接受 exact subject、descriptor digest 与 freshness window 内的当前 `granted` observation；`denied`、`not_determined`、`restricted`、`unavailable`、`unknown`、provider error、过期或 subject 漂移均 fail closed。contract 只描述 user mediation schema，既不探测系统授权，也不把 provider-specific API 写入产品核心；
- high-risk/destructive operation 必须使用 `exact_request`、single-use authorization 与 typed effect preview；deny、expiry、binding mutation 或 replay 均不得授权。bounded authorization 只允许非 high-risk operation，必须绑定 scope constraint/schema，并在每次消费前重新验证 permission snapshot、target/fence 与 typed membership evidence；consumption 在 mutation 前 append，receipt 只证明 authorization 被消费，不证明 operation 成功；
- admission order 固定为 operation registry/descriptor → request schema/capability/target/fence → current permission snapshot → challenge → explicit user decision → authorization → per-use revalidation → pre-mutation consumption。真实 permission prompt、trusted App confirmation UI、force-stop/resource-break/destroy/repair mechanics 与 acceptance 明确 deferred 到 Phase 4；
- fixed implementation `3bc693da01d5da7154563749fb9aa73857792e50` 已 fast-forward/push `main`；review `20260811-174835-oveydb` 给出 P0=0、P1=0、P2=0、`can_commit_push`。reviewed canonical digest：`2e84cc3fb493ff6f8838d10e0e28e357d837672ac2933f427a3096475fae3b6f`；raw SHA-256：`17345f51a1bcb3bedd72b2580755600175f4c945c81ffe85525ea90bc87d0181`；Host IPC dependency canonical digest：`74d6fffd9842d7d0a77fbc6623d0ef2d5bc5f80189c3f1c5d54514f84c4ba4a1`；专项 `102 passed`、完整 Harness regression `1233 passed`；
- validator 明确输出 `platform_permissions_observed=false`、`confirmation_ui_invoked=false`、`state_mutated=false`；本 slice 不修改 registry、verifier、machine state/receipts 或 fingerprint wiring，也不触发 one-shot cutover/rebuild。

## 用户裁决与执行边界

这不是技术执行困难。cutover 前的 `DOD-SPIKES-001` passed 仍可信，因为它会核验各 receipt 已记录的 dependency evidence SHA；问题是当时 registry-only graph 没有完整表达这些 receipt-level direct dependencies。该 graph completeness debt 现已按用户授权清偿并重建本机 current truth。

选择何时补齐会产生不同后果：

- 立即修改 registry 会改变 whole-registry digest，使当前机器的 registry-bound evidence/current projections stale，并触发真机/真实进程 witness 重建；
- 暂缓修改可以继续使用当前 evidence，但会把不完整 graph 带入 Phase 0 freeze；
- 同时迁移到 per-gate/sliced digest 能降低未来无关变更的失效范围，但会扩大本次 fingerprint/gate-engine contract 改动。

这是 gate 语义、证据成本和 Phase 0 contract 范围的取舍，因此先完成了只读审计。用户于 2026-08-10 批准审计推荐方案：在隔离 worktree/branch 中补齐精确 direct dependencies 与 verifier mirrors；实现固定并 push 后由 Claude review；只有 P0=0、P1=0 才 fast-forward `main`，随后保持 whole-registry digest 并按 9 层 DAG 重建。旧 immutable evidence 只保留为历史；任何失败立即停止，不自动回滚 `main`。

## Cutover implementation declared 与 recorded graph

下表的“recorded direct evidence”来自 fixed producer/verifier 与本机摘要一致的 immutable receipt `dependency_evidence` keys；不保存 run ID、绝对路径或完整 payload。

| Gate | Registry `depends_on` | Recorded direct evidence | 当前结论 |
|---|---|---|---|
| `LEGACY-DELIVERY-001` | `PREIMPL-SNAPSHOT-001` | `PREIMPL-SNAPSHOT-001` | match |
| `DOD-SCOPE-001` | `LEGACY-DELIVERY-001` | `LEGACY-DELIVERY-001` | match |
| `SPIKE-HOST-001` | `LEGACY-DELIVERY-001`, `DOD-SCOPE-001` | `LEGACY-DELIVERY-001`, `DOD-SCOPE-001` | match |
| `SPIKE-IPC-001` | `LEGACY-DELIVERY-001`, `DOD-SCOPE-001`, `SPIKE-HOST-001` | `LEGACY-DELIVERY-001`, `DOD-SCOPE-001`, `SPIKE-HOST-001` | match |
| `SPIKE-ITERM-001` | `LEGACY-DELIVERY-001`, `DOD-SCOPE-001`, `SPIKE-IPC-001`, `SPIKE-RUNTIME-DRIVER-001` | `LEGACY-DELIVERY-001`, `DOD-SCOPE-001`, `SPIKE-IPC-001`, `SPIKE-RUNTIME-DRIVER-001` | match |
| `SPIKE-RUNTIME-DRIVER-001` | `LEGACY-DELIVERY-001`, `DOD-SCOPE-001` | `LEGACY-DELIVERY-001`, `DOD-SCOPE-001` | match |
| `SPIKE-TUI-ID-001` | `LEGACY-DELIVERY-001`, `DOD-SCOPE-001`, `SPIKE-RUNTIME-DRIVER-001`, `SPIKE-IPC-001`, `SPIKE-ITERM-001` | `LEGACY-DELIVERY-001`, `DOD-SCOPE-001`, `SPIKE-RUNTIME-DRIVER-001`, `SPIKE-IPC-001`, `SPIKE-ITERM-001` | match |
| `SPIKE-TUI-LIFE-001` | `SPIKE-TUI-ID-001` | `SPIKE-TUI-ID-001` | match |
| `SPIKE-DELIVERY-001` | `SPIKE-HOST-001`, `SPIKE-IPC-001`, `SPIKE-TUI-ID-001`, `SPIKE-TUI-LIFE-001` | `SPIKE-HOST-001`, `SPIKE-IPC-001`, `SPIKE-TUI-ID-001`, `SPIKE-TUI-LIFE-001` | match |
| `SPIKE-WINDOW-TOPOLOGY-001` | `SPIKE-ITERM-001`, `SPIKE-TUI-ID-001` | `SPIKE-ITERM-001`, `SPIKE-TUI-ID-001` | match |
| `SPIKE-STORAGE-001` | `DOD-SCOPE-001` | `DOD-SCOPE-001` | match |
| `SPIKE-CLOSE-001` | `SPIKE-TUI-ID-001`, `SPIKE-TUI-LIFE-001` | `SPIKE-TUI-ID-001`, `SPIKE-TUI-LIFE-001` | match |
| `SPIKE-UPGRADE-001` | `[]` | `[]` | match |

隔离分支上的 source-only conformance 结果为 13/13 match、mismatch 0。此前正式只读审计的结果仍是 cutover 前 11 mismatch；历史 fingerprint 与审计结论不被覆盖。这里的 direct evidence 不等同于 workflow prerequisite 或 aggregate membership，也不新增 vendor/platform 依赖。

## Whole-registry digest blast radius

当前本机 13 份 immutable evidence/current pairs 都把同一份完整 `edgestudio_gates.yaml` digest 纳入 `input_fingerprint`。因此候选 registry cutover 在现行 digest contract 下会使以下 projections stale：

- immutable：`LEGACY-DELIVERY-001`、`DOD-SCOPE-001` 与 11 个 Phase -1 SPIKE，共 13；
- derived：`DOD-SPIKES-001` current view，共 1；
- preserved prerequisite：tracked `PREIMPL-SNAPSHOT-001` manifest；它不是本机 immutable receipt，不因 registry digest 变化而重写。

只读 audit 必须对 state root 做 before/after tree snapshot digest，验证 `state_mutated=false`；输出不得包含 state root、run ID、evidence path、credential 或 session identity。

## Candidate rebuild DAG

用户已批准按现行 whole-registry digest 使用以下安全重建层次：

1. `LEGACY-DELIVERY-001`、`SPIKE-UPGRADE-001`
2. `DOD-SCOPE-001`
3. `SPIKE-HOST-001`、`SPIKE-RUNTIME-DRIVER-001`、`SPIKE-STORAGE-001`
4. `SPIKE-IPC-001`
5. `SPIKE-ITERM-001`
6. `SPIKE-TUI-ID-001`
7. `SPIKE-TUI-LIFE-001`、`SPIKE-WINDOW-TOPOLOGY-001`
8. `SPIKE-CLOSE-001`、`SPIKE-DELIVERY-001`
9. derived recompute `DOD-SPIKES-001`

层次只表达依赖偏序，不授权并发运行 GUI、真实进程或高资源 witness。每层仍须使用 fixed pushed SHA、按各 gate preflight/cleanup 约束执行，并在进入下一层前复核 current→immutable digest 与 dependency freshness。

## Approved cutover procedure

来源：`user_decision`（2026-08-10），当前生效范围仅为本次 registry normalization cutover。

1. 在一个固定 migration implementation 中，仅为上述 11 个 mismatch gate 添加与 receipt `dependency_evidence` 精确一致的 `depends_on`；同步更新相应 exact gate-block verifier contracts 和 mutation tests，不改变 producer claim、vendor capability 或 acceptance 语义。
2. 本次 normalization 继续使用现有 whole-registry digest，接受一次 13 immutable + 1 derived 的保守重建；不在同一 cutover 混入 per-gate/sliced digest schema 迁移，避免同时改变 dependency graph 与 fingerprint trust model。
3. 本次明确保留 whole-registry digest；per-gate/sliced digest 仍是未授权的独立 contract 议题，没有新裁决前不得静默切换。
4. migration target 必须先 fixed/pushed、通过 peer review，再进行 registry cutover 与上述 ordered rebuild；旧 immutable evidence 保留为历史，不编辑、不复制成新 current truth。

## Audit 完成条件

1. read-only audit tool 与 mutation/fail-closed tests 位于 fixed pushed commit；
2. source-only 结果精确为 mismatch `11`；
3. 本机 state audit 精确为 immutable `13` + derived `1`，before/after snapshot digest 相等；
4. candidate edges 与 rebuild DAG 无未知 gate、重复边或 cycle；
5. tracked decision、总架构、handoff/progress 与本文一致，且 registry/state 均未修改；
6. Claude 对 fixed SHA 给出 P0=0、P1=0、`can_commit_push`，独立核验 11、13+1、no mutation 与 DAG。

## 运行入口

```bash
python scripts/audit_ai_collab_registry_dependencies.py \
  --expected-edgestudio-sha <40-char-pushed-sha> \
  --source-only

python scripts/audit_ai_collab_registry_dependencies.py \
  --expected-edgestudio-sha <40-char-pushed-sha>

python -m pytest tests/test_ai_collab_registry_dependency_audit.py -q
```

两条 audit 命令都只输出 JSON 到 stdout，不写 receipt、current view 或 tracked artifact。

## Formal audit record

- fixed implementation：`afeb6ab23eff00700c5ac720ef18f52465b6ee08`；`main` clean、已 push，local/remote commit 相等；
- source-only audit：`mismatch_gate_count=11`、`registry_mutated=false`、`state_mutated=false`；source fingerprint `f18e98e94bf3fd23992e81edbe12ad381b73ad44c636a7ae5620da5d6dac493f`；
- machine-state audit：immutable `13`、derived `1`、total current projections `14`、`state_mutated=false`；machine fingerprint `11118dc0145ace88946943b142745f8243c98f00a4379289afef84216a9cc325`；before/after state snapshot digest `9d898b8435497a4030848ee6557af214099ef6123d1666b4a4a4e2461bdbc9dd`；
- independent verification：source/machine canonical fingerprints 分别重算一致，source fingerprint 跨两种运行相等；输出隐私扫描未发现绝对路径、run path、credential/token/secret；registry 无 diff，working tree clean；
- verification：audit 专项 15 passed；audit + DOD aggregate 47 passed；完整 Harness 610 passed；`py_compile` 与 `git diff --check` 通过。
- closeout target：`54ef00a036bfaa61f8037be8c6b5e702c74368be`；Claude review `20260810-203530-y2nvqr` 独立运行 audit/tests，核验 mismatch 11、blast radius 13+1、9 层 DAG、no mutation 与 source fingerprint，给出 P0=0、P1=0、P2=0、`can_commit_push`，明确确认条件 6 成立并允许把精确 cutover proposal 提交用户裁决。

当前完成条件：1–6 全部成立，audit/migration proposal 已完成。用户随后已单独授权 cutover；audit review 本身仍不被解释为 implementation review 或 evidence rebuild 成功。

## Implementation branch verification（2026-08-11）

- source-only audit：`status=normalized`、`mismatch_gate_count=0`；registry digest `c5b4d7ae227e86c57ba70e338419fd83c5183f24de9a5bb8c678cc269891050d`；source fingerprint `1a82aafac99a6c83c3cf7c7ff7130419ea744fb987413a1d5644c8338118d348`；
- cutover decision digest：`64b47b2e35032a8695a7c32286b4a155b99e75cf25318bb6647823f8e22fbda4`；
- focused registry/verifier regression：538 passed；完整 `test_ai_collab_*.py` Harness regression：610 passed；使用仓库原生 `tests/conftest.py` 的第二次完整运行同样为 610 passed；
- `py_compile` 与 `git diff --check` 通过；
- 隔离 worktree 内用独立、ignored local clones 提供 `onboarding` / `edge-studio-dev` integration material；canonical sibling worktrees、`main` 与正式 Harness state 均未修改，也未发生 rollback。

以上 implementation branch 结果已由 Claude 在 `20260811-102603-ck9asp` 对固定 SHA 独立复核并给出 P0=0、P1=0、PASS；随后才执行 `main` promotion 与 formal rebuild。

## Formal cutover and rebuild record（2026-08-11）

- implementation：`ec9a9b4e77c895803d4d8b63bb59145355a2537c`；review `20260811-102603-ck9asp` 为 P0=0、P1=0、PASS；重建在该 SHA 上执行；tracked closeout `350796217c9e942535d860500635d13ecaa68b7a` 经 terminal review `20260811-104833-jhfpsc` 的 P0=0、P1=0、P2=0、`can_commit_push` 闭环并 fast-forward `main`；
- whole-registry digest：`c5b4d7ae227e86c57ba70e338419fd83c5183f24de9a5bb8c678cc269891050d`；source fingerprint：`1a82aafac99a6c83c3cf7c7ff7130419ea744fb987413a1d5644c8338118d348`；
- machine audit：`normalized`、mismatch 0、immutable 13、derived 1、`state_mutated=false`；machine fingerprint `dd04e5986de96286e83af24b12275d8e4a88bc3c4b80ec236e023906ae387310`；state snapshot `93e08122500475fc6549748b28a2d42b382247b3cbf452dadf105a0570e11087`；
- `DOD-SPIKES-001`：11/11 `passed + source_material_fresh`，aggregate fingerprint `2f06b9703d9096ab20a1f1913a1f09d68ebd5188f490e90e16e72f5d8995197e`，只写 derived current view，无 immutable DOD evidence；
- 13 个 current/evidence 文件均为 `0600`，依赖 keys 与 registry 精确一致；各 verifier 的 cleanup/failure boundary 均通过，PingAgent doctor 为 0 warning / 0 failure；
- 旧 immutable evidence 未删除或改写，只由新 current views 指向本轮 runs；全程无失败，因此没有触发或执行 rollback。

| Gate | Formal run | Evidence SHA-256 | Input fingerprint |
|---|---|---|---|
| `LEGACY-DELIVERY-001` | `bootstrap-20260811T022804Z-8e28b8321a1b` | `a8c4aa8039ac9db0056e25e6e63ae19d0794edf690d7d7f8a20ca7180c6fd5fc` | `8e28b8321a1b2ca13e1bee168cc500b1d031cc87946eea20995c92f8040cdbf0` |
| `DOD-SCOPE-001` | `decision-20260811T022840Z-a0fe10c0293d` | `942553e16b33afbb5b72e9f9ece2bace1faa89b6c1cae3ea40d9f77075347ed7` | `a0fe10c0293dd442f4d314a8b226a7ffacaff63eaa5d8ef203367c0f8b6cfc07` |
| `SPIKE-HOST-001` | `spike-host-20260811T022914Z-8e8e222296f6` | `14b0dc725e6ef1f7fe9cdfa66043ac6533b3a204cbe2a3bebf9889469ba211e9` | `8e8e222296f62ff17de9ec58b531d4890a6b661a00c186e30cc925c8ed7658fb` |
| `SPIKE-RUNTIME-DRIVER-001` | `spike-runtime-driver-20260811T022852Z-62d5b125c848` | `30478dbda2b45c16517460e0c15888712de7b88775150fb62d2bb6f4b090db14` | `62d5b125c848ebd990534f3844ce1eee179094002914e8272c63a6e20aa79eef` |
| `SPIKE-STORAGE-001` | `spike-storage-20260811T023334Z-a1b66bb7e7a4` | `a55825320d679a62876d1616a66b5dfc05e09c79a104d1c09d2922c697538fe9` | `a1b66bb7e7a48cef350ef4169c91842f12c29eaf1962f48be82bfe6bc8697bb2` |
| `SPIKE-IPC-001` | `spike-ipc-20260811T023353Z-68572d5f3275` | `bd05854188feecba6e0e0203f561266350ee0d1484bd8bb417ddde07b1eaa245` | `68572d5f3275bb8c40bf3cf25fd707802a74db8fecd5a0edbe8fbff350ea27ca` |
| `SPIKE-ITERM-001` | `spike-iterm-20260811T023416Z-863904e46378` | `3fa7172645fbdf3b6fa4ff6664a6ffb32b0ec336fee0f5a442a4a12ed050adab` | `863904e46378c6af065bb364c6d98dda22e4cf211de8a4658d4d76f610f578ef` |
| `SPIKE-TUI-ID-001` | `spike-tui-id-20260811T023451Z-2988107c2ebe` | `489dacb3f2dcf548654fb05e9c8d0294b024b2fa83279a66cb38680c9713e4b4` | `2988107c2ebe5c40034fa5638ed8b71756ea38c6b95206a1285e7e6574cf542d` |
| `SPIKE-TUI-LIFE-001` | `spike-tui-life-20260811T023520Z-96bf1f522fa5` | `cdbfe05df6e9f1bd5772f8b0955d5d283ecaa113a904f481a242449f48509fad` | `96bf1f522fa5569e25a2768526867e402f2fc2fcd329f4369b5d64108faf849f` |
| `SPIKE-WINDOW-TOPOLOGY-001` | `spike-window-topology-20260811T023542Z-3b6132da40ce` | `acc51889aaa4714034feb4cff9f39f5bc623f2ae7a57cf9e047f3e116c3e563c` | `3b6132da40ceae27d71ac680b2f075d7633b342da33dd8757d9b32e2b8568f5e` |
| `SPIKE-CLOSE-001` | `spike-close-20260811T023614Z-cabb98f59236` | `bbd024728c4a93ecf5b93a9791e0d677a697ece82de41bcf610ea0833708d8cd` | `cabb98f592361eaa145c5c18bf6248e6bad27ebaa46c505fc18f0e8e51b28a9d` |
| `SPIKE-DELIVERY-001` | `spike-delivery-20260811T023633Z-0fb4e98096c3` | `d8c8134a39767e5c7f1a8eb64349c1f4fa4e60a23271b2c7900d2e1a6e3bdeec` | `0fb4e98096c355e64a95ba9dea99ab0d78fdd6651cf502ee5e8db72055a3268d` |
| `SPIKE-UPGRADE-001` | `spike-upgrade-20260811T022828Z-cb71760421b6` | `511455488d87c9ae4b00fde5e9ae71555b2475748ddf8f112858820bc0c33733` | `cb71760421b60e3c7604d7b791d48fb870156d28e6c876baa2337b7d4ed11e3d` |

## Cutover completion conditions

1. 隔离分支只包含已批准的 11 个 registry edges、对应 exact verifier mirrors/tests、cutover decision 与 current-state 文档；
2. source-only audit 为 `normalized`、mismatch 0，9 层 DAG 不变；
3. fixed commit 已 push，focused 与完整 Harness regression 通过；
4. Claude 对该固定 SHA 给出 P0=0、P1=0；
5. 仅在条件 4 后 fast-forward `main`，再按 9 层顺序重建 13 份 immutable evidence；
6. 重算 `DOD-SPIKES-001` derived current view，并以独立复核和 closeout 文档闭环。

条件 1–6 已由 terminal review `20260811-104833-jhfpsc` 全部闭环。任一步失败即停、不自动回滚 `main` 的规则在本轮未被触发；该历史 cutover 不替代本页开头的 Phase 0 contract freeze。

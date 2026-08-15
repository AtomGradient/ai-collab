# SPIKE-STORAGE-001 — Scenario workspace / environment cost feasibility

> 状态：`completed`；正式 machine receipt、独立复核与 terminal peer review 已全部完成
>
> Scope 来源：`user_decision`（2026-08-10）。用户确认本 gate 是 Harness 自己的 disposable 测试，实际空间预计很小，并批准按下述最小真实 witness 继续。

## Gate 语义

本 gate 的 verifier 名称是 `storage_budget_feasibility`。它沿用原始架构中的精确定义：测量 no-local clone 与 per-scenario Python environment 的创建时间、增量磁盘和 cache 行为，并验证隔离方案在 `DOD-SCOPE-001` 已批准的资源预算内可行。

这里的 storage 不是 Host durable-state 数据库。本 spike 不实现 registry、CAS、journal、crash reconcile、retention、GC 或 schema migration；这些分别属于后续 Host/acceptance 与 `SPIKE-UPGRADE-001`。

## 用户批准的 hard claims

1. 只创建 1 个 disposable scenario；`concurrent_disposable_scenarios_max=1` 是本轮资源预算，不是产品 scenario 数量上限。
2. peak allocated bytes 必须严格小于 `incremental_disk_gib_max=100` GiB；不会预分配 100 GiB。producer 每阶段 fail closed，verifier 另用 `du` 周期采样峰值。
3. elapsed 只进入 measurement，不设置 AI 自拟的时间 pass threshold，也不据此声称效率改善。
4. 代表性 scenario 精确包含 `EdgeStudio`、`onboarding` 与 `edge-studio-dev` 三个 fixed-SHA repo。outer repo 提供 Harness，onboarding 提供 reviewed direct constraints，edge-studio-dev 提供真实 editable package；这三仓是当前布局下能证明真实 environment isolation 的最小集合。
5. 三仓 witness 不是完整 manifest 成本证明，不外推 `edge-scaffold`、其他 role/CI/team repo 或未来项目。
6. direct dependency 只有 `DOD-SCOPE-001`。`LEGACY-DELIVERY-001` 仍是 Phase -1 workflow precondition，但不提供 storage 验证输入，不能写入 target direct dependency set。
7. 本 spike 不调用 Codex、Claude、iTerm、vendor session API 或 model turn；Git/Python/pip 只是 EdgeStudio project feasibility witness，不成为 Host core 的 required supplier dependency。

## No-local clone 与 canonical isolation

verifier 先固定三个 source repo 的 branch、upstream、40-char SHA、tree 与 remote equality。producer 在 `0700` owned run root 中按固定顺序执行：

```text
git clone --no-local --no-checkout --origin canonical-source <source> <target>
git -C <target> checkout --detach <exact-sha>
git -C <target> remote remove canonical-source
```

每仓必须满足：

- target HEAD 精确等于输入 SHA，working tree clean，clone 完成后不保留包含本机路径的 local-source remote；
- `.git/objects/info/alternates` 不存在或为空；
- source/target object storage 的 device/inode 集合无交集，target object file link count 均为 1；
- canonical source 的 HEAD、porcelain-v2 status digest、完整 refs digest 与 index digest 在 clone/environment 前后完全相等；
- receipt 只保存 repo ref、SHA、摘要、计数、布尔结果和 logical target，不保存 canonical/run-root 绝对路径。

## Private environment 与 cache witness

producer 使用系统已有的 Python 3.11，但所有 writable environment/cache 都位于本轮 disposable root：

1. 解析 `edge-studio-dev/pyproject.toml` 的 direct dependencies，并要求 `onboarding/manifests/python-constraints.txt` 对每个 direct dependency 恰好提供一个 exact version；
2. 使用 `pip --isolated --cache-dir <private>` 和公开 package index，在 private root 构建 wheelhouse；不读取用户 pip config/environment cache，也不写全局 pip cache；
3. 将 wheelhouse artifact 改为只读并记录 artifact count/bytes 与 closed manifest digest；artifact filename、download URL 和内容不进入 receipt；
4. cold environment 只从 private wheelhouse 安装 dependencies，再以 `--no-build-isolation --no-deps --editable` 绑定 scenario 内的 `edge-studio-dev`；
5. 删除 cold venv，使用同一只读 wheelhouse 与 `--no-index` 创建 warm venv；要求 wheelhouse digest 不变、两轮完整 installed-distribution digest 相等；
6. 两轮都必须证明唯一 editable direct URL 指向 scenario package，`import edgestudio` 落在 scenario repo，`.pth` / `direct_url.json` 不包含 canonical source path；
7. 正式 witness 结束后 verifier 只凭 exact parent/name/nonce marker 删除 owned run root；readonly cache 先恢复 owner permissions。cleanup 失败时不写 passed receipt。

当前 constraints 是 reviewed direct-dependency baseline，不冒充 Phase 0 已冻结的完整 transitive lock。正式 receipt 会固定本轮 requirements digest、wheelhouse artifact digest 与 cold/warm installed-distribution digest；完整 product environment lock 仍由 Phase 0 contract 决定。

## Evidence 与隐私边界

- state root、spikes parent 与 run root 为 `0700`；plan/owner marker 为 `0600`；immutable receipt/current view 继续遵循 `0600` contract；
- plan 可以在 private run root 内短暂包含 source path，producer stdout、receipt、tracked 文档不得包含绝对路径、credential、package URL/artifact name 或 raw environment；
- formal receipt 的 `verifier.params={}`，dependency keys 必须精确为 `[DOD-SCOPE-001]`；
- input fingerprint 覆盖 registry、producer/verifier material、三仓 SHA vector、constraints/package metadata、Python executable、wheelhouse manifest 与 scope evidence；elapsed 和临时绝对路径不进入 fingerprint；
- cold cache preparation 需要公开网络，warm install 使用 `--no-index` private wheelhouse；本 gate不声称 offline bootstrap 或 persistent cross-run cache 已完成。

## 明确不证明

本 spike 不证明完整 repo manifest reconciliation、Phase 1 workspace/environment adapter、Git origin/ref/guard acceptance、Host durable state/database、provision crash recovery、physical disk-full、retention/GC、跨 run persistent cache、upgrade/migration、完整产品 dependency lock、产品效率或模型质量改善。

## 正式 witness（2026-08-10）

- fixed implementation：`2bdea5e0d9311da89691eb4a25c646c0455faaf4`，`main` clean 且已 push；onboarding 输入 `cfb6c1f144afbdb9c71316cb6506e7a68668862a`，edge-studio-dev 输入 `a3eff5c02786f089147271bf92e86570d1df0948`；
- implementation review：Claude `20260810-160530-n4kvwp`，P0=0、P1=0、`can_commit_push`；
- closeout ledger：`0ee09e91de394025d4186421377f5b2a292bd2d2`；terminal review `20260810-162530-v7nwqp`，P0=0、P1=0、P2=0、`can_commit_push`，明确 condition 6 成立；
- run：`spike-storage-20260810T093255Z-2270a6ecfe1e`；evidence SHA-256 `a5773ac2b85c1af06407737832bd705d4fad867265c9360ebeaabf8ba528ff60`；input fingerprint `2270a6ecfe1eac01665a4b6dd9334478f2e64dcb5ad056c4011b43e6092c851b`；
- verifier 独立采样峰值 `2,758,377,472` bytes，预算 `107,374,182,400` bytes；98 个 wheel artifact 合计 `365,386,890` bytes；cold/warm 均解析 100 个 distributions，digest 同为 `ef7dbb41c107b9018241300be1b83dee061c9b8ed70a21cba945bd3ef2d429e8`；
- elapsed 仅记录：clone total `6,214 ms`、cache prepare `108,574 ms`、cold `33,674 ms`、warm `33,258 ms`，没有应用时间阈值；
- 独立复核确认 current view 对 immutable evidence 的 path/digest/fingerprint 引用闭合，receipt/current 均为 `0600`，三仓 commit=remote_commit，dependency keys 精确为 `[DOD-SCOPE-001]`，无本机路径/credential/vendor API，owned run root 已删除且 retained bytes=0。

implementation review 的非阻塞 P2 保留为后续 hardening 输入：producer 自报 peak 当前以 cold/warm 两个完整 checkpoint 为主，正式 pass 采用 verifier 的独立周期峰值；Phase -1 手工正式运行只观测单实例，尚未实现跨进程 concurrency lock；部分 survival-implies-true 布尔值与 producer-lie mutation coverage 可在 acceptance 加固。这些边界不改变本次 verifier 的独立重算或 P0/P1 verdict，也不外推为通用 acceptance 已完成。

## Closeout 条件

只有以下条件全部满足后才能标记 `completed`：

1. implementation 固定、clean、push：已完成；
2. Claude 对固定 SHA 给出 `P0=0`、`P1=0`、`can_commit_push`：已完成；
3. 在同一 fixed SHA 上运行正式三仓 witness，并写入 immutable evidence/current view：已完成；
4. 独立复核 evidence/current digest、input fingerprint、三仓 checkout/remote equality、direct dependency、peak allocated bytes、cold/warm environment digest、`0700/0600` 权限与 cleanup：已完成；
5. tracked progress/handoff 记录实现 SHA、review、run/evidence/fingerprint 与下一 required gate：已由 closeout ledger `0ee09e91de394025d4186421377f5b2a292bd2d2` 完成；
6. closeout target 再取得无阻塞 peer review：已由 `20260810-162530-v7nwqp` 完成。

## 固定 commit 后运行

只读 preflight，不 clone、不联网、不创建环境：

```bash
python scripts/verify_ai_collab_storage_spike.py --preflight-only
```

正式 witness：

```bash
python scripts/verify_ai_collab_storage_spike.py \
  --expected-edgestudio-sha <40-char-sha> \
  --expected-onboarding-sha <40-char-sha> \
  --expected-edge-studio-dev-sha <40-char-sha>
```

纯 contract/mutation tests 不 clone 真实仓库、不创建完整环境、不访问网络：

```bash
python -m pytest tests/test_ai_collab_storage_spike.py -q
```

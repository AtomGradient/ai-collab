# Harness 用户裁决记录

本目录保存会改变 Harness gate、产品范围或实施授权的结构化用户裁决。记录只保存裁决日期与必要摘要，不保存聊天原文、PII、credential 或本机绝对路径。

当前记录：

- [gate0_scope_20260806.json](gate0_scope_20260806.json)：用户于 2026-08-06 批准 Phase -1 的 v1 范围、代表性场景、资源预算、排除项和停止条件。
- [phase0_registry_normalization_20260810.json](phase0_registry_normalization_20260810.json)：用户于 2026-08-10 批准进入 Phase 0，并先以只读 dependency graph / invalidation audit 形成 fixed、peer-reviewed registry migration 与 ordered evidence rebuild 方案；方案 review 前不修改 registry 或本机 receipt state。
- [phase0_registry_cutover_20260810.json](phase0_registry_cutover_20260810.json)：用户于 2026-08-10 批准按审计方案执行 cutover：隔离分支实现并审核固定 SHA，P0/P1 均为 0 后 fast-forward `main`，保留 whole-registry digest，按 9 层顺序重建 13 份 immutable evidence 与 1 个 derived current view；失败即停且不自动回滚 `main`。

`evaluation: decision` 的 gate 不能由测试自行“推导通过”。tracked decision 只负责保存用户裁决；recorder 校验其 schema、前置 gate 和固定的 pushed commit 后，才生成本机 immutable decision evidence 与 current view。

固定 commit 经 review 并 push 后记录 current evidence：

```bash
python scripts/record_ai_collab_scope_decision.py \
  --expected-edgestudio-sha <40-char-pushed-sha>
```

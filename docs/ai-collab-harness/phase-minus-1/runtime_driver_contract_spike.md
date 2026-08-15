# Runtime Driver Contract Feasibility

Gate：`SPIKE-RUNTIME-DRIVER-001`

目标：在接入任何真实 runtime 产品前，证明 Host 核心可以只依赖 versioned capability descriptor、`(scenario_id, participant_id)` 和 driver registry 完成 participant add/start/replace，不需要产品名条件分支。

## Witness

- 注册两个 opaque deterministic drivers；
- 声明四个 participant，同时运行三个，证明 witness 预算不等于声明集合或产品上限；
- 同一 driver 启动多个 participant，runtime identity 必须各自唯一；
- `explicit_recreate` 是 required baseline；vendor session identity 与 `exact_resume` 是 descriptor 声明的 optional capability；
- 不受支持的 interaction/continuity 在调用 driver 和改变 durable desired state 前拒绝，禁止静默 downgrade；
- ready ACK 必须匹配 participant identity、generation、driver 与 runtime instance；
- replace 建立新 generation，旧、新 launch spec、model binding 和 binding history 均保留；
- `RuntimeLaunchSpec` 与 `ModelBinding` 使用不可变值对象；
- verifier 解析 `HostPrototype` AST，要求直接控制流中的 `driver_id` 只用于 `_drivers` registry membership/lookup 或 binding ID 比对，并拒绝把它传入 helper/string-dispatch call。

AST guard 是 best-effort 防回归，不进行跨函数 alias/dataflow 的穷举证明；最终结论仍依赖 arbitrary-driver conformance witness、源码 review 与固定 commit evidence，不能把静态计数为零单独解释成架构已被形式化证明。

## 明确不证明

本 spike 不启动真实 runtime 进程，不验证 TTY、iTerm window、真实 session ID、resume、delivery 或 crash rollback。上述能力分别属于 `SPIKE-TUI-ID-001`、`SPIKE-TUI-LIFE-001`、`SPIKE-ITERM-001`、`SPIKE-DELIVERY-001`、`SPIKE-CLOSE-001` 等后续 gate。

## 固定 commit 后运行

```bash
python scripts/verify_ai_collab_runtime_driver_spike.py \
  --expected-edgestudio-sha <40-char-pushed-sha>
```

主要实现：

- `scripts/ai_collab_runtime_driver_spike.py`：disposable Host/driver contract prototype；
- `scripts/verify_ai_collab_runtime_driver_spike.py`：gate contract、前置 evidence、source guard、conformance 与 receipt verifier；
- `scripts/ai_collab_bootstrap_evidence.py`：供后续 Phase -1 verifier 复用的 timeout、exact pushed checkout、dependency digest 与 private receipt 支撑。

已签发的 Stage 0、Immediate、Gate 0 producer 暂不迁移到共享支撑模块，避免仅为代码整理改变 producer digest 并让已有 evidence 无谓 stale；需要迁移时必须作为独立 gate-regeneration change set。

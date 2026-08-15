# AI Collaboration Scenario Harness 文档入口

> 状态：Harness v3.3 既有产品切片与 technical closeout 已完成；当前仓库处于 private extraction staging，正在完成消费者 cutover 与 Codex/Claude 双重完整回归
>
> 文档版本：v3.3
>
> 更新日期：2026-08-16

本目录把 AI Collaboration Scenario Harness 的**通用产品架构**与 **EdgeStudio 集成设计**分开维护，避免项目实现细节反向定义产品内核。

## 阅读入口

1. [product_architecture.md](product_architecture.md)：通用产品真相源。定义 Scenario、Participant、Runtime Driver、Presentation Driver、Collaboration Policy、delivery、状态机、安全边界和产品 conformance；不得包含 EdgeStudio 仓库清单、Python 环境或组织规则。
2. [edgestudio_integration.md](edgestudio_integration.md)：EdgeStudio 集成真相源。定义项目 descriptor、multi-repo workspace、环境、Git guard、PingAgent 迁移、项目 policy、rollout 和 composed gates。
3. [edgestudio_gates.yaml](edgestudio_gates.yaml)：当前 EdgeStudio 集成的机器可读 composed gate registry。它组合产品、平台、driver 与项目 acceptance；表格或 checklist 不是通过证据。
4. [baselines/README.md](baselines/README.md)：实施前现场封存入口；记录 exact SHA vector、非 Git 数据边界、备份状态与恢复完成条件。
5. [decisions/README.md](decisions/README.md)：用户裁决的结构化记录；包含 Gate 0 scope 与 Phase 0 registry normalization/cutover 边界。
6. [phase-minus-1/README.md](phase-minus-1/README.md)：可丢弃 feasibility prototype、逐 gate verifier 与证据边界。
7. [phase-0/README.md](phase-0/README.md)：Phase 0 contract-freeze 入口；记录完整 10-surface closeout、M1–M4 working vertical slice，以及 registry normalization 的历史 audit/cutover/rebuild。
8. [contracts/README.md](contracts/README.md)：Phase 0 machine-readable contracts；包含 Host logical IPC v1、participant driver suite v2、Scenario/Participant state v1、Collaboration Policy/reliable-delivery v1、Workspace/Environment v1、gate registry v2 projection contract，以及 permission/high-risk confirmation v1 contract。
9. [IMPLEMENTATION_PROGRESS.md](IMPLEMENTATION_PROGRESS.md)：tracked 阶段进度与跨机器续接入口；记录固定实现 SHA、peer review 和非敏感 evidence 引用，但不替代本机 gate current view。
10. [CAPABILITY_ALIGNMENT.md](CAPABILITY_ALIGNMENT.md)：Host、PingAgent、CLI/TUI、App、driver、acceptance 和 gate 的持续能力对齐/更新域追踪；用于防止只修单 surface 或提前 formal closeout。
11. [EMPLOYEE_GUIDE.md](EMPLOYEE_GUIDE.md)：P2-A 员工使用指南；覆盖首次安装后的项目登记、双 Agent Scenario、Agent-native 协作、并行隔离、关闭恢复与故障处理。
12. [P2A_EMPLOYEE_RELEASE_CHECKLIST.md](P2A_EMPLOYEE_RELEASE_CHECKLIST.md)：P2-A internal pilot 与 broad employee release 的逐项验收表；不替代机器 gate 或 fixed-SHA review。
13. [HANDOFF_20260809.md](HANDOFF_20260809.md)：2026-08-09 至 Phase 0 实施期的历史交接快照；其中标注为 current/next 的旧指令已被 `IMPLEMENTATION_PROGRESS.md` 取代。

## 真相源边界

| 问题 | 真相源 |
|---|---|
| Scenario/Participant/Runtime/Model 分层 | `product_architecture.md` |
| Host、CLI、App、driver SDK、delivery 与 gate engine | `product_architecture.md` |
| macOS/iTerm2 首发实现 | 产品文档中的 platform/presentation plugin contract |
| EdgeStudio 仓库、路径、Git、Python environment | `edgestudio_integration.md` |
| EdgeStudio review/pushback/escalation 规则 | 项目 `AGENTS.md` 与 integration 文档 |
| 当前 gate 集合、依赖、producer 与 workflow phase | `edgestudio_gates.yaml` |

集成层只能通过版本化 contract 使用产品能力。另一家公司接入时，应只新增自己的 project descriptor、workspace/environment adapter、policy pack、runtime/presentation plugin 与 acceptance registry；不得修改 Host 核心来加入公司名、仓库名或工具特例。

## 仓库边界

本仓是 AI Collab 通用产品的 canonical target：`product_architecture.md`、通用 contract、Host/CLI/App、PingAgent 与 conformance suite 在这里维护。抽离 cutover 完成前，EdgeStudio 中的同名通用文档仍作为迁移期 review mirror；固定 SHA 经 Codex 与 Claude 各自完整回归后，EdgeStudio 只保留 project integration、adapter/manifest/gates、实施账本与迁移指针。

本目录仍含从 EdgeStudio 导入的 integration/progress/evidence 文档，便于 private staging 阶段核对来源和语义；公开前必须完成文档分流与内部信息审查，不能因文件已经迁入就视为适合发布。

## 迁移说明

原文件 `docs/design/ai_collab_scenario_harness_architecture_20260805.md` 保留为迁移指针，避免历史链接失效。自 v3.2 起，本目录中的两份文档与 YAML registry 是 active design source；v3.1 及更早版本只作为历史锚点。

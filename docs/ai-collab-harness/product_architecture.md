# AI Collaboration Scenario Harness 通用产品架构

> 状态：**active design source；Phase -1、Phase 0、M1–M4、延期 hardening、Phase 4 P4-A–P4-D、Phase 5 limited dogfood、native App/internal delivery/recovery、P0 Agent collaboration、P1 lifecycle/operations parity 与 P2-B technical gate/DoD closeout均已闭合；当前续接P2-A adoption/distribution，Hermes真实runtime conformance仍按裁决保留；P2-B完成不等于Developer ID/notarized员工发布完成**
>
> 版本：v3.3
>
> 更新日期：2026-08-15
>
> 目标：把长期 AI 协作任务保存为可恢复、可并行、可扩展且不会串线的本地 Scenario；使用方通过 adapter/plugin 接入，不修改产品核心。

## 0. 产品定位与已裁决原则

本文定义可被不同公司和项目复用的产品内核，不定义任何具体项目的仓库拓扑、语言环境、Git 规则、review 规则或收益目标。

以下原则来自 `user_decision`（2026-08-06）：

- `participants` 是运行时配置集合，架构不得固化数量、产品名或模型提供方；
- 支持 scenario 运行期间动态 add、start、stop、recover、replace 和 detach participant；
- 每个交互式 participant 使用独立顶层 terminal window，不使用同一 tab 内的 split panes；
- collaboration/review routing 是 scenario/task 的显式 policy，不由 Host 写死为固定角色拓扑；
- model/provider 在 participant runtime generation 启动前确定，启动后只读；更换模型必须建立新的 runtime generation，不能原地 rebind；
- Codex、Claude、Hermes、Qwen、DeepSeek 等只可作为非穷举示例，不进入核心 schema 条件分支。

具体测试可以使用有限实例作为 witness，但 contract、状态机、指标和 gate 判据必须量化为“所有 declared participants/targets”，不得从样例或机器 sizing 推导基数上限。

以下原则来自 `user_decision`（2026-08-09）：

- Host core 的 required identity 是 Harness-owned participant/generation、owned process instance 与 exact presentation binding；vendor session identity 不是 participant identity，也不是 required Host dependency；
- vendor session identity、exact resume、hook、App Server 或其他 lifecycle surface 只能作为 Runtime Driver 声明的 versioned optional capability；runtime 升级导致该 surface 不兼容时，只降级对应 driver；
- `explicit_recreate` 是所有 runtime driver 的 baseline continuity；请求未声明的 `exact_resume` 必须在 durable desired state 变化前 fail closed，不能静默降级或用 role、cwd、window、最近 session、`--last`、picker 或私有 store 猜测；
- 可选能力减少不等于 presentation 覆盖回退。每个交互式 participant 仍必须由 Harness-owned process 启动，并与 create response 返回的 exact 顶层 window/tab/session identity 绑定。

以下原则来自 `user_decision`（2026-08-14）的产品目标复核：

- 用户管理的是可恢复、可并行且不会串线的长期任务房间，不是 terminal、目录、session、mailbox、generation 或 fence；
- Agent 之间的双向协作是 Harness v3 核心能力。外部 dogfood runner 代 participant 调用 `message.send` 只能证明 delivery substrate，不能证明日常 Agent-native conversation 已产品化；
- 每个 participant 必须能在自己的 Harness-bound Scenario/Participant identity 下使用简洁的 send/reply 入口，不要求 Agent 或员工手填 project instance ID、generation/revision fence、receiver JSON 或 owner capability；
- macOS App 是员工默认的 collaboration control plane：管理团队/participant、policy/routing、delivery/review health、generation drift、异常和修复，但不复制供应商 TUI 的内容交互；实际模型内容工作继续发生在 Harness 打开的独立 terminal window；
- App 不得让用户选择 sender 后冒充 participant 发言。若未来允许从 App 直接发送内容，必须使用独立 human actor/provenance contract，并另行获得用户裁决；
- CLI 同时服务自动化、诊断、开发和 participant 自身操作；CLI、TUI client 与 App 可以采用不同交互形式，但核心语义和同一 Host authority 必须一致；
- onboarding 建立在可运行协作闭环之上，不能用 first-run 文档替代缺失的 Agent identity、policy、delivery read model 或 repair surface；
- final fingerprint / formal evidence rebuild 必须等上述 P0 产品化切片和对应 acceptance 固定后执行，避免为立即失效的中间 material 反复生成 receipt。

以下原则来自 `user_decision`（2026-08-15）的 P2-A 员工实测修正：

- 对声明 `exact_resume` 的 Runtime Driver，正常 `Close → Resume` 必须恢复同一已绑定的 vendor conversation；新的 terminal presentation 不等于新的 Agent conversation，普通恢复也不轮换 Participant generation；
- raw vendor session identity 只保存在 owner-private driver state。Host、App、receipt 与普通诊断只可使用 digest/opaque ref；只能通过供应商公开、受支持且版本化的 lifecycle surface 采集和恢复，禁止解析 transcript、扫描私有数据库或猜最近会话；
- exact restore 失败必须 fail closed 并显式标记 degraded。若用户接受丢失旧对话，App 必须再次明确确认 `explicit_recreate + Harness handoff`，建立新 generation 和新对话；不得静默 fallback；
- 每个 Participant 在首次用户输入前自动获得 owner-private、provider-neutral、带 revision/digest 的 collaboration context，包含自身 Harness identity、assignments、同 Scenario peers、current policy snapshot、允许的 outbound route 与 reply semantics；共享/跟踪的 `AGENTS.md`、`CLAUDE.md` 不承载动态 identity；
- collaboration context 必须明确区分 Harness peer 与供应商原生 agent discovery/messaging：同 Scenario peer 的可达性和发送结果以 Host/PingAgent为准，供应商原生 discovery 不能覆盖成功的 Harness结果或诱导员工显式指定底层命令；
- collaboration context 只提供模型工作提示，live Host policy、generation fence 与 sender process proof 始终是授权真相源。正常 accepted/delivered/consumed 仍保持 machine-only silent ACK；
- 默认员工安装中，Application Support只保存owner-private控制面状态、receipt、binding和cache；新建Scenario的隔离Workspace保存在`~/Documents/Scenarios`。既有Application Support Workspace不自动搬迁，Host按durable binding兼容解析；自定义测试state root仍可显式使用其隔离workspace root。
- 历史Scenario必须提供列表右键`Force Delete Scenario`的短路径。App先明确告知会丢失该Scenario隔离Workspace中的WIP，Host再对一个`scenario.force-destroy`请求执行一次trusted single-use confirmation；该复合动作只清理能够由frozen runtime/presentation binding精确证明归属的Participant、lease、Scenario Workspace与控制面记录。任一owner proof、Workspace exact binding、revision/generation fence或WIP digest无法复核时都fail closed；注册项目的canonical source永远不属于删除目标。详细页的`Load Destroy Preview → Destroy Scenario`继续作为可检查blocker的保守路径。

## 1. 范围与非目标

### 1.1 产品必须提供

- Scenario 的 create/open/focus/close/recover/destroy 生命周期；
- Participant 的独立 identity、desired/observed state、generation 与动态生命周期；
- Runtime Driver、Presentation Driver、Workspace Adapter 和 Collaboration Policy 的版本化 contract；
- capability-declared runtime continuity、Harness-owned process 与 exact window/session binding、mailbox routing 与 ACK/retry；
- 本地 Host、薄 CLI、可见 App、审计/诊断与高风险确认；
- operation journal、CAS/fencing、crash reconciliation、resource/process supervision；
- machine-evaluable gate/receipt engine 与 conformance harness；
- 项目、runtime、presentation 和 platform plugin 的隔离边界。

### 1.2 产品不提供

- 自动 commit、push、rebase、merge、stash 或丢弃 WIP；
- 模型训练、GRPO、自适应 routing、模型效果或 improvement claim；
- 自动生成并安装未经审核的 hook；
- root daemon、默认 Full Disk Access 或 Accessibility UI scripting；
- 组织自己的 review/quorum/escalation 规则；
- provider credential 代理或任意远程 Shell；
- 跨机器同步和远程控制（v1）。

产品 contract 可扩展不等于首版已经接入所有 runtime/provider。每个生产级 driver 都是独立、可审核的 change set，必须通过同一 conformance suite。

## 2. 核心领域模型

```text
Scenario
├── scenario_id
├── project_binding
├── workspace/environment binding
├── participants: Map<participant_id, Participant>
├── collaboration_policy + digest
├── desired_state / observed_state
└── scenario_generation

Participant
├── participant_id
├── runtime_driver + contract version
├── interaction_mode: tui | headless
├── immutable runtime_launch_spec
│   └── model_binding（optional, non-secret, immutable per generation）
├── continuity_policy: exact_resume | explicit_recreate
├── capability_snapshot
├── Harness-owned process/mailbox binding
├── optional vendor session binding
├── optional presentation binding
├── desired_state / observed_state
└── participant_generation
```

### 2.1 Identity 不变量

- Participant 的持久 identity 是 `(scenario_id, participant_id)`；
- runtime 产品名、display name、协作角色、model/provider、TUI session、窗口 ID 都不是 identity；
- 同一 runtime driver 可以同时存在多个 participant 实例；
- mailbox、ACK、delivery journal、process ownership 和 presentation binding 必须按 participant 分区；
- 后注册实例不得覆盖同 runtime 的先注册实例。

### 2.2 Runtime launch spec 与 model binding

`runtime_launch_spec` 在新 participant generation 启动前校验并冻结。它可以包含 runtime driver 需要的非敏感 model/provider profile 引用，但不得包含 API key、token 或 credential。

运行中不存在修改 model binding 的操作。若用户选择不同模型/provider：

1. 先生成 replace/add plan；
2. 验证新 launch spec 与 driver capability；
3. 建立新的 participant runtime generation；
4. 保留旧 binding、transcript、mailbox 和审计历史；
5. UI 明确显示新旧 generation，不能把新实例伪装成原会话续接。

## 3. 可插拔 contract

### 3.1 Runtime Driver

每个 Runtime Driver 必须提供版本化 capability descriptor：

- `interaction_mode: tui | headless`；
- create/start/stop/health；
- Harness-owned launch/process instance binding；
- required baseline continuity mode：`explicit_recreate`；
- optional capability：vendor session identity 与 `exact_resume`；
- create/resume/recreate/adopt/bind 与 retention 边界；
- ready ACK、delivery ACK 与 session drift signal；
- owned process、日志 redaction、错误类别和 repair capability。

Host 只按 capability 调用 driver，不得按产品名编写生命周期分支。hook、App Server 或 vendor-private lifecycle API 不得成为 Host core 的 required dependency。requested operation/continuity 无能力支持时，必须在改变 durable desired state 前拒绝；不得先提交 CAS 再把 `exact_resume` 静默改成 `explicit_recreate`。driver capability 不兼容只能降级使用它的 participant，不能阻断 Host 或其他 driver。

### 3.2 Presentation Driver

Presentation 是 Participant 的可选投影，不是 identity。v1 首发可以只交付 macOS + iTerm2，但通用 contract 至少表达：

- create/focus/close/health top-level window；
- stable window/session identity；
- geometry capture/restore；
- machine/display-topology fingerprint；
- Automation permission probe 与结构化错误；
- operation/participant generation fencing。

每个 `interaction_mode=tui` participant 对应一个独立顶层 window；headless participant 不创建占位窗口。位置索引只可用于 observed diagnostics，不能作为持久 identity。

Phase 0 machine-readable contract 位于 [contracts/participant_drivers_v2.schema.json](contracts/participant_drivers_v2.schema.json)。它固定 runtime/presentation capability descriptor、typed driver call/ACK values、canonical registry digest、generation-scoped immutable launch/model binding、Harness-owned process binding 与 exact presentation projection；registry population 与 platform/vendor implementation 留在 plugin 层。Host 只按 `(driver_id, contract_version)` registry key 和 capability snapshot dispatch，核心 contract 不依赖 Codex/Claude session API、iTerm 或 macOS transport。

### 3.3 Workspace Adapter

产品核心只认识类型化的：

- plan/provision/status/repair/destroy；
- workspace/environment identity 与 digest；
- WIP/dirty/ownership summary；
- immutable plan、journal 和 receipt；
- 项目声明的 resource/acceptance capability。

monorepo、多仓、Git worktree、容器、Python、Go、Node 或其他实现都属于 adapter，不进入 Host 条件分支。

来源：`user_decision`（2026-08-15）。员工默认安装的新Scenario Workspace物理根目录为`~/Documents/Scenarios`；Host control plane仍在Application Support。该路径策略属于本机platform/configuration，不进入public Workspace contract，也不能暴露到App projection或receipt。升级不得为了采用新默认值移动既有Workspace；新旧根目录必须由exact durable binding解析，重名、symlink或owner不一致时fail closed。

Phase 0 machine-readable contract 位于 [contracts/workspace_environment_v1.schema.json](contracts/workspace_environment_v1.schema.json)。它固定 versioned workspace/environment adapter refs、generation-scoped immutable plan、append-only operation journal、ready receipt 与 aligned/degraded/missing observation；logical component/revision、environment spec/lock/source binding 与 project-specific payload 都由 digest 绑定。product core 不保存 physical machine path/private binding，也不按 SCM 或 environment tool 分支；只有所有 component/environment exact verification 与 atomic publish/registry CAS 同时成立才可形成 ready receipt，结构通过本身仍不等于真实 execution evidence。

### 3.4 Collaboration Policy

实现者、reviewer、observer、lead 等是 policy attribute，不是 participant identity。policy schema 表达允许的 sender/receiver、review assignment 与 route；具体拓扑是 plugin/config，不是固定 enum。

每条入队消息固定 policy version/digest。policy 更新不能把已入队消息静默改投其他 participant，也不能让 Host 自行推导 quorum、broadcast 或 escalation。

Phase 0 machine-readable policy/delivery contract 位于 [contracts/collaboration_policy_delivery_v1.schema.json](contracts/collaboration_policy_delivery_v1.schema.json)。规则采用 ordered first-match + default deny；selector 只接受 exact participant generation 或 namespaced assignment，route decision 只返回声明所得的 exact targets。assignment（例如 implementer/reviewer）是可配置 policy data，不是 identity 或 Host 内置角色。policy pack 只表达纯数据匹配与 retry profile，不执行 shell 或 vendor API；EdgeStudio `AGENTS.md` 的协作规则属于 project policy pack 来源，不进入产品 core。

## 4. Host、App 与信任边界

### 4.1 AI Collab Host

Host 是用户级单一协调主体：

- scenario/participant registry；
- transaction、generation、CAS 与 fencing；
- runtime/presentation/workspace driver registry；
- policy enforcement 与精确 delivery；
- process/resource supervision；
- audit/status/repair 和 gate current view。

Host 只暴露版本化 allowlist operation，不接受任意 Shell/argv。IPC 必须校验 peer identity、owner、request schema、scenario capability 与 generation。

Phase 0 的 machine-readable logical contract 位于 [contracts/host_ipc_v1.schema.json](contracts/host_ipc_v1.schema.json)。它把 platform transport 隔离为 plugin binding，并固定 12 个 handshake/request/reply/progress/cancel message variants、allowlisted operation descriptor meta-contract、target/generation fencing 与 structured error/mutation semantics。具体 scenario/participant/driver/policy payload 由各自 contract surface 以 schema digest 接入；因此 macOS XPC 可实现该 contract，但 XPC 类名、Codex/Claude API 或 Phase -1 的单一 `status` probe 都不是 Host core public API。

### 4.2 App 与 CLI

- CLI 是薄客户端，不复制生命周期逻辑；
- App 提供 scenario/participant 状态、权限、错误、资源、policy、receipt 与高风险确认；
- App 退出后，已运行 scenario 仍由 Host 监督；
- CLI、TUI client 与 App 使用同一类型化 backend。

来源：`user_decision`（2026-08-14）。相同 backend 不要求逐按钮镜像，但要求员工、自动化和 participant 三种入口形成同一语义闭环：

- participant/TUI client 以自己的 scoped identity 发送和回复，不继承可任意冒充其他 participant 的 owner control surface；
- PingAgent 可以提供 participant-facing 的简洁命令和 exact-session transport，但 sender authority、route decision、delivery journal 与 ACK state 仍只属于 Host；Harness context 缺失时的 legacy mailbox compatibility 不得成为 Scenario 内的 fallback route；
- CLI 为自动化保留 raw JSON/fence 入口，同时为 participant 提供从 trusted launch context 解析 exact self identity 的简洁入口；
- App 展示并管理 collaboration topology、policy version、delivery/consumption 状态、generation drift 与 repair action，不要求员工理解底层 route selector、ACK 或 fence；
- 低层 `delivery.consume` 等 driver/automation operation 不必逐一成为 App 按钮，但 App 必须提供等价的状态可见性和用户可执行恢复路径；
- App 不是 participant，不以任何 participant 身份创建消息 provenance。

来源：`user_decision`（2026-08-13）。Harness 同时服务自动化研究与真实员工日常操作；App 是员工默认入口，CLI 保留给自动化、诊断与开发，不要求员工理解 project instance ID、Scenario fence、socket 或 adapter 参数。这个易用性要求不把 authority 移到 App：

- App 只把用户选择的目录作为一次 typed `project.register` intent 交给 Host；
- Host 独立验证 owner、canonical root、project descriptor、manifest 与 adapter capability，生成 machine-local opaque `project_instance_id`，并在 owner-private state 中保存规范路径/fingerprint；
- `project.list` 只返回 redacted public record，不返回 physical path/fingerprint；App 不扫描仓库、不推导 identity，也不复制 workspace、participant、permission 或 vendor lifecycle 逻辑；
- `participant.list` 必须以 exact Scenario target 查询；Host-scoped template discovery 只返回 Runtime Driver 提供的可选模板数据，不在 Host core 固化供应商或 participant 拓扑；
- App 的 destructive intent 不能代替 Host 的 fresh permission、target/fence、effect preview 与 trusted single-use confirmation。

来源：`user_decision`（2026-08-15）。员工删除历史Scenario不应被迫先逐项Resume/Close或进入详情页完成两步preview。`scenario.force-destroy`是同一Host authority下的高风险复合operation：它可以从running、degraded或closed状态开始，先按冻结binding精确停止Harness-owned Participant并释放该Scenario lease，再删除exact isolated Workspace并注销Scenario。App右键入口只负责选择目标、解释损失并取得用户意图，不能自行杀进程、删目录或降低Host确认；Host只签发一次mutation authorization，partial/crash结果仍由durable journal保留为可诊断状态。该新增员工路径不修改既有`scenario.destroy`的closed/stopped/released条件，也不重写已闭合`ACC-DESTROY-001`的gate语义。

首个 native SwiftUI vertical slice 可以先连接已运行的 current-user Host，以验证同一 typed IPC、状态展示和日常操作流；这只属于 developer/dogfood delivery。2026-08-13 后续 fixed slices 已产出 Apple Development 签名 App，内嵌独立 current-user Host runtime/helper，并由 `SMAppService` 完成用户级注册、启动、KeepAlive 与注销；稳定 installer 对候选 App 执行 identity/code-seal 校验，以 APFS 原子替换安装，升级后等待 typed Host health，失败时自动恢复上一版本并保留失败候选供诊断。用户不需在终端手工启动 Host。

来源：`evidence`（2026-08-15，新候选seal有效、旧安装仅因HarnessService下9个`__pycache__`目录/77个`.pyc`被codesign判为added resources）。若旧安装只因embedded Python遗留的owner-owned regular `__pycache__/*.pyc`破坏seal，installer可先把该精确集合移入owner-private quarantine，并且仅在原App deep/strict seal随后恢复时继续；任何其他bundle差异都必须恢复cache并fail closed。

同日在 macOS 26.5.1 与同 Team ID Apple Development 签名组合上完成本机受保护项目 witness：清除 App 的既有 TCC 授权后，用户经 `NSOpenPanel` 选择真实受保护项目并观察到系统权限提示；项目成功登记，随后 App 退出、Host 被 `SIGKILL`、launchd 重启 Host，恢复后的 Host 在 App 未运行且没有新增用户交互时仍能重新验证并登记同一项目，project identity 与 binding digest 保持不变。该结果证明当前 internal/dogfood 组合不需要新增 bookmark broker 或扩大 public IPC；只覆盖用户明确选择的目录、当前 macOS 与当前签名身份，不外推 Developer ID/notarized 分发、其他 macOS 版本或未由用户选择的目录。最终员工分发和整个 Harness 仍须通过各自剩余 gate。

### 4.3 权限原则

- Host 以当前用户运行，不使用 root；
- platform plugin 集中处理系统 Automation/background permission；
- presentation driver 不得依赖 Accessibility 模拟点击；
- Host 不保存 Git/SSH/provider private key 或 token；
- destroy、force stop、resource break 等高风险动作需要明确确认与 receipt。

Phase 0 的 machine-readable logical contract 位于 [contracts/permission_confirmation_v1.schema.json](contracts/permission_confirmation_v1.schema.json)。它把 permission observation 与 capability 分离，以 operation descriptor exact join、当前且 subject-scoped 的 `granted` snapshot、challenge/decision/authorization/consumption 链固定准入边界。destructive/high-risk operation 只能使用带 typed effect preview 的 exact single-use authorization；bounded scope 仅允许非 high-risk operation，且每次使用都重验 permission、target/fence 与 membership evidence。该 contract 不探测系统权限、不触发 prompt、不实现 trusted App UI，也不把 consumption receipt 冒充 operation success；这些 platform mechanics 与 acceptance 留在 Phase 4。

## 5. Durable state 与事务

### 5.1 类型化 state

必须区分：

- `scenario_id`、`participant_id`；
- `runtime_driver_id`、Harness-owned `runtime_instance_id` 与 process identity；
- optional vendor `tui_session_id`；
- presentation window/session UUID；
- `display_topology_fingerprint`；
- `operation_id`；
- `host_generation`、`participant_generation`。

Harness process binding 与可选的精确 runtime/TUI ID 只存放在 Host-owned durable state；日志、receipt、mailbox 通知和普通 UI 默认展示 redacted fingerprint。没有声明 vendor session identity capability 的 driver 不得伪造或猜测 `tui_session_id`。

### 5.2 短事务

外部操作不得长期持锁：

1. 加锁并 CAS desired/observed state，生成 operation/generation；
2. 释放锁后执行 driver/adapter 外部动作；
3. callback/ACK 带完整 identity 与 generation；
4. 重新加锁，只在 generation 匹配时 finalize；
5. stale controller/watch/bind 自动退出；
6. Host 重启从 journal 和 observed state reconcile。

## 6. 生命周期

### 6.1 Scenario aggregate

```text
unregistered --create--> provisioning --success--> closed
                            │ failure                 │ open
                            ▼                         ▼
                     provision_failed             opening
                                                    │ success/all desired ready
                                                    ▼
                                                  running
                                                    │ participant/host failure
                                                    ▼
                         repair/reconcile <───── degraded
                                                    │ close
                                                    ▼
                                                  closing
                                                    │ complete
                                                    ▼
                                                  closed

closed --gated destroy--> destroying --complete--> unregistered
                              │ failure
                              ▼
                           degraded --repair--> repairing --resume destroy--> destroying
```

Scenario 状态是 workspace、process 与 participants 的聚合投影。单个 participant degraded 不关闭或重绑其他 ready participant；scenario 显示 degraded，但 ready participant 继续工作。

### 6.2 Participant lifecycle

```text
unregistered --add--> stopped --start/open--> starting --ready--> ready
                     ▲                    │ failure       │ stop/detach/replace
                     │                    ▼               ▼
                     └── stop success ─ stopping <── degraded
                                            │ failure        │ recover
                                            └──────────────► degraded
                                                               │
                                                               ▼
                                                          recovering
                                                           │       │
                                                   success │       │ failure
                                                           ▼       ▼
                                           stopped (generation+1) degraded

stopped --detach--> detached
detached --gated destroy--> unregistered
stopped --replace plan/CAS--> replacing --success--> stopped or starting
                                      └── failure --> degraded
```

规范语义：

- stop/detach 可以从 `ready | degraded` 进入 `stopping`；
- `stopping --success--> stopped`，失败回到 `degraded` 并保留 owned-process 证据；
- detach request 立即把 desired state 设为 `detached` 并停止新投递；observed cleanup 未完成时保持 `degraded(cleanup_pending)`，允许 retry stop、repair 或 gated confirmed stop，不删除 registry 历史；
- `degraded --recover--> recovering` 先固定失败 generation；driver 只能对 exact Harness-owned binding 做正常清理，或证明启动尚未进入外部资源创建阶段。成功后保留旧 generation 的 journal/history 并切换到 `stopped (generation+1)`，再由显式 Start 启动；证据歧义或清理失败回到 degraded，不自动 force-stop、不复用失败 identity；
- replace 是 compound operation：先验证新 launch spec，再安全停止旧 generation，CAS 新 binding generation，并按先前 desired state 启动；CAS 前失败保留旧 binding，CAS 后失败进入可 rollback/repair 的 degraded；
- add 只从不存在的 current record 建立 generation/revision 1；detach 保留 current record 与 audit history，只有独立 gated destroy 才移除 current record；
- open/repair/destroy 的失败进入 degraded 并保留 desired state；当 desired 为 destroyed 时，repair 必须能够恢复到 destroying，不能形成清理死锁；
- 只有显式 create/open/close/destroy/add/start/stop/recover/detach/replace 请求可以改变 desired state；recover 显式把目标收敛到 stopped，ready/failure/recover completion/cleanup/replace completion 只能 preserve，不能借完成回调静默改写用户意图；
- 普通 transition 不改变 generation；participant replace 与 recovery success 分别为新 launch identity 和失败 identity 隔离而精确递增一个 generation。

Phase 0 machine-readable state contract 位于 [contracts/scenario_participant_state_v1.schema.json](contracts/scenario_participant_state_v1.schema.json)。它把 desired/observed state、scenario/participant generation、state revision、lifecycle operation plan、CAS precondition fence 与 append-only journal 分开表达；absence 以 null generation/revision fence 表达，persisted record 的初始 generation/revision 均为 1。external driver/adapter action 必须在释放 state lock 后执行；immutable operation fence 是 CAS 前置快照，desired commit 的 target revision 与 resulting generation 共同成为 callback finalize fence，journal revision 必须连续。2026-08-14 用户裁决进一步冻结 recovery 的 cleanup-or-absence proof、歧义 fail-closed、旧 generation evidence retention 与新 stopped generation 语义。contract 通过 `runtime_binding_id` 引用 participant driver v2，不复制 driver payload；policy、permission 以及 force-stop/destroy mechanics 仍由各自 contract surface 管理。

## 7. Continuity、ready 与 presentation binding

Host 遍历 desired-running participants，按 capability 和 continuity policy执行：

- `explicit_recreate`：所有 driver 必须支持的 baseline；从 scenario durable context 创建新的 Harness-owned process instance，记录 binding 链，并明确显示“重建而非续接”；
- `exact_resume`：只有 capability descriptor 同时声明 vendor session identity 与 exact resume 时才可请求；恢复已经精确绑定的 vendor runtime/TUI session；
- capability 缺失或版本不兼容时，在 durable desired state 变化前返回 `unsupported`；禁止运行时静默 downgrade；
- 禁止扫描私有 TUI 数据库、使用 role、cwd、window/position、最近 session、`--last` 或 picker 猜测；
- resume/recreate 失败只降级目标 participant；
- 每个 ready ACK 必须匹配 scenario、participant、Harness-owned process 与 generation；声明 vendor session identity 的 fresh interactive driver可以在窗口已经input-ready、但员工尚未提交首条真实输入时返回nullable pending digest，此时不得声称已经恢复了既有conversation；一旦vendor conversation物化，driver必须在owner-private state中绑定exact identity；
- 只有 ready participant 激活自己的 delivery。

对已经 clean close 的 `exact_resume` Participant，Host 保留 generation-scoped opaque continuity binding；Close 只释放 Harness-owned presentation/process/resource，不删除该 binding。Resume 创建新的 Harness-owned terminal presentation/process chain，Runtime Driver 通过已声明的 lifecycle surface恢复并核对原 vendor conversation，再签发 ready ACK。public binding 只携带 vendor identity digest，raw identity 留在该 generation 的 owner-private driver root。

来源：`evidence`（2026-08-15，Codex CLI 0.147.0真实interactive/exec/resume对照）。Codex的新interactive TUI在仅打开至input-ready时还没有物化可恢复conversation，`SessionStart`会在员工提交首条真实prompt时触发；`codex resume <exact UUID>`会在无新prompt时先加载旧历史，同一hook仍到下一条真实输入才触发。因此continuity binding采用两阶段：fresh input-ready可为`pending`，不能用合成prompt、`--last`、cwd扫描或私有store猜测强行制造identity；首次真实输入后hook静默绑定，status/supervision与normal Close/Stop在资源仍受Harness拥有时固化并严格校验。若fresh窗口从未收到输入，则没有vendor conversation需要恢复；若已有binding，Resume必须显式传入exact raw identity，旧历史成功加载后才ready，后续hook proof必须与同一identity及`source=resume`一致，否则fail closed。该时序只属于optional runtime adapter，不进入Host供应商分支。

来源：`evidence`（2026-08-15，fresh r4员工启动失败与installed packaged runtime隔离复现）。optional vendor lifecycle不得成为fresh baseline launch的同步阻塞：Runtime Driver可以在TUI input-ready后返回pending或已预分配的identity digest，hook在实际触发时静默固化/复核，不轮询等待模型会话事件。launch diagnostic必须覆盖dependency、presentation connection、topology与launch material等window-create前阶段；只有owner-private evidence明确证明尚未进入外部创建边界时，Recover才可把失败generation轮换为stopped。window-create结果未知或任何post-create异常仍保留cleanup pending并fail closed，禁止以“可能只是瞬时错误”为由重复创建窗口。

exact restore 的任何 capability/version/source/identity mismatch 都只使目标 Participant degraded。产品不得自动改成 `explicit_recreate`。App 可以在清理状态明确后提供 `Recreate + Handoff`，但必须先说明：Workspace/WIP、Harness journal/delivery 与当前 collaboration context会保留，旧 vendor conversation 不会恢复；用户确认后 replace 才建立新 Participant generation。

来源：`user_decision`（2026-08-12）。对交互式 terminal participant，Harness-owned process instance 是一条受监管的 process chain，不要求 supervisor 本身等于 terminal foreground job。Runtime Driver 必须以 private ACK 固定 supervisor、launch-root child、participant/generation/token 与 owned process group；Presentation Driver 观察 exact create-response session 的 foreground PID 后，只能在该 PID 是 launch-root 或经内核 PPID chain 证明的 live descendant、且 process group 匹配时建立 binding。供应商 wrapper 可以继续启动 native descendant，但层级差异不得进入 Host 产品分支。

同一 process group 不是充分 ownership 证据，process name 也只能作为 driver-private readiness/discovery hint，不能替代 exact descendant chain。durable private binding 的 process fingerprint 覆盖 supervisor、launch-root、accepted foreground job 与 process group；公开 receipt/UI 只展示 hash/布尔结果。stop/close 必须分别确认 launch-root 与 accepted foreground job 已消失，不能用 terminal window 消失反推 process cleanup。

## 8. 可靠消息投递

Canonical delivery enum 只有：

```text
queued -> delivery_attempted -> delivered -> consumed
          │ failure              │ ACK timeout
          └──── bounded retry ────┘
```

- 文件存在不等于 delivered；sidecar 不等于 receipt；
- delivery target 包含 scenario ID、from/to participant ID、runtime/presentation binding 与 generation；
- ACK 不匹配 fail closed；
- Host 重启恢复所有非终态 delivery：queued/delivery_attempted 继续 exact bounded dispatch，delivered-but-unconsumed 只恢复 consumption supervision，不得重复注入；
- retry 有上限和退避，超限使目标 participant delivery degraded；
- 同 runtime 多实例和跨 scenario 都不得 fallback 到 role、最近 session 或其他 mailbox。

同一 Phase 0 contract 把 enqueue provenance、policy/retry snapshot 与 append-only delivery events 固定为闭合值。`delivered` 只能由 exact scenario/sender/receiver/generation/runtime/presentation/payload/attempt ACK 推进，`consumed` 还必须绑定已接受 delivery ACK 的 digest；TUI 需要 presentation binding，headless 明确不需要。vendor session identity 仍只是 driver optional capability，既不是 route key，也不是 ACK 正确性的前置条件。真实 Host store、transport adapter 与 agent consumption witness 留给后续实现/acceptance phase。

来源：`user_decision`（2026-08-14）。participant `message.send-self` / `message.reply-self` 的完成边界是 Host 已完成 policy/identity/fence 校验，并以原子替换、文件与目录 `fsync` 把 route、private envelope、request replay record 和初始 `queued` delivery 持久化；调用方随后立即取得不可变 `accepted + durably_enqueued` 快照，不等待 exact-session transport、receiver 推理或 consumption。Host 独立监督 dispatch → delivery ACK → consumption ACK、bounded retry 与 restart recovery；调用 shell、Agent tool 或 App 连接断开不能取消已接受消息。当前状态与最终 consumption 通过独立 `delivery.status/list`（以及未来显式 wait surface）观察，不能把 acceptance 快照伪装成 `consumed`。

正常 `accepted/delivered/consumed` receipt 是 Host 机器状态，只供 UI、status、gate 与审计消费：不得重新注入任一 Agent 会话成为“对方已收到”的自然语言消息，不得触发模型专门生成 ACK，不得形成 ACK-of-ACK 循环。业务消息仍可触发业务推理和业务 reply；异常、超时或 degraded 可以通过结构化诊断要求人或 Agent 采取行动，但不能把正常 receipt 当作新业务内容。该边界同时适用于员工交互与自动化研究，避免供应商 tool timeout 决定消息正确性。

Agent-visible delivery notification 必须按 message kind 区分 reply 责任：`request/question/review-request/pushback` 可以要求完成工作后沿原 thread 回复；`response/review-response/notice/done` 默认是 terminal/informational，只消费，不要求再发送 Harness-tracked receipt。terminal payload 若明确提出一项新的业务工作，可以执行该工作，但不得仅为“收到”创建新 delivery。

来源：`user_decision`（2026-08-14）。日常 Agent-native conversation 还必须满足：

- send/reply request 的 sender 由 Harness-issued participant-scoped launch context 与 current generation 共同确定，不能由普通 App 表单或未绑定的 CLI 参数冒充；
- scoped sender proof 优先由本地 IPC peer/process identity 与 Host 已绑定的 Harness-owned descendant chain 建立；普通环境变量只能帮助定位请求，不能单独成为身份依据。PingAgent 将已核验 intent 交给 Host，并按 Host 返回的 exact target 执行 transport；
- receiver 可以由当前 policy 的 namespaced assignment 或 exact participant selector 表达，但 participant-facing client 不暴露底层 project/fence JSON；
- Scenario 必须提供 redacted delivery collection/read model，使 App 和 CLI 可按 Scenario 查看 sender、receiver、message kind、state、degraded reason 与 retry eligibility，而不是要求调用方事先持有 delivery ID；
- participant generation 轮换后，旧 policy snapshot 和既有 delivery 保持不可变；新消息必须先取得明确的新 policy plan/apply 结果，禁止静默把旧 generation route 改投新 generation；
- acceptance 必须包含两个真实 participant 分别以自身身份完成 send、consume、reply 的闭环，外部 runner 只允许负责启动和观察，不能代替任一 sender。

### 8.1 Participant collaboration context

Host 在 Participant start/resume/replace 前生成独立于供应商的私有 manifest。manifest 至少固定 context revision/digest、Scenario 与自身 generation、assignments、peers、current policy version/digest、policy-derived outbound route 以及上述 reply semantics；团队或 policy/generation 变化必须形成新 digest/revision。manifest 文件 owner-only，不能进入 Workspace Git、mailbox 正文或 public App model。

Runtime adapter 使用供应商支持的 system/session-start context surface，在首次用户输入前注入同一份语义；不能依赖员工先解释“你是谁、同事是谁、怎么 ai-ping”。供应商不支持安全注入时，该 capability fail closed或明确 degraded，不能写动态 `AGENTS.md`/`CLAUDE.md` 污染共享 Workspace。运行中 policy 仍由 Host 每次发送时重新校验；旧 context 即使存在也不能扩大权限。普通 lifecycle resume 会重新注入 current context，未来需要无重启热刷新时必须沿 versioned lifecycle capability 增量实现。

同一context必须指示Agent：列出的peer通过Harness `ai-ping`触达，不使用供应商原生agent discovery/messaging替代；`ai-ping`返回的Host结果是发送事实真相源。若Host已返回成功，Agent不得再根据供应商原生discovery声称peer不可达；若Host拒绝，则只报告该结构化失败与建议动作。

## 9. Window 与 display topology

- presentation geometry 按 `(machine_id, display_topology_fingerprint)` 保存；
- 显示器拔插、系统重排、App/Host 重启触发 reconcile；
- stable presentation identity 与 geometry/位置索引分离；
- 单窗失败只影响对应 participant；
- 窗口数量来自 declared interactive participants，contract 不声明基数上限。

## 10. Close、process 与 resource

- 来源：`user_decision`（2026-08-12）。Harness 同时服务自动化研究与有人参与的日常工作；员工可以主动输入、监控或查看交互式 participant。因此 TUI 屏幕内容、输入框 placeholder、最近一次键盘活动与供应商的 idle/busy 推断只能用于观察，不能成为显式 close 的授权或阻塞条件。`scenario.close` 对 exact scenario/participant/generation 的 Harness-owned window/process binding 执行 requested close；只有 exact window 与对应 owned process chain 都确认消失才成功。
- `requested`：显式 close 已在 exact owned binding 上完成；不声称 participant 此前 idle；
- `idle`：正常停止；
- `busy`：展示 owner/command/start time，有界 drain；
- `timeout`：requested close 后仍未证明 owned window/process 全部消失，不声称成功；
- `unknown`：degraded，不声称关闭成功；
- force stop 只处理精确 scenario/participant/generation 的 owned process，不修改 WIP。

Resource contract 支持项目声明的 port、device、compute、accelerator、exclusive runtime 和其他 machine-shared mutable resource。lease 必须包含 holder identity、process start、boot ID、heartbeat 和 fencing token；无法证明已释放时只能 stale，不自动抢占。

## 11. 命令 contract

```text
ai-collab project register
ai-collab create/open/switch/list/status/preflight/repair/close
ai-collab participant list/add/start/stop/recover/replace/detach
ai-collab policy show/plan/apply
ai-collab message send/reply/list/status/retry
ai-collab resource list/break
ai-collab destroy --dry-run
```

participant-facing `message send/reply` 从 trusted launch context 取得 exact self identity；owner/automation CLI 可以保留显式 target/fence 的低层形式，两者必须调用同一 Host operation semantics。不存在运行中 model-binding update 命令。participant add/replace 的 launch spec 在执行前冻结；detach 保留历史。所有长操作必须支持 progress、timeout、cancel，并返回保留状态和 repair 动作。

## 12. Gate 与 receipt engine

Gate registry 是集合、分类、producer、verifier params、依赖和 workflow phase 的机器真相源。

- verifier evidence：`cacheable | revocable`；
- aggregate current view：`derived`，每次从 dependency 的当前状态重算，不保存独立 pass evidence；
- Immediate/Phase -1 可由固定版本 bootstrap verifier 生成 evidence，Host 落地后导入/重放，避免自举环；
- immutable evidence 写入 `receipts/runs/<run-id>/<gate-id>.json`；`receipts/gates/<gate-id>.json` 只是 current view；
- fingerprint 至少覆盖 registry、producer、verifier/params、Host/CLI/App、driver registry、platform 和声明项目输入；
- `depends_on` 表达验证依赖；workflow phase assignment 单独表达实施顺序；
- 每个 gate 必须恰好映射到一个 workflow phase；phase 和 reporting `group` 是不同 namespace；
- registry 缺失、digest 不匹配、未知/重复 gate、悬空依赖、phase 未分配都 fail closed。

Phase 0 machine-readable loader/projection contract 位于 [contracts/gate_registry_v2.schema.json](contracts/gate_registry_v2.schema.json)，共享实现只读加载 tracked [edgestudio_gates.yaml](edgestudio_gates.yaml)，输出 registry snapshot、status-free gate projection 与 workflow phase projection。fingerprint 的 `registry_digest` 仍是完整 YAML source bytes 的 SHA-256；canonical registry digest 只用于 semantic comparison，per-gate definition digest 只用于 projection identity，不能替代 receipt evidence identity。结构 projection 明确不观察 inventory、不读取 machine state，也不宣称 current pass/freshness；current-status/evidence engine 与一次性 fingerprint wiring 属于后续实施边界。

Evidence hygiene 来源于 `user_decision`（2026-08-10），是对 Phase -1 已采用的独立重算、source guard 与 mutation testing 实践的显式固化，不追溯否定此前已经闭环的 gate：

- receipt 必须明确记录实际行使的 substrate、fixture/test-double 边界和未行使的平台、产品 schema 与外部能力；disposable local-component witness 不得外推为真实 App package、platform service、production database 或供应商 runtime upgrade；
- producer 的 `passed`、布尔声明、版本字符串或两个内容不同的文件都不能单独证明升级、隔离或清理；verifier 必须尽可能从 raw state、journal、process observation、artifact digest 与 workspace snapshot 独立重算；
- 若 v1/v2 compatibility 是 claim，两个版本必须具有可验证的结构差异和独立 entry source/artifact manifest；同一实现只切换版本参数不能冒充 component handoff；
- mutation/meta tests 必须证明关键 producer lie、retained old generation、stale request acceptance、partial transition、rollback epoch regression 与 WIP drift 会被 verifier 拒绝；
- workflow 顺序或 aggregate dependency 不等于 receipt direct dependency。只有 probe 实际消费的 evidence 才进入 `dependency_evidence` 和 freshness 链，避免把无关 platform/vendor material 收编为必要依赖。

Phase 0 normalization debt 来源于 `evidence`（2026-08-10，fixed verifier、正式 receipt 与 `DOD-SPIKES-001` aggregate 审计）：`DOD-SCOPE-001` 以及除 upgrade 外的 10 个 Phase -1 SPIKE receipt 都记录了实际消费的 `dependency_evidence`，而 cutover 前 registry block 未完整声明 `depends_on`。receipt-level evidence 当时仍有效，因为 gate verifier 与 aggregate 会重算已记录 dependency SHA/current-view consistency；但 registry-only graph 不完整。用户于 2026-08-10 批准只读 audit 后，又批准补齐精确 direct dependencies 与 verifier mirrors；fixed implementation `ec9a9b4e77c895803d4d8b63bb59145355a2537c` 经 P0/P1=0 review 后于 2026-08-11 fast-forward `main`，保持 whole-registry digest 并按 9 层顺序重建 13 immutable + 1 derived，machine audit 为 mismatch 0。该 cutover 只清偿 graph completeness 债务，不能被解释为其余 Phase 0 contract 已冻结；per-gate/sliced digest 仍未授权。

默认 receipt 威胁模型只覆盖误改、过期、错误 producer 和 drift，不宣称抵御已取得当前用户任意代码执行能力的恶意进程。若要抵御同用户 participant 主动伪造，需要用户另行裁决 Host-held signing key、签名 receipt 与受限签名 API。

## 13. Product conformance

产品 conformance 至少证明：

- identity：同 runtime 多实例不覆盖；
- lifecycle：动态 add/detach/replace、故障隔离和 crash reconcile；
- continuity：所有 driver 的 explicit recreate baseline、声明 exact-resume capability 的 driver 的 exact resume，以及 unsupported 在 desired-state mutation 前 fail closed；两种路径均无 role/cwd/window/最近会话 fallback；
- presentation：每交互式 participant 独立窗口、headless 无窗口、topology restore；
- policy/delivery：精确 participant route、ACK/retry、zero cross-delivery；
- Agent-native conversation：两个真实 participant 分别使用自己的 scoped identity 完成 send/consume/reply，App 可观察 topology 和 delivery health，外部 runner 不代发；
- security：typed IPC、permission、destroy/resource/stop fail closed；
- gate engine：bootstrap、freshness、derived aggregate、phase assignment 与 projection consistency。

项目 acceptance 不能替代 product conformance；产品 conformance 也不能证明具体项目 workspace/Git/environment 正确。

## 14. 实施阶段

1. Immediate：修复当前生产入口的已知安全缺陷；
2. Gate 0：用户确认范围、收益、预算和停止条件；
3. Phase -1：验证 Host/IPC、runtime/presentation driver、continuity、delivery、topology、close/upgrade feasibility；
4. Phase 0：冻结 schema、driver/adapter/policy/gate contract；
5. Phase 1：实现 project-neutral workspace/environment adapter execution；
6. Phase 2：实现 App/Host/registry 与 typed operations；
7. Phase 3：实现 participant/runtime/presentation/session/delivery；
8. Phase 4：实现 close/resource/security/diagnostics；
9. Phase 5：有限 dogfood，以项目 acceptance 组合发布条件。

进入下一阶段必须满足 composed registry 的 phase entry/exit requirements，不得用文档勾选代替 evidence。

### 14.1 当前交付节奏

来源：`user_decision`（2026-08-11）。为避免继续停留在 contract/hardening 细节中，当前实现按四个可运行纵向切片推进：M1 是最小 Host、typed local IPC、薄 CLI 与单 Scenario durable path；M2 接入 project-neutral workspace/environment execution；M3 接入 generic runtime 与 iTerm presentation；M4 接入 typed delivery/PingAgent 并完成首次 dogfood。fingerprint cutover 与非阻塞 hardening 在 M4 dogfood 后统一回收。

这只是实现批次，不重写上面的产品阶段或 gate 语义。每个切片只能声明其实际执行和验证过的能力；M1 的私有空 workspace binding 只用于形成真实、隔离且不污染项目源目录的 Scenario state，不得冒充 M2 的 adapter plan/receipt/environment execution。M2 必须在扩大 participant/runtime surface 前补齐 Phase 1 的 project-neutral execution。

来源：`user_decision`（2026-08-14）。在 native App、真实 recovery 和三个 limited dogfood candidate 后，代码审计确认当前 delivery 的发送方仍由外部 runner 代为调用，App 只显示 raw policy 且没有 Scenario-scoped delivery read model。此前“立即冻结 fingerprint 并 formal rebuild”的续接点因此被 supersede；当前按以下顺序完成产品化，再进入一次性 closeout：

1. **P0 Agent collaboration**：participant-scoped self identity 与 send/reply；policy/team plan；Scenario-scoped delivery collection；App collaboration topology/health/repair；真实 participant 双向 reply 与并行 Scenario no-cross witness；
2. **P1 lifecycle/operations parity**：实现已批准但缺失的 replace/detach；补齐 App repair/force-stop/resource-break、preflight/permission/actionable error、generation/profile/model 可见性、focus/topology restore、progress/cancel 和额外 runtime profile；
3. **P2 adoption/closeout**：在稳定产品流上更新 onboarding/员工文档与分发；修正受影响 gate，冻结最终 material，一次性重建 acceptance 与全部 required DoD current views；Developer ID/notarization、optional exact vendor resume 和 cross-machine 仍按各自授权边界处理。

持续状态、代码域、surface parity、acceptance 和 invalidation trigger 由 [CAPABILITY_ALIGNMENT.md](CAPABILITY_ALIGNMENT.md) 跟踪；它是 current-state routing 表，不替代本文件的 normative contract 或 gate registry。

P0-B fixed implementation（2026-08-14）已把 project-provided team/policy template discovery、current-generation plan/effect preview、digest-fenced explicit apply 与 policy generation drift 接入通用 Host/CLI，固定锚点为 edge-studio-dev `fdbb94cf8bfde07278c60531dae051e8fade563d`、EdgeStudio root code anchor `7edeb7970083b13565e7591997b2212d9ff84aea` 与 reviewed target `3148198054ce0af4a9d13405259ad42b92ab378b`。旧 policy/delivery 保持不可变，drift 只阻止新 send 并要求重做 plan；产品 core 不出现供应商名或固定双人/三人拓扑。Claude fixed-SHA review `20260814-134145-ni8mxf` 为 P0=0/P1=0/`can_commit_push`，P0-B 已终止式闭合；App collaboration control plane随后按 P0-D完成，template tamper测试 follow-up进入 P0-E candidate。

P0-C fixed implementation（2026-08-14）在 edge-studio-dev `d75fb8e2031d4245237ede3640ca9280f0b7c2ed` 增加 Scenario-scoped `delivery.list`、thread-root filter 与 digest-fenced bounded pagination，reviewed root target 为 `938061a1f07d17afd1288a37ca894a5760f29b8f`。public projection只暴露 redacted participant generation、message kind、policy/thread identity、state/event/degraded/retry health；正文与 private binding/ACK/transport evidence留在 owner-private durable envelope。retry eligibility同时区分本 Host active attempt 与前一 Host crash 遗留的 unknown attempt，前者拒绝并发 retry，后者保持精确恢复。Claude review `20260814-135513-h2igth` 为 P0=0、P1=0、P2=1、`can_commit_push`；active attempt 可使分页 cursor安全失效，App/CLI 应 refresh。P0-C 已终止式闭合，App 呈现和允许的 retry/replan action按 P0-D 实现。

P0-D superseding fixed implementation（2026-08-14）为 edge-studio-dev `4107a18af58b2c296b3a2ccef8c35cdd0a8215ef` 与 reviewed root target `7ae6ebdec80fe51cebbbe81371b215aeeae06e65`。macOS App 作为员工 collaboration control plane接通 template→plan/effect preview→显式 digest-fenced apply，展示 active policy version、participant generation drift与 redacted delivery/thread health，并只对 Host 标记 eligible 的 exact event提供 retry。App capability map仍排除 owner/participant send、private delivery status与 consume，因此不能冒充 Agent或读取正文/transport private state。generation drift的 P0-D repair语义是重做并应用 policy plan；通用 Scenario repair、force-stop与resource break不提前进入本切片，仍由 P1-B处理。相比已被 supersede 的初始 candidate，当前 Swift model对所有 collaboration collection执行整页原子解析，异常行不会静默消失。Claude superseding review `20260814-141534-vkecaj` 为 P0=0/P1=0/P2=0/`can_commit_push`；signed App已在真实 Host上完成 template preview/apply、active policy current投影与正常 close，P0-D至此闭合。该 control-plane witness不替代 P0-E 的 Agent-native TUI conversation。

P0-E implementation candidate（2026-08-14）把 acceptance orchestrator限定为 operator-input + observer：一个 fresh Host同时创建两个使用相同 logical `analyst/reviewer` 的 Scenario，runner只向 each exact Harness-owned analyst TUI输入员工式任务，不能调用 owner `message.send`。实际 request/reply必须由两个 Agent各自在自身 Harness-owned descendant process chain中执行 PingAgent命令；Host-issued scoped identity、policy、route、journal、generation/fence和exact-session delivery/consumption仍是唯一 authority。验收同时 join public consumed thread、Agent命令产生的 exact Scenario sender结果与 hash-only/redacted private envelope，证明两组相同身份和相同相对路径在重叠运行时没有 cross-delivery。candidate失败只做普通 safe close，不自动 recover/force-stop，不改变 registry或 gate语义，也不在真实 witness及 peer review前生成 formal receipt。

来源：`user_decision`（2026-08-14）。真实 P0-E 运行证明 Agent 的 terminal tool executor 是 exact Harness-owned Agent root 的内核 PPID 后代，但可建立独立 process group；sender 认证不得把“owned descendant”误写成“全链同 PGID”。participant self send/reply 仍同时要求 0600 Host-issued scoped context、exact project/Scenario/participant generation/state revision、同 UID Unix peer PID、live root process fingerprint，以及 peer PID 和 exact iTerm session 当前 job PID 均能沿无环 bounded PPID chain 回到该 root；任一证据缺失即 fail closed。只有 sender self IPC 使用这条 ancestry proof。status、typed delivery/consumption、foreground observation 与 close 继续要求 exact window/session/owner marker，并保留全链同 PGID foreground fence，不能借此裁决扩大 owner/App authority或接受同 PGID非后代。

来源：`evidence`（2026-08-14，P0-E fresh parallel diagnostics）。两次真实运行分别出现 Scenario A、Scenario B 的 Claude self-reply 首次 `identity.sender-rejected`，而另一 Scenario 同命令成功；外部只读采样证明四个 participant client 的 PID→PPID 链均准确回到各自 Host 绑定的 Agent root。root driver code anchor `df28588b47389d8e1597fedfc45fd26d678193e5` 不移除或降级任何 identity/authority 条件，只在消息尚未 accepted 时对 `websockets.ConnectionClosedError` 做三次窄重连；topology、session、owner marker、root/peer/current-job ancestry 或其他异常第一次即 fail closed。该窄重连经review后仍复现同类拒绝，证明它不是充分根因修复；superseding diagnostic anchor `12c6fe7c9a6dd23f9784bddd22636bb3fa8643ff` 只在 owner-private participant state 持久化枚举化 `binding/root-process/peer-process/exact-session` stage和bounded reason code，公开错误、authority和delivery contract不变，且不记录PID、路径、消息或token。取得fresh failure-stage或passing witness前仍是 implementation candidate。

来源：`evidence`（2026-08-14，result SHA-256 `5cfd1e6834f7a91fe53172d3e7a69e407fa06dd0432d5b4a7b8eac9c36b6caee`）。fixed root `547a55b8c540d5e77177fd807e9c801f6db7469a`、product `08353667da95eceb56a0590a71b8ce93df8c7e5b`、PingAgent `c402acd968a7b95288d5eeed033fbd2ac487e5cb` 的 fresh P0-E candidate witness已通过：两个并行 Scenario、四个真实 interactive participant和四个distinct runtime/presentation binding同时运行；runner owner send为0，两个Agent-native self-send和两个self-reply正好形成四条linked delivery并全部consumed，cross/degraded/identity ambiguity/WIP loss/canonical mutation/high-risk action/private leak均为0；相同相对evidence路径保持隔离，两个Scenario均normal safe close，force/recover为0，vendor session identity不需要。该result仍显式`formal_gate_receipt=false`；peer review与formal closeout前不得写成P0-E或Harness v3完成。

该 witness 的独立 review `20260814-222000-rvwit1` 已按result SHA、三仓fixed vector及20项claim给出P0=0/P1=0/P2=0并允许formal closeout。正式化不把candidate文件改写成receipt，也不新增P0-E平行gate：collaboration verifier只读校验owner-private regular file、exact digest/vector、closed-world schema及全部硬不变量，将bounded machine facts加入既有participant identity、ready、delivery、cross-delivery与policy-routing gate的fingerprint和immutable receipt。candidate继续声明`formal_gate_receipt=false`，正式性只来自独立verifier写出的receipt/current view；verifier不重开窗口、不再次调用Agent或vendor API，也不改变registry语义。

formalizer fixed target `4a0f2540aeb575c2efc665500995aa765b2e77c6` 经implementation review `20260814-223500-rvfvr1` P0=0/P1=0/P2=0后生成上述五张required-gate receipt；实际evidence/current digest join、0600、三仓checkout与machine-witness source SHA均独立复核通过，terminal review `20260814-224000-rvfrc1` 明确允许标记P0-E formal completed。该完成只关闭Agent-native parallel collaboration能力，不外推migration、efficiency、P1 operations parity、remaining DoD或Harness v3整体完成。

随后刷新现有`ACC-MIGRATION-001`与`ACC-EFFICIENCY-001`：前者限定为internal/dogfood App原子升级、previous version与owner-private project/service registration恢复，不声称canonical workspace/cross-machine migration；后者沿用用户认可的价值指标，证明Harness自动window/workspace/message orchestration、零人工上下文搬运、typed delivery consumed与parallel Scenario no-cross，不要求已否决的paired manual baseline，也不声称percentage speedup或模型质量提升。两张receipt通过后主线回到P1 lifecycle/operations parity及最终derived DoD current views。

P1-A fixed implementation（2026-08-14）为 edge-studio-dev `a2663c3709fe3579b23ca5d5003d9d86b37b0cb3`。产品 Host、durable store、coordinator、typed client/CLI 与 macOS App 已实现本节冻结的 participant replace/detach：replace 的新 launch spec 在旧 binding 变更前完成预验证，cleanup evidence先于 generation CAS持久化，CAS前失败保留旧 generation/binding，CAS后失败保留新 generation degraded证据；detach desired-first并停止新投递，cleanup失败保留record/history/binding evidence为`cleanup_pending`，只有独立destroy才删除current record。auth context随generation精确轮转，旧policy引用自然进入generation drift；实现复用通用driver operations，不引入供应商session/lifecycle依赖。Claude fixed-SHA reviews `20260814-225811-2wil71` / `20260814-230500-rvp1a1` 为P0=0/P1=0/P2=0、`can_commit_push`。该事实记录不改变既有normative semantics，也不把product slice外推为formal gate或Harness完成；其后按执行序列进入P1-B。

P1-B fixed implementation（2026-08-14）为 edge-studio-dev `5ba4243ab7e315db0f51d66b293e9e41055e7fbe`。macOS App 现能从typed current projection识别Participant degraded/cleanup_pending和exact stale resource lease，并提交Scenario repair、exact Participant force-stop或stale-lease break intent；App本地alert不能签发授权，四条含destroy的高风险operation仍由Host descriptor声明`confirmation.destructive-once`，并由Host独立执行fresh target/fence、permission、effect preview与trusted native single-use confirmation。mutation后App重新读取Scenario、participant、resource、policy与delivery，避免向员工展示旧generation或lease。Claude reviews `20260814-231138-11psea` / `20260814-231800-rvp1b1` 为P0=0/P1=0/P2=0、`can_commit_push`。该实现不扩大App authority、不增加vendor分支，也不替代P2 signed-App witness/formal current-view refresh；当前执行序列进入P1-C。

P1-C fixed implementation（2026-08-14）为 EdgeStudio root `e3be3fb8290cfac18e3299f7f943dc3f59c0752e` 与 edge-studio-dev `ef19fe65d9da8e8d9f258f696a66aeb2a315fac1`。`scenario.preflight`是Host聚合的只读、advisory诊断，不成为Start/Open等lifecycle的隐藏准入闸门；它将project、Scenario、Workspace、Participant与presentation permission投影为bounded typed checks和repair actions。Automation/iTerm等平台事实留在root driver，probe不触发系统prompt、不打开窗口，只向Host返回provider-neutral observation；attached TUI需要presentation permission，headless/detached不需要。structured error继续显式区分retryable、mutation state和repair action；App action不能扩大权限，高风险repair仍由Host执行fresh fence/permission/effect preview与single-use confirmation。Claude reviews `20260814-234623-i6xe1t` / `20260814-235500-rvp1c1` 为P0=0/P1=0/P2=0、`can_commit_push`。该产品切片不引入vendor API/session identity依赖，不替代P2 formal current-view refresh；当前执行序列进入P1-D。

P1-D fixed implementation（2026-08-15）为 EdgeStudio root `560828d1913b2f79051032068bde3f67dd0f92d3` 与 edge-studio-dev `3f613ecaa60761897da4fa3f28c5f81bfae4035d`。Scenario topology/focus由Host的provider-neutral operation聚合，平台driver只在exact owned presentation和Harness-owned foreground process chain复核后读取、聚焦和按display topology恢复geometry；单窗失败不阻断siblings，headless/inactive/driver unavailable保持独立typed状态。长操作progress使用同一operation的单调sequence，cancel由独立连接携带Host generation、owner capability proof和exact operation ID；accepted只表示请求已通过鉴权并交给当前active operation，不承诺rollback或撤销当前已开始动作。partial close先保存每个Participant evidence，再以degraded和`operation.cancelled`要求fresh refresh；观察client断线不能取消durable operation。App只显示redacted topology/progress并提交exact cancel intent，不持有PID、path、binding或vendor identity。Claude reviews `20260815-003555-9l8y3j` / `20260815-010500-rvp1d1` 为P0=0/P1=0/P2=0、`can_commit_push`。P1-D不刷新formal gate，当前执行序列进入P1-E。

P1-E fixed implementation（2026-08-15）为 edge-studio-dev `92813fd0d82e8e5757ccd0816159cbe00e38278a`。Host保留frozen participant record，只在既有Scenario-scoped participant list结果中增加并列、bounded、非敏感configuration projection；每条以participant identity+generation绑定runtime profile ref和immutable model binding，CLI与App消费同一truth。App对两张集合执行exact、atomic join，拒绝malformed、duplicate、missing或generation mismatch，避免旧generation/model配置误导员工；UI显示generation/profile/model/provider/inference，null model明确表示profile default。核心没有Codex、Claude、Hermes分支，private launch/process/presentation/credential material均不进入projection。product Harness 106、focused 65、root既有Codex/Claude profile/schema/driver conformance 101以及Xcode 14项（1 expected live-host skip）均通过；Claude review `20260815-005706-pnng06` / `20260815-011500-rvp1e1` 为P0=0/P1=0/P2=0。来源：`user_decision`（2026-08-15），Hermes真实runtime conformance延后，不以空壳验证或本机安装状态替代；当前直接进入P2-B final closeout，P2-A adoption/distribution保留为其后的独立采用流。

P2-B technical closeout（2026-08-15）以 root aggregate implementation `825119cd46a595b638688715f2477dda5e809479` 和 producer/evaluator compatibility correction `a22cd9b8d4807bff031ab654c36c7443e35a60c9` 为固定实现，reviews `20260815-014521-rw0mzo` / `20260815-015000-rvp2b1` 与 `20260815-023120-1bb5qk` / `20260815-023500-rvp2bc` 均为P0=0/P1=0/P2=0。最终formal rebuild在root `a22cd9b8d4807bff031ab654c36c7443e35a60c9`、product `92813fd0d82e8e5757ccd0816159cbe00e38278a`、PingAgent `c402acd968a7b95288d5eeed033fbd2ac487e5cb` 上使29个ACC direct dependency current/evidence全部`passed + source_material_fresh`；当前product/PingAgent上的双Scenario四Agent witness为2 self-send + 2 self-reply、4 consumed、zero cross/degraded/hard-invariant failure并normal close。registry驱动的13个non-spike required DoD均重算为passed derived current view，`DOD-SPIKES-001`仍为11/11 source-fresh；derived DoD只写current view，不写immutable evidence。机器审计确认29个ACC、13个non-spike DoD、`0600`文件、`0700`目录以及对应DoD immutable evidence为0。该闭环证明当前技术架构/实现/gate一致，不扩大Host/App authority、不引入vendor API/session identity依赖；下一主线是P2-A员工采用与发布，不把distribution未完成倒灌为P2-B失败。

M3 fixed implementation（2026-08-11）把产品核心限定为 versioned generic participant driver dispatch 与 `add/start/status/stop` lifecycle：Host 只持有 launch spec、capability/registry digest、opaque runtime/presentation binding 和哈希证据；原始 PID、窗口/session identity 与本机路径只存在于 owner-private driver state。TUI 的一个顶层窗口与 headless 的零窗口规则已由真实 iTerm witness 验证；vendor session identity 仍是 optional capability，当前 `explicit_recreate` inert runtime 不调用 Codex/Claude API。该实现不提前收编 M4 typed delivery/PingAgent、完整 recover/replace/detach/destroy 或 Phase 4 high-risk action。

M4 fixed implementation（2026-08-11）已在三仓 `main` 接通 ordered first-match/default-deny policy、exact participant-generation routing、durable bounded delivery、delivery ACK、consumption ACK 与 exact-session transport，并以两个真实 TUI participant 完成 `attempt_started → ack_accepted → consumed` 的首次无人工上下文转移 dogfood；Claude fixed-SHA review `20260811-225753-sx9h79` 为 P0=0、P1=0、P2=0、`can_commit_push`。供应商命令与 trust/input-ready matcher 仍是 optional plugin data，vendor session identity 仍非 baseline。M1–M4 working vertical slice 至此完成；下一续接点按同一用户裁决回收此前延期的 fingerprint/hardening debt，然后进入 Phase 4，不把单次 dogfood 外推为完整 acceptance。

来源：`user_decision`（2026-08-13）。Phase 5 parallel-isolation candidate 之后，主线回到架构中已经要求的可见 App，而不是继续添加同类 candidate runner。首个 fixed implementation 在产品仓增加 owner-private project registry、`project.register/list`、Scenario-scoped `participant.list`、driver-provided template discovery 与 native SwiftUI thin client；App 直接实现同一 versioned IPC/HMAC binding，不 shell out，也不包含供应商生命周期分支。EdgeStudio project adapter 与既有真实 runners 同步改用 machine-local registered project identity。后续 fixed implementations 内嵌独立 Python Host payload 与 signed Swift helper，使用 current-user `SMAppService`/launchd 注册和监督 Host，并补齐候选校验、原子替换、typed health、自动恢复和可诊断失败候选；Host-private canonical root 只经窄化 child environment 传入 project/security adapter，App 仍只提交 typed intent。internal/dogfood 安装、升级、恢复和本机用户选择受保护项目的 Host crash-recovery 已通过产品、根仓、Swift、codesign 与真实系统 witness；fixed-SHA implementation reviews 均以 P0=0/P1=0 闭合，独立 witness review 接受结论与边界，精确锚点见实施账本。当前不扩大 App authority，不新增 bookmark broker，不重新设计 Host contract，也不把 Apple Development 内部签名冒充 Developer ID/notarized 员工发布。现在冻结 App/Host fingerprint material，按当前 registry/freshness 一次性重建 formal evidence，再进入 migration、efficiency 与剩余 DoD closeout，避免继续生成会立即失效的中间 receipt。

Phase 4 按已经批准的架构顺序拆为四个工作切片：P4-A safe close + minimal diagnostic、P4-B resource/process supervision、P4-C high-risk confirmation + repair/destroy、P4-D crash/restart acceptance。P4-A fixed implementation（2026-08-12）只实现 safe close 与结构化诊断：复用冻结 D transition/fence/journal，不授权 force-stop；idle/busy 且 exact owned target 已关闭时才提交成功，timeout/unknown 必须 fail closed、保留 binding 并进入 repair。供应商 idle/drain 识别属于 optional runtime profile data，Host contract 不依赖 Codex/Claude API 或 vendor session identity。edge-studio-dev `301cc051ea3c19bdf673c3790496a46f4f633f84` 与 root `5fe3d7e81628ea33273ec23159106b6cb2f56ded` 已由 superseding review `20260812-112855-p4tsh0` 以 P0=0、P1=0、`can_commit_push` 闭合并进入两仓 `main`；下一切片是 P4-B。P4-A implementation closeout 不能外推为 Phase 4 或 `ACC-CLOSE-001` acceptance 已完成。

上述 P4-A 的屏幕 idle/busy gate 已被 2026-08-12 用户裁决 supersede：真实员工会在交互式窗口内主动输入、监控和查看，供应商输入框内容也会随版本与用户历史变化。当前交互式 close 以显式请求和 exact ownership binding 为充分前提，不解析屏幕文本；driver 关闭 exact owned window，必要时只对同一已验证 owned process group 做普通 SIGTERM，并独立确认窗口与进程均消失。屏幕 matcher 仍可用于 startup ready/diagnostic，不再决定能否关闭。

同一裁决还要求把 Harness 运行能力与参与者产出的工作结论分层：limited dogfood 的运行成功由 exact-owned 窗口启动、模型正常回应、typed delivery/consumption、隔离产物写入、WIP/Git 不变量和精确关闭共同证明；参与者 review 的 P0/P1/verdict 是该轮工作的结构化结果，必须原样保留，但不能反向把已经成功的运行机制改写成失败。若 review 不建议继续，其影响是后续工作决策或产物准入，不是窗口、投递或生命周期失败。对已经完成 owner marker/session/process-chain 校验的同一 exact iTerm binding，单次 RPC 瞬态失败允许一次 bounded close retry；动态 `jobPid` 仍须按 §6 证明为绑定 root 的同 PGID live descendant，不得改用窗口差集、标题、位置或供应商文本猜目标。

Phase 5 首个 fixed-vector candidate（2026-08-12）已按上述运行标准通过：两个真实 TUI participant 均完成启动、正常回应、双向 typed consumption、隔离产物写入与 exact requested close；两条 descendant process chain 均消失，hard invariants 为 0，未使用 auto force-stop/repair。该 machine-local 结果明确不是 formal gate receipt，也不承载效率、迁移或产品完成声明。它当时提出的 Gate 0 配对人工基线续接点，已被下述同日用户裁决 supersede；除非暴露真实产品/集成缺陷，不再以增加 candidate runner 约束代替 Phase 5 进展。fixed SHA、result digest 与 reviews 见 [IMPLEMENTATION_PROGRESS.md](IMPLEMENTATION_PROGRESS.md)。

Phase 5 第二个同项目 fixed-vector candidate（2026-08-12）进一步验证了既有通用 contract，而没有新增 gate 语义：Scenario 先以两名 participant 进入 `running`，再动态加入并启动第三名 participant；三段真实 typed delivery/consumption 与 hash-linked 隔离产物均完成。为兑现 exact ownership 的关闭证据，integration driver 在关闭前保存已验证的 foreground descendant PID，并在 close 后分别确认 launch-root 与该 foreground PID 消失；这不把动态 `jobPid`、供应商 session 或产品名升级为 Participant identity。三名 participant 均 clean stopped，hard invariants 为 0，未使用 force-stop/repair。该结果仍为 machine-local、non-formal candidate，工作内容语义由参与者 review 而非 runner 自行裁决。

来源：`user_decision`（2026-08-12）。在第二个 candidate 后尝试的“三个手工窗口 + 同题复制粘贴 analysis/review/synthesis”不能直接验证本架构的核心价值，只会重复显而易见的人工编排成本；该人工基线已终止且不形成 receipt。Phase 5 最近的架构能力缺口重新固定为本文件开头的“可并行且不会串线”：同一 Host 上多个 Scenario 必须能重叠运行，workspace、Participant identity、delivery 和 resource ownership 必须按 Scenario 隔离，一个 Scenario 的 exact close 不得改变另一个 Scenario 的 binding 或工作能力。这一优先级修正不修改 gate registry，也不取消后续 `ACC-EFFICIENCY-001`；效率仍在 formal closeout 中以与真实产品价值相关的用户认可度量完成。

Phase 5 第三个 fixed-vector candidate（2026-08-12）已真实覆盖上述缺口：一个 Host 同时运行两个 Scenario，每边使用同样的两个 logical participant IDs，却取得隔离 workspace、互异 runtime/presentation binding 与 Scenario-scoped lease；A、B 首次 delivery 以及 A close 后 B continuation delivery 均一次 consumed。A→B receiver intent 经产品 default-deny，并在公共 typed IPC 返回 `auth.capability-denied`，没有形成跨 Scenario delivery。A clean close 后 B 保持 ready/binding 不变并继续交付，随后 B 独立 clean close；四 participant stopped、live binding 清空、WIP/canonical source 不变、hard invariants 为 0。首次运行因 runner 误把内部 `policy.denied` 当公共错误码而 fail closed 并 clean up，superseding fix 只改为断言既有公共映射，不放宽为任意失败。结果仍是 machine-local、non-formal candidate，不完成 `ACC-CROSS-DELIVERY-001`。下一主线是按现有 registry 为已实现 Phase 1–3 能力生成正式 evidence，并完成 migration、efficiency 与 DoD；全部 required gates current/fresh 后才能按 §15 声明 Harness 完成。fixed SHA、result digest 与 reviews 见 [IMPLEMENTATION_PROGRESS.md](IMPLEMENTATION_PROGRESS.md)。

P4-B superseding fixed implementation（2026-08-12，edge-studio-dev `22bf945dd2ea53d828f1360a023914055495adb7`、root `f658ac56d05b7fd6bb9abc0b14226c4a38537339`）把 §10 的 supervision 最小语义接入 Host：ready 前必须取得 exact process-start、boot、heartbeat、fencing 与 resource evidence；后台只 re-observe Harness-owned exact binding。Host restart 或无法证明继续持有时把 lease 降为 stale；只有相同 holder/process/boot/fence 才能恢复 active，只有已证明的 stop/safe close 才能进入 released。machine-shared resource conflict 扫描 Host 的全部 Scenario ledger，任何其他 holder 的 active/stale lease 都禁止同一 resource identity 新建 active lease；若冲突在新 process 已启动后才发现，Host 必须先 exact stop，stop 未证明成功时持久化 immutable ACK/binding 为 degraded cleanup_pending，不能形成 orphan 或丢失 ownership evidence。Host shutdown 开始后不再调度后续 observation。公共面只提供 redacted `resource list`/diagnostic，不暴露 PID、raw token 或私有路径。最终 superseding review `20260812-125534-efpcon` 给出 P0=0、P1=0、`can_commit_push`，两仓已进入 `main`；该实现不能外推为 `ACC-RESOURCE-001` 或 Phase 4 acceptance 已完成。

P4-C superseding fixed implementation（2026-08-12，edge-studio-dev `35670455b6e3d1cb024e2658576e4d1e106ba9e1`、root `40202e94d1740551d8396c8b7a0189356aae466b`）实现冻结 J 的真实高风险链与 §7/§10 生命周期动作：`participant.force-stop`、`resource.break`、`scenario.repair`、read-only destroy preview 和 `scenario.destroy`。产品核心只依赖 versioned permission observer/trusted presenter plugin，不按 Codex/Claude 分支；每个 mutation 都要求 current exact-subject permission、typed redacted effect preview、single-use authorization 与 pre-mutation durable consumption，normal stop 不再隐式升级为 force。Host 还拒绝 future presenter decision，确保 authorization issued time 不晚于 consumption。resource break 需要 stale lease 加 owned process fresh absent；repair/destroy 重新验证 exact Workspace receipt、binding 与 WIP digest，禁止覆盖 Scenario WIP 或修改 canonical source，并保留 current-record 移除前的 durable audit history。macOS native presenter、owner-private process probe 与 EdgeStudio project probe 属于 integration plugin；vendor session identity 继续只是 optional capability。旧 targets `4df5b656...` / `b00bab7d...` 及其 review 已作废；superseding review `20260812-141258-48q3ls` 对新双仓 SHA 给出 P0=0、P1=0、`can_commit_push`。本切片只完成 implementation/conformance，不修改 composed acceptance gates 或 machine receipts；crash/restart fault injection、真实 App packaging 与 `ACC-SECURITY-001`/`ACC-RESOURCE-001`/`ACC-DESTROY-001` closeout 属于 P4-D。

P4-D 当前固定的 crash-reconciliation 原则是：外部动作前先持久化 immutable operation fence；外部动作成功后、清除 live binding 或提交最终 CAS 前，先持久化可精确重放/核验的 outcome evidence。Host 重启顺序必须先让 Workspace/driver owner 按同一 operation ID 恢复或返回幂等结果，再由 Store join 并完成 Scenario/Participant CAS；只有没有 durable outcome 的未知 callback 才进入 `repair_required`。该规则覆盖 provision、close、force-stop、resource break、repair 与 destroy，并禁止用“进程/目录看起来不存在”绕过 request、generation、receipt、WIP 或 ownership fence。Phase 4 五个既有 verifier 保持 registry 名称与 gate 语义不变，各自运行 focused evidence；不得把它们折叠成一个通用 receipt 生成框架。`ACC-ROLLBACK-001` 仍由 Host 外的 runner 在独立 iTerm 窗口中实际执行 legacy register + delivery round trip，Host 不可自证回滚。

P4-D fixed implementation（2026-08-12，edge-studio-dev `ed9fcbf5f67ba66ad227d4f23a573ec2d71f595e`、root `1b1b341ebe2b52cd3e896582749ffd237739eddd`）已按上述原则覆盖六个 external-action crash window，并由 review `20260812-152514-44zpgv` 以 P0=0、P1=0、P2=0、`can_commit_push` 闭合后进入双仓 `main`。同一 clean/pushed SHA 上的 `ACC-WIP-001`、`ACC-CLOSE-001`、`ACC-RESOURCE-001`、`ACC-DESTROY-001` 与 `ACC-ROLLBACK-001` current→immutable evidence 均为 `passed`，hash/fingerprint、`0600`、checkout/remote equality 与 privacy 已独立复核；rollback witness 还证明 canonical WIP 前后相同、Host 未启动、独立 watcher/window 已清理。tracked closeout `318a7a748acfae95ac5552a453328c0a3e2095d7` 再由 terminal review `20260812-154836-vcon65` 以 P0=0、P1=0、`can_commit_push` 闭合，P4-D/Phase 4 至此完成。下一主线按 §14 进入 Phase 5 limited dogfood，不能外推为 App packaging、migration、efficiency 或 Harness 产品整体完成。

## 15. 外部项目接入标准

另一家公司接入时只应提供：

- project descriptor；
- workspace/environment adapter；
- organization collaboration policy pack；
- 必要的 runtime/presentation/platform plugin；
- project acceptance gates 与 rollout plan。

如果接入需要修改 Host 核心、加入公司名/仓库名/语言环境条件分支，视为 product contract 缺口，必须先修通用 abstraction，不能把特例合入核心。

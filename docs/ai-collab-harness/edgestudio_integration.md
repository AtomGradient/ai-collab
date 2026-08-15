# AI Collaboration Scenario Harness — EdgeStudio 集成设计

> 状态：**active integration design；Phase 0、M1–M4、延期的 fingerprint/hardening debt、Phase 4、Phase 5 limited dogfood、P0 Agent collaboration、P1 lifecycle/operations parity与P2-B final formal evidence / required DoD technical closeout均已闭合；当前续接P2-A adoption/distribution，Hermes真实runtime conformance仍按裁决保留**
>
> 版本：v3.3
>
> 更新日期：2026-08-15
>
> 产品 contract：[product_architecture.md](product_architecture.md)
>
> Composed gate registry：[edgestudio_gates.yaml](edgestudio_gates.yaml)

## 0. 文档定位

本文只定义 EdgeStudio 如何消费通用 AI Collaboration Scenario Harness。Scenario、Participant、Runtime Driver、Presentation Driver、Collaboration Policy、delivery 和 gate engine 的 normative 语义全部来自产品架构，本文不得重新定义或收窄它们。

EdgeStudio 是首个真实消费者和 reference integration，不是产品核心的特殊分支。其他公司接入时应替换本文描述的 project adapter、policy pack 和 acceptance gates，而不是 fork Host。

## 1. EdgeStudio 集成范围

### 1.1 必须交付

- committed project descriptor 与真实 repo manifest；
- isolated multi-repo workspace adapter；
- per-scenario Python environment adapter；
- EdgeStudio Git origin/ref/pre-push guard；
- PingAgent legacy migration 与可靠 delivery 切换；
- 项目 `AGENTS.md` policy 的显式映射，不擅自扩展 quorum；
- EdgeStudio smoke/acceptance、效率基线、rollback 与 dogfood；
- 产品、macOS/iTerm、runtime driver 与项目 gates 的 composed registry。

### 1.2 不属于本文

- 通用 Host/CLI/App 状态机的产品定义；
- 新人账号、MFA、新机器 bootstrap；
- 模型训练、runtime routing 或产品效果声明；
- 自动 commit/push/rebase/merge/stash；
- 其他公司、其他项目的 repo/environment 规则；
- 新建独立 Harness 产品仓库本身（需要用户另行明确授权）。

## 2. Project contract

EdgeStudio canonical root 提供 committed、无机器绝对路径的 descriptor：

```yaml
schema_version: 1
project_key: edgestudio
product_contract_version: "3.2"
workspace_adapter: ai-collab-edgestudio-workspace-v1
repo_manifest: repo_manifest.yaml
environment_adapter: ai-collab-edgestudio-environment-v1
gate_registry: docs/ai-collab-harness/edgestudio_gates.yaml
participant_driver_contract: 2
collaboration_policy_schema: 1
```

root [project_descriptor.yaml](../../project_descriptor.yaml) 是上述 mapping 的机器真相源。两个 adapter 值是 versioned project-owned command contract references；Phase 0 validator 只校验 descriptor 结构、相对引用和 cross-contract version，不把尚待 Phase 1 实现的 executable/capability 冒充为已可用。descriptor 不保存 freeze 进度或 machine-local 状态。

project register 必须验证 descriptor schema、adapter capability、manifest 和 product contract compatibility，生成本机 `project_instance_id`，保存 canonical root 的规范路径/fingerprint，但不自动创建 scenario、participant 或 credential。

来源：`user_decision`（2026-08-13）。EdgeStudio App 的目录选择器只产生 `project.register` intent；root adapter 只接受经 Host canonicalize 后与当前 EdgeStudio root 精确相等的目录，并重新验证 descriptor、manifest 与 adapter capability。Host 的 owner-private registry 保存 path/fingerprint 与幂等 request history，公共 `project.register/list` record 只包含 opaque instance ID、project/binding/contract/capability digests 和 registration revision。App 不读取 `project_descriptor.yaml`、不扫描 nested repositories，也不自行生成项目 identity。Scenario 只能绑定已注册且 digest 匹配的 project instance。

timeout、retry、resource budget 等属于 machine/scenario policy，不进入 project contract 的全局常数。participant 模板只能作为用户显式选择的 convenience，不能隐式播种固定产品或基数。

## 3. Repo manifest 与 inventory

Harness contract manifest 位于 root [repo_manifest.yaml](../../repo_manifest.yaml)，由 project descriptor 直接引用，因此干净的 EdgeStudio checkout 不依赖 ignored nested repo 就能验证 project contract。`onboarding/manifests/repos.yaml` 继续作为 onboarding 自己的 operational inventory；两者用途不同，Harness 不与 onboarding checkout 耦合，也不把后者的机器绝对 `workspace_root` 或 `../PingAgent` placement 继承进 product contract。

root manifest 使用 `required | optional | unmanaged` classification、`project_root | project_child | bundle_sibling` typed placement、逐仓 `base_branch`、canonical SSH remote、显式 provision order/edge、acceptance layer 与 smoke policy。当前 `base` acceptance 集合保守映射为 `required`，其他已 managed 集合映射为 `optional`；2026-08-06 用户明确排除的两个仓库以最小 `unmanaged` row 保存身份/路径冲突边界，但不携带 remote、branch 或 provision 字段。schema 允许每仓不同 base branch；当前值恰好都是 `main` 不能被实现解释成全局假设。

Manifest 至少表达：

- `required | optional | unmanaged`；
- project child 或 bundle sibling placement；
- canonical source path、remote、per-repo base branch；
- dependency/provision order；
- acceptance/smoke capability。

Inventory 分类：

- `required | optional`：验证 source、base branch 和依赖；
- `unmanaged`：只做身份/路径冲突检测，不 provision、clone、检查分支或加入依赖；
- `unclassified`：报告但不自动收编；只在 closed-world 声明或路径重叠时 blocking；
- 任意重复/重叠 physical path 都 fail closed。

已裁决项目差集（`user_decision`，2026-08-06）：

| Repo | 分类 | 行为 |
|---|---|---|
| `AtomGradientMainSite` | unmanaged / ignored by default | 不检查、不 provision；具体任务显式 opt in 才纳入 |
| `edge-tool-calling-demo` | unmanaged / ignored by default | 客户集成仓；默认不检查，具体任务显式 opt in |

目录扫描不构成授权，不能自动写回 managed manifest。

Phase 0 conformance validator 只检查 committed schema、identity、typed path、remote/ref、order/DAG 与分类不变量，输出 canonical manifest digest；它不扫描本机目录、不读取 Git 状态，也不把未声明目录转成 managed repo。actual inventory reconciliation 和 exact-SHA plan 属于 Phase 1 adapter/preflight 行为。

## 4. Scenario workspace

Reference layout：

```text
<scenario-root>/
└── edgestudio/
    └── <slug>-<short-id>/
        ├── EdgeStudio/
        │   ├── .ai-mailbox/
        │   ├── edge-kit/
        │   ├── edge-studio-dev/
        │   └── ...
        ├── PingAgent/
        └── .venv/
```

真实根目录可配置；manifest 类型化表达 child/sibling placement，repo path 禁止 `..`。不同 scenario 必须独立拥有 Git dir/HEAD/index/refs/working tree、writable environment、participant mailbox/session/process binding 和 operation generation。

同一 scenario 的 participants 有意共享该 scenario workspace/environment；participant identity、mailbox、runtime session、process ownership 和 window binding 仍分别隔离。写入协调遵循 collaboration policy 与项目规则，不能靠混用 identity。

## 5. Workspace provisioning

### 5.1 Immutable plan

Provision 前生成不可变 plan：

- operation ID/generation；
- descriptor/manifest/adapter digest；
- 每仓 source、base branch、exact object ID、object format；
- target、scenario ref、真实 remote URL；
- environment plan、空间估计与 plan generation。

Plan 阶段只读，不 fetch/pull/checkout 或修改 canonical WIP。

### 5.2 每仓算法

1. 校验 source/target physical path、shallow/promisor/alternates；
2. 使用 no-local、no-checkout clone 避免 hardlink/source-copy race；
3. 验证 pinned base object 与 connectivity；
4. 创建 scenario-local branch/ref；
5. 删除临时 canonical-source remote/tracking refs；
6. 添加 manifest 声明的真实 origin，不伪造未 fetch 的 `origin/*`；
7. 安装 scenario-owned pre-push dispatcher 和项目 provider hook；
8. 记录 HEAD、clean、origin、object format 和 guard/provider digest。

所有步骤写 journal；crash 后只允许 resume/repair/rollback，不把半成品标 ready。final rename 与 registry CAS 后才能从 `provisioning` 进入 `closed`。

## 6. Git guard 与 review snapshot

Pre-push dispatcher 必须：

- 读取完整 stdin 后再做副作用；
- 校验 remote name/URL、source/destination ref、force/delete/tag/main、scenario ownership；
- 完整重放 stdin 给项目 provider hook；
- 任一 guard/provider 缺失或失败时整批拒绝；
- 不用 `core.hooksPath` 覆盖项目既有 hook。

v1 不自动 commit、push、rebase 或 merge。Review request 锚定已提交的 exact multi-repo SHA vector；验证使用只读 exact-SHA snapshot，不读取 moving dirty worktree。SHA 改变后原 review/receipt 失效。

远端 branch protection、ruleset 或同等 server-side guard 是否可用必须由 `DOD-GIT-001` 的 provider probe 证明；不可用时向用户展示本地 guard 与 server guarantee 的差异，不把本地 hook 宣称为远端保护。

## 7. Scenario environment

共享 editable venv 已造成源码路径漂移，因此每个 scenario 必须拥有独立 `.venv` 或 adapter 定义的等价 writable environment：

- 锁定 interpreter、dependency lock、editable source 和 digest；
- 只复用不可变 download/wheel/build cache；
- import/test 前验证 editable path 均在当前 scenario；
- status 展示大小、最后同步和 drift；
- 遵守用户确认的磁盘/并发预算。

Phase -1 测量真实创建时间、增量空间和 cache 行为；超预算时减少默认内容或延迟创建，不能退回共享可变 site-packages。

## 8. macOS、iTerm 与 runtime drivers

首发 platform/presentation 实现为当前用户级 macOS Host + iTerm2：

- Host 使用用户级 `SMAppService`/launchd，不使用 root；
- 优先验证 iTerm2 官方 Python API；Apple Events 仅作有明确退路的兼容候选；
- Automation/TCC 使用官方接口实时探测，不依赖 Accessibility；
- 每个交互式 participant 一个独立 iTerm2 顶层窗口；headless participant 无窗口；
- geometry 按 machine/display topology 保存；位置前缀不是 identity。

首批真实 runtime driver 可以是 Codex/Claude，但 Host contract 不写死产品。未来 Hermes、Qwen/DeepSeek-backed runtime 或其他服务通过相同 driver conformance 接入。

`model_binding` 是启动前确定、participant generation 内只读的 launch metadata（`user_decision`，2026-08-06）。EdgeStudio 不提供运行中 rebind；更换模型需要新的 runtime generation，并保留 binding history。

M3 reference implementation anchors 为 edge-studio-dev `25917af2f5d2d775fe25a18b7453b75c30732803` 与 root driver `7fdbcca3f2805048f7522d8da3e3bcb40ceec1b5`。项目 plugin 注册 `runtime.generic-process` 与 `presentation.iterm2`：当前唯一 runtime profile 是不含供应商语义的 inert process；iTerm dependency 由 tracked lock 固定并在 owner-private generation root 内安装，官方 `Window.async_create` 返回的 exact handle、owner variable、单 tab/session topology、session `jobPid` 与 process fingerprint 联合形成 binding，`async_close(force=True)` 只关闭同一 exact owned window。公共 Host state 只保存 opaque ID/hash，driver-private 0600 state 保存原始身份；stop 清 live binding 但保留该 generation 的私有 ACK audit history。此实现不把 Codex/Claude session API、产品名分支或 exact resume 变成 baseline。

M4 在上述 baseline 外增加 optional runtime-profile registry 与 PingAgent exact-session typed transport。供应商命令、startup trust prompt 和 input-ready screen matcher 都是 plugin data；Host 与产品 delivery core 只消费 generic capability/binding/ACK。来源 `user_decision`（2026-08-11）：Harness 仅可为 M2 已验证、owner-controlled 的隔离工作区自动确认目录 trust；driver 在 exact owned iTerm session 内用 live process cwd 绑定工作区，完整 prompt 精确匹配后才确认，并等待完整主输入界面稳定后签发 ready。提示或绑定不一致时 fail closed。来源 `user_decision`（2026-08-12）：真实 dogfood 的 vendor runtime 按本机正常员工配置启动（Claude skip-permissions、Codex bypass approvals/sandbox），不再叠加 `dontAsk` 或 Bash 禁令；隔离 workspace、exact ownership 与 canonical-source/WIP 校验承担边界。脚本启动器切换到原生子进程的 runtime 必须在 input-ready 后重新观测 session `jobPid`，避免把短命 wrapper 误签为最终 process binding。

M4 typed path 固定为 policy first-match/default deny → exact participant-generation route → durable delivery attempt → exact-session transport evidence → delivery ACK → agent-visible consumption marker → consumption ACK。文件、mailbox sidecar、role 注册或供应商 session identity 都不能替代该链。首次真实 dogfood 使用两个新 TUI participant，自动完成两端 trust，将 Claude logical sender 精确路由到 Codex receiver，最终记录 `attempt_started → ack_accepted → consumed`，无人工上下文转移；该 witness 证明本纵向切片可运行，不外推 broadcast/quorum/escalation、跨 Scenario、hot reroute、Phase 4 resource/destroy/rollback 或全部 acceptance。

来源：`user_decision`（2026-08-15）。P2-A 真实员工操作确认 Codex/Claude 的 normal Close/Resume 不能继续使用 `explicit_recreate` 冒充现场恢复。EdgeStudio 的两个 profile 现在把 vendor lifecycle 定义为可选 adapter capability，而不是 Host 依赖：

- Codex adapter 用受支持的 `SessionStart` hook 获取实际 session identity、用 session-start `additionalContext` 注入 collaboration context，并用显式 `codex ... resume <id>` 恢复；
- Claude adapter在首次启动前生成 UUID并传入 `--session-id`，用受支持的 `SessionStart` hook核对实际 identity、用 `--append-system-prompt-file` 注入 collaboration context，并用显式 `--resume <id>` 恢复；
- raw identity和hook/settings/prompt只在 owner-private participant generation root，Host/runtime ready ACK只携带 SHA-256；不读取 vendor transcript/private store，不使用 cwd、role、`--last`或 picker猜测；
- normal Close保留该 private binding，Resume在同 Participant generation创建新iTerm presentation和Harness-owned process chain后恢复同一conversation。hook source/identity/version任何不匹配都 fail closed；App只有在用户明确确认 `Recreate + Handoff` 后才以 `explicit_recreate` replace为新 generation。

同一修正增加 Host-owned collaboration manifest。Host按 current Scenario/team/policy生成 identity、assignments、peers、允许route、reply kinds与revision/digest，ParticipantAuth放在独立0600目录，driver在首次用户输入前注入。Codex/Claude只负责消费该 provider-neutral context，live policy enforcement仍由Host完成；共享Workspace里的`AGENTS.md`/`CLAUDE.md`不承载动态participant identity。

2026-08-15员工实测进一步收紧反馈语义：同一Scenario中reviewer→analyst的`Bye bye!`已由durable delivery账本证明`attempt_started → ack_accepted → consumed`，但Claude TUI仍显示供应商原生“No agents reachable”。这不是Harness路由失败，而是模型把provider-native discovery误当Harness reachability。driver context因此明确禁止以供应商原生agent discovery/messaging替代Harness peer，并规定成功的`ai-ping` Host结果具有反馈权威性；员工仍只需自然语言说“发给reviewer/analyst”，无需点名PingAgent。

同日用户冻结默认存储布局：production App/Host继续把control-plane state保存在`~/Library/Application Support/AI Collab`，但所有新Scenario workspace写入`~/Documents/Scenarios`。已有Application Support workspace不迁移，由Host按binding双根解析；测试或开发通过custom state/workspace root保持完全隔离。Scenario列表按最近journal activity倒序；Destroy按钮只有fresh effect preview明确`eligible=true`时启用，未满足closed/stopped/released/aligned条件时保留blocker而不再提交必然失败的高风险mutation。

同日后续员工UX裁决增加列表右键`Force Delete Scenario…`，保留上述详情页保守Destroy路径。右键动作向Host提交单个`scenario.force-destroy`：App先以原生alert明确Scenario WIP会丢失，Host再独立生成effect preview并完成一次trusted single-use confirmation；Host只使用Store冻结的runtime/presentation binding调用driver exact force-stop，只释放该Scenario lease，只把Workspace adapter当前exact binding目录交给destroy。unknown live binding、owner proof缺失、revision/generation漂移、Workspace/WIP digest变化或canonical source边界不成立时立即fail closed。该operation也由CLI暴露以维持自动化语义对齐，但不能绕过同一Host confirmation；既有`scenario.destroy`及其formal gate不改变。

来源：`user_decision`（2026-08-14）。Agent-native send/reply 不再把上述完整链路绑定到调用 shell 生命周期：Host 完成 sender/policy/fence 校验并 `fsync` 持久化 initial `queued` envelope/request replay record 后，PingAgent participant client立即返回紧凑 `accepted`；Host background supervision独立推进 transport、delivery/consumption ACK、retry与restart recovery。正常 ACK 只写 Host state/read model/审计，不向 sender 注入“对方已收到”、不触发额外模型推理或 ACK loop。`delivery.status/list` 证明 eventual state；P0-E participant command artifact只证明 Agent invocation + Host durable acceptance，最终 consumed linked thread和zero-cross由Host public/private delivery evidence证明。

2026-08-15 的 kind-aware transport修正进一步规定：Agent-visible `request/question/review-request/pushback` 可携带原 delivery ID和reply入口；`response/review-response/notice/done` 默认只要求消费，不再提示执行`ai-ping`回执。消费 token仍由现有exact-session机制推进Host machine state，但不得诱发第三条业务delivery或在TUI生成“已收到”式token开销。

来源：`user_decision`（2026-08-14）。PingAgent 继续承担 Agent 侧入口与 exact-session transport，不被新的 App 或 Host 重复实现取代；authority 边界固定为：

- Host 独占 Scenario/Participant identity、policy decision、delivery journal、ACK/retry、generation fencing 与审计；
- PingAgent 接收 Host 已授权的 exact target delivery 并注入对应 TUI，同时把 participant 的 send/reply intent 连同 Harness-issued scoped launch context 交回 Host；
- legacy `ai-ping` 保留兼容与人工协作价值，但在 Harness Scenario 中必须优先解析 scoped context，不能绕过 Host 形成第二套未审计 route，也不能按 role、cwd 或最近 session 猜 sender/receiver；
- App 只通过 Host 配置和观察 collaboration，不直接操作 iTerm transport，也不以 participant 身份发送；
- acceptance 必须区分“PingAgent transport 成功注入”与“participant 以自身身份发起 conversation”。M4 外部 runner 代发只证明前者和 delivery substrate，不证明后者。

## 9. PingAgent legacy repair 与迁移

### 9.1 Immediate repair

当前生产入口必须在 Gate 0 与任何新增 dogfood 前独立修复：

- `session not found` 返回失败；
- `errAEEventNotPermitted (-1743)` 分类为 Automation 拒绝；
- `.dispatched` 只在确认注入成功后写入；
- 增加 session missing、TCC denied、正常注入与旧 sidecar observability 回归测试；
- bootstrap verifier 生成 immutable `LEGACY-DELIVERY-001` evidence。

该 change set 位于 PingAgent，必须固定 SHA、测试、push 和 peer review，不等待未来 Host。
当前 bootstrap verifier 入口为 `scripts/verify_ai_collab_legacy_delivery.py`；它只接受 clean、已 push、与调用参数 exact SHA 一致的 EdgeStudio/PingAgent `main`，并把 immutable evidence 与 current view 写入 receipt contract 规定的两个逻辑位置。

### 9.2 Legacy identity gap

- `.panes/$ROLE.json` 每个 role 只有一个注册位，同 role 后注册会覆盖先注册；
- legacy `session_uuid` 与 `iterm_session_id` 都来自 `ITERM_SESSION_ID`，没有 TUI session identity；
- target migration 必须为每个实际实例分配 `(scenario_id, participant_id)`，从 runtime 官方 lifecycle 新增 exact binding；
- legacy role、position prefix 或 sidecar 不能提升为 target identity/receipt。

### 9.3 Rollback

Legacy repair 通过且独立 `ACC-ROLLBACK-001` 证明后，手工独立 iTerm 窗口 + register 才可作为回滚路径。回滚步骤必须展示 WIP/session/投递边界；Host/App 不可用时由外部 acceptance runner 验证，不能由失效 Host 自证。

## 10. EdgeStudio collaboration policy

当前项目 `AGENTS.md` 只定义已注册 Codex/Claude mailbox、review、pushback 与 escalation。Harness 可以托管其他 participant 和显式点对点消息，但在用户另行裁决并修改 `AGENTS.md` 前：

- 不自动把新增 participant 纳入项目 review/quorum；
- 不自动 broadcast/fan-out；
- 不把 role 当 participant identity；
- 用户仍是产品方向、权限、高风险、gate 语义与无法收敛 pushback 的 coordinator。

文档或 runtime policy 不能静默覆盖 `AGENTS.md`。

来源：`user_decision`（2026-08-14）。EdgeStudio 首个产品化 collaboration flow 采用可配置 team/policy data，而不是把 Codex/Claude 双人拓扑写入 Host：

- App 和 owner/automation CLI 可以选择 project-provided team/policy template并生成 plan；plan 必须解析所有 declared participant 的 current generation，展示 route 与 retry effect，再显式 apply；
- participant/TUI 侧使用 PingAgent 风格的简洁 send/reply 命令，从 Harness-issued scoped context 固定 sender；项目 `AGENTS.md` 继续决定何时 review、pushback 或升级给用户；
- Scenario-scoped delivery read model 至少返回 redacted sender、receiver、message kind、state、degraded reason、event sequence 与 retry eligibility，供 CLI/App 观察；正文 retention/展示继续遵守 owner-private 与 redaction boundary；
- recover/replace 产生新 participant generation 后，不改写旧 policy snapshot 或 delivery；App/CLI 必须显示 policy generation drift，并要求重新 plan/apply 后才允许向新 generation 发送；
- App 是 collaboration control plane，不是 vendor chat client；如果未来需要 App 发人类消息，必须新增独立 human provenance contract，不复用 participant sender 字段。

P0-B fixed implementation 为 edge-studio-dev `fdbb94cf8bfde07278c60531dae051e8fade563d`、EdgeStudio root code anchor `7edeb7970083b13565e7591997b2212d9ff84aea` 与 reviewed target `3148198054ce0af4a9d13405259ad42b92ab378b`。EdgeStudio project adapter 从 owner-owned `ai_collab_team_policies.json` 提供双 Agent peer-review 与三 Agent research 示例；Host core 只接收通用、bounded、path-free 数据，生成当前 generation 的 route/retry preview 和 digest-fenced apply，不包含供应商名或固定 participant 基数。模板注册表已进入 embedded App Host payload，App 选择/展示/重做 plan 的员工 UI 已按 P0-D 实现。Claude review `20260814-134145-ni8mxf` 为 P0=0/P1=0/`can_commit_push`，P0-B 当前为 `completed_product_slice`；root template tamper 负测的 P2 follow-up已在 P0-E implementation candidate补齐，等待同一 fixed-SHA review，不提前生成 formal gate receipt。

P0-C fixed implementation edge-studio-dev `d75fb8e2031d4245237ede3640ca9280f0b7c2ed` 与 reviewed root target `938061a1f07d17afd1288a37ca894a5760f29b8f` 已增加 bounded/digest-fenced `delivery.list` 和 thread-root filter。collection 只投影 redacted Agent generation、message kind、policy/thread/state/event/retry health，不暴露正文、message ID、payload/ACK token、runtime/presentation binding 或 transport evidence；同 Host active attempt不可并发 retry，crash 后 unknown attempt 仍可恢复。CLI 已接通，App capability map已同步但员工 UI 仍属于 P0-D。Claude review `20260814-135513-h2igth` 给出 P0=0、P1=0、P2=1、`can_commit_push`；active attempt 开始/结束造成 pagination stale 是预期 fail-closed 行为，调用方 refresh 即可。P0-C 已终止式闭合。

P0-D superseding fixed implementation edge-studio-dev `4107a18af58b2c296b3a2ccef8c35cdd0a8215ef` 与 reviewed root target `7ae6ebdec80fe51cebbbe81371b215aeeae06e65` 已把 App 从 raw policy inspector扩展为员工 collaboration control plane：project team template可生成 exact generation/route/retry preview并显式 apply；active policy version和generation drift可见；Scenario delivery/thread以 redacted health projection显示，Host 判定 exact event eligible时才允许 retry。App 不接 owner/participant send、private delivery status或consume，不读取正文也不冒充 Agent；内容工作仍在各自 TUI。P0-D 所说 repair仅为 drift后的 re-plan/apply，通用 repair/high-risk action仍留P1。Swift collaboration collections现按整页原子解析，任一 malformed row都会拒绝整块投影而不是静默漏行。Claude superseding review `20260814-141534-vkecaj` 为 P0=0/P1=0/P2=0/`can_commit_push`；signed App已在真实 Host上完成 template preview/apply、exact generation/policy current投影与正常 close。fresh Agent TUI self roundtrip仍由 P0-E独立证明，不能用 App control-plane witness替代。

P0-E implementation candidate把旧 Phase 5 runner的 sender职责移除：一个 fresh Host同时运行两个相同 logical `analyst/reviewer` 拓扑的 Scenario，operator automation只向 exact Harness-owned analyst TUI输入员工任务；后续 request和reply必须由 Agent自身执行 PingAgent命令，并由 Host-issued context + peer PID + descendant process chain认证。runner只观察 Scenario-scoped public delivery thread、Agent命令结果和 hash-only/redacted private no-cross证据；同一相对 evidence路径位于不同 workspace，不读写 canonical source。真实 witness前先 fixed-SHA review，运行使用本机正常员工 runtime permissions和proxy，不引入 vendor API/session identity依赖，不自动 recover/force-stop，也不提前生成 formal receipt。P0-B遗留的 project template symlink、wrong-owner和bad-JSON负测在同一 candidate补齐。

来源：`user_decision`（2026-08-14）。P0-E 实机诊断确认 Codex terminal tool shell 的 PPID 回到 exact Codex root，但工具执行器会建立独立 PGID；因此 self-send sender proof使用 sender-specific bounded PPID ancestry，不复用 presentation foreground 的同 PGID helper。Host仍以 0600 scoped context、exact identity/generation/revision、Unix peer credentials、live root fingerprint、exact iTerm window/session/owner marker和当前 job亦为 root后代共同认证；owner/App不能选择 sender。presentation status、typed delivery/consumption与close仍保留同 PGID foreground fence，两个证明路径不得互相替代。

P0-E 后续真实运行在 root `d9cc20ef6609ead99d5b7000bb9033c8e7245170` / product `4107a18af58b2c296b3a2ccef8c35cdd0a8215ef` / PingAgent `715378242c754457564283d0a37726f89bd1086d` 上证明两个 Scenario 的四条 exact self request/reply 全部最终 `consumed`、zero cross/degraded、normal close四 participant且无残留；但两份 analyst command artifact为0字节，Host记录客户端在等待receiver consumption期间断开后的 BrokenPipe。该轮因此不是 acceptance，却把唯一剩余根因限定为同步返回语义，而非identity、route或transport失败。superseding implementation anchors为 product `08353667da95eceb56a0590a71b8ce93df8c7e5b`、PingAgent `c402acd968a7b95288d5eeed033fbd2ac487e5cb` 与root runner `ad874deeddc38b95c882be5f289bedda23273e16`：accepted snapshot与eventual consumed evidence分离，queued/delivery_attempted/delivered-unconsumed均由Host恢复，幂等重放不会重新调度已经consumed或degraded的delivery，rejected extra command只作为hash-only diagnostic而不冒充成功delivery。fixed-SHA peer review与fresh real witness完成前，P0-E仍是implementation candidate。

durable-enqueue 修正后的两次 fresh parallel diagnostic 已越过 prompt、self-send、accepted/consumed 与一侧 self-reply，但交替出现另一 Claude reviewer 首次 `identity.sender-rejected`；两轮均普通 safe close 四 participant且无 canonical mutation。同期只读进程采样证明 A/B client 的内核 PPID 链均命中各自 exact Claude root，因此没有用 retry 放宽 ancestry。root driver `df28588b47389d8e1597fedfc45fd26d678193e5` 仅在 sender message 尚未 accepted 前，对 exact-session iTerm loopback 的 `ConnectionClosedError` 重建最多三次只读连接；所有 binding/ownership/process drift 不重试。该增量 fixed-SHA review 与 fresh witness 前不生成 acceptance。

上述 narrow reconnect 经review `20260814-215223-9hlqvi` 通过后，fresh run 仍在 A reviewer 首次 reply 重现同一公开拒绝，B 双向 consumed；因此不继续扩大retry猜测。root diagnostic anchor `12c6fe7c9a6dd23f9784bddd22636bb3fa8643ff` 在 exact private participant root 写入0600、无PID/路径/正文/token的枚举stage/reason，区分binding、root process、peer process与exact session；公开Host/PingAgent错误和权限边界完全不变。该诊断只用于下一fresh run定位，不构成acceptance或新增public surface。

diagnostic fixed target `547a55b8c540d5e77177fd807e9c801f6db7469a` 经review `20260814-220339-eudmb7` 为P0=0/P1=0/P2=0后，fresh candidate witness通过（result SHA-256 `5cfd1e6834f7a91fe53172d3e7a69e407fa06dd0432d5b4a7b8eac9c36b6caee`）：双Scenario四Agent/四窗口重叠ready，2 self-send + 2 self-reply全部consumed且thread-linked，runner owner send=0、cross/degraded/identity ambiguity/WIP/canonical mutation/high-risk/private leak=0，相同相对evidence路径隔离；两Scenario normal safe close，force/recover=0。elapsed 163.437s、增量708022272 bytes在批准预算内，未要求vendor session identity。该artifact仍是`formal_gate_receipt=false`，必须先做witness peer review再进入formal closeout。

witness review `20260814-222000-rvwit1` 已对result hash、三仓fixed vector及20项claim逐项给出P0=0/P1=0/P2=0，允许进入formal closeout。EdgeStudio formal collaboration verifier现只读消费该owner-private candidate，以exact SHA/vector和closed-world字段验证为前提，把bounded machine facts写入既有identity、ready、delivery、cross-delivery、policy-routing receipt；candidate不改写、窗口/Agent不重启、PingAgent或vendor API不再调用。registry、gate ID、verifier name/params和dependency语义保持不变；正式receipt只覆盖本机当前fixed vector，不外推migration、efficiency、Developer ID发布或Harness整体完成。

formalizer fixed target `4a0f2540aeb575c2efc665500995aa765b2e77c6` 的review `20260814-223500-rvfvr1` 与formal receipt closeout review `20260814-224000-rvfrc1` 均为P0=0/P1=0/P2=0；五个existing required gate current/evidence已由同一candidate digest与三仓vector绑定，post-write hash/0600复核通过，P0-E至此formal completed。后续`ACC-MIGRATION-001`与`ACC-EFFICIENCY-001` formal refresh也已通过：前者仅覆盖internal App升级/恢复，后者仅覆盖自动编排、零人工上下文转移和parallel no-cross；二者都不扩大Host/App authority或供应商依赖，也不构成Harness整体完成。

P1-A fixed/pushed product `a2663c3709fe3579b23ca5d5003d9d86b37b0cb3` 已把 participant replace/detach 接入通用 Host/CLI/App：replace 在旧 generation 变更前 resolve并冻结新 launch spec，持久化cleanup evidence后才CAS `generation+1`，CAS前/后失败分别保留旧/新 identity的可恢复证据；detach先提交desired `detached`并停止新投递，cleanup歧义保留record/history/binding为`cleanup_pending`而不误删。Host-issued auth context随generation轮转，policy generation drift保持既有语义；driver只使用通用resolve/stop/repair/start/supervise contract，不调用vendor lifecycle API。Claude reviews `20260814-225811-2wil71` / `20260814-230500-rvp1a1` 为P0=0/P1=0/P2=0、`can_commit_push`。该slice完成不替代P2受影响gate refresh，其后按序进入P1-B App degraded/high-risk operability。

P1-B fixed/pushed product `5ba4243ab7e315db0f51d66b293e9e41055e7fbe` 已把通用Scenario repair、exact Participant force-stop与stale resource break接入macOS App。Participant degraded/cleanup_pending和resource lease采用typed、整页原子解析；action只对Host current projection中的recover target、live exact binding或stale exact lease显示，并携带current generation/revision/lease fence。App intent alert不能替代或缓存授权；四条含destroy的高风险operation继续由Host descriptor和native security adapter完成fresh permission/effect/subject复核及single-use confirmation。mutation后App刷新全部相关control-plane projection。Claude reviews `20260814-231138-11psea` / `20260814-231800-rvp1b1` 为P0=0/P1=0/P2=0、`can_commit_push`；P1-B product slice完成，主线进入P1-C，signed App witness/formal refresh留P2。

P1-C fixed/pushed root `e3be3fb8290cfac18e3299f7f943dc3f59c0752e` 与 product `ef19fe65d9da8e8d9f258f696a66aeb2a315fac1` 已接通聚合`scenario.preflight`、no-prompt permission observation与typed actionable error。Host只理解project/Scenario/Workspace/Participant/presentation permission等provider-neutral check，不含iTerm或供应商产品分支；macOS Automation、iTerm local API/authentication状态仅由root platform driver观察，结果不包含PID、本机路径或raw payload。probe不打开窗口、不请求权限；只有attached TUI Participant要求presentation permission，headless/detached明确跳过。App自动与手动preflight都只提供诊断/修复入口，不禁用lifecycle；structured error保留category/retryable/mutation_state/repair_action，高风险repair仍走既有Host trusted confirmation。Claude reviews `20260814-234623-i6xe1t` / `20260814-235500-rvp1c1` 为P0=0/P1=0/P2=0、`can_commit_push`；P1-C product slice完成，主线进入P1-D，formal refresh留P2。

P1-D fixed/pushed root `560828d1913b2f79051032068bde3f67dd0f92d3` 与 product `3f613ecaa60761897da4fa3f28c5f81bfae4035d` 已把presentation focus/topology restore和长操作progress/cancel接入生产Host、CLI与macOS App。root driver以exact owned window/session/marker和foreground process chain为authority，geometry只按current display-topology fingerprint恢复；Host不理解iTerm、Codex、Claude或vendor session identity。Scenario topology逐Participant返回，单窗失败、headless、inactive TUI和driver unavailable互不串扰。close progress使用同连接单调sequence；cancel使用独立IPC连接、Host generation、owner HMAC和exact operation ID，accepted不等于rollback。已开始close继续到安全边界，未开始动作记为cancelled evidence；partial result持久化后Scenario degraded，client refresh后再决定下一动作。观察client断线不影响durable operation，App的session fence阻止迟到progress污染UI。Claude reviews `20260815-003555-9l8y3j` / `20260815-010500-rvp1d1` 为P0=0/P1=0/P2=0、`can_commit_push`；P1-D product slice完成，主线进入P1-E，formal refresh留P2。

P1-E fixed/pushed product `92813fd0d82e8e5757ccd0816159cbe00e38278a` 在不改变frozen participant record的前提下，为`participant.list`增加current-generation configuration projection。CLI与macOS App现在显示participant generation、runtime profile和immutable model/provider/inference binding；App以identity+generation exact join两张集合，任一malformed、duplicate、missing或generation mismatch都拒绝整页，禁止把旧配置配给新generation。projection不含executable、argv、path、credential、continuity或private binding，Host core仍无供应商分支。product Harness 106、focused 65、root既有Codex/Claude profile/schema/driver conformance 101、Xcode 14项（1 expected live-host skip）通过；Claude review `20260815-005706-pnng06` / `20260815-011500-rvp1e1` 为P0=0/P1=0/P2=0。按2026-08-15用户裁决，Hermes真实runtime conformance延后且不得由空壳测试替代；主线直接进入P2-B，P2-A adoption/distribution保留为后续独立流。

P2-B final technical closeout固定在root `a22cd9b8d4807bff031ab654c36c7443e35a60c9`、product `92813fd0d82e8e5757ccd0816159cbe00e38278a`、PingAgent `c402acd968a7b95288d5eeed033fbd2ac487e5cb`。root aggregate implementation `825119cd46a595b638688715f2477dda5e809479` 与compatibility correction `a22cd9b8d4807bff031ab654c36c7443e35a60c9` 的reviews均为P0=0/P1=0/P2=0；后者让collaboration/Phase 4 producer显式绑定manifest digest，并使Phase 4 receipt按完整registry verifier params生成，未修改registry或gate语义。最终29个ACC current/evidence全部在该固定vector上通过freshness检查；13个non-spike required DoD derived current views全部passed，`DOD-SPIKES-001`继续11/11 source-fresh。derived DoD不生成immutable evidence；machine audit为29 ACC、13 DoD、DoD immutable evidence 0、文件`0600`、目录`0700`。P2-B完成后集成主线转入P2-A员工first-run/onboarding/distribution，不重复增加candidate runner，也不把Developer ID/notarization冒充已完成。

## 11. Local state 与 receipt

默认 state root：

```text
~/Library/Application Support/AI Collab/
├── projects/
├── scenarios/
├── machines/<machine-id>/display-topologies/
├── resources/
├── requests/
└── receipts/
    ├── runs/<run-id>/<gate-id>.json
    └── gates/<gate-id>.json
```

目录 `0700`、状态文件 `0600`。Harness-owned process binding 与 driver 可选的 exact runtime/TUI identity 只在 Host durable state；mailbox、receipt、日志和普通 UI 使用 redacted fingerprint。

默认 receipt threat model 只覆盖误改、过期、错误 producer 和 drift，不抵御当前用户任意代码执行。若项目要求 same-user forgery resistance，必须由用户另行裁决 Host-held signing key 和签名 API。

## 12. Composed gates

`edgestudio_gates.yaml` 是当前唯一机器集合源，组合五类证据：

1. Pre-implementation safety：exact-SHA 现场封存、非 Git 数据分类与恢复演练；
2. Product conformance：participant、runtime、policy、delivery、lifecycle、security、gate engine；
3. Platform/driver：macOS Host、IPC、TCC、iTerm、runtime identity/continuity、window topology；
4. EdgeStudio project：repo/workspace/Git/environment/resource；
5. Rollout/decision：scope、efficiency、rollback、DoD aggregates。

`group` 只用于报告/evaluation 分类；`workflow.phase_gate_assignments` 使用与 `phase_order` 相同的 phase ID，并要求 registry 中每个 gate 恰好分配一次。二者不是同一 namespace。

Canonical delivery state 统一为 `queued | delivery_attempted | delivered | consumed`；registry/verifier/docs 不得再使用 `pending` 作为并列状态。

### 12.1 Gate 集合投影

Stage 0：`PREIMPL-SNAPSHOT-001`。

Immediate：`LEGACY-DELIVERY-001`。

Acceptance：

`ACC-HOST-001`、`ACC-IPC-001`、`ACC-PERM-001`、`ACC-PROJECT-ACCESS-001`、`ACC-PROVISION-001`、`ACC-GIT-STORAGE-001`、`ACC-GIT-ORIGIN-001`、`ACC-GIT-GUARD-001`、`ACC-REMOTE-NAME-001`、`ACC-ENV-001`、`ACC-REGISTRY-001`、`ACC-PARTICIPANT-IDENTITY-001`、`ACC-PARTICIPANT-LIFECYCLE-001`、`ACC-RUNTIME-CAPABILITY-001`、`ACC-MODEL-BINDING-001`、`ACC-READY-001`、`ACC-DELIVERY-001`、`ACC-CROSS-DELIVERY-001`、`ACC-POLICY-ROUTING-001`、`ACC-RESUME-001`、`ACC-PARTICIPANT-FAULT-001`、`ACC-WINDOW-TOPOLOGY-001`、`ACC-WIP-001`、`ACC-CLOSE-001`、`ACC-RESOURCE-001`、`ACC-DESTROY-001`、`ACC-MIGRATION-001`、`ACC-ROLLBACK-001`、`ACC-EFFICIENCY-001`。

DoD：

`DOD-SCOPE-001`、`DOD-SPIKES-001`、`DOD-PERMISSION-001`、`DOD-IPC-001`、`DOD-ISOLATION-001`、`DOD-PARTICIPANT-001`、`DOD-COLLABORATION-001`、`DOD-WINDOW-001`、`DOD-GIT-001`、`DOD-DELIVERY-001`、`DOD-LIFECYCLE-001`、`DOD-RISK-001`、`DOD-DIAGNOSTIC-001`、`DOD-EFFICIENCY-001`、`DOD-ROLLBACK-001`。

Phase -1：

`SPIKE-HOST-001`、`SPIKE-IPC-001`、`SPIKE-ITERM-001`、`SPIKE-RUNTIME-DRIVER-001`、`SPIKE-TUI-ID-001`、`SPIKE-TUI-LIFE-001`、`SPIKE-DELIVERY-001`、`SPIKE-WINDOW-TOPOLOGY-001`、`SPIKE-STORAGE-001`、`SPIKE-CLOSE-001`、`SPIKE-UPGRADE-001`。

以上只是可读投影，不表示 passed；current status 必须由 verifier evidence、live probe 与 dependency freshness 计算。

## 13. 实施计划

### Stage 0

在任何 Harness 实现代码、新产品仓库创建或 canonical workspace 迁移前，固定全部 declared repositories 的 exact SHA vector，分类非 Git 数据，在工作区外保存带 checksum 的恢复材料，并在非 canonical 临时目录完成恢复演练。机器基线见 [baselines/README.md](baselines/README.md)；只有 `PREIMPL-SNAPSHOT-001` 通过后才能进入 Immediate。

### Immediate

独立完成 PingAgent delivery repair、测试与 `LEGACY-DELIVERY-001`。

### Gate 0

用户确认 EdgeStudio v1 范围、代表性任务、收益目标、资源预算和停止条件。

状态：用户已于 2026-08-06 批准推荐范围，结构化记录见 [decisions/gate0_scope_20260806.json](decisions/gate0_scope_20260806.json)。该裁决允许进入 Phase -1，但不授权新建独立产品仓库或迁移 canonical workspace。

### Phase -1

Disposable workspace 验证 App/Host/IPC、iTerm presentation、runtime driver identity/continuity、ACK delivery、display topology、environment cost、close fencing 与 upgrade migration。

### Phase 0

冻结完整 versioned contract surfaces：

1. product contract envelope/version；
2. project descriptor；
3. App/Host/CLI typed IPC、capability 与 error schema；
4. Scenario/Participant state、lifecycle、operation/generation schema；
5. Runtime/Presentation driver interface、registry 与 model binding；
6. Collaboration Policy/routing schema；
7. repo manifest；
8. Workspace/Environment adapter plan/receipt；
9. composed gate registry loader/digest/phase/projection；
10. permission/high-risk confirmation matrix。

第 10 项在 Phase 0 冻结 contract，在 Phase 4 实现并 acceptance。精确覆盖/依赖/status matrix 位于 [phase-0/README.md](phase-0/README.md)，属于实施账本，不进入稳定 project descriptor。

当前状态来源于 `user_decision`（2026-08-10、2026-08-11）与 formal evidence（2026-08-11）：Phase -1、registry normalization 与 Phase 0 A–J 均已闭环；各 fixed SHA/review 见 [IMPLEMENTATION_PROGRESS.md](IMPLEMENTATION_PROGRESS.md)。J fixed implementation `3bc693da01d5da7154563749fb9aa73857792e50` / review `20260811-174835-oveydb` 完成后，用户批准暂缓 fingerprint cutover/hardening，按 M1–M4 先交付可运行产品路径。H product contract 只固定 logical plan/journal/receipt/observation；本节的 exact Git SHA、no-local clone、target ref/guard 与 per-scenario environment 由 EdgeStudio integration validator/payload join，不写成 Host 产品分支。J core 只固定 operation/permission/confirmation 的 product-neutral join；本节所列 macOS permission、trusted App UI 与 force/repair/destroy 行为仍是 platform integration/Phase 4 implementation。

M1 fixed product target `87dc3573fdc3d6de9f3cb8a1eba52f3953b79b16`、root integration `317e38684dd796f9c7d5bc1d8fbc5ee6463ae44a` 已由 review `20260811-190003-h0xjej` 以 P0=0、P1=0、`can_commit_push` 闭合并进入两仓 `main`。该切片提供最小 Host、typed local IPC、薄 CLI 与单 Scenario durable path，并以两阶段 durable transition、success/failure operation journal 和 restart reconcile 落实冻结 D lifecycle。它创建的 0700 私有空 workspace binding 只保证 Scenario state 真实且不污染 canonical source，不是本 Phase 1 的 exact-SHA multi-repo/environment execution。M2 现在必须在扩展 runtime/participant 前完成下述 Phase 1 execution。

M2 superseding fixed product target `b1fbdbaa05d74c44cbd47ac99f0b1be17c65527e` 与 root tracked target `407ef9288585a4dfe9e263e9923f26ee633129ef`（adapter code anchor `7b04b42806d908fb9a9ec57b28c202b2c13d49aa`）已把下述 Phase 1 主路径接入真实 Host/CLI：read-only exact plan、6-repo no-local materialization、manifest origin 与完整非 shallow/promisor/alternates canonical object storage、真实 scenario ref、dispatcher+provider pre-push guard、scenario-local minimal editable venv、atomic publish、restart reconcile、status/dirty-WIP/binding-marker-drift observation 与 exact review snapshot。执行结果用冻结 H validator 校验；真实 CLI 在 Scenario 从 closed revision 2 打开为 running revision 4 后再次 status，workspace 仍 `aligned`，snapshot 精确 pin 当前 root code anchor/product SHA，canonical source WIP 前后相等。旧向量 review `20260811-195230-we5gk5` 已被 self-review remediation supersede，不能用于 cutover；新向量由 review `20260811-202320-z7u7ao` 以 P0=0、P1=0、P2=0、`can_commit_push` 闭合并已进入两仓 `main`。M2 只实证 plan/provision/status；H descriptor 中 repair/destroy 是 versioned contract-family capability target，必须等 Phase 4 的 J permission/confirmation 与 destructive fencing 后才能进入 public Host registry，不得从当前 receipt 外推为已实现。

M3 fixed product target `25917af2f5d2d775fe25a18b7453b75c30732803`、root driver code anchor `7fdbcca3f2805048f7522d8da3e3bcb40ceec1b5` 与 tracked target `90411fef806652189156887078370b238df27f4e` 已由 review `20260811-211118-r1w721` 无阻塞闭合并进入 `main`。它实现 generic participant `add/start/status/stop`、D durable lifecycle/failure replay/restart repair-required、E runtime/presentation ACK join，以及 EdgeStudio inert process + official iTerm presentation plugin。真实 TUI witness 验证 exact top-level window、owner marker/session `jobPid`/process binding、health、exact close 与公共 raw identity redaction；headless 真实 process test 验证零窗口。

M4 fixed product target `2896a56d3cb71d93d39d3b8e1d48a8e13d38cbf0`、PingAgent transport target `6a86cf1ebf169202425e7bc9cf07ab8325aff9e7` 与 root target `28a4271e9760c3a1d6dffefdcdaf7e5e4909d987` 已由 review `20260811-225753-sx9h79` 以 P0=0、P1=0、P2=0、`can_commit_push` 闭合并进入三仓 `main`。首次真实双 Agent dogfood 在 M2 隔离工作区内以 scoped trust gate 启动两个 TUI participant，通过 default-deny policy 与 exact participant-generation route 完成 `attempt_started → ack_accepted → consumed`，没有人工上下文转移；供应商 session identity 保持 optional。M1–M4 working vertical slice 至此完整。当前续接点是按用户批准的 §14.1 节奏回收延期的 fingerprint/hardening debt，随后进入 Phase 4 close/resource/security/diagnostics；该次 dogfood 不等于 Phase 4 或完整 acceptance。

P4-A product `301cc051ea3c19bdf673c3790496a46f4f633f84` 与 root integration `5fe3d7e81628ea33273ec23159106b6cb2f56ded` 已接通 safe `scenario.close`、minimal diagnostic JSON 和 generic profile-driven idle/drain，并由 superseding review `20260812-112855-p4tsh0` 以 P0=0、P1=0、`can_commit_push` 闭合后按依赖顺序进入两仓 `main`。EdgeStudio driver 只在 exact owned binding 上动作：headless inert profile 走 bounded graceful termination；TUI 只有在 input-ready screen 连续稳定，或发送 profile drain sequence 后同一 ready screen 摘要连续稳定时才关闭 exact owned window。既有 scenario/participant active operation 会在 mutation 和 driver action 前拒绝；timeout/unknown 不关闭 busy window、不 kill process、不清 live binding，公共结果只保存 opaque binding、profile ref、时间和 evidence digest。该切片已通过真实 headless success/timeout、busy TUI timeout、WIP preservation、frozen D journal 与完整 Harness 回归；resource lease/heartbeat、explicit high-risk force-stop、repair/destroy 和 acceptance 仍分别属于 P4-B/C/D。

来源：`user_decision`（2026-08-12）。上段历史 P4-A 的 TUI screen idle/drain 闸门已被现实使用模型 supersede：Harness 既服务自动化研究，也服务会主动输入、监控、查看窗口的员工。当前 EdgeStudio 交互式 profile 在收到显式 `scenario.close` 后，只按 exact owner marker/window/session/process binding 关闭，不读取输入框文本或推断 idle；若窗口关闭后进程仍在，只对同一已验证 owned process group 做普通 SIGTERM。窗口和进程均消失才返回 `requested` 成功，否则返回 timeout/unknown 并保留诚实诊断。startup trust/ready 检查不变。

P4-B superseding fixed implementation `22bf945dd2ea53d828f1360a023914055495adb7` 与 root `f658ac56d05b7fd6bb9abc0b14226c4a38537339` 把 resource/process supervision 接入真实 generic process path，并由 final review `20260812-125534-efpcon` 以 P0=0、P1=0、`can_commit_push` 闭合。产品 ledger 记录 holder/scenario/participant generation、runtime binding、process-start/boot/heartbeat/fencing hash 与 active/stale/released lifecycle；EdgeStudio driver 的 owner-private state 保留 PID 和 raw fencing token，公共 Host/CLI 只返回 hash evidence。Host 会跨全部 Scenario ledger 检查 machine-shared resource，其他 holder 的 active/stale lease 都禁止第二条 active lease；冲突后的 exact cleanup 若失败，新 binding/ACK 会以 degraded cleanup_pending durable 保留，允许后续精确 stop。当前 root 插件只发布 `exclusive_runtime` observation；port/device/compute/accelerator 由未来 project/platform plugin 声明，不在 EdgeStudio core 添加产品分支。该实现不修改 composed gate registry 或 machine evidence，不完成 acceptance。

P4-C superseding fixed product `35670455b6e3d1cb024e2658576e4d1e106ba9e1` 与 root implementation `40202e94d1740551d8396c8b7a0189356aae466b` 接通四个冻结 J high-risk mutation，并由 review `20260812-141258-48q3ls` 以 P0=0、P1=0、`can_commit_push` 终止式闭合。`ai_collab_security_adapter.json` 注册 owner-controlled project/platform plugin：真实 permission observe 使用 owner-private exact process identity 或再次执行 EdgeStudio Workspace status/WIP probe，trusted presentation 使用 macOS native dialog；消息、Host state 与 receipt 只保存 digest，不保存 PID、private path 或原始 session identity，也不调用 Codex/Claude API。普通 `participant.stop` 只发送 graceful SIGTERM，存活即保留 binding/cleanup_pending；独立 `force-stop` 在 fresh present observation 与 exact one-shot confirmation 后才允许 escalation。Host 同时拒绝 future presenter decision，避免形成 authorization issued time 晚于 consumption 的无效链。`resource.break` 只在 exact stale lease 的 owned process fresh absent 时释放。repair 只接受 aligned binding/WIP 且不覆盖本地改动；destroy 只处理 Harness-owned Scenario bundle，要求 stopped/detached participants 与 released leases，原子 remove 后独立证明 missing，canonical source WIP 保持不变，Store/Workspace history 保留。真实六仓测试同时以冻结 H/J/D validators 验证 repair receipt、destroy absence、permission/authorization/consumption 与 lifecycle state；旧 targets `4df5b656...` / `b00bab7d...` 及其 review 已作废。本切片不修改 acceptance gate/receipt，P4-D 继续负责 crash/restart 与 composed acceptance。

P4-D fixed product `ed9fcbf5f67ba66ad227d4f23a573ec2d71f595e` 与 root `1b1b341ebe2b52cd3e896582749ffd237739eddd` 把六个 external-action crash window 收敛为同一规则：provision 通过 atomic publish marker reconcile；close 与 force-stop 先记录 external reports/release evidence；resource break 先保存 exact authorization consumption + lease fence；repair/destroy 在 Workspace 私有 state 保存 request/operation/WIP fence，EdgeStudio adapter 用同一 operation ID 幂等返回已完成 repair marker或已缺失 destroy bundle。Host 启动先恢复 Workspace owner outcome，再 join Store，避免 Store 抢先把可证明的结果误判为 unknown。review `20260812-152514-44zpgv` 给出 P0=0、P1=0、P2=0、`can_commit_push`，双仓已按依赖顺序进入 `main`。registry YAML 未改；五个既有 Phase 4 verifier 均已生成 fixed-SHA formal receipt。rollback 由 disposable 独立 iTerm window 真实执行 `ai-pane-register` 与 legacy watcher round trip，并证明 canonical WIP 不变、window/watcher 清理、Harness Host 未参与。tracked closeout `318a7a748acfae95ac5552a453328c0a3e2095d7` 由 terminal review `20260812-154836-vcon65` 以 P0=0、P1=0、`can_commit_push` 闭合，P4-D/Phase 4 完成。下一主线按 active architecture 进入 Phase 5 limited dogfood，而不是扩展 App packaging、migration 或 efficiency claim。

来源：`user_decision`（2026-08-14）。三个 Phase 5 candidate、native App 与 recovery witness 完成后，用户重新核对最初“长期任务房间”目标并批准 `P0 → P1 → P2`：P0 先把 PingAgent-backed participant self identity/send/reply、team/policy plan、Scenario delivery collection、App collaboration control plane 与真实双向 reply 验收产品化；P1 再补 replace/detach、App 高风险/repair、preflight/error/progress/focus/runtime profile；P2 在稳定流程上完成 onboarding、受影响 gate 与 final DoD。此前“立即冻结 App/Host fingerprint”的续接点被该裁决 supersede。

### Phase 1

实现 exact-SHA workspace plan、no-local provisioning、Git origin/ref/guard、per-scenario environment 与 exact-SHA review snapshot。

### Phase 2

实现 macOS App/Host、project/scenario/participant registry、typed operations、transaction/CAS/fencing/reconcile 与 legacy permission migration。

2026-08-13 fixed implementations 已补齐 Phase 2 的 native daily-operation 与 internal/dogfood delivery：SwiftUI App 可注册/选择项目，创建、准备、打开、关闭 Scenario，按 Driver template 添加/启动/停止 participant，并查看 diagnostic/resource/policy/workspace receipt；destructive request 先展示 App intent confirmation，再进入 Host trusted confirmation。Swift client 使用生成的 operation registry digest 与 capability mapping 直接连接 Unix socket，不 shell out；public model 不保存 canonical path，participant list 以 Scenario target 查询。签名 App 内嵌独立 Python Host payload 和 Swift helper，由 current-user `SMAppService` 注册、启动、KeepAlive 和注销；helper 从 macOS system proxy 生成标准 proxy environment，通用 participant driver 在 launchd 精简 PATH 下以 login shell 发现用户已安装 CLI，Host/App core 均无供应商分支。稳定 installer 校验 Team ID/code seal、使用 APFS 原子替换、等待 typed Host health，升级失败则恢复上一版本并保留失败候选；真实 first install、upgrade、fault recovery 与 unregister 已通过。项目选择的目录探针已修正为目录枚举而非把目录交给文件句柄，Swift 回归与产品 contract 测试通过。

来源：`evidence`（2026-08-15，旧安装的deep/strict seal仅报告HarnessService下运行期Python byte码为added resources）。installer只可quarantine owner-owned regular `__pycache__/*.pyc`，且必须在原App seal恢复后才继续升级；其他文件、symlink或签名差异一律恢复cache并fail closed。该修复不访问或修改Scenario、Workspace、mailbox与Host state。

受保护项目也已形成窄边界 witness：macOS 26.5.1、同 Team ID Apple Development 签名、App TCC reset 后，用户从 `NSOpenPanel` 选择受保护项目并看到系统权限提示；App 退出且 Host 被 `SIGKILL` 后，launchd 恢复的 Host 在 App 不运行且没有新增用户交互时成功重新验证同一项目，opaque project ID 与 binding digest 不变。该 internal/dogfood 组合因此不增加 bookmark broker 或 public path contract；Developer ID/notarized、其他 macOS 版本和非用户选择目录仍未证明。fixed-SHA implementation reviews 均为 P0=0/P1=0，独立 witness review 接受三个防假阳性控制与结论边界；精确锚点见实施账本。

2026-08-14 真实 App dogfood 还闭合了 Participant failure recovery：fixed product `e88327156984000eb03cd9142a338f92242a3170` 与 root `bb35549fdffa7ab0bd77713b9dbb5aed098ba8d7` 由 review `20260814-111735-upub64` 以 P0=0、P1=0、P2=0、`can_commit_push` 接受。已安装签名 App 保留旧 `launch_failed` generation 证据，从 pre-binding absence 证明路径恢复到 `stopped (generation+1)`；后续显式 Start 得到可回应的真实 Codex TUI，Stop 释放 exact binding/lease，Close 将 Scenario 收敛为 closed。全链不用 vendor lifecycle API、不自动 force-stop、不复用失败 identity，也不删 Scenario/Workspace/journal。该结果是 machine-local internal witness，不替代 formal gate receipt；其主线价值是解除真实员工操作的失败死路，完成后应回到 fingerprint/formal evidence closeout，不继续扩张 App 细节。

来源：`evidence`（2026-08-15，Codex CLI 0.147.0 interactive/exec/resume隔离对照）。optional exact-resume adapter必须区分terminal input-ready与vendor conversation materialized：fresh Codex窗口在首条真实prompt前没有SessionStart identity，不能为满足gate而自动发送模型turn，也不能扫描全局session store；首条真实输入触发participant-private hook后，status/supervision与normal Close/Stop固化binding。已绑定Resume只调用`codex resume <exact UUID>`，旧历史加载且TUI input-ready后可恢复员工操作；后续`source=resume` hook对同一UUID复核。fresh未输入即关闭没有conversation continuity可丢失，invalid/mismatched proof仍只降级exact participant。

fresh r4员工实测进一步固定了启动边界：dependency/bootstrap、iTerm API连接、display topology、collaboration material与window-create必须分别留下bounded private stage evidence。fresh Start不等待vendor hook；已知closed transport只允许在window-create前重连。若失败证据明确停在pre-window且无外部效果，Recover可以安全轮换generation；一旦进入window-create或结果未知，保持cleanup pending，不能自动重试、猜测窗口不存在或用新conversation掩盖失败。root fixed implementation为`c724589f507ccc3970248f8b21502812d1d0a1f9`，Claude fixed-SHA review `20260815-184323-ne0jlh`为P0=0、P1=0、P2=0；signed App已安装为Host generation 22，installed-bundle Codex与Claude fresh Start/Stop smoke通过，员工双Agent continuity/collaboration revalidation待完成。

### Phase 3

实现 runtime/presentation drivers、dynamic participant lifecycle、continuity、ready/bind、policy routing、ACK/retry、topology geometry 和 zero cross-delivery。

### Phase 4

实现 busy close、resource/process supervision、repair、gated destroy、diagnostic JSON、high-risk confirmation 和 crash/restart acceptance。

### Phase 5

从低风险 EdgeStudio scenario 开始有限 dogfood，与 Gate 0 基线比较；任一 WIP/session/delivery/Git/security 硬不变量失败立即回滚，收益不覆盖复杂度时停止或缩减。

来源：`user_decision`（2026-08-12）。Phase 5 同时面向自动化研究和员工主动操作，candidate runner 不再用自然语言长度、findings 数量或参与者对工作内容给出的 P0/P1/verdict 代替运行健康度。运行层只要求两个 exact-owned 窗口可启动、模型可正常回应、双向 typed delivery 被消费、隔离产物可写且 canonical source 不变，并在显式 close 后证明对应窗口与 descendant process chain 消失。工作内容的 review 结论继续记录并影响后续产物准入，但不反向改写运行层结果。一次 iTerm RPC 瞬态错误只能对已经完整验证的同一 exact binding 做一次 bounded close retry；status/delivery/close 观察到动态 foreground `jobPid` 时，复用既定 descendant-chain 规则验证其沿内核 PPID 链回到 fixed root 且全链 PGID 相同。

2026-08-12 首个 fixed-vector candidate 已通过上述运行层：两个真实窗口自动启动并 ready，Codex 与 Claude 均完成隔离产物，双向 route 各一次到达 consumed；显式 close 后两名 participant 均 stopped、live binding 清除、窗口和 fixed root/descendant process 不再存在，六类硬不变量为 0。该结果为 machine-local candidate，`formal_gate_receipt=false`、`efficiency_improvement_claimed=false`；它允许 Phase 5 继续，但不完成 Phase 5 或整个 Harness。当时记录的配对人工基线续接点已被下述用户纠正 supersede；固定 SHA、result digest 与 peer review 见 [IMPLEMENTATION_PROGRESS.md](IMPLEMENTATION_PROGRESS.md)。

2026-08-12 第二个同项目 fixed-vector candidate 也已通过：Harness 先打开 coordinator 与 analyst 两个真实窗口，再在 Scenario `running` 时动态加入并启动第三个 synthesizer 窗口；三个 participant 均 ready，analysis → peer review → synthesis 三段 route 各一次到达 consumed，三个隔离 JSON 以 digest 串联且 canonical source 不变。close 对每个 exact binding 同时保存并验证 launch-root 与当时接受的 foreground descendant PID，三名 participant 最终均 stopped、live binding 清除，未使用 auto force-stop/repair，六类硬不变量为 0。参与者产出的工作 review 为 `accept_with_observations`；runner 明确不以自然语言长度、finding 数量或工作 verdict 代替运行健康。该 machine-local 结果仍为 `formal_gate_receipt=false`、`efficiency_improvement_claimed=false`，只完成第二个有限场景。

来源：`user_decision`（2026-08-12）。手工打开三个窗口、在同一任务中复制粘贴 analysis/review/synthesis 的比较不能直接验证 Harness 的多实验价值，该人工基线已终止且不形成 receipt。EdgeStudio integration 的最近缺口改为两个真实 Scenario 在同一 Host 上重叠运行、使用 Scenario-scoped identity/workspace/delivery/resource ownership，并证明关闭 A 不影响 B 继续工作。该纠正不改变 composed registry 或效率 gate 语义。

2026-08-12 第三个 fixed-vector candidate 已通过：同一 Host 上两个 Scenario、四个真实 TUI participant 同时 ready；两边刻意复用相同 logical participant IDs，但 workspace/runtime/presentation binding 与 active lease 均隔离。A、B 首次 route 以及 A close 后 B 的 continuation route 共三次各一跳到达 consumed；A→B intent 经 default-deny，并由公共 IPC 返回 `auth.capability-denied`，没有 delivery record 或 sentinel。A close 后 B 的两条 binding 保持不变、模型继续回应并写出 hash-linked artifact；最终 B 也 clean close，四 participant stopped、live binding 清空、无 force/repair，六类硬不变量为 0，canonical source 不变。首次运行只因 runner 断言内部错误码而 fail closed 并完整 cleanup，superseding fix 改为既有 public mapping 后通过。该结果仍为 `formal_gate_receipt=false`、`efficiency_improvement_claimed=false`，不能外推为 `ACC-CROSS-DELIVERY-001` completed。下一主线按既有 registry 补齐 Phase 1–3 formal acceptance、migration、efficiency 和剩余 DoD；固定 SHA、result digest 与 reviews 见 [IMPLEMENTATION_PROGRESS.md](IMPLEMENTATION_PROGRESS.md)。

## 14. EdgeStudio 硬不变量

以下必须为零：

- WIP 丢失；
- 错误 runtime/session resume 或伪装 continuity；
- cross-delivery 或同 runtime instance 覆盖；
- 错误 remote/ref、误推 main；
- 未授权高风险动作；
- credential、secret、PII 或未 redacted session identity 泄漏。

平均效率收益不能抵消任何一次硬不变量失败。

## 15. 完成定义

EdgeStudio integration 只有在 composed registry 的全部 required product/platform/project/decision gates 当前通过、无 stale dependency，且用户批准的效率/成本目标达成时才可称为完成。

其中 collaboration acceptance 必须证明真实 participant 使用各自 scoped identity 通过 PingAgent-backed client 完成 send/consume/reply，App 能观察 topology、policy 与 delivery health，且相同 logical participant ID 在并行 Scenario 中仍 zero cross-delivery。外部 runner 代 participant 调用 `message.send` 不能满足该完成条件。

设计文档、测试输出或 `.dispatched` sidecar 都不能独立构成完成证据。

## 16. 官方平台依据

macOS、iTerm2 与首批 runtime driver 的实现边界以当前官方接口为准，Phase -1 receipt 必须记录实际版本和实测结果：

- [Apple SMAppService](https://developer.apple.com/documentation/servicemanagement/smappservice)：用户级 Login Item、LaunchAgent/Daemon 注册与授权状态；
- [Apple XPC](https://developer.apple.com/documentation/xpc)：受 launchd 管理的本地进程间通信与服务生命周期；
- [NSAppleEventsUsageDescription](https://developer.apple.com/documentation/bundleresources/information-property-list/nsappleeventsusagedescription)：发送 Apple Events 的用途说明；
- [AEDeterminePermissionToAutomateTarget](https://developer.apple.com/documentation/coreservices/3025784-aedeterminepermissiontoautomatet)：Automation 权限预检；
- [iTerm2 Python API](https://iterm2.com/python-api/) 与 [iTerm2 documentation](https://iterm2.com/documentation.html)：受支持的 scripting 能力与兼容边界；
- [Codex hooks](https://developers.openai.com/codex/hooks/)、[Codex CLI resume](https://learn.chatgpt.com/docs/developer-commands?surface=cli#cli-codex-resume)、[Claude Code hooks](https://code.claude.com/docs/en/hooks) 与 [Claude CLI reference](https://code.claude.com/docs/en/cli-reference)：session lifecycle、ready/bind、resume 和 retention 的受支持边界。

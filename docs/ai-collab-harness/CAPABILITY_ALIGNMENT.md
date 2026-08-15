# AI Collaboration Harness 能力对齐与更新域追踪

> 状态：**active current-state routing source；不替代 `product_architecture.md` 的 normative contract、`edgestudio_gates.yaml` 的 gate truth 或 `IMPLEMENTATION_PROGRESS.md` 的 fixed-SHA 账本**
>
> 来源：`user_decision`（2026-08-14 主线复核；2026-08-15 P1-E scope收敛、P2-B technical closeout与P2-A独立采用流边界；2026-08-16独立产品仓抽离与双 AI 完整回归门槛）
>
> 最近更新：2026-08-16

本表防止 Harness 升级再次被单个按钮、一次 dogfood 或中间 receipt 带离主线。每个 implementation slice 必须同时检查受影响的 Host、PingAgent、CLI/TUI client、App、driver/plugin、acceptance 和 tracked docs；没有进入某个 surface 必须说明是合理的 surface-specific boundary，不能默认为“CLI 有即可”。

## 1. 目标与 surface 分工

- **Host**：Scenario/Participant identity、policy、delivery、generation/fence、journal、resource、permission 与 gate current view 的唯一 authority。
- **PingAgent**：participant-facing 简洁命令、legacy mailbox compatibility 与 exact-session transport；Harness Scenario 内的 send/reply intent 必须回到 Host，不形成第二套路由状态。
- **CLI**：自动化、诊断、开发的 raw typed client；同时提供 participant 从 scoped launch context 使用的简洁 self send/reply。
- **macOS App**：员工默认 collaboration control plane，管理长期任务房间、团队、policy、delivery/review health、generation drift、权限、错误和 repair；不复制 vendor chat，不冒充 participant。
- **Runtime/Presentation driver**：供应商中立 capability dispatch、Harness-owned process chain、exact TUI binding、delivery/consumption ACK 和 platform presentation。

内容交互主要发生在独立 TUI；“App 不做聊天客户端”不能被解释为 App 不需要 collaboration topology、health 与 repair。

## 2. Surface parity 基线

当前 P0-A reviewed baseline 为 root code anchor `caa4625e76c47a6b7780a73b7091fe9c823fde1e`、edge-studio-dev `77dee3923101f72f052a09817d7e96117b21756b`、PingAgent superseding `715378242c754457564283d0a37726f89bd1086d`；P0-B reviewed baseline 为 root code anchor `7edeb7970083b13565e7591997b2212d9ff84aea`、root reviewed target `3148198054ce0af4a9d13405259ad42b92ab378b`、edge-studio-dev `fdbb94cf8bfde07278c60531dae051e8fade563d`；P0-C reviewed baseline 为 edge-studio-dev `d75fb8e2031d4245237ede3640ca9280f0b7c2ed` 与 root `938061a1f07d17afd1288a37ca894a5760f29b8f`；P0-D superseding fixed product为 edge-studio-dev `4107a18af58b2c296b3a2ccef8c35cdd0a8215ef`，reviewed root target为 `7ae6ebdec80fe51cebbbe81371b215aeeae06e65`；P1-A fixed product为 edge-studio-dev `a2663c3709fe3579b23ca5d5003d9d86b37b0cb3`；P1-B fixed product为 edge-studio-dev `5ba4243ab7e315db0f51d66b293e9e41055e7fbe`。产品 Host registry 当前有43个public operation；automation CLI基本覆盖owner operations，App capability map接入35个。两个self operations只允许持有Host-issued scoped context且通过本地process-chain认证的participant调用；App现已接入三个policy template/plan operations、redacted `delivery.list`、Host-authorized `delivery.retry`、participant replace/detach、Scenario repair/destroy/force-destroy、exact Participant force-stop和stale resource break，但仍不接入participant self operations、owner `message.send`或private `delivery.status`。该计数是current-state snapshot，Host operation或App workflow改动后必须按§4同步更新；逐操作按钮相等不是目标，等价的员工闭环才是目标。

App 当前未调用的 Host operation：

```text
workspace.status
participant.status
policy.apply
message.send
message.send-self
message.reply-self
delivery.status
delivery.consume
```

合理的单 surface 能力包括：CLI 的 foreground Host/raw JSON/socket override；App 的目录选择、system permission prompt 与 native confirmation；driver/PingAgent 的 consumption ACK 和 exact-session injection。它们不要求机械复制到其他 surface。

## 3. 当前能力与优先级

| Priority | Capability | Current evidence-backed baseline | Remaining product gap | Update domains | Acceptance / gates |
|---|---|---|---|---|---|
| P0-A | Participant-scoped Agent send/reply | `completed_product_slice`：Host 已有 scoped self send/reply、generation/revision fence、local peer PID + owned descendant-chain 认证与 durable reply provenance；PingAgent Harness 模式只把 intent 交回 Host，legacy 模式保持兼容；terminal review P0=0/P1=0/P2=0 | product slice 无剩余实现/review阻塞；真实双 TUI self-send/reply 与并行 no-cross 属于 P0-E；Scenario list/thread public projection属于 P0-C。App 不调用 self operations 是禁止 impersonation 的设计边界 | product IPC/client/delivery；participant launch context；PingAgent CLI/transport；root driver/profile；Swift registry/build payload；active docs | `ACC-DELIVERY-001`、`ACC-POLICY-ROUTING-001`、`DOD-COLLABORATION-001`、`DOD-DELIVERY-001` |
| P0-B | Team/policy plan | `completed_product_slice`：project plugin 提供 path-free configurable team/policy data；Host/CLI 支持 list → deterministic plan/effect preview → digest-fenced explicit apply；plan 固定所有 declared participant current generations，policy show 暴露 drift，新 send 在 drift 时 fail closed；fixed-SHA review P0=0/P1=0/`can_commit_push` | product slice 无剩余实现/review阻塞；App team/policy models 与员工操作流已进入 P0-D。root adapter 的 symlink/wrong-owner/bad-JSON 负测已随P0-E reviewed implementation补齐 | product protocol/delivery/client；project policy plugin；CLI；Swift registry/build payload；active docs | `ACC-POLICY-ROUTING-001`、`ACC-PARTICIPANT-FAULT-001` |
| P0-C | Scenario delivery read model | `completed_product_slice`：Host/CLI 提供 bounded Scenario collection 和 thread-root filter；projection 只含 redacted participant generation、message kind、policy snapshot、thread/reply identity、state/event/degraded/retry health；digest-fenced pagination 在集合或 active retry health变化时 fail closed；fixed-SHA review P0=0/P1=0/`can_commit_push` | product slice 无剩余实现/review阻塞。App capability map已接入 `delivery.list`，实际 topology/health/retry UI 属于 P0-D | product protocol/delivery/client；CLI；Swift contract；delivery privacy/retry tests；active docs | `ACC-DELIVERY-001`、`DOD-DIAGNOSTIC-001` |
| P0-D | App collaboration control plane | `completed_product_slice`：App 可选择 project team template，预览 exact generations/route/retry effect，显式 digest-fenced apply；显示 active policy version、generation drift、redacted delivery/thread health与 Host-authorized retry；capability tests证明无 participant/owner send composer或 private delivery status；superseding review P0=0/P1=0/P2=0 | signed App 已在真实 Host 上完成 template preview/apply、exact generation/policy current 与 Scenario close 控制面 witness；fresh Agent TUI self roundtrip不冒充为本切片证据，归 P0-E。此前留给P1-B的通用repair/high-risk App操作现已由P1-B完成 | Swift generated contract、IPC、models、view model、UI；Host read model；active docs | native App contract tests + signed App real Host control-plane witness |
| P0-E | Real Agent-native acceptance | `completed_formal_acceptance`：fixed root `547a55b8c540d5e77177fd807e9c801f6db7469a`、product `08353667da95eceb56a0590a71b8ce93df8c7e5b`、PingAgent `c402acd968a7b95288d5eeed033fbd2ac487e5cb` 的fresh witness `5cfd1e6834f7a91fe53172d3e7a69e407fa06dd0432d5b4a7b8eac9c36b6caee` 已实现双Scenario四真实Agent、2 send+2 reply全部consumed/thread-linked、zero cross/degraded、same-path isolation与normal close；witness、formalizer与receipt reviews均P0=0/P1=0/P2=0 | 本slice无剩余产品/acceptance阻塞；candidate保持non-formal且immutable，正式性来自5个existing required gate receipt。Harness整体仍需P1 lifecycle/operations parity、onboarding和remaining DoD current views | product async delivery supervisor/protocol/client；observer runner；root participant driver sender proof/private diagnostics；PingAgent contract；Host public/private evidence；active docs；gate producer/verifier | identity/ready/delivery/cross-delivery/policy-routing formal receipts completed；migration/efficiency refreshed；继续P1与required DoD |
| P1-A | Participant replace/detach | `completed_product_slice`：Host/CLI/App 已实现 vendor-neutral replace/detach；replace 在旧 binding 变更前完成新 launch spec 预验证，持久化 cleanup evidence 后以 generation CAS `+1` 切换，CAS 前失败保留旧 identity、CAS 后失败保留新 degraded identity；detach desired-first、停止新投递并保留 record/history，cleanup 歧义进入 `cleanup_pending` 而不误删 | fixed product `a2663c3709fe3579b23ca5d5003d9d86b37b0cb3` 已 push；review `20260814-225811-2wil71` / `20260814-230500-rvp1a1` 为 P0=0/P1=0/P2=0。产品实现/review无剩余阻塞；受影响formal gate/current view已随P2-B final rebuild fresh/passed | state/store/participant/protocol/client/CLI/App/driver；lifecycle/auth/reconcile/App contract tests；active docs | `ACC-PARTICIPANT-LIFECYCLE-001`、`ACC-MODEL-BINDING-001`；current/fresh |
| P1-B | App degraded/high-risk operability | `completed_product_slice`：App typed model显示 Participant degraded/cleanup_pending与stale resource lease，提供 Scenario repair、exact Participant force-stop、exact stale-lease break；四个含既有destroy的高风险operation先经App intent alert，再由Host独立复核fence/permission/effect preview并执行trusted single-use confirmation | fixed product `5ba4243ab7e315db0f51d66b293e9e41055e7fbe` 已push；review `20260814-231138-11psea` / `20260814-231800-rvp1b1` 为P0=0/P1=0/P2=0；受影响formal current view已随P2-B fresh/passed。2026-08-15新增的列表右键`scenario.force-destroy`是P2-A员工UX复合operation，superseding fixed product `651aaac7299e22301ae61c242265abaf3a0d450e`复用Host高风险链、exact binding cleanup与Workspace source boundary，不修改既有`scenario.destroy`或formal gate语义；当前待peer review/签名App实测 | Swift generated contract/models/view model/UI；Host confirmation metadata join；App/Host security tests；active docs | `ACC-RESOURCE-001`、`ACC-DESTROY-001`、`DOD-RISK-001`；既有gate保持current，新增员工路径走P2-A dogfood |
| P1-C | Preflight/permission/actionable error | `completed_product_slice`：Host/CLI/App 已接通只读 `scenario.preflight`，聚合 project、Scenario、Workspace、Participant 与按需 presentation permission 状态；platform driver 以 no-prompt probe 返回 provider-neutral observation；client/App 保留 structured error 的 category/retryable/mutation_state/repair_action，并提供受 Host authority 约束的员工修复入口 | fixed root `e3be3fb8290cfac18e3299f7f943dc3f59c0752e`、product `ef19fe65d9da8e8d9f258f696a66aeb2a315fac1` 已push；review `20260814-234623-i6xe1t` / `20260814-235500-rvp1c1` 为P0=0/P1=0/P2=0。preflight只作advisory，不阻塞Start/Open等lifecycle；headless/detached不要求presentation permission；formal current view已随P2-B fresh/passed | protocol/client/CLI/Host；root platform driver；Swift generated contract/models/view model/UI；permission/IPC/App tests；active docs | `ACC-PERM-001`、`DOD-PERMISSION-001`、`DOD-DIAGNOSTIC-001`；current/fresh |
| P1-D | Focus/topology/progress/cancel | `completed_product_slice`：root presentation driver 对 exact owned window/session/owner/job chain 执行 inspect/focus，并按 display-topology fingerprint 保存/恢复 geometry；Host/CLI/App 提供 provider-neutral Scenario topology/focus；close 通过同连接单调 progress 与独立鉴权 cancel request 支持 cooperative cancel，不暗示 rollback | fixed root `560828d1913b2f79051032068bde3f67dd0f92d3`、product `3f613ecaa60761897da4fa3f28c5f81bfae4035d` 已push；review `20260815-003555-9l8y3j` / `20260815-010500-rvp1d1` 为P0=0/P1=0/P2=0。逐Participant故障隔离，partial cancel先持久化evidence再进入degraded；formal current view已随P2-B fresh/passed | driver operations；Host server/client/store；CLI；Swift generated contract/IPC/models/view model/UI；topology/cancel/disconnect tests；active docs | `ACC-WINDOW-TOPOLOGY-001`、`DOD-WINDOW-001`、IPC acceptance；current/fresh |
| P1-E | Runtime/profile breadth | `completed_product_slice`：generic driver与inert/Codex/Claude profile保持plugin data；既有profile/schema/driver conformance 101 passed。product `92813fd0d82e8e5757ccd0816159cbe00e38278a` 在不改frozen participant record的前提下，由`participant.list`向CLI/App投影current generation、profile与immutable model binding；App exact join并对错配整页fail closed；review `20260815-005706-pnng06` / `20260815-011500-rvp1e1` 为P0=0/P1=0/P2=0 | 本slice无剩余实现/review阻塞；受影响formal current view已随P2-B fresh/passed。Hermes真实runtime conformance按2026-08-15用户裁决延后，不以本机安装状态或空壳测试作结论 | product protocol/store/CLI；Swift generated contract/models/view model/UI；root profile/schema conformance；active docs | `ACC-RUNTIME-CAPABILITY-001`、`ACC-MODEL-BINDING-001`；current/fresh |
| P2-A | Employee onboarding/distribution | `in_progress`：signed internal App、embedded Host、installer/TCC witness 与P2-B technical closeout已有；员工首次使用、日常协作/恢复指南和internal pilot/broad release checklist已进入active文档。r5已证明双Agent fresh Start、真实回应、Agent-native delivery与clean Close；Delivery selected-Scenario live refresh product `0ba384355f935edd3824a161bda9171917746aae`已review并随Host generation 24安装 | 使用generation 24从fresh r6验证Delivery无需离开Scenario即可刷新，以及双Agent exact Close/Resume同conversation/同generation；随后继续checklist的并行Scenario与异常恢复并补飞书/发布入口。Developer ID/notarization与clean-machine Gatekeeper仍未证明 | App README、`EMPLOYEE_GUIDE.md`、`P2A_EMPLOYEE_RELEASE_CHECKLIST.md`、onboarding/飞书入口、installer/release | employee dogfood；distribution-specific acceptance |
| P2-A-R | Employee dogfood continuity/collaboration remediation | `in_progress_employee_revalidation`（`user_decision` 2026-08-15 + `evidence` 2026-08-15）：terminal consume-only、自动context、owner-private exact identity与显式fallback保持有效。r3/r4修正已review；r5随后证明两个exact identity均保存，官方CLI按固定identity可加载原conversation，失败仅来自banner-dependent readiness对长历史恢复的false negative。root `50c8b61df4a332f433d5560255957681ac8df75a`改用稳定真实input prompt并继续拒绝numbered menu，review `20260815-203645-72jlmf`为P0=0/P1=0/P2=0；signed候选已安装为Host generation 24 | fresh r6完成双Agent Start、首轮binding、normal Close/Resume同conversation/同generation、context与terminal no-third-delivery。r5作为旧构建immutable degraded evidence，不手工改写或冒充acceptance；`degraded`员工友好文案为P2-A UX follow-up，已有binding mismatch继续fail closed | product participant/store validation；root contract/driver/tests；active architecture/integration/progress/employee docs | `ACC-RESUME-001`、`ACC-RUNTIME-CAPABILITY-001`、`DOD-LIFECYCLE-001`；Workspace/WIP、delivery/context既有slice不因本修正失效 |
| P2-B | Final gate/DoD closeout | `completed`：root `a22cd9b8d4807bff031ab654c36c7443e35a60c9`、product `92813fd0d82e8e5757ccd0816159cbe00e38278a`、PingAgent `c402acd968a7b95288d5eeed033fbd2ac487e5cb`；29 ACC current/evidence fresh+passed，13 non-spike DoD passed，`DOD-SPIKES` 11/11 fresh；implementation与compatibility reviews均P0/P1/P2=0 | 无technical gate/DoD阻塞；derived DoD只写current view、immutable evidence=0。P2-A distribution与Hermes真实runtime conformance保持独立边界 | registry、producer/verifier、receipt engine、active docs | 29 ACC + 13 DoD machine audit；`0600/0700`；peer reviewed |

| P2-X | Independent AI Collab repository extraction | `in_progress_validation`：private `AtomGradient/ai-collab`已建立，产品code anchor为`14e4c4038c7314b3a4d3b0c05205bf9270b57576`、EdgeStudio code anchor为`e07a71859ea21119fa0e7e9bf70a31b70f851da2`、consumer为`a619b355a1ae03ae1c48921c89ebf989f7e8eadc`；通用core/CLI/App/HostAgent/driver/PingAgent/tooling/contract/tests/docs已迁入，consumer/gate anchors已切换。产品241、root Harness1399、consumer242 passed，Swift14 executed（1 expected skip）/0 failures，PingAgent5+7 passed，isolated signed payload/embedded Host smoke通过。真实四TUI回归暴露vendor tool login shell不继承动态PATH/client env，产品已改用owner-private generation-scoped exact通信入口；该失败不计acceptance | Claude审核最终fixed vector；重建最终signed App/Host；Codex与Claude分别执行真实多Scenario/多Agent delivery/Close/Resume/no-cross/destroy完整回归。公开前另做license/header/secret/doc/notarization裁决；旧Scenario仅兼容读取，不迁移durable state | product package/App/PingAgent/docs；EdgeStudio manifest/adapter/build wrappers/gates；edge-studio-dev compatibility；all acceptance surfaces | superseding fixed-SHA cross-repo review；两套独立全量测试与真实Scenario evidence；通过后才进入employee acceptance |

## 4. Invalidation 与同步规则

以下改动发生时，相关行必须在同一 slice 更新状态、fixed SHA、verification 和下一动作：

| Change domain | Must re-check/update |
|---|---|
| Host operation/schema/capability | generated Swift contract、Python client/CLI、App surface、IPC tests、fingerprint |
| Participant identity/generation/process binding | PingAgent scoped sender、policy generation refs、delivery target/ACK、recover/replace tests |
| PingAgent CLI/transport | legacy compatibility、exact-session evidence、delivery ACK/consumption、PingAgent SHA anchor 与 rollback |
| Policy/team template | project `AGENTS.md` boundary、plan/apply digest、generation drift、App effect preview、routing gate |
| Delivery record/read model | redaction/private payload boundary、CLI/App projection、retry fence、diagnostic/gate tests |
| App workflow | Host authority/no-shell-out、structured error、high-risk confirmation、real signed-App witness |
| Runtime/profile | no product-name Host branch、startup trust、model-binding immutability、driver conformance |
| Gate producer/verifier/registry | fixed pushed SHA review、dependency freshness、affected current views、derived aggregates |

## 5. 当前执行序列

1. `P0-A`–`P0-E`均已闭合，migration/efficiency formal refresh已通过；
2. P1 lifecycle/operations parity：P1-A、P1-B、P1-C、P1-D、P1-E 均已完成产品切片；
3. P2-B一次性final fingerprint/formal DoD technical closeout已完成；P2-A-R的实现与真实Resume主故障已闭合，但员工采用流暂不继续扩张；
4. 当前先完成P2-X独立产品仓抽离、三仓consumer cutover与Codex/Claude各自完整回归；只有两边在同一fixed vector上通过后，才恢复P2-A员工采用验收；
5. Hermes真实runtime conformance继续按裁决延后。

P2-B technical closeout不等于Developer ID/notarized员工发布完成；P2-A继续负责员工first-run、onboarding与distribution-specific acceptance。

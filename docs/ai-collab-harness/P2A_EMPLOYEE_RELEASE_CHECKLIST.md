# P2-A 员工试用与发布 Checklist

> 用途：验证 AI Collab 从内部候选到员工可采用版本的 first-run、日常协作、恢复和分发体验
>
> 更新日期：2026-08-15
>
> 规则：本 checklist 不替代 `edgestudio_gates.yaml`、P2-B formal receipt 或代码 review；P2-B 已完成，P2-A 只验证员工采用和 distribution-specific 事实
>
> 来源：`user_decision`（2026-08-15，进入 P2-A 并编写员工使用文档/checklist）。具体执行项当前是 operational proposal，不新增 formal gate；扩大试用或 broad release 的最终裁决仍由用户/coordinator 作出

## 1. 使用方式

每次候选验证建立一份副本，记录：

```text
Candidate:
App version/build:
App bundle SHA-256:
Root SHA:
Product SHA:
PingAgent SHA:
macOS version/build:
Machine class:
Tester:
Date/time:
Install mode: clean install | upgrade | recovery
Signing mode: Apple Development | Developer ID
```

每项只能标记：

- `[x] PASS`：本候选、本机、本次运行有直接观测；
- `[ ] FAIL`：结果与预期不符；
- `[ ] BLOCKED`：前置条件缺失，尚未执行；
- `[ ] N/A`：本发布层明确不适用，并在备注说明原因。

不要把旧候选、另一台机器或 source test 的结果复制成当前候选 PASS。失败项保留原始事实；修复后使用新候选重新跑受影响项。

## 2. Release scope 与材料冻结

- [ ] 候选用途明确：`internal pilot` 或 `broad employee release`。
- [ ] 三仓完整 40-char SHA 已记录，均为已 push 的固定提交。
- [ ] 工作区 clean，候选不是从 moving working tree 构建。
- [ ] App bundle SHA-256、version、build 和 embedded service build identity 已记录。
- [ ] 候选包含预期的 embedded Host、integration plugin 和 PingAgent transport。
- [ ] code seal/deep verification 通过，签名 Team ID 与发布声明一致。
- [ ] 从旧版本升级时，若旧bundle seal因`__pycache__/*.pyc`失效，installer只quarantine HarnessService内owner-owned regular bytecode并在原seal恢复后继续；任一其他差异均fail closed且不改Scenario/WIP（来源：`evidence`，2026-08-15 codesign added-resource诊断）。
- [ ] 没有把 Apple Development 内部签名描述为 Developer ID/notarized 发布。
- [ ] Release notes 列出能力边界、已知问题、回退方法和支持入口。

## 3. 安装、升级与卸载

### Internal pilot 必须通过

- [ ] 从未安装状态完成安装，默认进入 `~/Applications/AI Collab.app`。
- [ ] 首次打开不要求员工在 Terminal 手工启动 Host。
- [ ] App 自动注册 current-user Host，最终显示 `Host: ready`。
- [ ] App 退出后 Host service 状态符合设计，不产生另一套孤立 Host。
- [ ] 从上一员工候选完成原子升级，Scenario/Workspace/registry 保留。
- [ ] 故障注入升级能恢复上一版本，并保留失败候选供诊断。
- [ ] unregister/uninstall 路径可执行，不误删项目代码或 Scenario WIP。

### Broad employee release 额外必须通过

- [ ] 使用有效 Developer ID Application/Installer 身份签名所有嵌套可执行内容。
- [ ] Apple notarization 成功，ticket 已 staple 到最终分发物。
- [ ] `spctl`/Gatekeeper 在一台没有开发环境和旧授权的干净机器上接受候选。
- [ ] 下载/传输后的分发物 hash 与发布记录一致。
- [ ] 支持的 macOS 版本矩阵已定义，并至少完成最小/当前版本验证。
- [ ] 发布渠道、下载权限、版本保留和紧急回退责任人明确。

## 4. First-run 与权限

- [ ] 新员工只依赖[员工使用指南](EMPLOYEE_GUIDE.md)即可完成首次使用。
- [ ] `Register Project` 打开系统目录选择器并只登记用户明确选择的目录。
- [ ] Files & Folders/TCC 提示的原因和下一动作对员工清晰。
- [ ] 受保护项目登记成功，左侧显示项目且不暴露 canonical path。
- [ ] App 退出、Host 被终止并由 launchd 恢复后，同一已登记项目仍可验证。
- [ ] Login Items/后台服务权限被拒绝时，App 给出可执行的修复说明。
- [ ] `Run Preflight` 无提示地返回 current permission/readiness truth。
- [ ] blocked preflight check 显示 provider-neutral summary 和明确 repair action。
- [ ] 网络/代理不可用时错误可理解，不表现为无限 `Bootstrapping`。
- [ ] Codex/Claude 首次 Workspace trust prompt 在指南中有安全边界说明。

## 5. 创建第一个双 Agent Scenario

- [ ] 员工登记可信项目并创建一个可长期识别的 Scenario identity。
- [ ] Scenario列表按最近活动倒序，最新Scenario位于最前。
- [ ] `Prepare Workspace` 成功，Workspace 与 canonical source 隔离。
- [ ] 默认安装中新Scenario Workspace位于`~/Documents/Scenarios`，控制面仍位于Application Support；既有旧Workspace可原地继续使用。
- [ ] 添加 `analyst`/Codex Participant 成功。
- [ ] 添加 `reviewer`/Claude Participant 成功。
- [ ] App 显示两者 exact generation、runtime profile 和 model binding。
- [ ] 选择 `Analyst + reviewer` 后，`Preview Plan` 显示两名成员和双向 route。
- [ ] 缺少成员或 generation drift 时 plan fail closed，而不是静默套用旧策略。
- [ ] `Apply Plan` 成功，policy status 显示 current。
- [ ] 两个 Participant 分别 Start，两个真实 TUI 均可见且达到 ready。
- [ ] 两个模型均能对员工输入正常回应。

## 6. Agent-native collaboration

- [ ] 两名Agent在首次员工输入前已自动获得自身identity、peers、current policy和reply规则；员工不需要解释PingAgent用法。
- [ ] analyst 从自己的 Harness-owned process chain 执行 `ai-ping reviewer`。
- [ ] send 返回紧凑 `accepted`，不等待 receiver 完成业务推理。
- [ ] reviewer exact TUI 收到 request，delivery 最终显示 consumed。
- [ ] reviewer 使用原 delivery ID 执行 `--reply-to`。
- [ ] analyst exact TUI 收到 thread-linked reply，reply 最终显示 consumed。
- [ ] Agent不以供应商原生agent discovery替代Harness peer；成功`ai-ping`后不得口头误报peer不可达。
- [ ] 正常 accepted/delivery/consumption ACK 没有重新注入 Agent 或形成 ACK 循环。
- [ ] `response/review-response/notice/done`只消费，不触发第三条receipt-only delivery或`policy.no-matching-rule`噪声。
- [ ] App 可观察 redacted thread/delivery health，但没有 Participant impersonation composer。
- [ ] 停留在当前Scenario时，Delivery列表会自动显示新send/reply/consume状态；员工不需要退出再进入Scenario触发刷新。
- [ ] receiver 未消费或 delivery degraded 时，状态和 retry eligibility 准确。
- [ ] 只有 Host 明确授权时 App 才显示并接受 `Retry`。

## 7. 并行 Scenario 隔离

- [ ] 同一项目同时运行两个 Scenario。
- [ ] 两个 Scenario 可以使用相同逻辑 Participant identity `analyst`/`reviewer`。
- [ ] 每个 Scenario 都拥有独立 Workspace、window binding 和 policy snapshot。
- [ ] A 中的 request/reply 只到达 A 的 exact Participants。
- [ ] B 中的 request/reply 只到达 B 的 exact Participants。
- [ ] App delivery read model 中没有 cross-Scenario thread、generation 或 retry health。
- [ ] 关闭 A 不停止、聚焦、修改或污染 B。
- [ ] 两个 Scenario 的 staged、unstaged 和 untracked WIP 互不影响。

## 8. 正常关闭与恢复

- [ ] `Focus & Restore` 找回 exact Scenario 窗口并恢复已知布局。
- [ ] 单个 Participant `Stop` 正常关闭其 TUI并释放 exact binding/lease。
- [ ] Scenario `Close` 显示单调进度，最终所有目标 Participant stopped。
- [ ] 忙碌关闭可以 `Cancel safely`，UI 不宣称已回滚完成步骤。
- [ ] clean close 保存 restore target，不删除 Workspace、journal 或 WIP。
- [ ] fresh Codex TUI在员工首条输入前可以ready；Harness不发送合成prompt，不因vendor conversation尚未物化而误报launch failure。
- [ ] fresh Codex/Claude Start不等待vendor lifecycle hook；dependency首次bootstrap与cache复用两条路径都能开窗、ready并正常Stop。
- [ ] 员工首条真实输入后，participant-private lifecycle hook静默固化vendor identity；App/TUI不显示无意义的“identity已收到”回复。
- [ ] 退出并重新打开 App 后项目和 Scenario 仍存在。
- [ ] Participant fault后误点`Resume`不得用empty restore plan把Scenario洗成`running`；仍有failed Participant时保留可操作的needs-attention/degraded状态与exact Recover动作。
- [ ] pre-window失败展示可重试的Recover；仅当private diagnostic证明尚未进入window-create时才轮换generation，window-create unknown/post-create仍保留cleanup pending。
- [ ] `Resume` 创建新的受管窗口/process chain，但恢复上次clean close前同一Codex/Claude conversation。
- [ ] exact Resume以恢复后持续存在的真实input prompt判定ready，不依赖会被长conversation滚出viewport的welcome banner；numbered trust/menu prompt仍必须fail closed。
- [ ] ordinary exact Resume保持Participant generation不变，App/receipt不暴露raw vendor session identity。
- [ ] exact session source/identity不匹配时fail closed，不能silent fallback到空白conversation。
- [ ] cleanup明确后App以显式警告提供`Recreate + Handoff`；只有员工确认后才建立新generation/新conversation，Workspace/WIP和Harness历史保留。
- [ ] 恢复后 generation/profile/model 展示与 Host truth exact join。
- [ ] staged、unstaged、untracked 文件及 branch 状态与关闭前一致。

## 9. 故障恢复与高风险操作

- [ ] 人为制造一个 Participant launch failure，失败状态和原因可见。
- [ ] `Recover` 创建新 Participant generation，保留旧失败证据。
- [ ] Recover 后显式 `Start` 可启动可回应的真实 TUI。
- [ ] Scenario degraded/provision failed 时 `Repair Scenario` 先展示 App intent。
- [ ] Host 对 Repair 独立复核 exact state、fence、权限和一次性确认。
- [ ] `Force Stop` 只针对 exact Harness-owned Participant process。
- [ ] `Break Lease` 只在 Host 证明 owned process 不存在且 lease stale 后可用。
- [ ] `Destroy Scenario` 先加载 exact effect preview，不能被普通 Close 误触发。
- [ ] Destroy按钮仅在fresh preview `eligible=true`时启用；否则展示exact blockers且不提交必然失败的mutation。
- [ ] 左侧列表右键历史Scenario可直接选择`Force Delete Scenario…`，不要求先Resume/Close或进入详情页加载preview。
- [ ] App警告明确说明Scenario隔离Workspace/WIP会丢失、注册项目源仓不会被删除；确认后Host只再要求一次trusted single-use confirmation。
- [ ] Force Delete只停止exact frozen binding证明属于该Scenario的Participant并释放该Scenario lease；unknown ownership、fence/WIP变化或Workspace binding不一致时fail closed且保留现场。
- [ ] running、degraded和closed历史Scenario均可走同一复合operation；既有保守`Destroy Scenario`路径及其blocker语义保持可用。
- [ ] 高风险操作取消或拒绝不会伪装为成功。
- [ ] 失败或 partial mutation 后保留 structured error、mutation state 和 repair action。
- [ ] 正常恢复不删除 Scenario、Workspace、journal、delivery history 或 WIP。

## 10. 员工可理解性

- [ ] 一名不了解 Host/mailbox/generation 内部实现的新同学可以独立完成首次协作。
- [ ] 员工不需要手填 project instance ID、generation/revision、receiver JSON 或 capability。
- [ ] 员工不需要运行 `ai-pane-register`、`ai-collab-watch` 或手工选择 mailbox。
- [ ] App 中按钮禁用时能通过 busy/state/preflight 判断下一动作。
- [ ] `Close`、`Stop`、`Recover`、`Repair`、`Force Stop`、`Destroy`、`Force Delete` 的差异可理解。
- [ ] App 不要求员工理解 vendor session ID 才能完成正常使用。
- [ ] 故障支持入口说明需要提供什么以及禁止发送什么敏感信息。
- [ ] 员工反馈中的 UX/文档问题与技术 gate failure 分开记录。

## 11. 文档与支持入口

- [ ] [员工使用指南](EMPLOYEE_GUIDE.md)与当前 UI 标签、流程一致。
- [ ] App README 的开发者构建说明与员工安装说明没有混用。
- [ ] 飞书/员工入口链接到唯一 current 使用指南和当前候选下载位置。
- [ ] Release notes 明确 internal pilot 或 broad employee release。
- [ ] 已知问题有 owner、优先级、workaround 和目标版本。
- [ ] 支持人员能从 structured error/receipt 定位问题，无需索取私钥或完整消息正文。

## 12. 不应被误设为 P2-A 阻塞项

- [ ] 没有要求重新运行已经完成且未被候选改动 invalidated 的 P2-B formal gates。
- [ ] 没有把 Hermes 真实 runtime conformance加入首发阻塞项；它按用户裁决延后。
- [ ] 没有要求 App 复制 vendor chat 或冒充 Participant 发言。
- [ ] 没有要求不声明exact-resume capability的未来runtime伪造vendor session identity；该runtime应明确使用`explicit_recreate`。
- [ ] 没有把跨机器同步、自动 commit/push/merge 或跨设备长期记忆扩进本阶段。
- [ ] 没有用任意自然语言长度、finding 数量或模型回答内容代替运行健康判断。

## 13. 发布结论

### Internal pilot 准入

形成 `can_expand_internal_pilot` 建议前，应满足：

- 第 2 节材料冻结完成；
- 第 3 节 internal pilot 安装项通过；
- 第 4–11 节所有适用的安全、first-run、协作、隔离、恢复和文档项通过；
- 未解决问题中 P0=0，P1 有明确 owner 和停止扩大范围的判定；
- 失败候选没有被覆盖或描述成成功候选。

### Broad employee release 准入

形成 `can_release_broadly` 建议前，在 internal pilot 准入基础上还应满足：

- 第 3 节 Developer ID/notarization/Gatekeeper 项全部通过；
- 支持的 macOS 版本范围有直接验证；
- 发布、升级、紧急回退和支持责任人明确；
- 最终员工入口指向已 notarized 的 exact artifact 和对应 hash。

最终记录：

```text
Verdict: can_start_internal_pilot | can_expand_internal_pilot | can_release_broadly | needs_fix
P0:
P1:
P2/follow-up:
Residual risk:
Evidence locations:
Reviewer:
Decision date:
```

# AI Collab 员工使用指南

> 适用范围：Harness v3.3 的 P2-A 内部员工试用
>
> 更新日期：2026-08-15
>
> 当前边界：内部签名 App、内嵌 Host、Codex/Claude 双 Agent 协作已经可用；Developer ID/notarized 广泛分发仍在 P2-A 发布工作中
>
> 来源：`user_decision`（2026-08-15，进入 P2-A 并编写员工使用文档/checklist）以及 `product_architecture.md` 与消费项目自己的集成文档的 current contract

AI Collab 把一个长期任务管理成独立的 **Scenario**。每个 Scenario 有自己的 Workspace、Participant、协作策略、消息记录、进程和恢复状态。员工管理的是任务房间，不需要手工管理 Host、mailbox、generation、PID 或 vendor session ID。

默认安装中新建Scenario的Workspace位于`~/Documents/Scenarios`；`~/Library/Application Support/AI Collab`只保存Harness控制面状态。升级前已经存在的旧Workspace会继续原地使用，不会自动搬迁。

## 1. 开始前

请确认：

- 使用公司提供的 AI Collab.app 候选版本，不从聊天附件或未知来源安装；
- Codex、Claude 等所需 CLI 已按公司开发环境完成登录；
- iTerm2 可正常打开；
- Git 项目来自可信来源，并且本机网络或公司代理可用；
- 项目中的未提交改动已经理解。Harness 会保留 WIP，但不会替员工 commit、push、merge、rebase 或 stash。

当前内部候选默认安装在：

```text
~/Applications/AI Collab.app
```

普通员工不需要手工启动 Python Host。App 会注册并等待内嵌的 current-user Host；左下角显示 `Host: ready` 后才能开始工作。

## 2. 十分钟首次使用

### 2.1 注册项目

1. 打开 **AI Collab**。
2. 等待左下角变为 `Host: ready`。若不是 ready，点击 `Retry`；仍失败则参见[故障处理](#8-故障处理)。
3. 点击工具栏中的 `Register Project`。
4. 在系统目录选择器中选择项目根目录。
5. macOS 如显示 Files & Folders 或目录访问提示，核对目录后允许访问。
6. 项目出现在左侧列表即表示登记成功。

`Register Project` 只登记用户明确选择的项目。App 对外使用 opaque project identity，不要求员工填写本机绝对路径。

### 2.2 创建 Scenario 和 Workspace

1. 在左侧选择刚登记的项目。
2. 在 `Scenario identity` 中输入能长期识别任务的短名称，例如 `harness-upgrade` 或 `ios-runtime-fix`。
3. 点击 `Create`。
4. 选择新 Scenario，点击 `Prepare Workspace`。
5. 等待操作结束后点击 `Run Preflight`。

Preflight 是只读检查。它会显示项目、Workspace、Participant runtime、网络和展示权限等当前状态。`ready` 表示可以继续；blocked 项应按界面给出的 repair action 处理。

不要用 `/tmp/test1` 一类名称管理真实长期任务。Scenario identity 应当在几天后仍能让团队理解它代表什么。

### 2.3 添加 Codex 和 Claude

默认 peer-review 团队使用以下 exact Participant identity：

| Participant identity | 推荐模板 | 日常职责 |
|---|---|---|
| `analyst` | Codex | 分析、实现、验证 |
| `reviewer` | Claude | 独立审核、提问、pushback |

添加步骤：

1. 在 `Participants` 区域输入 `analyst`，选择 Codex 模板，点击 `Add`。
2. 输入 `reviewer`，选择 Claude 模板，点击 `Add`。
3. 检查两行显示的 generation、runtime profile 和 model binding。
4. 在 `Collaboration policy` 中选择 `Analyst + reviewer`。
5. 点击 `Preview Plan`，确认 team 中两名 Participant 都存在，route 为 `analyst → reviewer` 和 `reviewer → analyst`，且没有 blocker。
6. 点击 `Apply Plan`。
7. 分别点击两名 Participant 的 `Start`。

每个 Agent 会打开 Harness 拥有的独立 TUI 窗口。App 是协作控制面，不复制聊天内容，也不会冒充某个 Agent 发消息。

### 2.4 处理目录信任提示

Codex 或 Claude 首次进入新的 Scenario Workspace 时，可能显示类似：

```text
Do you trust the contents of this directory?
1. Yes, continue
2. No, quit
```

当且仅当窗口中的目录属于刚才由 AI Collab 为可信公司项目创建的 Scenario Workspace 时，可以选择 `1. Yes, continue`。目录来源不明、与当前 Scenario 不符或项目本身不可信时，选择退出并检查 App 中的 project/Workspace receipt。

这是 vendor TUI 的安全确认，不代表 Harness 启动失败。

### 2.5 确认 Agent 可工作

1. 在 Codex TUI 输入一个简短问题，确认模型正常回应。
2. 在 Claude TUI 输入一个简短问题，确认模型正常回应。
3. 回到 App 点击 `Refresh`，两名 Participant 应显示健康状态；需要时使用 `Focus & Restore` 找回它们的窗口。

## 3. Agent 之间如何协作

员工通常只需要用自然语言向 Agent 描述任务和分工，例如“把当前实现交给 reviewer 审核”。Harness 启动的 Agent 在第一次用户输入前已经获得自己的 Scenario/Participant identity、同 Scenario peers、current policy和reply规则；员工不需要先解释谁是 reviewer、怎样填写 Scenario/generation或怎样调用PingAgent。

下面的命令用于维护者诊断或员工希望明确指定消息种类时参考，日常工作不要求员工手工输入；正常情况下由对应Agent在自己的TUI中执行。

例如 analyst 请求 reviewer 审核：

```bash
ai-ping reviewer --kind review-request --file review-request.md
```

reviewer 对该 delivery 回复：

```bash
ai-ping analyst \
  --kind review-response \
  --reply-to <delivery-id> \
  --file review-response.md
```

这些命令应由对应 Agent 在自己的 TUI/process chain 中执行。员工不需要填写 sender、Scenario ID、generation 或 Host 地址，也不要从 App 冒充 Participant 发送内容。

Host 持久化消息后会立即返回 `accepted`。正常 delivery/consumption ACK 只进入机器状态、App UI 和审计，不会再次注入 Agent，也不要求 Agent 浪费 token 输出“对方已经收到”。业务回复仍会进入对应 Agent 会话。

`request`、`question`、`review-request`、`pushback` 通常需要完成工作后回复；`response`、`review-response`、`notice`、`done` 默认只消费，不再回一封“收到”。若终态消息正文明确提出新的业务任务，Agent可以处理任务，但不要发送receipt-only回复。

在 App 的 `Agent deliveries` 区域可以查看：

- sender/receiver 和各自 generation；
- request 与 reply 的 thread 关系；
- queued、delivered、consumed 或 degraded 状态；
- Host 明确允许时出现的 `Retry`。

## 4. 日常操作

| 想做什么 | 使用的操作 | 结果 |
|---|---|---|
| 找回 Agent 窗口 | `Focus & Restore` | 聚焦该 Scenario 的 exact owned 窗口并恢复已知布局 |
| 暂停单个 Agent | Participant `Stop` | 正常关闭该 Participant 的 TUI，Scenario 和 WIP 保留 |
| 继续已关闭任务 | Scenario `Resume` | 为 exact Participants 新建受管窗口/process，并恢复关闭前的同一 Agent conversation |
| 正常结束当天工作 | Scenario `Close` | 协作式关闭 Participant，保存恢复目标、Workspace 和审计历史 |
| 启动失败后重建身份 | Participant `Recover`，然后 `Start` | 创建新 Participant generation，不复用失败 identity |
| 更换 Agent 配置 | `Replace` | 预验证新配置后切换到新 generation；历史记录保留 |
| 从团队移除 Agent | `Detach` | 停止新投递并保留 Participant 历史 |

`Scenario generation` 与 `Participant generation` 是两层身份。Recover 后 Participant generation 增加而 Scenario generation 不变是正常行为。

### 正常关闭

1. 确认 Agent 没有仍需保留的前台交互。
2. 点击 Scenario 的 `Close`。
3. 若 App 显示关闭进度，等待各 Participant 收敛。
4. 确实需要中止关闭流程时，使用 `Cancel safely`；它不表示自动回滚已完成的部分。
5. Scenario 显示 `closed` 后可以退出 App。

关闭 App 本身不会把 Host 当作一次性子进程杀掉。Host 由 current-user service 管理，可在 App 不运行时继续维护持久状态。

正常 `Close → Resume` 可以出现新的 iTerm窗口，但窗口中的Codex/Claude应回到关闭前的同一conversation，Participant generation保持不变。若供应商生命周期接口升级、session identity不匹配或旧conversation确实不可恢复，Harness会fail closed并显示degraded，不会悄悄打开空白conversation冒充恢复。

此时只有在App显示`Recreate + Handoff`且你确认后才继续。该操作保留Scenario Workspace/WIP、Harness journal、delivery历史以及当前identity/peer/policy提示，但会建立新Participant generation和全新的Agent conversation；旧conversation不会恢复。

## 5. 并行 Scenario

可以同时运行多个 Scenario，包括多个具有相同逻辑名称 `analyst`/`reviewer` 的团队。隔离边界由 project instance、Scenario identity、Participant generation、Workspace 和 Host policy 共同确定，而不是由窗口标题或当前目录猜测。

日常建议：

- 每个长期任务使用独立 Scenario；
- 不要让两个任务共享同一个 Scenario 只为少开窗口；
- 在 App 中通过 `Agent deliveries` 核对消息属于当前 Scenario；
- 从一个任务切换到另一个任务时使用 App 选择 Scenario 和 `Focus & Restore`；
- 不要复制另一个 Scenario 的 Harness context、receipt 或私有状态文件。

## 6. Workspace 和数据边界

Harness 正常关闭、恢复或 Participant replacement 不应删除：

- staged、unstaged 和 untracked WIP；
- Scenario/Participant journal；
- delivery/thread 历史；
- Workspace 和环境 binding；
- failure/cleanup evidence。

Harness 不会自动执行 commit、push、merge、rebase、stash 或清除 WIP。代码提交仍由员工和 Agent 按项目规则完成。

`Destroy Scenario` 与普通 `Close` 完全不同。Destroy、Force Delete、Force Stop、Break Lease 和某些 Repair 都是高风险操作：App 先展示意图，Host 还会独立复核 exact target、fence、权限和影响，并要求一次性可信确认。日常收工不要使用 Destroy 或 Force Delete。

删除时先点`Load Destroy Preview`。只有preview显示`eligible: true`，App才允许`Destroy Scenario`；若按钮仍为灰色，直接查看preview中的`blockers`，不要反复点击。Destroy成功后Scenario从当前列表注销，但审计历史按Host策略保留。

删除已经确认不再需要的历史Scenario时，可以直接在左侧列表右键该Scenario，选择`Force Delete Scenario…`。阅读App警告并确认后，继续完成Host的一次系统确认。Host会先关闭能够精确证明属于该Scenario的Agent，再删除该Scenario的隔离Workspace、lease和控制面记录；未提交的Scenario WIP会永久丢失。它不会删除或修改`Register Project`登记的原始项目目录。若Host无法证明进程/窗口或Workspace的exact ownership，操作会停止并保留现场，此时不要用Finder或Terminal手工补删。

## 7. 恢复与高风险操作

优先使用最窄的恢复操作：

1. `Refresh` 重新读取 Host truth。
2. `Run Preflight` 获取当前 blocked check 和建议操作。
3. 单个 Participant `launch_failed` 且属于普通启动失败时使用 `Recover`，然后显式 `Start`。
4. `Resume`因exact conversation恢复失败且cleanup已明确时，阅读警告后决定是否使用`Recreate + Handoff`；不要把它当普通Recover。
5. Scenario 为 `provision_failed` 或 `degraded` 时，查看 Diagnostics/Resources/Receipt，再使用 `Repair Scenario`。
6. 只有 Host 证明 exact process 无法正常停止时才考虑 `Force Stop`。
7. 只有资源明确显示 stale 且 App 提供按钮时才考虑 `Break Lease`。
8. `Destroy Scenario` 只用于明确不再需要整个任务房间的场景；优先使用详情页preview理解blocker，批量清理前不要把右键Force Delete当普通Close。

高风险操作失败后不要反复点击。保留界面中的 error category、retryable、mutation state 和 repair action，交给 Harness 维护者判断。

## 8. 故障处理

### `Harness Host is not running`

1. 等待数秒后点击左下角 `Retry`。
2. 检查系统是否提示 Login Items/后台服务权限。
3. 退出并重新打开 App。
4. 仍失败时记录 App 版本、Host 状态和发生时间，联系维护者；普通员工不要手工切换 Python 环境启动另一套 Host。

### `participant launch failed[operation.external-failure,retryable]`

这不是成功状态。先看 Preflight 和 Participant 的 degraded reason：

1. 确认对应 CLI 已安装、已登录且网络可用。
2. 处理系统或目录信任提示。
3. 点击 `Recover`，等待 Participant 进入 `stopped` 的新 generation。
4. 再点击 `Start`。

### Agent 窗口打开但模型不回应

1. 查看窗口内是否停在目录信任、登录、网络或升级提示。
2. 确认公司代理/网络可用。
3. 回到 App 查看 Preflight、Participant 状态和 Diagnostics。
4. 不要因等待模型回应而直接 Destroy Scenario。

### 消息没有业务回复

1. 在 `Agent deliveries` 中查看 delivery 是否已 `accepted`、`delivered`、`consumed` 或 `degraded`。
2. `consumed` 只证明内容已交给 exact receiver session，不保证模型已经完成业务推理。
3. 只有 App 显示 `Retry` 时才重试；不要重复发送同一业务请求制造多个 thread。
4. generation drift 时重新 `Preview Plan` 并 `Apply Plan`。

### App 按钮变灰

通常表示前一个操作仍在执行，或当前 Scenario 状态不允许该操作。等待进度完成并点击 `Refresh`。不要通过启动第二套 Host 绕过状态机。

## 9. 获取支持时提供什么

提供以下非敏感信息：

- AI Collab.app 版本/候选编号；
- project 的显示名称和 Scenario identity；
- 操作名称及发生时间；
- Participant identity、generation 和 observed state；
- App 展示的 structured error code、retryable、mutation state 和 repair action；
- Diagnostics/Receipt 中不含凭据、正文和本机私有路径的摘要。

不要发送 API key、token、证书、私钥、真实用户数据、完整消息正文或 Harness 私有 state root。

## 10. 当前不属于 P2-A 首发阻塞的能力

- Hermes 真实 runtime conformance：按用户裁决延后；
- 跨机器同步；
- App 内嵌 vendor chat；
- 要求所有未来runtime都提供exact resume；未声明该optional capability的runtime继续明确使用`explicit_recreate`；
- Harness 自动替员工 commit、push、merge 或决定产品方向。

P2-A 的目标不是增加新的 Harness authority，而是让员工稳定获得已经完成的 Scenario 隔离、恢复和 Agent 协作能力。

// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import Foundation

/// The bilingual copy catalog. Every entry carries English and Simplified
/// Chinese together, so the two languages cannot drift apart — a missing
/// translation is a missing argument and fails to compile.
///
/// Copy rules (user-approved 2026-08-20):
/// - Employee vocabulary: Scenario → Task Room / 任务房间; Participant →
///   AI Colleague / AI 同事; the workspace stays Workspace / 工作区.
/// - Human words in front, machine words only inside Technical Details.
/// - Orange means "one thing waits for you"; red means "cannot continue".
private func t(_ english: String, _ chinese: String) -> String {
    L10n.pick(english, chinese)
}

enum S {

    // MARK: Common actions

    /// The one live next step, shown as the Scenario's primary action.
    enum Common {
        static var cancel: String { t("Cancel", "取消") }
        static var retry: String { t("Retry", "重试") }
        static var delete: String { t("Delete", "删除") }
        static var close: String { t("Close", "关闭") }
        static var details: String { t("Technical Details", "技术详情") }
        static var done: String { t("Done", "完成") }
        static var notAvailable: String { t("Not available", "暂无数据") }
        static var continueToHostConfirmation: String {
            t("Continue to Host confirmation", "继续，由服务端最终确认")
        }
    }

    // MARK: App chrome

    enum Chrome {
        static var appTitle: String { t("AI Collab", "AI Collab") }
        static var registerProject: String { t("Register Project", "注册项目") }
        static func hostStatusLine(_ display: String) -> String {
            t("Host: \(display)", "服务状态：\(display)")
        }
        static var grantITermAccess: String {
            t("Grant iTerm2 Access", "授权访问 iTerm2")
        }
        static var grantITermHelp: String {
            t(
                "Let macOS ask for the Automation permission AI Collab needs "
                    + "to present agents in iTerm2 windows.",
                "让 macOS 弹出授权窗口——AI Collab 需要这项自动化权限，"
                    + "才能在 iTerm2 里展示 AI 同事的窗口。"
            )
        }
        /// Host phase display: machine tokens in, employee words out.
        static func hostPhaseDisplay(_ token: String) -> String {
            if token == "connecting" { return t("connecting…", "连接中…") }
            if token == "ready" { return t("ready", "就绪") }
            if token == "unavailable" { return t("unavailable", "不可用") }
            if token == "registration-failed" { return t("registration failed", "注册失败") }
            if token == "stale-bundle" { return t("Restart your Mac to finish updating AI Collab", "重新启动 Mac 以完成 AI Collab 更新") }
            if token.hasPrefix("starting:") {
                let label = String(token.dropFirst("starting:".count))
                return t("starting · \(Service.statusLabel(label))", "启动中 · \(Service.statusLabel(label))")
            }
            return token
        }
        static var diagnosticsHelp: String {
            t(
                "Open the Diagnostics report (also under Settings, ⌘,)",
                "打开诊断报告（也在设置里，⌘,）"
            )
        }
    }

    // MARK: Projects sidebar

    enum Projects {
        static var sectionTitle: String { t("Projects", "项目") }
        static func contractVersion(_ version: String) -> String {
            t("Contract \(version)", "契约版本 \(version)")
        }
        static var updateAvailable: String {
            t("Project configuration update available", "项目配置有更新可用")
        }
        static var needsAttention: String {
            t("Project configuration needs attention", "项目配置需要留意")
        }
        static func repositoryChanges(_ count: Int) -> String {
            t(
                "\(count) repository change\(count == 1 ? "" : "s") detected",
                "检测到 \(count) 处仓库变化"
            )
        }
        static var checkUpdates: String { t("Check Project Updates", "检查项目更新") }
        static var applyDetectedUpdate: String {
            t("Apply Detected Project Update", "应用检测到的项目更新")
        }
        static var unregister: String { t("Unregister Project…", "取消注册项目…") }
    }

    // MARK: Project registration

    enum Register {
        static var confirmTitle: String {
            t("Register this Git project?", "注册这个 Git 项目？")
        }
        static var confirmAction: String { t("Register Project", "注册项目") }
        static func confirmMessage(_ folder: String) -> String {
            t(
                "AICollab will use \(folder)'s tracked team intent when present, "
                    + "read older project contracts compatibly, or pin a built-in "
                    + "default for a fileless project. Registration never writes "
                    + "to the selected repository.",
                "AICollab 会优先使用 \(folder) 里的团队项目配置；旧版本配置会兼容读取；"
                    + "什么配置都没有也能直接注册。注册不会向你的仓库写入任何文件。"
            )
        }
    }

    // MARK: Task rooms (Scenarios)

    enum Rooms {
        static var listTitle: String { t("Task Rooms", "任务房间") }
        static var createButton: String { t("Create", "创建") }
        static var noSelection: String { t("No Task Room selected", "尚未选择任务房间") }
        static var identityPlaceholder: String { t("Room name", "房间名字") }
        static var objectivePlaceholder: String {
            t("Objective (optional)", "目标（可选）")
        }
        static var forceDelete: String {
            t("Force Delete Task Room…", "强制删除任务房间…")
        }
        /// The one row-menu / mission-bar entry point into the delete flow
        /// (review 20260903-185641-e6nznb): opens `DestroyPanel`, which loads
        /// a real preview before offering either the normal delete or, only
        /// once blocked, `forceDelete` above. Never itself force-deletes.
        static var deleteMenu: String {
            t("Delete Task Room…", "删除任务房间…")
        }
        static var selectTitle: String { t("Select a Task Room", "选择一个任务房间") }
        /// Group header for the room board's active/transitioning bucket.
        /// "Needs Attention" and "Closed" reuse existing labels
        /// (`S.NeedsAttention.sectionTitle`, `S.Status.label("closed")`); this
        /// is the one bucket with no existing equivalent string.
        static var activeGroupLabel: String { t("In Progress", "进行中") }
        static func memberCount(_ count: Int) -> String {
            t("\(count) colleague\(count == 1 ? "" : "s")", "\(count) 位同事")
        }
        static func closedSummary(_ count: Int) -> String {
            t("closed · \(memberCount(count))", "已休会 · \(memberCount(count))")
        }
        static func degradedSummary(_ count: Int) -> String {
            t("\(memberCount(count)) · needs attention", "\(memberCount(count)) · 需要处理")
        }
        static var selectDescription: String {
            t(
                "Register a project, create a task room, and work with your "
                    + "AI colleagues inside it.",
                "注册项目、创建任务房间，然后和你的 AI 同事在房间里开工。"
            )
        }
        /// The detail pane's own first-use canvas (review
        /// 20260903-194506-9xgiml P1): a registered project with zero rooms
        /// yet, distinct from "rooms exist, none selected".
        static var firstUseTitle: String {
            t("Create your first task room", "创建你的第一个任务房间")
        }
        static var firstUseBody: String {
            t(
                "Give it a name and, if you already know it, an objective — "
                    + "you can add or revise the objective later.",
                "给它起个名字，如果已经想好目标也可以先填——目标随时可以之后补充或修订。"
            )
        }
        static var onboardingRegistered: String {
            t("Project registered", "注册项目")
        }
        static func onboardingRegisteredDetail(_ project: String) -> String {
            // The `project` parameter must actually be interpolated — a
            // literal "(project)" placeholder shipped once already and
            // would show on every room regardless of its real project name.
            t(
                "\(project) is registered. Your selected checkout stays untouched.",
                "\(project) 已注册，未写入你选择的 checkout。"
            )
        }
        static var onboardingAddColleagues: String {
            t("Add AI colleagues", "添加 AI 同事")
        }
        static var onboardingAddColleaguesDetail: String {
            // The App stays vendor-neutral (test_app_is_vendor_neutral_and_
            // does_not_shell_out) — templates are a Host/config concern, not
            // a hardcoded name here.
            t(
                "Ready-made templates open each colleague in its own CLI window.",
                "内置模板开箱即用，每位同事都在自己的 CLI 窗口打开。"
            )
        }
        static var onboardingStart: String {
            t("Start working together", "全部启动，开始协作")
        }
        static var onboardingStartDetail: String {
            t(
                "Focus any colleague window to assign work; they can message one another afterward.",
                "聚焦任一窗口布置任务，之后同事之间即可互发消息。"
            )
        }
        static var onboardingPreviewTitle: String {
            t("After creation, the room board will look like this", "创建后，房间板会是这样")
        }
        static var onboardingNoColleagues: String {
            t("No colleagues yet", "还没有同事")
        }
        static var onboardingNextStep: String {
            t("Next: prepare the workspace", "下一步会是「准备工作区」")
        }
    }

    enum Objective {
        static var sectionTitle: String { t("Objective", "目标") }
        static var notSet: String { t("No objective set", "尚未设置目标") }
        static func revision(_ value: Int) -> String { t("rev \(value)", "修订 \(value)") }
        static var acceptanceCriteria: String {
            t("Acceptance criteria", "验收标准")
        }
        /// The compact mission header's one acceptance-criteria line
        /// (review 20260903-203219-kq79nn P1 visual) — label and value
        /// composed together since the punctuation between them differs
        /// per language.
        static func acceptanceLine(_ criteria: String) -> String {
            t("Acceptance criteria: \(criteria)", "验收标准：\(criteria)")
        }
        static var objectivePlaceholder: String {
            t("Revised objective", "修订后的目标")
        }
        static var acceptancePlaceholder: String {
            t("Acceptance criteria (optional)", "验收标准（可选）")
        }
        static var addRevision: String { t("Add revision", "追加修订") }
        static var edit: String { t("Edit", "编辑") }
        static var setObjective: String { t("Set objective", "设定目标") }
        static var issued: String { t("Issued", "已下发") }
        static var pendingIssuance: String {
            t("Pending issuance", "待下发")
        }
        /// A stopped/detached colleague cannot have the revision yet and is
        /// not a problem — it arrives with the next start. Neutral wording,
        /// neutral colour (the orange `pendingIssuance` is for a colleague
        /// that is running and still on the old revision).
        static var pendingIssuanceInactive: String {
            t("Issued at next start", "下次启动时下发")
        }
        static var pendingIssuanceHelp: String {
            t(
                "This revision takes effect at the next startup, resume, or compact.",
                "这一修订将在下次启动、恢复或上下文压缩时生效。"
            )
        }
    }

    // MARK: Task room detail actions

    enum Detail {
        static var refresh: String { t("Refresh", "刷新") }
        static var more: String { t("More", "更多") }
        static var prepareWorkspace: String { t("Prepare Workspace", "准备工作区") }
        static var resume: String { t("Resume", "恢复") }
        static var startAll: String { t("Start All", "全部启动") }
        static var close: String { t("Close", "休会") }
        static var startAllHelp: String {
            t(
                "Start every stopped or detached AI colleague in this room",
                "启动这个房间里所有已停止或已离开的 AI 同事"
            )
        }
    }

    // MARK: Machine state → human words

    enum Status {
        static func label(_ state: String) -> String {
            switch state {
            case "ready": return t("Ready", "已就绪")
            case "running": return t("Working", "工作中")
            case "stopped": return t("Stopped", "已停止")
            case "detached": return t("Detached", "已离开")
            case "closed": return t("Closed", "已休会")
            case "degraded": return t("Needs attention", "需要处理")
            case "provision_failed": return t("Workspace setup failed", "工作区准备失败")
            case "provisioning": return t("Preparing workspace", "正在准备工作区")
            case "opening": return t("Resuming", "正在恢复")
            case "closing": return t("Closing", "正在休会")
            case "starting": return t("Starting", "正在启动")
            case "stopping": return t("Stopping", "正在停止")
            case "recovering": return t("Recovering", "正在找回")
            case "repairing": return t("Resuming repair", "正在恢复修复")
            case "replacing": return t("Replacing", "正在替换")
            case "destroying": return t("Resuming deletion", "正在完成上次的删除")
            case "queued": return t("Queued", "排队中")
            case "pending": return t("Pending", "等待中")
            case "not_requested": return t("Not started", "未启动")
            case "delivered": return t("Delivered", "已送达")
            case "delivery_attempted": return t("Sending", "发送中")
            case "consumed": return t("Read", "已读取")
            case "recipient_deleted": return t("Recipient deleted", "收件人已删除")
            case "current": return t("Current", "最新")
            case "re-plan required": return t("Re-plan required", "需重新规划")
            case "blocked": return t("Blocked", "受阻")
            case "passed": return t("Passed", "通过")
            case "failed": return t("Failed", "失败")
            case "rejected": return t("Rejected", "被拒绝")
            case "available": return t("Available", "可用")
            case "missing": return t("Missing", "缺失")
            case "unavailable": return t("Unavailable", "不可用")
            case "granted": return t("Granted", "已授权")
            case "denied": return t("Denied", "被拒绝")
            case "not_determined": return t("Not asked yet", "尚未询问")
            case "active": return t("In use", "使用中")
            case "stale": return t("Stale", "已失效")
            case "released": return t("Released", "已释放")
            case "unknown": return t("Unknown state", "未知状态")
            default:
                // Approved rule: employee surfaces show a localized unknown;
                // the raw token stays available in technical detail.
                return t("Unknown state", "未知状态")
            }
        }

        static func degradedReason(_ reason: String) -> String {
            switch reason {
            case "participant_fault":
                return t("AI colleague needs recovery", "AI 同事需要找回")
            case "participant_restore_incomplete":
                return t("AI colleague restore is incomplete", "AI 同事恢复未完成")
            case "cleanup_pending":
                return t("Cleanup still needs attention", "仍有清理事项需要处理")
            case "launch_failed":
                return t("Launch failed", "启动失败")
            case "operation_unknown":
                return t("Previous operation needs verification", "上次操作需要核验")
            case "provision_failed":
                return t("Workspace setup failed", "工作区准备失败")
            default:
                return reason
            }
        }
    }

    // MARK: High-risk confirmations

    enum HighRisk {
        static var repairTitle: String {
            t("Request task room repair?", "请求修复这个任务房间？")
        }
        static var repairMessage: String {
            t(
                "Repair preserves the room's WIP and audit history. The Host will "
                    + "independently verify the exact degraded state, workspace "
                    + "fence, permissions, effect preview, and trusted single-use "
                    + "authorization.",
                "修复会完整保留房间里的工作内容和审计记录。服务端会独立核验当前状态、"
                    + "工作区一致性、权限与一次性授权后才执行。"
            )
        }
        static var forceStopTitle: String {
            t("Force stop this AI colleague?", "强制停止这位 AI 同事？")
        }
        static func forceStopMessage(_ id: String, _ generation: Int) -> String {
            t(
                "Force Stop may terminate the exact Harness-owned process for "
                    + "\(id) generation \(generation). The Host will independently "
                    + "revalidate its binding and require trusted single-use "
                    + "authorization.",
                "强制停止会结束 \(id)（第 \(generation) 代）对应的受管进程。"
                    + "服务端会重新核验其归属并要求一次性授权。"
            )
        }
        static var recreateTitle: String {
            t("Start a new AI conversation?", "开启一段全新的 AI 对话？")
        }
        static func recreateMessage(_ id: String, _ generation: Int) -> String {
            t(
                "Exact conversation recovery for \(id) generation \(generation) "
                    + "did not complete. Continuing creates a new colleague "
                    + "generation and a new AI conversation. Code and WIP stay "
                    + "in the room's workspace, and the new colleague receives the "
                    + "current Harness identity, peers, policy, and reply rules; "
                    + "the previous AI conversation is not restored.",
                "\(id)（第 \(generation) 代）的原对话没能完整找回。继续会创建新一代 "
                    + "AI 同事和一段全新对话。代码和工作内容仍在房间的工作区里；"
                    + "新同事会获得当前身份、伙伴与协作规则，但旧对话不会恢复。"
            )
        }
        static var breakResourceTitle: String {
            t("Request stale resource release?", "释放这个失效的资源占用？")
        }
        static func breakResourceMessage(_ resourceClass: String, _ shortID: String) -> String {
            t(
                "Break Lease releases only stale \(resourceClass) lease \(shortID) "
                    + "after the Host proves the exact owned process is absent and "
                    + "obtains trusted single-use authorization.",
                "只有当服务端证明占用它的进程确实已不存在、并获得一次性授权后，"
                    + "才会释放这个失效的 \(resourceClass) 占用（\(shortID)）。"
            )
        }
        static var destroyMessage: String {
            t(
                "The Harness Host will independently verify the current target, "
                    + "fences, permissions, effect preview, and trusted single-use "
                    + "authorization.",
                "服务端会独立核验目标、一致性、权限与一次性授权后才执行删除。"
            )
        }
        static var forceDestroyTitle: String {
            t("Force delete this task room?", "强制删除这个任务房间？")
        }
        static func forceDestroyMessage(_ id: String) -> String {
            t(
                "This permanently deletes task room \(id), its isolated Workspace "
                    + "and uncommitted room WIP. Exact Harness-owned colleague "
                    + "windows, processes, and leases are force-cleaned first. The "
                    + "registered project source is never deleted; any unproven "
                    + "ownership or changed fence stops the operation.",
                "这会永久删除任务房间 \(id)、它的隔离工作区和未提交的工作内容。"
                    + "受管的 AI 窗口、进程与资源占用会先被清理。你的原始项目仓库"
                    + "永远不会被删除；任何归属证明不通过都会立即停止操作。"
            )
        }
        static var unregisterTitle: String {
            t("Unregister this project?", "取消注册这个项目？")
        }
        static func unregisterMessage(_ key: String) -> String {
            t(
                "This removes only the registration record for \(key). The Host "
                    + "refuses while the project still owns any task room, nothing "
                    + "on disk is touched, and the project can simply be "
                    + "registered again.",
                "这只会移除 \(key) 的注册记录。项目还拥有任务房间时会被拒绝；"
                    + "磁盘上的任何文件都不会被动到，之后随时可以重新注册。"
            )
        }
    }

    // MARK: AI colleagues (participants)

    enum Colleagues {
        static var sectionTitle: String { t("AI Colleagues", "AI 同事") }
        static var identityPlaceholder: String { t("Colleague name", "同事名字") }
        static var templatePicker: String { t("Template", "模板") }
        static var advanced: String { t("Advanced", "高级") }
        static var add: String { t("Add", "添加") }
        static var emptyHint: String {
            t(
                "No AI colleagues yet. Add one below, then Start All — each works in its own terminal window.",
                "房间里还没有 AI 同事。在下面添加一位，然后「全部启动」，他们就会在各自的终端窗口里开工。"
            )
        }
        static var noneYet: String { t("No colleagues yet", "还没有同事") }
        static func attentionCount(_ count: Int) -> String {
            t("\(count) need\(count == 1 ? "s" : "") attention", "\(count) 位需要处理")
        }
        /// The team row's situation line (v2): still only sender / receiver /
        /// kind / state off a real DeliveryRecord — the kind is the Host's
        /// own message_kind token rendered as a noun, never message content.
        static func situationSent(_ receiverID: String, _ kindNoun: String, _ stateLabel: String) -> String {
            t(
                "Sent \(receiverID) a \(kindNoun) · \(stateLabel)",
                "向 \(receiverID) 发出\(kindNoun) · \(stateLabel)"
            )
        }
        static func situationReceived(_ senderID: String, _ kindNoun: String, _ stateLabel: String) -> String {
            t(
                "Received a \(kindNoun) from \(senderID) · \(stateLabel)",
                "收到 \(senderID) 的\(kindNoun) · \(stateLabel)"
            )
        }
        static var start: String { t("Start", "启动") }
        static var stop: String { t("Stop", "停止") }
        static var recover: String { t("Recover", "找回") }
        static var forceStop: String { t("Force Stop", "强制停止") }
        static var recreateHandoff: String { t("Recreate + Handoff", "重建并交接") }
        static var replaceWith: String { t("Replace with", "替换为") }
        static var repairRequired: String { t("repair required", "需要修复") }
        static func runningCount(_ count: Int) -> String {
            t("\(count) running", "\(count) 位工作中")
        }

        // Deletion (R1). Primary sentence is the user-approved copy; the
        // second line carries the safety boundary in smaller print.
        static var deleteMenu: String { t("Delete AI Colleague…", "删除 AI 同事…") }
        static func deleteConfirmTitle(_ id: String) -> String {
            t("Delete \(id)?", "删除 \(id)？")
        }
        static func deleteConfirmMessage(_ id: String) -> String {
            t(
                "\(id) will disappear from this room. Adding the same name "
                    + "later creates a brand-new colleague. Working colleagues "
                    + "must be stopped first.\n\nOnly the member entry is "
                    + "removed — the workspace and Git content stay, and the "
                    + "old conversation does not carry over.",
                "\(id) 将从这个房间消失。之后再添加同名成员是全新的一位。"
                    + "正在工作的成员需要先停止。\n\n只移出成员列表——工作区和 "
                    + "Git 内容都保留，旧对话不会带入新同事。"
            )
        }
        static func deleteActivity(_ id: String) -> String {
            t("Deleting \(id)…", "正在删除 \(id)…")
        }
        static func deleteSuccess(_ id: String) -> String {
            t("\(id) was deleted.", "已删除 \(id)。")
        }
        static func deleteRequiresStopped(_ id: String, _ stateLabel: String) -> String {
            t(
                "\(id) is \(stateLabel). Stop it first, then delete.",
                "\(id) 现在是「\(stateLabel)」。先停止它，再删除。"
            )
        }
    }

    // MARK: Delivery states (employee view)

    enum Delivery {
        static func stateLabel(_ state: String) -> String {
            switch state {
            case "queued": return t("queued", "排队中")
            case "delivery_attempted": return t("sending", "发送中")
            case "delivered": return t("delivered", "已送达")
            case "consumed": return t("read", "已读取")
            case "recipient_deleted": return t("recipient deleted", "收件人已删除")
            default: return t("unknown", "未知")
            }
        }
    }

    // MARK: Health checks (preflight)

    enum Preflight {
        static var sectionTitle: String { t("Health Checks", "健康检查") }
        static var allPassed: String {
            t("All readiness checks passed.", "全部就绪检查通过。")
        }
        static var resolveBlocked: String {
            t(
                "Resolve blocked checks before starting affected work.",
                "先处理未通过的检查，再开始相关工作。"
            )
        }
        static var runHint: String {
            t(
                "Run preflight to check permissions and readiness.",
                "运行预检，确认权限和就绪状态。"
            )
        }
        static var runButton: String { t("Run Preflight", "运行预检") }
        static func permissionStatus(_ raw: String) -> String {
            switch raw {
            case "granted": return t("granted", "已授权")
            case "denied": return t("denied", "被拒绝")
            case "undetermined": return t("not asked yet", "尚未询问")
            default: return raw
            }
        }
    }

    // MARK: Windows (topology)

    enum Topology {
        static var sectionTitle: String { t("Window Topology", "窗口拓扑") }
        static var description: String {
            t(
                "Each interactive AI colleague's exact window and saved position.",
                "每位可交互 AI 同事的窗口与保存的位置。"
            )
        }
        static var focusRestore: String { t("Focus & Restore", "聚焦并还原") }
        static var noEntries: String { t("No window entries.", "暂无窗口记录。") }
        static var noData: String {
            t(
                "No window data. Resume or refresh the task room first.",
                "暂无窗口数据。先恢复或刷新任务房间。"
            )
        }
    }

    // MARK: Collaboration policy

    enum Policy {
        static var sectionTitle: String { t("Collaboration Policy", "协作规则") }
        static func version(_ value: Int) -> String { t("Version \(value)", "版本 \(value)") }
        static var generationChanged: String {
            t("AI colleague generation changed", "AI 同事已更换代次")
        }
        static func driftRow(_ id: String, _ policyGeneration: Int, _ current: Int) -> String {
            t(
                "\(id): policy g\(policyGeneration) → current g\(current)",
                "\(id)：规则记录第 \(policyGeneration) 代 → 当前第 \(current) 代"
            )
        }
        static var noActivePolicy: String {
            t(
                "No active policy. Choose a team template below.",
                "尚未启用协作规则。在下面选择一个团队模板。"
            )
        }
        static var teamTemplate: String { t("Team template", "团队模板") }
        static var createRepairPlan: String { t("Create Repair Plan", "生成修复方案") }
        static var previewPlan: String { t("Preview Plan", "预览方案") }
        static var applyPlan: String { t("Apply Plan", "应用方案") }
        static var loadingRules: String {
            t("Loading collaboration rules", "正在载入协作规则")
        }
        static func teamLine(_ members: String) -> String {
            t("Team: \(members)", "团队：\(members)")
        }
        static var planPreview: String { t("Plan preview", "方案预览") }
        static var memberMissing: String { t("missing", "缺席") }
        static func upToAttempts(_ count: Int) -> String {
            t(" · up to \(count) attempts", " · 最多 \(count) 次")
        }
    }

    // MARK: Deliveries panel

    enum Deliveries {
        static var sectionTitle: String { t("Deliveries", "消息投递") }
        static var rawActivity: String { t("Raw activity", "原始活动") }
        static var empty: String { t("No deliveries recorded yet.", "还没有消息记录。") }
        /// v2 workbench: the delivery stream is the room's main content, not
        /// an evidence tab — this is its List section title.
        static var activityTitle: String { t("Collaboration Activity", "协作动态") }
        static func recentCount(_ shown: Int, _ total: Int) -> String {
            t("latest \(shown) of \(total)", "最近 \(shown) 条 · 共 \(total) 条")
        }
        static var activityEmptyTitle: String {
            t(
                "Handoffs, questions, reviews and done notices between colleagues appear here.",
                "同事之间的交接、提问、审核和完成通知会显示在这里。"
            )
        }
        static var activityEmptyBody: String {
            t(
                "This app does not assign the work for you — once colleagues are running, focus their terminal windows and tell them directly. Every delivery they exchange shows up in this list.",
                "这个 App 不替你布置任务——同事就位后，聚焦他们的终端窗口直接说；他们互发的每一条投递都会出现在这个列表里。"
            )
        }
        /// The Host's `message_kind` token as a noun. Only the token is
        /// interpreted — the client never sees message content. Unknown kinds
        /// show their raw token minus the `collaboration.` prefix.
        static func kindNoun(_ kind: String) -> String {
            switch kind {
            case "collaboration.review-request": return t("review request", "审核请求")
            case "collaboration.review-response": return t("review response", "审核回复")
            case "collaboration.request": return t("request", "请求")
            case "collaboration.response": return t("response", "回复")
            case "collaboration.question": return t("question", "提问")
            case "collaboration.pushback": return t("pushback", "回推")
            case "collaboration.notice": return t("notice", "通知")
            case "collaboration.done": return t("done notice", "完成通知")
            case "collaboration.message": return t("message", "消息")
            default:
                let trimmed = kind.hasPrefix("collaboration.")
                    ? String(kind.dropFirst("collaboration.".count)) : kind
                return trimmed
            }
        }
        static func lastEvent(_ event: String, _ sequence: Int) -> String {
            t("Last: \(event) · seq \(sequence)", "最近事件：\(event) · 序号 \(sequence)")
        }
    }

    enum NeedsAttention {
        static var sectionTitle: String { t("Needs attention", "需要关注") }
        static var allClear: String {
            t("No delivery needs attention.", "没有需要关注的投递。")
        }
        /// Stated over the whole collection, never over the loaded page, and
        /// one line per category. The two overlap, so they are never joined
        /// into a count of deliveries.
        static func degradedCount(_ count: Int) -> String {
            t(
                "\(count) deliver\(count == 1 ? "y is" : "ies are") degraded.",
                "有 \(count) 条投递降级。"
            )
        }
        static func retriedCount(_ count: Int) -> String {
            t(
                "\(count) deliver\(count == 1 ? "y" : "ies") needed more than one attempt.",
                "有 \(count) 条投递不止一次尝试。"
            )
        }
        static var examplesHeading: String {
            t("Examples:", "示例：")
        }
        static func noExamplesOnPage(_ limit: Int) -> String {
            t(
                "None of them are in the \(limit) most recent deliveries.",
                "它们都不在最近 \(limit) 条投递里。"
            )
        }
        static var unavailable: String {
            t(
                "Delivery totals are unavailable — attention cannot be assessed.",
                "投递总量不可用——无法判断是否需要关注。"
            )
        }
        static func reason(_ reason: DeliveryAttentionRecord.Reason) -> String {
            switch reason {
            case let .degraded(detail):
                return t("Degraded: \(detail)", "投递降级：\(detail)")
            case let .repeatedAttempt(attempt):
                return t(
                    "Delivery required \(attempt) attempts.",
                    "投递进行了 \(attempt) 次尝试。"
                )
            }
        }
    }

    /// Labels for the right-column stage timeline (review 20260903-201119-
    /// r9tf2j: the Artifact's progress panel leads with a lifecycle
    /// timeline, not a bare tile grid). Four stages derived from
    /// `observedState` + whether any colleague is running yet — a rendering
    /// of state already on the Scenario/Participant records, not a new
    /// concept the Host tracks.
    enum Stage {
        static var setup: String { t("Create · Prepare Workspace", "创建 · 准备工作区") }
        static var staffing: String { t("Colleagues Ready", "同事就位") }
        static var running: String { t("Running", "运行中") }
        static var runningAttention: String { t("Running · Needs Attention", "运行中 · 需要关注") }
        static var closed: String { t("Closed", "休会") }
    }

    enum CollaborationHealth {
        static var loading: String { t("Loading health data…", "正在加载健康数据…") }
        static var teamReady: String { t("Team ready", "团队就绪") }
        static var requestsClosed: String { t("Requests closed", "请求闭环") }
        static var endToEndEvidence: String {
            t("End-to-end evidence", "端到端证据")
        }
        static var firstAttemptDelivery: String {
            t("First-attempt delivery", "首次投递")
        }
        static var degraded: String { t("Degraded", "降级") }
    }

    enum DeliveryDistribution {
        /// Evidence & Diagnostics nav row label (review 20260903-194506-
        /// 9xgiml P1 visual): the two distribution charts moved out of the
        /// first viewport into the drawer's own tab.
        static var tabTitle: String { t("Analytics", "统计") }
        static var finalState: String { t("Delivery final state", "投递最终状态") }
        static var messageKind: String { t("Message kind", "消息类型") }
        static var noSettled: String {
            t("No settled deliveries yet.", "还没有已停歇的投递。")
        }
        static var consumptionAcknowledged: String {
            t("Consumption ack stored", "consumption ack 已落库")
        }
        static var repliedWithoutConsumptionAck: String {
            t("Ack not stored, but direct reply exists", "ack 未落库，但已有直接回复")
        }
        static var noConsumptionAckOrReply: String {
            t("Ack not stored and no reply", "ack 未落库，且无回复")
        }
        static var recipientDeleted: String { t("Recipient deleted", "收件人已删除") }
        static func kind(_ value: String) -> String {
            switch value {
            case "collaboration.review-request": return "review-request"
            case "collaboration.review-response": return "review-response"
            case "collaboration.notice": return "notice"
            case "collaboration.pushback": return "pushback"
            case "collaboration.response": return "response"
            case "collaboration.question": return "question"
            case "collaboration.request": return "request"
            case "collaboration.done": return "done"
            case "collaboration.message": return "generic msg"
            default: return value.replacingOccurrences(of: "collaboration.", with: "")
            }
        }
    }

    // MARK: Inspector

    enum Inspector {
        static var sectionTitle: String { t("Inspector", "检查器") }
        static var resources: String { t("Resources", "资源") }
        static var policy: String { t("Policy", "规则") }
        static var receipt: String { t("Receipt", "凭据") }
        static var resume: String { t("Resume", "恢复记录") }
    }

    // MARK: High-risk actions

    enum Risk {
        static var sectionTitle: String { t("High-risk Actions", "高风险操作") }
        static var hostConfirmNote: String {
            t(
                "The Host presents its trusted native single-use confirmation; "
                    + "this App cannot bypass it.",
                "最终确认由服务端弹出的一次性原生窗口完成；本应用无法绕过。"
            )
        }
        static var repairScenario: String { t("Repair Task Room", "修复任务房间") }
        static var destroyScenario: String { t("Delete Task Room", "删除任务房间") }
        static func destroyPanelTitle(_ id: String) -> String {
            t("Delete “\(id)”", "删除「\(id)」")
        }
        static var destroyPreviewOK: String {
            t("No blockers — this task room can be deleted.", "没有阻塞条件——可以删除这个任务房间。")
        }
        static var destroyPreviewLoading: String {
            t("Loading destroy preview…", "正在加载删除预览…")
        }
        /// The panel's target (id + generation) no longer matches what is
        /// actually selected — recreated under the same name, or selection
        /// changed, while the panel was open. Neither delete action is
        /// offered in this phase; only Retry.
        static var destroyPanelStale: String {
            t(
                "This task room changed while the panel was open. Retry to check again.",
                "面板打开期间这个任务房间发生了变化，请重试以重新核对。"
            )
        }
        static func destroyPreviewBlocked(_ blockers: [String]) -> String {
            let detail = blockers.isEmpty
                ? t("the Host reported unresolved blockers", "后台服务报告仍有阻塞条件")
                : blockers.joined(separator: ", ")
            return t(
                "Destroy preview is blocked: \(detail). Use Force Delete only after "
                    + "confirming the task room can be removed.",
                "删除预览受阻：\(detail)。确认可以移除这个任务房间后，再使用强制删除。"
            )
        }
        static func staleLease(_ resourceClass: String) -> String {
            t("Stale \(resourceClass) lease", "失效的 \(resourceClass) 占用")
        }
        static var staleDefault: String { t("stale", "失效") }
        static var breakLease: String { t("Break Lease", "释放占用") }
    }

    // MARK: Error banner & overlays

    enum Banner {
        static func machineLine(
            _ code: String, _ category: String, _ mutationState: String, retryable: Bool
        ) -> String {
            t(
                "\(code) · \(category) · mutation \(mutationState)"
                    + (retryable ? " · retryable" : ""),
                "\(code) · \(category) · 变更 \(mutationState)"
                    + (retryable ? " · 可重试" : "")
            )
        }
        static func recommended(_ label: String) -> String {
            t("Recommended: \(label)", "建议：\(label)")
        }
        static var reviewRepair: String { t("Review Repair", "查看修复") }
        static var cancelSafely: String { t("Cancel safely", "安全取消") }
    }

    // MARK: Diagnostics window

    enum Diagnostics {
        static var about: String { t("About", "关于") }
        static var appVersion: String { t("App version", "App 版本") }
        static var harnessContract: String { t("Harness contract", "Harness 契约") }
        static var host: String { t("Host", "服务") }
        static var machineReadiness: String { t("Machine Readiness", "本机就绪") }
        static var noReport: String {
            t(
                "No report yet. The Host may still be starting — use Refresh below.",
                "还没有报告。服务可能仍在启动——用下方的刷新。"
            )
        }
        static var automationPermission: String { t("Automation Permission", "自动化权限") }
        static var itermControl: String { t("iTerm2 control", "iTerm2 控制") }
        static var requiredBefore: String {
            t(
                "Required before agents can be presented",
                "展示 AI 同事窗口前必须先授权"
            )
        }
        static var requestPermission: String { t("Request Permission", "请求权限") }
        static func installHint(_ remediation: String) -> String {
            t(
                "Install it on this Mac, then Refresh · \(remediation)",
                "先在这台 Mac 上安装它，然后刷新 · \(remediation)"
            )
        }
    }

    // MARK: Operation messages (refusals, activities, successes)

    enum Msg {
        // Refusals — why a request could not even be attempted.
        static var selectRoomFirst: String { t("Select a task room first.", "先选择一个任务房间。") }
        static var selectProjectFirst: String { t("Select a project first.", "先选择一个项目。") }
        static var registerOrSelectProject: String {
            t("Register or select a project first.", "先注册或选择一个项目。")
        }
        static var nameTheRoom: String { t("Give the task room a name.", "给任务房间起个名字。") }
        static var objectiveRequired: String {
            t("Enter the revised objective.", "请输入修订后的目标。")
        }
        static func roomNameTaken(_ id: String) -> String {
            t("This project already has a task room named “\(id)”.", "这个项目已有名为「\(id)」的任务房间。")
        }
        static var nothingStartable: String {
            t(
                "No AI colleague is startable — every one is already working or needs repair.",
                "没有可启动的 AI 同事——都在工作中或需要修复。"
            )
        }
        static func startSummary(_ started: Int, _ failed: Int, _ skipped: Int) -> String {
            t(
                "Started \(started), \(failed) failed"
                    + (skipped > 0 ? ", \(skipped) skipped" : "")
                    + ". Use the colleague rows to repair — or, if the room itself "
                    + "needs attention, Resume or Repair it first.",
                "已启动 \(started) 位，\(failed) 位失败"
                    + (skipped > 0 ? "，跳过 \(skipped) 位" : "")
                    + "。请在同事行里逐个修复；若房间本身需要处理，先恢复或修复房间。"
            )
        }
        static var chooseTemplate: String {
            t("Choose a template for the new colleague.", "先为新同事选择一个模板。")
        }
        static var nameTheColleague: String { t("Give the colleague a name.", "给同事起个名字。") }
        static func colleagueNameTaken(_ id: String) -> String {
            t(
                "This room already has a colleague named “\(id)”.",
                "这个房间已有名为「\(id)」的同事。"
            )
        }
        static func onlyStoppedCanStart(_ id: String, _ stateLabel: String) -> String {
            t(
                "\(id) is “\(stateLabel)”; only a stopped colleague can be started.",
                "\(id) 现在是「\(stateLabel)」；只有已停止的同事可以启动。"
            )
        }
        static func nothingToStop(_ id: String, _ stateLabel: String) -> String {
            t(
                "\(id) is “\(stateLabel)”; there is nothing to stop.",
                "\(id) 现在是「\(stateLabel)」；没有可停止的进程。"
            )
        }
        static func nothingToRecover(_ id: String) -> String {
            t("\(id) has nothing to recover.", "\(id) 没有需要找回的内容。")
        }
        static var noColleaguesNeedRecovery: String {
            t("No AI colleagues need recovery.", "没有需要找回的 AI 同事。")
        }
        static func noProcessToForceStop(_ id: String) -> String {
            t(
                "\(id) has no Harness-owned process to force stop.",
                "\(id) 没有受管进程可以强制停止。"
            )
        }
        static func notAwaitingRecreate(_ id: String) -> String {
            t(
                "\(id) is not waiting on a failed conversation recovery.",
                "\(id) 并不处于「对话找回失败」状态。"
            )
        }
        static func noTemplateForRuntime(_ id: String, _ runtime: String) -> String {
            t(
                "No installed template matches \(id)'s runtime (\(runtime)), so it cannot be recreated.",
                "没有安装与 \(id) 的运行环境（\(runtime)）匹配的模板，无法重建。"
            )
        }
        static var runtimeDriverDefault: String { t("driver default", "默认驱动") }
        static var leaseNotStale: String {
            t("This lease is not stale, so it will not be released.", "这个占用并未失效，不会被释放。")
        }
        static var loadPreviewFirst: String {
            t(
                "Load the destroy preview first so the exact effect is known.",
                "先加载删除预览，确认会发生什么。"
            )
        }
        static func destroyPreviewBlocked(_ blockers: [String]) -> String {
            let detail = blockers.isEmpty
                ? t("the Host reported unresolved blockers", "后台服务报告仍有阻塞条件")
                : blockers.joined(separator: ", ")
            return t(
                "The destroy preview is blocked: \(detail). Review the preview or "
                    + "use Force Delete from High-risk Actions.",
                "删除预览受阻：\(detail)。请查看预览，或在高风险操作中使用强制删除。"
            )
        }
        static var chooseTeamTemplateToPreview: String {
            t("Choose a team template to preview.", "先选择要预览的团队模板。")
        }
        static var chooseTeamTemplateFirst: String {
            t("Choose a team template first.", "先选择一个团队模板。")
        }
        static var previewBeforeApply: String {
            t("Preview the plan before applying it.", "应用前先预览方案。")
        }
        static func planForDifferentTemplate(_ name: String) -> String {
            t(
                "The previewed plan is for a different template. Preview \(name) again.",
                "已预览的方案属于另一个模板。请重新预览 \(name)。"
            )
        }
        static var planBlocked: String {
            t(
                "This plan is blocked. Resolve the listed requirements, then preview again.",
                "方案受阻。先解决列出的条件，再重新预览。"
            )
        }
        static var planStale: String {
            t(
                "The room changed after this plan was previewed. Preview it again to pick up the current state.",
                "预览之后房间发生了变化。请重新预览以获取当前状态。"
            )
        }
        static var noMoreDeliveries: String {
            t("There are no further delivery records to load.", "没有更多消息记录了。")
        }
        static var deliveryNotRetryable: String {
            t("This delivery is not eligible for a retry.", "这条消息不符合重试条件。")
        }

        // Activities — what is happening right now.
        static func registering(_ name: String) -> String { t("Registering \(name)…", "正在注册 \(name)…") }
        static func unregistering(_ key: String) -> String { t("Unregistering \(key)…", "正在取消注册 \(key)…") }
        static var applyingUpdate: String { t("Applying project update…", "正在应用项目更新…") }
        static var focusingWindows: String {
            t("Focusing and restoring room windows…", "正在聚焦并还原房间窗口…")
        }
        static func creatingRoom(_ id: String) -> String { t("Creating task room \(id)…", "正在创建任务房间 \(id)…") }
        static var updatingObjective: String {
            t("Adding an objective revision…", "正在追加目标修订…")
        }
        static var planningWorkspace: String {
            t("Preparing the isolated workspace…", "正在准备隔离工作区…")
        }
        static func resuming(_ id: String) -> String {
            t(
                "Resuming \(id)… restoring each colleague's previous session.",
                "正在恢复 \(id)……逐个还原同事之前的会话。"
            )
        }
        static func closing(_ id: String) -> String { t("Closing \(id) safely…", "正在安全休会 \(id)…") }
        static func startingCount(_ count: Int) -> String {
            t("Starting \(count) colleague(s)…", "正在启动 \(count) 位同事…")
        }
        static var repairingRoom: String {
            t("Repairing the room… WIP and history are preserved.", "正在修复房间……工作内容和历史全部保留。")
        }
        static func adding(_ id: String) -> String { t("Adding \(id)…", "正在添加 \(id)…") }
        static func starting(_ id: String) -> String { t("Starting \(id)…", "正在启动 \(id)…") }
        static func stopping(_ id: String) -> String { t("Stopping \(id)…", "正在停止 \(id)…") }
        static func recovering(_ id: String) -> String { t("Recovering \(id)…", "正在找回 \(id)…") }
        static func forceStopping(_ id: String) -> String { t("Force stopping \(id)…", "正在强制停止 \(id)…") }
        static func replacing(_ id: String, _ template: String) -> String {
            t("Replacing \(id) with \(template)…", "正在把 \(id) 替换为 \(template)…")
        }
        static func recreating(_ id: String) -> String {
            t("Recreating \(id) with a new conversation…", "正在为 \(id) 重建全新对话…")
        }
        static func releasingLease(_ resourceClass: String) -> String {
            t("Releasing the stale \(resourceClass) lease…", "正在释放失效的 \(resourceClass) 占用…")
        }
        static func deletingRoom(_ id: String) -> String { t("Deleting \(id)…", "正在删除 \(id)…") }
        static func forceDeletingRoom(_ id: String) -> String { t("Force deleting \(id)…", "正在强制删除 \(id)…") }
        static func previewingPlan(_ name: String) -> String { t("Previewing the \(name) plan…", "正在预览 \(name) 方案…") }
        static func applyingPlan(_ name: String) -> String { t("Applying the \(name) plan…", "正在应用 \(name) 方案…") }
        static func retryingDelivery(_ shortID: String) -> String {
            t("Retrying delivery \(shortID)…", "正在重试消息 \(shortID)…")
        }

        // Successes — what just finished.
        static func registered(_ name: String) -> String { t("Registered \(name).", "已注册 \(name)。") }
        static func unregistered(_ key: String) -> String { t("Unregistered \(key).", "已取消注册 \(key)。") }
        static var updateApplied: String { t("Project update applied", "项目更新已应用") }
        static var windowsRestored: String { t("Restored the room windows.", "已还原房间窗口。") }
        static func createdRoom(_ id: String) -> String { t("Created task room \(id).", "已创建任务房间 \(id)。") }
        static var objectiveUpdated: String {
            t("Objective revision added.", "已追加目标修订。")
        }
        static var workspaceReady: String { t("Workspace is ready.", "工作区已就绪。") }
        static var itermPythonAPICommandCopied: String {
            t(
                "Copied iTerm2 Python API setup command. Paste it into Terminal.app "
                    + "if Settings is not available, then run preflight again.",
                "已复制 iTerm2 Python API 设置命令。如果无法从设置里开启，请粘贴到 Terminal.app 执行，"
                    + "然后重新运行预检。"
            )
        }
        static var restartIterm2Required: String {
            t(
                "Restart iTerm2, then run preflight again.",
                "重启 iTerm2，然后重新运行预检。"
            )
        }
        static func resumed(_ id: String) -> String { t("Resumed \(id).", "已恢复 \(id)。") }
        static func closed(_ id: String) -> String { t("Closed \(id).", "\(id) 已休会。") }
        static var repairFinished: String { t("Repair finished.", "修复完成。") }
        static func added(_ id: String) -> String { t("Added \(id).", "已添加 \(id)。") }
        static func isRunning(_ id: String) -> String { t("\(id) is running.", "\(id) 已在工作中。") }
        static func isStopped(_ id: String) -> String { t("\(id) is stopped.", "\(id) 已停止。") }
        static func recovered(_ id: String) -> String { t("Recovered \(id).", "已找回 \(id)。") }
        static func forceStopped(_ id: String) -> String { t("Force stopped \(id).", "已强制停止 \(id)。") }
        static func replaced(_ id: String, _ template: String) -> String {
            t("Replaced \(id) with \(template).", "已把 \(id) 替换为 \(template)。")
        }
        static func newConversation(_ id: String) -> String {
            t("\(id) now has a new AI conversation.", "\(id) 已开始全新对话。")
        }
        static func leaseReleased(_ resourceClass: String) -> String {
            t("Released the stale \(resourceClass) lease.", "已释放失效的 \(resourceClass) 占用。")
        }
        static func deletedRoom(_ id: String) -> String { t("Deleted \(id).", "已删除 \(id)。") }
        static func forceDeletedRoom(_ id: String) -> String {
            t("Deleted \(id) and its isolated Workspace.", "已删除 \(id) 及其隔离工作区。")
        }
        static func policyApplied(_ name: String) -> String {
            t("Applied the \(name) policy.", "已应用 \(name) 协作规则。")
        }
        static var deliveryRetryAccepted: String { t("Delivery retry accepted.", "消息重试已受理。") }

        static var cloningRepos: String {
            t(
                "Cloning repositories into the isolated Workspace… "
                    + "This is the long step and can take a few minutes.",
                "正在把仓库克隆进隔离工作区……这是最耗时的一步，可能需要几分钟。"
            )
        }
        static var everyoneAlreadyWorking: String {
            t("Every colleague is already working.", "所有同事都已在工作中。")
        }
        static func startAllSummary(_ started: Int, _ alreadyRunning: Int, _ skipped: Int) -> String {
            var en: [String] = []
            var zh: [String] = []
            if started > 0 { en.append("started \(started)"); zh.append("已启动 \(started) 位") }
            if alreadyRunning > 0 {
                en.append("\(alreadyRunning) already working"); zh.append("\(alreadyRunning) 位已在工作")
            }
            if skipped > 0 { en.append("\(skipped) skipped"); zh.append("跳过 \(skipped) 位") }
            return t(
                "Start All: " + en.joined(separator: ", ") + ".",
                "全部启动：" + zh.joined(separator: "，") + "。"
            )
        }
        static var cancellationAccepted: String {
            t(
                "Cancellation accepted · finishing the current safe boundary",
                "已受理取消 · 正在收尾到安全边界"
            )
        }
        static var anotherOperation: String { t("Another operation", "另一项操作") }
        static func busy(_ current: String?) -> String {
            t(
                "\(current ?? anotherOperation) is still running. Wait for it to finish, then try again.",
                "「\(current ?? anotherOperation)」仍在进行中。等它完成后再试。"
            )
        }
        static func progressLine(_ stateLabel: String, _ units: String, _ participant: String?) -> String {
            "\(stateLabel) · \(units)" + (participant.map { " · \($0)" } ?? "")
        }
    }

    // MARK: Policy status sentences

    enum PolicyNote {
        static var previewBeforeApply: String {
            t("Preview the selected template before applying it.", "应用前先预览所选模板。")
        }
        static var planReady: String {
            t("Plan is current and ready for explicit apply.", "方案已就绪，可显式应用。")
        }
        static var planBlocked: String {
            t(
                "Plan is blocked. Resolve the listed team requirements and plan again.",
                "方案受阻。先解决列出的团队条件，再重新预览。"
            )
        }
        static var applied: String {
            t(
                "Policy applied. Your AI colleagues can now message each other.",
                "协作规则已应用。AI 同事之间现在可以互发消息。"
            )
        }
        static var noActive: String {
            t(
                "No active policy. Preview a team template to continue.",
                "尚未启用协作规则。预览一个团队模板以继续。"
            )
        }
        static var unavailable: String {
            t(
                "Collaboration policy status is unavailable. Refresh before continuing.",
                "暂时无法读取协作规则状态。请刷新后再继续。"
            )
        }
        static var replanRequired: String {
            t(
                "Colleague generations changed. Create and explicitly apply a repair plan.",
                "AI 同事代次已变化。请生成并显式应用修复方案。"
            )
        }
        static var activeMatches: String {
            t(
                "Active policy matches the current colleague generations.",
                "当前协作规则与同事代次一致。"
            )
        }
    }

    // MARK: Delivery status sentences

    enum DeliveryNote {
        static var none: String {
            t("No message has been recorded for this room yet.", "这个房间还没有消息记录。")
        }
        static func showing(_ count: Int, _ total: Int) -> String {
            t("Showing \(count) of \(total) message records.", "显示 \(count)/\(total) 条消息记录。")
        }
        static func unavailable(_ raw: String) -> String {
            t("Delivery health is unavailable. \(raw)", "消息投递状况不可用。\(raw)")
        }
        static func liveRefreshUnavailable(_ raw: String) -> String {
            t("Live delivery refresh is temporarily unavailable. \(raw)", "消息实时刷新暂不可用。\(raw)")
        }
    }

    // MARK: Local IPC errors (App-generated; Host raw evidence stays verbatim)

    enum IPC {
        static var invalidStateRoot: String {
            t(
                "Harness state directory is unavailable or not owner-private.",
                "服务状态目录不可用，或不是仅限本人访问。"
            )
        }
        static var invalidProjectDirectory: String {
            t("The selected project must be a readable directory.", "所选项目必须是可读取的文件夹。")
        }
        static var capabilityUnavailable: String {
            t("Harness owner capability is unavailable or invalid.", "本人访问凭据不可用或无效。")
        }
        static var hostUnavailable: String {
            t("Harness Host is not running.", "后台服务未在运行。")
        }
        static var operationTimedOut: String {
            t(
                "Harness operation timed out. The Host may still be finishing it; refresh before retrying.",
                "操作超时。后台服务可能仍在收尾；先刷新再重试。"
            )
        }
        static var invalidReply: String {
            t("Harness Host returned an invalid reply.", "后台服务返回了无效应答。")
        }
        static var operationFailedFallback: String {
            t("Harness operation failed.", "操作失败。")
        }
        static var contractMismatch: String {
            t(
                "This App and Harness Host use different typed contracts.",
                "App 与后台服务的版本契约不一致。"
            )
        }
    }

    // MARK: Host service registration errors and status labels

    enum Service {
        static var approvalRequired: String {
            t(
                "Harness Host requires approval in System Settings → General → Login Items.",
                "需要在 系统设置 → 通用 → 登录项 中批准后台服务。"
            )
        }
        static func registrationFailed(_ domain: String, _ code: Int, _ detail: String) -> String {
            t(
                "Harness Host registration failed (\(domain) \(code)): \(detail)",
                "后台服务注册失败（\(domain) \(code)）：\(detail)"
            )
        }
        static func unregisterFailed(_ domain: String, _ code: Int, _ detail: String) -> String {
            t(
                "Harness Host re-registration could not release the previous "
                    + "registration (\(domain) \(code)): \(detail)",
                "后台服务重注册时无法释放旧注册（\(domain) \(code)）：\(detail)"
            )
        }
        static func serviceUnresolved(_ statusLabel: String) -> String {
            t(
                "macOS Service Management could not resolve the Harness Host "
                    + "service after registration (status: \(statusLabel)). Make sure "
                    + "the App runs from /Applications, then quit and reopen it.",
                "注册后 macOS 服务管理仍未能解析后台服务（状态：\(statusLabel)）。"
                    + "请确认 App 从「应用程序」文件夹运行，然后退出并重新打开。"
            )
        }
        static var buildIdentityMissing: String {
            t(
                "The signed App does not contain its Harness service build identity.",
                "签名 App 缺少后台服务的构建身份标识。"
            )
        }
        static var registrationStateUnavailable: String {
            t(
                "Harness Host registration state could not be stored securely.",
                "后台服务的注册状态无法安全保存。"
            )
        }
        static func statusLabel(_ token: String) -> String {
            switch token {
            case "enabled": return t("enabled", "已启用")
            case "approval required": return t("approval required", "待批准")
            case "not registered": return t("not registered", "未注册")
            case "not found": return t("not found", "未找到")
            default:
                // Unknown raw values stay out of employee sentences; the raw
                // token remains available in technical detail and logs.
                return t("unknown", "未知")
            }
        }
    }

    // MARK: Room headline

    enum Headline {
        static var noColleagues: String { t("no colleagues yet", "还没有同事") }
        static func noneRunning(_ people: Int) -> String {
            t(
                people == 1 ? "1 colleague, none working" : "\(people) colleagues, none working",
                "\(people) 位同事，均未工作"
            )
        }
        static func allRunning(_ people: Int) -> String {
            t(
                people == 1 ? "1 colleague working" : "all \(people) colleagues working",
                people == 1 ? "1 位同事工作中" : "\(people) 位同事全部工作中"
            )
        }
        static func someRunning(_ running: Int, _ people: Int) -> String {
            t("\(running) of \(people) colleagues working", "\(people) 位同事中 \(running) 位工作中")
        }
    }

    // MARK: Inspector default prompts

    enum Defaults {
        static var policy: String {
            t("Select a task room to inspect its collaboration policy.", "选择任务房间后查看其协作规则。")
        }
        static var delivery: String {
            t("Select a task room to inspect delivery health.", "选择任务房间后查看消息投递状况。")
        }
        static var diagnostics: String {
            t("Select a task room to inspect diagnostics.", "选择任务房间后查看诊断。")
        }
        static var resources: String {
            t("Select a task room to inspect resources.", "选择任务房间后查看资源。")
        }
        static var policyText: String {
            t("Select a task room to inspect policy.", "选择任务房间后查看规则。")
        }
        static var receipt: String {
            t("A provision receipt will appear here.", "准备工作区的凭据会显示在这里。")
        }
        static var resume: String {
            t(
                "Resume a closed task room to restore its previous running colleagues.",
                "恢复已休会的任务房间，可还原之前工作中的同事。"
            )
        }
    }

    // MARK: Repair action labels

    enum Repair {
        static func label(_ action: String) -> String {
            switch action {
            case "host.retry": return t("Retry Host", "重试服务连接")
            case "project.register": return t("Register Project Again", "重新注册项目")
            case "scenario.refresh": return t("Refresh Task Room", "刷新任务房间")
            case "scenario.preflight": return t("Run Preflight Again", "重新运行预检")
            case "workspace.prepare": return t("Prepare Workspace", "准备工作区")
            case "git.authenticate":
                return t("Sign in to Git, then prepare again", "先登录 Git，再重新准备")
            case "git.fetch-full-history":
                return t("Fetch complete Git history", "补全 Git 完整历史")
            case "git.materialize-full-clone":
                return t("Use a complete standalone Git clone", "改用完整独立的 Git 克隆")
            case "project.resolve-branch":
                return t("Correct the declared repository branch", "修正声明的仓库分支")
            case "project.resolve-remote":
                return t("Correct repository access or remote", "修正仓库访问或远端地址")
            case "project.resolve-origin":
                return t("Align checkout origin with team intent", "让本地仓库的远端与团队配置一致")
            case "project.fix-configuration":
                return t("Correct project configuration, then check again", "修正项目配置后再检查一次")
            case "project.reconcile": return t("Check project updates again", "重新检查项目更新")
            case "disk.free-space":
                return t("Free disk space, then prepare again", "清理磁盘空间后重新准备")
            case "scenario.open": return t("Resume Task Room", "恢复任务房间")
            case "participant.recover": return t("Recover AI Colleague", "找回 AI 同事")
            case "scenario.repair": return t("Use Repair Task Room Below", "使用下方的修复任务房间")
            case "scenario.force-destroy": return t("Use Force Delete", "使用强制删除")
            case "system-settings.automation":
                return t("Open Automation Settings", "打开自动化设置")
            case "presentation.permission-request":
                return t("Request Permission", "请求权限")
            case "iterm-presentation.launch-target": return t("Open iTerm2", "打开 iTerm2")
            case "participant.driver-configure":
                return t("Configure Presentation Driver", "配置展示驱动")
            case "host.update":
                return t("Update or Reinstall AI Collab", "更新或重装 AI Collab")
            case "iterm-presentation.enable-python-api":
                return t("Copy iTerm2 API Setup", "复制 iTerm2 API 设置命令")
            case "iterm-presentation.restart-after-python-api":
                return t("Restart iTerm2", "重启 iTerm2")
            case "iterm-presentation.reset-private-api-socket":
                return t("Reset iTerm2 API Socket", "重置 iTerm2 API Socket")
            case "iterm-presentation.remove-authentication-bypass":
                return t("Restore Authenticated Presentation API", "恢复带认证的展示 API")
            default: return t("Follow \(action)", "按 \(action) 处理")
            }
        }

        static func detail(_ action: String) -> String? {
            switch action {
            case "system-settings.automation":
                return t(
                    "Open System Settings and allow AI Collab to control iTerm2.",
                    "打开系统设置，允许 AI Collab 控制 iTerm2。"
                )
            case "presentation.permission-request":
                return t(
                    "Ask macOS to show the Automation permission prompt for iTerm2.",
                    "让 macOS 弹出 iTerm2 自动化权限请求。"
                )
            case "iterm-presentation.launch-target":
                return t(
                    "Open iTerm2, then run preflight again.",
                    "打开 iTerm2，然后重新运行预检。"
                )
            case "scenario.force-destroy":
                return t(
                    "Use Force Delete from the task room menu to finish removing this room.",
                    "从任务房间菜单使用强制删除，完成删除这个房间。"
                )
            case "iterm-presentation.enable-python-api":
                return t(
                    "In iTerm2, open Settings -> General -> Magic, enable Python API, "
                        + "then restart iTerm2. The button copies equivalent defaults commands.",
                    "在 iTerm2 打开 Settings -> General -> Magic，启用 Python API，"
                        + "然后重启 iTerm2。按钮会复制等效的 defaults 命令。"
                )
            case "iterm-presentation.restart-after-python-api":
                return t(
                    "Python API is enabled, but iTerm2 has not created its local API "
                        + "socket yet. Quit and reopen iTerm2, then run preflight again.",
                    "Python API 已启用，但 iTerm2 还没有创建本地 API socket。"
                        + "退出并重新打开 iTerm2，然后重新运行预检。"
                )
            case "iterm-presentation.reset-private-api-socket":
                return t(
                    "iTerm2's local API socket is not usable by this user. Quit and "
                        + "reopen iTerm2; if this stays blocked, remove the stale socket "
                        + "from iTerm2's support folder.",
                    "当前用户无法使用 iTerm2 的本地 API socket。退出并重新打开 iTerm2；"
                        + "如果仍然受阻，删除 iTerm2 支持目录里的旧 socket。"
                )
            case "iterm-presentation.remove-authentication-bypass":
                return t(
                    "Remove any development authentication bypass for the iTerm2 "
                        + "presentation adapter, then run preflight again.",
                    "移除 iTerm2 展示适配器的开发认证绕过配置，然后重新运行预检。"
                )
            default:
                return nil
            }
        }
    }

    // MARK: Guidance rail (the one next step)

    enum Guide {
        static func step(_ index: Int) -> String { t("Step \(index)", "第 \(index) 步") }
        static var readyTag: String { t("Ready", "就绪") }
        static var next: String { t("Next", "下一步") }
        static var previous: String { t("Back", "上一步") }
        static var done: String { t("Done", "完成") }
        static func stepOf(_ index: Int, _ total: Int) -> String {
            t("Step \(index) of \(total)", "第 \(index) 步 · 共 \(total) 步")
        }
        static var reopenHelp: String {
            t("Open the getting-started guide", "打开上手引导")
        }
        static var policySay: String {
            t(
                "This room has no collaboration rules yet, so messages "
                    + "between colleagues would be refused. Apply the rules "
                    + "for the current team, then start them.",
                "这个房间还没有协作规则，同事之间的消息会被拒绝。"
                    + "按当前团队应用规则，然后就能启动他们。"
            )
        }

        static var registerSay: String {
            t(
                "Register your project — pick its folder; nothing is written to it.",
                "注册你的项目——选中文件夹即可，不会写入任何文件。"
            )
        }
        static var registerAction: String { t("Register Project…", "注册项目…") }
        static var createSay: String {
            t("Open the first task room for this project.", "为这个项目开第一个任务房间。")
        }
        static var createAction: String { t("Create Room", "创建房间") }
        static var prepareSay: String {
            t(
                "Prepare the workspace — an isolated copy; your originals stay untouched.",
                "准备工作区——隔离副本，不动你的原始仓库。"
            )
        }
        static var prepareAction: String { t("Prepare Workspace", "准备工作区") }
        static var addSay: String {
            t("Invite AI colleagues into the room.", "请 AI 同事进房间。")
        }
        static var addAction: String { t("Add AI Colleague", "添加 AI 同事") }
        static var resumeAction: String { t("Resume Room", "恢复房间") }
        static var configurePolicyAction: String {
            t("Apply Collaboration Rules", "应用协作规则")
        }
        static var startAction: String { t("Start All", "全部启动") }
        static var focusSay: String {
            t(
                "Everyone is working — focus a colleague's window and assign the task directly.",
                "都在工作中——聚焦同事窗口，直接布置任务。"
            )
        }
        /// Honest about the Host's semantics: `scenario.focus` focuses every
        /// interactive colleague's window, not one.
        static var focusAction: String { t("Focus All Windows", "聚焦所有窗口") }

        static var checkingWorkspace: String {
            t("Checking the workspace", "正在检查工作区")
        }

        /// A read, never a lifecycle mutation — it has no Host precondition to
        /// violate, so the one blocked state that used to offer nothing can
        /// still offer the step its own copy names.
        static var recheckAction: String {
            t("Re-read the Room", "重新读取房间")
        }
        static var readyMomentTitle: String { t("The room is ready", "房间已就绪") }
        static var readyMomentBody: String {
            t(
                "Focus any colleague's window and assign the task directly — that's the whole workflow.",
                "聚焦任一同事的窗口、直接布置任务——流程就这么多。"
            )
        }
    }

    // MARK: Repair cards — typed code → one human sentence

    enum Fix {
        /// A human sentence for the codes an employee can plausibly meet.
        /// Unknown codes return nil and the card falls back to the raw Host
        /// message (verbatim evidence is better than a wrong translation).
        static func sentence(_ code: String) -> String? {
            switch code {
            case "workspace.shallow-source":
                return t(
                    "A repository's Git history is incomplete.",
                    "有仓库的 Git 历史不完整。"
                )
            case "workspace.partial-source", "workspace.partial-source-invalid",
                 "workspace.alternate-object-source":
                return t(
                    "A repository's local Git storage cannot be copied safely.",
                    "有仓库的本地 Git 存储无法被安全复制。"
                )
            case "workspace.git-auth-required":
                return t(
                    "Git sign-in is needed before repositories can be downloaded.",
                    "需要先登录 Git 才能下载仓库。"
                )
            case "workspace.network-unavailable":
                return t(
                    "The network is unavailable right now.",
                    "当前网络不可用。"
                )
            case "workspace.branch-unavailable":
                return t(
                    "A declared repository branch could not be found.",
                    "声明的仓库分支不存在。"
                )
            case "workspace.remote-unavailable", "workspace.remote-download-failed":
                return t(
                    "A repository could not be reached at its declared address.",
                    "按声明地址联系不上某个仓库。"
                )
            case "workspace.source-origin-mismatch":
                return t(
                    "A local checkout points at a different remote than the team definition.",
                    "本地仓库的远端地址与团队配置不一致。"
                )
            case "workspace.disk-full":
                return t("There is not enough free disk space.", "磁盘空间不足。")
            case "project.intent-too-new":
                return t(
                    "This project needs a newer AICollab.",
                    "这个项目需要更新版本的 AICollab。"
                )
            case "project.intent-invalid", "project.descriptor-invalid",
                 "project.manifest-invalid", "project.partial-configuration":
                return t(
                    "The project's configuration file has a problem.",
                    "项目配置文件有问题。"
                )
            case "project.reconciliation-required":
                return t(
                    "Finish the project update first, then create the room.",
                    "先完成项目更新，再创建房间。"
                )
            case "project.reconciliation-stale":
                return t(
                    "The project changed — check for updates again.",
                    "项目有变化——请重新检查更新。"
                )
            case "operation.adapter-crashed":
                return t(
                    "A background helper stopped unexpectedly.",
                    "后台组件异常退出。"
                )
            case "availability.adapter-unavailable", "project.adapter-unavailable":
                return t(
                    "A required background helper is unavailable.",
                    "所需的后台组件不可用。"
                )
            case "availability.host-unavailable":
                return t("The background service is not running.", "后台服务未在运行。")
            case "auth.confirmation-timeout":
                return t(
                    "The high-risk confirmation timed out.",
                    "高风险操作确认超时。"
                )
            default:
                return nil
            }
        }

        /// When no code sentence exists, the card leads with a localized
        /// category sentence; the raw Host message stays in Technical Details.
        static func categoryFallback(_ category: String) -> String {
            // The exact host_ipc_v1 category enum:
            // protocol | identity | authorization | fencing | availability | operation
            switch category {
            case "protocol":
                return t(
                    "The App and the service could not understand each other.",
                    "应用与后台服务的通信出错。"
                )
            case "identity":
                return t("An identity check did not pass.", "身份校验未通过。")
            case "authorization":
                return t("This action is not authorized.", "没有执行此操作的授权。")
            case "fencing":
                return t(
                    "The room changed underneath this action — refresh and try again.",
                    "状态已发生变化——刷新后重试。"
                )
            case "availability":
                return t(
                    "A required service is not available right now.",
                    "所需的服务暂时不可用。"
                )
            case "operation":
                return t("The operation could not be completed.", "操作未能完成。")
            default:
                return t("Something went wrong.", "发生了一个错误。")
            }
        }
    }

    // MARK: Workspace preparation progress rows

    enum Prepare {
        static var environmentRow: String { t("Python environment", "Python 环境") }
        static func rowState(_ state: String, afterFailure: Bool) -> String {
            switch state {
            case "waiting":
                return afterFailure
                    ? t("not started", "未开始")
                    : t("waiting", "等待中")
            case "cloning": return t("cloning…", "克隆中…")
            case "building": return t("building…", "构建中…")
            case "ready": return t("done", "完成")
            case "failed": return t("failed", "失败")
            default: return t("unknown", "未知")
            }
        }
    }

    // MARK: Room detail sections

    enum Sections {
        static var health: String { t("Health", "健康") }
        static var resources: String { t("Resources", "资源") }
        /// Phase 1 redesign: the collapsed drawer holding Preflight, Window
        /// Topology, Collaboration Policy, Deliveries, Resources, Inspector
        /// and high-risk actions — everything that used to be separate
        /// top-level disclosures. See review 20260903-175908-nyr2wy.
        static var evidenceAndDiagnostics: String {
            t("Evidence & Diagnostics", "证据与诊断")
        }
        static var inspectorToggleHelp: String {
            t("Show or hide Evidence & Diagnostics", "显示或隐藏证据与诊断")
        }
        /// The workbench's secondary column (v2): lifecycle stage, the four
        /// collaboration-health facts, and the needs-attention list.
        static var progress: String { t("Progress", "协作进度") }
        static var noResources: String { t("Nothing is held.", "无占用。") }
        static func healthNeedsRepair(_ stateLabel: String) -> String {
            t(
                "This room needs repair: \(stateLabel). Repair keeps all work and history.",
                "这个房间需要修复：\(stateLabel)。修复会保留全部工作内容和历史。"
            )
        }
        static func resourceRow(_ resourceClass: String, _ holder: String) -> String {
            t(
                "\(resourceClassLabel(resourceClass)) · held by \(holder)",
                "\(resourceClassLabel(resourceClass)) · 由 \(holder) 持有"
            )
        }
        /// The Host's exact resource classes, in employee words; raw tokens
        /// stay in Technical Details.
        static func resourceClassLabel(_ token: String) -> String {
            switch token {
            case "port": return t("network port", "网络端口")
            case "device": return t("device", "设备")
            case "compute": return t("compute", "算力")
            case "accelerator": return t("accelerator", "加速器")
            case "exclusive_runtime": return t("exclusive runtime", "独占运行时")
            default: return t("resource", "资源")
            }
        }
    }

    // MARK: Settings

    enum Settings {
        static var generalTab: String { t("General", "通用") }
        static var diagnosticsTab: String { t("Diagnostics", "诊断") }
        static var languageTitle: String { t("Language", "语言") }
        static var languageSystem: String { t("Follow System", "跟随系统") }
        static var languageChinese: String { t("简体中文", "简体中文") }
        static var languageEnglish: String { t("English", "English") }
        static var languageFootnote: String {
            t(
                "Applies immediately to the whole app.",
                "立即对整个应用生效。"
            )
        }
    }
}

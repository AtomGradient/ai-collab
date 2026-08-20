// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import XCTest
@testable import AICollab

@MainActor
final class RepairAndProgressTests: XCTestCase {

    private var previousLanguage: AppLanguage = .system

    override func setUp() {
        super.setUp()
        previousLanguage = L10n.shared.preference
        L10n.shared.preference = .english
    }

    override func tearDown() {
        L10n.shared.preference = previousLanguage
        super.tearDown()
    }

    private func componentEvent(
        _ id: String, index: Int, state: String, kind: String = "repository",
        phase: String = "workspace.repositories"
    ) -> HarnessProgress {
        HarnessProgress(
            operationID: "wsop-1", sequence: 0, state: "running",
            completedUnits: 0, totalUnits: 3, participantID: nil,
            cancellable: false, progressKind: "workspace-component-v1",
            phase: phase, componentID: id, componentKind: kind,
            componentIndex: index, componentState: state
        )
    }

    func testProgressSessionGuardsAndMutationReset() async {
        let model = HarnessViewModel()
        let session = UUID()
        model.activeProgressSessionID = session

        model.applyProgress(
            componentEvent("repo-a", index: 0, state: "waiting"),
            progressSessionID: UUID()
        )
        XCTAssertTrue(
            model.workspaceProgress.isEmpty,
            "a stale session must never write rows"
        )

        model.applyProgress(
            componentEvent("repo-a", index: 0, state: "waiting"),
            progressSessionID: session
        )
        XCTAssertEqual(model.workspaceProgress.count, 1)

        // Any new mutation clears the previous preparation's rows before its
        // own work begins — here the mutation fails fast at the local client.
        model.projects = [
            ProjectRecord([
                "project_instance_id": "proj-1",
                "project_key": "edge-studio",
                "project_binding_digest": String(repeating: "a", count: 64),
                "product_contract_version": "3",
            ])!
        ]
        model.selectedProjectID = "proj-1"
        model.newScenarioID = "room-reset"
        await model.createScenario()
        XCTAssertTrue(
            model.workspaceProgress.isEmpty,
            "another mutation must not display a previous Prepare's rows"
        )
    }

    func testCategoryFallbackAndDistinctAdapterSentences() {
        XCTAssertNil(S.Fix.sentence("workspace.some-future-code"))
        XCTAssertEqual(
            S.Fix.categoryFallback("availability"),
            "A required service is not available right now."
        )
        XCTAssertEqual(
            S.Fix.sentence("operation.adapter-crashed"),
            "A background helper stopped unexpectedly."
        )
        XCTAssertEqual(
            S.Fix.sentence("availability.adapter-unavailable"),
            "A required background helper is unavailable."
        )
        L10n.shared.preference = .simplifiedChinese
        XCTAssertEqual(S.Fix.categoryFallback("availability"), "所需的服务暂时不可用。")
        XCTAssertEqual(S.Fix.sentence("operation.adapter-crashed"), "后台组件异常退出。")
        XCTAssertEqual(
            S.Fix.sentence("availability.adapter-unavailable"), "所需的后台组件不可用。"
        )
        L10n.shared.preference = .english
    }

    func testRepairSentencesCoverKnownCodesAndFallBackForUnknown() {
        XCTAssertEqual(
            S.Fix.sentence("workspace.shallow-source"),
            "A repository's Git history is incomplete."
        )
        L10n.shared.preference = .simplifiedChinese
        XCTAssertEqual(
            S.Fix.sentence("workspace.shallow-source"),
            "有仓库的 Git 历史不完整。"
        )
        L10n.shared.preference = .english
        XCTAssertNil(
            S.Fix.sentence("workspace.some-future-code"),
            "unknown codes must use the localized category fallback"
        )
    }

    func testComponentProgressAccumulatesFailFastRows() {
        let model = HarnessViewModel()
        for event in [
            componentEvent("repo-a", index: 0, state: "waiting"),
            componentEvent("repo-b", index: 1, state: "waiting"),
            componentEvent("env-1", index: 2, state: "waiting", kind: "environment"),
            componentEvent("repo-a", index: 0, state: "cloning"),
            componentEvent("repo-a", index: 0, state: "ready"),
            componentEvent("repo-b", index: 1, state: "cloning"),
            componentEvent("repo-b", index: 1, state: "failed"),
        ] {
            model.applyWorkspaceComponentProgress(event)
        }
        XCTAssertEqual(model.workspaceProgress.count, 3)
        XCTAssertEqual(model.workspaceProgress[0].state, "ready")
        XCTAssertEqual(model.workspaceProgress[1].state, "failed")
        XCTAssertEqual(
            model.workspaceProgress[2].state, "waiting",
            "fail-fast: the environment never starts"
        )
        XCTAssertTrue(model.workspaceProgressHasFailure)
        XCTAssertEqual(
            S.Prepare.rowState("waiting", afterFailure: true), "not started"
        )
    }

    func testCompletionAuthorityMarksEveryRowReady() {
        let model = HarnessViewModel()
        model.applyWorkspaceComponentProgress(
            componentEvent("repo-a", index: 0, state: "waiting")
        )
        model.applyWorkspaceComponentProgress(
            HarnessProgress(
                operationID: "wsop-1", sequence: 9, state: "completed",
                completedUnits: 1, totalUnits: 1, participantID: nil,
                cancellable: false, progressKind: "workspace-component-v1",
                phase: "workspace.prepare", componentID: nil,
                componentKind: nil, componentIndex: nil,
                componentState: "complete"
            )
        )
        XCTAssertEqual(model.workspaceProgress.map(\.state), ["ready"])
    }

    func testCategoryFallbackCoversTheExactProtocolEnum() {
        let expectations: [(String, String, String)] = [
            ("protocol", "The App and the service could not understand each other.", "应用与后台服务的通信出错。"),
            ("identity", "An identity check did not pass.", "身份校验未通过。"),
            ("authorization", "This action is not authorized.", "没有执行此操作的授权。"),
            ("fencing", "The room changed underneath this action — refresh and try again.", "状态已发生变化——刷新后重试。"),
            ("availability", "A required service is not available right now.", "所需的服务暂时不可用。"),
            ("operation", "The operation could not be completed.", "操作未能完成。"),
        ]
        for (category, english, chinese) in expectations {
            L10n.shared.preference = .english
            XCTAssertEqual(S.Fix.categoryFallback(category), english, category)
            L10n.shared.preference = .simplifiedChinese
            XCTAssertEqual(S.Fix.categoryFallback(category), chinese, category)
        }
        L10n.shared.preference = .english
    }

    func testRetrySemanticActionHonorsTheRetryableGate() {
        let retryable = ActionableErrorRecord(
            HarnessIPCError.hostRejected(
                code: "availability.host-degraded", category: "availability",
                message: "raw", retryable: true, mutationState: "not_started",
                repairAction: "host.retry"
            )
        )
        let model = HarnessViewModel()
        XCTAssertEqual(model.performableRepairAction(retryable), "host.retry")

        let terminal = ActionableErrorRecord(
            HarnessIPCError.hostRejected(
                code: "availability.host-degraded", category: "availability",
                message: "raw", retryable: false, mutationState: "not_started",
                repairAction: "host.retry"
            )
        )
        XCTAssertNil(
            model.performableRepairAction(terminal),
            "retryable=false must never render a Retry button"
        )
    }
}

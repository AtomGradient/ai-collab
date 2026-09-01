// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import XCTest
@testable import AICollab

/// The user-approved rule: switching the language retranslates every visible
/// App-owned string instantly — stored transient state included. These tests
/// generate presentation state in English, flip to Chinese, and assert the
/// same accessors answer in Chinese immediately (and back).
@MainActor
final class LanguageSwitchTests: XCTestCase {

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

    func testStoredPresentationRetranslatesInBothDirections() async {
        let model = HarnessViewModel()

        await model.createScenario()
        XCTAssertEqual(
            model.validationMessage(for: .scenarioCreate),
            "Register or select a project first."
        )
        XCTAssertEqual(
            model.policyMessage,
            "Select a task room to inspect its collaboration policy."
        )
        XCTAssertEqual(model.hostStatusDisplay, "connecting…")
        model.noteSuccess(S.Msg.updateApplied)
        XCTAssertEqual(model.successMessage, "Project update applied")

        L10n.shared.preference = .simplifiedChinese
        XCTAssertEqual(
            model.validationMessage(for: .scenarioCreate),
            "先注册或选择一个项目。"
        )
        XCTAssertEqual(model.policyMessage, "选择任务房间后查看其协作规则。")
        XCTAssertEqual(model.hostStatusDisplay, "连接中…")
        XCTAssertEqual(model.successMessage, "项目更新已应用")

        L10n.shared.preference = .english
        XCTAssertEqual(model.successMessage, "Project update applied")
        XCTAssertEqual(model.hostStatusDisplay, "connecting…")
    }

    /// codex review P1-1/P1-2: a persisted delivery failure and a service
    /// status label are App-owned copy; both must follow a language switch —
    /// including the inner local-error description captured at failure time.
    func testPersistedFailuresRetranslateIncludingInnerLocalErrors() {
        let model = HarnessViewModel()

        model.presentDeliveryFailure(HarnessIPCError.invalidReply, live: false)
        XCTAssertEqual(
            model.deliveryMessage,
            "Delivery health is unavailable. Harness Host returned an invalid reply."
        )
        XCTAssertEqual(
            HarnessServiceError.serviceUnresolved(status: .notRegistered)
                .localizedDescription
                .contains("status: not registered"),
            true
        )

        L10n.shared.preference = .simplifiedChinese
        XCTAssertEqual(
            model.deliveryMessage,
            "消息投递状况不可用。后台服务返回了无效应答。"
        )
        XCTAssertTrue(
            HarnessServiceError.serviceUnresolved(status: .notRegistered)
                .localizedDescription
                .contains("状态：未注册")
        )
        XCTAssertTrue(
            HarnessServiceError.serviceUnresolved(status: .requiresApproval)
                .localizedDescription
                .contains("状态：待批准")
        )

        L10n.shared.preference = .english
        XCTAssertEqual(
            model.deliveryMessage,
            "Delivery health is unavailable. Harness Host returned an invalid reply."
        )
    }

    func testStaleBundleStatusRetranslates() {
        let model = HarnessViewModel()
        model.hostStatus = "stale-bundle"
        XCTAssertEqual(model.hostStatusDisplay, "Restart your Mac to finish updating AI Collab")

        L10n.shared.preference = .simplifiedChinese
        XCTAssertEqual(model.hostStatusDisplay, "重新启动 Mac 以完成 AI Collab 更新")
    }
}

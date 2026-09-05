// SPDX-License-Identifier: MIT
// Copyright (c) 2026 AtomGradient

import XCTest
@testable import AICollab

@MainActor
final class PingAgentInstallationTests: XCTestCase {
    private static let entry = PingAgentCommandEntry(
        name: "ai-ping", path: "/Users/test/.local/bin/ai-ping", kind: "file",
        state: "conflict", replaceable: true
    )
    private static let conflict = PingAgentInstallationResult(
        status: "conflict", bundle: "verified", entries: [entry], conflicts: [entry],
        reason: nil, backupDirectory: nil
    )
    private static let ready = PingAgentInstallationResult(
        status: "ready", bundle: "verified", entries: [], conflicts: [],
        reason: nil, backupDirectory: nil
    )

    private struct Request: Sendable {
        let executable: URL
        let arguments: [String]
        let environment: [String: String]
    }

    private actor FakeRunner {
        var replies: [PingAgentInstallationResult]
        var requests: [Request] = []
        init(_ replies: [PingAgentInstallationResult]) { self.replies = replies }
        func run(_ process: Process) async throws -> Data {
            requests.append(Request(executable: process.executableURL!, arguments: process.arguments!,
                                    environment: process.environment!))
            return try JSONEncoder().encode(replies.removeFirst())
        }
    }

    func test_conflict_check_is_read_only_and_uses_embedded_runtime_without_host() async throws {
        let runner = FakeRunner([Self.conflict])
        let controller = PingAgentInstallationController(
            appBundleURL: URL(filePath: "/Applications/AI Collab.app"),
            stateRoot: URL(filePath: "/Users/test/state"),
            runner: { try await runner.run($0) }, developmentBuild: false
        )
        let result = try await controller.check()
        XCTAssertEqual(result, Self.conflict)
        let requests = await runner.requests
        XCTAssertEqual(requests.count, 1)
        XCTAssertTrue(requests[0].executable.path.hasSuffix("HarnessService/runtime/bin/python3"))
        XCTAssertTrue(requests[0].arguments.contains("status"))
        XCTAssertFalse(requests[0].arguments.contains("reconcile"))
        XCTAssertEqual(requests[0].environment["PYTHONDONTWRITEBYTECODE"], "1")
        XCTAssertEqual(requests[0].environment["PYTHONNOUSERSITE"], "1")
        XCTAssertTrue(requests[0].arguments.contains("-B"))
    }

    func test_missing_links_reconcile_but_ready_installation_does_not() async throws {
        let drift = PingAgentInstallationResult(
            status: "needs_repair", bundle: "verified", entries: [], conflicts: [],
            reason: nil, backupDirectory: nil
        )
        let runner = FakeRunner([drift, Self.ready, Self.ready])
        let controller = PingAgentInstallationController(runner: { try await runner.run($0) }, developmentBuild: false)
        let repaired = try await controller.check()
        XCTAssertEqual(repaired.status, "ready")
        let unchanged = try await controller.check()
        XCTAssertEqual(unchanged.status, "ready")
        let requests = await runner.requests
        XCTAssertEqual(requests.count, 3)
        XCTAssertTrue(requests[1].arguments.contains("reconcile"))
        XCTAssertFalse(requests[2].arguments.contains("reconcile"))
    }

    func test_explicit_replacement_passes_exact_named_paths() async throws {
        let runner = FakeRunner([Self.ready])
        let controller = PingAgentInstallationController(runner: { try await runner.run($0) }, developmentBuild: false)
        _ = try await controller.reconcile(replacing: [Self.entry])
        let request = await runner.requests[0]
        XCTAssertEqual(Array(request.arguments.suffix(2)), ["--replace-command", Self.entry.path])
    }

    func test_development_build_skips_process_and_has_no_banner() async throws {
        let runner = FakeRunner([])
        let controller = PingAgentInstallationController(runner: { try await runner.run($0) }, developmentBuild: true)
        let model = HarnessViewModel(installationController: controller)
        await model.checkCommandInstallation()
        XCTAssertNil(model.commandInstallationError)
        let requests = await runner.requests
        XCTAssertTrue(requests.isEmpty)
    }

    func test_conflict_survives_other_error_dismissal_and_cancel_does_not_replace() async throws {
        let runner = FakeRunner([Self.conflict, Self.conflict])
        let installer = PingAgentInstallationController(runner: { try await runner.run($0) }, developmentBuild: false)
        let model = HarnessViewModel(installationController: installer)
        await model.checkCommandInstallation()
        model.actionableError = ActionableErrorRecord(HarnessIPCError.hostUnavailable)
        model.dismissError()
        XCTAssertEqual(model.commandInstallationError?.code, "installation.commands")
        await model.repairCommandInstallation()
        XCTAssertEqual(model.pendingCommandReplacement, Self.conflict)
        model.pendingCommandReplacement = nil
        let requests = await runner.requests
        XCTAssertTrue(requests.allSatisfy { !$0.arguments.contains("reconcile") })
        XCTAssertNotNil(model.commandInstallationError)
    }

    func test_confirmed_replacement_clears_only_on_success() async throws {
        let runner = FakeRunner([Self.conflict, Self.ready])
        let installer = PingAgentInstallationController(runner: { try await runner.run($0) }, developmentBuild: false)
        let model = HarnessViewModel(installationController: installer)
        await model.repairCommandInstallation()
        let confirmation = try XCTUnwrap(model.pendingCommandReplacement)
        model.pendingCommandReplacement = nil // SwiftUI dismisses before the async action runs.
        await model.confirmCommandReplacement(confirmation)
        let requests = await runner.requests
        XCTAssertEqual(Array(requests[1].arguments.suffix(2)), ["--replace-command", Self.entry.path])
        XCTAssertNil(model.pendingCommandReplacement)
        XCTAssertNil(model.commandInstallationError)
        XCTAssertFalse(model.isBusy)
    }

    func test_failed_replacement_remains_visible() async throws {
        let failed = PingAgentInstallationResult(
            status: "error", bundle: nil, entries: [], conflicts: [],
            reason: "disk full; original restored", backupDirectory: nil
        )
        let runner = FakeRunner([Self.conflict, failed])
        let installer = PingAgentInstallationController(runner: { try await runner.run($0) }, developmentBuild: false)
        let model = HarnessViewModel(installationController: installer)
        await model.repairCommandInstallation()
        await model.confirmCommandReplacement(try XCTUnwrap(model.pendingCommandReplacement))
        XCTAssertNotNil(model.commandInstallationError)
        XCTAssertTrue(model.commandInstallationError?.message.contains("disk full") == true)
        XCTAssertFalse(model.isBusy)
    }
}

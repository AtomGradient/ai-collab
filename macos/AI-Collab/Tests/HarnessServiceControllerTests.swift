// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import ServiceManagement
import XCTest
@testable import AICollab

/// Registration state machine, driven through the injected service slice so
/// no test touches launchd/BTM. The regression pinned here: a fresh machine
/// (clean Background Task Management store) reports `.notFound` before the
/// first registration, and v0.1.6 translated that into "the bundle is
/// missing its service" without ever calling register().
@available(macOS 13.0, *)
final class HarnessServiceControllerTests: XCTestCase {
    private final class FakeService: HarnessServiceManaging {
        var status: SMAppService.Status
        var statusAfterRegister: SMAppService.Status
        var registerError: Error?
        private(set) var registerCalls = 0
        private(set) var unregisterCalls = 0

        init(
            status: SMAppService.Status,
            statusAfterRegister: SMAppService.Status = .enabled
        ) {
            self.status = status
            self.statusAfterRegister = statusAfterRegister
        }

        func register() throws {
            registerCalls += 1
            if let registerError { throw registerError }
            status = statusAfterRegister
        }

        func unregister() async throws {
            unregisterCalls += 1
            status = .notRegistered
        }
    }

    private let validDigest = String(repeating: "ab", count: 32)

    private func makeController(
        service: FakeService,
        digest: String? = nil
    ) throws -> (HarnessServiceController, URL) {
        let stateRoot = FileManager.default.temporaryDirectory
            .appending(path: "controller-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: stateRoot, withIntermediateDirectories: true
        )
        addTeardownBlock {
            try? FileManager.default.removeItem(at: stateRoot)
        }
        let controller = HarnessServiceController(
            service: service,
            stateRoot: stateRoot,
            appBundleURL: URL(filePath: "/Applications/AICollab.app"),
            buildDigest: digest ?? validDigest
        )
        return (controller, stateRoot)
    }

    /// Fresh machine: `.notFound` is clean pre-registration state, so a
    /// normal register() must run and succeed — never a missing-bundle error.
    func test_not_found_registers_like_a_clean_first_install() async throws {
        let service = FakeService(status: .notFound)
        let (controller, stateRoot) = try makeController(service: service)

        let result = try await controller.ensureRegistered()

        XCTAssertEqual(result, .enabled)
        XCTAssertEqual(service.registerCalls, 1)
        let receipt = stateRoot
            .appending(path: "installation/service-registration.json")
        XCTAssertTrue(FileManager.default.fileExists(atPath: receipt.path))
    }

    /// Parity: `.notRegistered` keeps its existing behavior.
    func test_not_registered_still_registers() async throws {
        let service = FakeService(status: .notRegistered)
        let (controller, _) = try makeController(service: service)

        let result = try await controller.ensureRegistered()

        XCTAssertEqual(result, .enabled)
        XCTAssertEqual(service.registerCalls, 1)
    }

    /// A real register() failure must surface the original NSError
    /// (domain/code), not be masked by a guessed cause.
    func test_register_failure_preserves_the_underlying_error() async throws {
        let service = FakeService(status: .notFound)
        service.registerError = NSError(
            domain: "SMAppServiceErrorDomain",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: "Operation not permitted"]
        )
        let (controller, _) = try makeController(service: service)

        do {
            _ = try await controller.ensureRegistered()
            XCTFail("registration failure must throw")
        } catch let HarnessServiceError.registrationFailed(underlying) {
            let value = underlying as NSError
            XCTAssertEqual(value.domain, "SMAppServiceErrorDomain")
            XCTAssertEqual(value.code, 1)
        }
    }

    /// register() returned but launchd/BTM still does not resolve the agent:
    /// fail closed with the observed status, do not report success.
    func test_unresolved_after_register_fails_closed() async throws {
        let service = FakeService(status: .notFound, statusAfterRegister: .notFound)
        let (controller, _) = try makeController(service: service)

        do {
            _ = try await controller.ensureRegistered()
            XCTFail("unresolved registration must throw")
        } catch let HarnessServiceError.serviceUnresolved(status) {
            XCTAssertEqual(status, .notFound)
            XCTAssertEqual(service.registerCalls, 1)
        }
    }

    /// Approval gate keeps failing closed without a register() attempt.
    func test_requires_approval_fails_closed_without_registering() async throws {
        let service = FakeService(status: .requiresApproval)
        let (controller, _) = try makeController(service: service)

        do {
            _ = try await controller.ensureRegistered()
            XCTFail("approval-required must throw")
        } catch HarnessServiceError.approvalRequired {
            XCTAssertEqual(service.registerCalls, 0)
        }
    }

    /// Missing or malformed build digest still refuses before any
    /// service-management call.
    func test_missing_build_identity_fails_before_any_service_call() async throws {
        let service = FakeService(status: .notFound)
        let (controller, _) = try makeController(service: service, digest: "not-a-digest")

        do {
            _ = try await controller.ensureRegistered()
            XCTFail("missing build identity must throw")
        } catch HarnessServiceError.buildIdentityMissing {
            XCTAssertEqual(service.registerCalls, 0)
            XCTAssertEqual(service.unregisterCalls, 0)
        }
    }
}

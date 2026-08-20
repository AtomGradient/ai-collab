// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import Foundation
import ServiceManagement

enum HarnessServiceStatus: Equatable, Sendable {
    case notRegistered
    case enabled
    case requiresApproval
    case notFound
    case unknown(Int)

    var label: String {
        switch self {
        case .notRegistered: "not registered"
        case .enabled: "enabled"
        case .requiresApproval: "approval required"
        case .notFound: "not found"
        case let .unknown(value): "unknown (\(value))"
        }
    }
}

enum HarnessServiceError: LocalizedError {
    case approvalRequired
    case registrationFailed(underlying: Error)
    case unregisterFailed(underlying: Error)
    case serviceUnresolved(status: HarnessServiceStatus)
    case buildIdentityMissing
    case registrationStateUnavailable

    var errorDescription: String? {
        switch self {
        case .approvalRequired:
            return "Harness Host requires approval in System Settings → General → Login Items."
        case let .registrationFailed(underlying):
            let value = underlying as NSError
            return "Harness Host registration failed (\(value.domain) \(value.code)): "
                + value.localizedDescription
        case let .unregisterFailed(underlying):
            let value = underlying as NSError
            return "Harness Host re-registration could not release the previous "
                + "registration (\(value.domain) \(value.code)): "
                + value.localizedDescription
        case let .serviceUnresolved(status):
            return "macOS Service Management could not resolve the Harness Host "
                + "service after registration (status: \(status.label)). Make sure "
                + "the App runs from /Applications, then quit and reopen it."
        case .buildIdentityMissing:
            return "The signed App does not contain its Harness service build identity."
        case .registrationStateUnavailable:
            return "Harness Host registration state could not be stored securely."
        }
    }
}

/// The slice of SMAppService the controller uses. Injected so the
/// registration state machine is testable without touching launchd/BTM.
@available(macOS 13.0, *)
protocol HarnessServiceManaging {
    var status: SMAppService.Status { get }
    func register() throws
    func unregister() async throws
}

@available(macOS 13.0, *)
extension SMAppService: HarnessServiceManaging {}

@available(macOS 13.0, *)
final class HarnessServiceController: @unchecked Sendable {
    static let launchAgentPlistName = "com.atomgradient.aicollab.host.plist"
    static let serviceBuildDigestKey = "AICollabServiceBuildDigest"
    private let service: any HarnessServiceManaging
    private let registrationReceipt: URL
    private let appBundleURL: URL
    private let buildDigest: String?

    init(
        service: any HarnessServiceManaging = SMAppService.agent(
            plistName: launchAgentPlistName
        ),
        stateRoot: URL = HarnessServiceController.defaultStateRoot(),
        appBundleURL: URL = Bundle.main.bundleURL,
        buildDigest: String? = Bundle.main.object(
            forInfoDictionaryKey: HarnessServiceController.serviceBuildDigestKey
        ) as? String
    ) {
        self.service = service
        self.appBundleURL = appBundleURL
        self.buildDigest = buildDigest
        registrationReceipt = stateRoot
            .appending(path: "installation", directoryHint: .isDirectory)
            .appending(path: "service-registration.json")
    }

    var status: HarnessServiceStatus { Self.status(service.status) }

    func ensureRegistered() async throws -> HarnessServiceStatus {
        guard let buildDigest, Self.isSHA256(buildDigest) else {
            throw HarnessServiceError.buildIdentityMissing
        }

        if status == .enabled, !registrationMatches(buildDigest: buildDigest) {
            do {
                try await service.unregister()
            } catch {
                // Same rule as register(): keep the original NSError and a
                // typed stage, so the header never sticks on "Connecting…".
                throw HarnessServiceError.unregisterFailed(underlying: error)
            }
        }
        switch status {
        case .enabled:
            break
        case .notRegistered, .notFound, .unknown:
            // `.notFound` can be a clean pre-registration state on a
            // machine whose Background Task Management store has not
            // resolved this agent yet. It must attempt a normal
            // registration exactly like `.notRegistered` — the spike
            // contract validates both as "clean before registration". It
            // must never be translated into a missing-bundle claim.
            do {
                try service.register()
            } catch {
                throw HarnessServiceError.registrationFailed(underlying: error)
            }
        case .requiresApproval:
            throw HarnessServiceError.approvalRequired
        }
        let current = status
        switch current {
        case .enabled:
            try storeRegistrationReceipt(buildDigest: buildDigest)
        case .requiresApproval:
            throw HarnessServiceError.approvalRequired
        case .notRegistered, .notFound, .unknown:
            // register() returned without throwing, yet launchd/BTM still
            // does not resolve the agent. Fail closed with the observed
            // status instead of guessing at a cause.
            throw HarnessServiceError.serviceUnresolved(status: current)
        }
        return current
    }

    func unregister() async throws {
        if status != .notRegistered {
            try await service.unregister()
        }
        try? FileManager.default.removeItem(at: registrationReceipt)
    }

    private func registrationMatches(buildDigest: String) -> Bool {
        guard
            let data = try? Data(contentsOf: registrationReceipt),
            let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            value["service_build_digest"] as? String == buildDigest,
            let appBundlePath = value["app_bundle_path"] as? String
        else { return false }
        return URL(filePath: appBundlePath, directoryHint: .isDirectory)
            .standardizedFileURL.path == appBundleURL.standardizedFileURL.path
    }

    private func storeRegistrationReceipt(buildDigest: String) throws {
        let directory = registrationReceipt.deletingLastPathComponent()
        do {
            try FileManager.default.createDirectory(
                at: directory,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o700], ofItemAtPath: directory.path
            )
            let value: [String: Any] = [
                "schema_version": 1,
                "service_build_digest": buildDigest,
                "app_bundle_path": appBundleURL.path,
            ]
            let data = try JSONSerialization.data(
                withJSONObject: value, options: [.sortedKeys]
            )
            try data.write(to: registrationReceipt, options: [.atomic])
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o600], ofItemAtPath: registrationReceipt.path
            )
        } catch {
            throw HarnessServiceError.registrationStateUnavailable
        }
    }

    private static func isSHA256(_ value: String) -> Bool {
        value.count == 64 && value.allSatisfy { $0.isHexDigit && !$0.isUppercase }
    }

    private static func defaultStateRoot() -> URL {
        let environment = ProcessInfo.processInfo.environment
        if let override = environment["AI_COLLAB_STATE_ROOT"], !override.isEmpty {
            return URL(filePath: override, directoryHint: .isDirectory)
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appending(path: "Library/Application Support/AI Collab", directoryHint: .isDirectory)
    }

    static func status(_ value: SMAppService.Status) -> HarnessServiceStatus {
        switch value {
        case .notRegistered: .notRegistered
        case .enabled: .enabled
        case .requiresApproval: .requiresApproval
        case .notFound: .notFound
        @unknown default: .unknown(value.rawValue)
        }
    }
}

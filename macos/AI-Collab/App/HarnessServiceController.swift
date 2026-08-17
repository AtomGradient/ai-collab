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
    case serviceMissing
    case buildIdentityMissing
    case registrationStateUnavailable

    var errorDescription: String? {
        switch self {
        case .approvalRequired:
            "Harness Host requires approval in System Settings → General → Login Items."
        case .serviceMissing:
            "The signed App does not contain its Harness Host service."
        case .buildIdentityMissing:
            "The signed App does not contain its Harness service build identity."
        case .registrationStateUnavailable:
            "Harness Host registration state could not be stored securely."
        }
    }
}

@available(macOS 13.0, *)
final class HarnessServiceController: @unchecked Sendable {
    static let launchAgentPlistName = "com.atomgradient.aicollab.host.plist"
    static let serviceBuildDigestKey = "AICollabServiceBuildDigest"
    private let service: SMAppService
    private let registrationReceipt: URL

    init(
        service: SMAppService = .agent(plistName: launchAgentPlistName),
        stateRoot: URL = HarnessServiceController.defaultStateRoot()
    ) {
        self.service = service
        registrationReceipt = stateRoot
            .appending(path: "installation", directoryHint: .isDirectory)
            .appending(path: "service-registration.json")
    }

    var status: HarnessServiceStatus { Self.status(service.status) }

    func ensureRegistered() async throws -> HarnessServiceStatus {
        guard
            let buildDigest = Bundle.main.object(
                forInfoDictionaryKey: Self.serviceBuildDigestKey
            ) as? String,
            Self.isSHA256(buildDigest)
        else { throw HarnessServiceError.buildIdentityMissing }

        if status == .enabled, !registrationMatches(buildDigest: buildDigest) {
            try await service.unregister()
        }
        switch status {
        case .enabled:
            break
        case .notRegistered:
            try service.register()
        case .requiresApproval:
            throw HarnessServiceError.approvalRequired
        case .notFound:
            throw HarnessServiceError.serviceMissing
        case .unknown:
            try service.register()
        }
        let current = status
        if current == .requiresApproval {
            throw HarnessServiceError.approvalRequired
        }
        if current == .notFound {
            throw HarnessServiceError.serviceMissing
        }
        if current == .enabled {
            try storeRegistrationReceipt(buildDigest: buildDigest)
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
            .standardizedFileURL.path == Bundle.main.bundleURL.standardizedFileURL.path
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
                "app_bundle_path": Bundle.main.bundleURL.path,
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

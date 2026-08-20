// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import CryptoKit
import Darwin
import Foundation

enum HarnessIPCError: LocalizedError {
    case invalidStateRoot
    case invalidProjectDirectory
    case capabilityUnavailable
    case hostUnavailable
    case operationTimedOut
    case invalidReply
    case contractMismatch
    case hostRejected(
        code: String,
        category: String,
        message: String,
        retryable: Bool,
        mutationState: String,
        repairAction: String?
    )

    var errorDescription: String? {
        switch self {
        case .invalidStateRoot: S.IPC.invalidStateRoot
        case .invalidProjectDirectory: S.IPC.invalidProjectDirectory
        case .capabilityUnavailable: S.IPC.capabilityUnavailable
        case .hostUnavailable: S.IPC.hostUnavailable
        case .operationTimedOut: S.IPC.operationTimedOut
        case .invalidReply: S.IPC.invalidReply
        case .contractMismatch: S.IPC.contractMismatch
        case let .hostRejected(code, category, message, retryable, mutationState, repairAction):
            "\(message) [\(code) · \(category) · \(mutationState)"
                + "\(retryable ? " · retryable" : "")"
                + "\(repairAction.map { " · next: \($0)" } ?? "")]"
        }
    }
}

struct HarnessCall: @unchecked Sendable {
    let operation: String
    let target: [String: Any]
    let fence: [String: Int]
    let payload: [String: Any]
    let requestID: String
    let responseTimeoutSeconds: Int

    init(
        operation: String,
        target: [String: Any],
        fence: [String: Int] = [:],
        payload: [String: Any] = [:],
        requestID: String = "req-\(UUID().uuidString.lowercased())",
        responseTimeoutSeconds: Int = 60
    ) {
        self.operation = operation
        self.target = target
        self.fence = fence
        self.payload = payload
        self.requestID = requestID
        self.responseTimeoutSeconds = responseTimeoutSeconds
    }
}

struct HarnessProgress: Sendable, Equatable {
    let operationID: String
    let sequence: Int
    let state: String
    let completedUnits: Int
    let totalUnits: Int
    let participantID: String?
    let cancellable: Bool
    // workspace-component-v1 side channel (optional; absent on other kinds).
    let progressKind: String?
    let phase: String?
    let componentID: String?
    let componentKind: String?
    let componentIndex: Int?
    let componentState: String?
}

private struct HarnessReply: @unchecked Sendable {
    let value: [String: Any]
}

final class HarnessIPCClient: @unchecked Sendable {
    let stateRoot: URL
    let socketPath: String
    private let clientInstanceID = "macos-app-\(UUID().uuidString.lowercased())"

    init(stateRoot: URL? = nil, socketPath: String? = nil) {
        let environment = ProcessInfo.processInfo.environment
        let environmentRoot = environment["AI_COLLAB_STATE_ROOT"].map {
            URL(filePath: $0, directoryHint: .isDirectory)
        }
        let root = stateRoot ?? environmentRoot
            ?? FileManager.default.homeDirectoryForCurrentUser
                .appending(path: "Library/Application Support/AI Collab", directoryHint: .isDirectory)
        self.stateRoot = root.standardizedFileURL
        self.socketPath = socketPath ?? environment["AI_COLLAB_SOCKET_PATH"]
            ?? root.appending(path: "host.sock").path
    }

    func grantProjectDirectoryAccess(_ url: URL) throws {
        let scoped = url.startAccessingSecurityScopedResource()
        defer {
            if scoped {
                url.stopAccessingSecurityScopedResource()
            }
        }
        let values = try url.resourceValues(forKeys: [.isDirectoryKey])
        guard values.isDirectory == true else {
            throw HarnessIPCError.invalidProjectDirectory
        }
        _ = try FileManager.default.contentsOfDirectory(
            at: url,
            includingPropertiesForKeys: nil,
            options: [.skipsSubdirectoryDescendants]
        )
    }

    func call(
        _ call: HarnessCall,
        progress: (@Sendable (HarnessProgress) -> Void)? = nil
    ) async throws -> [String: Any] {
        let reply = try await Task.detached(priority: .userInitiated) { [self] in
            HarnessReply(value: try callSynchronously(call, progress: progress))
        }.value
        return reply.value
    }

    func cancelOperation(_ operationID: String) async throws -> [String: Any] {
        let reply = try await Task.detached(priority: .userInitiated) { [self] in
            HarnessReply(value: try cancelSynchronously(operationID))
        }.value
        return reply.value
    }

    private func callSynchronously(
        _ call: HarnessCall,
        progress: (@Sendable (HarnessProgress) -> Void)?
    ) throws -> [String: Any] {
        let capability = try readOwnerCapability()
        let descriptor = try connect(timeoutSeconds: call.responseTimeoutSeconds)
        defer { Darwin.close(descriptor) }
        let hostGeneration = try performHandshake(descriptor: descriptor)

        guard let requiredCapability = HarnessContract.capabilities[call.operation] else {
            throw HarnessIPCError.contractMismatch
        }
        let proof = try capabilityProof(
            secretHex: capability,
            operation: call.operation,
            requiredCapability: requiredCapability,
            target: call.target,
            hostGeneration: hostGeneration
        )
        var fence = call.fence
        fence["host_generation"] = hostGeneration
        try writeFrame(
            [
                "message_type": "operation_request",
                "contract_version": HarnessContract.version,
                "request_id": call.requestID,
                "operation": call.operation,
                "operation_schema_version": 1,
                "operation_registry_digest": HarnessContract.operationRegistryDigest,
                "capability_proof": proof,
                "target": call.target,
                "fence": fence,
                "payload": call.payload,
            ],
            descriptor: descriptor
        )
        var reply = try readFrame(descriptor: descriptor)
        var expectedSequence = 0
        var progressOperationID: String?
        while reply["message_type"] as? String == "progress_event" {
            guard
                reply["contract_version"] as? Int == HarnessContract.version,
                let operationID = reply["operation_id"] as? String,
                reply["sequence"] as? Int == expectedSequence,
                let state = reply["state"] as? String,
                ["queued", "running", "waiting", "cancelling", "completed", "failed", "cancelled"]
                    .contains(state),
                reply["host_generation"] as? Int == hostGeneration,
                let value = reply["progress"] as? [String: Any],
                let completedUnits = value["completed_units"] as? Int,
                let totalUnits = value["total_units"] as? Int,
                let cancellable = value["cancellable"] as? Bool,
                progressOperationID == nil || progressOperationID == operationID
            else { throw HarnessIPCError.invalidReply }
            progressOperationID = operationID
            expectedSequence += 1
            progress?(
                HarnessProgress(
                    operationID: operationID,
                    sequence: expectedSequence - 1,
                    state: state,
                    completedUnits: completedUnits,
                    totalUnits: totalUnits,
                    participantID: value["participant_id"] as? String,
                    cancellable: cancellable,
                    progressKind: value["progress_kind"] as? String,
                    phase: value["phase"] as? String,
                    componentID: value["component_id"] as? String,
                    componentKind: value["component_kind"] as? String,
                    componentIndex: value["component_index"] as? Int,
                    componentState: value["component_state"] as? String
                )
            )
            reply = try readFrame(descriptor: descriptor)
        }
        try checkError(reply)
        guard
            reply["message_type"] as? String == "operation_reply",
            reply["contract_version"] as? Int == HarnessContract.version,
            reply["request_id"] as? String == call.requestID,
            reply["outcome"] as? String == "completed",
            let result = reply["result"] as? [String: Any]
        else { throw HarnessIPCError.invalidReply }
        return result
    }

    private func cancelSynchronously(_ operationID: String) throws -> [String: Any] {
        let capability = try readOwnerCapability()
        let descriptor = try connect(timeoutSeconds: 30)
        defer { Darwin.close(descriptor) }
        let hostGeneration = try performHandshake(descriptor: descriptor)
        let requestID = "cancel-\(UUID().uuidString.lowercased())"
        try writeFrame(
            [
                "message_type": "cancel_request",
                "contract_version": HarnessContract.version,
                "request_id": requestID,
                "operation_id": operationID,
                "host_generation": hostGeneration,
                "capability_proof": try cancelCapabilityProof(
                    secretHex: capability,
                    operationID: operationID,
                    hostGeneration: hostGeneration
                ),
            ],
            descriptor: descriptor
        )
        let reply = try readFrame(descriptor: descriptor)
        try checkError(reply)
        guard
            reply["message_type"] as? String == "cancel_reply",
            reply["contract_version"] as? Int == HarnessContract.version,
            reply["request_id"] as? String == requestID,
            reply["outcome"] as? String == "accepted",
            reply["operation_id"] as? String == operationID,
            reply["host_generation"] as? Int == hostGeneration,
            let mutationState = reply["mutation_state"] as? String
        else { throw HarnessIPCError.invalidReply }
        return [
            "operation_id": operationID,
            "outcome": "accepted",
            "mutation_state": mutationState,
        ]
    }

    private func connect(timeoutSeconds: Int) throws -> Int32 {
        let descriptor = socket(AF_UNIX, SOCK_STREAM, 0)
        guard descriptor >= 0 else { throw HarnessIPCError.hostUnavailable }
        var timeout = timeval(tv_sec: numericCast(timeoutSeconds), tv_usec: 0)
        withUnsafePointer(to: &timeout) { pointer in
            _ = setsockopt(
                descriptor, SOL_SOCKET, SO_RCVTIMEO, pointer,
                socklen_t(MemoryLayout<timeval>.size)
            )
            _ = setsockopt(
                descriptor, SOL_SOCKET, SO_SNDTIMEO, pointer,
                socklen_t(MemoryLayout<timeval>.size)
            )
        }
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = Array(socketPath.utf8CString)
        let capacity = MemoryLayout.size(ofValue: address.sun_path)
        guard pathBytes.count <= capacity else {
            Darwin.close(descriptor)
            throw HarnessIPCError.hostUnavailable
        }
        withUnsafeMutableBytes(of: &address.sun_path) { destination in
            destination.initializeMemory(as: UInt8.self, repeating: 0)
            pathBytes.withUnsafeBytes { source in destination.copyBytes(from: source) }
        }
        let addressLength = socklen_t(MemoryLayout<sa_family_t>.size + pathBytes.count)
        let connected = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(descriptor, $0, addressLength)
            }
        }
        guard connected == 0 else {
            Darwin.close(descriptor)
            throw HarnessIPCError.hostUnavailable
        }
        return descriptor
    }

    private func performHandshake(descriptor: Int32) throws -> Int {
        let handshakeID = "hs-\(UUID().uuidString.lowercased())"
        try writeFrame(
            [
                "message_type": "handshake_request",
                "request_id": handshakeID,
                "client_instance_id": clientInstanceID,
                "supported_contract_versions": [HarnessContract.version],
                "client_capabilities": [],
            ],
            descriptor: descriptor
        )
        let handshake = try readFrame(descriptor: descriptor)
        try checkError(handshake)
        guard
            handshake["message_type"] as? String == "handshake_reply",
            handshake["request_id"] as? String == handshakeID,
            handshake["contract_version"] as? Int == HarnessContract.version,
            handshake["operation_registry_digest"] as? String
                == HarnessContract.operationRegistryDigest,
            let hostGeneration = handshake["host_generation"] as? Int
        else { throw HarnessIPCError.contractMismatch }
        return hostGeneration
    }

    private func readOwnerCapability() throws -> String {
        var rootStat = stat()
        var capabilityStat = stat()
        let capabilityPath = stateRoot.appending(path: "owner-capability").path
        guard
            lstat(stateRoot.path, &rootStat) == 0,
            (rootStat.st_mode & S_IFMT) == S_IFDIR,
            rootStat.st_uid == getuid(),
            (rootStat.st_mode & 0o777) == 0o700
        else { throw HarnessIPCError.invalidStateRoot }
        guard
            lstat(capabilityPath, &capabilityStat) == 0,
            (capabilityStat.st_mode & S_IFMT) == S_IFREG,
            capabilityStat.st_uid == rootStat.st_uid,
            (capabilityStat.st_mode & 0o777) == 0o600
        else { throw HarnessIPCError.capabilityUnavailable }
        let value = try String(contentsOfFile: capabilityPath, encoding: .utf8)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard value.count == 64, value.allSatisfy({ $0.isHexDigit && !$0.isUppercase }) else {
            throw HarnessIPCError.capabilityUnavailable
        }
        return value
    }

    private func capabilityProof(
        secretHex: String,
        operation: String,
        requiredCapability: String,
        target: [String: Any],
        hostGeneration: Int
    ) throws -> String {
        let binding: [String: Any] = [
            "contract_version": HarnessContract.version,
            "host_generation": hostGeneration,
            "operation": operation,
            "required_capability": requiredCapability,
            "target": target,
        ]
        let key = SymmetricKey(data: try Self.hexData(secretHex))
        let signature = HMAC<SHA256>.authenticationCode(
            for: try Self.canonicalJSON(binding), using: key
        )
        return Data(signature).map { String(format: "%02x", $0) }.joined()
    }

    private func cancelCapabilityProof(
        secretHex: String,
        operationID: String,
        hostGeneration: Int
    ) throws -> String {
        let binding: [String: Any] = [
            "contract_version": HarnessContract.version,
            "host_generation": hostGeneration,
            "operation_id": operationID,
            "purpose": "cancel",
        ]
        let key = SymmetricKey(data: try Self.hexData(secretHex))
        let signature = HMAC<SHA256>.authenticationCode(
            for: try Self.canonicalJSON(binding), using: key
        )
        return Data(signature).map { String(format: "%02x", $0) }.joined()
    }

    private func checkError(_ value: [String: Any]) throws {
        guard let outcome = value["outcome"] as? String else {
            throw HarnessIPCError.invalidReply
        }
        guard outcome == "rejected" || outcome == "failed" else { return }
        guard let error = value["error"] as? [String: Any] else {
            throw HarnessIPCError.invalidReply
        }
        throw HarnessIPCError.hostRejected(
            code: error["code"] as? String ?? "operation.failed",
            category: error["category"] as? String ?? "operation",
            message: error["redacted_message"] as? String ?? S.IPC.operationFailedFallback,
            retryable: error["retryable"] as? Bool ?? false,
            mutationState: value["mutation_state"] as? String ?? "not_started",
            repairAction: error["repair_action"] as? String
        )
    }

    private func writeFrame(_ value: [String: Any], descriptor: Int32) throws {
        var bytes = try Self.canonicalJSON(value)
        bytes.append(0x0a)
        guard bytes.count <= HarnessContract.maxMessageBytes else {
            throw HarnessIPCError.invalidReply
        }
        try bytes.withUnsafeBytes { buffer in
            var offset = 0
            while offset < buffer.count {
                let written = Darwin.write(
                    descriptor,
                    buffer.baseAddress!.advanced(by: offset),
                    buffer.count - offset
                )
                if written < 0, errno == EAGAIN || errno == ETIMEDOUT {
                    throw HarnessIPCError.operationTimedOut
                }
                guard written > 0 else { throw HarnessIPCError.hostUnavailable }
                offset += written
            }
        }
    }

    private func readFrame(descriptor: Int32) throws -> [String: Any] {
        var data = Data()
        var byte: UInt8 = 0
        while data.count <= HarnessContract.maxMessageBytes {
            let count = Darwin.read(descriptor, &byte, 1)
            if count < 0, errno == EAGAIN || errno == ETIMEDOUT {
                throw HarnessIPCError.operationTimedOut
            }
            guard count == 1 else { throw HarnessIPCError.hostUnavailable }
            if byte == 0x0a { break }
            data.append(byte)
        }
        guard data.count <= HarnessContract.maxMessageBytes else {
            throw HarnessIPCError.invalidReply
        }
        let value = try JSONSerialization.jsonObject(with: data)
        guard let object = value as? [String: Any] else {
            throw HarnessIPCError.invalidReply
        }
        return object
    }

    static func canonicalJSON(_ value: Any) throws -> Data {
        try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .withoutEscapingSlashes])
    }

    private static func hexData(_ value: String) throws -> Data {
        guard value.count.isMultiple(of: 2) else { throw HarnessIPCError.capabilityUnavailable }
        var result = Data()
        var index = value.startIndex
        while index < value.endIndex {
            let end = value.index(index, offsetBy: 2)
            guard let byte = UInt8(value[index..<end], radix: 16) else {
                throw HarnessIPCError.capabilityUnavailable
            }
            result.append(byte)
            index = end
        }
        return result
    }
}

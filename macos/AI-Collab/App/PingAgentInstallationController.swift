// SPDX-License-Identifier: MIT
// Copyright (c) 2026 AtomGradient
import Foundation
import Security
import Darwin

struct PingAgentInstallationController: Sendable {
    var appBundleURL: URL = Bundle.main.bundleURL
    var stateRoot: URL = FileManager.default.homeDirectoryForCurrentUser
        .appending(path: "Library/Application Support/AI Collab")
    var runner: @Sendable (Process) async throws -> Data = Self.runProcess
    var developmentBuild: Bool? = nil

    func check() async throws -> PingAgentInstallationResult {
        let result = try await invoke("status", replacing: [])
        return result.status == "needs_repair" ? try await reconcile(replacing: []) : result
    }

    func reconcile(replacing: [PingAgentCommandEntry]) async throws -> PingAgentInstallationResult {
        try await invoke("reconcile", replacing: replacing)
    }

    private func invoke(_ action: String, replacing: [PingAgentCommandEntry]) async throws -> PingAgentInstallationResult {
        if developmentBuild ?? Self.isDevelopmentBundle(appBundleURL) {
            NSLog("PingAgent command setup skipped for an unverified development bundle")
            return .development
        }
        let payload = appBundleURL.appending(path: "Contents/Resources/HarnessService")
        let runtime = payload.appending(path: "runtime")
        let process = Process()
        process.executableURL = runtime.appending(path: "bin/python3")
        process.arguments = ["-B", "-s", "-m", "ai_collab.pingagent_commands", action,
                             "--app", appBundleURL.path, "--state-root", stateRoot.path]
            + replacing.flatMap { ["--replace-command", $0.path] }
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONHOME"] = runtime.path
        environment["PYTHONPATH"] = payload.appending(path: "python").path
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONNOUSERSITE"] = "1"
        process.environment = environment
        return try JSONDecoder().decode(PingAgentInstallationResult.self, from: await runner(process))
    }

    private static func runProcess(_ process: Process) async throws -> Data {
        try await Task.detached(priority: .userInitiated) {
            let output = Pipe()
            process.standardInput = FileHandle.nullDevice
            process.standardOutput = output
            process.standardError = FileHandle.nullDevice
            try process.run()
            let timeout = DispatchWorkItem {
                if process.isRunning { kill(process.processIdentifier, SIGKILL) }
            }
            DispatchQueue.global().asyncAfter(deadline: .now() + 50, execute: timeout)
            defer { timeout.cancel() }
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            guard !data.isEmpty else {
                throw NSError(domain: "PingAgentInstallation", code: Int(process.terminationStatus),
                              userInfo: [NSLocalizedDescriptionKey: S.Installation.processFailed])
            }
            return data // Conflict/error JSON deliberately has a nonzero process exit status.
        }.value
    }

    private static func isDevelopmentBundle(_ url: URL) -> Bool {
        var code: SecStaticCode?
        guard SecStaticCodeCreateWithPath(url as CFURL, [], &code) == errSecSuccess,
              let code else { return false }
        var information: CFDictionary?
        let status = SecCodeCopySigningInformation(code, SecCSFlags(rawValue: kSecCSSigningInformation), &information)
        if status == errSecCSUnsigned { return true }
        let flags = (information as? [String: Any])?[kSecCodeInfoFlags as String] as? UInt32 ?? 0
        return SecCodeSignatureFlags(rawValue: flags).contains(.adhoc)
    }
}

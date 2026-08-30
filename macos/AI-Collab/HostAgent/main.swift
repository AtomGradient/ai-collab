// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import Darwin
import CFNetwork
import Foundation

private enum AgentFailure: Int32 {
    case bundleLayout = 78
    case launch = 70
}

private func fail(_ reason: String, _ code: AgentFailure) -> Never {
    FileHandle.standardError.write(Data("AI Collab Host Agent: \(reason)\n".utf8))
    exit(code.rawValue)
}

private func executableURL() -> URL {
    var bufferSize: UInt32 = 0
    _ = _NSGetExecutablePath(nil, &bufferSize)
    var buffer = [CChar](repeating: 0, count: Int(bufferSize))
    guard _NSGetExecutablePath(&buffer, &bufferSize) == 0 else {
        fail("could not resolve executable path", .bundleLayout)
    }
    return URL(filePath: String(cString: buffer)).resolvingSymlinksInPath()
}

private let executable = executableURL()
private let contents = executable
    .deletingLastPathComponent()
    .deletingLastPathComponent()
    .deletingLastPathComponent()
private let service = contents
    .appending(path: "Resources/HarnessService", directoryHint: .isDirectory)
private let runtime = service.appending(path: "runtime", directoryHint: .isDirectory)
private let python = runtime.appending(path: "bin/python3")
private let pythonPath = service.appending(path: "python", directoryHint: .isDirectory)
// Adapter configurations resolve user-first: an owner-supplied file in
// Application Support replaces the bundled one, exactly like the runtime
// profile overlay. The project and security adapters are optional — a build
// without an embedded integration payload starts the Host without them and
// project registration reports a typed refusal instead of the agent dying
// here. The participant driver ships in every build, so its absence still
// means the bundle itself is broken.
private let userConfigurationDirectory = FileManager.default
    .homeDirectoryForCurrentUser
    .appending(path: "Library/Application Support/AI Collab", directoryHint: .isDirectory)

private func resolvedConfiguration(_ name: String) -> URL? {
    let supplied = userConfigurationDirectory.appending(path: name)
    if FileManager.default.fileExists(atPath: supplied.path) {
        return supplied
    }
    let bundled = service.appending(path: name)
    if FileManager.default.fileExists(atPath: bundled.path) {
        return bundled
    }
    return nil
}

private let adapter = resolvedConfiguration("ai_collab_harness_adapter.json")
private let security = resolvedConfiguration("ai_collab_security_adapter.json")

for required in [python, pythonPath] {
    guard FileManager.default.fileExists(atPath: required.path) else {
        fail("embedded service payload is incomplete", .bundleLayout)
    }
}
guard let participant = resolvedConfiguration("ai_collab_participant_driver.json") else {
    fail("embedded service payload is incomplete", .bundleLayout)
}

setenv("PYTHONHOME", runtime.path, 1)
setenv("PYTHONPATH", pythonPath.path, 1)
setenv("PYTHONDONTWRITEBYTECODE", "1", 1)
setenv("PYTHONNOUSERSITE", "1", 1)
setenv("PYTHONUNBUFFERED", "1", 1)
let home = FileManager.default.homeDirectoryForCurrentUser.path
let userPaths = [
    "\(home)/.local/bin",
    "\(home)/.nvm/current/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
]
let inheritedPaths = (ProcessInfo.processInfo.environment["PATH"] ?? "")
    .split(separator: ":")
    .map(String.init)
let searchPaths = (userPaths + inheritedPaths).reduce(into: [String]()) { result, path in
    if !path.isEmpty && !result.contains(path) {
        result.append(path)
    }
}
setenv("PATH", searchPaths.joined(separator: ":"), 1)

private func configureSystemProxyEnvironment() {
    guard
        let settings = CFNetworkCopySystemProxySettings()?.takeRetainedValue()
            as? [String: Any]
    else { return }
    let proxyMappings = [
        ("HTTPEnable", "HTTPProxy", "HTTPPort", "http", "HTTP_PROXY", "http_proxy"),
        ("HTTPSEnable", "HTTPSProxy", "HTTPSPort", "http", "HTTPS_PROXY", "https_proxy"),
        ("SOCKSEnable", "SOCKSProxy", "SOCKSPort", "socks5", "ALL_PROXY", "all_proxy"),
    ]
    for (enabledKey, hostKey, portKey, scheme, upper, lower) in proxyMappings {
        guard
            (settings[enabledKey] as? NSNumber)?.boolValue == true,
            let host = settings[hostKey] as? String,
            !host.isEmpty,
            let port = settings[portKey] as? NSNumber
        else { continue }
        let value = "\(scheme)://\(host):\(port.intValue)"
        setenv(upper, value, 0)
        setenv(lower, value, 0)
    }
    let bypass = (settings["ExceptionsList"] as? [String] ?? [])
        + ["localhost", "127.0.0.1"]
    let noProxy = Array(Set(bypass)).sorted().joined(separator: ",")
    setenv("NO_PROXY", noProxy, 0)
    setenv("no_proxy", noProxy, 0)
}

configureSystemProxyEnvironment()

var arguments = [
    python.path,
    "-m",
    "ai_collab.service",
    "--participant-driver-config",
    participant.path,
]
if let adapter {
    arguments += ["--adapter-config", adapter.path]
}
if let security {
    arguments += ["--security-adapter-config", security.path]
}
let duplicated = arguments.map { strdup($0) }
defer { duplicated.forEach { free($0) } }
var argv = duplicated + [nil]
execv(python.path, &argv)
fail("could not launch embedded Host", .launch)

import Foundation

@main
enum LiveHarnessRoundTrip {
    static func main() async throws {
        let environment = ProcessInfo.processInfo.environment
        guard
            let stateRoot = environment["AI_COLLAB_LIVE_TEST_STATE_ROOT"],
            let socketPath = environment["AI_COLLAB_LIVE_TEST_SOCKET_PATH"],
            let projectPath = environment["AI_COLLAB_LIVE_TEST_PROJECT_PATH"]
        else { throw HarnessIPCError.invalidStateRoot }

        let client = HarnessIPCClient(
            stateRoot: URL(filePath: stateRoot, directoryHint: .isDirectory),
            socketPath: socketPath
        )
        let status = try await client.call(
            HarnessCall(operation: "host.status", target: ["scope": "host"])
        )
        guard status["status"] as? String == "ready" else {
            throw HarnessIPCError.invalidReply
        }
        let registration = try await client.call(
            HarnessCall(
                operation: "project.register",
                target: ["scope": "host"],
                fence: ["operation_generation": 0],
                payload: ["canonical_project_path": projectPath]
            )
        )
        guard
            let rawProject = registration["project"] as? [String: Any],
            let projectID = rawProject["project_instance_id"] as? String,
            let bindingDigest = rawProject["project_binding_digest"] as? String,
            rawProject["canonical_root"] == nil,
            rawProject["canonical_root_fingerprint"] == nil
        else { throw HarnessIPCError.invalidReply }

        let scenarioID = "swift-live-\(UUID().uuidString.lowercased())"
        let target: [String: Any] = [
            "scope": "scenario",
            "project_instance_id": projectID,
            "scenario_id": scenarioID,
        ]
        _ = try await client.call(
            HarnessCall(
                operation: "scenario.create",
                target: target,
                fence: ["operation_generation": 0],
                payload: ["project_binding_digest": bindingDigest]
            )
        )
        let participants = try await client.call(
            HarnessCall(operation: "participant.list", target: target)
        )
        let templates = try await client.call(
            HarnessCall(operation: "participant.template.list", target: ["scope": "host"])
        )
        guard
            (participants["participants"] as? [[String: Any]])?.isEmpty == true,
            (templates["templates"] as? [[String: Any]])?.isEmpty == false
        else { throw HarnessIPCError.invalidReply }
        print("LIVE_HOST_ROUND_TRIP_OK project=\(projectID) scenario=\(scenarioID)")
    }
}

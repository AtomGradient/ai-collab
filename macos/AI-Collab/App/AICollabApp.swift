import SwiftUI
import Darwin

@main
struct AICollabApp: App {
    @StateObject private var model: HarnessViewModel

    init() {
        let runningTests = ProcessInfo.processInfo.environment[
            "XCTestConfigurationFilePath"
        ] != nil
        let unregistering = CommandLine.arguments.contains("--unregister-host-service")
        if !runningTests && unregistering {
            Task.detached {
                do {
                    try await HarnessServiceController().unregister()
                    exit(EXIT_SUCCESS)
                } catch {
                    FileHandle.standardError.write(
                        Data("\(error.localizedDescription)\n".utf8)
                    )
                    exit(EXIT_FAILURE)
                }
            }
        }
        _model = StateObject(
            wrappedValue: HarnessViewModel(
                serviceController: runningTests || unregistering
                    ? nil
                    : HarnessServiceController()
            )
        )
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
        }
        .windowStyle(.titleBar)
        .commands {
            CommandGroup(replacing: .newItem) {}
        }
    }
}

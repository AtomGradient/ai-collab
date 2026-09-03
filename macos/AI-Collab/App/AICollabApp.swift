// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import SwiftUI
import Darwin

@main
struct AICollabApp: App {
    @StateObject private var model: HarnessViewModel
    @StateObject private var language = L10n.shared

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
                .environmentObject(language)
                // Rebuild every visible string the moment the language
                // preference changes — switching is instant, no relaunch.
                .id(language.effectiveLanguageID)
                .tint(.brandAccent)
        }
        .windowStyle(.titleBar)
        // First-launch size, not a minimum: three columns plus a 300pt
        // progress column need more than the 1100pt floor to read as the
        // two-column workbench; a window this size the user later resizes is
        // remembered by the frame autosave as usual.
        .defaultSize(width: 1440, height: 900)
        .commands {
            CommandGroup(replacing: .newItem) {}
        }

        Settings {
            SettingsView()
                .environmentObject(model)
                .environmentObject(language)
                .id(language.effectiveLanguageID)
        }
    }
}

/// App settings: language first, diagnostics behind a second tab. The
/// diagnostics content is unchanged — it just no longer occupies the whole
/// settings surface.
struct SettingsView: View {
    @EnvironmentObject private var language: L10n

    var body: some View {
        TabView {
            generalTab
                .tabItem { Label(S.Settings.generalTab, systemImage: "gearshape") }
            DiagnosticsView()
                .tabItem { Label(S.Settings.diagnosticsTab, systemImage: "stethoscope") }
        }
        .frame(minWidth: 640, minHeight: 420)
    }

    private var generalTab: some View {
        Form {
            Picker(S.Settings.languageTitle, selection: $language.preference) {
                Text(S.Settings.languageSystem).tag(AppLanguage.system)
                Text(S.Settings.languageChinese).tag(AppLanguage.simplifiedChinese)
                Text(S.Settings.languageEnglish).tag(AppLanguage.english)
            }
            .pickerStyle(.inline)
            Text(S.Settings.languageFootnote)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(24)
    }
}

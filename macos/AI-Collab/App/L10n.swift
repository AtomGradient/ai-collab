// SPDX-License-Identifier: MIT
// Copyright © 2026 AtomGradient
// 版权所有 © 2026 质子梯度（北京）科技有限公司

import Foundation
import Combine

/// The employee's language preference. "system" follows macOS; the other two
/// are explicit overrides the employee can pick in Settings.
enum AppLanguage: String, CaseIterable, Identifiable {
    case system
    case english
    case simplifiedChinese

    var id: String { rawValue }
}

/// Central language engine. Every user-visible string in the App flows
/// through `L10n.pick(en, zh)` (usually via the `S` catalog), so switching
/// the preference re-renders the whole interface instantly — the root view
/// keys its identity off `effectiveLanguageID`.
///
/// Machine keys, operation names, raw Host codes, and JSON are never
/// translated; they appear only inside collapsed technical detail.
@MainActor
final class L10n: ObservableObject {
    static let shared = L10n()
    private static let preferenceKey = "AICollabLanguagePreference"

    /// Snapshot read by `pick`. Written only from the main thread (init,
    /// Settings picker, tests); read from any render context. A torn read is
    /// impossible for a Bool and the worst race outcome is one frame of the
    /// previous language, so the unsafe opt-out is sound here.
    nonisolated(unsafe) private(set) static var chineseActive = false

    @Published var preference: AppLanguage {
        didSet {
            defaults.set(preference.rawValue, forKey: Self.preferenceKey)
            Self.chineseActive = Self.resolveChinese(preference)
        }
    }

    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let stored = defaults.string(forKey: Self.preferenceKey)
        let initial = stored.flatMap(AppLanguage.init(rawValue:)) ?? .system
        preference = initial
        Self.chineseActive = Self.resolveChinese(initial)
    }

    /// Changes whenever the effective language changes; the App root uses it
    /// as a view identity so a switch rebuilds every visible string at once.
    var effectiveLanguageID: String { Self.chineseActive ? "zh-Hans" : "en" }

    nonisolated static func pick(_ english: String, _ chinese: String) -> String {
        chineseActive ? chinese : english
    }

    nonisolated private static func resolveChinese(_ preference: AppLanguage) -> Bool {
        switch preference {
        case .simplifiedChinese: return true
        case .english: return false
        case .system:
            return Locale.preferredLanguages.first?
                .lowercased().hasPrefix("zh") ?? false
        }
    }
}

#!/usr/bin/env swift
// SPDX-License-Identifier: LicenseRef-AtomGradient-Proprietary
// Copyright (c) 2026 AtomGradient. All rights reserved.
// 版权所有 (c) 2026 质子梯度（北京）科技有限公司。保留所有权利。
// Unauthorized copying, distribution, or use is strictly prohibited.
// 未经授权，禁止复制、分发或使用本文件。

import AppKit
import Foundation

enum ObservationError: Error {
    case noScreens
    case nonIntegralGeometry
    case invalidGeometry
}

func exactInteger(_ value: CGFloat) throws -> Int {
    guard value.isFinite, value.rounded() == value else {
        throw ObservationError.nonIntegralGeometry
    }
    let integer = Int(value)
    guard integer >= -1_000_000, integer <= 1_000_000 else {
        throw ObservationError.invalidGeometry
    }
    return integer
}

func rectangle(_ rect: NSRect) throws -> [String: Int] {
    let width = try exactInteger(rect.width)
    let height = try exactInteger(rect.height)
    guard width > 0, height > 0 else {
        throw ObservationError.invalidGeometry
    }
    return [
        "x": try exactInteger(rect.origin.x),
        "y": try exactInteger(rect.origin.y),
        "width": width,
        "height": height,
    ]
}

do {
    let screens = NSScreen.screens
    guard !screens.isEmpty else {
        throw ObservationError.noScreens
    }
    var displays: [[String: Any]] = []
    for (index, screen) in screens.enumerated() {
        displays.append([
            "frame": try rectangle(screen.frame),
            "visible_frame": try rectangle(screen.visibleFrame),
            "is_primary": index == 0,
        ])
    }
    let payload: [String: Any] = [
        "schema_version": 1,
        "displays": displays,
    ]
    let data = try JSONSerialization.data(
        withJSONObject: payload,
        options: [.sortedKeys]
    )
    guard let output = String(data: data, encoding: .utf8) else {
        throw ObservationError.invalidGeometry
    }
    print(output)
} catch {
    FileHandle.standardError.write(
        Data("window-topology-observation blocked\n".utf8)
    )
    exit(1)
}

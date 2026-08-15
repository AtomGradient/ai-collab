# macOS Host Install Feasibility

Gate：`SPIKE-HOST-001`

目标：在正式 App/Host 产品实现之前，证明当前用户级 macOS Host 可以使用 `SMAppService` 打包、签名、注册、由 launchd 监督并完整注销，不使用 root，不依赖 App 控制进程持续存活。

## 真实 witness

verifier 在 private disposable install root 中构建一个无 GUI 的临时 App bundle：

- `Contents/MacOS/AIHostInstallSpike`：只负责 `status/register/unregister` 的薄控制器；
- `Contents/Resources/AIHostInstallSpikeAgent`：独立 Host helper executable；
- `Contents/Library/LaunchAgents/*.plist`：使用 bundle-relative `BundleProgram` 指向 helper；
- App 与 helper 使用本机稳定 code-signing identity 签名，证书名称和 Team ID 不写入 receipt；
- `SMAppService.agent(plistName:)` 注册后，要求 status 为 `enabled`，并要求 helper 真正写出 ready marker；
- ready marker 的 PID 必须与 launchd job PID 和内核 `proc_pidpath` 观察到的 helper 一致，UID/EUID 必须为当前非 root 用户；
- 控制器退出后 helper 仍然存活；对已验证身份的 helper 发送 `SIGKILL`，launchd 必须以新 PID 重启它；
- 使用 `unregister(completionHandler:)` 等待系统完成注销，再验证进程消失、launchd job 消失并将临时 install root 移入 Trash。

`register()` 返回成功或 status 显示 `enabled` 不能单独作为通过证据；必须继续证明真实 spawn、identity binding 和 cleanup。

## Phase -1 发现的系统边界

- `SMAppService` 管理的 App 必须签名；
- 对同一 bundle identity 反复使用不稳定 ad-hoc 签名重建，可以触发 launchd Lightweight Code Requirement constraint；
- LaunchAgent 应是 App bundle 中的独立 helper，不把 App 控制器当成 Host helper；
- 同步 `unregister()` 返回不表示运行中进程已经被回收；需要完成回调后才能安全重签/重注册；
- macOS 可能在注销后暂时保留 System Settings 中的 disabled display record，这不能被解释为运行中 launchd job。

verifier 如遇 `requires_approval` 或签名缺失会 fail closed，不自动打开 System Settings，不代替用户扩大权限。

## 明确不证明

本 spike 不构建可见 macOS App UI，不证明 Developer ID 发布、notarization、签名轮换或升级迁移，不执行登出/重启持久性实验，不验证 XPC、iTerm2、真实 runtime、delivery 或 resume。这些由后续独立 gate 证明。

## 固定 commit 后运行

```bash
python scripts/verify_ai_collab_host_install_spike.py \
  --expected-edgestudio-sha <40-char-pushed-sha>
```

日常 contract 测试不会注册系统后台服务：

```bash
python -m pytest tests/test_ai_collab_host_install_spike.py -q
```

官方接口依据：

- [Apple SMAppService](https://developer.apple.com/documentation/servicemanagement/smappservice)；
- [Apple register()](https://developer.apple.com/documentation/servicemanagement/smappservice/register%28%29)；
- [Apple 的 GUI-less LaunchAgent sample](https://developer.apple.com/documentation/ServiceManagement/updating-your-app-package-installer-to-use-the-new-service-management-api)。

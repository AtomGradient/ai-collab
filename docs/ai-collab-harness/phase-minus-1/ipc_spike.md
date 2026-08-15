# macOS Typed IPC Feasibility

Gate：`SPIKE-IPC-001`

目标：在正式 Host/App/CLI 产品实现之前，证明当前用户级 launchd Host 可以通过 Mach service 暴露 typed NSXPC contract，并在真实跨进程 request/reply 中同时执行 peer identity、request schema、operation allowlist、scenario capability 与 generation fencing；不得把“XPC 能连通”单独解释为 IPC 架构成立。

## 真实 witness

verifier 在 private disposable install root 中构建一个稳定签名的临时 App bundle：

- `Contents/MacOS/AIIPCSpike`：只负责 `status/register/unregister` 的薄控制器；
- `Contents/Resources/AIIPCSpikeAgent`：由 `SMAppService` 注册、launchd 监督并持有 Mach service 的独立 Host helper；
- `Contents/Resources/AIIPCSpikeClient`：合法 typed NSXPC client；
- `Contents/Resources/AIIPCSpikeImpostor`：使用同一稳定开发证书、不同 signing identifier 的负向 client；
- LaunchAgent plist 使用 `MachServices` 声明固定 service name，App/agent/client/impostor 分别拥有独立 code identity。

Host listener 在激活前设置精确 client code-signing requirement；client 在激活前设置精确 Host code-signing requirement。requirement 绑定 signing identifier 与 certificate leaf，原始证书引用、证书名称和 Team ID 不写入 receipt，只记录 requirement 与 identity 的 SHA-256。

合法 client 必须完成带随机 nonce 的真实 request/reply，随后依次证明：

1. 不支持的 request schema 返回 `unsupported_schema`；
2. 非 allowlist operation（负向样例为 `shell.exec`）返回 `operation_not_allowed`，Host 不执行 shell/argv；
3. 错误 scenario 返回 `scenario_not_found`；
4. 错误 opaque capability 返回 `capability_denied`；
5. 旧 generation 返回 `stale_generation`；
6. 同证书、不同 identifier 的 impostor 无法取得 reply，且合法 Host 仍保持精确 process binding；
7. 合法 client 使用错误 Host identifier requirement 时，必须观察到 code-signing requirement failure；
8. async unregister 后，进程、launchd job 与 dedicated install root 全部消失。

listener delegate 校验连接进程的 effective UID 为当前非 root 用户；exported method 使用 `NSXPCConnection.current()` 再校验一次调用方 owner。跨 UID 负向连接需要额外用户/权限，本 spike 不扩大权限来制造该实验。

IPC verifier 复用 `SPIKE-HOST-001` 的正式 lifecycle evidence，但不只检查旧 current view 的 `passed`：它会在当前 checkout 重新计算 Host producer、Swift source、bootstrap support 与 gate registry digest，核对当前 macOS/Swift/SDK/Xcode observation、LEGACY/SCOPE dependency evidence，并要求 Host 实现 commit 仍在当前 pushed history 中。无关文档或后续 gate commit 不强制重签 Host evidence；任何 Host material、平台或依赖链变化都会 fail closed，要求先重跑 Host gate。

## Contract 边界

- transport 是 macOS platform 实现，不是产品内核对其他平台的写死依赖；
- v1 typed protocol 只暴露 `status` witness，不冻结正式产品 API；
- Host 接收 scalar typed fields，不接收任意 shell、argv 或未声明 payload；
- capability 使用本次 probe 的随机 opaque value，只证明 mismatch 会 fail closed，不证明正式 capability 的签发、保存、轮换或撤销；
- code-signing identity 建立本地二进制信任边界，不能替代 scenario capability 与 generation fencing；
- 精确复制合法签名 client 的攻击者仍属于受信分发边界，正式发布必须结合 hardened runtime、notarization、安装与升级策略。

## 明确不证明

本 spike 不证明 Developer ID 发布/notarization、App Sandbox/hardened runtime、跨用户拒绝实跑、证书轮换、并发 client/backpressure、in-flight crash recovery、正式 capability issuance、可见 App UI、iTerm2、真实 runtime、delivery、window topology 或 upgrade。这些仍由后续独立 gate 证明。

## 固定 commit 后运行

```bash
python scripts/verify_ai_collab_ipc_spike.py \
  --expected-edgestudio-sha <40-char-pushed-sha>
```

日常 contract 测试不会注册系统后台服务：

```bash
python -m pytest tests/test_ai_collab_ipc_spike.py -q
```

官方接口依据：

- [Apple NSXPCConnection](https://developer.apple.com/documentation/foundation/nsxpcconnection)：双向进程通信与 typed interface；
- [Apple init(machServiceName:options:)](https://developer.apple.com/documentation/foundation/nsxpcconnection/init%28machservicename%3Aoptions%3A%29)：连接 launchd plist 声明的 LaunchAgent Mach service；
- [Apple NSXPCListener setConnectionCodeSigningRequirement](https://developer.apple.com/documentation/foundation/nsxpclistener/setconnectioncodesigningrequirement%28_%3A%29)：listener 对 client 施加 code-signing requirement；
- [Apple NSXPCConnection setCodeSigningRequirement](https://developer.apple.com/documentation/foundation/nsxpcconnection/setcodesigningrequirement%28_%3A%29)：client 对 Host 施加 code-signing requirement。

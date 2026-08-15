# iTerm2 Presentation Driver Feasibility

Gate：`SPIKE-ITERM-001`

目标：在正式 macOS App/Host presentation driver 落地前，使用 iTerm2 官方 Python API 证明动态 participant plan 可以建立一对一的顶层窗口 binding；不得用同一窗口的 tab 或 split pane 冒充独立窗口，也不得为 headless participant 创建占位窗口。

## 真实 witness

固定 verifier plan 声明四个 vendor-neutral participant ref：三个 `interaction_mode=tui`、一个 `interaction_mode=headless`。三个交互 participant 只通过 `iterm2.Window.async_create(...)` 创建窗口，并逐一证明：

1. 返回三个互不相同、创建前不存在的 top-level `window_id`；
2. 每个 owned window 恰好包含一个 tab、一个可见且非 minimized session；
3. window/tab/session identity 一对一，且可由 `App.get_window_by_id()` 稳定回查；
4. 每个 owned window 写入并读回本次 probe 的 opaque ownership marker；
5. headless participant 没有 window binding，API create 调用数严格等于交互 participant 数；
6. probe 前至少有一个用户已打开的窗口，并在创建和清理后仍保持存在；
7. 清理只关闭本次创建且 ownership 可证明的窗口，最后 owned window 为零。

真实 window/tab/session ID 和 ownership token 只在 disposable adapter 进程内使用；receipt 只保存 identity SHA-256、基数和布尔 witness。窗口使用固定的 inert `/usr/bin/tail -f /dev/null` session command，避免加载用户 shell 配置；它不代表真实 runtime/TUI。

三个运行中窗口是用户批准的 Phase -1 witness 预算，不是产品 participant 上限。driver 逻辑按 plan 中的交互 participant 动态迭代，headless-only plan 的 contract 测试证明零新增窗口路径。正式 probe 要求至少一个用户已打开的既有窗口，只是为了让“未误伤既有窗口”成为非空 witness，并避免关闭最后一个 owned window 时受用户的 quit-on-close 偏好影响；这不是产品运行时必须已有窗口的规则。

## API、依赖与权限边界

- transport 只使用 iTerm2 官方 Python API；不调用 `async_create_tab`、split pane、tmux tab 或 tab relocation API；
- configured App 必须匹配 iTerm2 bundle identifier、稳定签名 team reference 与 Gatekeeper assessment；receipt 仅保存签名 reference/requirement digest，不保存证书名称或原始 Team ID；
- adapter 环境在 private disposable root 中创建，按 [ai_collab_iterm_adapter_lock.json](../../../scripts/ai_collab_iterm_adapter_lock.json) 下载并校验三个精确 wheel，安装后执行 dependency check，结束时移入 Trash；
- 锁定的 PyPI `iterm2` package 只用于本次内部 disposable spike；这不等于批准把它随正式产品分发，产品化前必须单独完成许可证与分发方式审查；
- verifier 与真正连接 iTerm2 的 disposable adapter 进程都会各自以 `AEDeterminePermissionToAutomateTarget(..., askUserIfNeeded=false)` 只读探测 Automation/TCC，绝不触发授权提示；
- verifier 和 adapter 都拒绝 iTerm2 的 unauthenticated API bypass，要求 cookie authentication 仍生效；不创建、删除或修改相关用户配置；
- adapter 连接前必须证明 iTerm2 private API endpoint 是当前用户拥有的 Unix socket。运行环境移除 `IT2_SUITE` 与所有 proxy routing override，并把 `NO_PROXY` 固定为 loopback；不允许 package 回退到 TCP transport。当前锁定依赖的 legacy client 没有已知 proxy 路径，这些检查仍作为防未来依赖升级回归的结构性护栏；
- verifier 不使用 Accessibility，不打开系统设置，不执行 `defaults write`，也不切换 iTerm2 的 Python API 设置；
- iTerm2 必须已由用户启动、用户已经启用 Python API，且至少保留一个用户打开的既有窗口。任一条件不满足均 fail closed，不自动启动 App，不修改用户窗口或权限；
- normal run 还会重算 Scope、Runtime Driver、IPC/Host 的当前 producer/source/support/registry/platform/dependency chain，避免把旧 receipt 的 `passed` 当成当前实现兼容性。

不写 receipt 的只读 preflight：

```bash
python scripts/verify_ai_collab_iterm_spike.py --preflight-only
```

固定 commit 后运行正式 verifier：

```bash
python scripts/verify_ai_collab_iterm_spike.py \
  --expected-edgestudio-sha <40-char-pushed-sha>
```

日常 contract 测试不会连接 iTerm2、创建窗口、下载 wheel 或改动权限：

```bash
python -m pytest tests/test_ai_collab_iterm_spike.py -q
```

## 明确不证明

本 spike 不证明 iTerm2 的自动启动/退出、真实 runtime/TUI process identity、TTY continuity、resume/recreate、delivery ACK、窗口 geometry/多显示器/Spaces restore、强制 kill 时的 crash recovery、正式 App UI、发布/notarization 或跨平台 presentation adapter；`Window.async_create()` 可能带来的窗口前置/焦点副作用也不在本 gate 的测量范围。如果 create 请求在返回 window identity 前超时，远端创建是否已发生可能暂时不明确，本 gate 不会凭猜测关闭窗口，可能需要后续 close gate 或人工恢复。上述能力仍由 `SPIKE-TUI-ID-001`、`SPIKE-TUI-LIFE-001`、`SPIKE-DELIVERY-001`、`SPIKE-WINDOW-TOPOLOGY-001`、`SPIKE-CLOSE-001`、`SPIKE-UPGRADE-001` 及后续实现证明。

官方接口依据：

- [iTerm2 Python API Security](https://iterm2.com/python-api-auth.html)：Python API 默认关闭，外部程序通过 Automation 取得 cookie；
- [PyPI iterm2](https://pypi.org/project/iterm2/)：本 spike 使用的官方 Python interface package 与其公开 package metadata；
- [iTerm2 Window](https://iterm2.com/python-api/window.html)：`Window.async_create()` 创建顶层窗口，`window_id` 是唯一标识，`async_close()` 关闭窗口；
- [iTerm2 App](https://iterm2.com/python-api/app.html)：`App.windows` 与 `get_window_by_id()` 提供稳定回查；
- [iTerm2 Tab](https://iterm2.com/python-api/tab.html)：`Tab.sessions` 表示该 tab 的 split panes；
- [Apple AEDeterminePermissionToAutomateTarget](https://developer.apple.com/documentation/coreservices/3025784-aedeterminepermissiontoautomatet)：以 `askUserIfNeeded=false` 探测 Automation 权限而不弹窗。

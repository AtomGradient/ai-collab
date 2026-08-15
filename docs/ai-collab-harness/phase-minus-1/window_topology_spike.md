# Window / Display Topology Feasibility

Gate：`SPIKE-WINDOW-TOPOLOGY-001`

目标：在正式 Host geometry persistence 与 acceptance layout policy 之前，证明公开 macOS display geometry 可以形成不含硬件身份的稳定 topology fingerprint，真实 disposable iTerm 顶层窗口可以按精确整数 Frame capture / move / restore，geometry 与 presentation identity 相互独立，并且 topology change、单窗失败与动态 participant 数量可以由 vendor-neutral pure core fail closed 地 reconcile。

## 用户裁决

来源：`user_decision`，2026-08-10。用户在 Codex 与 Claude 完成证据复核并共同推荐后明确指示“继续”，批准本 gate 使用以下 Phase -1 语义；落地 commit body 记录同一裁决摘要：

- hybrid witness：真实当前 topology、真实 disposable iTerm geometry round-trip，加 deterministic synthetic topology-change reconcile；
- direct dependencies 仅为 `SPIKE-ITERM-001` 与 `SPIKE-TUI-ID-001`，同时验证它们已有的 transitive chain；
- geometry key 的 Phase -1 witness 使用 opaque ephemeral `machine_ref`，不把 `SPIKE-HOST-001` 没有证明的 durable `machine_id` 冒充为既有能力；
- fingerprint 只含规范化 display count、相对逻辑 frame、visible frame、primary marker 与有效逻辑分辨率，不含 EDID、序列号、型号、名称、raw display UUID 或 backing scale factor；
- iTerm restore 先要求捕获到的整数 Frame 精确 readback，不预设无实测依据的 tolerance；
- topology change 中无法安全映射的窗口必须为 `unplaced/requires_user_decision`，不得静默移到 primary display；
- Phase -1 明确排除 Spaces/private virtual-desktop API 与物理 display 拔插、重排实验。

## 真实 witness

verifier 只通过公开 `NSScreen.screens`、`frame` 与 `visibleFrame` 读取当前 display arrangement。单独的 Swift observer 不读取 `deviceDescription`、`NSScreenNumber`、ColorSync、IOKit display identity、EDID、serial/model、raw display UUID、backing scale factor 或 private Spaces API。producer 将 primary origin 归一化、对 arrangement descriptors 排序并计算 SHA-256；receipt 只保留 fingerprint 与计数，不保存当前机器的 raw topology。

真实 window 路径复用 source-fresh `SPIKE-ITERM-001` 的 pinned official Python adapter 与无提示安全预检：

1. 记录非空 preexisting iTerm window 集合；
2. 只为 declared interactive witness 创建 disposable 顶层 window，headless participant 不创建窗口；
3. 通过 ownership marker 绑定唯一 owned window，并以与 `SPIKE-TUI-ID-001` 相同的 window/tab/session fingerprint namespace 计算 presentation identity；
4. 使用 `Window.async_get_frame()` 捕获整数 Frame，写入 private disposable geometry store；
5. 使用 `Window.async_set_frame(Frame)` 移到同一 observed display 内的不同安全 Frame，再恢复捕获值；
6. 要求 restore 后 exact integer Frame readback 相等，presentation identity 在 move/restore 前后不变；
7. 只关闭 ownership 已验证的 disposable window，preexisting windows 必须在 probe 与 cleanup 后仍存在。

geometry store root 使用 `0700`、文件使用 `0600`，以 `(ephemeral_machine_ref, display_topology_fingerprint)` 作为精确 composite key；wrong machine ref 与 wrong topology fingerprint 都必须 fail closed。store 和 pinned adapter environment 在 witness 结束后移入 Trash，不进入 Git receipt。

## Deterministic synthetic reconcile

pure core 只接受 `{x, y, width, height}`、arrangement-derived display refs、presentation identity hash 与 opaque participant ref，不读取 iTerm、runtime vendor 或 machine hardware identity。

- 同 topology 生成 `restore_exact` plan；
- topology 变化后，只对 arrangement descriptor 仍精确存在的 display 保留 `restore_exact`；
- 被移除、移动或分辨率变化而无法精确匹配的 display 上窗口标记为 `unplaced/requires_user_decision`；
- 不使用 primary fallback、最近窗口、index 或 product-name heuristic；
- apply 循环逐窗隔离失败，一个 synthetic window failure 不阻塞 sibling restore；
- 1、2、N records 使用同一循环，witness record count 不是产品基数上限。

## 依赖与证据边界

正式 receipt 的 direct dependency keys 必须精确为：

- `SPIKE-ITERM-001`：官方 iTerm window adapter、安全 preflight、preexisting preservation 与 owned cleanup；
- `SPIKE-TUI-ID-001`：geometry 与 presentation identity 分层，以及 exact presentation fingerprint contract。

verifier 仍穿透验证 ITERM/TUI-ID receipt 的 source material、checkout ancestor、fingerprint 与既有 transitive evidence chain。`SPIKE-HOST-001` / `SPIKE-IPC-001` 因传递链可被复核，但不重复写成 topology direct dependencies；`SPIKE-TUI-LIFE-001`、`SPIKE-DELIVERY-001` 与 `LEGACY-DELIVERY-001` 不进入本 gate target dependency set。

## 明确不证明

本 spike 不证明 durable architecture `machine_id` 的生成/持久化、真实物理 display 拔插或重排、Spaces/virtual desktop、mirrored indistinguishable display 的设备级连续性、Host/App restart 后 geometry recovery、完整 N-window layout/conflict policy、跨机器 geometry migration、性能或 acceptance UX。opaque ephemeral `machine_ref` 只证明 composite-key isolation，不能替代 [product architecture](../product_architecture.md) 中正式 `(machine_id, display_topology_fingerprint)` contract。

## 正式机器 witness 与 closeout

固定实现 `85b220a67c04c3fefceb392f69c02b50125c3771` 已 push，Claude implementation review `20260810-143530-f9bwqr` 给出 P0=0、P1=0、`can_commit_push`，并独立杀死 6/6 producer/verifier mutation；三个 P2 是 structural mutation auto-discovery、producer literal disclosure 与 fake polling coverage，均不改变本次 hard claim 或正式 witness 准入。

正式 run `spike-window-topology-20260810T073520Z-202fe1f36103` 已通过：evidence `c02d7545620380c9689422b1761f01fa65824225a1f9fbf0741f97423cb0169a`，input fingerprint `202fe1f361039dc0d0d222ba1100c2e209388bc157f85a9874ca6859043e1a69`。独立复核确认：

- receipt checkout / tree / remote 精确锚定 fixed target；current/evidence digest 与 canonical input fingerprint 重算相等；producer、params、verifier material digests 全部匹配当前 source；
- state root、current view、immutable evidence 权限依次为 `0700`、`0600`、`0600`；direct dependency keys 精确为 `SPIKE-ITERM-001` 与 `SPIKE-TUI-ID-001`，digest 均与当前 view 相等；
- 当前真实 display count 为 2，只保留 arrangement fingerprint；1 个 real disposable iTerm window 完成 changed-frame 与 exact restore，original/restored frame digest 相等，presentation identity 未改变，tolerance 未使用；
- synthetic reconcile 为 3 records：2 个 exact restore、1 个 `unplaced/requires_user_decision`、0 primary fallback；single-failure witness 为 1 failed + 2 restored siblings；
- geometry store `0700/0600` 且已移除；1 个 owned window closed、0 refusal/error/remaining，3 个 preexisting windows 在 probe 与 cleanup 后均保留；spikes root 为空；receipt 未出现 raw window/tab/session/machine ref 或本机绝对路径。

closeout 条件 1–5 由 ledger target `7f3097f8ce5ccdf18edd9634192d91aeeea02f06` 固定：用户 scope、fixed pushed implementation、P0/P1 清零 implementation review、正式 machine receipt、独立 digest/permission/cleanup 复核与 tracked ledger 均具备。Claude closeout review `20260810-144530-h2nwqp` 进一步独立重算 evidence/fingerprint、权限、dependency set、checkout 与 limitations，给出 P0=0、P1=0、P2=0、`can_commit_push`，明确条件 6 成立。`SPIKE-WINDOW-TOPOLOGY-001` 状态为 `completed`，下一 required gate 是 `SPIKE-STORAGE-001`；本结论不外推 storage、close 或 upgrade feasibility。

公开接口依据：

- [Apple NSScreen screens](https://developer.apple.com/documentation/AppKit/NSScreen/screens)、[frame](https://developer.apple.com/documentation/appkit/nsscreen/frame) 与 [visibleFrame](https://developer.apple.com/documentation/appkit/nsscreen/visibleframe)；
- [iTerm2 Window API](https://iterm2.com/python-api/window.html) 的 `async_get_frame()` / `async_set_frame()`；
- [iTerm2 Frame / Point / Size](https://iterm2.com/python-api/util.html)。

## 固定 commit 后运行

只做 read-only preflight，不创建 window：

```bash
python scripts/verify_ai_collab_window_topology_spike.py --preflight-only
```

正式 witness：

```bash
python scripts/verify_ai_collab_window_topology_spike.py \
  --expected-edgestudio-sha <40-char-pushed-sha>
```

纯 contract / fake-adapter tests 不创建真实窗口：

```bash
python -m pytest tests/test_ai_collab_window_topology_spike.py -q
```

# Harness 实施前现场封存

> 状态：Stage 0 `recoverable_snapshot_verified`；可防本次重构误操作，不覆盖整块数据卷损坏
>
> 用户决定：2026-08-06，在 Harness 实现代码开始前完成当前现场 snapshot 与恢复验证。

本目录保存不含 secret、PII、完整 session identity 或私有 payload 的机器可读基线。它用于回答“实施前各仓库和设计输入是什么”，不保存大文件归档本身，也不把同盘目录副本误称为备份。

## 当前基线

- [pre_implementation_snapshot_20260806.yaml](pre_implementation_snapshot_20260806.yaml)：15 个受管仓库的 exact SHA vector、设计输入 digest、工具版本、ignored 数据容量与备份完成条件。
- EdgeStudio 的 `captured_source_head` 是 Harness v3.1 设计评审通过后的 commit；记录本文件的 commit 只增加 snapshot metadata，不改变产品实现代码。
- 工作区 sibling `HarnessPreImplementationSnapshot-20260806` 保存 15 个 verified Git bundle、9 个非 Git archive、SHA256SUMS 和非 canonical restore drill receipt；它不进入 Git。

## 两阶段状态

1. `inventory_captured`：所有受管仓库 clean、upstream 同步、非 shallow、无 alternates；设计输入和非 Git 数据范围已盘点。
2. `recoverable_snapshot_verified`：Git 离线恢复包和经过筛选、受 FileVault/ACL 保护的非 Git 归档已写入工作区外的明确目的地，生成 checksum，并在临时目录完成恢复演练。

第二阶段已于 2026-08-06 完成：15/15 Git exact-SHA restore、9/9 非 Git archive extract、25,133 个筛选 ignored 文件和 `.ai-mailbox/refs` 的 696,506 个本地证据文件均完成 source/restore 内容 digest 对比，45 个持久 artifact checksum 全部通过。由于目的地与 source 位于同一 FileVault data volume，本 snapshot 只承诺防重构或 workspace 误损，不能宣称抵御整卷损坏。

## 数据边界

- `.ai-mailbox/inbox|sent|outbox` 等协作 transport、`.venv/`、cache、dist 和普通日志不进入长期现场归档；
- `.ai-mailbox/refs` 不是纯 transport，包含可能唯一的 device/eval/receipt 现场证据，已作为 sensitive local evidence 整体封存；只做自动 hash，不人工读取或把文件名写进 tracked receipt；
- `RealData/`、设备测试原始结果和 ignored 文档先做 private/reproducible 分类；需要保留的内容只进入 archive-level encryption 或 FileVault + `0700` ACL 保护的目录；
- `.env*`、SMTP credential、waitlist PII、Keychain、token 和完整 runtime/session identity 禁止写入 manifest、Git bundle 清单或 tracked receipt；
- unmanaged repo 可以另行备份，但不因现场封存自动进入 Harness manifest 或 acceptance scope。

## 恢复完成条件

- 15 个 Git repository bundle/remote 均可恢复到记录的 exact SHA；
- Git LFS、submodule、shallow 和 alternates 状态均经过显式检查；
- 非 Git 归档 checksum 通过，抽样文件可恢复；
- 恢复目录不覆盖 canonical workspace；
- snapshot receipt 只记录 logical path、digest、大小和结果，不记录敏感正文。

[English](README.md) | 简体中文

# AI Collab

macOS 上的本地多 AI 任务房间协作套件。把任何 Git 项目变成可长期保存的
任务房间：多位 AI 同事在各自隔离的工作区里干活，并通过一个类型化、可审计
的 Host（后台服务）相互通信。界面支持中英文（设置 → 通用 → 语言），并全面
使用任务房间词汇：Scenario 在界面里叫**任务房间**，Participant 叫 **AI 同事**。

博客文章：[AI Collab — 开源多 AI 协作](https://www.atomgradient.com/zh/blog/ai-collab-open-source-multi-ai-collaboration)
（[English](https://www.atomgradient.com/en/blog/ai-collab-open-source-multi-ai-collaboration)）

![AI Collab App：一个运行中的任务房间、两位 AI 同事、协作规则与投递日志](ai-collab.png)

四位 AI 同事分属两个任务房间，互发消息、互不干扰：

![两个任务房间的四位 AI 同事 TUI 与 App 并排，交换类型化投递](ai-collab-with-tuis.png)

## 快速开始

1. 从 [Releases](../../releases) 下载 `AICollab.dmg`，拖入「应用程序」。
   发布版已签名并完成 Apple 公证，可直接打开。
2. 安装 [iTerm2](https://iterm2.com) —— Agent 运行在由 Host 拥有并可恢复的
   iTerm2 窗口里。然后在 iTerm2 打开 Settings -> General -> Magic，
   启用 **Python API** 并重启 iTerm2。等效命令是：
   `defaults write com.googlecode.iterm2 EnableAPIServer -bool true` 和
   `defaults write com.googlecode.iterm2 NoSyncEnableAPIServer -bool true`。
3. 点击 **注册项目**，选择任意 Git 目录。无配置文件的新项目和旧版
   AI Collab 声明都能直接注册；注册不会写入所选 checkout。
4. 创建一个任务房间，点击 **准备工作区**。在这次明确授权后，Host
   会自动 clone 缺失仓库、checkout 精确版本并验证隔离工作区，界面按仓库逐行
   显示进度；凭据、网络、浅克隆、分支和磁盘错误都会保留精确分型与修复说明；
   只有瞬态错误才提供立即重试。
5. 添加 AI 同事（Codex 与 Claude CLI 的配置开箱即用）。加错了？先停止，
   再从它的 ⋯ 菜单删除。
6. 点 **恢复**——新建的房间处于休会状态，协作策略只有在房间打开后才能
   预览和应用。然后在协作规则区选团队模板，**预览方案** → **应用方案**，
   再点 **全部启动**。同事进入工作后，聚焦任一窗口直接布置任务——此后它
   们即可互发消息，每位同事都持有 Host 签发的专属 `ai-ping` 命令。居中的
   引导卡片会陪你走完这些步骤，随时可从工具栏 **?** 叫回。

## 工作原理

- **Host** 是唯一权威：身份、隔离工作区、消息投递、生命周期、恢复与权限
  全部经过它。App 和 CLI（`ai-collab harness …`）只是同一个 Host 的两个
  入口。
- 简单 Git 根仓不需要项目配置。需要稳定多仓意图的团队可追踪
  `.aicollab/project.yaml`；它只保存项目语义，不保存 AICollab runtime 或
  adapter 版本。旧 `project_descriptor.yaml` / `repo_manifest.yaml` 仍可读取，
  但 AICollab 不再生成或重写它们。
- Host 会在私有状态中保存解析后的 runtime contract，并把完整快照复制进每个
  新 Scenario。App 在启动、选择项目、
  Workspace 准备完成后或手动刷新时，侦测缺失、未声明和漂移仓库；涉及语义
  的变化必须由用户点击 **Apply project update**；工具自身兼容 pin 会自动刷新，
  但升级永远不会改写已有 Scenario 的自包含契约。
- `.aicollab/project.yaml` 的 schema 与升级行为见
  [Project intent and zero-touch onboarding](docs/project-intent.md)。
- 每个 Scenario 都会拿到所声明仓库的精确版本克隆和一个绑定的 Python
  环境，带漂移检测、保留 WIP 的修复和 fail-closed 销毁。

## 自定义

- **Agent 启动参数 / 其他 CLI**：把完整的 profile 行写入
  `~/Library/Application Support/AI Collab/runtime_profiles.overlay.json`。
  行按 `profile_id` 替换内置 profile 或新增，校验标准与内置注册表完全
  一致。（内置的 Codex/Claude profile 有意跳过审批提示 —— 为了无人值守
  协作。）
- **Adapter**：在 `~/Library/Application Support/AI Collab/` 放同名配置
  即可替换内置版本（`ai_collab_harness_adapter.json`、
  `ai_collab_participant_driver.json`、`ai_collab_security_adapter.json`）。
  配置内的路径只能相对配置所在目录解析 —— 正是这条限制防止 Host 被指向
  任意程序。
- **自己写项目 adapter**：机器可读的契约在 `contracts/`；
  `scripts/ai_collab_project_adapter.py` 是一份完整的参考实现。

## 从源码构建

需要 Python 3.11+；构建 App 还需要 Xcode 命令行工具、`xcodegen` 和一个
代码签名身份：

```bash
git clone https://github.com/AtomGradient/ai-collab.git && cd ai-collab
python3 -m venv .venv && .venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest                          # 全量测试，无需网络
.venv/bin/python scripts/build_ai_collab_app.py \
  --output /tmp/AICollab.app --dmg /tmp/AICollab.dmg
```

MIT 许可。仅支持 macOS。

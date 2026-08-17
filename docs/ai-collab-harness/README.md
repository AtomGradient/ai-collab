# AI Collaboration Scenario Harness 文档入口

> 文档版本：v3.3
>
> 更新日期：2026-08-17

本目录只维护**通用产品**文档。接入某个具体项目所需的集成设计、gate registry、实施账本、能力对齐表和一次性可行性实验记录，都属于那个项目自己的仓库，不放在这里 —— 否则项目细节会反向定义产品内核。

## 阅读入口

1. [product_architecture.md](product_architecture.md)：通用产品真相源。定义 Scenario、Participant、Runtime Driver、Presentation Driver、Collaboration Policy、delivery、状态机、安全边界和产品 conformance。不得包含任何具体项目的仓库清单、语言环境或组织规则。
2. [contracts/README.md](contracts/README.md)：machine-readable contracts —— Host logical IPC v1、participant driver suite v2、Scenario/Participant state v1、Collaboration Policy/reliable-delivery v1、Workspace/Environment v1、gate registry v2 projection contract、permission/high-risk confirmation v1。core 只依赖这些版本化 contract。
3. [decisions/README.md](decisions/README.md)：用户裁决的结构化记录（Gate 0 scope、registry normalization/cutover 边界）。
4. [EMPLOYEE_GUIDE.md](EMPLOYEE_GUIDE.md)：员工使用指南；覆盖安装之后的项目登记、双 Agent Scenario、Agent 之间协作、并行隔离、关闭恢复与故障处理。它假设 App 已经装好，安装本身还没有员工文档。

## 真相源边界

| 问题 | 真相源 |
|---|---|
| Scenario/Participant/Runtime/Presentation 分层 | `product_architecture.md` |
| Host、CLI、App、driver SDK、delivery、gate engine | `product_architecture.md` |
| core 与 adapter 之间的接口 | `contracts/` |
| 某个项目的仓库、路径、Git、语言环境 | 那个项目自己的集成文档 |
| 某个项目当前的 gate 集合与 workflow phase | 那个项目自己 tracked 的 composed gate registry |
| 实施进度、fixed SHA、peer review 记录 | 那个项目自己的实施账本 |

集成层只能通过版本化 contract 使用产品能力。另一家公司接入时只新增自己的 project descriptor、workspace/environment adapter、policy pack、runtime/presentation plugin 与 acceptance registry；不修改 Host 核心来加入公司名、仓库名或工具特例。

## 已知缺口

- `product_architecture.md` §14.1「当前交付节奏」目前仍混着某个具体项目的实施账本（fixed SHA、仓库名、review 记录）和真正属于产品的设计原则（例如 crash-reconciliation 的持久化顺序）。两者需要分开：原则留在这里，账本回到项目仓。在那之前，这一节不能当作 project-neutral 内容看待。
- 安装、升级、卸载和首次启动的系统授权流程没有员工文档；`EMPLOYEE_GUIDE.md` 从「App 已经装好」开始。

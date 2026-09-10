# 设计与实现文档

软件开发工作台的设计方案与实现细节。使用说明在仓库根的 [README.md](../README.md)，给 Claude 的操作约定在 [CLAUDE.md](../CLAUDE.md)。

**这些文档记的是「为什么这样设计、取舍是什么、已知边界在哪」，不是 API 参考。** 具体行为以 `wb.py` 与 `wb.py selfcheck` 的断言为准 —— 抄一份到散文里只会造出一份会漂移的副本。用法在 `CLAUDE.md` 与 `.claude/skills/` 里。

本目录只保留**当前架构设计参考**。评估、调研、平台对齐、历史方案与逐日评审等过程文档移到了仓库根的 [`draft/`](../draft/)，见文末。

| 文档 | 内容 |
| --- | --- |
| [architecture.md](architecture.md) | 分层、状态模型、外层唯一状态布局、数据流、设计取舍、已知边界与升级路径 |
| [roles.md](roles.md) | 八个角色的矩阵与写入范围、协作协议、交接格式、定制与新增 |
| [gates.md](gates.md) | 门禁引擎：八种断言（加 `artifacts` 键共九种准出条件）的语义与实现要点、六阶段准出条件、强推边界、扩展方式 |
| [contracts.md](contracts.md) | 契约机制：哈希冻结 + 只读守卫、申报窗口、生命周期、`bump` 的影响面传播、失效模式 |
| [permissions.md](permissions.md) | 权限模型：四层拦截、Bash 绕过检查、wb.py 特权子命令层、危险命令分级、hook 载荷与失败语义 |
| [scheduling.md](scheduling.md) | 调度与 loop：就绪集合、并行派发协议、任务生命周期与租约、产物归属、停止条件与防失控 |
| [wb-init.md](wb-init.md) | 多仓库初始化 skill：需求与设计取舍、清单/落点/合并策略、测试矩阵与已知边界 |

**任何文档与代码冲突时以代码为准。**

## draft/：过程文档（非当前架构参考）

评估、调研、平台对齐、历史方案与逐日评审记录 —— 记的是「某个时点做过什么判断、为什么这么改」，不是当前设计的正文。留档备查，读时以正文与代码为准。

| 文档 | 内容 |
| --- | --- |
| [review.md](../draft/review.md) | 实现评审（2026-09-01）：19 项问题的结果一览与**评审自己判错的地方**（19 项已全部处理，不是待办清单） |
| [parallel-implementation.md](../draft/parallel-implementation.md) | 并行开发改造记录（2026-09-06）：环境变量钉根 / flow 维度 / 嵌套根反查三步实现，顺带修掉的存量洞、判错复盘、遗留边界 |
| [cross-flow-review.md](../draft/cross-flow-review.md) | 跨 flow 并发评审（2026-09-06）：四处缺口（关窗死锁 / 归属串扰 / 指针竞态 / 配置丢失）的实证、修复方案、判错复盘与修复后的边界 |
| [code-review-2026-09-09.md](../draft/code-review-2026-09-09.md) | 工作区未提交改动审查（2026-09-09）：wb-init 提为 `scripts/` + TUI + 清单 + Codex 软链的 20 项已确认问题，含守卫回归、数据丢失路径与修复优先级 |
| [framework-assessment.md](../draft/framework-assessment.md) | 框架功能设计评估（2026-09-04）：已有能力盘点、主要缺口与控制面提案 |
| [codex-claude-parity-review.md](../draft/codex-claude-parity-review.md) | Codex 与 Claude 平台能力对齐审阅：身份字段、静态 profile、shell 审计等的实测与结论 |
| [codex-agent-migration.md](../draft/codex-agent-migration.md) | 迁移到 Codex 的适配层调研与方案（配置层与守卫内核均已落地）：工具名与载荷键差异、输出协议差异 |
| [roma-comparison.md](../draft/roma-comparison.md) | 与 ROMA（另一套 agent workspace 运行时）的对比：十条可借鉴项、明确不抄的、落地顺序 |
| [references-extraction.md](../draft/references-extraction.md) | ROMA `references/` 公共参考层的分析（2026-09-07）与落地记录 |
| [wbsvr.md](../draft/wbsvr.md) | **历史设计，已移除**：曾讨论的契约托管服务方案，不是当前安装或运行手册 |
| [open-issues-2026-09-10.md](../draft/open-issues-2026-09-10.md) | 待修复问题清单（2026-09-10）：汇总此前评估/审查文档、逐条核对当前代码后仍未修复的项，按优先级列出背景、证据、修复方案 —— 这一份是待办清单，其余多数不是 |

## 一页速览

工作台把软件开发的六个阶段做成**有状态、有门禁、有契约约束**的流水线。

```
需求澄清 → 现状分析 → 方案设计 → 开发实现 → 测试验证 → 总结复盘
clarify    analyze    design     develop    verify     retro
  pm       analyst   architect   fe/be dev    qa       reviewer → knowledger
```

三个机制让流程有约束力，而不只是提示词里的提醒：

1. **门禁**（gate）—— 阶段准出条件不满足，`phase advance` 退出码 1，推不动。
2. **契约**（contract）—— 接口定义、技术方案文档与过了门禁的阶段产物锁定后哈希冻结、文件转只读，改它必须先申报理由。绕过守卫的改动被门禁检出漂移。
3. **权限守卫**（`PreToolUse` hook）—— 角色越权写、写出项目根、写冻结文件（含 Bash 路径），退出码 2 阻止调用。

三者共享同一份 `.workbench/state.json`，由同一个 Python 内核（`.claude/hooks/wb.py` 入口 + 同目录 `wb_*` 模块，划分见 [architecture.md](architecture.md#内核的模块划分曾是单文件记录一次决策反转)）读写。

## 为什么需要这一层

Claude Code 原生已有 subagent、skill、hook 与工具权限。但原生机制缺四样东西，正是这套工作台补上的：

| 缺口 | 原生表现 | 本方案 |
| --- | --- | --- |
| **跨轮次状态** | 每个 subagent 独立上下文，主线程靠对话历史记进度，会话压缩后丢失 | 状态落盘 JSON，`status` 一条命令重建全貌 |
| **强制性** | 提示词写「先设计再编码」，模型大部分时候遵守 | 门禁退出码挡住阶段推进；hook 退出码挡住越权写 |
| **并行的前提** | 前后端 subagent 并行时各自猜接口，联调才发现字段名不一致 | 契约先锁定，双方对着同一份冻结定义写；漂移被门禁抓出 |
| **定稿的东西不被回改** | 方案文档与接口定义在开发中被悄悄改成实现的样子，文档与实现「永远一致」因为文档跟着实现改 | 锁定即只读，改动要先申报理由，`bump` 自动通知所有消费方 |

原生 `tools:` 白名单只能控制「能不能用 Write」，控制不了「能写哪个目录」。角色隔离必须落到 hook 上。

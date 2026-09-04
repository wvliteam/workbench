# Claude Code Subagents 到 Codex Agent 迁移调研与设计方案

## 文档状态

- 状态：**配置层与守卫内核均已落地**（2026-09-03 复核）
- 范围：Codex CLI / IDE 的本地 custom agents
- 不包含：OpenAI Agents SDK / API 运行时重构
- 已落地：`.codex/agents/*.toml`（7 角色）、`.agents/skills/*`（3 个 skill）、`AGENTS.md`、`.codex/hooks.json`，相关文件已加入 Git 索引
- 守卫内核已落地：`wb.py` 的 `WRITE_TOOL` / `SHELL_TOOL` 覆盖 Codex 工具名，`apply_patch` 目标解析复用了同一套根路径 / 冻结 / 角色范围检查，`SubagentStop` 经 `--format codex` 输出 `{"systemMessage": ...}` JSON。`selfcheck` 含 Codex 形态用例（`apply_patch`、`exec_command`/`shell`、`subagent-stop` JSON）。
- 剩余非代码项：Codex 项目级 hook 只有在项目受信任并通过 `/hooks` 审核后才会加载。`hook` 信任与 `danger-full-access` 不在仓库代码范围内，落地见下方 M3。

## 1. 结论

可以迁移，但不能把 `.claude/` 直接改名为 `.codex/`。

推荐采用“共享工作台内核 + 宿主适配层”的双宿主方案：

1. 保留 `.workbench`、状态模型、门禁、契约和任务调度逻辑。
2. 保留现有 Claude Code 配置，新增 Codex agent、skill 和 hook 配置。
3. 在 hook 层适配 Codex 的 `apply_patch` 输入和事件输出协议。
4. 用 Codex permission profile 承担基础文件系统/网络边界，用共享 hook 承担敏感路径补充与动态角色权限。

当前实现的核心流程高度可复用；迁移前的主要风险集中在文件权限守卫、改动归属和 `SubagentStop` 输出协议，本次已完成这三项适配。

## 2. 现状调研

### 2.1 当前分层

当前工作台由五层组成，定义见 [architecture.md](architecture.md)：

| 层 | 当前实现 | 宿主耦合 |
| --- | --- | --- |
| 编排层 | `.claude/skills/wb-flow`、`wb-loop`、`wb-contract` | 中 |
| 执行层 | `.claude/agents/*.md` 的 7 个角色 | 高 |
| 内核层 | `.claude/hooks/wb.py` | 低 |
| 状态层 | `.workbench/state.json`、契约、产物、锁和日志 | 低 |
| 拦截层 | `.claude/settings.json` 注册的 4 个 hook | 高 |

`wb.py` 内部的阶段、门禁、契约、任务依赖和并发状态处理是普通 Python/JSON/CLI 逻辑，见 [`wb.py:42-119`](../.claude/hooks/wb.py:42)、[`wb.py:450-584`](../.claude/hooks/wb.py:450)、[`wb.py:767-1115`](../.claude/hooks/wb.py:767)。这些部分不依赖 Claude Code 的 API。

### 2.2 Claude 绑定点

1. Agent 定义使用 Markdown frontmatter，字段包括 `tools` 和 `model`，见 [`.claude/agents`](../.claude/agents)。
2. Hook 通过 `.claude/settings.json` 注册，命令路径依赖 `$CLAUDE_PROJECT_DIR`，见 [`.claude/settings.json:36-82`](../.claude/settings.json:36)。
3. 文件守卫处理 `Write`、`Edit`、`NotebookEdit`、`MultiEdit`，并从 Codex `apply_patch` 的 `tool_input.command` 解析所有目标路径，见 [`wb.py:1365-1510`](../.claude/hooks/wb.py:1365)。
4. 改动流水账同时支持单文件工具和多文件 patch；Codex payload 会保留 `agent_id`，见 [`wb.py:1512-1540`](../.claude/hooks/wb.py:1512)。
5. `SubagentStop` 通过 `hook --format codex` 输出合法 JSON，Claude 默认仍输出原文本，见 [`wb.py:1570-1621`](../.claude/hooks/wb.py:1570)。
6. 自检覆盖 Claude 与 Codex 形态 payload，见 [`wb.py:1823-1870`](../.claude/hooks/wb.py:1823)。
7. 已新增 `AGENTS.md`、`.codex/agents/*.toml`、`.codex/hooks.json` 和 `.agents/skills/*`，保留 `CLAUDE.md` 与 `.claude/` 入口用于双宿主。

### 2.3 已验证的 Codex 能力

根据 [OpenAI Docs: Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) 和 [OpenAI Docs: Hooks](https://learn.chatgpt.com/docs/hooks)：

- Codex 支持并行 subagent/thread，以及项目级 `.codex/agents/*.toml`。
- custom agent 必须定义 `name`、`description`、`developer_instructions`，还可配置 `model`、`model_reasoning_effort`、`sandbox_mode`、`mcp_servers` 和 `skills.config`。
- Codex 支持 `PreToolUse`、`PostToolUse`、`SessionStart`、`SubagentStart`、`SubagentStop` 等生命周期 hook。
- 本地 shell 和 `exec_command` 使用 `tool_name = "Bash"`；文件 patch 使用 `tool_name = "apply_patch"`，其输入放在 `tool_input.command`。
- `PreToolUse` 可以用 `permissionDecision = "deny"` 拒绝，也兼容退出码 2 和 stderr。
- `SessionStart` 的 stdout 会作为额外上下文；`SubagentStop` 则要求 JSON 输出。
- 项目级 `.codex/` 配置和 hook 只有在项目受信任时才会加载；hook 需要通过 `/hooks` 审核和信任。布局 A 下，hook 入口从当前目录向上查找同时包含 `.codex/hooks.json` 与 `.claude/hooks/wb.py` 的工作台根，不依赖内层仓库的 Git 根。

## 3. 兼容性矩阵

| 能力 | 判断 | 迁移动作 |
| --- | --- | --- |
| 六阶段状态机 | 高度兼容 | 保留 `wb.py` |
| 门禁和命令执行 | 高度兼容 | 保留；按 Codex sandbox 检查命令权限 |
| 契约哈希、unlock、bump | 高度兼容 | 保留 |
| 任务依赖和 `next --all` | 高度兼容 | 保留；并行上限与 Codex 配置取最小值 |
| 角色定义 | 部分兼容 | `.md` 转 `.toml`，正文转 `developer_instructions` |
| `tools:` 白名单 | 不直接兼容 | 删除该字段，改用 sandbox、权限 profile 和 hook |
| `sonnet` / `opus` | 不兼容 | 映射到当前可用 Codex 模型和 reasoning effort |
| `.claude/skills` | 不直接兼容 | 迁移到 `.agents/skills`，保留 Claude 入口用于双宿主 |
| Bash 危险命令守卫 | 基本兼容 | 继续处理 `Bash` payload |
| 文件角色守卫 | 已兼容 | `apply_patch` 解析全部目标后复用共享守卫；任一目标越权则整体拒绝 |
| 文件改动归属 | 已兼容 | 记录 patch 中全部路径，并在 Codex payload 存在时保留 `agent_id` |
| SessionStart | 兼容 | 保留文本输出，调整 hook 注册 |
| SubagentStop 清理 | 已兼容 | `--format codex` 输出 JSON；覆盖所有 Codex subagent |
| Claude `settings.json` 权限 | 不兼容 | 改写为 `.codex/config.toml` / `hooks.json` |
| 多仓库根路径 | 已验证（启动目录有前提） | hook 从当前目录向上查找工作台根；布局 A/B 的状态归属和角色范围需按文档配置 |

## 4. 关键阻塞与风险

### R1：文件权限守卫会失效，严重（已处理）

此前 `hook_pre_tool()` 对未知工具直接返回。现已增加 `apply_patch` 目标解析并复用同一套根路径、冻结契约和角色范围检查。

此前影响：`pm`、`qa` 或前端 agent 可以通过 patch 修改不属于自己的文件；冻结契约也可能被直接改写。

验证：`selfcheck` 覆盖 Add/Update、多文件 patch、前端迁移越权、后端迁移放行及混合冻结文件整体拒绝。

### R2：改动归属会丢失，严重（已处理）

现已由同一解析器提取 patch 中全部路径，并逐行追加到流水账；Codex 的 `agent_id` 同步保留。

此前影响：`artifacts.jsonl` 不记录真实改动，`task done` 无法正确归并产物，develop 门禁的证据链不完整。

剩余边界：任务归并仍按“角色 + 任务开始时间”，同一角色多个并行任务的精确绑定仍推迟。

### R3：SubagentStop 输出协议不兼容，中高（已处理）

Codex 入口现在通过 `--format codex` 把清理提示包装为 `{"systemMessage": ...}`；Claude 入口不变。

验证：`selfcheck` 对 Codex 停止事件 stdout 执行 JSON 解析。

### R4：Hook 信任可能让保护静默失效，中高

Codex 项目级 hook 在未信任项目或 hook hash 变化后不会运行。已初始化工作台中 `wb.py` 自身异常退出码为 2，避免守卫异常时 fail-open。

缓解：安装步骤必须包含项目信任和 `/hooks` 审核；文档不得把 hook 单独描述成绝对不可绕过。当前 `.codex/config.toml` 已启用 `workbench` permission profile 做敏感路径静态边界，`wb.py` 继续负责动态规则。

### R5：宿主配置和路径假设不兼容，中

当前 Claude 配置依赖 `$CLAUDE_PROJECT_DIR`，技能和 README 也大量使用 `.claude/...` 路径。Codex hook 从当前目录向上查找同时包含 `.codex/hooks.json` 与 `.claude/hooks/wb.py` 的工作台根，状态根仍由 payload 的 `cwd` 交给 `find_root()` 判断。

多仓库布局 A/B 已通过路径解析与角色范围自检：共享外层 `.codex` 时，hook 从当前目录向上找到同时包含 `.codex/hooks.json` 与 `.claude/hooks/wb.py` 的工作台根；状态仍由最近的 `.workbench/` 归属。运行前仍需从工作台根启动 Codex，并确认项目已信任、hook 已审核，否则项目级 hook 不会加载。

## 5. 目标架构

### 5.1 双宿主目录

```text
.workbench/                 需求状态、门禁、契约、产物和审计日志
.claude/hooks/wb.py         共享工作台内核与宿主 hook 适配

.claude/agents/*.md         Claude Code 角色定义
.claude/settings.json       Claude Code hook 与权限配置
.claude/skills/*            Claude Code skills

.codex/agents/*.toml        Codex custom agents
.codex/hooks.json           Codex lifecycle hooks
.agents/skills/*            Codex 项目级 skills
AGENTS.md                   Codex 项目级持久指导
```

如果只支持 Codex，可把内核入口逐步移到中性目录；如果需要双宿主，优先保留现有 Claude 入口，并让 Codex wrapper 调用同一份内核，避免复制状态机。

### 5.2 责任边界

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| `wb.py` 核心 | 状态、阶段、门禁、契约、任务、审计 | 具体宿主 payload 格式 |
| Codex hook adapter | payload 解析、patch 路径、JSON 输出 | 阶段业务规则 |
| `PreToolUse` | 动态角色范围、冻结契约、危险命令 | 事后恢复已执行副作用 |
| Codex permission profile | 静态敏感路径读取和基础 sandbox | 动态角色目录 |
| custom agent TOML | 角色职责、模型和运行模式 | 任务状态落盘 |
| `.agents/skills` | 编排步骤和可复用流程 | 权限强制 |

### 5.3 Agent 转换规则

每个 Claude agent 转成一个 Codex TOML 文件：

```toml
name = "backend-developer"
description = "按锁定契约实现后端任务，并回报可复现的校验命令。"
model = "gpt-5.6-sol"
model_reasoning_effort = "medium"
developer_instructions = """
执行单个任务。先读任务指定的契约和上游产物，再运行
python3 <工作台入口> task start <ID>。
只修改角色允许的路径；发现契约不足时 task block，不直接修改冻结契约。
完成后运行最小校验并回报完整命令和输出。
"""
```

转换原则：

- `name` 与 `ROLES` 保持完全一致，使 `agent_type` 能直接映射角色。
- `description` 保留“何时使用”而不是只描述角色名称。
- `tools` 字段不搬运；由 Codex sandbox、permission profile 和 hook 共同约束。
- 不要把需要写产物的 `analyst` / `reviewer` 直接设成 `read-only` sandbox；它们仍需写自己的报告目录。
- `architect` / `reviewer` 使用高 reasoning effort，其余角色使用中等 effort；当前 CLI `0.152.1` 的 provider 已验证 `gpt-5.6-sol` 可用，部署到其他 provider 时按可用目录替换。

### 5.4 Hook 适配规则

Codex 的 `.codex/hooks.json` 至少注册：

1. `PreToolUse` matcher：覆盖 `Bash` / shell 别名、`apply_patch`、文件读写工具。
2. `PostToolUse` matcher：覆盖 `Bash` / shell 别名、`apply_patch`、文件写入工具。
3. `SessionStart`：输出工作台状态上下文。
4. `SubagentStop`：不限定 matcher，覆盖 Codex 内置与自定义 subagent，确保角色锁和解冻窗口不会遗留。

事件处理：

- `Bash`：复用现有危险命令和冻结路径检查。
- `apply_patch`：解析全部目标路径，先做项目根、冻结文件和角色范围检查，再允许执行。
- 拒绝：保留退出码 2 + stderr；需要时增加 Codex `hookSpecificOutput.permissionDecision = "deny"`。
- `PostToolUse`：只追加 `artifacts.jsonl`，Bash 仅记录静态可解析的重定向目标，不在热路径读改写 `state.json`。
- `SessionStart`：保留现有文本状态摘要。
- `SubagentStop`：Codex 入口输出 JSON，清理逻辑仍复用 `hook_subagent_stop()`。

### 5.5 权限设计

采用两层：

1. Codex `workbench` permission profile + `wb.py` 静态路径守卫：拒绝 `.env`、证书、私钥和 `secrets/**` 等敏感路径读取。
2. `PreToolUse` hook：动态判断角色写入范围、契约冻结、状态文件和危险 Bash 命令。

默认不使用 `danger-full-access`。Codex hook 只作为动态策略层，不能替代 git、CI、测试和代码审查等最终防线。

### 5.6 编排和并发

`wb.py next --all --json` 继续负责计算就绪任务；Codex 负责实际 spawn 和 thread 管理。有效并发数取：

```text
min(.workbench.max_parallel,
    agents.max_concurrent_threads_per_session)
```

`wb-flow` 和 `wb-loop` 迁移为 Codex skill 后，保留“同一批任务并行派发、主线程复核命令、再 task done”的协议，不把任务状态交给对话历史。

## 6. 分阶段落地

### M0：适配前基线（已完成）

- 固定当前 `wb.py selfcheck` 为回归基线。
- 记录单仓库和跨仓库两种布局的实际 hook 根路径，并覆盖跨仓库角色范围自检。
- 明确 Codex CLI 版本、项目信任方式和可用模型目录。

### M1：Codex 配置入口（已完成）

- 增加 `AGENTS.md`，只放 Codex 必须持久遵循的工作台规则。
- 增加 `.codex/agents/*.toml`，完成 7 个角色转换。
- 增加 `.agents/skills/wb-flow`、`wb-loop`、`wb-contract`。
- 增加 `.codex/hooks.json`，先接入 `SessionStart` 和 `Bash`。

### M2：文件 patch 适配（已落地）

- 实现 `apply_patch` 目标路径解析，复用根路径 / 冻结 / 角色范围检查。
- `PostToolUse` 记录多文件 patch 全路径，并按载荷保留调用者身份。
- 增加 Codex `SubagentStop` `--format codex` JSON 输出，清理逻辑复用在 `hook_subagent_stop()`。

### M3：权限和双宿主验证（代码与自检已落地）

- `selfcheck` 已含 Codex 形态用例：`apply_patch` 越权 / 冻结整体拒绝、`shell` / `exec_command` 工具名、`subagent-stop` JSON 输出。
- `.codex/config.toml` 默认选择 `workbench` profile；`wb.py` 同时覆盖 Read 工具和 shell 静态路径，拒绝敏感文件读取。
- 首次使用需通过 `/hooks` 审核并信任项目 hook；未信任时 Codex 不会加载项目级 hook，这是部署期操作，不在仓库代码范围内。

### M4：文档和发布（已落地）

- 更新 README、`docs/README.md`、`CLAUDE.md` 与本文档的 Codex 迁移状态。
- 明确 hook 未信任、hook 失败和 `danger-full-access` 的风险。
- 记录 Codex CLI `0.152.1` 和已验证的事件 schema（见文末验证部分）。

## 7. 验收标准

- [x] 7 个 Codex custom agents 已按 `ROLES` 命名并提供 TOML 定义，且已在 Codex CLI `0.152.1` 交互式路径验证 `analyst` spawn。
- [x] `pm` 通过 `apply_patch` 修改产品代码被拒绝。
- [x] `frontend-developer` 修改 `migrations/` 被拒绝；`backend-developer` 修改后端迁移被放行。
- [x] 一个 patch 同时包含允许文件和冻结文件时整体拒绝。
- [x] 多文件 patch 的全部路径写入 `artifacts.jsonl`，并按载荷保留调用者身份。
- [x] `contract unlock` 窗口只放行对应 owner，`bump` 后重新冻结并创建消费方任务。
- [x] `SubagentStop` 返回 Codex 合法 JSON，并且并行任务存在时不会清掉兄弟任务的约束。
- [x] 两个并行 subagent 执行 `task done` 后，状态和冻结清单没有丢失或回退。
- [ ] 未信任项目或 hook hash 变化时，启动流程给出明确提示（由 Codex 宿主负责，仓库 hook 无法在未加载时自报）。
- [x] 当前 Claude Code 自检继续通过。

## 8.1 已执行验证

```bash
python3 .claude/hooks/wb.py selfcheck
python3 -m py_compile .claude/hooks/wb.py
python3 -m json.tool .codex/hooks.json >/dev/null
git diff --check
```

注意：`selfcheck` 同时覆盖 Claude 与 Codex 形态用例（`apply_patch`、`shell`/`exec_command` 工具名、`subagent-stop` JSON）。它们通过证明两条宿主路径都没被破坏。

## 9. 非目标与刻意推迟

- 不在本阶段把 `wb.py` 改造成 OpenAI Agents SDK/API 服务。
- 不重做阶段、契约和任务模型。
- 不新增数据库、消息队列或外部编排服务。
- 不在初版解决同一角色多个并行任务的精确归属；先记录 `agent_id`，确认真实需求后再扩展任务绑定。
- 不把 Codex 的 sandbox 当成业务角色权限的唯一实现。

## 10. 参考资料

- [OpenAI Docs: Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [OpenAI Docs: Hooks](https://learn.chatgpt.com/docs/hooks)
- [OpenAI Docs: Configuration Reference](https://developers.openai.com/codex/config-file/config-reference)
- [本项目架构设计](architecture.md)
- [本项目权限模型](permissions.md)
- [本项目调度与 Loop](scheduling.md)

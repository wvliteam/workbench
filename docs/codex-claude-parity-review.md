# Codex 与 Claude 平台能力对齐审阅

审阅范围：`.claude/`、`.codex/`、`.agents/`、`AGENTS.md`、`CLAUDE.md`、共享 hook 内核及迁移文档。

工作台未初始化，本审阅不修改工作台状态。已运行 `wb.py selfcheck`、Python 编译、JSON 解析和 `git diff --check`。

## 总结

当前 Codex 已能运行六阶段工作台的基础流程：自定义 agent、并行 subagent、生命周期 hook、`apply_patch` 守卫和 `SubagentStop` JSON 输出均已接通。静态敏感路径 profile、shell 改动审计、身份拒绝和根路径查找也已落地。

它仍不是与 Claude 完全等价的环境：hook 仍受项目受信任和审核状态影响；Claude 的工具白名单和模型分层没有 Codex 的一等对应项；`permission_mode = "bypassPermissions"` 与 profile 的交互仍需单独用 `codex exec` 验证。

## 第一轮：事实校正

### 已验证：Codex subagent 能提供身份字段

环境：`codex-cli 0.152.1`，测试了项目自定义 `analyst` agent，以及内置 `explorer` agent。

`PreToolUse`、`PostToolUse` 和 `SubagentStop` 三类事件均观察到：

| 字段 | 结果 |
| --- | --- |
| `agent_type` | 自定义 agent 为 `analyst`，内置 agent 为 `explorer` |
| `agent_id` | 存在，并在同一个 subagent 的多个事件中保持一致 |
| `agent_transcript_path` | `SubagentStop` 中存在 |
| `session_id` | 存在，但与主线程共享，不能单独区分身份 |
| `turn_id` / `tool_use_id` | 存在，可区分轮次或单次工具调用 |
| `transcript_path` / `cwd` | 存在 |
| `model` / `permission_mode` | 存在，本次分别为 `gpt-5.6-sol` / `bypassPermissions` |

因此，当前版本的 Codex payload 可以进入 [`current_role()`](../.claude/hooks/wb.py#L2088) 的角色分支。旧版本或异常 payload 只有 `agent_id` 时，hook 现在拒绝写入并要求升级 CLI，所以仍应固定版本并做 schema 自检。

## 第二轮：运行时与安全

### 1. Major（已修复）：Codex 已启用静态权限 profile，动态规则仍由 hook 负责

Claude 在 [`settings.json:23-33`](../.claude/settings.json#L23) 静态禁止读取 `.env`、证书、私钥和 `secrets/**`，并禁止直接编辑状态文件。Codex [`config.toml:1-11`](../.codex/config.toml#L1) 现已启用 `default_permissions = "workbench"`，继承 `:workspace` 并拒绝敏感路径；状态文件保护仍由 [`hooks.json:16-24`](../.codex/hooks.json#L16) 与共享 hook 负责。

Codex 项目未信任、hook hash 变化或 hook 未加载时，动态限制仍不存在；这是宿主审核边界，不能由仓库内代码自报。已初始化工作台中 `wb.py` 自身异常现改为退出码 2，避免 fail-open（[`wb.py:2448-2458`](../.claude/hooks/wb.py#L2448)）。

#### 官方权限文档后的结论：问题 1 可修复，但不是用 profile 替代 hook

本轮读取用户提供的官方 OpenAI 文档本地快照（[OpenAI Docs: 权限](https://learn.chatgpt.com/zh-Hans/docs/permissions)，本地文件 `/Users/wangpenghao/Downloads/权限 _ ChatGPT Learn.mhtml`，提取文本 `/tmp/codex-permissions.txt`）。文档明确说明：

- `default_permissions` / `[permissions]` 与 `sandbox_mode` / `[sandbox_workspace_write]` 是两套互斥方案；只要任一生效配置或 CLI 参数仍有 `sandbox_mode` / `--sandbox`，旧版沙盒优先，`default_permissions` 会被忽略（快照约第 140-166 行）。
- 自定义 profile 可以用 `extends = ":workspace"`，再用 `read` / `write` / `deny` 规则覆盖文件系统范围；更具体的 `deny` 可以在工作空间可写时拒绝 `**/*.env` 等路径（约第 210-234、264-341、479-504 行）。
- 网络域名规则只有在网络代理运行时才生效；需启用 `features.network_proxy = true` 或由托管要求启动代理（约第 172-188、521-540 行）。
- 该能力仍是 Beta，且不同操作系统的强制路径不同。

因此，**平台能力和项目配置均已落地**。当前 profile 补上了 Claude “敏感路径拒绝”这一部分的静态兜底：

```toml
default_permissions = "workbench"

[permissions.workbench]
extends = ":workspace"

[permissions.workbench.filesystem]
"**/*.env" = "deny"
"**/*.pem" = "deny"
"**/*.key" = "deny"
"**/id_rsa*" = "deny"
"secrets/**" = "deny"
```

落地前必须先清除所有生效层级的 `sandbox_mode` / `[sandbox_workspace_write]`，并避免使用 `--sandbox`；否则这份 profile 看似配置成功、实际不会生效。不要把 `.workbench/state.json`、`.workbench/role` 或契约文件简单设为 `deny`：profile 的 `deny` 同时作用于读和写，可能连 `wb.py status/task/contract` 的正常状态访问也拦截；Claude 的“只禁止直接编辑”语义只能继续由 hook 实现。配置 profile 后，仍需保留 `wb.py` hook，原因是 profile 不能表达以下动态规则：

| 规则 | 仍由谁负责 |
| --- | --- |
| `pm`、`qa`、前后端各自的阶段产物范围 | `wb.py` 根据 `agent_type` 判定 |
| 契约 lock/unlock、冻结清单和哈希校验 | `wb.py` |
| 任务状态、争议熔断和危险 shell 语义 | `wb.py` |
| `artifacts.jsonl` 改动归属与 `SubagentStop` 清理 | `wb.py` |
| hook 未加载时的最后告警和启动审计 | 宿主信任/审核流程 |

仍有一项平台交互未定论：真实 Codex payload 带有 `permission_mode = "bypassPermissions"`。官方权限页面说明了 profile 与旧沙盒的选择规则，但没有说明该运行态字段是否会绕过 filesystem profile；在独立 `codex exec` 配置层实验完成前，不能把 profile 宣称为覆盖所有 subagent 运行态的最终边界。

#### 实测：deny 状态文件会阻断 `wb.py`

使用 `codex-cli 0.152.1` 在 `/tmp` 临时工作区运行真实 `codex sandbox -P <profile> -C <dir> -- <command>`，通过 CLI `-c` 注入 profile，未修改仓库或用户配置：

| 场景 | 结果 |
| --- | --- |
| `extends = ":workspace"`，执行 `wb.py init` / `status` / `task list` / `contract list` | 全部正常；普通工作区写入也正常 |
| 对临时工作区的绝对路径 `.workbench/state.json` 配置 `deny`，直接读取 | `PermissionError: Operation not permitted` |
| 同一 profile 直接写入 `.workbench/state.json` | `PermissionError: Operation not permitted` |
| 同一 profile 执行 `wb.py status`、`task list`、`task add`、`contract list` | 均无法正常执行，退出码 1，统一输出“未初始化工作台” |
| 同一 profile 写入普通文件 | 成功 |

这说明 permission profile 确实在强制执行，但 `deny` 是读写一体的文件系统拒绝；它不能复刻 Claude `Edit(path)` 只禁直接编辑的语义。将 `.workbench/state.json` 放进 profile deny 会连 `wb.py` 的状态读取和状态写入一起切断，因此状态、契约和冻结文件必须继续由 hook / `wb.py` 业务规则保护。

本次用 `codex sandbox` 显式传入 `-P` 选择 profile；该子命令要求显式 profile，因此这组结果证明了 profile 的强制效果，不等同于已验证项目配置中 `default_permissions` 的自动选择和与旧 `sandbox_mode` 的优先级。后两项仍应在不改仓库配置的独立 `codex exec` 配置层实验中验证。

### 2. Medium（已修复）：Codex 的 agent 身份已进入审计流水账

实测确认 `agent_id` 存在；[`hook_post_tool()`](../.claude/hooks/wb.py#L2303) 现在会在 `artifacts.jsonl` 中保留 `agent_id`、`agent_type`、`session_id`、`turn_id`、`tool_use_id` 等可用字段（[`wb.py:2307-2314`](../.claude/hooks/wb.py#L2307)）。

同一角色多个并行任务的产物归属已由 `task start` 时的 `agent_id` 绑定解决；旧工作台没有绑定记录时仍回退到「角色 + 任务开始时间」。

### 3. Medium（已修复）：Codex hook 的根路径查找与文档一致

[`hooks.json:10`](../.codex/hooks.json#L10) 等四个命令现在都向上查找同时包含 `.codex/hooks.json` 和 `.claude/hooks/wb.py` 的工作台根，与迁移文档一致。

嵌套多个工作台时仍应从目标工作台根启动，以确保宿主传入的 `cwd` 属于预期状态目录。

### 4. Medium（已修复）：shell 静态写入目标已进入改动流水账

两端的 `PostToolUse` 都匹配 shell 工具；[`hook_post_tool()`](../.claude/hooks/wb.py#L2316) 现在用 `resolve()` 解析重定向、`cp`、`mv`、`install` 等静态写入目标并追加到 `artifacts.jsonl`。动态不可解析命令由 `PreToolUse` 拒绝，避免无审计写入。

## 第三轮：交付与能力边界

### 5. Major（已修复）：Codex 适配层已纳入 Git 索引

`.codex/`、`.agents/`、`AGENTS.md` 及相关迁移文档已加入 Git 索引，发布时会随项目版本交付；自动生成的 `graphify-out/` 刻意不纳入。

最终提交仍属于发布流程，但重新 clone 或 CI 获取提交后即可得到 Codex 入口。

### 6. Medium（平台残余差异）：Claude 的 per-agent `tools:` 白名单在 Codex 没有直接等价物

例如 Claude PM 角色声明 `Read, Grep, Glob, Bash, Write`（[`pm.md:2-5`](../.claude/agents/pm.md#L2)），Codex PM TOML 没有工具级白名单（[`pm.toml:1-4`](../.codex/agents/pm.toml#L1)）。Codex 只能依赖全局 sandbox 和 hook 做路径判断，无法表达“该角色不能调用某类工具”。迁移文档已将此列为“不直接兼容”（[`codex-agent-migration.md:71-74`](codex-agent-migration.md#L71)）。

### 7. Medium（平台残余差异）：模型语义是有损映射

Claude 使用 `opus` / `sonnet` 区分角色（[`architect.md:4-5`](../.claude/agents/architect.md#L4)、[`analyst.md:4-5`](../.claude/agents/analyst.md#L4)）；Codex 中所有角色都固定为 `gpt-5.6-sol`，只通过 reasoning effort 区分（[`architect.toml:1-4`](../.codex/agents/architect.toml#L1)、[`analyst.toml:1-4`](../.codex/agents/analyst.toml#L1)）。

这不是当前配置错误，但不能保证其他 provider 或未来 CLI 版本仍提供该模型。

### 8. Minor（已修复）：平台状态文档已统一

`docs/README.md`、迁移文档和 `roma-comparison.md` 现均说明 Codex 适配与共享守卫已落地，同时保留 hook 信任、工具白名单和模型映射等平台残余差异。

## 剩余验证与运维项

1. 在不改仓库配置的独立 `codex exec` 实验中验证 `default_permissions` 自动选择、旧 `sandbox_mode` 优先级和 `bypassPermissions` 交互。
2. 固定 Codex CLI 版本，并在部署步骤中完成项目受信任与 `/hooks` 审核；宿主未加载项目 hook 时仓库无法自报。
3. 继续把工具白名单和模型映射视为平台残余差异，不宣称与 Claude 完全等价。

## 验证记录

- `codex-cli --version`：`codex-cli 0.152.1`
- `python3 .claude/hooks/wb.py selfcheck`：通过。
- `python3 -m py_compile .claude/hooks/wb.py`：通过。
- `.codex/hooks.json`：JSON 解析通过；`.codex/config.toml`：TOML 解析通过。
- `git diff --check`：通过。
- 真实 `analyst` subagent 只执行 `pwd`，未修改仓库文件；临时 hook 探针已删除，内核已恢复原状。
- 官方权限文档：读取用户提供的本地 MHTML 快照并用 Python 标准库提取；结论已落实到 `.codex/config.toml` 的 `workbench` profile。
- permission profile 实测：`:workspace` 基线命令通过；deny 状态文件后直接读写均为 `PermissionError`，`wb.py status/task/contract` 均不可用；普通工作区文件写入通过。
- 本轮回归：`wb.py selfcheck`、`py_compile`、TOML/JSON 解析和 `git diff --check` 均通过；Codex 适配文件已加入 Git 索引。

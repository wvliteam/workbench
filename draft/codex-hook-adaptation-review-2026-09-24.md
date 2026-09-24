# Codex 平台适配审查：`.claude/hooks/wb_guard.py`

## 1. 审查结论

审查对象是工作台守卫实现 `.claude/hooks/wb_guard.py` 及其 Codex 注册表 `.codex/hooks.json`。审查基于：

- 工作区当前代码，HEAD 为 `f2b1851`；
- 本机 `codex-cli 0.156.1`；
- Codex 上游 `rust-v0.156.1` 的 hook 输入、输出与解析实现；
- 本地命令复现，不依赖猜测。

结论：**工具拦截主链路基本可用，但 Codex 输出适配没有统一收口，存在 3 个已确认问题和 2 个应补强的风险点。**

最重要的问题是：未完成 flow 归属时，`UserPromptSubmit` hook 输出以 `[` 开头的普通文本，恰好触发 Codex 的“看起来像 JSON、但 JSON 无法解析”错误：

```text
hook returned invalid user prompt submit JSON output
```

该问题已经在当前工作区复现。

## 2. 严重度摘要

| 编号 | 严重度 | 位置 | 问题 | 当前状态 |
| --- | --- | --- | --- | --- |
| CDEX-001 | P1 | `.claude/hooks/wb_guard.py:1278-1298` | `UserPromptSubmit` 输出非法 JSON 形状 | 已复现；导致归属提醒失败并产生 hook 错误 |
| CDEX-002 | P2 | `.claude/hooks/wb_guard.py:1033-1037` | `PreToolUse` 警告以 `[` 开头的普通文本输出 | 已复现；警告不生效并记录为 invalid JSON |
| CDEX-003 | P2 | `.codex/hooks.json:6` | `SessionStart` matcher 漏掉 `fork` | 已确认；fork 会话收不到工作台上下文 |
| CDEX-004 | P3 | `.claude/hooks/wb_guard.py:1303-1331` | hook 输入 JSON 解析失败后统一降级为 `{}` | 未在正常 Codex 载荷中复现；异常输入时可能 fail-open |
| CDEX-005 | P3 | `.claude/hooks/wb_guard.py:1132-1139` | PostToolUse 记录路径字段未复用完整路径字段表 | 对当前已知工具影响有限；新写入工具会漏记流水账 |

## 3. Codex hook 输出契约

当前 Codex 版本的解析行为决定了工作台不能简单地把 Claude 风格 stdout 原样复用到所有事件：

1. 空 stdout：允许，表示 hook 没有附加输出。
2. JSON stdout：必须是 JSON object，且要符合事件对应的 schema。
3. `[` 或 `{` 开头但无法解析为有效事件 JSON：判定为 invalid JSON hook output。
4. `SessionStart` 和 `UserPromptSubmit` 可以把非 JSON 普通文本当作上下文，但前提是文本不能被 `looks_like_json` 判断为 JSON 外形。
5. `PreToolUse` / `PostToolUse` 的普通文本不会按预期展示为警告；需要用合法 JSON 的 `systemMessage` 或事件专属输出字段。
6. 退出码 `2` 且 stderr 有内容可用于阻止操作；`hook_deny()` 当前采用这一方式，方向正确。

Codex 上游解析器的关键逻辑是：先尝试解析 JSON object；失败后再检查 stdout 是否以 `{` 或 `[` 开头。工作台的中文提醒恰好以 `[工作台...]` 开头，因此会进入错误分支。

## 4. 详细问题分析

### CDEX-001：`UserPromptSubmit` 输出格式错误

**位置**

- `.codex/hooks.json:40-50` 注册了 `UserPromptSubmit`，调用 `hook user-prompt --format codex`。
- `.claude/hooks/wb_guard.py:1278-1298` 的 `hook_user_prompt()` 没有接收或使用 `fmt`。
- `.claude/hooks/wb_guard.py:1311-1316` 只把 `args.format` 传给了 `hook_subagent_stop()`，没有传给 `hook_user_prompt()`。

**现状行为**

当以下条件同时成立时，hook 输出普通文本：

- 工作台已经初始化；
- 输入中有 `session_id`；
- 当前会话尚未归属 flow。

输出内容的首字符是 `[`：

```text
[工作台] 本会话尚未做 flow 归属。若本轮涉及实现/修复/接口/跨模块/验证/留痕，...
```

**复现证据**

执行：

```bash
printf '{"cwd":"/home/work/workspace/code/workbench","session_id":"diagnostic"}' \
  | python3 .codex/hooks/wb.py hook user-prompt --format codex
```

实际 stdout 是上述普通文本；再执行：

```bash
... | python3 -m json.tool
```

得到 JSON 解析失败：

```text
Expecting value: line 1 column 2 (char 1)
```

**影响面**

- 每个尚未做 flow 归属的 Codex 会话，在用户首次发言或归属前发言时都可能产生该错误。
- Codex 将这次 hook 运行标记为失败。
- 归属提醒不会作为有效 `additionalContext` 注入模型。
- 当前通常不会阻止用户 prompt，因为 hook 进程退出码仍是 `0`；但用户会看到错误，且提醒功能实际失效。
- 该问题与本次会话出现的 `hook returned invalid user prompt submit JSON output` 直接吻合。

**根因**

`--format codex` 已经从注册表传入，但 `cmd_hook()` 没有把格式参数传入 `hook_user_prompt()`；同时函数自身只实现了 Claude 风格的纯文本输出。

**修复建议**

最小修复是让 `hook_user_prompt(data, fmt)` 在 Codex 下输出事件专属 JSON：

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "[工作台] 本会话尚未做 flow 归属……"
  }
}
```

并在 `.claude/hooks/wb_guard.py:1315` 将 `args.format` 传入。Claude 路径继续保留原文本输出，避免改变已有行为。

建议补充的断言：

- Codex 未归属路径 stdout 必须能被 `json.loads()` 解析；
- `hookSpecificOutput.hookEventName == "UserPromptSubmit"`；
- `additionalContext` 非空；
- 已归属、无 session_id、未初始化三条路径保持空 stdout。

### CDEX-002：`PreToolUse` 警告被误判为非法 JSON

**位置**

- `.claude/hooks/wb_guard.py:1033-1037` 遍历 `WARN_BASH` 后直接 `print()` 到 stdout。
- `.claude/hooks/wb_const.py:564-570` 定义了 `git reset --hard`、`git clean`、`git checkout --`、发布命令等警告。

**复现证据**

执行：

```bash
printf '%s' '{"cwd":"/home/work/workspace/code/workbench","tool_name":"Bash","tool_input":{"command":"git reset --hard HEAD"}}' \
  | python3 .codex/hooks/wb.py hook pre-tool --format codex
```

实际输出：

```text
[工作台提示] git reset --hard 会丢弃未提交改动。确认这是你要的操作。
```

该输出同样无法被 JSON 解析，且首字符为 `[`，因此符合 Codex 的 invalid pre-tool-use JSON 触发条件。

**影响面**

- 被 `WARN_BASH` 命中的命令仍可能执行，因为 hook 退出码是 `0`，这不是阻止路径。
- 预期的风险提醒不会作为 Codex 的有效 `systemMessage` 展示。
- Codex hook 运行状态会变成失败，污染 hook 诊断信息。
- 当前单条警告就会触发；若未来同一命令命中多条规则，逐条输出多个 JSON 也会进一步造成非法多文档输出。

**修复建议**

让 `hook_pre_tool(data, fmt)` 在 Codex 下将所有警告合并后输出单个 JSON object，例如：

```json
{
  "systemMessage": "[工作台提示] git reset --hard 会丢弃未提交改动。确认这是你要的操作。"
}
```

实现上应先收集命中的 warning，再一次性 `json.dumps()`；不要在循环中输出多个 JSON object。Claude 继续使用原有纯文本输出即可。

不建议只把文本改到 stderr：退出码为 `0` 时，Codex 的 PreToolUse 解析路径不会把 stderr 当作可见警告。合法 JSON 的 `systemMessage` 更符合现有 Codex 契约。

### CDEX-003：`SessionStart` 漏掉 `fork`

**位置**

`.codex/hooks.json:6` 当前为：

```regex
^(startup|resume|clear|compact)$
```

Codex `rust-v0.156.1` 的 `SessionStartSource` 还包含 `fork`。该事件通过 source 字符串参与 matcher，当前正则对 `fork` 返回 false。

**复现证据**

```text
matcher: ^(startup|resume|clear|compact)$
fork matches: false
```

**影响面**

- 从已有会话 fork 出来的新 Codex 会话不会执行工作台 `session-start` hook。
- 新会话看不到 flow 清单、当前阶段、任务进度、契约漂移等上下文。
- 这不会直接绕过 `PreToolUse` 权限守卫，因为工具 hook 仍单独注册；但会造成上下文缺失和误操作概率上升。

**修复建议**

将 matcher 改为：

```regex
^(startup|resume|clear|compact|fork)$
```

并在静态自检中增加 `fork` matcher 断言，避免 Codex 新增 SessionStart source 后再次漏配。

### CDEX-004：无效 stdin 被静默降级为 `{}`

**位置**

`.claude/hooks/wb_guard.py:1303-1309`：

```python
try:
    data = json.loads(raw or "{}")
except json.JSONDecodeError:
    data = {}
```

**影响面**

- 正常 Codex 调用会提供合法 JSON，因此这不是正常路径故障。
- 如果 hook 注册器、CLI 版本、stdin 传递或上游协议异常，关键字段会全部丢失。
- `hook_pre_tool()` 对空 `tool_name` 没有主动拒绝，部分情况下会直接返回，造成保护层 fail-open。
- 对读取/提示类事件，fail-silent 是合理的；对 `PreToolUse` 这种权限边界事件，不能用同一策略。

**修复建议**

按事件区分策略：

- `pre-tool`：stdin 非法、缺少 `tool_name` 或缺少 `cwd` 时，退出码 `2` 并把原因写入 stderr；
- `post-tool`：记录失败但不阻断已完成的工具调用；
- `user-prompt`：保持 fail-silent，避免提醒 hook 阻断用户输入；
- `session-start` / `subagent-stop`：至少输出错误到 stderr，并使用与 Codex 事件契约一致的退出策略。

这项修复应以明确的 Codex 载荷 schema 校验为边界，不要在守卫里复制完整 schema。

### CDEX-005：PostToolUse 路径字段没有复用统一解析表

**位置**

- `.claude/hooks/wb_guard.py:823-833` 的 `_PATH_FIELDS` 支持：`file_path`、`notebook_path`、`path`、`target_path`、`target`、`dest`、`destination`。
- `.claude/hooks/wb_guard.py:1132-1139` 的 `hook_post_tool()` 只读取 `file_path` 或 `notebook_path`。

**影响面**

- `PreToolUse` 对带 `path` 等字段的未知写入工具可以执行路径检查。
- 但对应的 `PostToolUse` 不会把这些改动写入 `artifacts.jsonl`，导致任务产物流水账漏记。
- 对当前 Codex 常见的 shell 工具，shell 分支使用 `resolve()`，因此不会影响 `exec_command`；对 `apply_patch`，专门分支也能记录 patch 标记。
- 这是新工具或第三方工具接入时会暴露的跨端一致性缺口，当前属于潜在问题而非已复现的 Codex 0.156.1 核心故障。

**修复建议**

复用 `_tool_path_values(ti)`，或抽出一个同时服务 PreToolUse/PostToolUse 的路径提取函数。这样新增工具字段只需改一处。

## 5. 事件逐项兼容性矩阵

| 事件 | 注册 | 输入字段 | 当前输出 | 结论 |
| --- | --- | --- | --- | --- |
| `SessionStart` | 已注册 | `cwd`、`session_id` 等 | 普通 Markdown 文本 | Codex 接受普通文本作为上下文；但 matcher 漏 `fork` |
| `PreToolUse` | 已注册，catch-all | `tool_name`、`tool_input`、agent 字段 | 空 stdout；拒绝走 exit 2 + stderr；警告走纯文本 | 拒绝路径兼容，警告路径不兼容 |
| `PostToolUse` | 已注册，catch-all | `tool_name`、`tool_input`、`tool_response` | 通常空 stdout | 基本兼容；路径流水账字段不完整 |
| `UserPromptSubmit` | 已注册，catch-all | `session_id`、`prompt`、agent 字段 | 未归属时输出 `[` 开头普通文本 | 不兼容，已复现 |
| `SubagentStop` | 已注册 | `agent_id`、`agent_type` 等 | `{"systemMessage": ...}` | 兼容；Codex 通用输出字段接受 `systemMessage` |

## 6. 已确认正确的部分

### 6.1 `hook_deny()` 的阻止方式正确

`.claude/hooks/wb_guard.py:41-44` 使用 stderr 输出原因并退出码 `2`。Codex 对 `PreToolUse`、`PostToolUse`、`UserPromptSubmit` 的退出码 `2` 都有明确的阻止/反馈处理，因此冻结文件、危险命令、角色越权等拒绝路径不需要改成普通 JSON 才能生效。

### 6.2 Codex 工具字段读取基本对齐

Codex 0.156.1 的 `PreToolUse` / `PostToolUse` 输入包含：

- `session_id`；
- `turn_id`；
- 可选 `agent_id` / `agent_type`；
- `cwd`；
- `tool_name`；
- `tool_input`；
- `PostToolUse` 额外有 `tool_response` 和 `tool_use_id`。

`wb_guard.py` 当前使用的核心字段与这些名称一致，`current_role()` 的三态处理也能覆盖主线程、已知角色和未知 subagent 身份。

### 6.3 `SubagentStop` 的 JSON 包装方向正确

`.claude/hooks/wb_guard.py:1272-1273` 输出 `systemMessage` object。Codex 的 `SubagentStop` 输出 schema 继承通用 hook 输出字段，`systemMessage` 是允许字段，且 `continue` 默认是 `true`，因此当前包装不会因为缺少显式 `continue` 而停止 agent。

### 6.4 `apply_patch` 与 Codex shell 路由已有专门处理

- `.claude/hooks/wb_const.py:357-366` 覆盖 `exec_command`、`shell`、`unified_exec`、`Monitor` 等 shell 工具名；
- `.claude/hooks/wb_guard.py:1048-1053` 处理独立的 `apply_patch` 工具；
- `.claude/hooks/wb_guard.py:962-964` 处理 shell 中嵌入的 `apply_patch`；
- `.claude/hooks/wb_selfcheck.py:1482-1514` 已有对应动态回归。

这部分不是本次发现的主要缺陷。

## 7. 推荐修复顺序

### 第一批：立即修复

1. 给 `hook_user_prompt()` 增加 Codex 输出分支，并把 `args.format` 传入。
2. 给 `hook_pre_tool()` 的 warning 增加 Codex JSON 输出分支；多个 warning 合并成一个 object。
3. 将 `.codex/hooks.json` 的 SessionStart matcher 加入 `fork`。

### 第二批：补回归

1. 增加 `UserPromptSubmit` Codex JSON 输出测试。
2. 增加 `PreToolUse` warning Codex JSON 输出测试。
3. 增加 `fork` matcher 静态测试。
4. 增加所有事件的 stdout 合法性 smoke test：空输出、合法 object、以 `[` 开头的普通文本。
5. 增加异常 stdin 的 PreToolUse fail-closed 测试。

### 第三批：低风险收口

1. 让 PostToolUse 与 PreToolUse 共用路径字段提取函数。
2. 将 `fmt` 传播规则集中到 `cmd_hook()`，避免未来新增 Codex 事件时忘记传格式。
3. 在 Codex 版本升级时重新核对上游 hook schema；当前结论以 `codex-cli 0.156.1` 为基线。

## 8. 建议验收命令

修复后至少运行：

```bash
# 工作台动态自检
python3 .claude/hooks/wb.py selfcheck --skip-static

# 静态布局与 Codex 注册表检查
python3 .claude/hooks/wb.py selfcheck

# UserPromptSubmit 必须输出可解析 JSON
printf '{"cwd":"/home/work/workspace/code/workbench","session_id":"diagnostic"}' \
  | python3 .codex/hooks/wb.py hook user-prompt --format codex \
  | python3 -m json.tool

# PreToolUse warning 必须输出可解析 JSON
printf '%s' '{"cwd":"/home/work/workspace/code/workbench","tool_name":"Bash","tool_input":{"command":"git reset --hard HEAD"}}' \
  | python3 .codex/hooks/wb.py hook pre-tool --format codex \
  | python3 -m json.tool
```

## 9. 评审边界与未覆盖项

- 本文审查的是仓库内 `.claude/hooks/wb_guard.py` 及其 Codex 注册配置，不修改代码。
- 工作区外的用户级 hook `/home/work/.comate/.baidu-cx/hooks.json` 另有 `data-report --user-prompt-submit`，它是否输出合法 Codex JSON 需要单独审查；本报告不能把该 hook 的行为与仓库 hook 混为一谈。
- 当前已确认的 `UserPromptSubmit` 和 `PreToolUse` 问题来自仓库 hook 本身，因为可以直接用 `.codex/hooks/wb.py` 在本地复现。
- Codex hook schema 可能随 CLI 版本变化；升级到其他版本后，应重新核对输出字段名、matcher source 和退出码语义。

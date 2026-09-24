# 任务 write-scopes 必须进入实际写入授权链

## 依据

2026-09-24，工作台的 `write_scopes` 原先只用于 `next --all` 的并发冲突筛选，PreToolUse 不读取它；integrator 的裸 `*.py`、`*.yaml` 范围因此无法区分联调胶水代码和业务源码。现已在 `.claude/hooks/wb_guard.py` 接入 `agent_id`、flow、角色、`doing` 状态和 attempt 校验，并由 `.claude/hooks/wb_selfcheck.py` 覆盖 Write、Bash、`apply_patch`、失败启动和重试隔离。

## 适用范围

适用于本工作台所有带显式 `write_scopes` 的 developer 任务，尤其是需要修改线下 Redis 客户端、服务 local 配置、指定下游、代理、Mock、启动脚本或 E2E 文件的 integrator 任务。角色默认范围仍是受守工作流路径的另一层限制；没有 `write_scopes` 的历史任务保持兼容。

## 失效条件

如果任务绑定载荷不再稳定提供 `agent_id` / `agent_type`，或任务 attempt 记录格式改变，必须同步调整守卫与 selfcheck；如果任务范围从相对路径前缀改为 glob，也要重新验证路径匹配和源码挂载。

## 来源

flow `task-write-scope-guard`，2026-09-24。

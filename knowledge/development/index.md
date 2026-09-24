# Development

## Current Knowledge

本工作台自身的工程约定 —— 编码与目录纪律这类「改代码时必须一起改什么」的规则。判据、分类与条目格式见 [../README.md](../README.md)。

## Knowledge Map

- [多端资产是单源软链：skills 与角色定义都不需要双写](multi-end-assets-share-one-source.md) —— `.agents/skills` 软链到 `.claude/skills`，两端 agents 软链到根 `agents/`；改源即全端生效，先 `ls -la` 确认是不是软链再决定要不要 diff。
- [任务 `write_scopes` 必须进入实际写入授权链](task-scoped-write-permissions.md) —— 调度器的范围冲突检查不能替代 PreToolUse 授权；按 agent、flow、角色、doing 状态与 attempt 绑定，支持 integrator 精确修改联调适配代码。

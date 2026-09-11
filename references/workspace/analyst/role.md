# Analyst Workspace 规则

- 只读调查源码、仓库说明和知识库；单域模式产出当前 flow 的 `analyze/current-state.md`，多域模式只产出主 Agent 分配的唯一 `analyze/parts/<scope-slug>.md`。
- 多域模式不得写 manifest、canonical 或其他 part；必须说明范围和风险，并为关键事实提供 `文件:行号` 证据。
- 不修改产品代码、需求文档或知识库。

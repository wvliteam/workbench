# Environment

## Current Knowledge

harness 与环境的能力边界：哪些能力可用、不可用时流程怎么走。这里记环境差异与配置语义，**永不记凭据值**，只记名称与来源位置。

## Knowledge Map

- [本环境派不出角色 subagent，六阶段在主线程执行但要保留全部门禁纪律](harness-dispatches-no-role-subagents.md) —— `Agent` 工具只暴露内置类型，八个角色不在可派发列表里；门禁纪律一项不减，派给内置 subagent 时它不受角色范围约束。

# 本环境派不出角色 subagent，六阶段在主线程执行但要保留全部门禁纪律

当前 ZCode 会话里 Agent 工具只暴露 `general-purpose` / `Explore` 等内置类型，`.claude/agents/*.md` 的八个角色**不会出现在可派发列表里**。走六阶段流程时不要等角色派发 —— 主线程直接执行各阶段产物，但门禁纪律一项不减：每阶段产物落盘、`gate check` 真过、校验命令编排者亲自跑、结果写进 `verification.md`（这本来就是编排者的产物）。

## 依据
2026-09-07 flow main 全程：clarify→retro 六个阶段全部主线程执行，门禁逐个真过（含 verify 的 selfcheck 命令门禁 exit=0），流程未被卡死 —— 说明门禁机制的强制性不依赖角色派发成立。

## 适用范围
ZCode harness 会话。派给 `general-purpose` 子 agent 时它不受角色范围约束（无 `agent_type`），写入靠主线程事后复核。

## 失效条件
harness 把项目自定义 agent 暴露为可派发类型之后（届时恢复 wb-flow 的标准派发路径）。

## 来源
flow main「复盘沉淀流程 + knowledger 角色」，全程观察，retro 沉淀。

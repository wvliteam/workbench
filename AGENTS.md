# 工作台协作约定

这是一个共享状态的六阶段开发工作台。Codex custom agents 的角色名必须与 `wb.py` 的 `ROLES` 完全一致。

## 必须遵守

- 每轮先运行 `python3 .claude/hooks/wb.py status`，状态、任务、门禁和契约只能通过 `wb.py` 修改。
- 需求澄清、现状分析、方案设计先完成并过门禁；契约锁定后再并行开发。
- 开发阶段用 `python3 .claude/hooks/wb.py next --all --json` 取得整批就绪任务，并行派发；每个 agent 先 `task start`，完成后 `task done`。
- 主线程必须复核 agent 报告的校验命令，再把结果写入 `.workbench/artifacts/develop/verification.md`。
- 不要绕过权限 hook 修改冻结文件、契约或状态；契约不足时 `task block`，由 architect 走 unlock/bump。
- Codex 项目 hook 只有在项目受信任并通过 `/hooks` 审核后才会运行；不要把 `danger-full-access` 当作角色权限控制。

## 入口

- 主编排：`/wb-flow`，自动排空：`/wb-loop`，契约操作：`/wb-contract`
- Codex skills：`.agents/skills/`
- 状态内核：`.claude/hooks/wb.py`

小改动或纯文档任务不必套完整六阶段，但仍须遵守冻结文件和危险命令守卫。

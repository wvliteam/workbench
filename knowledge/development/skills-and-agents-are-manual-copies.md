# skills 与 agent 定义是手工同步的双份拷贝，改完必须 diff

`.agents/skills/` 与 `.claude/skills/` 是两份**手工同步的完整拷贝**（不是软链）：改任何一份 skill 必须把改动复制到另一份并 `diff -r` 确认一致。角色定义同理 —— `.claude/agents/<名>.md` 与 `.codex/agents/<名>.toml` 内容同构但格式不同（YAML frontmatter vs TOML），改角色职责/模板要双写。

漏同步不会报错：Codex 端会静默用旧版 skill 干活，两边行为漂移要到任务结果不一致才暴露。

## 依据
2026-09-07：`diff -r .agents/skills .claude/skills` 确认改动前完全一致，T3/T4 每次改动后 `diff` 单文件核对一致。roma-comparison.md 也记过同型教训（两份 post-tool guard 曾发生漂移）。

## 适用范围
本工作区全部 skill 与角色定义。新加 skill 时两个目录都要建（`mkdir -p` + `cp` + `diff`）。

## 失效条件
某天两目录改成软链/构建产物生成，或在 AGENTS.md 明确了单源同步机制 —— 届时以新机制为准。

## 来源
flow main「复盘沉淀流程 + knowledger 角色」，T3/T4 任务。

# Troubleshooting

## Current Knowledge

已确认根因、已实施修复的坑，每条带可复现现象与排查线索 —— 下次撞上同一个报错能直接对上。

## Knowledge Map

- [Codex agent TOML 的 description 里不能内嵌裸双引号](codex-agent-toml-quote-escapes.md) —— 内嵌 `"` 会让 `tomllib` 解析整份 agent 定义失败（agent 加载不出来），用「」代替；`developer_instructions` 的多行字符串不受影响。
- [`WB_ROOT` 指向的目录里没有 `.workbench/` 时会被静默忽略，命令落到真实工作区](wb-root-requires-existing-workbench-dir.md) —— 临时目录做夹具必须先 `mkdir -p <dir>/.workbench`（或先 `cd` 再跑），否则 `task add` / `role set` 悄悄写真实状态；`.workbench/` 被 gitignore，`git status` 查不出来。

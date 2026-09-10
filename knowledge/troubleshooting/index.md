# Troubleshooting

## Current Knowledge

已确认根因、已实施修复的坑，每条带可复现现象与排查线索 —— 下次撞上同一个报错能直接对上。

## Knowledge Map

- [Codex agent TOML 的 description 里不能内嵌裸双引号](codex-agent-toml-quote-escapes.md) —— 内嵌 `"` 会让 `tomllib` 解析整份 agent 定义失败（agent 加载不出来），用「」代替；`developer_instructions` 的多行字符串不受影响。

# Development

## Current Knowledge

本工作台自身的工程约定 —— 编码与目录纪律这类「改代码时必须一起改什么」的规则。判据、分类与条目格式见 [../README.md](../README.md)。

## Knowledge Map

- [skills 与 agent 定义是手工同步的双份拷贝，改完必须 diff](skills-and-agents-are-manual-copies.md) —— `.agents/skills/` 与 `.claude/skills/`、`.claude/agents/*.md` 与 `.codex/agents/*.toml` 都是双份；漏同步不报错，只会静默漂移。

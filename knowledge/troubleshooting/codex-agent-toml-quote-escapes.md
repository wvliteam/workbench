# Codex agent TOML 的 description 里不能内嵌裸双引号

`.codex/agents/*.toml` 的 `description` 是 TOML basic string，内层直接写 `"` 会破坏整个文件的解析（agent 加载不出来），要把引号内容换成「」。`developer_instructions` 的 `"""` 多行字符串里单双引号都安全（只要不出现连续三个 `"`）。

## 依据
2026-09-07 实测：`tomllib` 解析 `description = "问"X"时使用"` 报 `TOMLDecodeError: Expected newline or end of document after a statement`；换成 `问「X」时使用` 后解析通过。同日写 `.codex/agents/knowledge.toml` 时在评审中抓住，改后解析验证通过。

## 适用范围
所有 Codex custom agent 定义（`.codex/agents/*.toml`）。Claude 端 `.claude/agents/*.md` 的 YAML frontmatter 不受影响（YAML 处理引号规则不同）。

## 失效条件
Codex 改用其他 agent 定义格式，或 description 字段改为 literal string（单引号包裹）。

## 来源
flow main「复盘沉淀流程 + knowledger 角色」，T2 任务。

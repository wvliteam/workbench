# 多端资产是单源软链：skills 与角色定义都不需要双写

**（2026-09-12 修订：原条目名与前半段写的「skills 是手工同步的双份拷贝」在 `d606944`（2026-09-11）后已失效 —— `.agents/skills` 现在是软链。文件名随内容改为 `multi-end-assets-share-one-source.md`。）**

两端共用一份资产，靠软链而不是复制：

- **skills**：唯一正文在 `.claude/skills/`，`.agents/skills` 是指向它的软链（`d606944` 起）。此前是两份手工同步的拷贝，靠人记得 `diff -r` —— 实测漂移过，同一类问题还有更早的两份 post-tool guard（见 roma-comparison.md 跨端节）。
- **角色定义**：唯一正文在仓库根 `agents/`（每角色一份 `.toml` 为源、一份 `.md` 由 `scripts/generate_agents.py` 生成），`.claude/agents/*.md` 与 `.codex/agents/*.toml` 都是指向根目录的软链（`0c7e7ab` 起）。

软链模型买到的是**漂移不可能**：改一处全端生效，「忘了同步另一份」这个失败模式不存在。代价是新增资产有步骤要记 —— 新 skill 建在 `.claude/skills/` 即可（`.agents/skills` 是整目录软链，自动可见）；新角色先写 `agents/<名>.toml`，跑 `python3 scripts/generate_agents.py` 生成 `.md`，两端软链手工加（生成脚本只管 `.md` 内容，不建软链）。

## 依据

- 2026-09-12 复核：`ls -la .agents/` 显示 `skills -> ../.claude/skills`；`.claude/agents/` 与 `.codex/agents/` 每个条目都是软链（`analyst.md -> ../../agents/analyst.md`）。
- `wb.py selfcheck` 的静态布局断言（`wb_selfcheck_static.check_static_layout`）：`.agents/skills` 是软链时必须 resolve 到 `.claude/skills`；**不是软链**才退回逐文件比对两棵目录树。两种布局都守得住，回退到双份拷贝也不会静默漂移。同一处还断言 `agents/*.toml` 能被 `tomllib` 解析 —— TOML 里嵌裸双引号会让角色静默加载不出来（见 [codex-agent-toml-quote-escapes.md](../troubleshooting/codex-agent-toml-quote-escapes.md)）。

## 适用范围

本工作区的 skills 与角色定义分发。判断某份资产要不要双写：先 `ls -la` 看是不是软链 —— 是软链就只改源；不是软链才需要两份都改并 `diff`。

## 失效条件

- 软链模型回退（`.agents/skills` 或两端 agents 变回正文副本）：双写与 `diff` 义务恢复，selfcheck 的逐文件比对分支接手守。
- skills 改成构建产物生成：以生成脚本的单源为准，本条的「改源即可」仍成立，只是「源」换了位置。

## 来源

flow main「复盘沉淀流程 + knowledger 角色」T3/T4 任务；2026-09-11 文档对齐时按失效条件触发第一次修订，2026-09-12 补 `d606944` 之后 skills 也转为单源的事实。

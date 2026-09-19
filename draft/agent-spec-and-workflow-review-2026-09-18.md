# Agent 职责、流程规范与工作流约束全仓审计报告（2026-09-18）

> **时点快照**：针对软件开发工作台核心协作文档（[AGENTS.md](../AGENTS.md) / [CLAUDE.md](../CLAUDE.md)）、架构与设计文档（[docs/](../docs/)）、角色定义体系（[agents/](../agents/)、[references/workspace/](../references/workspace/)）、技能编排（[.claude/skills/](../.claude/skills/)）与 Python 状态内核实现（[.claude/hooks/](../.claude/hooks/)）进行的一次深度交叉审查。
>
> **审查焦点**：Agent 职责边界、六阶段准出与交接规范、工作流与权限约束细节，重点挖掘**与项目实现冲突**、**自我冲突/前后矛盾**以及**过度设计冗余可简化**之处。

---

## 目录

- [一、总览与问题矩阵](#一总览与问题矩阵)
- [二、第一部分：与项目实现冲突（文档宣称 vs 代码实现）](#二第一部分与项目实现冲突文档宣称-vs-代码实现)
  - [1. 硬链接别名防线（`st_nlink > 1`）在实现中完全缺失](#1-硬链接别名防线st_nlink--1在实现中完全缺失)
  - [2. 架构与调度文档仍在指导 Agent 运行已遭特权拦截的 `role set`](#2-架构与调度文档仍在指导-agent-运行已遭特权拦截的-role-set)
  - [3. `reviewer` 宣称的 `*.md` 权限与直接修改流程定义被守卫硬拦截](#3-reviewer-宣称的-md-权限与直接修改流程定义被守卫硬拦截)
  - [4. `architect` 写入范围包含 `docs/**` vs 内核默认范围无此路径](#4-architect-写入范围包含-docs-vs-内核默认范围无此路径)
  - [5. `analyst` 开头职责描述遗漏单仓画像三件套权限](#5-analyst-开头职责描述遗漏单仓画像三件套权限)
  - [6. 未认领仓库探针路径在架构文档中仍记为旧单级路径](#6-未认领仓库探针路径在架构文档中仍记为旧单级路径)
  - [7. Codex 端 Hook 数量与事件描述滞后（遗漏 `user-prompt`）](#7-codex-端-hook-数量与事件描述滞后遗漏-user-prompt)
  - [8. 核心状态模型表遗漏 `task-agents.jsonl` 与 `sessions/`](#8-核心状态模型表遗漏-task-agentsjsonl-与-sessions)
- [三、第二部分：自我冲突与前后矛盾（文档逻辑打架）](#三第二部分自我冲突与前后矛盾文档逻辑打架)
  - [1. `agents/architect.toml` 中关于前端任务依赖 `--deps` 的致命自相矛盾](#1-agentsarchitecttoml-中关于前端任务依赖---deps-的致命自相矛盾)
  - [2. `agents/backend-developer.toml` 中关于契约 owner 权限的病句矛盾](#2-agentsbackend-developertoml-中关于契约-owner-权限的病句矛盾)
  - [3. `wb_const.py` 注释自我冲突（`reviewer` 是否分配 `*.md`）](#3-wb_constpy-注释自我冲突reviewer-是否分配-md)
  - [4. 降级模式（无角色 subagent 时）操作指引与权限守卫硬规则冲突](#4-降级模式无角色-subagent-时操作指引与权限守卫硬规则冲突)
  - [5. `docs/wb-init.md` 内部演进记录与正文决策未同步](#5-docswb-initmd-内部演进记录与正文决策未同步)
- [四、第三部分：冗余与可简化部分（架构过度设计与信息冗余）](#四第三部分冗余与可简化部分架构过度设计与信息冗余)
  - [1. `references/workspace/<role>/` 体系严重空心化（16 个空壳文件浪费调用）](#1-referencesworkspacerole-体系严重空心化16-个空壳文件浪费调用)
  - [2. 各 Agent TOML 中大段逐字重复的公共规范模版](#2-各-agent-toml-中大段逐字重复的公共规范模版)
  - [3. TOML 与 Markdown 角色定义双轨维护缺乏自检强一致性校验](#3-toml-与-markdown-角色定义双轨维护缺乏自检强一致性校验)
  - [4. 文档失效锚点与死链接](#4-文档失效锚点与死链接)
- [五、建议修复与演进路线图](#五建议修复与演进路线图)

---

## 一、总览与问题矩阵

本次审查累计发现 **17 项** 具体问题，分类分布如下：

| 类别                   | 数量 | 核心影响                                                                               |
| :--------------------- | :--: | :------------------------------------------------------------------------------------- |
| **与项目实现冲突**     | 8 项 | 文档描述不存在的安全机制、指导 Agent 跑已被拦截的特权命令、权限范围承诺与守卫策略脱节  |
| **自我冲突与前后矛盾** | 5 项 | 核心示例与文字规范相反（直接摧毁并行开发）、语病导致角色不知所措、降级指引引发权限死锁 |
| **冗余与可简化**       | 4 项 | 16 个空壳文件浪费 subagent 首轮调用与上下文窗口、跨文件规则死板复制、双轨定义校验断层  |

---

## 二、第一部分：与项目实现冲突（文档宣称 vs 代码实现）

### 1. 硬链接别名防线（`st_nlink > 1`）在实现中完全缺失

- **文档出处**：[AGENTS.md:226](../AGENTS.md#L226)
  > “硬链不用解析侧解，用 inode 判：`ln .workbench/flows/main/state.json innocent.md` 之后写 `innocent.md`，`Path.resolve()` 分不清同一 inode 的另一个目录项，别名按 `*.md` 命中角色范围一路放行，内容直改 `state.json` —— 冻结防线与「状态只能经 wb.py 改」同时失效且不留哈希痕迹。`_check_write_target` 因此在冻结检查之后加一道：目标**已存在且是普通文件**且 `st_nlink > 1` 即拒...”。
- **项目实现**：
  检索全仓代码（包括 [.claude/hooks/wb_guard.py](../.claude/hooks/wb_guard.py)），`st_nlink` **出现次数为 0**。
- **实测与影响**：
  在工作区通过 `ln .workbench/flows/main/state.json test_alias.md` 建立硬链接后，由于 `test_alias.md` 符合后端开发者的 `*.md` 白名单且不在冻结清单字符串中，Write/Edit 工具调用直接被守卫放行，可无痕篡改 `state.json`。文档描述了一道**完全虚构、并未落地**的防线。

### 2. 架构与调度文档仍在指导 Agent 运行已遭特权拦截的 `role set`

- **文档出处**：
  - [docs/roles.md:61-72](../docs/roles.md#L61-L72) 保留专门小节“每个角色的开工三步”：
    > “所有 agent 定义的开头都是同一个模式：
    >
    > 1. `python3 .claude/hooks/wb.py role set <自己>` # 收紧写入范围
    > 2. 读上游产物
    > 3. `python3 .claude/hooks/wb.py task start <ID>`
    >    `role set` 放在第一步而不是由编排者代设，原因是 subagent 一定会执行自己的第一条指令，而编排者可能忘。”
  - [docs/scheduling.md:127](../docs/scheduling.md#L127)：“`--role-lock` 是给编排者用的便捷开关（`start` 的同时 `role set`）。subagent 自己开工时通常先 `role set` 再 `task start`，两种路径等价。”
  - [docs/architecture.md:229](../docs/architecture.md#L229) 流程图明确包含：“`role set → task start → 读契约 → 写代码...`”。
- **项目实现**：
  - 在 [.claude/hooks/wb_guard.py:751](../.claude/hooks/wb_guard.py#L751) 与 `PRIVILEGED_WB` 中，`("role", "set")` 与 `("role", "clear")` 属于特权子命令，角色 subagent 运行必然被拦截，输出：
    > `[工作台 Workflow Guard] 拒绝：角色 X 不能跑 role set：它改的是主线程与非角色 agent 的写入范围兜底。报回编排者，由主线程决定。`
  - 所有的 [agents/\*.toml](../agents/) 以及 [AGENTS.md:189](../AGENTS.md#L189) 均已纠正为“无需也不能自己 `role set`（会被守卫拦，纯噪声）”，但 `docs/roles.md`、`docs/scheduling.md`、`docs/architecture.md` 严重滞后，且大段论述与现有设计截然相反。

### 3. `reviewer` 宣称的 `*.md` 权限与直接修改流程定义被守卫硬拦截

- **文档出处**：
  - [agents/reviewer.toml:11](../agents/reviewer.toml#L11) 声明：“写入范围：`.workbench/artifacts/*/retro/**`（当前需求线的 retro 目录）、`docs/**` 与 `*.md`（落 ADR、补说明属于评审产出）...”。
  - [agents/reviewer.toml:80](../agents/reviewer.toml#L80) 声明：“适合改流程的（门禁规则、角色定义、CLAUDE.md 条目）→ 写明改哪个文件的哪一条，**这属于你的评审产出，可以直接改**”。
- **项目实现**：
  - 在 [.claude/hooks/wb_const.py:209](../.claude/hooks/wb_const.py#L209) 中，`DEFAULT_ROLE_SCOPES["reviewer"]` 仅有 `[".workbench/artifacts/*/retro/**"]`，`*.md` 已经被**完全移除**（注释自述：为了防止裸 `*.md` 跨越目录漏写进 `knowledge/`）。
  - 在 `wb_const.py:282` 中，`agents/`（角色定义）与 `.claude/`（门禁规则）均被纳入 `GUARDED_PREFIXES`。`reviewer` 既没有 `agents/**` 权限，也没有 `.claude/**` 权限，如果按照 prompt 去“直接修改门禁规则或角色定义”，必被守卫硬拦。
  - [wb-flow/SKILL.md:236](../.claude/skills/wb-flow/SKILL.md#L236) 明确规定：流程级沉淀是由**编排者（主线程）**在收尾第 4 步落地的，而不是由 reviewer subagent 直接修改。

### 4. `architect` 写入范围包含 `docs/**` vs 内核默认范围无此路径

- **文档出处**：[agents/architect.toml:11](../agents/architect.toml#L11)：“写入范围：`.workbench/artifacts/*/design/**`、`.workbench/contracts/**`、`docs/**`”。
- **项目实现**：
  [.claude/hooks/wb_const.py:189](../.claude/hooks/wb_const.py#L189) 的 `DEFAULT_ROLE_SCOPES["architect"]` 仅配置了：
  ```python
  "architect": [
      ".workbench/artifacts/*/design/**", ".workbench/contracts/**",
  ],
  ```
  没有 `docs/**`。虽然 `docs/` 不在 `GUARDED_PREFIXES` 之中因而不会被误拦，但 prompt 中的声明与内核 `role_scopes` 权威定义脱节。

### 5. `analyst` 开头职责描述遗漏单仓画像三件套权限

- **文档出处**：[agents/analyst.toml:11](../agents/analyst.toml#L11) 开头第一句话声称：“你的写入范围被收窄到 `.workbench/artifacts/*/analyze/**`。你不修改任何代码”。
- **项目实现**：
  在 [.claude/hooks/wb_const.py:187](../.claude/hooks/wb_const.py#L187) 中，分析师的默认可写范围包含画像三件套：
  ```python
  "analyst": ([".workbench/artifacts/*/analyze/**"]
              + [f"repos/*/*/{f}" for f in REPO_PROFILE_FILES]),
  ```
  TOML 开头的概括遗漏了其在 `repos/<项目>/<仓库>/` 下的写权限，且与其后文第 33-41 行指导其编写画像三件套产生描述性断层。

### 6. 未认领仓库探针路径在架构文档中仍记为旧单级路径

- **文档出处**：[docs/architecture.md:195](../docs/architecture.md#L195)：“判定按守卫自己的方式做：拿 `repos/<仓库>/src/probe.{ts,py}` 去撞两个开发角色的模式，撞不上就算没人认领。”
- **项目实现**：
  自 2026-09-14 迁移至两级项目目录后，[.claude/hooks/wb_core.py:1488](../.claude/hooks/wb_core.py#L1488) 实际使用的探针路径是：
  ```python
  fnmatch.fnmatch(f"repos/.source/{name}/*/src/probe{ext}", p)
  ```
  架构文档遗漏了 `.source/` 层次和通配结构。

### 7. Codex 端 Hook 数量与事件描述滞后（遗漏 `user-prompt`）

- **文档出处**：
  - [docs/permissions.md:320](../docs/permissions.md#L320) 小节名为“## 其余三个 hook”，表格仅列出 `PostToolUse`、`SessionStart`、`SubagentStop`。
  - [docs/architecture.md:21](../docs/architecture.md#L21) 写道：“拦截层 settings.json 注册的 4 个 hook”。
- **项目实现**：
  在 [.codex/hooks.json:40-51](../.codex/hooks.json#L40-L51) 中，Codex 端实际注册了 **5 个 hook**，包含了 `UserPromptSubmit`（对应 `wb.py hook user-prompt --format codex`）。在 `wb_cli.py:1372`、`wb_guard.py:1297` 以及 `wb_selfcheck_static.py:32` 中均有对应实现与自检，但两处架构与权限文档完全遗漏了 `user-prompt`。

### 8. 核心状态模型表遗漏 `task-agents.jsonl` 与 `sessions/`

- **文档出处**：[docs/architecture.md:52-61](../docs/architecture.md#L52-L61) 表格“一份主状态 + 五份 hook 缓存”仅列出了 7 个文件。
- **项目实现**：
  `.workbench/task-agents.jsonl`（任务 ID ↔ agent_id 绑定，调度与产物归并核心）和 `.workbench/sessions/`（归属首写闸门会话标记，已列入 `FROZEN_ALWAYS`）已成为工作台的核心持久化设施，但在状态总览表中未获得等同地位，只在后续散文零碎提及。

---

## 三、第二部分：自我冲突与前后矛盾（文档逻辑打架）

### 1. `agents/architect.toml` 中关于前端任务依赖 `--deps` 的致命自相矛盾

- **冲突位置**：[agents/architect.toml:117](../agents/architect.toml#L117) vs [agents/architect.toml:124](../agents/architect.toml#L124)
- **矛盾重现**：
  - 在第 114-120 行给出的任务拆解示例中：
    ```bash
    python3 .claude/hooks/wb.py task add --title "用户列表接口" \
      --role backend-developer --phase develop --contracts user-api
    python3 .claude/hooks/wb.py task add --title "用户列表页" \
      --role frontend-developer --phase develop --deps T1 --contracts user-api
    ```
    （注意：**前端任务显式加上了 `--deps T1`！**）
  - 紧接着在第 124 行规则第一条加粗写道：
    > **“- 依赖只写真依赖。契约锁定后，前端不依赖后端实现完成 —— 双方对着契约并行。写 `--deps` 会串行化，白等。”**
  - 在 [docs/scheduling.md:20-22](../docs/scheduling.md#L20-L22) 中，第 117 行的写法被作为明确的**反例（错）**专门展示！
- **危害**：架构师 Agent 在 design 阶段拆解任务时，极易照抄第 117 行的示例，为前端任务添加 `--deps T1`，直接将并行开发降级为串行开发，抵消契约先行的全部价值。

### 2. `agents/backend-developer.toml` 中关于契约 owner 权限的病句矛盾

- **冲突位置**：[agents/backend-developer.toml:46](../agents/backend-developer.toml#L46)
- **矛盾重现**：
  > “即使你是契约 owner，也不能直接改冻结 contract 或 `design.md`，不能在实现侧私自扩展字段；**hook 校验下你（owner）之外只有 architect 能跑 unlock / bump，你跑了会被拦**，被拦不是错误，报回主线程即可。”
- **分析**：前半句“你（owner）之外只有 architect 能跑”明确肯定了 owner 的权限，后半句紧接着却说“你跑了会被拦”。在实现中（`wb_guard.py:770`），契约的登记 owner 以及 architect 均被允许执行 `unlock/bump`。此处病句使得后端开发 subagent 在面对自身拥有的契约时不知所措。

### 3. `wb_const.py` 注释自我冲突（`reviewer` 是否分配 `*.md`）

- **冲突位置**：[.claude/hooks/wb_const.py:165](../.claude/hooks/wb_const.py#L165) vs [.claude/hooks/wb_const.py:209-214](../.claude/hooks/wb_const.py#L209-L214)
- **矛盾重现**：
  - 第 165 行注释：“`*.md 给开发与 reviewer：写 README、补接口说明、落 ADR 都是本职...`”。
  - 第 209-214 行代码与注释：“`"reviewer": [".workbench/artifacts/*/retro/**"], 上游这里给 reviewer 的裸 *.md 同样移除：fnmatch 的 * 跨 /，留着它等于把知识出口的收窄整个抵消掉。`”。
  - 同一个代码文件，前面的注释与后面的实际配置和注释直接冲突。

### 4. 降级模式（无角色 subagent 时）操作指引与权限守卫硬规则冲突

- **冲突位置**：[wb-flow/SKILL.md:171](../.claude/skills/wb-flow/SKILL.md#L171)
- **矛盾重现**：
  - skill 对降级模式（Harness 无法派发角色 Agent）的指引：“角色产物由主线程代写，但同样必须先过门禁再冻结，**`role set <角色>` 照打**（`status` 里能看出当前范围）”。
  - 但在权限守卫中，主线程一旦调用 `role set <角色>`，就会在 `.workbench/role` 写入该角色名；一旦该文件存在，`wb_guard.py` 的 `current_role()` 会在主线程调用工具时退回读取该文件，并把主线程直接当作该角色进行权限判定！
- **危害**：主线程代写完 PM 产物后，如果打了 `role set pm`，其身份就会被锁死在 PM 范围，紧接着去写后端或前端代码时会被守卫全部阻拦；且主线程执行 `flow switch` 等特权子命令也会触发冲突。降级模式下主线程应依靠“无 agent_type 且无 role 文件”的通用身份执行代写，不应执行 `role set`。

### 5. `docs/wb-init.md` 内部演进记录与正文决策未同步

- **冲突位置**：[docs/wb-init.md:15](../docs/wb-init.md#L15) vs [docs/wb-init.md:48-49](../docs/wb-init.md#L48-L49)
- **矛盾重现**：
  - 第 15 行：“明确不做：...仓库画像 overview/setup/test 三件套已于 2026-09-14 纳入...”（在“明确不做”小节中括号修补）。
  - 第 48-49 行决策表格中，单仓画像与独立画像任务被作为核心决策详述。

---

## 四、第三部分：冗余与可简化部分（架构过度设计与信息冗余）

### 1. `references/workspace/<role>/` 体系严重空心化（16 个空壳文件浪费调用）

- **现状分析**：
  - 项目在 `references/workspace/` 下为 8 个角色各建了 `index.md` 和 `role.md`（共 16 个文件，总字数仅约 80 行）：
    - 例如 [references/workspace/pm/role.md](../references/workspace/pm/role.md) 仅有 4 行文本（38 个汉字），纯粹是 `agents/pm.toml` 开头指令的二次复述；
    - [references/workspace/pm/index.md](../references/workspace/pm/index.md) 仅有 8 行，是一张毫无增量信息的超链接表格。
  - 但每一个 `agents/*.toml` 都在第一条指令强制规定：“开工前阅读：`references/workspace/<role>/index.md`”。
- **成本与负面影响**：
  - 每个 subagent 启动时，必须平白无故消耗 1~2 轮工具调用去阅读没有任何干货的占位文件；
  - 换来的是每次微调角色职责时，必须在 TOML 与 `references/workspace/<role>/role.md` 两个地方同步维护。
- **优化建议**：
  彻底废弃 `references/workspace/<role>/` 目录下的 16 个空壳文件。角色的业务原则直接内联在 `agents/<role>.toml`；跨角色通用规范统一收拢在 [references/output-contract.md](../references/output-contract.md)。

### 2. 各 Agent TOML 中大段逐字重复的公共规范模版

- **现状分析**：
  - 8 个角色的 TOML 都逐字复制了大量的公共段落：
    - “必读（开工前读完）：`references/output-contract.md` —— 全角色共用的输出信封与禁止事项。底线：结论≤5 条带 文件:行号 证据指针；运行过命令就给命令原文+退出码；禁止给 PASS/FAIL 判定；返回前收敛全部后台任务...”；
    - “会话收尾与知识沉淀：完成本次会话前，总结发现的可重复利用经验：通用经验交由主线程派 knowledger 查重后写入 knowledge/...”。
- **优化建议**：
  [references/output-contract.md](../references/output-contract.md) 已经作为全局必读规范存在。所有信封约束、禁止事项、退出码判定与知识沉淀收敛应作为该文档的权威条款。TOML 中仅需保留一句话“遵循 `output-contract.md`”，将提示词空间留给角色专属职责。

### 3. TOML 与 Markdown 角色定义双轨维护缺乏自检强一致性校验

- **现状分析**：
  - 根目录 `agents/*.toml` 通过 [scripts/generate_agents.py](../scripts/generate_agents.py) 生成 `agents/*.md`，各端（Claude、Codex、Comate）通过符号链接引用。
  - 当前 `wb.py selfcheck`（位于 `wb_selfcheck_static.py`）只通过 `tomllib` 验证了 TOML 本身的语法是否可解析，**并没有调用 `generate_agents.py --check`**。
  - 实测向 `agents/*.toml` 添加或修改规则后，`wb.py selfcheck` 依然全绿通过，导致开发者极易忘记执行重新生成脚本，造成各端加载的 Markdown 角色定义发生漂移。

### 4. 文档失效锚点与死链接

- [AGENTS.md:219](../AGENTS.md#L219) 链接到 `[docs/permissions.md#bash-绕过检查]`，而目标标题为 `## Bash 分支：绕过检查`（GitHub 锚点为 `#bash-分支绕过检查`）。
- [docs/permissions.md:126](../docs/permissions.md#L126) & [docs/roles.md:160](../docs/roles.md#L160) 链接到 `architecture.md#跨仓库同一个语义的反面`，该小节已不存在。
- [docs/permissions.md:112](../docs/permissions.md#L112) 链接到 `gates.md#retro-经验已沉淀knowledge_written`，该小节已不存在。

---

## 五、建议修复与演进路线图

按照“收益 × 成本”排序，给出建议的落地整改清单：

```
[P0 立即纠错] 修复 architect.toml 错误依赖示例、reviewer.toml 越权承诺、backend-developer.toml 语病
     │
[P1 一致性对齐] 清除 docs/ 中陈旧的 role set 说明；诚实处理 st_nlink 描述；同步画像范围
     │
[P2 架构精简化] 移除 references/workspace/<role>/ 16 个空壳文件；挂接 generate_agents --check 到 selfcheck
     │
[P3 细节清理] 修正 wb_const.py 注释冲突；修复死锚点；同步 Codex 5 hooks 文档
```

### 1. P0 级（直接影响 Agent 行为，10 分钟可修复）

1. **修正 `agents/architect.toml` 第 117 行**：将前端任务示例中的 `--deps T1` 删去，消除与第 124 行及 `scheduling.md` 的致命冲突。
2. **修正 `agents/reviewer.toml` 第 11、80 行**：移除 `*.md` 范围描述；明确流程与门禁修改由主线程在收尾时执行，reviewer 仅在 `retro.md` 提出改进条目。
3. **理顺 `agents/backend-developer.toml` 第 46 行**：纠正语病，明确“除了该契约 owner 与 architect 外，其他角色执行 unlock/bump 均会被拦截”。

### 2. P1 级（消除误导与潜在死锁，30 分钟可修复）

1. **清理架构与调度文档的 `role set` 残留**：更新 `docs/roles.md`、`docs/scheduling.md`、`docs/architecture.md`，统一阐明“角色按 hook 载荷 `agent_type` 自动判定，subagent 严禁调用特权命令 `role set`”。
2. **修正降级模式指引**：在 `wb-flow/SKILL.md:171` 中移除“`role set <角色>` 照打”，改为保持主线程原生身份代写产物。
3. **决断 `st_nlink` 硬链防线**：若实现层决定不维护硬链接检查，则从 `AGENTS.md:226` 中诚实删除该段描述；若需要该防线，则在 `wb_guard.py:390` 附近的 `_check_write_target` 中补齐 `os.stat().st_nlink > 1` 判断。
4. **补齐 `agents/analyst.toml` 画像权限说明**：在开头明确指出包含单仓画像三件套。

### 3. P2 级（架构降噪与自动化防护，1 小时可完成）

1. **清理空壳参考文档**：删除 `references/workspace/<role>/` 下的 16 个占位文件，精简 Agent 启动提示词。
2. **挂接生成一致性检查到 selfcheck**：在 `wb_selfcheck_static.py` 中增加对 `scripts/generate_agents.py --check` 的断言调用，杜绝 TOML 与 Markdown 漂移。
3. **对齐探针路径与 Codex Hook 描述**：修正 `docs/architecture.md` 中的 `repos/.source/...` 探针路径与 `docs/permissions.md` 中的 5 hooks 清单。

### 4. P3 级（机械清理）

1. 修正 `wb_const.py:165` 注释中的 `reviewer` 残留。
2. 修复各文档间的死锚点超链接。

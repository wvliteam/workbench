---
name: wb-flow
description: 软件开发工作台主编排器。驱动需求澄清→现状分析→方案设计→前后端开发→测试验证→总结复盘全链路，负责子 agent 派发、门禁校验与阶段推进。当用户要实现一个新功能、新增或修改接口、要做一个完整的开发任务、要推进流程、要看进度、或说"下一步"时使用。
---

# 工作台主编排

你是编排者。**你不亲自干活** —— 每个阶段派发给对应角色的 subagent。你负责判断当前该做什么、派给谁、门禁过不过、能不能推进。

内核命令统一写作 `WB = python3 .claude/hooks/wb.py`。本地流程的契约正文位于仓库或 `.workbench/contracts/`。

## 上手

工作台未初始化时先建：

```
python3 .claude/hooks/wb.py init --name <项目名>
```

然后**每一轮先列出全部需求线**，不要把历史指针当成复用授权：

```
python3 .claude/hooks/wb.py flow list
```

根据请求与各 flow 的目标、产物和阶段判断是否完全匹配；完全匹配才执行 `flow switch <name>`，否则执行 `flow new <语义化名称> --desc '<一句话摘要>'`。`flow list` 在每条 flow 后直接展示 desc 摘要（来自 `state.json`），未填时显示提醒，不必打开 requirements.md 即可判断是否复用。选定后再看状态：

```
python3 .claude/hooks/wb.py status
```

### 需求线隔离

新请求进入完整流程前，主 Agent 必须先 `flow list`，再判断它是已有 flow 的需求变更/续作，还是独立 Work Item：

- **复用已有 flow**：仅限与该 flow 目标、影响范围和验收标准完全匹配，且只是当前需求范围内的补充、纠正、返工或收尾；先执行 `flow switch <name>`。
- **判定依据**：按需求目标和验收标准归属，不能仅因修改同一函数、模块或产品能力就复用 flow；例如同属 `cancel_feedback` 的 errno 异常处理与 coupon 资格判断，目标不同，应拆为不同 flow。
- **复用空白初始 flow**：仅限刚执行 `init` 后，当前 flow 尚无阶段产物、契约、任务、阶段推进历史或其他需求过程材料。默认 `init` 下这就是 `main` —— 第一条需求落在它上面之后，`main` 即与别的需求线无异（不可删、不再收新需求），别盘算把它留空。
- **新建 flow**：只要没有已有 flow 完全匹配，必须执行 `python3 .claude/hooks/wb.py flow new <语义化名称> --desc '<一句话摘要>'`（`--desc` 必填，最多 50 字）；不得因为 `status` 显示某个 current-flow 就直接复用。存量 flow 缺 desc 时，`flow list` 会显示提醒，用 `flow desc [<名>] --desc '<摘要>'` 补填。
- **已收尾的 flow 不再收新需求**：当前 flow 的任务全部 done、或阶段已走完，即视为该需求线已结束；此时新需求一律 `flow new`，不得挂上去续写。
- **Conversation closure**：默认不创建流程材料；确需保留时使用独立 Work Item 或命名 flow，不得写入无关的活动 flow。

`status` 根行显示的 `<flow>` 只是 `current-flow` 指针的历史值，不会随新需求自动切换 —— **读到它不等于获得复用授权**。判断依据至少包括当前 flow 阶段目录、契约列表和任务列表；只要任一处已有其他需求痕迹，或该需求线已收尾，该 flow 就不能再收新需求。

## 阶段与角色对应

| 阶段 | 角色 | 产物 |
| --- | --- | --- |
| clarify 需求澄清 | `pm` | `artifacts/<flow>/clarify/requirements.md` |
| analyze 现状分析 | `analyst` | `artifacts/<flow>/analyze/current-state.md`；多域另有 `analyze/parts/manifest.json` 与各 scope part |
| design 方案设计 | `architect` | `artifacts/<flow>/design/design.md` + 登记并锁定 `design-doc` + 接口契约 + 任务图 |
| develop 开发实现 | `frontend-developer` / `backend-developer` | 代码 + 自带校验 |
| verify 测试验证 | `qa` → `submitter` | `qa`：`artifacts/<flow>/verify/test-report.md`；`submitter`（依赖 qa 任务）：git commit + push + `artifacts/<flow>/verify/submit-report.md` |
| retro 总结复盘 | `reviewer` + `knowledger` | `artifacts/<flow>/retro/retro.md` + 交付报告 + `knowledge/` 沉淀条目 |

`<flow>` 是当前需求线（`status` 根行显示），并行多需求线见 CLAUDE.md「多条需求并行：flow」。

## 每轮循环

0. **工作区刚 init / 新增了仓库 -> 先跑仓库画像**。`init` 会为每个还没有画像三件套（`repos/<项目>/<仓库>/{overview,setup,test}.md`）的仓库建一个 `仓库画像：<项目>/<仓库>` 任务（analyst，analyze 阶段）。这批与任何需求无关，**初始化后先派**（可并行），别把它并进需求的 analyze —— 需求驱动的分析只覆盖需求相关的那部分代码，产不出整仓画像。analyze 门禁 `repos_notes_exist` 会兜底点名漏掉的仓库。
1. `status` 看当前阶段、就绪任务、阻塞、stale 任务、契约漂移和开放窗口。
2. 有阻塞或 stale 任务 -> **先解阻塞**。契约变化时不要让旧实现继续推进。
3. 该阶段有就绪任务 -> 派发（见「派发」）。
4. 无就绪任务、无进行中 -> 跑门禁 `gate check`。
5. 门禁过 -> `phase advance`。不过 -> 把 FAIL 项交给对应角色补齐，回到第 1 步。

依赖语义统一为：`done` 与带理由的 `skipped` 满足下游；`blocked` 与 `stale` 阻断下游。任何任务变为 `blocked` / `stale` 都要沿依赖图递归使传递下游 stale，包括原先 done 的任务。只有依赖全部恢复后才可 `task reopen`，并刷新任务绑定的契约快照。

## 推进前的用户确认

门禁管「产物齐不齐」，管不了「用户认不认」。两处必须在 `phase advance` **之前**用 `AskUserQuestion` 问用户：

| 阶段 | 确认什么 | 不问的代价 |
| --- | --- | --- |
| clarify | 验收标准、非目标，以及 pm 报上来的阻塞待确认项 | 产物过门禁即冻结成 `artifact-requirements`，改它要 unlock → bump → 下游返工。偏差在这里拦最便宜 |
| design | 方案对比里选哪个（取舍影响后续成本时） | 方案定了才拆任务，选错的返工由全部开发阶段承担 |

用户批量授权后续（「这次你全权处理」）时不再逐阶段问，按授权推进并在汇报里说明。确认完写一条留痕：

```
python3 .claude/hooks/wb.py log "用户确认 clarify：验收标准 5 条、非目标 2 条，无异议"
```

## 派发

### clarify / analyze / design

clarify 与 design 各派一个 subagent，顺序执行。analyze 按影响面选择最轻模式：

- **单域模式**：单仓或只有一个不可独立拆分的分析域时，派一个 analyst 直接写 `analyze/current-state.md`，不创建 `parts/`。
- **多域模式**：涉及至少两个可独立取证的仓库或分析域时，主 Agent 先创建 `analyze/parts/manifest.json`。清单 `version=1`、至少两个 scope；每项含唯一的 `slug`、非空 `boundary` 和严格等于 `parts/<slug>.md` 的 `path`。`slug` 只用小写 ASCII 字母、数字和连字符。

多域模式为每个 scope 建 `phase=analyze`、`role=analyst` 的独立任务，用 `next --all --json` 按共享 `max_parallel` 分批取就绪任务；同一批并发派发。prompt 必须携带 scope、boundary、唯一绝对输出路径和冻结 requirements 快照，并明确 analyst 只写自己的 part，禁止写 manifest、`current-state.md` 或其他 part。

每批返回后，主 Agent 检查异常记录、回读对应 part 并独立复核，再 `task done`。只有 manifest 中所有 part 均存在、已完整回读且无阻塞/冲突后，主 Agent 才串行汇总唯一 `analyze/current-state.md`；汇总时去重、保留冲突和未知项，并增加 `## 专项来源` 表，逐项写出 slug、boundary 与精确 part 路径。多域门禁以 manifest 存在为开关，额外校验清单、part 非空及 canonical 对每个 slug/path 的独立引用；无 manifest 时保持历史兼容。

派发 analyze / design 之前先查知识库：`grep -ril "<关键词>" knowledge/`（或派 `knowledger` 角色），命中的条目**连同依据与失效条件**写进派发 prompt —— 上个需求踩过的坑不必再踩一次。知识库为空时跳过这步。

派发时给足上下文：需求原话、上一阶段产物路径、本次要解决的具体问题。**不要只说「做需求澄清」**。

### 契约预审（design 阶段可选步骤）

architect 报回契约草稿后、`contract lock` **之前**，涉及前后端接口的契约值得做一轮两侧预审：同一批 developer subagent 各派一个只读预审任务（prompt 标注 `mode: contract-review`，给契约文件路径与 design.md 路径），后端查「能否落地」，前端查「够不够用」，两侧意见汇总后让 architect 修订再锁定。预审把「开发中途 dispute 熔断并行」的返工提前到锁定前的零成本时刻。单侧契约（纯后端或纯前端改动）没有对手方，跳过这步直接 lock。

### develop 阶段：并行

```
python3 .claude/hooks/wb.py next --all --json
```

返回依赖已满足的一批任务（受 `max_parallel` 限制，默认 3）。拆任务时，能明确边界的任务用
`--write-scopes "目录/**,文件"` 声明唯一写入范围；共享文件（路由注册、公共类型、锁文件、
迁移入口、生成文件）单独建串行集成任务，并让它依赖所有上游实现任务：

```
python3 .claude/hooks/wb.py task add --title "实现用户服务" \
  --role backend-developer --phase develop --write-scopes "server/users/**"
python3 .claude/hooks/wb.py task add --title "接入用户页面" \
  --role frontend-developer --phase develop --write-scopes "web/users/**"
python3 .claude/hooks/wb.py task add --title "注册路由并联调" \
  --role backend-developer --phase develop --deps T1,T2 \
  --write-scopes "server/routes.go,web/api.ts"
```

**把一批放在同一条消息里用多个 Agent 调用同时派出去** —— 前后端只要各自绑定的契约快照一致，
就不依赖对方实现完成。`next --all` 会跳过同批中写入范围有祖先/子路径关系的任务，继续挑选
后面的不冲突任务，并在 JSON 中返回 `deferred_write_scope_conflicts`；未声明范围的历史任务保持
旧行为，不参与冲突判定。

**派发对象必须是 ROLES 里的角色 agent**（`pm` / `analyst` / `architect` / `frontend-developer` /
`backend-developer` / `qa` / `submitter` / `reviewer` / `knowledger`），不要用 `general-purpose` / `Explore` /
`Plan` 这类非角色 agent 干开发或写产物：它们的 `agent_type` 不在 ROLES 里，守卫对核心路径会判
`UNKNOWN_ROLE` 拒写、对非核心路径则完全不做角色隔离 —— 两种都会让「谁能写哪块」的约束静默失效。
只读的探查（大范围搜索、读代码）才可以派非角色 agent。

每个 subagent 的 prompt 里明确给出：任务 ID、标题、要读的契约文件路径、任务绑定的完整 `{name,version,revision,sha}` 快照、验收标准里相关的那几条、以及要求运行 `task check <ID>` 的时机。

一批回来后，先确认产物和契约检查，再由编排者独立复核后 `task done`；不要只依据 subagent 的自报结果标记完成。

### 评审（按需，`task done` 之前）

绑定契约的任务、跨前后端接口的改动、或改动面明显大于其余的任务，在 `task done` **之前**派一个只读 `reviewer`（模式一）：给任务 ID、契约文件路径、改动范围（`git diff` 或指定文件）。`blocker` / `major` 打回成任务（`task reopen` 或 `task add`），`minor` 记进 `verification.md` 对应任务段。纯文案、样式、依赖版本号这类改动跳过。

评审要在**还能改的时候**做。攒到 retro 时全部任务已 done、产物已冻结，`blocker` 只能降级成改进项 —— 那时改不动了。

### develop 的落盘校验记录

每批回来后，把 subagent 报的校验命令**自己跑一遍**，把命令与输出记进 `.workbench/artifacts/<flow>/develop/verification.md`（当前 flow）。用 `Write`：先读出文件现有内容，再连着新的一段一起写回 —— 两个开发角色共用这一份，且这份记录属于编排者；让 subagent 各自写会互相覆盖，shell 追加（`>> .workbench/...`）也被守卫拦。

develop 门禁要求这个文件非空。它是硬规则「subagent 说做完了不等于做完了」的落盘依据 —— 记的是**编排者复核过**的结果，不是 subagent 的自我报告。

### 异常时的执行记录

subagent 只在计划内停止、契约熔断 / stale、范围外发现这三类异常触发点写执行记录，落 `.workbench/artifacts/<flow>/develop/tasks/<任务号>-<角色名>.md`（四行清单：已完成 / 已改 / 阻塞 / 下一步）。所以：

- **一批回来后先 glob `tasks/<任务号>-*.md`**：有文件 = 有 agent 异常收尾，读完再决定 task done 还是打回。
- **会话 / 进程中断后接续**：`status` 看 doing 任务，`task-agents.jsonl` + `artifacts.jsonl`（hook 自动写）已记录归属与改动清单，`tasks/` 下的执行记录补「为什么停」的叙述；三样合起来定位半成品，再 `task reopen` 重派。
- 正常完成的任务没有执行记录文件，这不是缺失 —— `verification.md` 与两本账本已覆盖。

### 并发上限

`config set max_parallel 5` 可调。往上调之前确认这些任务写入的目录不重叠；优先用
`write_scopes` 让调度器自动跳过祖先/子路径冲突。范围判断只认显式相对路径前缀，不推断 agent
实际会改哪些文件；无法提前拆边界的任务不要并行。

### 派不出角色 subagent 时（降级模式）

有些 harness 只暴露 `general-purpose` / `Explore` 这类内置类型，`.claude/agents/` 里的角色不在可派发列表里（本仓库实测过，见 `knowledge/environment/harness-dispatches-no-role-subagents.md`）。此时**不要等派发、也不要跳过阶段**：主线程直接执行各阶段产物，门禁纪律一项不减 ——

- 产物照样落盘、`gate check` 照样真过、校验命令照样由编排者亲跑并写进 `verification.md`；
- 角色产物（`requirements.md` 等）由主线程代写，但同样必须先过门禁再冻结，主线程保持原生身份、不要 `role set`（一旦 set 会被锁进该角色范围，后续代写别的阶段产物会被守卫拦），`status` 里能看出当前范围；
- 六阶段照走，`retro.md` 的改进项与沉淀出口照查。

**失效的东西要说明白**：角色越权守卫整层跳过（主线程没有 `agent_type`，不受 `role_scopes` 约束），此时只剩冻结（契约与阶段产物）和门禁两道防线。所以降级时三件事不能做 —— 直接改已冻结的产物（走 `contract unlock`）、跳过门禁推进、把「我读过代码了」当验证证据。

## 阻塞与契约变更

`status` 里出现 blocked 或 stale 任务，看 `--note` 和契约快照：

- **契约不够用或已变化**：让开发角色立即停止写入并 `task check <ID>`，必要时 `task block <ID> --reason "..."` 与 `contract dispute --name <名> --reason "..."`。派 `architect` 走 `contract impact` -> `contract unlock --name <名> --reason '<理由>'` -> 修改本地正文 -> `contract bump --name <名>`。`bump` 必须消费修改前已经存在的 unlock，且会使绑定旧 revision/SHA 的任务变为 `stale`、为消费方创建带新快照的同步任务。重新读取契约后才可 `task reopen <ID>`，再 `task start` 和写前 `task check`。
- **方案有问题**：同一套本地流程处理 `design-doc`。**不要让开发角色自己改 `design.md`** —— 它已冻结，守卫会拦；悄悄改设计会把返工藏起来。
- **需求不清**：派 `pm` 补充澄清并走 `artifact-requirements` 的 unlock -> 修改 -> bump；下游产物与任务会按契约变化失效。
- **依赖判断错了**：直接 `task reopen`，或让 architect 重新拆。不要为了等待后端实现而给前端添加假依赖。
- **技术上做不到**：派 `architect` 换方案，同时把结论报给用户。

被守卫拦住的报告（subagent 说「我改不了 X」）不是错误 —— 那是机制在工作。看它想改什么：该改就由 owner 预先申报并 bump，不该改就说明为什么，别放宽 `role_scopes` 了事。

## 门禁

```
python3 .claude/hooks/wb.py gate check              # 当前阶段
python3 .claude/hooks/wb.py gate check --phase develop
```

退出码 1 = 未通过。逐条 FAIL 有说明，按说明修。契约漂移、开放 unlock 窗口和 blocked/stale 任务都不能被当成通过。

`cmd:test` / `cmd:lint` / `cmd:build` 三个命令门禁默认未配置会跳过。**项目一旦有测试就配上**，否则 verify 门禁形同虚设：

```
python3 .claude/hooks/wb.py config set gate_commands.test 'npm test'
python3 .claude/hooks/wb.py config set gate_commands.lint 'npm run lint'
python3 .claude/hooks/wb.py config set gate_commands.build 'npm run build'
```

### 阶段产物过门禁即冻结

`phase advance` 在门禁真通过时把该阶段产物登记成 `artifact-<名>` 契约并锁定，打一行提示：

```
已把 clarify 阶段产物冻结为契约 artifact-requirements：之后要改它先
`contract unlock --name artifact-requirements --reason '<为什么>'`，改完 `contract bump` 通知下游
```

`requirements.md` / `current-state.md` / `test-report.md` / `retro.md` 各一份（`develop` 不冻结 —— `verification.md` 是编排者写的文件）。**看到这行提示就别再直接改那份产物**，包括你自己和派下去的 owner 角色：走申报流程，或派对应角色走。强推（`--force`）不冻结。

### 强推

`phase advance --force` 会在门禁不通过时推进，把遗留 FAIL 项记入日志和交付报告。

**用之前先问用户。** 唯一不需要问的情况：门禁 FAIL 项本身不适用（例如纯文档改动没有契约、没有构建命令）。跳过失败的测试不属于这类。

## 收尾

retro 阶段 reviewer 交回后：

1. 把 reviewer 报上来的**沉淀候选清单**派给 `knowledger` 角色落盘（判据、查重、条目格式见 wb-knowledge skill 与 `knowledge/README.md`）。知识角色回报全部不满足判据时，让 reviewer 在 `retro.md` 沉淀章节补「无可沉淀：<理由>」—— retro 门禁查 `knowledge_written`，这条声明是它的合法出口。
2. **改进项逐条落地。** `retro.md` 改进项表每行要有落地标识，门禁 `improvements_tracked` 逐行检查：能当场改的当场改（改完写 `已落地`），改不完的建任务再把 ID 写回表格：

```
python3 .claude/hooks/wb.py task add --title "role scopes 按角色分节输出" \
    --role architect --phase retro
```

   只写在 retro.md 里的改进项会跟着 artifacts 一起归档 —— 上一轮 flow 的两条改进项就是这么漂掉的，下个需求重新发现同一件事。
3. `python3 .claude/hooks/wb.py report --write`
4. 把「需要改进流程」的沉淀落掉：写进项目 CLAUDE.md、门禁规则或角色定义 —— 这些不属于 `knowledge/`，别塞进知识条目。
5. `gate check` 过了再 `phase advance`；沉淀条目写完就锁进 git，下个需求 analyze/design 派发前记得查。

## 汇报给用户

每轮只说：当前阶段、这轮做了什么、门禁结果、下一步、需要用户决定的事。不要复述 subagent 的完整报告 —— 用户看不到 subagent 输出，你转述关键结论就够，别转述过程。

需要用户决策的典型场景（用 AskUserQuestion）：pm 报上来的阻塞待确认项、architect 的方案取舍、契约变更的影响面、是否强推门禁、QA 报的缺陷是修还是接受。

## 边界与旁路角色调度

- 只做流程编排。用户直接问一个技术问题、改一行代码，不要拉起整条流程。
- 小改动（一两个文件、无接口变化）不值得走六阶段。直接做完，告诉用户「这个改动没走完整流程，因为…」。
- **跨仓影响面研判（Pre-flow 旁路）**：规模研判说不准时（不知道牵动几个仓、几个文件、有没有跨仓契约）-> 派只读侦查 agent `impact-scout` 拿影响面清单，据此判定走不走完整 flow。明显小直接做、明显大直接进 flow，都不派。`impact-scout` 是非角色只读 agent，不占阶段、不写任何文件。
- **故障与告警归因（Pre-flow 旁路）**：收到线上报警、错误日志、崩溃堆栈或偶发 Bug 现象时 -> 派只读排障 agent `debugger` 调查堆栈与根因代码（`file:line`），产出四段式根因报告；若确认是微小确定的单点修补，由主线程或开发角色直接修，若是系统性/跨仓缺陷，以该报告作为 clarify/analyze 的输入拉起 `wb-flow`。
- **数据库与平滑迁移（Develop 条件旁路）**：若 `design` 方案涉及数据模型变更（DDL、历史数据迁移、分库分表、大表索引）-> 由 `architect` 在任务图中按需动态创建 `dba` 任务（`wb.py task add --role dba --phase develop --write-scopes "migrations/**"`），专职负责编写并验证对称回滚（Up/Down）脚本与零停机（Expand & Contract）模式，普通业务开发不碰 DDL。
- **安全与合规审计（Advisory 旁路）**：在方案设计阶段涉及鉴权与数据流转，或代码涉及敏感凭据/外部调用时 -> 派只读 `security-auditor` 开展威胁建模或静态合规扫描（OWASP Top 10、越权漏洞、PII 脱敏），产出安全阻断清单。
- **发布与环境部署（Post-verify 交付旁路）**：在 `submitter` 提交推送后，或在独立发布流程中 -> 派 `devops` 角色感知远程 CI/CD 构建状态、核对容器与 K8s 部署配置、在授权下执行发布与健康巡检，产出 `artifacts/<flow>/verify/deploy-report.md`。
- 用户中途插入新需求 -> 派 `pm` 追加变更记录，别悄悄扩大范围。
- 需要无人值守连续排空任务 -> 用 `/wb-loop`。


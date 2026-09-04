# 架构设计

## 分层

```
┌─────────────────────────────────────────────────────────────┐
│  编排层    skills/wb-flow  wb-loop  wb-contract             │
│            主线程读状态、决策、派发 subagent、判断门禁       │
├─────────────────────────────────────────────────────────────┤
│  执行层    agents/  pm analyst architect fe-dev be-dev qa    │
│                     reviewer                                 │
│            每个 subagent 独立上下文，只做单一阶段/单一任务   │
├─────────────────────────────────────────────────────────────┤
│  内核层    hooks/wb.py                                      │
│            状态机 · 门禁引擎 · 契约管理 · 调度 · 权限守卫    │
├─────────────────────────────────────────────────────────────┤
│  状态层    .workbench/                                      │
│            state.json · contracts/ · artifacts/             │
│            role · artifacts.jsonl · frozen · unlock/        │
├─────────────────────────────────────────────────────────────┤
│  拦截层    settings.json 注册的 4 个 hook                    │
│            PreToolUse PostToolUse SessionStart SubagentStop  │
└─────────────────────────────────────────────────────────────┘
```

层间只有两种交互：**编排层与执行层通过 CLI 读写内核**，**拦截层由 Claude Code 在工具调用时同步触发内核**。没有第三种路径 —— 这是状态一致性的保证。

## 为什么内核是一个文件

`wb.py` 一个文件承担状态机、门禁、契约、调度、权限守卫五件事。这不是偷懒，是刻意的：

- 五者共享同一份 `state.json`。拆成五个脚本，每个脚本都要各自 load/save，任何一次并发写就丢数据。
- 权限守卫要读 `role_scopes`、门禁要读 `contracts`、调度要读 `tasks`，全在 state 里。拆开只会导致一份状态五处解析、五套向前兼容逻辑。
- hook 必须是单次进程调用，启动开销敏感。单文件零 import 依赖，冷启动最快。

代价：单文件较长。缓解方式是内部按职责分段（常量表 / 基础设施 / 门禁引擎 / 调度 / CLI 命令 / hook / 自检 / 参数解析），且规则全部数据化成表（`GATES`、`DEFAULT_ROLE_SCOPES`、`PHASE_ARTIFACT_CONTRACTS`、`DENY_BASH`、`WARN_BASH`），改规则动表不动逻辑。

## 状态模型

### 一份主状态 + 四份 hook 缓存

| 位置 | 内容 | 生命周期 |
| --- | --- | --- |
| `.workbench/state.json` | 阶段、任务、门禁记录、契约、配置、审计日志 | 与项目同寿，进 git |
| `.workbench/role` | 角色锁兜底（单行文本）—— subagent 优先按载荷 `agent_type` 判定，这份给主线程与非角色 agent | 单个 subagent 执行期间，`SubagentStop` 在无 doing 任务时清除 |
| `.workbench/artifacts.jsonl` | 改动流水账（一行一条 JSON：路径 + 角色 + 时间，含可用 agent 身份字段） | 只追加，`task done` 归并进任务的 `artifacts` |
| `.workbench/frozen` | 冻结路径清单（一行一条） | 由 `save_state()` 每次重写，是 `state.json` 的派生缓存 |
| `.workbench/unlock/` | 解冻申报窗口，一份契约一个文件（文件名=契约名，内容=理由） | `contract unlock` 到 `bump`/`lock`（或无 doing 任务时的 `SubagentStop`）之间 |

后四个独立成文件而不是塞进 `state.json`，原因分两半。`role` / `frozen` / `unlock` 是因为 `PreToolUse` hook 在**每一次** Write/Edit/Bash 上都要读它们 —— 读几行文本比解析整个 JSON 便宜一个量级，而 hook 的延迟直接叠加到每次工具调用上。`artifacts.jsonl` 是反过来：`PostToolUse` 只往它尾部追加一行，纯 append 没有竞态，而在 hook 里读改写 `state.json` 会在并行下静默吞掉期间落盘的 `task done`。

`frozen` 是**纯派生数据**，唯一权威在 `state.json` 的 `contracts`。所以它缺失或为空时 `read_frozen()` 从 state 现算，而不是退化成默认值 —— 派生缓存缺失必须能重建，否则升级路径上会出现静默的能力丢失（老项目没有这个文件，契约的 Bash 防线整条消失且不报错）。「为空」一并当作不可信：`FROZEN_ALWAYS` 那五条恒在，合法的清单不可能为空。

这四个文件自己也在冻结清单里（`FROZEN_ALWAYS`），任何工具调用都写不了它们，每一条对应一层机制的地基（见 [permissions.md](permissions.md#第二层冻结清单)）。`wb.py` 自己写它们不受影响：守卫只拦工具调用。

### state.json 结构

字段清单以 `wb.py` 里的 `default_state()` 为准，这里只记语义不明显的几个：

| 字段 | 语义 |
| --- | --- |
| `version` | schema 版本。字段**只增不改语义**，要改语义就升它并写迁移 |
| `seq` | 任务 ID 自增计数器，只增不减 —— 删过任务后 ID 不复用 |
| `tasks[].deps` | 前置任务 ID，全部处于满足依赖的终态（`done` 或带明确理由的 `skipped`）才算就绪；`blocked` 与 `stale` 不满足依赖。只写真依赖（[scheduling.md](scheduling.md)） |
| `gates[].forced` | `true` = 门禁未过但强推了。`passed` 记真实结果，两个字段不能合并 |
| `gates[].failures` | 强推时遗留的 FAIL 项，进交付报告 |
| `contracts[].path` | 相对项目根，同时是冻结清单的来源 |
| `contracts[].sha` | `null` = 未锁定。未锁定的不进冻结清单 |
| `contracts[].kind` | `"artifact"` = 阶段产物，`contracts_locked` 门禁不数它。接口契约与 `design-doc` 没有这个字段 |
| `role_scopes` | 角色 → 可写路径 `fnmatch` 模式，产物目录按阶段隔离 |
| `gate_commands` | 命令门禁，空值 = 跳过 |
| `log` | 审计日志，尾部保留 `MAX_LOG`（500）条 |

### 写入原子性与并发

两层：

1. **原子替换。** `save_state` 先写 `state.json.<pid>.tmp` 再 `replace()`，同目录 rename 在 POSIX 上是原子的 —— 读的人永远看到完整的一份。临时名带 pid 是必需的：共用一个名字时两个进程会把彼此的字节交织进同一个临时文件再各自 replace，实测 45 个并发进程能写出语法上就无效的 `state.json`，那时连 `status` 都跑不起来。派生缓存 `.workbench/frozen` 同样这么写，理由见下。
2. **排他锁。** 会改状态的命令用 `load_state(root, lock=True)`，在读之前对 `.workbench/state.lock` 上 `flock(LOCK_EX)`，由 `save_state`（或 `main` 收尾、`die`）解锁。只读路径（`status` / `next` / `gate` / `report` / `session-start` hook）不上锁。同一个命令的只读子动作也不上锁：`contract impact` 在锁里跑 `git grep`，大仓库要几秒，而 `wb-contract` 要求改契约前先跑它 —— 那几秒里结束的 subagent 的 `SubagentStop` 会等在锁上，超时后角色锁与解冻窗口都不清理，下一个写入被限制在上一个角色的范围里。所以 `contract` 只在 `add`/`lock`/`unlock`/`bump` 上锁，`log` 只在写日志时上锁。

**原子性不等于隔离性。** rename 只保证「不会读到半截」，不保证「不会拿旧快照覆盖」。无锁时的实测：45 个并发 `task done` 丢 20–23 个。丢掉的每一条都有连带损失 ——

- `tasks_done:<阶段>` 门禁永远 FAIL，而任务确实做完了，报出来的却是「未完成：T3, T6, …」；
- `save_state` 顺手重写的 `.workbench/frozen` 一起退回旧版，刚 `lock` 的契约在 Write/Edit 与 Bash 两条防线上同时失去保护，直到下一次 `save_state`；
- 丢掉一次 `contract bump` 时文件在 v2、状态在 v1，`contract verify` 报漂移并把原因指向「有人绕过守卫改了契约」—— 归因指向了错的方向。

这三条都是「门禁与进度不可绕过」在并发下失效，且不需要谁去绕。所以锁不是复杂度换性能，是这条硬规则在并行 develop 下成立的前提。

**锁不能跨门禁命令持有。** `cmd:test` 可能是几分钟的 `npm test`，攥着锁跑会把并行 subagent 的 `task done` 全堵在等锁上。`phase advance` 因此分两段：先无锁算门禁并打印结论，再上锁重读状态落记录 —— 期间落盘的 `task done` 不会被门禁前的旧快照盖掉。代价是门禁结论反映的是它开跑那一刻的状态，晚 0.1 秒完成的任务不算进这次结论，下一次 `gate check` 才算。

两段之间阶段可能已被另一个进程推走，所以第二段重读后要比对：还是 `cur` 才落记录，否则拒绝并让重跑。不比对的话记录会按旧 `cur` 写回 `phase` —— 对方推了两次就是**倒退一个阶段**，而这次的门禁结论算的本来就是 `cur` 那个阶段，已经作废。

`PostToolUse` hook 仍然完全不写 `state.json`：它在每次文件写入时触发，上锁会把所有并行写入串行化到状态锁上。它只往 `artifacts.jsonl` 追加，纯 append 无竞态。

`fcntl` 缺失时（非 POSIX）锁退化成无操作，行为回到上面那些实测数字。

### 派生缓存的并发

`.workbench/frozen` 是守卫的热路径输入，`save_state` 每次重写它。旧版就地重写（`write_text` = truncate 再 write），于是那一瞬文件存在但内容不全，而**守卫只判路径在不在清单里** —— 清单空了就等于全部放行。实测 4 写 6 读并行，12000 次读里 5588 次读到空清单；用真实 hook 载荷跑子进程验证那一刻的行为：Write 契约、Write `state.json`、Bash 改契约、Bash 写 `state.json`、Bash `echo architect > .workbench/role` 五条全部 `exit=0`，含提权方向。触发不需要谁去绕，一次 `task done` 与一次工具调用重叠就够。

所以两侧都改：

- **`write_frozen` 原子替换**（带 pid 的临时文件 + rename）。这是唯一同时覆盖「空」与「写了一半」的修复 —— 半截清单要跨多页才出现，实测 45 行 0 次、405 行 54 次、4005 行 82 次，小项目碰不到但大项目会。
- **`read_frozen` 把空清单视同缺失**，从 `state.json` 现算。`FROZEN_ALWAYS` 那五条恒在，合法的清单不可能为空，所以这个判据不会误判。它不认成因，任何原因写出的空文件都接得住，失效方向是误拒而非放行。

`save_state` 的写序也因此固定：**先落 `frozen`，再 `replace()` 换 `state.json`。** 反过来的话中途崩溃会留下「state 新、frozen 旧」—— 刚 `lock` 的契约不在清单里，守卫放行。现在这个顺序崩在中间是 frozen 比 state 新，多冻一份契约的误拒，下一次 `save_state` 自然纠正。

自检覆盖两侧：清空缓存后五条防线仍拒绝（管 `read_frozen`），以及 `write_frozen` 前后 inode 必须变（管原子替换 —— 中间态单进程测不到，inode 是它事后唯一可靠的痕迹）。

### 向前兼容

`load_state` 用 `default_state()` 的字段逐个 `setdefault`。老 `state.json` 遇到新增字段自动补默认值，不需要迁移脚本。代价是 `setdefault` 只补**缺失**字段、不覆盖已有的 —— 所以改过默认值（`DEFAULT_ROLE_SCOPES`）之后老项目要手工刷：

```bash
wb.py role scopes            # 先看当前值，定制过的存一份
wb.py role scopes --reset    # 刷成当前默认值，顺带重写 .workbench/frozen 缓存
                             # 跨仓库布局下按仓库前缀重算，与 init 同一条路径
```

## 状态归属：一个工作区多个仓库

`find_root()` 向上查找最近的 `.workbench/`，这一条语义支撑了两种相反的拓扑，取决于 `.workbench/` 放在哪一层 —— 没有模式开关，没有配置项。这是这个设计的主要收益。操作步骤见 [CLAUDE.md](../CLAUDE.md)，这里记它为什么成立与代价在哪。

### 每仓库一份状态（默认）

```
workbench/
├── .claude/            # 工作台本体，唯一一份，用 $CLAUDE_PROJECT_DIR 定位
├── .workbench/         # 工作台自身的状态
└── repos/foo/.workbench/   # foo 的状态，在 repos/foo 下操作时命中这一份
```

四件事让它成立，都不需要额外代码：

| 机制 | 效果 |
| --- | --- |
| `find_root()` 向上查找 | 在 `repos/foo/server/` 里跑命令，状态归属 `repos/foo` |
| hook 用 `$CLAUDE_PROJECT_DIR` 绝对路径注册 | cwd 在任意子目录都能触发，不依赖相对路径 |
| `cmd_init` 用 `Path.cwd()`（不是 `find_root()`） | 在子目录 init 会建自己的 `.workbench/`，不会误改外层 |
| 守卫第一层按 `find_root(cwd)` 算项目根 | `repos/foo` 的角色写不到 `repos/bar`，也写不到外层 `docs/` |

角色范围的 glob 相对各仓库根，所以 `server/**`、`web/**` 不用改；`gate_commands` 的执行 cwd 就是仓库根，`npm test` 直接对。

**代价：忘了 `cd` 进仓库就跑命令会操作到外层状态，且不报错**（外层已初始化，`load_state` 不会 die）。缓解是 `status` 与 `SessionStart` 都打一行根路径。没做成硬约束，因为「哪一份才是你要的」只有用户知道。

同一仓库的第二个需求：`report --write` 归档后 `init --force` 重开（串行），或 `git worktree add` 出一份新工作树再 `init`（并行，代码与状态一起隔离）。一份 `state.json` 就是一条流水线，没做多流程实例 —— 那需要在每个命令上加 `--flow` 选择器，而 worktree 已经免费解决了这件事。

### 跨仓库：同一个语义的反面

上面那个布局让仓库互相隔离，这既是它的价值也是它挡住跨仓库需求的原因：守卫按 `find_root(cwd)` 算根，`repos/foo` 的角色写不到 `repos/bar`，而且两份 `state.json` 不共享契约列表。所以一个需求要同时改两个仓库时**只在外层 init，各仓库都不 init** —— 项目根 = 整个工作区，一份契约一条流水线。

代价是两处配置必须跟着改，而且**改错是静默的**：

| 项 | 单仓库 | 跨仓库 | 不改的后果 |
| --- | --- | --- | --- |
| `role_scopes` | `server/**`、`web/**` | `repos/backend/**`、`repos/frontend/**` | 歪成按语言隔离，见下 |
| `gate_commands` | `npm test` | `(cd repos/frontend && npm test) && (cd repos/backend && pytest)` | 在外层根跑，找不到 `package.json` |

**默认范围在跨仓库下歪成「按语言隔离」**，因为 `fnmatch` 的 `*` 跨 `/`：

```
backend-developer  repos/backend/src/api.py          放行 ['*.py']       ← 靠扩展名蒙对
backend-developer  repos/backend/migrations/001.sql  拦                  ← 自己仓库的迁移写不了
frontend-developer repos/backend/**/*.tsx            放行 ['*.tsx']      ← 隔离漏了
```

`migrations/**` 匹配不上 `repos/backend/migrations/001.sql`，而 `*.py` 却匹配任意深度。结果是「后端写不了自己的迁移，却能写别人仓库的同语言文件」。跨仓库时仓库目录本身就是最准的边界，所以按前缀写。`init` 检测到 `repos/*` 会自己换成按仓库前缀（`repo_layout_scopes()`），但它只能按目录名猜（`REPO_HINTS`）。

**猜不出名字的仓库谁都写不了。** 只要有一个仓库被认领，`repos/<仓库>/**` 这条分支就把范围钉在被认领的仓库上，于是 `shared` / `payments-core` 这类名字落在所有角色范围之外 —— 是硬拦，不是跨仓库放行。这个失败只会在 develop 阶段暴露成一次权限拒绝，所以 `unclaimed_repos()` 判定它、`init` 与 `role scopes` 当场点名并给出手写认领的命令。判定按守卫自己的方式做：拿 `repos/<仓库>/src/probe.{ts,py}` 去撞两个开发角色的模式，撞不上就算没人认领。只看开发角色是因为 `qa` 的 `repos/*/tests/**` 覆盖所有仓库，而「只有 qa 能写它的测试目录」不构成认领。

只有**一个仓库都认不出**时才退回「任意仓库的对应位置」（模式逐条加 `repos/*/` 前缀），那时才是跨仓库放行。这条回退分支必须**带上裸扩展名模式** —— 丢掉它们，`qa` 就只剩四个测试目录（它没有仓库提示词，永远走这条分支），配不了 `repos/frontend/vitest.config.ts`，与单仓库下同一个误拦，只是布局 B 下更难发现。跨仓库放行是这个分支本来就有的性质（`repos/*/src/**` 一样跨），加裸扩展名没有新破的边界。

`role scopes --reset` 走的是同一条路径（`repo_layout_scopes()` 先算，为 `None` 才落回裸默认值）。只写 `DEFAULT_ROLE_SCOPES` 会把跨仓库项目**两个方向同时刷坏**：后端从此写不了自己仓库的 `migrations/`，却能写别人仓库的同语言文件 —— 而输出看起来只是「刷成默认值」。自检对这两个方向与点名判定都有断言。

契约可以放仓库里（进该仓库的 git，适合契约由该服务发布）或外层 `.workbench/contracts/`（不进任何仓库，适合契约独立于双方）。冻结保护对两种位置等效 —— 清单存的是相对项目根的路径，`frozen_hits()` 按完整相对路径匹配，两种都在清单里。差别只在先 `cd` 再改的那条兜底：切进 `.workbench` 的被它覆盖，而 `cd repos/backend && sed -i openapi.yaml` 这种切进仓库再改的它看不到，靠 `contract verify` 的哈希校验兜。

## 数据流

### 阶段推进

```
subagent 写产物到 artifacts/<phase>/
        ↓
wb.py gate check          读 GATES[phase] → 逐条断言 → 退出码
        ↓ 通过
wb.py phase advance       记录 gates[phase] → 该阶段产物登记为契约并锁定 → phase = 下一个
        ↓ 不通过
退出码 1 + 逐条 FAIL 原因 → 主线程派 subagent 补齐 → 重来
```

### 开发阶段并行

```
architect: 写 design.md
           contract add design.md + contract add 各接口
           contract lock --all                 （哈希冻结 + 守卫只读）
           task add ×N                          （只写真依赖）
        ↓
wb.py next --all --json                    → 就绪集合（依赖全部满足）
        ↓
主线程同一条消息多个 Agent 调用             → fe-dev 与 be-dev 并行
        ↓ 各自
role set → task start → 读契约 → 写代码 → 自检 → task check → 回报编排者
        ↓
PostToolUse hook 把改动追加到 artifacts.jsonl（角色取自载荷 agent_type，不看单文件）
        ↓ 编排者复核后
主线程把 subagent 报的校验命令自己跑一遍 → 写 artifacts/develop/verification.md → task done
        ↓
wb.py gate check（verification.md + contracts_intact + tasks_done:develop + cmd:lint/build）
```

前端不依赖后端实现完成，只依赖契约锁定。这是整套设计的收益来源 —— 也是为什么契约锁定被做成 design 阶段的硬门禁。`design.md` 与接口契约一起锁定：并行的前提是**双方对着同一个不动的基准**，方案文档和接口定义都是那个基准的一部分。

契约变更如何传播到下游任务见 [contracts.md](contracts.md#bump-的影响面传播)。

## 阶段产物即契约

每个阶段的产物路径与必备章节硬编码在 `GATES` 表里，过门禁后由 `freeze_phase_artifacts()` 按 `PHASE_ARTIFACT_CONTRACTS` 登记成契约并锁定 —— 对照表见 [gates.md](gates.md#六个阶段的门禁)。

**为什么这么做**：此前上游产物只在「恰好有角色锁」时受保护 —— 角色范围检查在角色取不到时整层跳过，主线程与非角色 subagent 随时能重写 `requirements.md` 且不留痕。登记成契约后走的是 `design-doc` 那条现成的路：哈希冻结、改动先 `contract unlock --reason` 申报、`bump` 给下游发同步任务，零新机制。

三处刻意的例外：

- **强推的阶段不冻结。** `--force` 意味着那个阶段没有真的做完，冻结一份未定稿的产物只会立刻要求申报解冻。
- **develop 不在表里。** `verification.md` 由编排者写，没有角色 owner，而且它是当前阶段的工作面 —— 冻结自己正在写的文件没有意义。
- **`kind: "artifact"` 与接口契约分开计数。** `contracts_locked` 只数非 artifact 的契约，否则 clarify 一过契约列表就永远非空，那条断言再也逼不出「并行开发前先把接口定下来」。

## 设计取舍

| 取舍 | 选择 | 理由 | 代价 |
| --- | --- | --- | --- |
| 状态存储 | 单个 JSON 文件 | 能 `git diff`、能 `jq`、能人工修、零依赖 | 无查询能力，任务上千条会慢 |
| 内核形态 | 单 Python 文件 | 共享状态不拆、hook 冷启动最快、无依赖 | 文件较长 |
| 规则表达 | 数据表（`GATES` 等） | 加规则加一行，不加一个类 | 表达力受限于预定义的断言类型 |
| 角色隔离 | hook 强制，角色取自载荷 `agent_type` | 提示词大部分时候遵守，hook 每次都遵守；并行 subagent 各自判定 | 非角色 agent（`general-purpose` 等）退回读单文件 |
| 契约校验 | 内容哈希 + 只读守卫 | 语言/格式无关，方案文档与阶段产物零成本复用 | 不懂语法；守卫覆盖不到外部编辑器与 `git checkout` |
| 冻结解除 | 申报窗口（理由必填），按契约分片 | 改动理由在改之前留痕；多份契约可同时解冻 | 同一份契约上不区分是谁在申报 |
| 拒绝机制 | 退出码 2 + stderr | 所有 Claude Code 版本都支持 | 只能 deny，无法 ask |
| 门禁失败 | 退出码 1，不自动修 | 修哪里是决策，不该由校验器代劳 | 需要主线程多一轮 |
| 并发控制 | `flock` 排他锁 + 带 pid 的原子 rename（`state.json` 与 `frozen` 缓存都是） | 无锁时 45 个并发 `task done` 丢 20+ 个；缓存非原子重写时守卫在那一瞬全放行（含提权）。两者都让「门禁与进度不可绕过」在并行下失效 | 门禁命令必须在锁外跑，结论反映的是它开跑那一刻的状态 |

## 已知边界与升级路径

### 解冻窗口按契约分片（曾是单文件，记录一次纠错）

`.workbench/unlock/` 是目录，一份契约一个文件。早期版本这里是单个文件，两个 subagent 同时 `contract unlock` 后者覆盖前者，当时判断「失效方向是误拒而非漏放，可以接受」。

**阶段产物冻结之后这个判断不再成立**：`bump` 一份产物契约会给每个消费方各建一条同步任务（`artifact-requirements` 的消费方是 `analyst` 与 `architect`），CLAUDE.md 硬规则 5 要求并行派发，两者各要解冻自己那份冻结产物。于是「同时申报」从边界情况变成 `bump` 之后的必然路径 —— 前一个 agent 刚申报完就被拒，而拒绝理由还是「先申报」，它没有任何出路。**可接受的误拒和会卡死流程的误拒不是同一件事。**

**分片键是契约，不是 agent。** 窗口标识的是「哪份契约正在改」而不是「谁在改」。同一份契约被两个 agent 同时申报本身就该避免，按 agent 分片只会把它变成合法操作 —— 那是把唯一真该拦的情况放开了。所以同一份契约上仍不区分申报者，这是设计而非残留。

契约名因此是**信任边界上的输入**（它是文件名）—— `contract add` 校验它只含字母数字与 `.`、`_`、`-`，否则 `--name ../../x` 能让 `unlock` 写到项目根外。

### 角色锁曾经也是单文件（已解决，记录一次纠错）

早期版本的角色锁也是 `.workbench/role` 一个文件，并行 subagent 各自 `role set` 会互相覆盖。后果不是精度下降而是**非确定性误拒**：backend-developer 写自己的 `migrations/` 会撞上前端的范围被拒，成败取决于两个 subagent 谁后启动。当时给的根因「hook 载荷里没有 subagent 标识」是错的。

实测（Claude Code 2.1.252）：subagent 的 `PreToolUse` / `PostToolUse` / `SubagentStop` 载荷都带 `agent_type` 与 `agent_id`，主线程两个都没有；而 `session_id` 是**共享的** —— 所以早期设想的「按 `session_id` 分片」本来也不通。`agent_type` 的值就是 agent 定义 frontmatter 的 `name`，与 `ROLES` 同名，零映射。

`current_role()` 因此优先取载荷的 `agent_type`，取不到或不是角色名才退回读文件。并行 subagent 各自判定，与启动顺序无关；产物归属（`artifacts.jsonl` 的 `role`）同样按载荷取，不再全挂到最后一次 `role set` 的角色名下。

**残留边界**：非角色 agent（`Explore` / `general-purpose` / `Plan`）的 `agent_type` 不在 `ROLES` 里，仍退回读文件 —— 开发活要派给角色 agent，用 `general-purpose` 干开发时角色范围只能按最后一次 `role set` 兜底。

### 冻结防线覆盖不到的写入路径

Bash 分支的冻结检查靠 `BASH_WRITE` 正则识别写入意图。`cp` / `mv` / `install` 已纳入当前本地 `wb.py` hook 的 `resolve()` 解析；这些规则最初曾在已移除的 `wbsvr` 历史设计阶段 0 中被提出，但当前能力不依赖、也不调用该服务 —— 促成它的是状态文件：`cp` 覆盖 `state.json` 此前直接通过，而状态文件没有任何哈希兜底，契约有 `contract verify`、状态没有。仍未纳入的：`rsync`、编译型工具的输出、外部编辑器、`git checkout`、用户自己动手改。

**这一节的旧版预测「加进去会拦掉大量正常的构建与资源拷贝」，那个预测错了。** 判定是两段式的：命中 `BASH_WRITE` 只是第一段，还要 `frozen_hits()` 在命令文本里找到冻结路径才拒。`cp dist/x.js public/` 两段都不沾，构建与资源拷贝根本不进第二段。

**`cp`/`mv` 的源和目标区分已由 `resolve()` 精确处理。** `_LAST_ARG` 类命令只取最后一个非 flag 参数作为写入目标，`cp .workbench/contracts/api.yaml /tmp/bak` 的目标是 `/tmp/bak`（safe 目录，跳过），契约路径只出现在源位置，不会被误拦。`uncertain` 模式下退回旧行为（不区分源和目标）。实现细节见 [roma-comparison.md](roma-comparison.md) 第一节。

**用户手改不在覆盖范围内**，那是有意为之 —— 用户是这套机制的所有者，不是被约束的对象。

**兜底不是升级路径，是设计的另一半**：`contract verify` 的哈希校验不管改动从哪来，develop 与 verify 两个阶段的门禁都跑它。守卫在改之前拦（能给出可操作的拒绝理由），校验在门禁时抓（能兜住守卫覆盖不到的一切）。两者都留着，不是重复。

**`git checkout`/`git restore` 故意不在 `_GIT_WRITE` 集合里。** 这两个命令恢复的是 git 跟踪的文件内容，不涉及 `.workbench/` 下的状态或契约（那些不在 git 里）。拦截它们只会干扰正常开发流程。`git mv`/`git rm`/`git clean`/`git stash` 则在集合内，因为它们会改变工作区文件的物理位置或删除内容。

### 路径匹配偏宽松

角色范围用 `fnmatch.fnmatch(rel, pattern)`。Python 的 `fnmatch` 把 `*` 翻译成 `.*`，会跨 `/`。所以 `*.css` 也匹配 `web/theme/a.css`，`src/**` 匹配任意深度。

**这是有意的宽松**：守卫的目标是挡住「pm 改代码」「前端改迁移」这类角色越界，不是做精确的路径 ACL。误杀比漏杀更影响可用性 —— 它会让 agent 开始想办法绕过守卫。跨仓库布局下这个宽松会变成实际问题，见上文。

**一处例外：`.workbench/` 下的路径只认显式以 `.workbench/` 开头的模式。** 跨 `/` 在仓库里是宽松，跨进状态目录就是漏洞 —— `*.md` 会匹配 `artifacts/clarify/requirements.md`，`*.json` 会匹配 `contracts/events.json`，于是「产物按阶段隔离」与「契约只有 architect 能写」两条被裸扩展名整个绕开。这不能靠冻结那层兜：它只认已锁定的契约，强推过的阶段产物不冻结、未 `lock` 的契约不在清单里。所以这一层自己收窄（[permissions.md](permissions.md#第四层角色写入范围)）。

**要严格匹配**：换成 `pathlib.PurePath.full_match()`（Python 3.13+）或引入 `wcmatch.globmatch`。改动在 `hook_pre_tool` 一处。

### 契约不校验语法

哈希冻结只保证「没人偷偷改」，不保证「内容是合法的 OpenAPI」。**缓解**：挂到 `gate_commands.lint` 上。

### 强推无硬确认

`phase advance --force` 直接生效，只写日志和交付报告。「先问用户」是 `wb-flow` skill 里的约定，不是代码约束。

**要硬约束**：在 `cmd_phase` 的 force 分支加环境变量门（如要求 `WB_ALLOW_FORCE=1`），让强推必须由人在 shell 里显式开。约 5 行。

### 日志尾部截断

`log` 只保留最后 500 条（`MAX_LOG`）。长项目早期的记录会丢，复盘时看不到全程。

**要完整审计**：改成追加写 `.workbench/audit.jsonl`，`state.json` 里只留最近 500 条做快速查看。约 10 行。

## 自检

`wb.py selfcheck` 在临时目录跑一遍全链路，六十余条断言覆盖状态机、门禁、契约漂移、命令门禁、权限守卫、并发写状态、产物挂载、报告渲染。**具体断言什么以代码为准**，这里不复述 —— 抄一份就是造一份会漂移的副本。跑法与验证真实 hook 路径的方式见 [permissions.md](permissions.md#自检覆盖)。

**改过 `wb.py` 必须跑。** 它是唯一防止「规则表改坏但没人发现」的东西 —— 门禁与守卫失效都是静默的，不会报错，只会让流程失去约束力。

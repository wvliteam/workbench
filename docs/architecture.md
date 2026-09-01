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
│            role · current_task · frozen · unlock            │
├─────────────────────────────────────────────────────────────┤
│  拦截层    settings.json 注册的 4 个 hook                    │
│            PreToolUse PostToolUse SessionStart SubagentStop  │
└─────────────────────────────────────────────────────────────┘
```

层间只有两种交互：**编排层与执行层通过 CLI 读写内核**，**拦截层由 Claude Code 在工具调用时同步触发内核**。没有第三种路径 —— 这是状态一致性的保证。

## 为什么内核是一个文件

`wb.py` 一个文件承担状态机、门禁、契约、调度、权限守卫五件事，1200 余行。这不是偷懒，是刻意的：

- 五者共享同一份 `state.json`。拆成五个脚本，每个脚本都要各自 load/save，任何一次并发写就丢数据。
- 权限守卫需要读 `role_scopes`（在 state 里），门禁需要读 `contracts`（在 state 里），调度需要读 `tasks`（在 state 里）。拆开只会导致一份状态五处解析、五套向前兼容逻辑。
- hook 必须是单次进程调用，启动开销敏感。单文件零 import 依赖，冷启动最快。

代价：单文件较长。缓解方式是内部按职责分段（常量表 / 基础设施 / 门禁引擎 / 调度 / CLI 命令 / hook / 自检 / 参数解析），且规则全部数据化成表（`GATES`、`DEFAULT_ROLE_SCOPES`、`DENY_BASH`、`WARN_BASH`），改规则动表不动逻辑。

## 状态模型

### 一份主状态 + 四份 hook 缓存

| 位置 | 内容 | 生命周期 |
| --- | --- | --- |
| `.workbench/state.json` | 阶段、任务、门禁记录、契约、配置、审计日志 | 与项目同寿，进 git |
| `.workbench/role` | 当前角色锁（单行文本） | 单个 subagent 执行期间，`SubagentStop` 清除 |
| `.workbench/current_task` | 当前进行中任务 ID（单行文本） | `task start` 到 `task done` 之间 |
| `.workbench/frozen` | 冻结路径清单（一行一条） | 由 `save_state()` 每次重写，是 `state.json` 的派生缓存 |
| `.workbench/unlock` | 解冻申报窗口（`<契约名>\n<理由>`） | `contract unlock` 到 `bump`/`lock`/`SubagentStop` 之间 |

后四个独立成文件而不是塞进 `state.json`，原因是 `PreToolUse` hook 在**每一次** Write/Edit/Bash 上都要读它们。读几行文本比解析整个 JSON 便宜一个量级，而 hook 的延迟直接叠加到每次工具调用上。

`frozen` 是**纯派生数据**，唯一权威在 `state.json` 的 `contracts`。所以它缺失时 `read_frozen()` 从 state 现算，而不是退化成默认值 —— 派生缓存缺失必须能重建，否则升级路径上会出现静默的能力丢失（老项目没有这个文件，契约的 Bash 防线整条消失且不报错）。

这四个文件自己也在冻结清单里（`FROZEN_ALWAYS`），任何工具调用都写不了它们。写 `role` 能提权，写 `frozen` 能把自己想改的文件摘出清单，写 `unlock` 能伪造一个申报 —— 三条都会让整套机制作废。

### 状态归属：一个工作区多个仓库

`find_root()` 向上查找最近的 `.workbench/`，这一条语义直接支撑了「代码库 clone 进工作区、工作台本体不拷贝」的用法：

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

角色范围的 glob 是**相对各仓库根**的，所以 `server/**`、`web/**` 不用改成 `**/server/**`。

`gate_commands` 的执行 cwd 是 `find_root()` 的结果，也就是仓库根 —— `npm test` 直接对，不需要 `cd`。

**这个布局的代价：忘了 `cd` 进仓库就跑命令会操作到外层状态，且不报错**（外层已初始化，`load_state` 不会 die）。缓解是 `status` 与 `SessionStart` 都打一行根路径。没做成硬约束，因为「哪一份才是你要的」只有用户知道。

同一仓库的第二个需求：`report --write` 归档后 `init --force` 重开（串行），或 `git worktree add` 出一份新工作树再 `init`（并行，代码与状态一起隔离）。一份 `state.json` 就是一条流水线，没做多流程实例 —— 那需要在每个命令上加 `--flow` 选择器，而 worktree 已经免费解决了这件事。

### 跨仓库开发：同一个语义的反面

上面那个布局让仓库互相隔离，这既是它的价值也是它挡住跨仓库需求的原因：守卫第一层按 `find_root(cwd)` 算根，`repos/foo` 的角色写不到 `repos/bar`，而且两份 `state.json` 不共享契约列表。

一个需求要同时改前端和后端两个仓库时，**只在外层 init，各仓库都不 init**。`find_root()` 一路向上命中外层，项目根 = 整个工作区，两个仓库都在根之下，一份契约一条流水线。

同一份 `find_root()` 语义支撑两种相反的拓扑，取决于 `.workbench/` 放在哪一层。这是「向上查找最近的标记目录」这个设计的主要收益 —— 没有模式开关，没有配置项。

代价是两处配置必须跟着改，而且**改错是静默的**：

| 项 | 单仓库模式 | 跨仓库模式 | 不改的后果 |
| --- | --- | --- | --- |
| `role_scopes` | `server/**`、`web/**`（相对仓库根） | `repos/backend/**`、`repos/frontend/**`（按仓库前缀） | 歪成按语言隔离，见下 |
| `gate_commands` | `npm test` | `(cd repos/frontend && npm test) && (cd repos/backend && pytest)` | 在外层根跑，找不到 `package.json` |

**默认范围在跨仓库下歪成「按语言隔离」**，因为 `fnmatch` 的 `*` 跨 `/`：

```
backend-developer  repos/backend/src/api.py          放行 ['*.py']       ← 靠扩展名蒙对
backend-developer  repos/backend/migrations/001.sql  拦                  ← 自己仓库的迁移写不了
frontend-developer repos/backend/**/*.tsx            放行 ['*.tsx']      ← 隔离漏了
```

`migrations/**` 匹配不上 `repos/backend/migrations/001.sql`，而 `*.py` 却匹配任意深度。结果是「后端写不了自己的迁移，却能写别人仓库的同语言文件」。跨仓库时仓库目录本身就是最准的边界，所以按前缀写。

契约可以放仓库里（`repos/backend/openapi.yaml`，进该仓库的 git，适合契约由该服务发布）或外层 `.workbench/contracts/`（不进任何仓库，适合契约独立于双方）。冻结保护对两种位置等效 —— 清单存的是相对项目根的路径，`frozen_hit` 的 basename 半边也照样覆盖先 `cd` 进仓库再 `sed -i` 的写法。

### state.json schema

```jsonc
{
  "version": 1,                    // schema 版本，用于将来迁移
  "project": "my-project",
  "created": "2026-08-31T19:06:08+0000",

  "phase": "clarify",              // 当前阶段
  "phases": ["clarify", "analyze", "design", "develop", "verify", "retro"],

  "max_parallel": 3,               // next --all 一批返回的上限
  "seq": 0,                        // 任务 ID 自增计数器，只增不减

  "tasks": [
    {
      "id": "T1",
      "title": "用户列表接口",
      "role": "backend-developer", // 必须是 ROLES 之一
      "phase": "develop",
      "status": "todo",            // todo | doing | done | blocked
      "deps": ["T0"],              // 前置任务 ID，全部 done 才算就绪
      "contracts": ["user-api"],   // 依赖的契约名，bump 时算影响面用
      "artifacts": ["api/users.py"], // 由 PostToolUse hook 自动累加
      "notes": "",                 // block 的原因 / done 的备注
      "created": "...", "updated": "..."
    }
  ],

  "gates": {
    "clarify": {
      "passed": true,
      "at": "...",
      "forced": false,             // true = 门禁未过但强推了
      "failures": []               // 强推时遗留的 FAIL 项，进交付报告
    }
  },

  "contracts": [
    {
      "name": "user-api",
      "path": ".workbench/contracts/user-api.json", // 相对项目根，同时是冻结清单的来源
      "owner": "backend-developer",
      "consumers": ["frontend-developer"],
      "version": 2,
      "sha": "364aedbbccd8...",    // null = 未锁定，未锁定的不进冻结清单
      "locked_at": "...",
      "created": "..."
    },
    {
      "name": "design-doc",        // 技术方案文档走同一套机制，零特殊代码
      "path": ".workbench/artifacts/design/design.md",
      "owner": "architect",
      "consumers": ["frontend-developer", "backend-developer", "qa"],
      "version": 1, "sha": "ad0360f5b084...", "locked_at": "...", "created": "..."
    }
  ],

  "role_scopes": {                 // 角色 -> 可写路径 fnmatch 模式，产物目录按阶段隔离
    "pm": [".workbench/artifacts/clarify/**"]
  },

  "gate_commands": {               // 命令门禁，空值 = 跳过
    "test": "npm test", "lint": "...", "build": "..."
  },

  "log": [                         // 审计日志，尾部保留 500 条
    { "at": "...", "event": "contract_unlock", "name": "user-api",
      "reason": "分页要返回 total" },
    { "at": "...", "event": "contract_bump", "name": "user-api",
      "from": 1, "to": 2, "reason": "分页要返回 total" }
  ]
}
```

### 写入原子性

`save_state` 先写 `state.json.tmp` 再 `replace()`，同目录 rename 在 POSIX 上是原子的。防的是 hook 与 CLI 同时写导致的半截 JSON —— 那会让整个工作台不可读。

**没有做锁。** 真正的并发写窗口只有「并行 subagent 同时 `task done`」，概率低且损失是一条状态更新。加文件锁的复杂度不值得。这是一个刻意接受的竞态，记录在此。

### 向前兼容

`load_state` 用 `default_state()` 的字段逐个 `setdefault`。老 `state.json` 遇到新增字段自动补默认值，不需要迁移脚本。字段**只增不改语义** —— 要改语义就升 `version` 并写迁移。

## 数据流

### 阶段推进

```
subagent 写产物到 artifacts/<phase>/
        ↓
wb.py gate check          读 GATES[phase] → 逐条断言 → 退出码
        ↓ 通过
wb.py phase advance       记录 gates[phase] → phase = 下一个
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
wb.py next --all --json                    → 就绪集合（依赖全 done）
        ↓
主线程同一条消息多个 Agent 调用             → fe-dev 与 be-dev 并行
        ↓ 各自
role set → task start → 读契约 → 写代码 → 自检 → task done
        ↓
PostToolUse hook 把改动文件挂到 current_task
        ↓
wb.py gate check（contracts_intact + tasks_done:develop + cmd:lint/build）
```

前端不依赖后端实现完成，只依赖契约锁定。这是整套设计的收益来源 —— 也是为什么契约锁定被做成 design 阶段的硬门禁。

`design.md` 与接口契约一起锁定：并行的前提是**双方对着同一个不动的基准**，方案文档和接口定义都是那个基准的一部分。

### 契约变更传播

```
开发角色发现契约不够用
        ↓ 直接改文件会被守卫拦（Write/Edit 与 shell 写入都拦）
task block <ID> --reason "契约 X 缺 Y"
        ↓ 报回主线程
派 architect: contract impact          （先看影响面）
              contract unlock --reason  （申报，理由入日志，窗口只开这一份）
              改文件
              contract bump             （继承理由）
        ↓ bump 自动做五件事
version+1 · 重新锁哈希 · 给每个 consumer 建同步任务 · 写审计日志 · 关闭窗口
        ↓
task reopen <被阻塞的ID>
```

`design-doc` 走完全一样的路径 —— 开发中发现方案有问题不能自己改文档，走申报，bump 后三个消费方各拿到一条同步任务。

## 六个阶段的产物契约

每个阶段的产物路径与必备章节是硬编码在 `GATES` 表里的，因为它们是阶段间的接口 —— 下游 subagent 按固定路径读上游产物。

| 阶段 | 产物 | 门禁要求的章节 |
| --- | --- | --- |
| clarify | `artifacts/clarify/requirements.md` | `验收标准`、`非目标` |
| analyze | `artifacts/analyze/current-state.md` | `风险` |
| design | `artifacts/design/design.md` | `方案对比` |
| develop | 代码（无固定产物） | — |
| verify | `artifacts/verify/test-report.md` | — |
| retro | `artifacts/retro/retro.md` | `改进项` |

章节检查是字符串包含，不解析 Markdown。粗糙但有效：它挡住的是「产物写了但漏了关键思考」这类最常见的敷衍，而不是格式错误。

## 设计取舍

| 取舍 | 选择 | 理由 | 代价 |
| --- | --- | --- | --- |
| 状态存储 | 单个 JSON 文件 | 能 `git diff`、能 `jq`、能人工修、零依赖 | 无查询能力，任务上千条会慢 |
| 内核形态 | 单 Python 文件 | 共享状态不拆、hook 冷启动最快、无依赖 | 文件较长 |
| 规则表达 | 数据表（`GATES` 等） | 加规则加一行，不加一个类 | 表达力受限于预定义的断言类型 |
| 角色隔离 | hook 强制 | 提示词大部分时候遵守，hook 每次都遵守 | 拿不到 subagent 身份，见下 |
| 契约校验 | 内容哈希 + 只读守卫 | 语言/格式无关，方案文档零成本复用 | 不懂语法；守卫覆盖不到 `cp`/外部编辑器 |
| 冻结解除 | 申报窗口（理由必填） | 改动理由在改之前留痕 | 单文件，并行下不隔离 |
| 拒绝机制 | 退出码 2 + stderr | 所有 Claude Code 版本都支持 | 只能 deny，无法 ask |
| 门禁失败 | 退出码 1，不自动修 | 修哪里是决策，不该由校验器代劳 | 需要主线程多一轮 |
| 并发控制 | 无锁，原子 rename | 竞态窗口小、损失小 | 极端情况丢一条状态更新 |

## 已知边界与升级路径

### 角色锁与解冻窗口在并行下不隔离

`.workbench/role` 与 `.workbench/unlock` 都是单个文件。两个 subagent 并行时，后一个 `role set` / `contract unlock` 覆盖前一个，守卫只按最后一次生效。

**根因**：`PreToolUse` hook 的载荷里没有「当前是哪个 subagent」。hook 拿到的是 `session_id`、`transcript_path`、`cwd`、`tool_name`、`tool_input` —— 主线程和 subagent 在这些字段上无法区分。这是上游限制，不是实现疏漏。

**当前缓解**：每个 agent 定义的第一条指令是 `role set <自己>`，且写入范围也写在提示词里。守卫是兜底而非唯一防线。解冻窗口的缓解更强一点 —— 窗口只对一份契约生效，两个 subagent 互相覆盖的结果是**后者生效、前者被拒**，不是两者都放开；而且 `SubagentStop` 会清窗口。

**升级路径**：上游在 hook 载荷暴露 subagent 标识后，把 `role` / `unlock` 改成 `role.<agent_id>` 多文件，守卫按 ID 读对应的那份。改动约 20 行，集中在 `hook_pre_tool`、`cmd_role` 与 `read_unlock`。

### 冻结防线覆盖不到的写入路径

Bash 分支的冻结检查靠 `BASH_WRITE` 正则识别写入意图。未纳入的：`cp`、`mv`、`install`、编译型工具的输出、外部编辑器、`git checkout`。

**这是刻意的取舍**：把 `cp`/`mv` 加进去会拦掉大量正常的构建与资源拷贝，误报成本高于收益。

**兜底不是升级路径，是设计的另一半**：`contract verify` 的哈希校验不管改动从哪来，develop 与 verify 门禁都跑它。守卫在改之前拦（能给出可操作的拒绝理由），校验在门禁时抓（能兜住守卫覆盖不到的一切）。两者都留着，不是重复。

要更严就把 `cp|mv|install|rsync` 加进 `BASH_WRITE`，改动一行 —— 加之前先在自己项目上试跑，看误报频率能不能接受。

### 路径匹配偏宽松

角色范围用 `fnmatch.fnmatch(rel, pattern)`。Python 的 `fnmatch` 把 `*` 翻译成 `.*`，会跨 `/`。所以 `*.css` 也匹配 `web/theme/a.css`，`src/**` 匹配任意深度。

**这是有意的宽松**：守卫的目标是挡住「pm 改代码」「前端改迁移」这类角色越界，不是做精确的路径 ACL。误杀比漏杀更影响可用性。

**要严格匹配**：换成 `pathlib.PurePath.full_match()`（Python 3.13+）或引入 `wcmatch.globmatch`。改动在 `hook_pre_tool` 一处。

### 契约不校验语法

哈希冻结只保证「没人偷偷改」，不保证「内容是合法的 OpenAPI」。

**缓解**：挂到命令门禁上。
```
wb.py config set gate_commands.lint 'npx @redocly/cli lint .workbench/contracts/*.yaml'
```

### 强推无硬确认

`phase advance --force` 直接生效，只写日志和交付报告。「先问用户」是 `wb-flow` skill 里的约定，不是代码约束。

**要硬约束**：在 `cmd_phase` 的 force 分支加环境变量门（如要求 `WB_ALLOW_FORCE=1`），让强推必须由人在 shell 里显式开。约 5 行。

### 日志尾部截断

`log` 只保留最后 500 条（`MAX_LOG`）。长项目早期的记录会丢，复盘时看不到全程。

**要完整审计**：改成追加写 `.workbench/audit.jsonl`，`state.json` 里只留最近 500 条做快速查看。约 10 行。

### 升级已有项目

`wb.py` 加了新的默认值（`DEFAULT_ROLE_SCOPES` 按阶段隔离、`FROZEN_ALWAYS`）之后，老项目的 `state.json` 里存的还是旧值。`load_state` 的 `setdefault` 只补**缺失**字段，不覆盖已有的。

```bash
wb.py role scopes            # 先看当前值，定制过的存一份
wb.py role scopes --reset    # 刷成当前默认值，顺带重写 .workbench/frozen 缓存
```

`.workbench/frozen` 不存在也不影响正确性 —— `read_frozen()` 会从 `state.json` 现算。这个兜底是刻意加的：**派生缓存缺失必须能重建**，否则升级会造成静默的能力丢失（老项目的契约突然不再受 Bash 路径保护，且不报错）。

## 自检

`wb.py selfcheck` 在临时目录跑一遍全链路，断言四十余条：状态机推进、依赖拒绝、门禁挡与放、契约漂移检出、bump 生成返工任务、bump 拒绝空改动、命令门禁退出码、权限守卫（越界写 / 冻结清单 / `rm -rf /` / force push / `curl|sh` / 正常 `rm` 不误杀 / 角色越权 / 产物按阶段隔离 / 六种 Bash 绕过 / 四种正常命令不误杀 / 申报窗口开关与不外溢 / 方案文档同等保护 / 冻结缓存缺失时不退化）、产物自动挂载、报告渲染。

**改过 `wb.py` 必须跑。** 它是唯一防止「规则表改坏但没人发现」的东西 —— 门禁失效是静默的，不会报错，只会让流程失去约束力。

自检里的 `guard()` 直接调函数，不走子进程。改过 hook 的事件分发或 argparse 之后要另外用真实载荷跑一遍：

```bash
echo '{"tool_name":"Bash","cwd":"'"$PWD"'","tool_input":{"command":"echo {} > .workbench/state.json"}}' \
  | python3 .claude/hooks/wb.py hook pre-tool; echo "exit=$?"
```

事件名是 `pre-tool` / `post-tool` / `session-start` / `subagent-stop`。写错时 argparse 也退出 2，看起来像「拦住了」—— 验证绕过路径时要确认拒绝信息是守卫发的。

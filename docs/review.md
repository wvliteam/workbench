# 设计评审：工作台自身的实现审查

**评审日期**：2026-09-01
**评审对象**：`.claude/hooks/wb.py`（1488 行）、`.claude/settings.json`、7 个 agent 定义、3 个 skill
**方法**：逐行阅读实现，配合三组临时探针脚本对权限守卫、状态并发、门禁行为做实测验证。本文所有结论均以代码行为为准，不以 `docs/` 现有描述为准 —— 评审中发现文档与实现已有分歧，见问题 3 与问题 12。

---

## 一、结论摘要

契约机制（哈希冻结 + 申报窗口 + 变更传播）是这套工作台里最扎实的设计，没有发现漏洞。问题集中在**权限守卫层**与**状态并发**：守卫在它最该起作用的 develop 并行阶段既拦不住越权写、又会非确定性地误拒正当写入；`post-tool` 钩子的无锁读改写会在并行场景下静默丢失任务状态。

按严重度分为三档，共 15 项。

| 档位 | 数量 | 性质 |
| --- | --- | --- |
| 严重 | 3 | 机制在关键路径上反向工作，或破坏「进度不可绕过」这一核心前提 |
| 中等 | 8 | 误报、门禁盲区、可诊断性缺失、审计链缺口 |
| 设计层面 | 4 | 文档与实现的漂移风险、恒真断言、副作用、死代码 |

---

## 二、站得住的部分

先记录不需要改的地方，避免后续改动误伤。

**契约链完整。** 登记（`wb.py:641`）→ 锁定哈希（`wb.py:682`）→ 漂移检出（`wb.py:311`）→ `unlock --reason` 强制申报（`wb.py:702`）→ `bump` 为消费方自动造返工任务（`wb.py:741`）。两个细节体现了设计者想清楚了：

- `unlock` 拒绝无理由调用，理由在改动之前落盘，不是改完补 —— 事后补的理由都是给已发生的事找解释。
- `bump` 在内容哈希未变时拒绝执行（`wb.py:733`），防止刷版本号。

**`selfcheck` 是真的自检。** 在临时目录跑一遍完整流水线，60 余条断言覆盖状态机、门禁、契约漂移、命令门禁、权限守卫、产物挂载、报告渲染。比多数同类脚手架的"自检"扎实得多。

**规则表驱动。** `GATES`（`wb.py:60`）与 `DEFAULT_ROLE_SCOPES`（`wb.py:103`）是纯数据表，改规则只动一张表，不动逻辑。

**状态写入原子。** `save_state` 走 tmp 文件 + `replace`（`wb.py:214`），单进程下不会写出半个 JSON。

---

## 三、严重问题

### 问题 1：并行 develop 与角色锁在机制上互斥

`.workbench/role` 是**单个文件**（`wb.py:817`）。而 CLAUDE.md 硬规则 5 要求 develop 阶段并行派发前后端 subagent，每个 agent 定义的第一条指令又是 `role set <自己>`。

两者同时成立的后果：

1. 前后端 subagent 并行启动，各自 `role set`，**后者覆盖前者**。
2. 此后 backend-developer 写 `migrations/001.sql`，而全局 role 已是 `frontend-developer` → 守卫拒绝退出码 2。这不是假设，`selfcheck` 自己在第 1258 行就断言了这个拒绝行为。
3. 先结束的 subagent 触发 `SubagentStop`，`hook_subagent_stop` 无条件 `rolef.unlink()`（`wb.py:1093`）→ 把另一个仍在运行的 subagent 的约束一起清掉，它随后进入完全无限制状态。

于是在唯一真正需要角色隔离的阶段，守卫**既拦不住越权、又会随机误杀正当写入**，具体表现取决于两个 subagent 的启动与结束顺序。

`docs/architecture.md:285` 与 `README.md:197` 把这描述为「守卫只按最后一次 `role set` 生效，是兜底而非唯一防线」。实测行为比这个描述更糟：不是"精度下降"，而是"非确定性误拒 + 约束被提前清除"。

**建议**：改成 identity-free 的设计 —— **阶段门禁一旦通过，该阶段的产物目录即进入冻结清单**。这样覆盖了角色范围真正要防的风险（下游角色悄悄回改上游的需求与方案文档），且确定性、不依赖 hook 拿到 subagent 身份。`role_scopes` 相应降级为提示词层面的约定，不再作为强制机制。

另一条路是按 `session_id` 分片 role 文件（`.workbench/role.<session_id>`）。但需要先验证 PreToolUse 载荷里 subagent 的 `session_id` 是否真的与主线程不同 —— 本次评审未验证该字段，不能当作前提。

### 问题 2：`post-tool` 无锁读改写 state.json，会静默丢失任务状态

`hook_post_tool`（`wb.py:1026`）对每次 Write/Edit 都做一次完整的 state.json 读 → 改 → 写，没有任何锁。

实测交错（真实可复现的顺序）：

```
subagent A 的 post-tool 读取状态快照
     ↓
期间 subagent B 执行 wb.py task done T2 并落盘
     ↓
subagent A 的 post-tool 用旧快照 save_state 覆盖

结果：T2 状态回退为 todo（期望 done）
      日志里的 task_done 条目一并消失
```

而 develop 并行阶段正是「高频 Write」叠加「高频 `task done`」的场景 —— 这是问题最容易发生的地方。

后果超出"丢一条记录"：门禁 `tasks_done:develop` 读的是被覆盖后的状态，所以**「门禁与进度不可绕过」这一核心前提在并发下失效**。不需要谁去绕，状态自己会丢。

**建议**：让 `post-tool` 不再写 state.json，改为 append 一行到 `.workbench/artifacts.jsonl`，由 `task done` 时归并进任务的 `artifacts` 字段。纯 append 天然无竞态，顺带把全量 JSON 读写从每次工具调用的热路径上挪走。比给 `save_state` 加 `flock` 更彻底 —— 加锁只能解决冲突，去掉写入直接消除冲突。

### 问题 3：Bash 完全不受角色范围与项目根约束

`hook_pre_tool` 的 Bash 分支跑完 `DENY_BASH` 与冻结路径匹配就 `return`（`wb.py:975`），后续的「写出项目根」检查与「角色范围」检查都在 `Write|Edit|NotebookEdit|MultiEdit` 分支之后，Bash 永远走不到。

实测（`role set pm` 状态下）：

| 操作 | 实测结果 |
| --- | --- |
| `Write` → `src/app.ts` | 拒绝（退出码 2） |
| `echo code > src/app.ts` | **放行** |
| `cp /etc/hosts src/app.ts` | **放行** |
| `echo x > /tmp/outside_root.txt` | **放行** |
| `Write` → `/tmp/outside_root.txt` | 拒绝（对照） |

CLAUDE.md「权限守卫」一节明确写着「不要用 Bash 代替 Write —— 那条也拦」。代码不拦。这句话会让人（和模型）对守卫的覆盖面产生错误预期，属于比缺失防护更危险的一类问题。

**建议**：只补「写出项目根」这一条 —— 从重定向目标里抽绝对路径相对可靠。角色范围维持 Write/Edit-only，同时**修正 CLAUDE.md 那句陈述**。从任意 shell 命令里可靠地抽出所有写入目标是做不到的，与其做一个漏一半的检查让人误以为有防护，不如把边界写清楚。

---

## 四、中等问题

### 问题 4：冻结匹配用 basename 子串，误杀正常命令

`frozen_hit`（`wb.py:929`）除了匹配相对路径，还做 `os.path.basename(rel) in cmd`。而 `FROZEN_ALWAYS` 的四个 basename 是 `state.json`、`role`、`unlock`、`frozen`。

于是任何命中 `BASH_WRITE` 且**文本里出现这四个词**的命令都被拒绝。实测被误杀：

```bash
echo 'ALTER TABLE users ADD COLUMN role text' >> migrations/002.sql
echo 'const roles = []' >> web/roles.ts
echo '{}' > web/state.json
```

`role` 是业务代码里最常见的字段名之一，这条会在真实项目里高频触发。更糟的是错误信息说的是「契约改动走 `wb.py contract unlock` 申报」—— 与实际原因毫无关系，读到的人会去申报一个不存在的契约。

评审过程中本文作者的第一条探针命令就被这条规则拦住了（命令里含 `wb.py role set` 加 `>/dev/null`）。

**建议**：删掉 basename 匹配，只匹配相对路径（含规范化后的绝对路径）。被保护的四个状态文件本来都在 `.workbench/` 下，路径匹配已经足够；`cd` 到目录再改的场景可以用「命令里出现 `.workbench` 且命中写动作」来覆盖。约 3 行改动。

### 问题 5：verify 门禁看不见 `contract bump` 造出的返工任务

`contract bump` 创建返工任务时把 `phase` 硬编码为 `"develop"`（`wb.py:748`）。

若在 verify 阶段发现契约要改并执行 bump，返工任务落到 develop 阶段，而 verify 门禁只检查 `tasks_done:verify`（`wb.py:91`）。实测：返工任务 T2 处于 todo 状态时，`gate check --phase verify` 输出「结论：通过」。

`retro` 门禁的 `tasks_done:*` 最终会兜住，但 verify 阶段已经带着未完成的返工放过去了 —— 这正是契约变更影响面传播机制想防的事。

**建议**：返工任务的 `phase` 取当前阶段而非硬编码。1 行改动。

### 问题 6：门禁失败只保留最后一行，不留档

`run_check` 的 `cmd` 分支取 `tail[-1][:200]` 作为 detail（`wb.py:371`），完整输出直接丢弃，没有落盘。

测试框架的最后一行通常是汇总行。实测：

```
[FAIL] 命令门禁 test — `bash faketest.sh` exit=1 2 failed, 8 passed in 3.2s
```

哪两个用例失败、失败原因是什么，全部丢失。要诊断只能手动重跑一遍完整命令 —— 而门禁刚刚已经跑过一次了。

**建议**：完整输出写入 `.workbench/gate-<name>.log`，detail 里给出日志路径加最后 5 行。约 6 行改动。

### 问题 7：门禁命令超时抛出未捕获异常

`subprocess.run(..., timeout=1800)`（`wb.py:369`）没有 `except subprocess.TimeoutExpired`。hook 路径有 `cmd_hook` 的兜底 try（`wb.py:1127`），CLI 路径没有。实测 `gate check` 打出完整 Traceback，退出码 1。

超时应当是一条 FAIL 结论，不是崩溃。

### 问题 8：`status` 把 `--force` 强推的阶段显示为「门禁已过」

`cmd_phase` 无论门禁是否通过都写 `passed: True`（`wb.py:506`），只用单独的 `forced` 字段记录。`cmd_status` 的阶段行按 `passed` 打标记（`wb.py:451`）。实测强推 clarify 之后：

```
阶段：vclarify  *analyze  -design  -develop  -verify  -retro   （* = 当前，v = 门禁已过）
```

只有 `report` 会区分「强制通过」并列出遗留失败项。最常用的看板反而看不出哪个阶段是硬推过去的。

**建议**：`gates[cur]["passed"] = passed`，保留 `forced` 字段，`status` 用 `!` 标记强推阶段。2 行改动。

### 问题 9：`contract add` 不校验路径在项目根内

`cmd_contract` 的 add 分支计算相对路径后直接使用（`wb.py:643`），不检查是否越出根目录。实测登记 `../outside.json` 成功，冻结清单变成：

```
['.workbench/state.json', '.workbench/role', '.workbench/unlock',
 '.workbench/frozen', '../outside.json']
```

后果有两面：此后任何提到 `outside.json` 的 Bash 写命令都被拦，而守卫自身也无法通过 Write 修改该文件（越根检查优先），契约进入无法维护的状态。

**建议**：拒绝 `rel.startswith("..")`。1 行改动。

### 问题 10：`current_task` 未纳入冻结清单

`FROZEN_ALWAYS`（`wb.py:126`）包含 `state.json`、`role`、`unlock`、`frozen`，不含 `current_task`。实测 `echo T1 > .workbench/current_task` 放行。

`hook_post_tool` 依据这个文件决定把改动挂到哪个任务上，因此产物归属可以被任意改写。审计链上的缺口。

**建议**：加入 `FROZEN_ALWAYS`。1 行改动。注意这会让问题 4 的误报面再扩大一个词，所以应当与问题 4 的修复一起做。

### 问题 11：跨仓库布局下默认角色范围是错的

`fnmatch` 的 `*` 跨 `/`，导致 CLAUDE.md 里描述的布局 B（一个需求跨多仓库）下默认范围失效。实测：

```
backend-developer  可写 repos/frontend/src/api.py          True   ← 越界写别人仓库
backend-developer  可写 repos/backend/migrations/001.sql   False  ← 自己的迁移写不了
frontend-developer 可写 repos/backend/package.json         True   ← 越界
```

默认值退化成「按语言隔离」而非「按仓库隔离」，方向恰好反了。CLAUDE.md 已承认这一点并要求手动改 `role_scopes`，但依赖人记得改一个静默出错的配置，不是好设计。

**建议**：`init` 时检测工作区内是否存在 `repos/*` 布局，命中就换用按仓库前缀的默认范围。

---

## 五、设计层面

### 问题 12：文档量超过代码量，且已在复制实现细节

`docs/` 1671 行 + `README.md` 202 行 + `CLAUDE.md` 144 行 ≈ 2000 行散文，对应 1488 行代码。

其中 `docs/permissions.md:333-342` 直接抄录了 `selfcheck` 的断言列表。抄一遍就得到一份会漂移的副本 —— 问题 3 里那句与实现不符的陈述正是这样产生的：文档描述了一个"应该拦住"的行为，代码里没有，而两处相隔太远，没人发现。

**建议**：断言只存在于 `selfcheck` 里，文档指向它而不复述。`docs/` 收敛到「为什么这样设计、取舍是什么、已知边界在哪」，「怎么用」交给 `CLAUDE.md` 与 skill 定义。按当前重复情况估算，2000 行可以压到 600 行左右而不丢信息。

### 问题 13：`no_blocked:design` 近乎恒真

design 门禁的 `no_blocked:design`（`wb.py:78`）只检查 `phase == "design"` 且状态为 blocked 的任务。而 architect 在 design 阶段产出的任务图，任务 phase 基本都是 develop，design 阶段自己通常没有任务。这条断言几乎永远为真。

门禁列表看着有 4 条，实际生效 3 条。

### 问题 14：`gate check --phase X` 有副作用

`cmd_gate` 对任意 `--phase` 都会执行该阶段的 `cmd:*` 门禁。实测在 clarify 阶段执行 `gate check --phase develop`，配置的 build 命令真的被执行了。

`--phase` 读起来像「查一下那个阶段的情况」，实际会触发构建。至少应在文档里写明，或对非当前阶段的 `cmd:*` 只报告"未执行"。

### 问题 15：死代码

- `cmd_status:447` 的 `order` 变量赋值后未使用
- `cmd_selfcheck:1145` 的 `run = lambda *a: main(list(a))` 赋值后未使用
- `cmd_task:544` 的未知角色 `die` 不可达 —— argparse 的 `choices=ROLES`（`wb.py:1403`）已经拦在前面

---

## 六、修复顺序建议

**第一批（纯 bug 修复，改动都在几行内，互不冲突）**

1. `frozen_hit` 删掉 basename 匹配，只匹配相对路径 —— 消除问题 4 的误报
2. `current_task` 加入 `FROZEN_ALWAYS` —— 问题 10，与上一条同批做
3. `contract add` 拒绝越根路径 —— 问题 9
4. `cmd:*` 捕获 `TimeoutExpired` 转为 FAIL；完整输出写 `.workbench/gate-<name>.log` —— 问题 6、7
5. `gates[cur]["passed"] = passed`，`status` 标记强推阶段 —— 问题 8
6. 返工任务 `phase` 取当前阶段 —— 问题 5
7. 删除死代码 —— 问题 15

每一项都应在 `selfcheck` 里补上对应断言，否则改完的行为没有回归保护。

**第二批（需要先定路线，不是纯改 bug）**

- **状态并发**（问题 2）：`post-tool` 改为 append `.workbench/artifacts.jsonl`，`task done` 时归并
- **角色隔离**（问题 1）：改为「阶段门禁通过后产物目录进冻结清单」，`role_scopes` 降级为提示词约定
- **Bash 覆盖面**（问题 3）：只补越根检查，同时修正 CLAUDE.md 的错误陈述

**第三批**

- 文档瘦身（问题 12）
- 跨仓库默认范围自动检测（问题 11）
- `no_blocked` 语义修正（问题 13）、`gate check --phase` 副作用说明（问题 14）

---

## 七、评审方法说明

结论的实测部分由三组临时探针脚本产生，覆盖：

- PreToolUse 守卫对 Bash / Write / Edit 的实际拦截边界（含误报用例与对照用例）
- 状态读改写的交错复现
- `fnmatch` 在跨仓库路径下的匹配结果
- 门禁失败输出、超时行为、`--phase` 副作用
- 基于 AST 的未使用变量扫描

探针为一次性验证工具，评审结束后已删除，未进入仓库。需要复现时按本文各问题给出的命令与行号重建即可。

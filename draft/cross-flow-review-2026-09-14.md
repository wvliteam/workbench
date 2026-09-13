# 跨 flow 归属与生命周期评审（2026-09-14）

[cross-flow-review.md](cross-flow-review.md)（2026-09-06 第二轮）的续篇。那轮修掉 P1–P4 并留下四条边界，本文展开其中**第 3 条**（「subagent 的 flow 定位靠指针」），并首次把两条此前未评审的路径纳入核对：**走流程 + 钉 WB_FLOW 的并发编排**、以及**串行接续与 flow 生命周期**。

方法与前作一致：以代码事实为准，在 `/tmp` 建临时工作台跑探针并附原始输出，不动仓库自身状态（探针目录已清理）。文内行号基于 `1087bd9`，定位以函数名为准。

**结论**：主干（隔离维度、守卫并集、指针定点写回）复查仍然成立；但 P2（归属记录带 flow 字段）与 P3（引入 WB_FLOW）两处修复的**交互面从未被测**，实测在并发场景下产物归属整条失效。另有两处生命周期缺口。**本文只做评审，未改任何代码。**

**补记（同日复核）**：P1 的绑定失败有两个分支，原稿探针只跑到良性的那个（空 main → 绑定行不写）。指针 flow 有同号任务时——本仓库、以及「main 承载首条需求」的常态都是这样——是**错绑到指针 flow 的同号任务**，比丢账更坏。这个分支与 P3 确立的「main 非空」现实叠在一起才是常见形态，下文 P1 已补入实证（「绑定失败的两个分支」一节）。复核仍未改任何代码。

## 结论一览

| 维度 | 评审结论 | 处置 |
| --- | --- | --- |
| flow 内并行（锁 / 原子写 / 身份 / 守卫） | 复查与前作一致，仍然成立 | 无需改动 |
| 跨 flow 守卫并集（冻结 / 窗口 / 争议） | 复查成立，失败方向是误拒 | 无需改动 |
| **跨 flow 归属 × WB_FLOW** | **实证失效**：推荐的并发用法下 feature-b 丢账；指针 flow 有同号任务时更会把改动误认到它名下 | **已修（同日）**：归属与权限两个语义分离，见文末「修复记录」 |
| 归属机制的测试覆盖 | 写侧零覆盖：断言只测了读侧的过滤 | **已修**：selfcheck 新增两组归属断言 |
| 串行接续（`init --force`） | **实证残留**：清 state 不清产物目录 | **已修**：`--force` 时清理并提示清理文件数 |
| flow 生命周期（main 语义 / 归档态） | 文档两处口径互相打架，实现只有 active / deleted | **半修**：口径已统一（承认 main 是首条需求线）；归档态仍未做 |
| 争议全线停工 / 配置继承快照 / 窗口全局唯一 | 有意设计，代价明确 | 确认可接受 |

## P1（严重）WB_FLOW 钉死 ≠ 归属正确

### 现象（临时工作台实测）

建临时工作台，`flow new feature-b` 后在 feature-b 建任务 T1，再把指针切回 main —— 即文档推荐的并发形态「两个终端各自 `export WB_FLOW=<名>` 钉死」（`AGENTS.md`「多条并行：flow」）。然后以 `WB_FLOW=feature-b` 模拟该线 subagent 的一次文件写入：

```
$ echo "$PAYLOAD" | WB_FLOW=feature-b python3 wb.py hook post-tool
$ cat .workbench/artifacts.jsonl
{"at": "2026-09-14T00:44:07+0800", "path": "server/x.py", "role": "backend-developer",
 "flow": "main", "agent_id": "a1", "agent_type": "backend-developer", "session_id": "s1"}
```

改动属于 feature-b 的 T1，**记成了 `"flow": "main"`**。同一条链上再跟一步：

```
$ echo "$BIND" | WB_FLOW=feature-b python3 wb.py hook pre-tool    # 模拟 task start T1 的 PreToolUse
$ cat .workbench/task-agents.jsonl
(未创建)                                                           # 绑定行根本没写
$ WB_FLOW=feature-b python3 wb.py task done T1
$ # feature-b 的 T1：
T1 done artifacts = []                                             # 归属全丢
```

对照：`WB_FLOW=feature-b python3 wb.py task list` 正确显示 feature-b 的任务 —— **CLI 侧尊重 WB_FLOW，hook 侧不尊重**，两侧对「当前是哪个 flow」的答案不一致。

**注意**：这一节的探针用的是刚 init 的**空 main**，所以绑定行「没写」。空 main 是罕见分支；指针 flow 有同号任务时的常见形态更坏，见「绑定失败的两个分支」一节。

### 根因：同一个机制读写两侧的 flow 来源不同

| 环节 | 位置 | flow 来源 | 结果 |
| --- | --- | --- | --- |
| CLI 定位（`task` / `status` / `merge` 调用点） | `wb_cli.py:524`、`wb_core.py:107` | WB_FLOW 优先 | feature-b ✓ |
| PreToolUse 写 agent 绑定 | `wb_guard.py:706-712` | `cmd_hook` 清空 override（`wb_guard.py:958`）→ 指针 | main ✗ |
| PostToolUse 写改动流水账 | `wb_guard.py:822` | 同上 → 指针 | main ✗ |
| 归并侧过滤 | `wb_cli.py:363`、`wb_cli.py:373` | 任务所在 flow | feature-b ✗ 全不匹配 |

三个后果叠在一起：

1. **流水账标签错**：改动记到指针 flow 名下，`e.get("flow") != flow` 直接过滤掉（`wb_cli.py:373`）。
2. **绑定行错写或不写**：PreToolUse 遍历的是**指针 flow** 的任务表（`wb_guard.py:706` 的 `load_state(root)`）、按任务 ID 匹配（`_is_task_start`）。指针 flow 没有同号任务时绑定行不产生（现象节跑的就是这个良性分支）；有同号任务时——每条 flow 都从 T1 编起，这才是常态——绑定行**错写到指针 flow 的同号任务上**（详见下节）。两种都让 agent_id 精确认领路径（前作 P2 修的）失效。
3. **归并退化路径也救不了**：绑定缺失时 `merge_artifacts` 会退回「角色 + `started` 时间」匹配（`wb_cli.py:378`），但 flow 过滤发生在它**之前**（373 行），流水账行已被全部跳过。

### 绑定失败的两个分支：错绑比空绑更常见也更坏

现象节的探针用的是刚 init 的空 main，PreToolUse 在 main 的任务表里找不到 T1，绑定行不产生（良性）。但绑定环是按**任务 ID** 在指针 flow 里匹配的，而每条 flow 的任务 ID 都独立从 T1 编起 —— 指针 flow 同样有 T1 才是常态（本仓库 main 就带着 T1–T9，P3 里那个「main 承载首条需求」的现实正是如此）。补一轮探针（main 与 feature-b 各有一个 T1，指针在 main，feature-b 的 `fe-B` 跑 `task start T1`）：

```
$ echo "$PRE" | WB_FLOW=feature-b python3 wb.py hook pre-tool
$ cat .workbench/task-agents.jsonl
{"id": "T1", "role": "backend-developer", "flow": "main",
 "agent_id": "fe-B", "agent_type": "frontend-developer", "session_id": "s2"}
```

feature-b 的 agent `fe-B` 被绑到 **main 的 T1** 上，`role` 还顶了 main 的 backend-developer。后果不是「少一条账」，是**主动误认账**：日后对 main 的 T1 跑 `task done`（flow=main）时，这条绑定（`id=T1, flow=main, agent_id=fe-B`）在 `wb_cli.py:363` 命中 → `agent_ids={fe-B}`；产物流水账里那条同样记着 `flow=main, agent_id=fe-B` 的 feature-b 改动过 373 的 flow 过滤、过 376 的 agent_id 匹配，被并进 **main 的 T1**。feature-b 的改动既从自己的 T1 丢了，又挂到了别的 flow 的任务名下。

换句话说：现象节展示的是**罕见的良性分支**（空 main → 丢账），而与 P3 确立的「main 非空」现实叠在一起的**常见分支是恶性的**（跨 flow 误认账）。修复优先级不变，但严重度按「误认账」算，不是按「丢账」算。

### 为什么前两轮的修复没接住

- 前作 P2 修的是**行的形状**（每行加 `flow` 字段）与**读侧的过滤**，写入侧的 flow 直接取 `read_current_flow`。当时指针是唯一来源，读写天然一致。
- 前作 P3 引入 WB_FLOW 时，只让 **CLI** 尊重它，并明确写了「守卫与 hook 是工作区级视角，不跟调用方 shell 的 WB_FLOW 走」。这句话对**权限判定**成立（守卫必须读全部 flow 并集），但对**归属标签**不成立 —— 归属不需要工作区级视角，它需要「这条改动属于哪个 agent、哪个任务」，而 hook 手里恰好有 `agent_id`。
- **P2 与 P3 各自都测了，交互面没测。** 前作验证清单里的 WB_FLOW 断言只到 `hook session-start` 输出（`wb_selfcheck.py:1782-1802`，断言原文「hook 路径不该被 WB_FLOW 改道」），归属记录的 flow 字段不在断言范围内。更弱的是那条断言本身 —— `assert "项目 demo" in r.stdout`（`wb_selfcheck.py:1798`）里 main 与 feature-b 同名 project，对两个 flow 都为真：它只验到「hook 跑了」，验不出「hook 定位到了哪个 flow」。归属这条数据流从写到读没有任何一处断言碰过。

这是前作判错复盘的**同型错误换了个维度**：那轮总结「教训要跟着维度走，不是跟着代码走」，本轮是「修复要跟着数据流走，不是跟着模块走」—— P2 改了写侧和读侧的一半，P3 改了定位的一半，两半的交点无人认领。

### 影响面

- **不阻断门禁**：`t["artifacts"]` 的唯一消费者是 `merge_artifacts` 自身；`report --write` 不渲染它，`GATES` 里的 `artifacts` 键是阶段产物**文件名列表**（`requirements.md` 等），与之无关。
- **损失的是审计追溯**：而「谁改了哪块」正是这个字段存在的理由 —— `docs/contracts.md:74` 用「`artifacts.jsonl` 里不留角色」论证冻结机制的必要性，说明归属记录被当作可信证据使用。且在常见分支（指针 flow 有同号任务）里，这份「可信证据」不只是缺，是**记反了**：feature-b 的改动落在 main 的 T1 名下，审计据此追溯会追到错的 flow、错的角色。
- **讽刺点**：文档给并发编排两条路 —— 「各自 `export WB_FLOW`」与「串行交错」。实测**坏的是前者**；后者（切指针）归属正常。文档没有提示这个取舍。

### 修复方向（未实施，按侵入性排序）

1. **分离两个语义**（推荐）：`read_current_flow` 同时服务「CLI 定位」与「归属标签」是根因。归属标签应取会话级来源 —— hook 载荷自带 `session_id`，可维护 `.workbench/sessions/<session_id>.flow` 映射，由 CLI 在 WB_FLOW 生效时写入。代价：多一份会话状态，需要清理策略。
2. **CLI 侧写绑定**：`task start` 的 CLI 处理直接写 `{id, flow, at}` 到 `task-agents.jsonl`（flow 定位天然正确），hook 侧按 agent_id 反查。代价：绑定行没有 agent_id，精确认领需要 hook 回填一次（Read-Modify-Write，正是前作刻意避开的形态）。
3. **最小改动**：hook 路径**保留** `os.environ["WB_FLOW"]` 用于归属字段，`set_flow_override(None)` 只影响权限判定。精度限制要说清：只对「启动 harness 前 export」生效，中途改无效 —— 而这个限制恰好与文档用法（终端级 export）吻合。（补核：探针里 hook 进程确实读得到自己环境里的 `WB_FLOW`，所以这条技术上可行；但真实 harness 是否把终端级 `WB_FLOW` 透传给 subagent 的 hook 子进程，仍需实机验证，见「未做的事」。）
4. 无论选哪条，**补 selfcheck**：断言「指针在 main、`WB_FLOW=feature-b` 时，hook 写出的归属行 flow 为 feature-b，`task done` 能归并到产物」；并覆盖恶性分支「指针 flow 有同号 T1 时，绑定不得错写到指针 flow 的 T1 上」。这是当前测试网的准确缺口。

## P2（中）`init --force` 清 state 不清产物

串行接续新需求的官方路径是 `report --write` + `init --force`（`AGENTS.md`「注意」节）。实测：

```
$ # 上一代需求留下的产物
$ find .workbench/artifacts/main -type f
.workbench/artifacts/main/clarify/requirements.md
.workbench/artifacts/main/develop/tasks/T1-backend-developer.md
$ python3 wb.py init --name demo2 --force
$ find .workbench/artifacts/main -type f        # 一个都没少
.workbench/artifacts/main/clarify/requirements.md
.workbench/artifacts/main/develop/tasks/T1-backend-developer.md
$ # 新 state：任务数 0，契约数 0
```

`cmd_init`（`wb_cli.py:43`）用 `mkdir(parents=True, exist_ok=True)` 建目录，没有任何清理逻辑；真正删产物的只有 `flow remove`（`wb_cli.py:1054`）。于是：

- **任务号从 T1 重编，执行记录文件同名撞车** —— `develop/tasks/T1-backend-developer.md` 是上一代需求的。
- **而 `wb-flow` SKILL 规定「会话中断接续时先 glob `tasks/<任务号>-*.md`，读完再决定 task done 还是打回」** —— 照这条规则执行会读到上一条需求的执行记录，把「已完成 / 已改 / 阻塞 / 下一步」当成本次的。执行记录只在三类异常触发点才写（`SKILL` develop 节），多数任务根本不写 —— 于是上一代那份同名记录常常是 glob 到的**唯一**匹配，误导概率不低。
- `requirements.md` 等阶段产物被新需求覆盖（门禁要求存在且非空），旧内容静默消失；`analysis/`、`design/` 同理。更隐蔽的一种：新需求若没重跑 clarify，旧 `requirements.md` 非空、含「验收标准」小节，门禁照样放行 —— 新需求踩着旧验收标准往下走。

失效方向是**误导**而非阻断：不会拦错，但会让接续判断基于错误前提。修法两条：`init --force` 时清空 `artifacts/<flow>/`，或加 `--purge-artifacts` 显式开关 + 默认提示。当前连提示都没有。

## P3（中）main 语义错位与 flow 生命周期缺口

文档两处口径互相打架：

- `AGENTS.md`：「`main` 仅用于工作台初始化和基础配置，不承载具体用户指派的任务。」
- `wb-flow` SKILL「需求线隔离」：「复用空白初始 flow：仅限刚执行 `init` 后，当前 flow 尚无阶段产物、契约、任务、阶段推进历史。」

两条一起执行的结果是**确定的**：init 后第一条需求直接跑在 main（SKILL 允许 —— 默认 `init` 下「空白初始 flow」就是 main），跑完 main 就非空了，从此三条路全断 ——

- 不能复用（「独立需求不得混入已有非空 flow」）；
- 不能删除（`wb_cli.py:1043` 拒删 `DEFAULT_FLOW`，它是指针回退点与老布局兼容位）；
- 不能标记完成（**flow 生命周期只有 active 与 deleted 两态**，没有 archived）。

本仓库就是活例：main 承载 **9 个任务、6 份契约**、跑完 audit 到 retro（实测 `status --json`）。它既不是「只做初始化的 main」，也无法归档。下一步只有两条，各带一个已记录的缺陷：`init --force`（撞 P2）或 `flow new`（main 永久僵尸，产物留在 `artifacts/main/`）。而 P1 的恶性分支正是拴在这个现实上：只要 main 非空且带着 T1，跨 flow 误认账就成立 —— P3 不只是「口径打架」，它同时是 P1 常见形态的前提。

修法二选一，都要先改文档：**要么承认 main 就是第一条需求线**（删掉「仅用于初始化」那句，main 与 `feature-b` 语义对齐），**要么 init 后强制首条需求也 `flow new`**（main 永远空，`flow list` 里它不再是需求线）。归档态是更大的设计问题，可以后置 —— 当前 `report --write` 勉强够用。

## 长期运行的状态膨胀

本次评审顺带核对了「不走流程的路径会不会污染 flow 目录」（触发问题：大量问答与一行改动）。结论：**流程材料不涨，但有一份账本只进不出。**

| 位置 | 不走流程时 | 上限 |
| --- | --- | --- |
| `artifacts/<flow>/<phase>/` 阶段产物 | 不写（只有角色 subagent 走流程时落盘） | — |
| `flows/<flow>/state.json` 的 `tasks` | 不涨 | **无上限、无裁剪**；`report` 不清理 |
| `flows/<flow>/state.json` 的 `log` | 不涨 | `MAX_LOG`（500），溢出追加进 `audit.jsonl` |
| `flows/<flow>/audit.jsonl` | 不涨 | 无上限，append-only |
| **`.workbench/artifacts.jsonl`** | **每次文件写入 +1 行** | **无上限，且无任何删除路径** |

`artifacts.jsonl` 由 PostToolUse 在每次 Write/Edit/Bash 写入时追加（`wb_guard.py:818`），在 `FROZEN_ALWAYS` 里（`wb_const.py:230`），`merge_artifacts` **只读不删**（`wb_cli.py:366-383`），`init --force` 不动它，`flow remove` 只删 `artifacts/<flow>/` 目录。本仓库实测 840→846 行 / 176K（数字随每次文件写入增长 —— 写这份文档本身也记了一行，不是常量）。纯问答与纯读代码零增长（`wb_guard.py:848` 对 Read 直接 return）—— 增长只跟随文件写入。

缓解是环境性的：`.workbench/` 在 `.gitignore`（`.gitignore:2`），不进 git，不污染仓库历史。这是拿「无清理」换「纯 append 无竞态」的有意取舍（`docs/architecture.md:61`），代价真实但可控。

## 复查为仍然成立的机制

前作已核实过的，本次不重推，仅在临时工作台上重新确认了这几条的当前行为与文档一致：

- **守卫并集**：`read_frozen` / `read_unlocks` / `read_disputes` 聚合 `all_flows`（`wb_core.py:125`），A flow 锁定的契约在 B 视角照样冻结。
- **指针定点写回**：`load_state` 把 flow 定在 `st["_flow"]`，`save_state` 写回时 `st.pop("_flow")`（`wb_core.py:309+`）而非现读指针 —— 命令中途切指针不会把 A 的状态写进 B。
- **窗口按 flow 定点收窄**：`contract lock` / `bump` / `SubagentStop` 只关本 flow 的窗口（前作 P1 的修复方向正确，`wb_selfcheck.py:1738-1745` 有断言覆盖）。
- **配置继承**：`flow new` 从 main 继承四个工作区级键（`INHERIT_KEYS`，`wb_core.py:1532`），任务 / 契约 / 阶段不继承。
- **跨 flow 归属过滤**：归并侧按 flow 过滤本身是对的（`wb_cli.py:373`）—— 缺陷 1 在写入侧，不在过滤侧。

## 张力点（有意设计，复查时别当 bug 提）

1. **冻结跨 flow 并集 vs 角色范围按当前 flow 配置**。同一角色在不同 flow 里可以有不同权限，而角色定义（`agents/*.toml`）是全局的 —— 两道防线口径不同。从 `inherit_flow_config` 的设计看是有意的（配置是「这条 flow 怎么干活」），但值得知道。
2. **争议无差别全线停工**（`wb_guard.py:696-700`）。争议记录里带契约名，按消费关系精确匹配可以更细，作者选了保守。前作边界第 1 条已声明，维持。
3. **配置继承是快照**。main 改 `role_scopes` 后已有 flow 不跟随 → 权限规则随时间漂移。可接受，但切换 flow 时值得意识到。
4. **两条 flow 共享同一份契约文件仍是死局源头**（前作边界第 2 条），本次未再触及。

## 验证方式

临时工作台（`/tmp/wbflowtest` 与 `/tmp/wbflowtest2`，探针后均已清理，仓库状态零改动）：

1. `mkdir -p /tmp/wbflowtest/.workbench` → `wb.py init --name demo` → `flow new feature-b` → 在 feature-b `task add` → `flow switch main`（指针回 main，模拟并发形态）；
2. 以 `WB_FLOW=feature-b` 跑 `hook post-tool` / `hook pre-tool`，检查 `artifacts.jsonl` 与 `task-agents.jsonl` 的 flow 字段（P1 现象，空 main 良性分支）；
3. `WB_FLOW=feature-b wb.py task done T1`，读 feature-b 的 state 看 `T1.artifacts`（P1 后果：丢账）；
4. 在 `artifacts/main/` 放两个上一代需求的产物文件 → `init --name demo2 --force` → `find` 复查残留（P2 现象）；
5. 复核补测（`/tmp/wbflowtest2`）：main 与 feature-b **各建一个 T1**、指针在 main，以 `WB_FLOW=feature-b` 跑 `hook pre-tool`（`task start T1`），读 `task-agents.jsonl` —— 绑定行落在 main 的 T1 上、带 feature-b 的 `agent_id`（P1 恶性分支：错绑）。

**注意**：探针里**含写入**的命令（重定向、`sed -i`、`mkdir -p && echo >` 等）必须放进 `/tmp` 脚本执行 —— 命令文本里的 `.workbench/...` 字面量会命中真实工作区的冻结路径，被守卫按 uncertain 分支拦下。这是守卫在正常工作，不是探针失败。纯读命令（`wc -l` / `find -type f` / `grep`）直接敲即可：守卫的冻结文本匹配在 `if BASH_WRITE.search(cmd) or all_targets` 那一步就跳过了，实测六种形态（`wc` 直接 / `<` 输入重定向 / 管道 / `find` / `grep` / `ls`）全部放行。

## 未做的事

- 未改任何代码、配置或状态；探针全部在临时目录。
- 未验证修复方案 1–3 的可行性（`session_id` 映射需要确认 harness 载荷里 `session_id` 在 subagent 内是否稳定不变 —— 这是方案 1 的前置条件，未取证；方案 3 需确认 harness 是否把终端级 `WB_FLOW` 透传给 subagent 的 hook 子进程 —— 探针里手动 export 能读到，实机透传未验）。
- 未评估归档态（P3 后半）的设计成本。

## 修复记录（同日）

按本文优先级依次修复，三项已落地。**未走六阶段流程** —— 用户明确要求直接改，且改动面在工作流内核（`.claude/hooks/`），本就没有角色拥有其写入权，主线程是唯一执行者；每项都附了可复跑的验证。

| 项 | 改动 | 验证 |
| --- | --- | --- |
| P1 | `wb_core.attribution_flow()` —— 归属标签独立于 `read_current_flow`（后者继续服务权限判定，仍读工作区并集）；`wb_guard` 的绑定行与流水账改用前者；绑定环改按 `load_state_of(root, attr)` 找**归属 flow** 的任务表 | 探针：指针在 main、`WB_FLOW=feature-b` 时绑定行落 feature-b 的 T1（`role=frontend-developer`）、流水账 `flow=feature-b`、`task done` 把改动归并到 feature-b 而 main 的 T1 为空（修复前两者恰好相反）；对照组不钉 `WB_FLOW` 时仍按指针。selfcheck 新增「跨 flow 回归 4」两条断言，role 字段是防错绑的第二个证据 |
| P2 | `cmd_init` 在 `--force` 时清空 `artifacts/<flow>/`，并把清理掉的文件数打出来 | 探针实测残留归零；selfcheck 新增 #11 断言，覆盖「未提示清理」与「文件仍在」两个失败面 |
| P3 | `AGENTS.md` 与 `wb-flow` SKILL 口径统一：承认 `main` 是第一条需求线 —— init 后首条需求落在它上面，此后不可删、不再收新需求 | 文档改动，无断言可加；归档态仍未做（本文列为可后置） |

**P1 修复留下的前提**：归属标签取 `os.environ["WB_FLOW"]`，依赖 harness 把终端级 `WB_FLOW` 透传给 hook 子进程 —— 探针里手动 export 读得到，**真实 harness 的透传仍未实机验证**。若哪天确认不透传，失效方向是退回修复前的行为（归属跟指针），不比原来更坏；届时改走本文「修复方向 1」（会话级映射）。

## 关联

- 前作（P1–P4 修复与遗留边界）：[cross-flow-review.md](cross-flow-review.md)
- 机制第一轮：[parallel-implementation.md](parallel-implementation.md)
- 未修问题清单（含守卫三层等更高优先级项）：[open-issues-2026-09-10.md](open-issues-2026-09-10.md)
- 用法层：`AGENTS.md`「多条需求并行：flow」、`.claude/skills/wb-flow/SKILL.md`「需求线隔离」
- 设计取舍：`docs/architecture.md`（状态文件表、并发与锁）、`docs/scheduling.md`

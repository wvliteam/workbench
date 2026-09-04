# 调度与 Loop 执行

调度解决一个问题：**哪些任务现在可以并行派发。** Loop 解决另一个：**怎么连续推进而不失控。**

## 就绪集合

`ready_tasks()` 二十行，一次依赖图前沿扫描：**状态为 `todo` 且所有前置任务都处于满足依赖的终态（`done` 或带明确理由的 `skipped`）**，可选按阶段与角色过滤。`blocked` 与 `stale` 都不满足依赖。不是拓扑排序 —— 不需要全序，只需要「现在能动的那一批」。每轮重新算，所以没有增量状态要维护。复杂度 O(任务数)，上千条仍毫秒级。

输出按（阶段顺序，任务 ID）排序，让派发批次稳定可预测 —— 同样的状态两次 `next --all` 给同样的顺序，否则 loop 的日志无法复现。

### 依赖只写真依赖

这是任务拆分时最重要的一条规则，写在 `architect` agent 定义里：

> 契约锁定后，前端**不依赖**后端实现完成 —— 双方对着契约并行。写 `--deps` 会串行化，白等。只有「后端产出的东西前端必须先读到」才是真依赖（如生成的 client、迁移后的数据）。

反例与正例：

```bash
# 错：前端等后端，并行退化成串行
task add --title "用户列表接口" --role backend-developer --phase develop
task add --title "用户列表页"   --role frontend-developer --phase develop --deps T1

# 对：契约已锁，两者并行；测试才依赖两者
task add --title "用户列表接口" --role backend-developer  --phase develop --contracts user-api
task add --title "用户列表页"   --role frontend-developer --phase develop --contracts user-api
task add --title "分页契约测试" --role qa --phase verify --deps T1,T2
```

第二种写法下 `next --all` 一次返回 T1 与 T2，一批派出去。

**假依赖是这套流程最常见的性能损失**，且不报错，只表现为「跑得慢」。复盘时看 `deps` 里有多少条其实不必要。

## 并行派发协议

```bash
wb.py next --all --json     # 一批（受 max_parallel 限制，默认 3）
wb.py next                  # 只要第一个
wb.py next --role qa        # 指定角色
wb.py next --any-phase      # 不限当前阶段
```

主线程拿到一批后，**放在同一条消息里用多个 Agent 调用同时派出去**。分多条消息发就是串行，浪费掉契约先行并行开发的全部收益。

每个 subagent 的 prompt 必须给到：任务 ID（它要用来跑 `task start` / `task check`）、任务标题与范围（避免顺手改别的）、要读的契约文件路径（实现的唯一事实来源）、完整的 `{name, version, revision, sha}` 契约快照、相关的验收标准条目（「做到什么程度算完」）、上游产物路径（`current-state.md` 里的既有约定与可复用资产）。

只说「做 T1」的派发会让 subagent 自己去摸索上下文，慢且容易跑偏 —— 它的上下文是独立的，你知道的它一概不知道。

### 并发上限

```bash
wb.py config set max_parallel 5
```

`next --all` 最多返回 `max_parallel` 条。往上调之前确认**这一批任务写入的目录不重叠** —— 同一批里两个 agent 改同一个文件会互相覆盖，且没有任何机制能检出（Edit 的 `old_string` 匹配可能恰好都成功）。

默认 3 是保守值。前后端分离清晰的项目可以调到 5–6；单体项目里多个任务都改 `src/` 时应该调到 1–2。

### 退出码

`next` 的退出码区分「阶段做完了」和「还在等」，便于串进脚本：

| 退出码 | 含义 | 编排者该做什么 |
| --- | --- | --- |
| 0 有输出 | 有就绪任务 | 并行派发 |
| 0 无输出 | 无就绪、无进行中、无 `blocked` 或 `stale` | 该阶段做完了，跑 `gate check` |
| 3 | 无就绪，但有进行中、`blocked` 或 `stale` 的任务 | 等上一批回来，或先解阻塞。**不要重复派发** |

无就绪任务时的文本输出会说明是哪种情况：`无就绪任务。进行中：T3。阻塞或 stale：T4。` 或 `无就绪任务。该阶段可以跑门禁了。`

## 任务生命周期

```
        task add
            ↓
    ┌─────────────┐ ← task reopen
    │    todo     │ ───────────────┐
    └──────┬──────┘                │ task skip --reason
           │ task start            ▼
           │                ┌─────────────┐
           ▼                │   skipped   │
    ┌─────────────┐          └─────────────┘
    │   doing     │
    └──────┬──────┘
           │ task done                 task block --reason
           ▼                           ▼
    ┌─────────────┐               ┌─────────────┐
    │    done     │               │   blocked   │
    └──────┬──────┘               └──────┬──────┘
           │ 上游 blocked/stale          │ task reopen
           ▼                             │
    ┌─────────────┐                      │
    │    stale    │ ─────────────────────┘
    └─────────────┘       task reopen
```

`done` 与带明确 `--reason` 的 `skipped` 满足下游依赖；`blocked` 与 `stale` 均不满足。任务被标为 `blocked` 或 `stale` 时，失效沿依赖图递归传播到全部传递下游，包括此前已经 `done` 的任务。恢复 `stale` 任务前，必须先让所有依赖不再处于 `blocked` / `stale`，刷新它绑定的契约快照，再执行 `task reopen`、`task start` 和写入前的 `task check`。

```bash
wb.py task start T1                    # 依赖未满足时退出码 1
wb.py task start T1 --force            # 忽略依赖检查
wb.py task start T1 --role-lock        # 同时把写入范围锁到该任务的角色
wb.py task done T1 --note "接口 + 契约测试已通"
wb.py task block T2 --reason "契约缺 total 字段"
wb.py task skip T3 --reason "该任务对本项目不适用"
wb.py task reopen T2 --note "契约已 bump 到 v2"
```

**依赖检查在 `start` 而非 `add`**：`add` 时依赖的任务可能还没建（先建 T2 再建它依赖的 T1 是合法的，只要建 T1 时用 `--id`）。`add` 只校验依赖的任务**存在**，避免写错 ID 造成永久无法就绪的僵尸任务；`start` 则要求依赖处于 `done` 或带理由的 `skipped`。

### 产物归属

`task start` 记下 `started` 时间戳。`PostToolUse` hook 把每次改动追加一行到 `.workbench/artifacts.jsonl`（路径 + 当时的角色 + 时间），`task done` 时按「角色 + `started` 之后」认领并归并进任务的 `artifacts`。

归属不看「当前任务」那种单文件 —— 并行下它的内容永远是最后启动的那个任务，据它归属会把两个 subagent 的改动全挂到一个任务上。

**剩余边界：同一角色的两个任务并行时分不开**，它们的改动会互相认领。不是上游限制 —— hook 载荷里有 `agent_id`，`PostToolUse` 能拿到。真正的障碍在另一头：`task start` 是 subagent 自己在 shell 里跑的 CLI，那个进程拿不到自己的 `agent_id`（它只出现在 hook 的 stdin 载荷里），所以任务与 agent 对不上号。要修得让编排者代跑 `task start`，那会把「subagent 自己开工」这条简单约定换成一轮额外往返。当前的取法在「一个角色同时只跑一个任务」下是准的，那也是 `max_parallel` 默认值下的常态。

`--role-lock` 是给编排者用的便捷开关（`start` 的同时 `role set`）。subagent 自己开工时通常先 `role set` 再 `task start`，两种路径等价。

## Loop 执行

`/wb-loop` skill 驱动自动排空。每轮开头**重读状态**（`status` + `next --all --json`），不用上一轮的记忆 —— 期间可能有 subagent 落盘、有契约 bump 造出新任务。

四种分支：有就绪任务就并行派发整批，回来逐个验证产物再 `task done`；无就绪但有进行中就等，**不重复派发**；无就绪但有阻塞或 stale 就停下交人决策；全部任务都处于满足依赖的终态（`done` 或带理由的 `skipped`）就 `gate check`，过了 `advance`，不过则派 subagent 补齐。

### 派发回来必须验证

> **subagent 说做完了不等于做完了。** 至少确认它声称改的文件存在、它声称跑过的命令你也跑一遍，再 `task done`。

这条是 loop 里最重要的一句。自动化循环最危险的失效模式是「乐观确认级联」—— subagent 报告成功，编排者标 done，门禁因为 `tasks_done` 通过，阶段推进，最后在 verify 阶段发现前三个任务都没真做完。验证一次的成本远低于回滚三个阶段。

### 七种必停条件

碰到任一条就停，报告状态并把决策交给用户。**不自行绕过。**

| 条件 | 为什么要人 |
| --- | --- |
| 阻塞任务需要改契约或改需求 | 契约变更的影响面由人确认 |
| 门禁不通过且修不动 | 例如测试暴露的是设计问题，不是代码问题 |
| 需要 `--force` 强推 | 强推是作废门禁，必须是人的决定 |
| 同一任务失败两次 | 第三次还是同样的失败说明理解错了，不是手滑 |
| QA 报出 blocker 级缺陷 | 修还是接受是产品决策 |
| 契约漂移 | 有人绕过 `bump` 改了契约，需要判断是有意还是误改 |
| 走完 retro 阶段 | 流程结束 |

### 防失控

**轮次上限 12。** 到上限就停下汇报，不管进度如何。

防的不是逻辑死循环（`next` 是纯函数，不会自己产生任务），而是**同一个门禁失败反复重试**：门禁 FAIL → 派 subagent 补齐 → subagent 没真正解决 → 门禁还是 FAIL → 再派。这个循环每轮都在烧 token 且不收敛。「同一任务失败两次就停」是第一道防线，轮次上限是第二道。

**每轮记一条日志**（`wb.py log "第 3 轮：派发 T3,T4；T3 完成，T4 阻塞于契约"`）。复盘阶段 `reviewer` 靠这些还原过程 —— 没有日志的自动化循环在复盘时是黑盒。

### 与内置 /loop 的区别

| | `/wb-loop` | 内置 `/loop` |
| --- | --- | --- |
| 触发 | 事件驱动：任务清空就推进 | 时间驱动：固定间隔重跑 |
| 用途 | 排空任务队列、推进阶段 | 轮询外部状态（等 CI、等部署） |
| 终止 | 七种条件或 12 轮 | 用户停止或 7 天过期 |

要「每 5 分钟检查一次 CI 然后继续」用内置 `/loop`；要「把 develop 阶段的任务都做完」用 `/wb-loop`。

## 汇报格式

Loop 停下来时**四行以内**：跑了几轮、现在什么阶段、卡在哪一条、需要用户决定什么。

不要罗列每一轮的细节 —— 用户看不到 subagent 的输出，也不需要看编排的过程，只需要知道现在的状态和该做什么决定。过程在 `wb.py log` 里，需要时再查。

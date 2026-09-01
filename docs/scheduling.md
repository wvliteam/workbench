# 调度与 Loop 执行

调度解决一个问题：**哪些任务现在可以并行派发。** Loop 解决另一个：**怎么连续推进而不失控。**

## 就绪集合

核心算法在 `ready_tasks()`，二十行：

```python
def ready_tasks(st, phase=None, role=None):
    done = {t["id"] for t in st["tasks"] if t["status"] == "done"}
    out = []
    for t in st["tasks"]:
        if t["status"] != "todo":          continue
        if phase and t["phase"] != phase:  continue
        if role  and t["role"]  != role:   continue
        if all(d.upper() in done for d in t.get("deps", [])):
            out.append(t)
    order = {p: i for i, p in enumerate(PHASES)}
    out.sort(key=lambda t: (order.get(t["phase"], 99), t["id"]))
    return out
```

**就绪 = 状态为 `todo` 且所有前置任务已 `done`。** 这是依赖图上的一次前沿扫描，不是拓扑排序 —— 不需要全序，只需要「现在能动的那一批」。每轮重新算，所以不需要维护增量状态。

排序按（阶段顺序，任务 ID），让输出稳定可预测。

复杂度 O(n)，n 是任务数。任务上千条时仍然毫秒级，不需要索引。

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

第二种写法下 `next --all` 返回 T1 与 T2，一批派出去。实测输出：

```
$ wb.py next --all
T1	backend-developer	用户列表接口  契约:user-api
T2	frontend-developer	用户列表页  契约:user-api
```

**假依赖是这套流程最常见的性能损失**，且不报错，只表现为「跑得慢」。复盘时看 `deps` 里有多少条其实不必要。

## 并行派发协议

```bash
wb.py next --all --json     # 一批（受 max_parallel 限制，默认 3）
wb.py next                  # 只要第一个
wb.py next --role qa        # 指定角色
wb.py next --any-phase      # 不限当前阶段
```

主线程拿到一批后，**放在同一条消息里用多个 Agent 调用同时派出去**。分多条消息发就是串行，浪费掉契约先行的全部收益。

每个 subagent 的 prompt 必须给到：

| 内容 | 为什么必需 |
| --- | --- |
| 任务 ID | subagent 要用它跑 `task start` / `task done` |
| 任务标题与范围 | 避免超出任务范围顺手改别的 |
| 要读的契约文件路径 | 契约是实现的唯一事实来源 |
| 相关的验收标准条目 | 让 subagent 知道「做到什么程度算完」 |
| 上游产物路径 | `current-state.md` 里的既有约定与可复用资产 |

只说「做 T1」的派发会让 subagent 自己去摸索上下文，慢且容易跑偏。

### 并发上限

```bash
wb.py config set max_parallel 5
```

`next --all` 最多返回 `max_parallel` 条。往上调之前确认**这一批任务写入的目录不重叠** —— 同一批里两个 agent 改同一个文件会互相覆盖，且没有任何机制能检出（Edit 工具的 old_string 匹配可能恰好都成功）。

默认 3 是保守值。前后端分离清晰的项目可以调到 5–6；单体项目里多个任务都改 `src/` 时应该调到 1–2。

### 退出码

`next` 的退出码区分「阶段做完了」和「还在等」，便于串进脚本：

| 退出码 | 含义 | 编排者该做什么 |
| --- | --- | --- |
| 0 有输出 | 有就绪任务 | 并行派发 |
| 0 无输出 | 无就绪、无进行中、无阻塞 | 该阶段做完了，跑 `gate check` |
| 3 | 无就绪，但有进行中或阻塞的任务 | 等上一批回来，或先解阻塞。**不要重复派发** |

无就绪任务时的文本输出会说明是哪种情况：`无就绪任务。进行中：T3. 阻塞：T4.` 或 `无就绪任务。该阶段可以跑门禁了。`

## 任务生命周期

```
        task add
            ↓
    ┌─────────────┐
    │    todo     │ ← task reopen
    └──────┬──────┘
           │ task start（依赖未完成时拒绝）
           ▼
    ┌─────────────┐   task block --reason   ┌─────────────┐
    │   doing     │ ──────────────────────> │  blocked    │
    └──────┬──────┘                         └──────┬──────┘
           │ task done                              │ task reopen
           ▼                                        │
    ┌─────────────┐ <───────────────────────────────┘
    │    done     │
    └─────────────┘
```

```bash
wb.py task start T1                    # 依赖未 done 时退出码 1
wb.py task start T1 --force            # 忽略依赖检查
wb.py task start T1 --role-lock        # 同时把写入范围锁到该任务的角色
wb.py task done T1 --note "接口 + 契约测试已通"
wb.py task block T2 --reason "契约缺 total 字段"
wb.py task reopen T2 --note "契约已 bump 到 v2"
```

`task start` 会把任务 ID 写进 `.workbench/current_task`，`PostToolUse` hook 靠它把改动文件挂到任务上。`task done` 清除它。

`--role-lock` 是给编排者用的便捷开关（`start` 的同时 `role set`）。subagent 自己开工时通常先 `role set` 再 `task start`，两种路径等价。

### 依赖检查在 start 而非 add

`task add` 时依赖的任务可能还没建（先建 T2 再建它依赖的 T1 是合法的，只要建 T1 时用 `--id`）。检查放在 `start`：

```python
undone = [d for d in t["deps"] if (find_task(st, d) or {}).get("status") != "done"]
if undone and not args.force:
    die(f"依赖未完成：{', '.join(undone)}（--force 忽略）")
```

`add` 时只校验依赖的任务**存在**，避免写错 ID 造成永久无法就绪的僵尸任务。

## Loop 执行

`/wb-loop` skill 驱动自动排空。循环体：

```
每轮开头重读状态（不用上一轮的记忆）
        ↓
wb.py status && wb.py next --all --json
        ↓
┌───────────────────┬──────────────────┬─────────────────┬──────────────┐
│ 有就绪任务         │ 无就绪 有进行中   │ 无就绪 有阻塞    │ 全部完成      │
├───────────────────┼──────────────────┼─────────────────┼──────────────┤
│ 并行派发整批       │ 等，不重复派发    │ 停 → 交人决策    │ gate check   │
│ 回来逐个验证产物   │                  │                 │ 过 → advance │
│ 再 task done      │                  │                 │ 不过 → 补齐  │
└───────────────────┴──────────────────┴─────────────────┴──────────────┘
        ↓
回到循环开头
```

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

**每轮记一条日志：**

```bash
wb.py log "第 3 轮：派发 T3,T4；T3 完成，T4 阻塞于契约"
```

复盘阶段 `reviewer` 靠这些还原过程。没有日志的自动化循环在复盘时是黑盒。

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

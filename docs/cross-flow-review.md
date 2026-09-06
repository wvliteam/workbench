# 跨 flow 并发评审与修复记录（2026-09-06）

flow（需求线）机制落地当天做的第二轮评审：**以代码事实为准逐条核对 [parallel-implementation.md](parallel-implementation.md) 的三步声明，再用临时目录探针实跑验证疑点**（不动仓库自身的状态），发现四处跨 flow 缺口并当日修复。第一轮的机制现状与判错在 [parallel-implementation.md](parallel-implementation.md)；本文记评审发现了什么、为什么是问题、修成什么样、还剩什么边界。

改动范围：`wb.py` 四处（`close_unlock` / `merge_artifacts` / `hook_subagent_stop` / `read_current_flow` / `cmd_init`）+ selfcheck 三组新断言 + CLAUDE.md / scheduling.md 用法层同步。

## 结论一览

| 维度 | 评审结论 | 处置 |
| --- | --- | --- |
| flow 内并行（同一条线的任务批并行派发） | 合理且扎实：锁、原子写、身份归属、守卫四层都有实测与断言支撑 | 无需改动 |
| 跨 flow 守卫视角（冻结/窗口/争议并集） | 合理，失败方向一致是误拒 | 无需改动 |
| 跨 flow 生命周期（窗口清理） | **实证 bug**：SubagentStop 全 flow 关窗 → 契约死锁 | 已修（P1） |
| 跨 flow 身份（产物归属） | **实证 bug**：共享归属文件无 flow 维度，同名任务互相认领 | 已修（P2） |
| 跨 flow 状态定位（并发编排） | 指针竞态：状态命令按共享指针在调用时刻定位 | WB_FLOW 钉 CLI + 文档如实声明（P3） |
| 跨 flow 配置 | `flow new` 丢工作区级配置，守卫范围随指针漂移 | 已修（P4） |

## 核实为合理的机制（复查时不必重推）

评审先确认了这些机制与第一轮文档声明一致，修复不碰它们：

- **状态并发**：每 flow 一把 flock（`acquire_state_lock`），所有读改写命令进 `load_state(lock=True)` 临界区；`save_state` 用 pid 后缀临时文件原子替换，`write_frozen` 先于 state 落盘 —— 崩溃方向是「多冻误拒」而非「漏拦放行」。docstring 里的实测数字（45 个并发 task done 丢 23、12000 次读 5588 次空清单）与 selfcheck 断言对得上。
- **指针定点**：`load_state` 读入时把 flow 定在 `st["_flow"]`，命令中途切指针不会把 A flow 的状态写进 B flow 的文件。
- **守卫并集**：`read_frozen` / `read_unlock_records` / `read_disputes` / `unlocked_paths` / `contracts_for` 全部聚合 `all_flows()`，A flow 锁定的契约不因指针切走而失效。
- **并行身份**：subagent 角色取 hook 载荷的 `agent_type`（`current_role` 三态降级，有 agent_id 无类型显式拒绝而非放行）；`task start` 的 PreToolUse 把 agent_id 写进绑定文件，`task done` 精确归并；`hook_subagent_stop` 在有兄弟 doing 时不清理。
- **两段式推进**：`phase advance` 锁外跑门禁命令、入锁前重查阶段是否被别人推进。
- **嵌套根**：`find_root` 环境变量钉根；`nested_roots` 只反查会话根之内，冻结检查对每个写入目标循环会话根 + 嵌套根。

## P1 SubagentStop 全 flow 关窗 → 契约死锁（最重）

**现象**（探针三步复现）：

1. feature-b 的 architect `contract unlock` 开窗，把契约正文改到一半；
2. 指针在 main（无 doing 任务）的一个 subagent 结束，SubagentStop 输出「解冻窗口已随子 agent 结束关闭」—— 关掉的是 feature-b 的窗口；
3. 此后 feature-b 三条路全断：`bump` 被拒（没有窗口可消费）、`unlock` 再申报被拒（`cmd_contract` 要求正文哈希等于基线才准开窗）、`contract verify` 永久 FAIL。唯一出路是手工恢复旧正文。

**根因**：`hook_subagent_stop` 的「是否清理」判断只看指针 flow 的 doing 任务表，`close_unlock(root)` 不带 flow 参数却关**全部 flow** 的同名窗口。第一轮注释的理由是「宁可多关不能悬挂（误伤只是把申报重做一遍）」—— 探针证明误关的代价不是重报，是死锁；两个方向的代价不对称，兜底方向选反了。

**修复**：

- `close_unlock` 的清理与 doing 判断用同一个 flow 定点；
- `contract lock` / `bump` 的关窗同样只限本 flow —— A 的 lock 不能替另一条流水线收尾。

同型但**有意未修**的：`bump` 消费窗口的记录查找仍按聚合 —— 安全，因为 `unlock` 的聚合查重保证同一契约名全工作区同时只有一个窗口；`dispute` 哨兵保持工作区级（全线停工语义，见遗留边界第 1 条）。

## P2 归属文件没有 flow 维度 → 同名任务互相认领

**现象**：main 与 feature-b 各有一个 T1、各绑一个 agent 后，main 的 T1 把 feature-b 前端 agent 写的 `repos/other/somefile.py` 归并进了自己的 artifacts。

**根因**：`task-agents.jsonl` 与 `artifacts.jsonl` 放在 `.workbench/` 根（工作区共享），任务 ID 每条 flow 独立从 T1 编起，`merge_artifacts` 只按任务 ID 匹配 —— 跨 flow 的 ID 冲突是结构性的，不是巧合。

**修复**：两个文件的写入侧（`hook_pre_tool` 的绑定、`hook_post_tool` 的流水账）每行带 `flow`；`merge_artifacts` 按任务所在 flow 过滤。无 flow 字段的旧行按 main 归属 —— 语义取舍见判错复盘第二条。

## P3 状态命令按共享指针定位 → 并发编排竞态

**现象**（结构性，代码可证）：CLI 状态命令在**调用时刻**按 `current-flow` 指针定位（`load_state` → `read_current_flow`），`task` / `contract` / `phase` 都没有按命令的 flow 参数。并发推两条 flow 时，A 的 subagent 跑 `task start T1` 的瞬间若指针已被切到 B，命令落到 B 的 state 上 —— ID 不存在时报错还好，ID 撞了（每条 flow 都有 T1）就是启动别人的任务。

第一轮否掉「每命令选择器」的理由（subagent 不带 flag、守卫不能依赖 flag）**对守卫成立、对状态层不成立**：守卫是并集视角确实不需要 flag，但状态层的定位恰恰需要。

**修复**（两层）：

- CLI 增加 `WB_FLOW` 环境变量显式钉 flow（`read_current_flow` 优先读它；`main()` 读入，`cmd_hook` 清空，selfcheck 摘除环境变量保证自检环境无关）。两个终端并行各推一条 flow 时各自 export，状态命令不再被对方切走的指针带跑；守卫与 hook 不受它影响。
- 文档如实降级：不钉 WB_FLOW 时，多 flow 只支持「编排者串行交错」。

## P4 `flow new` 丢工作区级配置

**现象**：main 配了 `gate_commands.test` 与 `max_parallel=7` 后 `flow new`，新 flow 两项全回默认值。

**根因**：`cmd_init` 每次从默认值与 `repo_layout_scopes` 现推；而 `role_scopes` / `gate_commands` / `gate_timeout` / `max_parallel` 描述的是「这个工作区怎么干活」，却存在每条 flow 各自的 state 里。更隐蔽的一层：守卫的角色范围按**指针 flow** 的 state 读 —— 不继承的话，切了指针同一批 agent 被按另一套范围判定，repos/ 布局下手写认领的仓库前缀整个丢失。

**修复**：`inherit_flow_config` 让新 flow 从 main 继承这四个键（main 是配置的事实标准源），init 输出与日志都留痕。任务、契约、阶段不继承 —— 那是进度，不是配置。

## 判错复盘

| 当时的判断 | 实际 | 为什么错 |
| --- | --- | --- |
| 修复前：close_unlock 的「宁可多关」是可接受的兜底 | 误关把改到一半的契约拆成 bump / unlock 双拒死局 | 只推演了「悬挂窗口」的代价，没推演「窗口属于另一条在制品 flow」的代价 —— 兜底方向的代价要按最坏情况算，不是按平均值 |
| P2 第一版：无 flow 字段的旧行「视为本 flow」 | feature-b 的 T1 同样匹配到旧行，串扰换了个方向还在 | `e.get("flow", flow)` 的默认值让「缺字段」在每条 flow 里都成立。selfcheck 断言当场打回 —— 跨机制兼容的默认值要选一个确定的根（main），不是调用方上下文 |
| selfcheck 并发断言全绿 = 并发安全 | 全部并发断言都在单 flow 内，跨 flow 分支零覆盖 | 第一轮自己总结过「断言要覆盖每一条匹配分支」，但机制换维度（单根 → 多 flow）时断言没有跟着换 —— 教训要跟着维度走，不是跟着代码走 |

## 修复后仍成立的边界（复查时别当 bug 提）

1. **争议哨兵仍是工作区级**。A flow 的 dispute 停所有 flow 的 developer 是写明的语义；`dispute --clear --name` 不带 flow 定点，跨 flow 清理时「已解除」的提示可能与哨兵实情不符 —— 争议是全线停工信号，全局方向保守，暂不动。
2. **两条 flow 共享同一份契约文件时，先 bump 的那条会让另一条陷入 P1 同型死局**（对方基线哈希与文件不符，unlock 被拒）。这是「契约按 flow 各记一份、文件只有一份」的模型张力：跨 flow 契约本来就靠 owner 拆，同一份文件跨 flow 登记是工作流味道，先靠文档警告。
3. **subagent 的 flow 定位靠指针**。WB_FLOW 钉的是编排者与脚本；subagent 的状态命令仍按指针走，并发安全依赖「一批一条 flow」的编排纪律。
4. **既有取舍未变**：Windows 无 fcntl 静默无锁；uncertain shell 写入即拒；`.workbench/role` 主线程兜底是工作区级单文件。

## 验证方式

- selfcheck 新增三组断言，全部在 feature-b 存活的完整链路里跑：
  - **跨 flow 关窗**：feature-b 开窗 → 指针切回 main → SubagentStop → feature-b 窗口必须存活；
  - **跨 flow 归属**：两边各有 T1，带 flow 与不带 flow 的行各一条，main 与 feature-b 各自只认领自己的；
  - **WB_FLOW**：指针在 main、`WB_FLOW=feature-b`，`task list` / `status` 钉到 feature-b，`hook session-start` 仍按指针走，非法值拒绝。
- selfcheck 在干净环境与 `WB_FLOW` 残留环境各跑一遍全绿。selfcheck 进程内必须摘除 `WB_FLOW` —— `quiet()` 反复走 `main()`，每次都从环境重读，只清全局变量不够。
- 端到端探针（临时目录，已清理）：P1 死锁链在修复后不可再触发，feature-b 的 bump 闭环正常；P4 继承与 P2 隔离与断言结论一致。

## 关联

- 机制从哪来：[parallel-implementation.md](parallel-implementation.md)（第一轮三步改造）
- 用法层结论：[CLAUDE.md](../CLAUDE.md)「多条需求并行：flow」小节
- 上一轮评审的判错记录：[review.md](review.md)

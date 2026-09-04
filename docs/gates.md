# 门禁引擎

门禁（gate）是阶段准出条件。不满足时 `phase advance` 退出码 1，阶段推不动。这是让流程有约束力的第一个机制。

## 设计目标

一句话：**把「应该先做完 X 再做 Y」从提示词里的提醒变成退出码。**

提示词说「先写完需求文档再设计」，模型大部分时候遵守。会话被压缩、用户中途插话、subagent 报告过于乐观时，就不遵守了。门禁不受这些影响 —— 它只看落盘的事实。

## 规则表

全部规则集中在 `wb.py` 的 `GATES` 字典，两个键：

- `artifacts` —— 文件必须存在于 `.workbench/artifacts/<阶段>/` 且**非空**。空文件不算产出。
- `checks` —— 断言字符串，格式 `类型:参数[:参数]`，由 `run_check()` 分派。

加一条准出条件是往表里加一行字符串，不需要写代码。

## 六个阶段的门禁

| 阶段 | 产物 | 章节要求 | 其他断言 | 过门禁后冻结为 |
| --- | --- | --- | --- | --- |
| clarify | `requirements.md` | `验收标准`、`非目标` | — | `artifact-requirements`（owner `pm`） |
| analyze | `current-state.md` | `风险` | — | `artifact-current-state`（owner `analyst`） |
| design | `design.md` | `方案对比` | `contracts_locked` `tasks_exist` `no_blocked:*` | `design-doc` —— architect 自己登记 |
| develop | `verification.md` | — | `contracts_intact` `tasks_done:develop` `cmd:lint` `cmd:build` | 不冻结 |
| verify | `test-report.md` | — | `contracts_intact` `tasks_done:verify` `cmd:test` | `artifact-test-report`（owner `qa`） |
| retro | `retro.md` | `改进项` | `tasks_done:*` | `artifact-retro`（owner `reviewer`） |

产物路径与章节是**阶段间的接口** —— 下游 subagent 按固定路径读上游产物，所以它们硬编码在表里而不是配置项。

## 八种断言

| 断言 | 语法 | 通过条件 | 用意 |
| --- | --- | --- | --- |
| 产物存在 | （`artifacts` 键） | 文件存在且 size > 0 | 阶段真的产出了东西 |
| 章节包含 | `artifact_contains:<文件>:<字符串>` | 文件内容含该子串 | 产物覆盖了关键思考，不是敷衍 |
| 契约已锁定 | `contracts_locked` | 接口契约非空且每份都有 `sha`（阶段产物不计） | 并行开发的前提已就绪；也让 `design.md` 转为只读 |
| 契约无漂移 | `contracts_intact` | 每份契约当前哈希 == 锁定哈希 | 兜住守卫覆盖不到的改动路径 |
| 已拆解任务 | `tasks_exist` | 任务数 > 0 | 设计阶段真的落到了可执行任务 |
| 任务完成 | `tasks_done:<阶段>` 或 `tasks_done:*` | 该范围内任务全部 `done` | 活干完了 |
| 无阻塞 | `no_blocked:<阶段>` 或 `no_blocked:*` | 该范围内无 `blocked` 任务 | 阻塞项被处理而非绕过 |
| 命令门禁 | `cmd:<键>` | `gate_commands[键]` 退出码 0 | 测试/构建/lint 真的通过 |

### 实现要点

**`artifact_contains` 是字符串包含，不解析 Markdown。** 粗糙但有效 —— 它挡的是「需求文档写了但没写验收标准」，不是格式问题。解析 Markdown 会引入依赖并对写法过于挑剔。

**`contracts_locked` 只数接口契约，且在没有接口契约时判 FAIL**，理由说明写「无接口的纯本地改动可 `--force` 跳过」。这是刻意的：默认假设有接口，让「确实没有接口」成为需要显式声明的例外，而不是反过来。

「只数接口契约」这半句是后补的，不补就有个静默失效：`phase advance` 会把过了门禁的阶段产物自动登记成契约（`kind: "artifact"`）。不做区分的话 clarify 一过契约列表就永远非空，这条断言再也逼不出「并行开发前先把接口定下来」—— 门禁还在列表里，但永远 PASS。`selfcheck` 有一对断言盯这件事：clarify 过完之后 `artifact-requirements` 必须已登记，而 `gate check --phase design` 必须仍以「尚未登记任何接口契约」FAIL。

**`contracts_intact` 与权限守卫不是重复。** 守卫在改之前拦，能给出可操作的拒绝理由；这条断言在门禁时抓，不管改动从哪来（[architecture.md](architecture.md#冻结防线覆盖不到的写入路径)）。

**`tasks_done:<阶段>` 在该阶段无任务时判 PASS**（说明「无任务（视为通过）」）。避免 develop 阶段没有前端任务时被自己卡住。

**`no_blocked` 用 `*` 而不是 `design`。** design 阶段产出的任务图里任务的 `phase` 基本都是 `develop`，design 自己通常没有任务 —— 只看本阶段这条断言近乎恒真，门禁列表看着 4 条实际生效 3 条。改成看全部任务后，架构师留下的任何阻塞项都拦得住。

**develop 的 `artifacts` 是后补的。** 没有它，develop 门禁在未配 `gate_commands` 的项目里四条全 PASS（无漂移即通过、任务做完即通过、两条 `cmd:*` 未配置跳过）—— 阶段可以在零代码证据下推进。`verification.md` 记的是**编排者复核过**的校验命令与输出，给硬规则 4（「子 agent 说做完了不等于做完了」）一个落盘依据。

**写它的是编排者，不是 developer subagent。** 两个原因，缺一条这个安排就没必要：一是并行的两个开发角色共用这一份文件，各自 Write 会覆盖对方，而 shell 追加（`>> .workbench/...`）被守卫的 `.workbench` 兜底那条拦掉 —— subagent 没有安全的追加通道；二是这份文件的价值恰好在于它不是自我报告，subagent 声称跑过什么不构成证据，编排者自己跑一遍才构成。所以 developer agent 的定义只要求它把命令原文与完整输出报回来。

**`cmd:<键>` 未配置时判 PASS 并说明「未配置，跳过」。** 这是最容易失效的一条 —— 未配置就等于门禁不存在。因此在 `wb-flow` skill、`qa` agent 与 `CLAUDE.md` 三处都写了「项目一旦有测试就配上」。

命令值必须是**非空字符串**：

```python
if not isinstance(cmd, str) or not cmd.strip():
    return True, label, "未配置，跳过（...）"
```

`isinstance` 检查不是多余的。`config set` 会尝试 `json.loads` 值（为了支持 `role_scopes` 的数组和 `max_parallel` 的整数），所以 `config set gate_commands.test false` 会存成布尔 `False`。没有类型检查就会被静默当成「未配置」。

命令执行：`shell=True`，`cwd=项目根`，超时取 `config` 里的 `gate_timeout`（默认 1800 秒）。

**`shell=True` 意味着门禁命令是一条从不经过 Bash 守卫的 shell。** 它不撞 `PreToolUse` hook（不是工具调用）、不撞冻结清单（`config` 键不是契约）、不撞角色范围（subprocess 没有 `agent_type`）。谁能写 `gate_commands.*`，谁就有一段任意代码执行 —— 所以特权子命令层把 `config set` 收窄到**只有 qa 能设 `gate_commands.*`**，其余键任何角色都设不了（[permissions.md](permissions.md#wbpy-特权子命令只有-hook-拿得到调用者身份)）。

qa 也不能设任意值。`cmd_config` 写入前、`run_check` 执行前都会用 `catastrophic_command()` 筛一遍命令值（删根删家目录、force push、`DROP`/`TRUNCATE`、下载远端脚本直接进 shell、直写块设备、格式化文件系统、fork bomb 那一套，与 Bash 分支共用同一张表）。写入时筛一遍防新增，执行时再筛一遍防**存量** —— 这层加上之前配进 `state.json` 的值不在当时任何检查里。筛掉的是灾难性模式，不是任意代码执行本身：qa 配一条 `npm test` 就是一条 `npm test`，这是流程要它干的事；这层的上限是「catastrophic 模式进不了门禁」，不是「qa 只能配已知命令」。后者做不了 —— 门禁命令天然是任意的（每个项目的测试命令都不同），把白名单写死在 wb.py 里等于让门禁只对已知技术栈的项目存在。

**完整输出落盘 `.workbench/gate-<键>.log`，detail 只带最后 5 行加日志路径。** 之前只带最后一行，而测试框架的最后一行通常是汇总行（`2 failed, 8 passed in 3.2s`）—— 哪两个用例失败、为什么失败全部丢失，诊断只能手动重跑一遍刚跑完的命令。

**超时是一条 FAIL，不是崩溃。** `subprocess.TimeoutExpired` 被捕获转成 FAIL。不捕获的话 hook 路径有 `cmd_hook` 的兜底 try，但 CLI 路径没有 —— `gate check` 与 `phase advance` 会打出 Traceback，退出码恰好也是 1，自动化脚本看不出区别，人看到的是崩溃。

## 输出与退出码

```
$ python3 .claude/hooks/wb.py gate check
门禁 · develop（开发实现）
  [PASS] 契约无漂移 — 一致
  [FAIL] develop 任务全部完成 — 未完成：T1, T2, T4
  [PASS] 命令门禁 lint — 未配置，跳过（config set gate_commands.lint '<命令>'）
结论：未通过
$ echo $?
1
```

每条断言返回 `(通过, 标签, 说明)` 三元组。**说明必须可操作** —— `未完成：T1, T2, T4` 让主线程直接知道派谁去做；`缺少该章节` 让 subagent 知道补什么。只说 FAIL 不说原因的门禁会让流程停在原地。

`--json` 输出结构化结果供 skill 与 loop 解析。退出码约定：`0` 通过，`1` 未通过，能直接串进 shell 与 CI。

**`--phase X` 会真的执行那个阶段的 `cmd:*` 门禁。** 读起来像「查一下那个阶段的情况」，实际在 clarify 阶段跑 `gate check --phase develop` 会真的执行配置的 build 命令。`--phase` 的 help 文本里写明了这一点。

## 阶段推进

```bash
wb.py phase advance          # 跑当前阶段门禁，通过才推进
wb.py phase advance --force  # 门禁不通过也推进
wb.py phase set <阶段> --reason '<为什么>'   # 直接跳，不跑门禁（回退用；理由必填）
wb.py gate check --phase X   # 只校验不推进（会执行该阶段的 cmd:* 门禁）
```

推进时写入门禁记录，四个字段：`passed`（真实门禁结果）、`at`、`forced`（强推标记）、`failures`（遗留的 FAIL 项）。

`passed` 记的是**真实结果，强推不把它改写成 `true`** —— 否则 `status` 的阶段行（按 `passed` 打 `v`）会把硬推过去的阶段显示成「门禁已过」，而 `status` 是最常看的看板。强推的阶段在 `status` 里打 `!`：

```
阶段：!clarify  *analyze  -design  -develop  -verify  -retro   （* = 当前，v = 门禁已过，! = 强推）
```

`forced` 与 `failures` 会出现在 `wb.py report` 生成的交付报告里，也是复盘阶段 `reviewer` 的重点材料 —— **每一次强推都是一次流程摩擦，记录下来才能改进规则**。

**`phase set` 是回退通道，不是 `advance` 的快捷方式。** 它一条门禁都不跑，所以 `--reason` 必填（不给直接拒绝，与 `contract unlock` 同一条约定：理由必须在跳之前写）。向前跳时被跨过的每个阶段都补一条门禁记录 `{passed: false, forced: true, skipped_by_set: true}`，`failures` 里写明「门禁未运行：phase set 直接跳到 X（理由）」。没有这条记录时 `status` 只显示「当前阶段 develop」，被跳过的 clarify / analyze / design 既没有 gates 记录也没有 `forced` 标记 —— **「门禁不通过不推进」就有了一条不留痕的旁路**，而 `permissions.allow` 里的 `Bash(python3 .claude/hooks/wb.py:*)` 对所有角色开放。理由与跳跃方向也进 `phase_set` 日志。

门禁**真**通过时 `advance` 顺带把该阶段产物登记成契约并锁定，打一行提示：

```
已把 clarify 阶段产物冻结为契约 artifact-requirements：之后要改它先
`contract unlock --name artifact-requirements --reason '<为什么>'`，改完 `contract bump` 通知下游
```

强推与 develop 两处不冻结，理由见 [architecture.md](architecture.md#阶段产物即契约)。走到最后一个阶段时 `advance` 记 `flow_complete` 而不是越界，输出「已是最后阶段，全链路完成」—— `retro.md` 的冻结在这条早返回之前完成。

## 强推的边界

`--force` 是逃生舱，不是常规路径。约定（在 `wb-flow` skill 里）：

**用之前必须问用户。** 唯一不需要问的情况：FAIL 项本身不适用于当前改动 —— 例如纯文档改动没有跨角色契约、项目没有构建命令。

**跳过失败的测试不属于这类。** 测试红了强推 verify 门禁，等于把门禁这套机制作废。

这是提示词约定而非代码约束，要做成硬约束见 [architecture.md](architecture.md#强推无硬确认)。

## 扩展

**加一条准出条件**：往 `GATES[阶段]["checks"]` 加一行，用现有的八种断言之一（例如 `artifact_contains:design.md:回滚`）。

**加一种断言类型**：在 `run_check()` 里加一个 `if kind == "...":` 分支，返回 `(bool, 标签, 说明)`：

```python
if kind == "adr_exists":
    hits = list((root / "docs" / "adr").glob(f"*{rest}*.md"))
    return bool(hits), f"ADR {rest} 存在", str(hits[0].name) if hits else "未找到"
```

**加一个阶段**：改 `PHASES` 与 `PHASE_CN`，在 `GATES` 加条目，建 `artifacts/<新阶段>/`，写一个角色 agent，在 `wb-flow` 的阶段-角色表加一行。`PHASES` 的顺序决定推进顺序与 `next` 的排序权重。已初始化的项目改 `PHASES` 后老 `state.json` 的 `phases` 不会自动更新（`setdefault` 只补缺失字段），用 `config set phases '[...]'` 手动改。

**每次改完跑 `selfcheck`。** 门禁失效是**静默的** —— 规则写错不会报错，只会让门禁永远 PASS。自检里有「缺产物时门禁应失败」「产物齐全后门禁应通过」两条对偶断言专门抓这个，新增断言类型时给它补一对。

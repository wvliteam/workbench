# 门禁引擎

门禁（gate）是阶段准出条件。不满足时 `phase advance` 退出码 1，阶段推不动。这是让流程有约束力的第一个机制。

## 设计目标

一句话：**把「应该先做完 X 再做 Y」从提示词里的提醒变成退出码。**

提示词说「先写完需求文档再设计」，模型大部分时候遵守。会话被压缩、用户中途插话、subagent 报告过于乐观时，就不遵守了。门禁不受这些影响 —— 它只看落盘的事实。

## 规则表

全部规则集中在 `wb.py` 的 `GATES` 字典。加一条准出条件是往表里加一行字符串，不需要写代码。

```python
GATES = {
    "clarify": {
        "artifacts": ["requirements.md"],
        "checks": [
            "artifact_contains:requirements.md:验收标准",
            "artifact_contains:requirements.md:非目标",
        ],
    },
    "analyze": {
        "artifacts": ["current-state.md"],
        "checks": ["artifact_contains:current-state.md:风险"],
    },
    "design": {
        "artifacts": ["design.md"],
        "checks": [
            "artifact_contains:design.md:方案对比",
            "contracts_locked",
            "tasks_exist",
            "no_blocked:design",
        ],
    },
    "develop": {
        "checks": ["contracts_intact", "tasks_done:develop", "cmd:lint", "cmd:build"],
    },
    "verify": {
        "artifacts": ["test-report.md"],
        "checks": ["contracts_intact", "tasks_done:verify", "cmd:test"],
    },
    "retro": {
        "artifacts": ["retro.md"],
        "checks": ["artifact_contains:retro.md:改进项", "tasks_done:*"],
    },
}
```

两个键：

- `artifacts` —— 文件必须存在于 `.workbench/artifacts/<阶段>/` 且**非空**。空文件不算产出。
- `checks` —— 断言字符串，格式 `类型:参数[:参数]`，由 `run_check()` 分派。

## 八种断言

| 断言 | 语法 | 通过条件 | 用意 |
| --- | --- | --- | --- |
| 产物存在 | （`artifacts` 键） | 文件存在且 size > 0 | 阶段真的产出了东西 |
| 章节包含 | `artifact_contains:<文件>:<字符串>` | 文件内容含该子串 | 产物覆盖了关键思考，不是敷衍 |
| 契约已锁定 | `contracts_locked` | 契约列表非空且每份都有 `sha` | 并行开发的前提已就绪；也让 `design.md` 转为只读 |
| 契约无漂移 | `contracts_intact` | 每份契约当前哈希 == 锁定哈希 | 兜住守卫覆盖不到的改动路径（外部编辑器、`cp`、`git checkout`） |
| 已拆解任务 | `tasks_exist` | 任务数 > 0 | 设计阶段真的落到了可执行任务 |
| 任务完成 | `tasks_done:<阶段>` 或 `tasks_done:*` | 该范围内任务全部 `done` | 活干完了 |
| 无阻塞 | `no_blocked:<阶段>` | 该阶段无 `blocked` 任务 | 阻塞项被处理而非绕过 |
| 命令门禁 | `cmd:<键>` | `gate_commands[键]` 退出码 0 | 测试/构建/lint 真的通过 |

### 实现要点

**`artifact_contains` 是字符串包含，不解析 Markdown。** 粗糙但有效 —— 它挡的是「需求文档写了但没写验收标准」，不是格式问题。解析 Markdown 会引入依赖并对写法过于挑剔。

**`contracts_locked` 在契约列表为空时判 FAIL**，理由说明写「无接口的纯本地改动可 `--force` 跳过」。这是刻意的：默认假设有接口，让「确实没有接口」成为需要显式声明的例外，而不是反过来。走完 design 阶段后 `design.md` 本身就该是一份契约，所以这条实际上很少 FAIL。

**`contracts_intact` 与权限守卫不是重复。** 守卫在改之前拦（Write/Edit 与 Bash 写入路径），能给出可操作的拒绝理由；这条断言在门禁时抓（不管改动从哪来），兜住守卫覆盖不到的 `cp`、外部编辑器、`git checkout`、用户手改。两道都留着。

**`tasks_done:<阶段>` 在该阶段无任务时判 PASS**（说明「无任务（视为通过）」）。避免 develop 阶段没有前端任务时被自己卡住。

**`cmd:<键>` 未配置时判 PASS 并说明「未配置，跳过」。** 这是最容易失效的一条 —— 未配置就等于门禁不存在。因此在 `wb-flow` skill、`qa` agent 与 `CLAUDE.md` 三处都写了「项目一旦有测试就配上」。

命令值必须是**非空字符串**：
```python
if not isinstance(cmd, str) or not cmd.strip():
    return True, label, "未配置，跳过（...）"
```
`isinstance` 检查不是多余的。`config set` 会尝试 `json.loads` 值（为了支持 `role_scopes` 的数组和 `max_parallel` 的整数），所以 `config set gate_commands.test false` 会存成布尔 `False`。没有类型检查就会被静默当成「未配置」。

命令执行：`shell=True`，`cwd=项目根`，超时 1800 秒，只把最后一行输出带进结果（避免测试日志淹没门禁报告）。

## 输出与退出码

```
$ python3 .claude/hooks/wb.py gate check
门禁 · develop（开发实现）
  [PASS] 契约无漂移 — 一致
  [FAIL] develop 任务全部完成 — 未完成：T1, T2, T4
  [PASS] 命令门禁 lint — 未配置，跳过（config set gate_commands.lint '<命令>'）
  [PASS] 命令门禁 build — 未配置，跳过（config set gate_commands.build '<命令>'）
结论：未通过
$ echo $?
1
```

每条断言返回 `(通过, 标签, 说明)` 三元组。**说明必须可操作** —— `未完成：T1, T2, T4` 让主线程直接知道派谁去做；`缺少该章节` 让 subagent 知道补什么。只说 FAIL 不说原因的门禁会让流程停在原地。

`--json` 输出结构化结果，供 skill 与 loop 解析：

```json
{
  "phase": "develop",
  "passed": false,
  "checks": [{"ok": true, "label": "契约无漂移", "detail": "一致"}]
}
```

退出码约定：`0` 通过，`1` 未通过。这样能直接串进 shell 与 CI。

## 阶段推进

```
wb.py phase advance          # 跑当前阶段门禁，通过才推进
wb.py phase advance --force  # 门禁不通过也推进
wb.py phase set <阶段>       # 直接跳，不跑门禁（回退用）
wb.py gate check --phase X   # 只校验不推进，可查任意阶段
```

推进时写入门禁记录：

```jsonc
"gates": {
  "develop": {
    "passed": true,
    "at": "2026-08-31T19:20:00+0000",
    "forced": true,                      // 强推标记
    "failures": ["develop 任务全部完成"]  // 遗留的 FAIL 项
  }
}
```

`forced` 与 `failures` 会出现在 `wb.py report` 生成的交付报告里，也是复盘阶段 `reviewer` 的重点材料 —— **每一次强推都是一次流程摩擦，记录下来才能改进规则**。

走到最后一个阶段时 `advance` 记 `flow_complete` 而不是越界，输出「已是最后阶段，全链路完成」。

## 强推的边界

`--force` 是逃生舱，不是常规路径。约定（在 `wb-flow` skill 里）：

**用之前必须问用户。** 唯一不需要问的情况：FAIL 项本身不适用于当前改动 —— 例如纯文档改动没有跨角色契约、项目没有构建命令。

**跳过失败的测试不属于这类。** 测试红了强推 verify 门禁，等于把门禁这套机制作废。

这是提示词约定而非代码约束。要做成硬约束见 [architecture.md 的「强推无硬确认」](architecture.md#强推无硬确认)。

## 扩展

### 加一条阶段准出条件

往 `GATES[阶段]["checks"]` 加一行，用现有的八种断言之一。例如要求设计文档提到回滚策略：

```python
"design": {
    "artifacts": ["design.md"],
    "checks": [
        "artifact_contains:design.md:方案对比",
        "artifact_contains:design.md:回滚",   # 新增
        "contracts_locked", "tasks_exist", "no_blocked:design",
    ],
},
```

### 加一种新断言类型

在 `run_check()` 里加一个 `if kind == "...":` 分支，返回 `(bool, 标签, 说明)`。例如「必须有对应的 ADR 文件」：

```python
if kind == "adr_exists":
    hits = list((root / "docs" / "adr").glob(f"*{rest}*.md"))
    return bool(hits), f"ADR {rest} 存在", str(hits[0].name) if hits else "未找到"
```

### 加一个阶段

改 `PHASES` 列表与 `PHASE_CN` 映射，在 `GATES` 里加对应条目，建 `artifacts/<新阶段>/` 目录，写一个角色 agent。注意 `PHASES` 的顺序决定推进顺序与 `next` 的排序权重。

已初始化的项目改 `PHASES` 后，老 `state.json` 的 `phases` 字段不会自动更新（`setdefault` 只补缺失字段）。用 `wb.py config set phases '["...", ...]'` 手动更新。

### 每次改完

```
python3 .claude/hooks/wb.py selfcheck
```

门禁失效是**静默的** —— 规则写错不会报错，只会让门禁永远 PASS。自检里有「缺产物时门禁应失败」「产物齐全后门禁应通过」两条对偶断言，专门抓这个。新增断言类型时给它补一对。

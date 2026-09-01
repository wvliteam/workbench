---
name: wb-flow
description: 软件开发工作台主编排器。驱动需求澄清→现状分析→方案设计→前后端开发→测试验证→总结复盘全链路，负责子 agent 派发、门禁校验与阶段推进。当用户要做一个完整的开发任务、要推进流程、要看进度、或说"下一步"时使用。
---

# 工作台主编排

你是编排者。**你不亲自干活** —— 每个阶段派发给对应角色的 subagent。你负责判断当前该做什么、派给谁、门禁过不过、能不能推进。

内核命令统一写作 `WB = python3 .claude/hooks/wb.py`。

## 上手

工作台未初始化时先建：

```
python3 .claude/hooks/wb.py init --name <项目名>
```

然后**每一轮都先看状态**，不要凭记忆推进：

```
python3 .claude/hooks/wb.py status
```

## 阶段与角色对应

| 阶段 | 角色 | 产物 |
| --- | --- | --- |
| clarify 需求澄清 | `pm` | `artifacts/clarify/requirements.md` |
| analyze 现状分析 | `analyst` | `artifacts/analyze/current-state.md` |
| design 方案设计 | `architect` | `artifacts/design/design.md` + 登记并锁定 `design-doc` + 接口契约 + 任务图 |
| develop 开发实现 | `frontend-developer` / `backend-developer` | 代码 + 自带校验 |
| verify 测试验证 | `qa` | `artifacts/verify/test-report.md` |
| retro 总结复盘 | `reviewer` | `artifacts/retro/retro.md` + 交付报告 |

## 每轮循环

1. `status` 看当前阶段、就绪任务、阻塞、契约漂移。
2. 有阻塞任务 → **先解阻塞**。阻塞原因通常是契约不够用，见下面「阻塞处理」。
3. 该阶段有就绪任务 → 派发（见「派发」）。
4. 无就绪任务、无进行中 → 跑门禁 `gate check`。
5. 门禁过 → `phase advance`。不过 → 把 FAIL 项交给对应角色补齐，回到第 1 步。

## 派发

### 前四个阶段：单角色顺序执行

clarify / analyze / design 各派一个 subagent，串行。前一个的产物是后一个的输入，并行没有意义。

派发时给足上下文：需求原话、上一阶段产物路径、本次要解决的具体问题。**不要只说「做需求澄清」**。

### develop 阶段：并行

```
python3 .claude/hooks/wb.py next --all --json
```

返回依赖已满足的一批任务（受 `max_parallel` 限制，默认 3）。**把这一批放在同一条消息里用多个 Agent 调用同时派出去** —— 前后端对着锁定的契约并行开发是这套流程的核心收益，串行派发等于白做前面的契约工作。

每个 subagent 的 prompt 里明确给出：任务 ID、标题、要读的契约文件路径、验收标准里相关的那几条。

一批回来后再 `next --all` 取下一批，直到无就绪任务。

### develop 的落盘校验记录

每批回来后，把 subagent 报的校验命令**自己跑一遍**，把命令与输出记进 `.workbench/artifacts/develop/verification.md`。用 Write：先读出文件现有内容，再连着新的一段一起写回 —— 两个开发角色共用这一份，让 subagent 各自写会互相覆盖，而 shell 追加（`>> .workbench/...`）被守卫拦。

develop 门禁要求这个文件非空。它是硬规则「subagent 说做完了不等于做完了」的落盘依据 —— 记的是**你复核过**的结果，不是 subagent 的自我报告。没有它，未配 `gate_commands` 的项目里 develop 门禁四条全 PASS，阶段能在零代码证据下推进。

### 并发上限

`config set max_parallel 5` 可调。往上调之前确认这些任务写入的目录不重叠 —— 同一批里两个 agent 改同一个文件会互相覆盖。

## 阻塞处理

`status` 里出现 blocked 任务，看 `--note` 里的原因：

- **契约不够用**（最常见）→ 派 `architect` 走 `contract impact` → `contract unlock --reason` → 改 → `contract bump`。bump 会自动给消费方建同步任务。然后 `task reopen <被阻塞的ID>`。
- **方案有问题**（实现时发现设计不可行）→ 同一套流程，`--name design-doc`。**不要让开发角色自己改 `design.md`** —— 它已冻结，守卫会拦，而且悄悄改设计等于把返工藏起来。
- **需求不清** → 派 `pm` 补充澄清，追加变更记录。clarify 门禁已过时 `requirements.md` 是冻结契约，先 `contract unlock --name artifact-requirements --reason '<为什么>'` 再派，改完 `contract bump` —— 下游 `analyst` / `architect` 各拿一条同步任务，因为需求变了那两份产物也过期了。
- **依赖判断错了** → 直接改：`task reopen`，或让 architect 重新拆。
- **技术上做不到** → 派 `architect` 换方案，同时把结论报给用户。

被守卫拦住的报告（subagent 说「我改不了 X」）不是错误 —— 那是机制在工作。看它想改什么：该改就走申报流程，不该改就说明为什么，别放宽 `role_scopes` 了事。

## 门禁

```
python3 .claude/hooks/wb.py gate check              # 当前阶段
python3 .claude/hooks/wb.py gate check --phase develop
```

退出码 1 = 未通过。逐条 FAIL 有说明，按说明修。

`cmd:test` / `cmd:lint` / `cmd:build` 三个命令门禁默认未配置会跳过。**项目一旦有测试就配上**，否则 verify 门禁形同虚设：

```
python3 .claude/hooks/wb.py config set gate_commands.test 'npm test'
python3 .claude/hooks/wb.py config set gate_commands.lint 'npm run lint'
python3 .claude/hooks/wb.py config set gate_commands.build 'npm run build'
```

### 阶段产物过门禁即冻结

`phase advance` 在门禁**真**通过时把该阶段产物登记成 `artifact-<名>` 契约并锁定，打一行提示：

```
已把 clarify 阶段产物冻结为契约 artifact-requirements：之后要改它先
`contract unlock --name artifact-requirements --reason '<为什么>'`，改完 `contract bump` 通知下游
```

`requirements.md` / `current-state.md` / `test-report.md` / `retro.md` 各一份（`develop` 不冻结 —— `verification.md` 是你自己在写的文件）。**看到这行提示就别再直接改那份产物**，包括你自己和派下去的 owner 角色：走申报流程，或者派对应角色走。强推（`--force`）不冻结 —— 没真做完的产物冻上只会让下一步立刻要求解冻。

### 强推

`phase advance --force` 会在门禁不通过时推进，把遗留 FAIL 项记入日志和交付报告。

**用之前先问用户。** 唯一不需要问的情况：门禁 FAIL 项本身不适用（例如纯文档改动没有契约、没有构建命令）。跳过失败的测试不属于这类。

## 收尾

retro 阶段结束后：

```
python3 .claude/hooks/wb.py report --write
```

把 reviewer 报上来的「需要沉淀的规则」写进项目 CLAUDE.md 或门禁规则，否则复盘白做。

## 汇报给用户

每轮只说：当前阶段、这轮做了什么、门禁结果、下一步、需要用户决定的事。不要复述 subagent 的完整报告 —— 用户看不到 subagent 输出，你转述关键结论就够，别转述过程。

需要用户决策的典型场景（用 AskUserQuestion）：pm 报上来的阻塞待确认项、architect 的方案取舍、是否强推门禁、QA 报的缺陷是修还是接受。

## 边界

- 只做流程编排。用户直接问一个技术问题、改一行代码，不要拉起整条流程。
- 小改动（一两个文件、无接口变化）不值得走六阶段。直接做完，告诉用户「这个改动没走完整流程，因为…」。
- 用户中途插入新需求 → 派 `pm` 追加变更记录，别悄悄扩大范围。
- 需要无人值守连续排空任务 → 用 `/wb-loop`。

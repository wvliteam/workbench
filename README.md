# 软件开发工作台

基于 Claude Code 的软件开发流程工作台。把「需求澄清 → 现状分析 → 方案设计 → 前后端开发 → 测试验证 → 总结复盘」做成有状态、有门禁、有契约约束的流水线，而不是靠提示词提醒模型「记得先设计」。

## 结构

```
.claude/
├── settings.json           权限规则 + 4 个 hook 注册
├── hooks/wb.py             状态内核：状态机 / 门禁 / 契约 / 调度 / 权限守卫（含自检）
├── agents/                 7 个角色 subagent
│   ├── pm.md                   需求澄清
│   ├── analyst.md              现状分析（只读）
│   ├── architect.md            方案设计 + 契约定义 + 任务拆解
│   ├── frontend-developer.md
│   ├── backend-developer.md
│   ├── qa.md                   测试验证
│   └── reviewer.md             代码评审 + 复盘
└── skills/
    ├── wb-flow/                主编排：全链路推进
    ├── wb-loop/                自动排空循环
    └── wb-contract/            契约生命周期

.workbench/                 全部状态，纯 JSON，可 git diff
├── state.json                  阶段 / 任务 / 门禁记录 / 契约 / 审计日志
├── contracts/                  接口定义文件
├── artifacts/<阶段>/           各阶段产物（按阶段隔离写入权限）
├── role                        当前角色锁（守卫读它收紧写入范围）
├── current_task                当前任务（改动文件自动挂到它上面）
├── frozen                      冻结路径清单（守卫读它拒绝直接写）
└── unlock                      解冻申报窗口（契约名 + 理由）
```

## 上手

```bash
python3 .claude/hooks/wb.py selfcheck                      # 确认内核正常
python3 .claude/hooks/wb.py init --name my-project
```

然后在 Claude Code 里说要做什么，或直接 `/wb-flow`。

### 把代码库 clone 进这个工作区

不要把 `.claude/` 拷到每个项目里。反过来：代码库 clone 进来，共享同一套工作台。两种布局按需求是否跨仓库选。

**一个需求只改一个仓库**（默认）—— 每个仓库自带一份状态：

```bash
git clone <url> repos/foo
cd repos/foo
python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/wb.py" init --name foo
echo '.workbench/' >> .git/info/exclude    # 不改仓库自己的 .gitignore
```

之后在 `repos/foo` 里正常用全部命令 —— `wb.py` 向上查找最近的 `.workbench/`，hook 用绝对路径注册，都不受 cwd 影响。仓库之间天然隔离，角色范围里的 `server/**`、`web/**` 相对各仓库根，不用改。

**一个需求跨多个仓库** —— 只在外层 init，各仓库都不 init。项目根 = 整个工作区，一份契约一条流水线，前后端对着同一份锁定契约并行开发。需要调两处：

```bash
python3 .claude/hooks/wb.py init --name <需求名>      # 只在外层
python3 .claude/hooks/wb.py config set role_scopes.backend-developer \
  '["repos/backend/**",".workbench/artifacts/develop/**"]'    # 按仓库前缀，不是按目录名
python3 .claude/hooks/wb.py config set gate_commands.test \
  '(cd repos/frontend && npm test) && (cd repos/backend && pytest)'   # 子 shell 分别 cd
```

不改 `role_scopes` 会歪成按语言隔离（`*.py` 跨仓库放行，而 `migrations/**` 匹配不到嵌套路径），细节见 [architecture.md](docs/architecture.md#跨仓库开发同一个语义的反面)。

`status` 与会话开头都会打一行根路径。**忘了 `cd` 进仓库就跑命令会操作到外层工作台自己的状态，且不报错** —— 看那一行。

同一仓库的下一个需求：`report --write` 归档后 `init --force` 重开。要并行两个需求，`git worktree add ../foo-b` 再在新工作树里 `init`。

## 六个能力

### 阶段门禁

每个阶段有准出条件，不满足就推不动。规则在 `wb.py` 的 `GATES` 表里，一处修改。

```bash
python3 .claude/hooks/wb.py gate check          # 退出码 1 = 未通过，逐条给原因
python3 .claude/hooks/wb.py phase advance       # 门禁通过才推进
python3 .claude/hooks/wb.py phase advance --force   # 强推，遗留项记入日志与交付报告
```

门禁类型：产物文件存在且非空、产物含指定章节、契约已锁定、契约无漂移、任务已拆解、指定阶段任务全部完成、无阻塞任务、外部命令退出码为 0。

### 契约管理

前后端并行开发的前提。**锁定后内容哈希被冻结，文件同时变成只读** —— 连 owner 和主线程都不能直接写，改它必须先申报理由。

```bash
python3 .claude/hooks/wb.py contract add .workbench/contracts/user-api.yaml \
    --name user-api --owner backend-developer --consumers frontend-developer
python3 .claude/hooks/wb.py contract lock --all
python3 .claude/hooks/wb.py contract verify                  # 退出码 1 = 有漂移
python3 .claude/hooks/wb.py contract impact --name user-api  # 消费方 + 关联任务 + 代码引用
python3 .claude/hooks/wb.py contract unlock --name user-api --reason "响应加 email 字段"
# 改文件
python3 .claude/hooks/wb.py contract bump --name user-api
```

`bump` 会自动给每个消费方角色创建同步任务并记入变更历史 —— 这是契约有约束力而不只是文档的原因。

**技术方案文档走同一套机制。** `architect` 写完 `design.md` 后把它登记成 `design-doc` 契约，消费方是三个开发/测试角色。之后改设计要申报理由、bump 后三方各拿一条同步任务。零新代码 —— 方案文档和接口定义要的是同一件事：多方对着同一个不动的基准干活，改动要通知到人。

### 任务与进度

```bash
python3 .claude/hooks/wb.py task add --title "用户列表接口" \
    --role backend-developer --phase develop --contracts user-api
python3 .claude/hooks/wb.py task add --title "用户列表页" \
    --role frontend-developer --phase develop --deps T1
python3 .claude/hooks/wb.py task start T1
python3 .claude/hooks/wb.py task done T1 --note "接口 + 契约测试已通"
python3 .claude/hooks/wb.py task block T2 --reason "契约缺 total 字段"
python3 .claude/hooks/wb.py status
```

依赖未完成时 `task start` 会拒绝。改动过的文件由 `PostToolUse` hook 自动挂到进行中的任务上。

### 子 agent 调度

```bash
python3 .claude/hooks/wb.py next --all --json     # 依赖已满足的一批，用于并行派发
python3 .claude/hooks/wb.py config set max_parallel 5
```

`next` 做的是依赖图上的就绪计算：状态为 todo 且所有前置任务已完成。编排者把一批放在同一条消息里多个 Agent 调用并行派出去。

### 权限控制

`PreToolUse` hook 拦四类，退出码 2 阻止调用并把原因回灌给模型：

1. 写出项目根之外
2. 写冻结文件 —— `state.json` / `role` / `frozen` / `unlock` / 所有已锁定的契约与 `design.md`
3. 角色越权写 —— `pm` 写代码、前端写 `migrations/`、`qa` 改 `requirements.md`（产物目录按阶段隔离）
4. 危险命令（`rm -rf /`、force push、`DROP TABLE`、`curl | sh`、`mkfs`、`dd of=/dev/`、fork bomb）+ 提示级警告（`git reset --hard`、`git clean -fd`、`npm publish`）

第 2 条**同时覆盖 Bash 路径**：`>` `>>` `tee` `sed -i` `perl -i` `truncate` `patch` `dd` `python3 -c` `node -e` `ln -sf` 提到冻结路径时一并拒绝。只做 Write/Edit 检查等于没做 —— 一行 shell 就能绕过全部。

```bash
python3 .claude/hooks/wb.py role scopes         # 范围 + 冻结清单 + 解冻窗口
python3 .claude/hooks/wb.py role scopes --reset # 老项目刷成当前默认值
python3 .claude/hooks/wb.py config set role_scopes.qa '["tests/**","e2e/**",".workbench/artifacts/verify/**"]'
```

### Loop 执行

`/wb-loop` 连续取就绪任务、并行派发、清空后跑门禁并推进阶段。最多 12 轮，撞到必须人工决策的事就停：契约要改、需求不清、门禁修不动、同一任务失败两次、blocker 缺陷、契约漂移。

按固定时间间隔轮询（等 CI 之类）用内置的 `/loop`，那是另一回事。

## 复盘

```bash
python3 .claude/hooks/wb.py report --write   # -> artifacts/retro/delivery-report.md
python3 .claude/hooks/wb.py log --tail 200
```

日志里的 `forced=true`、`contract_bump`、`task_reopen`、`task_block` 每一条都是一次流程摩擦，是复盘的主要材料。

## 适配到自己的项目

1. 把 `.claude/` 和 `.workbench/` 拷进项目根。
2. `wb.py init --name <项目名>`。
3. 配门禁命令：`config set gate_commands.test '<你的测试命令>'`（lint / build 同理）。
4. 按实际目录布局调角色范围：`config set role_scopes.<角色> '<JSON 数组>'`。别把产物目录放宽回 `.workbench/artifacts/**` —— 那会撤掉阶段隔离。
5. 阶段准出条件要改就动 `wb.py` 里的 `GATES` 表，改完跑 `selfcheck`。

升级已有项目的 `wb.py` 之后跑一次 `wb.py role scopes --reset`（先 `role scopes` 存一份定制值）—— 新增的默认值不会自动覆盖老 `state.json` 里的旧值。

## 设计与实现细节

`docs/` 下有完整的设计文档：

| 文档 | 内容 |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | 分层、状态模型、`state.json` schema、数据流、设计取舍、已知边界与升级路径 |
| [docs/roles.md](docs/roles.md) | 角色职责矩阵、产物与门禁的耦合、协作协议、定制角色 |
| [docs/gates.md](docs/gates.md) | 门禁引擎：八种断言的语义与实现、强推机制、扩展方式 |
| [docs/contracts.md](docs/contracts.md) | 契约机制：哈希冻结 + 只读守卫、申报窗口、`bump` 的影响面传播、方案文档复用、失效模式 |
| [docs/permissions.md](docs/permissions.md) | 四层拦截、Bash 绕过检查、角色范围匹配、危险命令分级、hook 载荷与失败语义 |
| [docs/scheduling.md](docs/scheduling.md) | 就绪集合算法、并行派发协议、任务生命周期、loop 停止条件 |

## 设计取舍

- **状态用一个 JSON 文件，不用数据库。** 能 `git diff`、能 `jq`、能人工修，零依赖。
- **内核是一个 Python 文件。** 状态机、门禁、契约、权限守卫共享同一份状态，拆成多个脚本只会带来同步 bug。
- **门禁规则是一张表。** 加一个阶段准出条件是往 `GATES` 里加一行，不是加一个类。
- **角色写入范围由 hook 强制，不靠提示词。** 提示词里写「你不要改代码」模型会遵守大部分时候；hook 是每次都遵守。
- **方案文档复用契约机制，不另写一套。** 「文档不能被随意改」和「接口不能被随意改」是同一个需求。多一套机制就多一套要维护、要测、会漂移的东西。
- **冻结没有豁免角色。** owner 与其他人的区别只在有权申报，不在能跳过申报。能豁免的机制等于没有机制。

## 已知边界

- 角色锁与解冻窗口都是单个文件。并行 subagent 会互相覆盖 —— hook 拿不到「当前是哪个 subagent」，这是上游限制。并行时守卫只按最后一次 `role set` 生效，实际靠 subagent 自身遵守范围。要真隔离得等上游在 hook 载荷里暴露 subagent 身份。
- 角色范围用 `fnmatch`，`*` 会跨 `/`，偏宽松。要严格匹配换成 `pathlib.PurePath.full_match`（Python 3.13+）或 `globmatch`。
- Bash 冻结检查不覆盖 `cp` / `mv` / 编译输出 / 外部编辑器 / `git checkout` —— 把它们加进去会拦掉大量正常操作。这类改动靠 `contract verify` 的哈希校验在门禁时抓出。**守卫防的是模型主动绕过，哈希校验兜住剩下的一切。**
- Bash 冻结检查同时按 basename 匹配（为了拦 `cd 某目录 && sed -i 文件名`），项目里有同名文件时会误拦。方向刻意选保守：误拦是显式的，漏拦是静默的。
- 契约只校验内容哈希，不校验语法。语法校验挂到 `gate_commands.lint`。
- `--force` 强推没有二次确认，靠 skill 里「先问用户」的约定。要硬约束就在 `cmd_phase` 里加环境变量开关。

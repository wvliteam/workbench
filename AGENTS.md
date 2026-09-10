# 软件开发工作台

这个仓库是一套供多种 coding agent 共用的软件开发流程工作台：六阶段流水线、八个角色 subagent、契约管理、门禁校验、任务调度与权限守卫。

**本文件（`AGENTS.md`）是协作约定的唯一正文，其他入口文件名（如 `CLAUDE.md`）以软链指向它** —— 各端 agent 按自己的约定文件名读取项目指导，接入新端时加一条软链即可，不复制正文。改协作约定只改本文件。

## 核心命令

```
python3 .claude/hooks/wb.py status          # 先看这个，每轮都看
python3 .claude/hooks/wb.py --help          # 全部子命令
python3 .claude/hooks/wb.py selfcheck       # 改过 wb.py 后必须跑
```

## 入口

- 主编排：`/wb-flow`；自动排空：`/wb-loop`；契约操作：`/wb-contract`；多仓库初始化：`/wb-init`；知识沉淀/查找：`/wb-knowledge`
- skills 按端各放一份（当前 `.claude/skills/` 与 `.agents/skills/`，手工同步，见文末「多端适配」）
- 状态内核：`.claude/hooks/wb.py` 是唯一入口，实现按层次拆在同目录（`wb_const` / `wb_bash` / `wb_core` / `wb_guard` / `wb_cli` / `wb_selfcheck`，分发时整目录拷贝，缺文件入口会明确报错），全端共用同一份，`--format` 适配各端 hook 载荷与输出协议；各端 hook 注册表引用自己的入口 —— Claude 端直接引用，Codex 端经 `.codex/hooks/wb.py` 软链落到同一入口文件，`resolve()` 会解析回真实路径，守卫不受影响

## 何时走流程，何时不走

**流程是默认路径，不走是例外。** 用户的请求像「实现一个功能、加一个接口、做一个页面」时，直接 `/wb-flow`——哪怕你觉得自己动手更快。判断模糊时走流程：它就是把模糊需求变成可验收需求再动手的机制，自己动手是把判断跳过去。

**走（默认）**：新功能、跨前后端的改动、有接口变化、需求本身不清楚、要动多个文件。用 `/wb-flow`。

**不走（例外，才需要理由）**：改一两个文件、修个明显的 bug、回答问题、纯文档。直接做，做完说一句「这个改动没走完整流程，因为改动面小/无接口变化」。

把六阶段套在一行改动上是纯开销。小改动不必套完整六阶段，但仍须遵守冻结文件和危险命令守卫。

## 多仓库工作区

代码库直接 clone 到本工作区里,不要把 `.claude/` 拷到别处。**工作台只用一种布局:外层根一份状态,各仓库里都不要 init** —— 项目根 = 整个工作区,一份 state、一份契约、一条流水线,前后端对着同一份锁定契约并行开发,天然支持跨仓库需求。

```
/home/work/workbench/
├── .claude/            # 工作台本体,唯一一份
├── .workbench/         # 唯一的状态、契约、流水线（init 就 init 在这里）
├── repos/              # 各代码库,纯代码目录,里面不 init
│   ├── foo/
│   └── bar/
└── scripts/            # repos_apply.py / repos_tui.py 等公共脚本
```

clone 与 IDE 配置按清单自动化：工作区根放一份 `repos.json`（`{"repos":[{"name":"foo","remote":"git@…"}]}`），跑 `python3 scripts/repos_apply.py --root .` —— 按 清单 clone/软链到 `repos/`、生成 `.workbench/<工作区名>.code-workspace` 多根工作区与 `.vscode/settings.json` 的 git 发现配置（幂等，已存在的 checkout 不覆盖，clone 失败显式报错）。交互式编辑清单用 `python3 scripts/repos_tui.py`。清单格式与幂等边界见 `.claude/skills/wb-init/SKILL.md`。

在外层跑一次工作台 init（每个工作区一次，不是每个需求一次）：

```bash
python3 .claude/hooks/wb.py init --name <需求名>   # 只在外层
```

`init` 之后必须调两处,否则会静默出错:

**1. 角色范围按仓库前缀，不是按目录名。** `init` 看到 `repos/*` 会自己换成按仓库前缀，并在输出里说明 —— 但它只能按目录名猜（`frontend` / `web` / `client` / `ui` / `www` 归前端，`backend` / `server` / `api` / `service` / `svc` 归后端）。

**猜不出名字的仓库谁都写不了。** 只要有一个仓库被认领，认不出的那些（`shared`、`payments-core`）就落在所有角色范围之外 —— 是硬拦，不是跨仓库放行。`init` 与 `role scopes` 会点名，照它给的命令认领：

```bash
python3 .claude/hooks/wb.py config set role_scopes.frontend-developer \
  '["repos/frontend/**",".workbench/artifacts/*/develop/tasks/**"]'
python3 .claude/hooks/wb.py config set role_scopes.backend-developer \
  '["repos/backend/**","repos/shared/**",".workbench/artifacts/*/develop/tasks/**"]'
```

`config set` 是**整条覆盖不是追加** —— 漏抄一个前缀，那个仓库就换成没人认领，`role scopes` 下一次会点它的名。

只有**一个仓库都认不出**时（全叫 `foo` / `bar`）才退回「任意仓库的对应位置」，那时才是跨仓库放行。

未经检测的默认范围在跨仓库下会歪成**按语言隔离**：`fnmatch` 的 `*` 跨 `/`，所以 `*.py` 会放行任意仓库里的 `.py`，而 `migrations/**` 匹配不到 `repos/backend/migrations/` —— 后端在自己仓库里写不了迁移，却能写别人仓库的同语言文件。跨仓库时仓库本身就是边界，按前缀写最准。

**2. 门禁命令用子 shell 分别 cd。** `gate_commands` 的 cwd 是外层根,那里没有 `package.json`。

```bash
python3 .claude/hooks/wb.py config set gate_commands.test \
  '(cd repos/frontend && npm test) && (cd repos/backend && pytest)'
```

子 shell 括号让 `cd` 不外溢。`&&` 串联时先失败的那个决定退出码，哪个仓库红了看 `.workbench/flows/<flow>/gate-test.log` 的完整输出。

契约放哪里两种都行:放 `repos/backend/openapi.yaml` 会进那个仓库的 git(适合契约由该服务负责发布);放外层 `.workbench/contracts/` 则不进任何仓库(适合契约独立于双方)。两种都受同一套冻结保护 —— 实测跨仓库路径的契约,前端、后端 owner、主线程的 Write/Edit/`sed -i`/先 `cd` 再改全部拦住。

### 多条需求并行：flow

一份 `.workbench/` 可以同时跑多条流水线，每条一个 flow（需求线）：state、锁、门禁记录、产物目录都按 flow 隔离在 `.workbench/flows/<flow>/` 与 `.workbench/artifacts/<flow>/`。

```bash
python3 .claude/hooks/wb.py flow new feature-b   # 开一条新流水线并切换过去
python3 .claude/hooks/wb.py flow list           # 全部 flow 与各自阶段
python3 .claude/hooks/wb.py flow switch main   # 切回
python3 .claude/hooks/wb.py flow remove feature-b --force   # 删整条（先切走）
```

- `init --flow <名>` 可以直接初始化指定 flow；默认 `main`。
- **新 flow 从 main 继承工作区级配置**（`role_scopes` / `gate_commands` / `gate_timeout` / `max_parallel`）：这些描述的是「这个工作区怎么干活」，不继承的话每条 flow 都要重抄一遍，漏抄的仓库认领会让指针切换后的角色范围判定整个换掉。任务、契约、阶段不继承 —— 那是每条需求线自己的进度。
- CLI 命令按 `.workbench/current-flow` 指针定位；`status` 的根行会显示当前 flow。指针是全部会话共享的一份文件：**两个终端并行推两条 flow 时，CLI 各自 `export WB_FLOW=<名>` 钉死**（只影响 wb.py 命令，hook 与守卫不受它影响）。不钉的话，状态命令会被对方切走的指针带到别的流水线上 —— 并发编排多条 flow 没有别的机制保护，要么各自钉 WB_FLOW，要么串行交错。
- **守卫不看指针，看全部 flow 的并集**：A flow 锁定的契约在 B flow 视角下照样冻结；A flow 的契约争议会让所有 flow 的 developer 一起停工（争议本来就是全线停工信号）。
- 解冻窗口按 flow 生命周期隔离：SubagentStop 与 `contract lock` / `bump` 只关**本 flow** 的窗口，别的 flow 正在使用的窗口不会被顺带拆掉。同一契约名全工作区同时只允许一个窗口（`unlock` 聚合查重），A flow 开窗期间 B flow 对同名契约的 `unlock` 会被拒 —— 共享同一份契约文件的两条 flow，变更本来就要排队。
- 产物归属（`task-agents.jsonl` / `artifacts.jsonl`）带 flow 字段：任务 ID 每条 flow 独立从 T1 编起，归属按任务所在 flow 过滤，跨 flow 同名任务不会互相认领对方的 agent 与产物。
- 角色范围模式带 flow 通配（`.workbench/artifacts/*/clarify/**`），不用为每条需求改配置。
- flow new/switch/remove 是编排者的调度决定，角色 subagent 跑不了（守卫特权层拦截）。
- 同一仓库要并行第二个需求、又要代码也物理隔离时，仍可叠加 `git worktree`；只隔离状态时用 flow 就够。

### 注意

**每轮 `status` 先看「根」那一行。** 它是当前操作的状态归属。忘了看会在不知不觉间操作到别的仓库的状态 —— 不报错，只能靠看。

同一个仓库要走第二个需求:并行用 `flow new`(状态、门禁记录、产物按 flow 隔离);需要代码也物理隔离时,用 `git worktree add ../foo-featureB`,新 worktree 也归外层状态管。串行接续则先 `report --write` 归档,再 `init --force` 重开。

## 六阶段与角色

`clarify` → `analyze` → `design` → `develop` → `verify` → `retro`

| 阶段 | 角色 subagent | 必须产出 |
| --- | --- | --- |
| clarify | `pm` | `.workbench/artifacts/<flow>/clarify/requirements.md`（含「验收标准」「非目标」） |
| analyze | `analyst` | `<flow>/analyze/current-state.md`（含「风险」） |
| design | `architect` | `<flow>/design/design.md`（含「方案对比」）+ 登记并锁定 `design-doc` 契约 + 接口契约 + 任务图 |
| develop | `frontend-developer` `backend-developer` | 代码 + `<flow>/develop/verification.md`（编排者复核每个任务的校验命令与输出后写入，不是 subagent 自己写） |
| verify | `qa` | `<flow>/verify/test-report.md` |
| retro | `reviewer` `knowledger` | `<flow>/retro/retro.md`（含「改进项」「沉淀」）+ `knowledge/<类别>/` 沉淀条目（retro 门禁查 `knowledge_written`：递归数条目，或 retro.md 显式「无可沉淀」） |

编排者不亲自干活，派 subagent。派发时给足上下文：需求原话、上游产物路径、要读的契约文件、相关的验收标准条目。

## 硬规则

1. **状态只能经 wb.py 改。** 直接写 `.workbench/state.json`、`role`、`frozen`、`unlock` 会被守卫拦，Write/Edit 与 shell 重定向、`sed -i` 都拦。门禁与进度必须不可绕过，否则记录没有意义。
2. **契约、方案文档与过了门禁的阶段产物锁定后就是只读的。** 包括对 owner 和主线程。要改先申报：`contract unlock --name <名> --reason '<为什么>'`，改完 `contract bump`。理由必须在改之前写 —— 事后补的理由都是给已发生的事找解释。`design.md` 由 architect 登记为 `design-doc`；`requirements.md` / `current-state.md` / `test-report.md` / `retro.md` 由 `phase advance` 在门禁**真**通过时自动登记为 `artifact-<名>`（强推不冻结）。回头改上游需求走 `--name artifact-requirements`，改文件那一步派 `pm`。
3. **门禁不通过不推进。** 需要 `phase advance --force` 时先问用户。唯一例外：FAIL 项本身不适用（纯文档改动没有构建命令）。跳过失败的测试不算例外。`phase set` 只用于回退，`--reason` 必填，向前跳会给被跨过的阶段留下「门禁未运行」的记录。
4. **子 agent 说做完了不等于做完了。** 至少确认它声称改的文件存在、它声称跑过的命令你也跑一遍，再 `task done`。异常中断的 agent 会在 `.workbench/artifacts/<flow>/develop/tasks/<任务号>-<角色名>.md` 留执行记录（已完成/已改/阻塞/下一步），接续时先读它，再决定重派还是续做。
5. **develop 阶段并行派发。** `next --all --json` 拿整批就绪任务，放在同一条消息里多个 Agent 调用同时发出。串行派发会浪费掉契约先行带来的全部收益。每个 agent 先 `task start`、完成后 `task done` —— 产物归属合并与解冻窗口清理都挂在 `task done` 上。
6. **不可简化的东西**：信任边界上的输入校验、防数据丢失的错误处理、安全措施、可访问性基础、用户明确要求的功能。其余按最小可用实现。
7. **clarify 与 design 推进前问用户。** 门禁管「产物齐不齐」，管不了「用户认不认」：需求偏差在这里拦最便宜（产物一过门禁就冻结成契约，改它要走 unlock → bump → 下游返工），方案取舍选错的返工由全部开发阶段承担。用 `AskUserQuestion`，确认完 `wb.py log` 一条留痕；用户批量授权后续时按授权推进并在汇报里说明。

## 门禁命令

`cmd:test` / `cmd:lint` / `cmd:build` 默认未配置会跳过。项目一旦有测试就配上，否则 verify 门禁形同虚设：

```
python3 .claude/hooks/wb.py config set gate_commands.test 'npm test'
python3 .claude/hooks/wb.py config set gate_commands.lint 'npm run lint'
python3 .claude/hooks/wb.py config set gate_commands.build 'npm run build'
```

失败时完整输出落在 `gate-<名>.log`（main flow 在 `.workbench/`，其余在 `.workbench/flows/<flow>/`），门禁说明里只带最后 5 行 —— 别为了看失败原因把命令再跑一遍。单条命令超过 `gate_timeout`（默认 1800 秒）记 FAIL，不是崩溃。

## 权限守卫

`PreToolUse` hook 拦以下几类：

- 写出项目根之外
- 写冻结文件（`state.json` / `role` / `frozen` / `unlock` / `artifacts.jsonl` / 所有已锁定的契约，含 `design.md` 与各阶段过门禁后的产物）—— Write/Edit 与 Bash 的 `>` `tee` `sed -i` `python3 -c` 等写法都拦
- 角色越权写（`pm` 写代码、前端写 `migrations/`、`qa` 改 `requirements.md`）—— 产物目录按阶段隔离；`role_scopes` 里显式的 `[]` 是「什么都不能写」，缺 key 才回落默认值；`.claude/` `.codex/` `.agents/`（权限引擎、hook 注册表、角色定义）、`knowledge/`（按知识类别分目录的知识库，专属 `knowledger` 角色）、`references/` 公共规范任何角色只读；仅 `references/workspace/<自己的角色>/` 可由对应角色修改，其他角色目录不可写
- 角色跑特权 wb.py 子命令（`phase set`、`phase advance --force`、`role set|clear`、`role scopes --reset`、`task skip`、`init --force`、`contract dispute --clear`、非 owner 的 `contract unlock|bump|consumers`、`config set`）—— 只有 qa 能设 `gate_commands.*`，契约的 `unlock`/`bump`/`consumers` 只认 owner 与 architect。被拦就报回编排者，别换写法
- 灾难性命令（`rm -rf /`、force push、`DROP TABLE`、`curl | sh`、`mkfs`、写块设备）—— 门禁命令同样被筛：`config set gate_commands.*` 的值在写入与执行时各过一遍 `catastrophic_command()`
- 未审核的 skill 调用 —— 角色 subagent 都带了 `Skill` 工具，但只能调 `allowed_skills` 白名单里的 skill。任何非主线程调用者（角色、`general-purpose`、`Explore`，凡带 `agent_type`/`agent_id`）都受这层约束；主线程（两者都无）是审核者，不限。默认空表 = 拒全部，`["*"]` = 全放行。白名单只有主线程能改（`config set` 对角色一律拦），所以「哪些 skill 能用」就是编排者的审核动作。被拦的 skill 报回主线程审核，别绕

被拦时不要绕（不要改 settings、不要换等价命令）。写冻结文件时 Bash 的等价写法（`>` / `tee` / `sed -i`）也拦，换写法没用。要么走 `contract unlock` 申报，要么交给有权限的角色，要么说明理由让用户决定。契约不够用（缺字段、对不上现实）也不是绕过的理由：`task block` 留言，由 architect 走 unlock/bump 修契约。拒绝信息里已经按 owner 分岔给了该跑的命令与契约实名，照它说的做。

```
python3 .claude/hooks/wb.py role scopes      # 当前范围 + 冻结清单 + 解冻窗口
python3 .claude/hooks/wb.py role scopes --reset   # 老项目刷成当前默认值（跨仓库布局会重新按仓库前缀算）
python3 .claude/hooks/wb.py config set role_scopes.backend-developer '["server/**","migrations/**",".workbench/artifacts/*/develop/tasks/**"]'
python3 .claude/hooks/wb.py config set allowed_skills '["wb-flow","wb-knowledge"]'   # 审核放行给 subagent 的 skill（`*`=全部；主线程专属）
```

## 多端适配

- 本文件是唯一正文，其他入口文件名（如 `CLAUDE.md`）软链到它 —— 改协作约定只改 `AGENTS.md`，接入新端加软链即可。历史上两文件曾是各自维护的摘要，各端拿到的规则深度不一致，已收敛。
- 角色定义唯一维护在根目录 `agents/`（每个角色一份 `.md` 与 `.toml`）；`.claude/agents/` 与 `.codex/agents/` 只保留指向根目录的软链，平台通过各自入口加载。**角色名必须与 `wb_const.py` 的 `ROLES` 完全一致**。新增平台时只增加入口软链，不复制角色正文；改角色只改 `agents/` 后检查两端软链与 TOML 解析。
- hook 挂在各端自己的注册表里（如 `.claude/settings.json`、`.codex/hooks.json`），部分端要求项目受信任、hook 通过审核后才真正运行。不要把端上的完全放行模式（如 `danger-full-access`）当作角色权限控制 —— 守卫本身就是 hook，hook 不加载就什么都不是。
- 端注入的环境变量（如 `$CLAUDE_PROJECT_DIR`）只在该端存在，脚本不要依赖它跨端可用；通用钉根用 `WB_ROOT`，或直接相对/绝对路径。

## 已知边界

- 角色按 hook 载荷里的 `agent_type` 判定，并行 subagent 各自生效，与谁最后 `role set` 过无关。`.workbench/role` 只兜底主线程与内置非角色 agent（`BUILTIN_AGENT_TYPES`：`general-purpose` / `Explore` / `Plan`）—— 开发活派给角色 agent，别派给 `general-purpose`，那时范围只能按最后一次 `role set` 兜底。`agent_type` 是陌生值（既非角色名也不在内置白名单）时判 `UNKNOWN_ROLE`，不读 `role` 文件，直接拒写。
- `allowed_skills` 审核门设在「subagent 调用 skill」这一步，管的是**哪些 skill 能跑**，不是**跑起来的 skill 的子 worker 能写什么**。纯 inline 指令型 skill（第三方效率 skill 多是这类）无风险：执行者还是本角色，`agent_type` 不变，写入照旧受本角色范围约束，连恶意 inline skill 也越不过本角色天花板。要留意的只有会 spawn 子 agent 的 skill：若 harness 给 spawn 出的 worker 打不在 `BUILTIN_AGENT_TYPES` 里的陌生 `agent_type`，`current_role` 判 `UNKNOWN_ROLE` 直接拒写，不会退回读 `role` 文件、也不会早退放行——`_check_write_target` 已经在角色解析之前挡住了这类调用者。仍要留意的是白名单本身：只放行 spawn 行为你已核过的 skill，避免恶意 skill 让子 worker 顶着 `general-purpose`/`Explore`/`Plan` 这几个被信任的内置身份行事。
- 解冻窗口按 flow 隔离在 `.workbench/flows/<flow>/unlock/`，一份契约一个文件，多份可以同时开着。同一份契约上不区分申报者 —— 两个 agent 同时改一份契约本身就该避免。`bump` / `lock` 只关自己那一份；`SubagentStop` 关全部但只在没有任务处于 doing 时才关，否则先结束的那个会收掉仍在跑的兄弟的窗口。**所以每个任务收尾都要 `task done`。**
- 产物归属按「角色 + 任务 `started` 时间」认领，同一角色的两个任务并行时分不开。
- 改状态的命令走 `.workbench/flows/<flow>/state.lock` 排他锁，并行 subagent 的 `task done` 不会互相覆盖。只读的不占锁（`status` / `next` / `gate` / `contract impact` / `log --tail`）。锁不跨门禁命令持有，所以 `phase advance` 的门禁结论是**它开跑那一刻**的快照 —— 期间刚落盘的 `task done` 不算进这次结论，再跑一次 `gate check` 就对了；期间别人推了阶段则这次直接拒绝（「这次门禁结论作废，重跑 phase advance」），照它说的重跑。撞上「等状态锁超时」直接重试。
- 角色范围用 `fnmatch` 匹配，`*` 跨 `/`，偏宽松而非严格。一处例外：`GUARDED_PREFIXES`（`.workbench/` `.claude/` `.codex/` `.agents/` `knowledge/` `references/` `scripts/` `repos.json` `.vscode/`）下的路径只认显式以该前缀开头的模式，否则 `*.md` / `*.json` / `*.py` 会跨进产物与契约目录、守卫自己的权限引擎与 hook 注册表、知识库、公共规范、公共脚本与仓库清单、本机 IDE 配置，把阶段隔离、防线本身、沉淀专属性、规范只读性与初始化流程一起绕开。开发与 `reviewer` 有 `*.md`、`qa` 有 `*.config.{ts,js,mjs}` 与 `pytest.ini` / `tox.ini`，都只对仓库内的文件生效。
- Bash 冻结检查先用 `resolve()` 解析重定向、`cp` / `mv` / `install` 等静态写入目标，再检查冻结、越根和角色范围；`sed -i` 只把 `-i` 之后真实存在的文件当写入目标，脚本表达式（`s/a/b/`）与 BSD 的空后缀不算。`cd`/`pushd` 切进 `.workbench` 后写仍有兜底。动态不可解析命令对 subagent 拒绝，不把「Bash 没被拦」当成「这个写入是允许的」。`git checkout`、外部编辑器和用户手改仍由门禁哈希校验兜底。`cp .workbench/contracts/api.yaml /tmp/bak` 的源路径不再误报，目标在 safe 目录时放行。
- 冻结检查带嵌套根反查（写入目标向上找 `.workbench/`）：`repos/` 下若出现自带的 `.workbench/`（如误操作 init 进仓库），写它的冻结契约或 `state.json` 照样拦，申报解冻要在那个仓库里跑 `contract unlock`。本工作台唯一布局下 `repos/` 里不该有 `.workbench/`，这层是防御冗余。
- 特权子命令层的校验基于**解析出的 wb.py 参数**：heredoc body 不在其中、管道分段、`shlex` 分词后逐段核对。`--name` 用的是 flag 的字面值，`--name $C` 这类 shell 变量在 hook 里解析不了（不做变量展开），按「查不到 owner」拒绝 —— 报回编排者用实名重跑即可。主线程不受这层影响（没有 `agent_type`），这层的存在正是「状态只能经 wb.py 改」能成立的原因：没有它，wb.py 能改的一切任何角色都能改。
- 门禁命令是 `shell=True` 的 subprocess，不经 Bash 守卫 —— 这是它作为门禁的前提（任意项目的任意测试命令）。已知上限：catastrophic 模式筛得掉，但 qa 配的非灾难命令就是会原样执行。
- 契约内核只校验内容哈希，不校验语法。要语法校验挂到 `gate_commands.lint`。
- Bash `resolve()` 三态输出：`(all_targets, outside_targets, uncertain)`。`uncertain=True` 时冻结与越根检查退回旧行为（`BASH_WRITE` + `frozen_hits` 文本匹配 + 重定向兜底正则），误报面宽但不漏拦；拒绝信息里会注明「写入目标无法解析，已一并拦截」。兜底正则里的目标先 `resolve()` 再与 safe 目录比对 —— macOS 的 `/tmp` 是软链，不展开的话 `/tmp/xx` 永远比不中，写临时补丁脚本会被误拦。`cp`/`mv` 精确模式下只取最后一个非 flag 参数为写入目标，源路径不误拦。
- 门禁 `run_check` 三态：exit code 0 且命中 `0 tests`/`No tests ran`/`-DskipTests`/`--passWithNoTests` 等零用例或跳过标记时返回 `unverified` 而非 PASS。`unverified` 在门禁汇总里等同 FAIL，但拒绝信息说明不同：「exit=0 但无独立证据表明测试通过」。

设计取舍与每条边界的理由在 `docs/`，索引见 [docs/README.md](docs/README.md)。

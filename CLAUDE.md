# 软件开发工作台

这个仓库是一套基于 Claude Code 的软件开发流程工作台：六阶段流水线、七个角色 subagent、契约管理、门禁校验、任务调度与权限守卫。

## 核心命令

```
python3 .claude/hooks/wb.py status          # 先看这个，每轮都看
python3 .claude/hooks/wb.py --help          # 全部子命令
python3 .claude/hooks/wb.py selfcheck       # 改过 wb.py 后必须跑
```

## 何时走流程，何时不走

**走**：新功能、跨前后端的改动、有接口变化、需求本身不清楚。用 `/wb-flow`。

**不走**：改一两个文件、修个明显的 bug、回答问题、纯文档。直接做，做完说一句「这个改动没走完整流程，因为改动面小/无接口变化」。

把六阶段套在一行改动上是纯开销。

## 多仓库工作区

代码库直接 clone 到本工作区里,不要把 `.claude/` 拷到别处。两种布局,**按需求是否跨仓库来选,不能混用**。

### A. 一个需求只改一个仓库(默认)

每个仓库自带一份 `.workbench/`,共享外层这一套 `.claude/`。

```
/home/work/workbench/
├── .claude/            # 工作台本体,唯一一份
├── .workbench/         # 工作台自身的状态（改 wb.py 时才用）
└── repos/
    ├── foo/.workbench/ # foo 的流程状态、契约、产物
    └── bar/.workbench/
```

clone 之后两步:

```bash
cd repos/foo
python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/wb.py" init --name foo
echo '.workbench/' >> .git/info/exclude    # 别改仓库自己的 .gitignore
```

之后在 `repos/foo` 里正常用所有命令。`wb.py` 向上查找最近的 `.workbench/`,状态自动归属当前仓库;hook 用 `$CLAUDE_PROJECT_DIR` 绝对路径注册,子目录里照样触发。角色范围里的 `server/**`、`web/**` 相对各仓库根,不用改;`gate_commands` 的 cwd 就是仓库根,`npm test` 直接对。

仓库之间天然隔离 —— 这既是好处也是它不能跨仓库的原因。

### B. 一个需求跨多个仓库

**只在外层 init,各仓库里都不要 init。** `find_root()` 一路向上命中外层,项目根 = 整个工作区,一份 state、一份契约、一条流水线,前后端对着同一份锁定契约并行开发。

```bash
python3 .claude/hooks/wb.py init --name <需求名>   # 只在外层
```

必须调两处,否则会静默出错:

**1. 角色范围按仓库前缀,不是按目录名。**

```bash
python3 .claude/hooks/wb.py config set role_scopes.frontend-developer \
  '["repos/frontend/**",".workbench/artifacts/develop/**"]'
python3 .claude/hooks/wb.py config set role_scopes.backend-developer \
  '["repos/backend/**","repos/shared/**",".workbench/artifacts/develop/**"]'
```

默认范围在跨仓库下会歪成**按语言隔离**:`fnmatch` 的 `*` 跨 `/`,所以 `*.py` 会放行任意仓库里的 `.py`,而 `migrations/**` 匹配不到 `repos/backend/migrations/` —— 后端在自己仓库里写不了迁移,却能写别人仓库的同语言文件。跨仓库时仓库本身就是边界,按前缀写最准。

**2. 门禁命令用子 shell 分别 cd。** `gate_commands` 的 cwd 是外层根,那里没有 `package.json`。

```bash
python3 .claude/hooks/wb.py config set gate_commands.test \
  '(cd repos/frontend && npm test) && (cd repos/backend && pytest)'
```

子 shell 括号让 `cd` 不外溢。`&&` 串联时先失败的那个决定退出码,门禁说明只带最后一行输出,所以哪个仓库红了要自己看完整输出。

契约放哪里两种都行:放 `repos/backend/openapi.yaml` 会进那个仓库的 git(适合契约由该服务负责发布);放外层 `.workbench/contracts/` 则不进任何仓库(适合契约独立于双方)。两种都受同一套冻结保护 —— 实测跨仓库路径的契约,前端、后端 owner、主线程的 Write/Edit/`sed -i`/先 `cd` 再改全部拦住。

### 两种模式共同的坑

**每轮 `status` 先看「根」那一行。** 它是当前操作的状态归属。模式 A 下忘了 `cd` 进仓库,会改到外层工作台自己的状态 —— 不报错,只能靠看。

同一个仓库要走第二个需求:先 `report --write` 归档,再 `init --force` 重开。需要两个需求并行改同一份代码,用 `git worktree add ../foo-featureB`,新 worktree 里再 `init` —— 代码与状态一起隔离。

## 六阶段与角色

`clarify` → `analyze` → `design` → `develop` → `verify` → `retro`

| 阶段 | 角色 subagent | 必须产出 |
| --- | --- | --- |
| clarify | `pm` | `.workbench/artifacts/clarify/requirements.md`（含「验收标准」「非目标」） |
| analyze | `analyst` | `analyze/current-state.md`（含「风险」） |
| design | `architect` | `design/design.md`（含「方案对比」）+ 登记并锁定 `design-doc` 契约 + 接口契约 + 任务图 |
| develop | `frontend-developer` `backend-developer` | 代码 + 最小可运行校验 |
| verify | `qa` | `verify/test-report.md` |
| retro | `reviewer` | `retro/retro.md`（含「改进项」） |

编排者不亲自干活，派 subagent。派发时给足上下文：需求原话、上游产物路径、要读的契约文件、相关的验收标准条目。

## 硬规则

1. **状态只能经 wb.py 改。** 直接写 `.workbench/state.json`、`role`、`frozen`、`unlock` 会被守卫拦，Write/Edit 与 shell 重定向、`sed -i` 都拦。门禁与进度必须不可绕过，否则记录没有意义。
2. **契约与方案文档锁定后就是只读的。** 包括对 owner 和主线程。要改先申报：`contract unlock --name <名> --reason '<为什么>'`，改完 `contract bump`。理由必须在改之前写 —— 事后补的理由都是给已发生的事找解释。`design.md` 由 architect 登记为 `design-doc` 契约，走同一套。
3. **门禁不通过不推进。** 需要 `phase advance --force` 时先问用户。唯一例外：FAIL 项本身不适用（纯文档改动没有构建命令）。跳过失败的测试不算例外。
4. **子 agent 说做完了不等于做完了。** 至少确认它声称改的文件存在、它声称跑过的命令你也跑一遍，再 `task done`。
5. **develop 阶段并行派发。** `next --all` 拿一批，放在同一条消息里多个 Agent 调用同时发出。串行派发会浪费掉契约先行带来的全部收益。
6. **不可简化的东西**：信任边界上的输入校验、防数据丢失的错误处理、安全措施、可访问性基础、用户明确要求的功能。其余按最小可用实现。

## 门禁命令

`cmd:test` / `cmd:lint` / `cmd:build` 默认未配置会跳过。项目一旦有测试就配上，否则 verify 门禁形同虚设：

```
python3 .claude/hooks/wb.py config set gate_commands.test 'npm test'
python3 .claude/hooks/wb.py config set gate_commands.lint 'npm run lint'
python3 .claude/hooks/wb.py config set gate_commands.build 'npm run build'
```

## 权限守卫

`PreToolUse` hook 拦四类：

- 写出项目根之外
- 写冻结文件（`state.json` / `role` / `frozen` / `unlock` / 所有已锁定的契约与 `design.md`）—— Write/Edit 与 Bash 的 `>` `tee` `sed -i` `python3 -c` 等写法都拦
- 角色越权写（`pm` 写代码、前端写 `migrations/`、`qa` 改 `requirements.md`）—— 产物目录按阶段隔离
- 灾难性命令（`rm -rf /`、force push、`DROP TABLE`、`curl | sh`、`mkfs`、写块设备）

被拦时不要绕（不要改 settings、不要换等价命令、不要用 Bash 代替 Write —— 那条也拦）。要么走 `contract unlock` 申报，要么交给有权限的角色，要么说明理由让用户决定。

```
python3 .claude/hooks/wb.py role scopes      # 当前范围 + 冻结清单 + 解冻窗口
python3 .claude/hooks/wb.py role scopes --reset   # 老项目刷成当前默认值
python3 .claude/hooks/wb.py config set role_scopes.backend-developer '["server/**","migrations/**",".workbench/artifacts/develop/**"]'
```

## 已知边界

- 角色锁与解冻窗口都是单个文件，并行 subagent 会互相覆盖。hook 拿不到「当前是哪个 subagent」，这是上游限制。并行派发时依赖 subagent 自己遵守写入范围，守卫只兜底最后一次 set 的角色。
- 角色范围用 `fnmatch` 匹配，`*` 跨 `/`，偏宽松而非严格。
- Bash 冻结检查不覆盖 `cp` / `mv` / 外部编辑器 / `git checkout`（误报成本高于收益）。这类改动靠 `contract verify` 的哈希校验在门禁时抓出。
- 契约内核只校验内容哈希，不校验语法。要语法校验挂到 `gate_commands.lint`。

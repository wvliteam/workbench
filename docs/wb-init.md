# wb-init：多仓库初始化 skill 的需求与实现（2026-09-07）

把「配置的代码仓库 clone 到本地 + 自动生成 VS Code 多根 workspace 文件」做成初始化 skill，让一个新工作区从清单到可开发状态一条命令完成。对照材料是 ROMA v0.3.6 源码快照（`output/agents.tgz`，`output/` 不进仓库）里的 `roma-onboarding` 与 `check-health` 两个 skill。

改动范围：新增 `.claude/skills/wb-init/`（SKILL.md + `scripts/init_repos.py`）与 `.agents/skills/wb-init/` 双端副本；AGENTS.md / CLAUDE.md / README 入口各一段；顺带把三个旧 skill 的 `.agents` 副本同步成 `.claude` 权威版（它们停在 flow 改造前的旧产物路径）。**不动 `wb.py`** —— 初始化不是流程状态，不属于状态内核。

## 需求与验收

需求来自 ROMA 的使用体验：用户只说「初始化」，agent 按清单把仓库落地、配好 IDE、报告阻塞项，而不是让人手动逐仓 clone 再逐仓 init。三条验收：

1. **按清单 clone**：仓库清单是一份跟着 git 走的配置文件，脚本按清单把仓库落到 `repos/`，软链本机已有 checkout 作为等价选项。
2. **自动生成 VS Code workspace 文件**：多根 `.code-workspace`（根 + 全部仓库）与 `.vscode/settings.json` 的 git 发现配置，VS Code 打开一个文件就能同时看到所有仓库。
3. **兼容 Claude、Codex 与其他通用 agents**：SKILL.md 双端各一份（`.claude/skills/` 与 `.agents/skills/`），执行脚本全端共用一份；SKILL.md 本身不引用任何一家独有的工具机制。

明确不做：依赖安装与环境验证（那是各仓库开发阶段的事，走 `/wb-flow`）；仓库知识沉淀（ROMA 的 overview/setup/test 三件套，对单人工作台是纯开销，与 [roma-comparison.md](roma-comparison.md)「明确不抄的」同一判据）。

## 参照物：ROMA 的这个能力在哪

**不在 `env-init`。** env-init 是环境初始化（runtime 契约、依赖安装、验证、smoke，六步门禁），clone 仓库不在它的六步里。真正的参照物是：

- **`roma-onboarding/scripts/materialize_repo_selection.py`**（489 行）：读 `.roma/init-selection.json`，按 `use_local`（软链）/ `clone` / `skip` 三种 action 落地到 `repos/.source/<name>`，末尾 `ensure_ide_workspace()` 与 `ensure_ide_settings()` 生成 IDE 配置。
- **`check-health/scripts/workspace_profile.py`**：`ide:code-workspace` / `ide:vscode-settings` 健康检查项与 `--fix` 修复，里面有更保守的合并策略（保留用户键、无效 JSON 不覆盖）。

借过来的三条行为准则（也写进了脚本 docstring）：

1. **幂等**：已存在的 checkout 不覆盖，按 `exists` 上报；IDE 配置**始终刷新** —— 所以部分仓库已存在时也不能跳过重跑，否则 IDE 配置会漏掉新挂载的仓库。
2. **磁盘为准**：workspace 文件的 folders 来自扫描 `repos/` 下带 `.git` 的目录（含软链 resolve），不信任清单 —— clone 失败的仓库不会混进 IDE 配置。
3. **失败不伪装成功**：clone / 软链失败显式报 error、退出码非 0；ROMA 还禁止用指针文件把失败「消解」成完成态（`blocked_source`），这条进 SKILL.md 的汇总要求。

没抄的：iCode 专属逻辑（`icode_user` 推断链、ssh-config 脚本下载、commit-msg hook、`baidu/` 前缀映射）；`.roma/init-selection.json` 的用户选择记录（我们的清单直接是确认态）；INIT-PLAN 任务图与证据账本（对两三个仓库的一次性落地是纯开销）。

## 设计决策与取舍

| 决策 | 选择 | 为什么 / 代价 |
| --- | --- | --- |
| 清单位置 | 工作区根 `repos.json`，进 git | 清单是团队共识不是本机状态；ROMA 的 `.repo-list` 同样进 git，它的 `init-selection.json`（用户选择）才不进。代价：仓库地址公开在仓库里，私有 fork 地址不适合入清单 |
| clone 落点 | `repos/<name>` | CLAUDE.md 布局 A 的既有约定，不引入 ROMA 的 `repos/.source/` 第二层 —— `wb.py` 的 `repo_layout_scopes` / `nested_roots` 都按 `repos/*` 认 |
| workspace 文件落点 | `.workbench/<工作区名>.code-workspace` | 对应 ROMA 的 `.roma/`：folders 用绝对路径，所以必须放 gitignore 里（`.workbench/` 已忽略），每台机器自理。绝对路径换机器失效的解药就是「始终刷新」—— 重跑一次脚本即修复 |
| settings 合并策略 | `git.scanRepositories` 只管理 `repos/` 前缀（按磁盘重写），前缀外的用户条目原样保留；`git.autoRepositoryDetection` / `git.repositoryScanMaxDepth` 直接更新；无效 JSON 不覆盖只告警 | 照抄 check-health 的 `_init_ide_settings`。代价：用户手写的 `repos/xxx` 条目若目录不存在会被清掉 —— 这是「磁盘为准」的延伸，不是缺陷 |
| `--init` 调用方式 | `wb.py init --name <名> --root <仓库路径>` | `cmd_init` 以 cwd / `--root` 定点、**不走 `find_root()`**（`wb.py:1445`），所以不会被 hook 注入的 `CLAUDE_PROJECT_DIR` / `WB_ROOT` 带到外层根 —— 验证过：状态落在仓库自己的 `.workbench/flows/main/state.json`，外层不受污染。幂等：已有 `.workbench/` 的仓库跳过 |
| 脚本归属 | `.claude/skills/wb-init/scripts/`，不进 `wb.py` | clone 和 IDE 配置不是流程状态，`wb.py` 是状态内核；布局 A 的 init 是调用既有 CLI，不需要新子命令。ROMA 同样把落地做成 skill 脚本而非内核 |
| 双端兼容 | 脚本一份，SKILL.md 双端各一份，内容逐字一致 | 与 Codex hook 共用 `.claude/hooks/wb.py` 同一模式 —— 每端一份脚本买不到任何东西（roma-comparison 跨端节的结论）。顺带发现三个旧 skill 的 `.agents` 副本已漂移，一并同步 |
| 执行者 | 主线程直做，SKILL.md 明令不派 subagent | 写入目标 `repos/**` 与 `.vscode/**` 不在任何 developer 角色范围内，派下去会被自己的守卫拦；ROMA 的 materialize 同样是主 Agent 直做 |
| 输入校验 | `name` 必须匹配 `^[A-Za-z0-9][A-Za-z0-9._-]*$`（单段、不以 `.` 开头）；clone 命令带 `--` 分隔；`remote` 以 `-` 开头直接拒绝 | 防 `../` 穿越与 `.git` 这类名字；防远程地址被 git 解析成选项；URL 里的 `user@` 是 SSH 登录账号，逐字使用 —— 这是 ROMA SKILL.md 里用整段教训换来的规则 |

布局 B（一个需求跨多个仓库）不引入新机制：脚本不带 `--init` 只 clone，外层 `wb.py init` 与角色范围认领仍是既有流程（CLAUDE.md「多仓库工作区」）。布局选择交给 SKILL.md 第 3 步判断，脚本不猜。

## 测试（临时工作区 + 本地 fixture 仓库实跑）

| 场景 | 预期 | 结果 |
| --- | --- | --- |
| clone 指定分支（本地路径 remote + `--branch dev`） | 落在 dev 分支 | 通过 |
| `link` 软链本机 checkout | symlink 建立，workspace 文件 resolve 到真实路径 | 通过 |
| `.code-workspace` 内容 | folders = 根 + 全部仓库，按名排序 | 通过 |
| settings 注入 | 3 个 git 键写入，`git.scanRepositories` 含 `repos` + 各仓库相对路径 | 通过 |
| 幂等重跑 | 全部 `EXISTS`、退出码 0、IDE 文件刷新 | 通过 |
| `--init`（布局 A 两步） | 仓库内 `.workbench/` 生成、`project` 为仓库名、`.git/info/exclude` 增一行；外层 `.workbench/` 只有 workspace 文件 | 通过 |
| `--init` 重跑 | init 全部跳过 | 通过 |
| `--json` | 可解析，`error_count` 与文件路径正确 | 通过 |
| `name: "../evil"` | 拒绝，退出码 1 | 通过 |
| `remote` + `link` 同填 | 拒绝 | 通过 |
| 缺清单 | 报错并打印样例 | 通过 |
| `.vscode/settings.json` 为无效 JSON | 不覆盖、WARN、退出码 0 | 通过 |
| 用户键保留 | `editor.tabSize` 与前缀外 scanRepositories 条目原样保留 | 通过 |
| clone 失败（remote 不存在） | `ERROR` 带 git 输出尾部，退出码 1 | 通过 |
| 目标已存在但非 git 目录 | 不覆盖、报错、退出码 1 | 通过 |
| `wb.py selfcheck` | 全绿（内核未改动的回归确认） | 通过 |

## 已知边界

- **`git.scanRepositories` 的 `repos/` 前缀条目按磁盘重写**：手工登记但目录不存在的条目会被清掉；前缀外的条目不受影响。
- **绝对路径跨机器失效**：`.code-workspace` 里的 folders 是绝对路径，换机器后重跑脚本即修复（幂等刷新是设计内行为，不是补丁）。
- **递归扫描不进已确认的 git 根下钻**：`repos/foo` 本身是仓库时，它内部的嵌套仓库不进 workspace 文件；`repos/` 下的断链软链被静默跳过（不进 IDE 配置，也不报错 —— 清单里的同名条目会在 materialize 阶段报断链 error）。
- **clone 失败的诊断只保留通用提示**：ROMA 那套「从报错登录账号推断 URL 丢了 `user@`」的对话树依赖 iCode 的固定主机名，不通用；SKILL.md 保留了「看 `<user>@<host>` 判断账号」的人工指引。
- **布局 B 的角色范围认领不做自动化**：`repo_layout_scopes` 按目录名猜前缀、认不出的点名让人 `config set`，这是 wb.py 的既有取舍 —— 初始化 skill 不重复猜一遍。
- **`repos/` 未被外层 git 忽略只警告不修**：外层工作区通常不是 git 仓库；是且未忽略时打一行 WARN，改 `.gitignore` 留给用户。

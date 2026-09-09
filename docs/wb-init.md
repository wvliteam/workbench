# wb-init：多仓库初始化 skill 的需求与实现

把「配置的代码仓库 clone 到本地 + 自动生成 VS Code 多根 workspace 文件」做成初始化 skill，让一个新工作区从清单到可开发状态一条命令完成。对照材料是 ROMA v0.3.6 源码快照（`output/agents.tgz`，`output/` 不进仓库）里的 `roma-onboarding` 与 `check-health` 两个 skill。

改动范围：根级 `scripts/repos_apply.py`（清单落地，`--root` 起跑）、`scripts/repos_tui.py`（交互式编辑清单，带 `--selftest` 非交互自测）、工作区根 `repos.json` 清单；SKILL.md 双端各一份（`.claude/skills/wb-init/` 与 `.agents/skills/wb-init/`，逐字一致），执行脚本全端共用一份。**不动 `wb.py`** —— 初始化不是流程状态，不属于状态内核。

## 需求与验收

需求来自 ROMA 的使用体验：用户只说「初始化」，agent 按清单把仓库落地、配好 IDE、报告阻塞项，而不是让人手动逐仓 clone 再逐仓 init。三条验收：

1. **按清单 clone**：仓库清单是一份跟着 git 走的配置文件，脚本按清单把仓库落到 `repos/`，软链本机已有 checkout 作为等价选项。
2. **自动生成 VS Code workspace 文件**：多根 `.code-workspace`（根 + 全部仓库）与 `.vscode/settings.json` 的 git 发现配置，VS Code 打开一个文件就能同时看到所有仓库。
3. **兼容 Claude、Codex 与其他通用 agents**：SKILL.md 双端各一份，执行脚本全端共用一份；SKILL.md 本身不引用任何一家独有的工具机制。

明确不做：依赖安装与环境验证（那是各仓库开发阶段的事，走 `/wb-flow`）；仓库知识沉淀（ROMA 的 overview/setup/test 三件套，对单人工作台是纯开销，与 [roma-comparison.md](../draft/roma-comparison.md)「明确不抄的」同一判据）。

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
| clone 落点 | `repos/<name>` | 外层唯一布局的既有约定，不引入 ROMA 的 `repos/.source/` 第二层 —— `wb.py` 的 `repo_layout_scopes` / `nested_roots` 都按 `repos/*` 认 |
| workspace 文件落点 | `.workbench/<工作区名>.code-workspace` | 对应 ROMA 的 `.roma/`：folders 用绝对路径，所以必须放 gitignore 里（`.workbench/` 已忽略），每台机器自理。绝对路径换机器失效的解药就是「始终刷新」—— 重跑一次脚本即修复 |
| settings 合并策略 | `git.scanRepositories` 只管理 `repos/` 前缀（按磁盘重写），前缀外的用户条目原样保留；`git.autoRepositoryDetection` / `git.repositoryScanMaxDepth` 直接更新；无效 JSON 不覆盖只告警 | 照抄 check-health 的 `_init_ide_settings`。代价：用户手写的 `repos/xxx` 条目若目录不存在会被清掉 —— 这是「磁盘为准」的延伸，不是缺陷 |
| 脚本归属 | 根级 `scripts/`（`repos_apply.py` / `repos_tui.py`），不进 `wb.py`，也不进 `.claude/skills/wb-init/scripts/` | clone 和 IDE 配置不是流程状态，`wb.py` 是状态内核；`scripts/` 与 `repos.json` 是工作区级公共资产，进守卫前缀、角色写不到（见 [permissions.md](permissions.md#第四层角色写入范围)），主线程直做 |
| 双端兼容 | 脚本一份，SKILL.md 双端各一份，内容逐字一致 | 与 Codex hook 共用 `.claude/hooks/wb.py` 同一模式 —— 每端一份脚本买不到任何东西（roma-comparison 跨端节的结论） |
| 执行者 | 主线程直做，SKILL.md 明令不派 subagent | 写入目标 `repos/**` 与 `.vscode/**` 不在任何 developer 角色范围内，派下去会被自己的守卫拦；ROMA 的 materialize 同样是主 Agent 直做 |
| 输入校验 | `name` 必须匹配 `^[A-Za-z0-9][A-Za-z0-9._-]*$`（单段、不以 `.` 开头）；clone 命令带 `--` 分隔；`remote` 以 `-` 开头直接拒绝 | 防 `../` 穿越与 `.git` 这类名字；防远程地址被 git 解析成选项；URL 里的 `user@` 是 SSH 登录账号，逐字使用 —— 这是 ROMA SKILL.md 里用整段教训换来的规则 |
| `remote` 省略 `name` | `derive_name()` 取最后一段去 `.git`；SCP 风格 remote（`git@gitlab.com:payments-core.git`，无 `://`）取冒号后段 | 整串返回的旧逻辑会让 `git@gitlab.com:payments-core` 过不了 NAME_RE；有 `repos_tui.py --selftest` 断言兜底 |
| 既存仓库的一致性 | 目标已是 git 仓库时核对 `git remote get-url origin` 与清单 `remote`：不一致报 ERROR、退出码 1，提示 `git remote set-url`；一致才按 `EXISTS` 跳过 | 「已存在」与「存在且与清单一致」是两个不同的幂等结论。不校验的话清单换了源、磁盘还是旧源，IDE 配置看着正常、pull 全走错远端 —— 错误只在第一次 fetch 时以更迷惑的形态出现 |
| `link` 相对路径 | 非绝对路径时相对工作区根 resolve | 清单进 git，协作者机器上本机 checkout 路径不同是常态 |

清单格式：`{"repos":[{"name":"foo","remote":"git@…","link":"/path/to/local","branch":"dev"}]}`，`remote` 与 `link` 二选一，交互式编辑用 `python3 scripts/repos_tui.py`。

## 测试（临时工作区 + 本地 fixture 仓库实跑）

| 场景 | 预期 | 结果 |
| --- | --- | --- |
| clone 指定分支（本地路径 remote + `--branch dev`） | 落在 dev 分支 | 通过 |
| `link` 软链本机 checkout | symlink 建立，workspace 文件 resolve 到真实路径 | 通过 |
| `.code-workspace` 内容 | folders = 根 + 全部仓库，按名排序 | 通过 |
| settings 注入 | 3 个 git 键写入，`git.scanRepositories` 含 `repos` + 各仓库相对路径 | 通过 |
| 幂等重跑 | 全部 `EXISTS`、退出码 0、IDE 文件刷新 | 通过 |
| `--json` | 可解析，`error_count` 与文件路径正确 | 通过 |
| `name: "../evil"` | 拒绝，退出码 1 | 通过 |
| `remote` + `link` 同填 | 拒绝 | 通过 |
| 缺清单 | 报错并打印样例 | 通过 |
| `.vscode/settings.json` 为无效 JSON | 不覆盖、WARN、退出码 0 | 通过 |
| 用户键保留 | `editor.tabSize` 与前缀外 scanRepositories 条目原样保留 | 通过 |
| clone 失败（remote 不存在） | `ERROR` 带 git 输出尾部，退出码 1 | 通过 |
| 目标已存在但非 git 目录 | 不覆盖、报错、退出码 1 | 通过 |
| `repos_tui.py --selftest` | 非交互自测全过（含 SCP 名字推导、非法/重复/二选一校验） | 通过 |
| `wb.py selfcheck` | 全绿（内核未改动的回归确认） | 通过 |

## 已知边界

- **`git.scanRepositories` 的 `repos/` 前缀条目按磁盘重写**：手工登记但目录不存在的条目会被清掉；前缀外的条目不受影响。
- **绝对路径跨机器失效**：`.code-workspace` 里的 folders 是绝对路径，换机器后重跑脚本即修复（幂等刷新是设计内行为，不是补丁）。
- **递归扫描不进已确认的 git 根下钻**：`repos/foo` 本身是仓库时，它内部的嵌套仓库不进 workspace 文件；`repos/` 下的断链软链被静默跳过（不进 IDE 配置，也不报错 —— 清单里的同名条目会在 materialize 阶段报断链 error）。
- **clone 失败的诊断只保留通用提示**：ROMA 那套「从报错登录账号推断 URL 丢了 `user@`」的对话树依赖 iCode 的固定主机名，不通用；SKILL.md 保留了「看 `<user>@<host>` 判断账号」的人工指引。
- **角色范围认领不做自动化**：`repo_layout_scopes` 按目录名猜前缀、认不出的点名让人 `config set`，这是 wb.py 的既有取舍 —— 初始化 skill 不重复猜一遍。
- **`repos/` 未被外层 git 忽略只警告不修**：外层工作区通常不是 git 仓库；是且未忽略时打一行 WARN，改 `.gitignore` 留给用户。

## 演进记录

初版（2026-09-07）含布局 A/B 两方案与 `--init` 两步流程（脚本在 `.claude/skills/wb-init/scripts/init_repos.py`，布局 A 可选地对单仓库跑 `wb.py init`）。2026-09-08 收敛为唯一布局：外层一份状态、各仓库不 init，脚本移到根级 `scripts/repos_apply.py`，`--init` 随布局 A 移除，新增 `repos_tui.py`，既存仓库的 origin 一致性校验随后补入。布局取舍的理由见 [architecture.md](architecture.md#状态归属一个工作区多个仓库)；ROMA 对照的完整记录见 [roma-comparison.md](../draft/roma-comparison.md)。

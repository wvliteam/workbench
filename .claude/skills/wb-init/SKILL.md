---
name: wb-init
description: 多仓库工作区初始化。按 repos.json 清单把代码仓库 clone 或软链到 repos/，生成 VS Code 多根 .code-workspace 与 .vscode git 发现配置。当用户要"初始化工作区、把代码仓库拉下来、配置要开发的仓库、生成 workspace 文件、新加一个仓库"时使用；不负责依赖安装与环境验证、不负责工作台 init（在外层跑 wb.py init）。
---

# 工作台多仓库初始化

把「清单里的仓库」落到本地并配好 IDE，配合 CLAUDE.md「多仓库工作区」的唯一布局（外层一份状态）。脚本幂等：已有 checkout 不覆盖、IDE 配置只刷新；clone 失败显式列为阻塞项，不伪装成功。

**由主线程直接执行，不派 subagent** —— 初始化是编排动作；且写入目标 `scripts/`、`repos.json`、`.vscode/**` 都收在守卫前缀里（`repos/**` 不在其中，但 clone 到那里同样是编排决定），派下去会被自己的守卫拦。内核命令 `WB = python3 .claude/hooks/wb.py`。

## 交互式管理清单（repos_tui.py）

不想手写 repos.json 时，用 curses 全屏界面在终端管理仓库列表：

```
python3 scripts/repos_tui.py --root .
```

边框 + 标题栏 + 表格 + 底部键栏：`↑↓`/`j k` 上下选中（反色高亮）、`a` 添加（分步模态输入）、`d` 删除、`e` 编辑（回车保留原值、`-` 清空字段、带默认值的框输入即替换）、`Enter` 看详情、`w` 写入 repos.json 并立即落地（clone/软链 + workspace）、`q` 退出（未保存会确认）。写盘在 `w` 才发生，之前随便试。

校验与落地全部复用下面 repos_apply.py 的同一套逻辑，TUI 只是多了「编辑清单」这层界面。**交互模式要真实终端**，由用户在终端直接运行；Claude 侧只跑非交互自测 `repos_tui.py --selftest`，别在工具里直接跑交互模式（需要 tty，会挂起）。

已经有 repos.json、只想按清单一次性落地时，直接用下面的 repos_apply.py。

## 第 0 步：看状态

```
python3 .claude/hooks/wb.py status
```

工作区还没 init 时它会提示未初始化，属正常 —— 第 3 步在外层 init。注意输出里的根路径一行，确认状态归属外层根。

## 第 1 步：确认仓库清单 repos.json

清单在工作区根，跟着 git 走（团队共享）。没有就先向用户要仓库地址，**不要猜 clone URL**；用户给的名字模糊（只有仓库名）时同样先确认。格式：

```json
{
  "repos": [
    { "name": "frontend", "remote": "git@github.com:org/frontend.git" },
    { "name": "backend", "remote": "https://github.com/org/backend.git", "branch": "main" },
    { "name": "shared-libs", "link": "/Users/me/code/shared-libs" }
  ]
}
```

- `name` 省略时取 remote 最后一段（去 `.git`）；必须是单段路径名。
- `remote` 逐字使用 —— URL 里的 `user@` 是 SSH 登录账号，删掉它会退化成当前 shell 用户登录。
- `link` 是本机已有 checkout 的路径（软链接入，不复制代码），与 `remote` 二选一。

## 第 2 步：落地仓库与 IDE 配置

```
python3 scripts/repos_apply.py --root .
```

行为：

- `repos/<name>` 已存在且是 git 仓库 → 跳过不覆盖（按 `EXISTS` 上报）；存在但不是 git 仓库 → 报错不碰。
- clone / 软链失败的仓库按 `ERROR` 上报，退出码非 0 —— **停下来处理，不要静默继续**。常见原因：SSH key 缺失、登录账号不对（看报错里的 `<user>@<host>`）、网络不可达、link 路径不存在。
- `.workbench/<工作区名>.code-workspace`（folders = 工作区根 + `repos/` 下全部 git 仓库，**以磁盘扫描为准**，不信任清单）与 `.vscode/settings.json`（`git.scanRepositories` 等，保留用户已有键）每次都刷新，所以部分仓库已存在时也不要跳过本步。
- `repos/` 未被外层 git 忽略时给一行警告（clone 进来的仓库会脏外层 git status）。

## 第 3 步：工作台 init（外层）

**唯一布局：只在外层 init，各仓库里都不要 init。** 项目根 = 整个工作区，一份 state、一份契约、一条流水线：

```
python3 .claude/hooks/wb.py init --name <需求名>
```

init 检测到 `repos/*` 会把角色范围按仓库前缀重算，并点名认不出的仓库 —— 按它给的命令 `config set` 认领，再配 `gate_commands`（子 shell 分别 cd）。**不调这两处是静默出错**：认不出的仓库谁都写不了，门禁命令在外层根跑不了。

## 第 4 步：汇总

重跑一次 `--json` 确认最终磁盘状态，然后向用户报告五类，不要一句话带过：

1. 本次 clone 成功的仓库与落点；
2. 已存在而跳过的 checkout；
3. 软链接入的仓库与实际指向；
4. **阻塞项**（clone 失败、断链、非法清单条目）—— 单独列出，给可选方案：补权限/SSH 后重试、改用本地 checkout 路径软链、明确跳过；
5. init 状态（外层 state 与角色范围认领结果）。

## 边界

- 不装依赖、不跑构建/测试 —— 仓库环境验证走 `/wb-flow` 的对应阶段。
- 不覆盖用户已有 checkout；换 remote、换本地地址属于单个仓库的维护，手动做。
- `.vscode/settings.json` 不是有效 JSON 时脚本不覆盖、只告警，等用户手工修复。
- 清单 `name` 非法（含 `/`、以 `.` 开头等）会被脚本拒绝，不要绕过校验手建目录。

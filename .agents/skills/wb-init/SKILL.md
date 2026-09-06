---
name: wb-init
description: 多仓库工作区初始化。按 repos.json 清单把代码仓库 clone 或软链到 repos/，生成 VS Code 多根 .code-workspace 与 .vscode git 发现配置，并可选完成布局 A 每仓库的 init 两步。当用户要"初始化工作区、把代码仓库拉下来、配置要开发的仓库、生成 workspace 文件、新加一个仓库"时使用；不负责依赖安装与环境验证。
---

# 工作台多仓库初始化

把「清单里的仓库」落到本地并配好 IDE，对应 CLAUDE.md「多仓库工作区」的两种布局。脚本幂等：已有 checkout 不覆盖、IDE 配置只刷新；clone 失败显式列为阻塞项，不伪装成功。

**由主线程直接执行，不派 subagent** —— 写入目标 `repos/**` 与 `.vscode/**` 不在任何角色范围内，派下去会被自己的守卫拦。内核命令 `WB = python3 .claude/hooks/wb.py`。

## 第 0 步：看状态

```
python3 .claude/hooks/wb.py status
```

工作区还没 init 时它会提示未初始化，属正常 —— 布局选择（第 3 步）决定要不要 init、在哪 init。多仓库布局下注意输出里的根路径一行，别把状态记到别的仓库头上。

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
python3 .claude/skills/wb-init/scripts/init_repos.py --root .
```

行为：

- `repos/<name>` 已存在且是 git 仓库 → 跳过不覆盖（按 `EXISTS` 上报）；存在但不是 git 仓库 → 报错不碰。
- clone / 软链失败的仓库按 `ERROR` 上报，退出码非 0 —— **停下来处理，不要静默继续**。常见原因：SSH key 缺失、登录账号不对（看报错里的 `<user>@<host>`）、网络不可达、link 路径不存在。
- `.workbench/<工作区名>.code-workspace`（folders = 工作区根 + `repos/` 下全部 git 仓库，**以磁盘扫描为准**，不信任清单）与 `.vscode/settings.json`（`git.scanRepositories` 等，保留用户已有键）每次都刷新，所以部分仓库已存在时也不要跳过本步。
- `repos/` 未被外层 git 忽略时给一行警告（clone 进来的仓库会脏外层 git status）。

## 第 3 步：布局与工作台 init

布局按「一个需求是否跨仓库」选（CLAUDE.md「多仓库工作区」），不能混用；不确定就问用户。

- **布局 A（默认，一个需求只改一个仓库）**：每仓库自带 `.workbench/`。加 `--init` 让脚本补每仓库两步（`wb.py init --name <仓库名>` + `.workbench/` 写进该仓库 `.git/info/exclude`），已有 `.workbench/` 的仓库自动跳过：

  ```
  python3 .claude/skills/wb-init/scripts/init_repos.py --root . --init
  ```

- **布局 B（一个需求跨多个仓库）**：只在外层 init，各仓库都不 init：

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
5. init 状态（布局 A 每仓库是否就绪 / 布局 B 外层 state 与角色范围认领结果）。

## 边界

- 不装依赖、不跑构建/测试 —— 仓库环境验证走 `/wb-flow` 的对应阶段。
- 不覆盖用户已有 checkout；换 remote、换本地地址属于单个仓库的维护，手动做。
- `.vscode/settings.json` 不是有效 JSON 时脚本不覆盖、只告警，等用户手工修复。
- 清单 `name` 非法（含 `/`、以 `.` 开头等）会被脚本拒绝，不要绕过校验手建目录。

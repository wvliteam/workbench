---
name: wb-init
description: 多仓库工作区初始化。按 repos.json 清单把代码仓库 clone 或软链到 repos/.source/<项目>/<仓库>，生成 VS Code 多根 .code-workspace 与 .vscode git 发现配置。当用户要"初始化工作区、把代码仓库拉下来、配置要开发的仓库、生成 workspace 文件、新加一个仓库"时使用；不负责依赖安装与环境验证、不负责工作台 init（在外层跑 wb.py init）。
---

# 工作台多仓库初始化

把「清单里的仓库」落到本地并配好 IDE，配合 CLAUDE.md「多仓库工作区」的唯一布局（外层一份状态）。脚本幂等：已有 checkout 不覆盖、IDE 配置只刷新；clone 失败显式列为阻塞项，不伪装成功。

## 目录结构（先看这个）

源码与画像是**两棵树**，都在 `repos/` 下、彼此不重叠：

```
repos/
├── index.md                         索引：| 仓库 | 源码入口 | 所属项目 | 主要职责 | 介绍文档 |
├── .source/<项目>/<仓库>   ⇒ 软链或 clone 到真实 checkout   ← 代码（不进 git）
└── <项目>/<仓库>/                    ← 画像三件套（进 git）
    ├── overview.md                     职责、仓库关系、对外能力
    ├── setup.md                        怎么跑起来
    └── test.md                         怎么测、判据是什么
```

- **项目**是仓库的归属单元（`bddev` / `map-cjh-hotel` / `map-hotel-fe` …），角色认领按**项目**一级判定，前后端边界正好落在这里。新增仓库落进既有项目时不用改任何配置。
- 源码入口是软链（`link`）或 clone（`remote`）：软链指向本机已有 checkout，不复制代码；两种都落在 `.source/<项目>/<仓库>`。
- 画像跟着工作区走 git，源码跟着 checkout 走 —— 所以两者必须分开，别把画像写进 `.source/`。

**由主线程直接执行，不派 subagent** —— 初始化是编排动作；且写入目标 `scripts/`、`repos.json`、`repos/index.md`、`repos/<项目>/`、`.vscode/**` 都收在守卫的工作区材料前缀里（角色只读），派下去会被自己的守卫拦。内核命令 `WB = python3 .claude/hooks/wb.py`。

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
    { "project": "map-hotel-fe", "name": "hotel-product", "remote": "git@github.com:org/hotel-product.git", "description": "用户界面，React SPA" },
    { "project": "bddev", "name": "maphotel", "remote": "https://github.com/org/maphotel.git", "branch": "main" },
    { "project": "map-cjh-hotel", "name": "map-mc", "link": "/Users/me/code/map-mc" }
  ]
}
```

- `project` 是**归属项目**（`bddev` / `map-cjh-hotel` / `map-hotel-fe` …），决定落点 `repos/.source/<项目>/<仓库>` 与角色认领 —— 认领按项目一级判定，写错会让这个仓库谁都写不了（`status` 会点名）。
- `name` 是仓库名，单段路径名，不能含 `/`。
- `remote` 逐字使用 —— URL 里的 `user@` 是 SSH 登录账号，删掉它会退化成当前 shell 用户登录。
- `link` 是本机已有 checkout 的路径（软链接入，不复制代码），与 `remote` 二选一。
- `description` 可选，一句话职责：`repos_apply.py` 会打印出来，也是 `repos/index.md` 的种子。取不到证就留空，别猜。

## 第 2 步：落地仓库与 IDE 配置

```
python3 scripts/repos_apply.py --root .
```

目标落点：**`repos/.source/<项目>/<仓库>`**（软链或 clone），已存在不覆盖。

- 已存在且是 git 仓库 → 跳过（按 `EXISTS` 上报）；已存在但不是 git 仓库 → 报错不碰。
- clone / 软链失败的仓库按 `ERROR` 上报，退出码非 0 —— **停下来处理，不要静默继续**。常见原因：SSH key 缺失、登录账号不对（看报错里的 `<user>@<host>`）、网络不可达、link 路径不存在。
- `.workbench/<工作区名>.code-workspace`（folders = 工作区根 + `repos/.source/*/*` 下全部 git 仓库，**以磁盘扫描为准**，不信任清单）与 `.vscode/settings.json`（`git.scanRepositories` 等，保留用户已有键）每次都刷新。
- `repos/.source/` 未被外层 git 忽略时给一行警告。
- 画像目录 `repos/<项目>/<仓库>/` 的创建**不归本脚本**：它由 `wb.py init` 建画像任务、analyst 产出（源码与画像分两棵树，本脚本只碰 `.source/` 那棵）。

## 仓库索引与画像（分工图与稳定事实）

仓库落地后补两处，`wb.py status` 与 `role scopes` 每次校验并点名缺失：

**索引 `repos/index.md`** —— 编排者扫分工用，每仓一行。列的位置不限，**按列名定位** ——
只有列名含「仓库」的那张表被当作仓库表，index.md 里的其它表（契约消费方、运行时依赖…）
会被跳过：

```markdown
| 仓库 | 源码入口 | 所属项目 | 主要职责 | 介绍文档 |
| --- | --- | --- | --- | --- |
| bddev/maphotel | .source/bddev/maphotel | 百度地图酒店业务 | 后端主服务，提供酒店查询/下单/优惠券… | [repos/bddev/maphotel/overview.md](bddev/maphotel/overview.md) |
```

**画像三件套 `repos/<项目>/<仓库>/{overview,setup,test}.md`** —— 跨需求复用的稳定事实，
作者是 `analyst`。三件各管一件事，对应上游单文件笔记的三个章节：

| 文件 | 放什么 |
| --- | --- |
| `overview.md` | 职责、仓库关系、对外能力 |
| `setup.md` | 怎么跑起来（依赖、启动顺序、readiness 判据） |
| `test.md` | 怎么测、判据是什么 |

- 职责来源：清单的 `description`、仓库 README、或直接问用户。**取不到证写「待补充」，不要猜** —— 校验只报不改，编一句话比留空更糟。
- 三件缺任一、或某件是空文件都会被点名（**不**逐条查「待补充」—— 画像里局部未取证是合规写法，逐条点名只会把警告刷成噪音）。首轮初始化可以只建索引，画像留给第一次 analyze 的 analyst 补。
- 入口文档可空；填了就必须存在（`路径` 与 `[文字](路径)` 两种写法都认），否则点名死链。
- **进 git 的是索引与画像，不是源码**：`.gitignore` 忽略 `repos/.source/*`（clone/软链进来的 checkout 各有自己的远程），`repos/index.md` 与 `repos/<项目>/<仓库>/` 照常入库。
- 画像目录还有一处约定：画像是**进 git 的工作区材料**，所以 `analyst` 的写入范围是逐个文件列的（`repos/*/*/{overview,setup,test}.md`），**不要**写成 `repos/*/*/**` —— 那会把 `repos/.source/` 下的源码一并放行。

## 第 3 步：工作台 init（外层）

**唯一布局：只在外层 init，各仓库里都不要 init。** 项目根 = 整个工作区，一份 state、一份契约、一条流水线：

```
python3 .claude/hooks/wb.py init --name <需求名>
```

init 检测到 `repos/.source/*` 会把角色范围按**项目**重算，并点名认不出的项目 —— 按它给的命令 `config set` 认领，再配 `gate_commands`（子 shell 分别 cd）。**不调这两处是静默出错**：认不出的项目谁都写不了，门禁命令在外层根跑不了。

init 还会为每个还没有画像三件套的仓库建一个 `仓库画像：<项目>/<仓库>` 任务（分析师的写入范围是 `repos/<项目>/<仓库>/**`），并在输出里提示**先派这批** —— 画像与需求分析是两件事，需求驱动的那次只覆盖需求相关部分，产不出整仓事实。派发方式与常规任务相同（`task start` → analyst → `task done`），analyze 门禁 `repos_notes_exist` 兜底点名漏掉的仓库。新增仓库后补建同一条命令：`task add --title "仓库画像：<项目>/<仓库>" --role analyst --phase analyze --write-scopes "repos/<项目>/<仓库>/**"`。

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

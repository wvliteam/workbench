# 工作台改动审查（2026-09-09）

审查对象：仓库未提交改动 —— wb-init 脚本提为工作区级 `scripts/`（`repos_apply.py`）、新增 TUI（`repos_tui.py`）、清单 `repos.json`、Codex 端 hook 目录与软链、wb.py 新增 `scripts/` `repos.json` `.vscode/` 守卫前缀，及对应文档。

**结论：重构方向合理，落地有 20 项已确认问题。** 最重一批不是风格问题，是安全与数据丢失。其中两条（未跟踪软链 = Codex 守卫在干净环境静默失效、脚本执行可绕过新增只读守卫）意味着本次改动的安全面在部分场景比改动前更差，需在合并前修复。

审查方法：10 个发现角度 + 16 个候选验证器 + 1 轮独立清扫 + 5 个清扫验证器，21 项候选全部验证，20 项确认（1 项 PLAUSIBLE-weak 剔除）。所有结论均经独立代码路径验证。

## 安全类（4 项，最高优先级）

### 1. `.codex/hooks/wb.py` 是未跟踪软链，全新 clone 下 Codex 守卫全线静默失效

**文件**：`.codex/hooks.json:10` 及四个 hook 的根发现逻辑

`.codex/hooks.json` 里每个 Codex hook 命令都先检查 `$root/.codex/hooks/wb.py` 是否存在再决定是否加载。但该文件是**未跟踪软链**（git status `?? .codex/hooks/`，`git ls-files` 空，未被 gitignore）。全新 clone：while 循环沿父目录一路爬到 `/` 都找不到文件，最终 `if` 永不触发，四个 hook（SessionStart/PreToolUse/PostToolUse/SubagentStop）全部 no-op 且 exit 0 无任何报错。

**失败场景**：干净 checkout 或 CI 下跑 Codex —— 权限守卫、契约冻结、角色执法全瘫，角色可以写冻结契约、`.workbench/state.json`、甚至 `.claude/hooks/wb.py` 本身，还能跑特权 wb.py 子命令。旧 gate 挂在已跟踪的 `.claude/hooks/wb.py` 上；新 gate 依赖一个只有当前工作区碰巧存在的软链。

**修复**：`git add .codex/hooks/wb.py`，或让根发现逻辑回退到已跟踪文件。

### 2. 角色可执行脚本绕过 `scripts/` `repos.json` `.vscode/` 只读守卫

**文件**：`.claude/hooks/wb.py:3112` 附近（Bash 写目标解析）

`python3 scripts/repos_apply.py --root .` 没有 Bash 解析器能识别的写目标（不带 `-c` 的 python3 不算写命令，不触发 UNCERTAIN/BASH_WRITE），`_check_write_target` 根本不执行，命令被放行。

**失败场景**：backend-developer 执行 `python3 scripts/repos_apply.py --config /tmp/evil.json --root .` —— 脚本照写被守卫的 `.vscode/settings.json`、`.workbench/<名>.code-workspace`、建任意 `repos/*` 软链；`repos_tui.py` 同理可写 `repos.json`。新增守卫只挡直接 Write/Edit/cp/sed，挡不住角色还能执行的脚本的程序化写入。

**修复**：对角色（非主线程）执行已知脚本（repos_apply.py / repos_tui.py）显式拒绝，或让脚本支持受约束的只读/交互子集。

### 3. `repos.json` 文件名前缀匹配过宽过窄

**文件**：`.claude/hooks/wb.py:2843`（GUARDED_PREFIXES 匹配逻辑）

GUARDED_PREFIXES 其余条目都是目录（带尾斜杠），`repos.json` 是裸文件名，却统一用 `rel.startswith(g)` 匹配。于是 `repos.json.bak`、`repos.json5`、目录 `repos.json/` 都被当清单处理；反过来配置 `repos.json5/**` scope 会被误放行并匹配兄弟路径。

**修复**：精确匹配 `rel == 'repos.json'`（或斜杠形式），与其他目录条目区分。

### 4. `scripts/` `.vscode/` 硬编码为全局保留，误伤单项目适配场景

**文件**：`.claude/hooks/wb.py:222`（GUARDED_PREFIXES 定义）

`scripts/`、`.vscode/`、`repos.json` 对所有运行 wb.py 的工作区无条件保留，包括 README「适配到自己的项目」流程（把 `.claude/` 拷进普通项目根）创建的单项目工作区 —— 那里顶层 `scripts/` 只是项目自己的代码。

**失败场景**：适配项目里 backend-developer（默认 `*.py`）写 `scripts/deploy.py` → 范围收窄到 `startswith('scripts/')` → `["（无）"]` → 被拒，报「scripts/ 是工作区级公共资源」—— 而此处并没有多仓库布局，也无工作区级 opt-out。角色被静默锁在自己的项目目录外。

**修复**：守卫条件化于 workbench 布局（检测 `repos/` 存在与否）。

## 数据丢失类（3 项）

### 5. TUI 静默销毁损坏但可解析的 repos.json

**文件**：`scripts/repos_tui.py:46` 与 `:400`

`load_entries` 对任何合法 JSON 但形状错误（`{"repos": "abc"}`、`{"repos": {}}`、`42`、`"str"`）返回 `[]`；tui() 预检只 catch OSError/JSONDecodeError。按 `w` → `validate_entries([])` 通过 → `save_entries` 把原文件重写成 `{"repos": []}`，无任何报错。repos_apply.load_config 能拒同样文件，TUI 是唯一静默销毁路径。

**修复**：复用 repos_apply.load_config（加载即校验），tui() 里显示错误。

### 6. save_entries 无条件丢顶层 key

**文件**：`scripts/repos_tui.py:49`

无条件写 `{"repos": entries}`，`$schema`、`comment` 等顶层 key 首次 `w` 即被删；repos_apply.load_config 明确接受的 bare-array 顶层形式也被改写。round-trip 非内容恒等。

**修复**：保顶键（加载时存原 dict，写回时只替换 `repos` 字段）。

### 7. modal 编辑 Backspace 后打字清空剩余默认值

**文件**：`scripts/repos_tui.py:142-148`

`replaced` 标志只在输入字符时置位，Backspace（142-145）与方向键不算。默认 `'abc'`：按 Backspace（buf→`'ab'`，replaced 仍 False）再打 `'x'` → 命中 `if not replaced:` 清空 → 结果 `'x'` 而非 `'abx'`，两个字符静默丢失。任何「先删后打」编辑都丢未动部分。

**修复**：编辑类按键同样置位 `replaced`。

## 静默误行为类（8 项）

### 8. TUI 报「落地完成」但 remote URL 编辑从未应用

**文件**：`scripts/repos_tui.py:392` 与 `scripts/repos_apply.py:189`

materialize 对任何有 `.git` 的路径返回 exists（exit 0），从不比对 manifest remote 与 checkout 实际 origin。TUI 只看 rc==0 报「落地完成」。

**失败场景**：仓库换主机、TUI 里改 remote、按 w → repos.json 记了新 URL，但 repos_apply 见 repos/frontend 已存在 → EXISTS → 0 errors → 「落地完成」。clone 永远指旧 remote，manifest 与磁盘永久不一致且无警告。

**修复**：exists 分支比对 manifest remote 与 `git remote get-url origin`，不一致时报告。

### 9. collect_repos 别名覆盖真实仓库

**文件**：`scripts/repos_apply.py:189`

`found` 以解析后路径为 key，link 条目解析到已存在的真实 clone（如 link `shared` → 真实 `repos/backend`）时 add() 覆盖后者的 (rel, name, path)。生成的 .code-workspace folders 只列 `shared`，git.scanRepositories 只列 `repos/shared` —— 真实仓库从 IDE 工作区静默消失。

**修复**：重复解析路径时报错而非覆盖，或只保留真实仓库。

### 10. 非 dict 清单条目让 TUI 崩溃

**文件**：`scripts/repos_tui.py:227`

`{"repos": [42]}` 或裸数组：load_entries 原样返回非 dict 列表，draw() 首次 entry_name(42) → `AttributeError: 'int' object has no attribute 'get'` → traceback（tui() 只 catch 两类异常）。repos_apply 本会报「repos[0] 不是对象」。

**修复**：渲染前用 load_config 校验，加载即报错。

### 11. run_init 失败留「已保存未应用」残局 + 假消息

**文件**：`scripts/repos_tui.py:293` 与 `:388`

`w` 处理器先写 manifest（388）再跑 run_init，其 `subprocess.run`（293）无包装：repos_apply.py 缺失/抛错 → FileNotFoundError 穿透，终态未恢复。期间 input() 只 catch EOFError：Ctrl-C 打印「已中断（未保存改动已丢弃）」—— 假话，`w` 已写盘且 dirty=False。

**修复**：run_init 包 try/except 并重绘；input 捕获 KeyboardInterrupt 如实提示。

### 12. derive_name 对 SCP 风格 flat remote 整串返回

**文件**：`scripts/repos_apply.py:66`

`rsplit("/",1)[-1].removesuffix(".git")`：`git@gitlab.com:payments-core.git`（合法 clone 语法，无路径段）整串返回 → `git@gitlab.com:payments-core` 过不了 NAME_RE。与 TUI「回车 = 从地址末段派生」提示矛盾，无 name 的此类条目 w 时才以费解消息被拒。

**修复**：对 `host:path.git` 形式取冒号后段派生。

### 13. selfcheck 软链断言是假的

**文件**：`.claude/hooks/wb.py:4448`

断言跑在 tempdir，`.codex/hooks/wb.py` 不存在，`Path.resolve()` 从不真正跟软链，只测字面 `.codex/` 前缀路径 —— 生产路径（软链解析到 `.claude/hooks/wb.py`）从未被断言，也没断言软链存在且已跟踪。问题 1 的软链缺失时 selfcheck 照样「全部通过」，虚假信心。附带：代码注释「Write 的 file_path 不解析软链」也是错的，resolve_target 确实跟软链。

**修复**：断言针对真实工作区路径的软链存在与解析结果。

### 14. link 条目 name 留空的死路

**文件**：`scripts/repos_tui.py:242`

flow_add 邀请 link 条目 name 留空（「回车 = 从地址末段派生」），但 derive_name 只从 remote 派生，从不看 link。nameless link 每次都被 validate_entries 拒（「name 非法」），flow_edit 清空 name 同样死路，表内渲染空名。

**修复**：link 条目允许 name 缺失，或从 link 路径末段派生。

### 15. draw() 几何 off-by-border

**文件**：`scripts/repos_tui.py:216`

`src_w = w-49` 使行恰好 w-2 字符，但行经 `fit(line, w-4)` 绘制 → 每行被截到 `s[:w-8]+"..."`：Branch 列尾巴全切（`'main...'` 即使没截断也显示），表头（w-2 且 CJK「地址 / 来源」约双倍宽）溢出右边框。任何终端下表头与数据列都对不齐，CJK 仓库名加重。

**修复**：行宽统一按 `w-4` 计算或放宽列宽预算。

## 次等（已确认，未进 15 槽）

| 文件 | 问题 |
| --- | --- |
| `scripts/repos_tui.py` | modal 在极小终端下 `newwin()` 崩溃 |
| `scripts/repos_tui.py` | `locale.setlocale` 未捕获 |
| `scripts/repos_tui.py` | modal_detail 提示被覆盖 |
| `scripts/repos_tui.py` | KEY_ENTER 被吞 |
| `docs/wb-init.md` | 残留过期 `--init`/布局A 指令 |

## 修复优先级

1. **问题 1、2**：守卫回归，合并前必修 —— Codex 端安全网在干净环境静默失效；脚本可绕过新增只读守卫。
2. **问题 3、4**：守卫边界判定错误，与 1、2 同批改。
3. **问题 5、6、7**：TUI 数据丢失，首次上手即可踩中。
4. **其余**：静默误行为，按落地节奏排。

改完跑 `python3 .claude/hooks/wb.py selfcheck` 确认，其中问题 13 的断言升级须覆盖问题 1 的场景。

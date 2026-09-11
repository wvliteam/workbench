# `WB_ROOT` 指向的目录里没有 `.workbench/` 时会被静默忽略，命令落到真实工作区

`wb_core.find_root()` 对 `WB_ROOT` 与 `CLAUDE_PROJECT_DIR` 一视同仁，都要求该目录已经存在 `.workbench/` 才算数（`if v and (Path(v) / ".workbench").is_dir()`）。用一个空目录当隔离夹具（`mktemp -d` 的结果、还没 init 过的目录）去跑 `wb.py`，环境变量被跳过、回落到从 cwd 向上找最近的 `.workbench/` —— 于是 `task add` / `role set` 这类不经过 `cmd_init` 的命令会落进真实工作区，**静默污染真实状态，工具不报错**。

## 依据

2026-09-11 本会话实测：

- 读源码确认分支：`.claude/hooks/wb_core.py:49-52` —— `for var in ("WB_ROOT", "CLAUDE_PROJECT_DIR")`，命中条件 `Path(v) / ".workbench"` 是目录；不命中就继续下面 `cur.parents` 的向上查找（`wb_core.py:53-56`）。docstring 自己写明这是故意的：「要求目录里确实有 `.workbench/`：环境变量指错时不静默接管，仍走向上查找」。
- 后果实测：`WB_ROOT=<空目录>` 下跑 `task add` / `role set` 成功写真实状态（真实 state 多出一个 todo 任务、`.workbench/role` 被写成某个角色）。`init` 因为「flow 已存在 state.json」die（`.claude/hooks/wb_cli.py:51-52`），是唯一会出声的一条。
- `cmd_init` 本身不读 `WB_ROOT`：根只取 `--root` 或 cwd（`wb_cli.py:42`）—— 所以「先 init 再 add」在夹具里也不成立。
- 污染看不见：`.workbench/` 在 `.gitignore:2`，`git status` 干净；只能靠 `wb.py task list` / `wb.py status` 发现。
- 连带效应：`.workbench/role` 被写后兜底主线程（`wb_guard.py:256-281`，主线程无 `agent_type` 时读该文件），主线程随后跑 `task skip` / `role clear` 会被按「角色跑特权子命令」拒掉（自锁）。机制内的解除路径是 `python3 .claude/hooks/wb.py hook subagent-stop` —— 无 doing 任务时它 `rolef.unlink(missing_ok=True)` 清掉 role 锁（`wb_guard.py:1090`、`:1103`）。

## 适用范围

本工作台 `wb_core.find_root()` 当前实现（`WB_ROOT` 与 `CLAUDE_PROJECT_DIR` 共用同一条存在性判据）。适用于任何用临时目录做隔离夹具的场景：自检脚本、回归脚本、CI、以及为验证 wb.py 行为而临时造的目录。

安全做法二选一：

```bash
mkdir -p "$dir/.workbench" && WB_ROOT="$dir" python3 .claude/hooks/wb.py init --name x
# 或：cd "$dir" && mkdir -p .workbench && python3 <绝对路径>/wb.py init --name x   # 按 cwd 向上找到刚建的 .workbench/
```

不能用做法：`WB_ROOT=$(mktemp -d) python3 .../wb.py ...`（目录里没有 `.workbench/`，环境变量形同不存在）。

自查污染：

```bash
python3 .claude/hooks/wb.py task list    # 多出没建过的 todo
python3 .claude/hooks/wb.py status       # 根那一行 + flow 状态
cat .workbench/role                      # 不该有内容时出现了角色名 = 被误写
```

## 失效条件

`find_root()` 改了环境变量分支（例如 `WB_ROOT` 不再要求目录里已有 `.workbench/`、或在缺失时直接报错而不是回落），或 `cmd_init` 改为也读 `WB_ROOT`。改的是 `.claude/hooks/wb_core.py` / `wb_cli.py`，条目要按新实现复核。

## 来源

flow main，2026-09-11 会话（审查 wb.py 行为时实测踩到），由 knowledger 角色沉淀。

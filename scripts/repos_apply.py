#!/usr/bin/env python3
"""wb-init：按 repos.json 清单把代码仓库落到 repos/，并生成 VS Code 多根工作区。

交互式编辑清单用同目录 repos_tui.py（curses 界面），本脚本是纯批处理 ——
按已有清单幂等落地，不做任何交互。

用法（在工作区根）：

    python3 scripts/repos_apply.py [--root PATH] [--config PATH] [--json]

清单 `repos.json` 在工作区根（进 git，团队共享）：

    {
      "repos": [
        { "name": "frontend", "remote": "git@github.com:org/frontend.git" },
        { "name": "backend",  "remote": "https://github.com/org/backend.git", "branch": "main" },
        { "name": "shared",   "link": "/Users/me/code/shared-libs" }
      ]
    }

三条行为准则（照 ROMA `materialize_repo_selection.py` 的经验）：

- 幂等：已存在的 checkout 不覆盖，按 exists 上报；IDE 配置**始终刷新**。
- 仓库发现以磁盘为准：`.code-workspace` 的 folders 来自扫描 `repos/` 下带 `.git`
  的目录（含软链），clone 失败的仓库不会混进 IDE 配置。
- 失败不伪装成功：clone / 软链 / init 失败按 error 上报，最终退出码非 0。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

# 合并进 .vscode/settings.json 的 git 发现配置。git.scanRepositories 只管理
# repos/ 前缀（按磁盘扫描重写），用户自己的条目原样保留。
IDE_GIT_SETTINGS = {
    "git.autoRepositoryDetection": "subFolders",
    "git.repositoryScanMaxDepth": 6,
}

# 单段路径名：防 ../ 穿越、防混入路径分隔符、防以 . 开头（.git 等）。
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

CONFIG_SAMPLE = """{
  "repos": [
    { "name": "frontend", "remote": "git@github.com:org/frontend.git" },
    { "name": "backend", "remote": "https://github.com/org/backend.git", "branch": "main" },
    { "name": "shared-libs", "link": "/Users/me/code/shared-libs" }
  ]
}"""


def run(cmd: list, cwd: Path | None = None) -> tuple[bool, str]:
    """跑一条命令，返回 (成功, 合并输出)。"""
    r = subprocess.run([str(c) for c in cmd],
                       cwd=str(cwd) if cwd else None,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return r.returncode == 0, (r.stdout or "").strip()


def derive_name(remote: str) -> str:
    """remote 省略 name 时取最后一段（去 .git）。

    SCP 风格 remote（`git@gitlab.com:payments-core.git`，无路径段）取冒号后段 ——
    旧逻辑整串返回，`git@gitlab.com:payments-core` 过不了 NAME_RE。
    """
    seg = str(remote or "").rstrip("/")
    if ":" in seg and "://" not in seg:
        seg = seg.rsplit(":", 1)[-1]
    return seg.rsplit("/", 1)[-1].removesuffix(".git")


PROJECT_HINT = '清单条目补一个 "project" 字段，或让 remote 能推导出来'


def derive_project(remote: str) -> str:
    """从 remote 推导归属项目；推不出返回空串。

    iCode 的控制台 URL 与 git URL 都是 `.../<org>/<项目>/<仓库>` 形态，本仓的项目就是
    中间那一段（`baidu/bddev/maphotel` → `bddev`），角色认领按项目一级判定。两段式
    （`org/repo`）没有项目层级，取 org —— 至少不同项目的同名仓库不会互相覆盖。

    推不出时**返回空串让调用方报错**，不猜：猜错的项目会让这个仓库谁都认领不到，
    而症状要到 develop 阶段才显形（一次权限拒绝）。
    """
    seg = str(remote or "").rstrip("/")
    if "://" in seg:
        seg = seg.split("://", 1)[1]
    if ":" in seg:                      # scp 风格：git@host:org/a/b.git
        seg = seg.split(":", 1)[1]
    parts = [p for p in seg.split("/") if p]
    if "tree" in parts:                 # iCode 控制台 URL 的尾巴 /tree/<branch>
        parts = parts[:parts.index("tree")]
    if len(parts) >= 3:
        return parts[-2]
    if len(parts) == 2:
        return parts[0]
    return ""


def validate_entries(entries: list) -> str | None:
    """逐条校验清单条目，返回错误信息或 None。文件读入与交互式内存编辑共用同一套规则。"""
    seen: set[str] = set()
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            return f"repos[{i}] 不是对象"
        name = str(e.get("name") or derive_name(e.get("remote", "") or e.get("link", "")))
        if not name or not NAME_RE.match(name):
            return (f"repos[{i}] 的 name 非法：{name!r}（须为单段路径名，"
                    "字母数字开头，不含 / 和空白）")
        project = str(e.get("project") or derive_project(str(e.get("remote") or "")))
        if not project or not NAME_RE.match(project):
            return (f"repos[{i}]（{name}）的 project 缺失或推导不出：{project!r}"
                    f"（须为单段路径名 —— {PROJECT_HINT}）")
        full = f"{project}/{name}"
        if full in seen:
            return f"repos[{i}] 的 project/name 重复：{full}"
        seen.add(full)
        remote = str(e.get("remote") or "")
        link = str(e.get("link") or "")
        if not remote and not link:
            return f"repos[{i}]（{name}）缺 remote 或 link"
        if remote and link:
            return f"repos[{i}]（{name}）remote 与 link 只能二选一"
        if remote.startswith("-"):
            return f"repos[{i}]（{name}）的 remote 以 - 开头，会被 git 当成选项"
    return None


def load_config(path: Path) -> tuple[list[dict], str | None]:
    """读清单并校验条目。返回 (条目列表, 错误信息)。"""
    if not path.is_file():
        return [], f"清单不存在：{path}\n先在工作区根创建 repos.json，格式：\n{CONFIG_SAMPLE}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [], f"清单不是有效 JSON：{path}（{e}）"
    entries = data.get("repos", []) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return [], "repos.json 顶层应为对象（含 repos 数组）或数组"
    err = validate_entries(entries)
    if err:
        return [], err
    return entries, None


def materialize(entry: dict, repos_dir: Path) -> dict:
    """把一个条目落到 repos/.source/<项目>/<仓库>：已存在不覆盖，clone 或软链。

    源码入口与画像（`repos/<项目>/<仓库>/`）是**两棵树**：这里只碰源码那一棵。
    画像由 `wb.py init` 建画像任务、analyst 产出，不归本脚本。
    """
    name = str(entry.get("name") or derive_name(entry.get("remote", "") or entry.get("link", "")))
    project = str(entry.get("project") or derive_project(str(entry.get("remote") or "")))
    full = f"{project}/{name}"
    target = repos_dir / project / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists():
        try:
            resolved = target.resolve(strict=True)
        except OSError:
            return {"repo": full, "action": "materialize", "status": "error",
                    "detail": f"目标已存在但是断链，不覆盖：{target}"}
        if (resolved / ".git").exists():
            expected = str(entry.get("remote") or "").strip()
            if expected:
                ok, origin = run(["git", "-C", str(resolved), "remote", "get-url", "origin"])
                if ok and origin and origin != expected:
                    return {"repo": full, "action": "materialize", "status": "error",
                            "detail": (f"{target} 已存在，但 origin 是 {origin}，与清单"
                                       f"remote（{expected}）不一致，未改动。如需换源："
                                       f"git -C {target} remote set-url origin {expected}")}
            return {"repo": full, "action": "materialize", "status": "exists",
                    "detail": str(target)}
        return {"repo": full, "action": "materialize", "status": "error",
                "detail": f"目标已存在但不是 git 仓库，不覆盖：{target}"}

    if str(entry.get("link") or ""):
        return link_repo(full, str(entry["link"]), target)
    return clone_repo(entry, full, str(entry.get("remote") or ""), target)


def link_repo(name: str, link: str, target: Path) -> dict:
    """清单 link：本机已有 checkout，软链接入，不复制代码。"""
    root = target.parent.parent
    src = Path(link)
    if not src.is_absolute():
        src = (root / src).resolve()
    if not src.exists():
        return {"repo": name, "action": "link", "status": "error",
                "detail": f"link 路径不存在：{src}"}
    resolved = src.resolve()
    if not (resolved / ".git").exists():
        return {"repo": name, "action": "link", "status": "error",
                "detail": f"link 路径不是 git 仓库：{resolved}"}
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.symlink_to(resolved)
    except OSError as e:
        return {"repo": name, "action": "link", "status": "error",
                "detail": f"创建软链失败：{e}"}
    return {"repo": name, "action": "link", "status": "ok",
            "detail": f"{target} -> {resolved}"}


def clone_repo(entry: dict, name: str, remote: str, target: Path) -> dict:
    """git clone。<remote> 逐字使用（user@ 是 SSH 登录账号，不要改写）。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone"]
    branch = str(entry.get("branch") or "")
    if branch:
        cmd += ["--branch", branch]
    # `--` 防远程地址被解析成 git 选项；remote 里带 user@ 时原样保留。
    cmd += ["--", remote, str(target)]
    ok, out = run(cmd)
    if not ok:
        lines = out.splitlines()
        detail = "\n".join(lines[-5:]) if lines else "(无输出)"
        return {"repo": name, "action": "clone", "status": "error",
                "detail": f"git clone 失败：\n{detail}"}
    return {"repo": name, "action": "clone", "status": "ok", "detail": str(target)}


def collect_repos(root: Path) -> list[tuple[str, str, Path]]:
    """扫描源码挂载点下真实的 git 仓库（含软链），返回 [(相对路径, 名字, 绝对路径)]。

    扫的是 `repos/.source/<项目>/<仓库>`，名字取 `项目/仓库` —— 它是角色认领与
    `repos/index.md` 登记的单元，光用仓库名会和别的项目下的同名仓库混。
    IDE 配置以这里的结果为准，不信任清单 —— clone 失败的仓库不会出现在里面。
    不进入已确认的 git 根继续下钻。
    """
    found: dict[Path, tuple[str, str, Path]] = {}
    source = root / "repos" / ".source"

    def add(entry: Path) -> None:
        try:
            resolved = entry.resolve(strict=True)
        except OSError:
            return
        if not (resolved / ".git").exists():
            return
        rel = entry.relative_to(root).as_posix()
        name = (entry.relative_to(source).as_posix()
                if source in entry.parents else entry.name)
        if entry.is_symlink():
            # 软链不覆盖真实目录：link 别名解析到已挂载的真实仓库时，真实仓库
            # 是权威（IDE workspace 按真实路径登记），同源软链只登记一次。
            found.setdefault(resolved, (rel, name, resolved))
        else:
            found[resolved] = (rel, name, resolved)

    def walk(d: Path) -> None:
        try:
            entries = sorted(d.iterdir(), key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            if e.is_symlink():
                add(e)
            elif e.is_dir():
                if (e / ".git").exists():
                    add(e)
                else:
                    walk(e)

    if source.is_dir():
        walk(source)
    return sorted(found.values(), key=lambda t: t[0])


def ensure_workspace_file(root: Path, repos: list[tuple[str, str, Path]]) -> Path:
    """生成多根 .code-workspace：folders = 工作区根 + 全部挂载仓库。

    放 .workbench/（gitignore）里，绝对路径不进 git —— 每台机器自理。
    """
    wb = root / ".workbench"
    wb.mkdir(parents=True, exist_ok=True)
    name = root.resolve().name or "workspace"
    path = wb / f"{name}.code-workspace"
    folders = [{"name": name, "path": root.resolve().as_posix()}]
    folders += [{"name": n, "path": a.as_posix()} for _, n, a in repos]
    path.write_text(json.dumps({"folders": folders}, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def ensure_vscode_settings(root: Path, repos: list[tuple[str, str, Path]]) -> tuple[Path | None, str | None]:
    """合并 .vscode/settings.json：注入 git 发现配置；无效 JSON 不覆盖只告警。"""
    path = root / ".vscode" / "settings.json"
    settings: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None, ".vscode/settings.json 不是有效 JSON —— 未改动，请手工修复后重跑"
        if not isinstance(loaded, dict):
            return None, ".vscode/settings.json 不是 JSON 对象 —— 未改动"
        settings = loaded
    settings.update(IDE_GIT_SETTINGS)
    existing = settings.get("git.scanRepositories")
    if not isinstance(existing, list):
        existing = []
    # repos/ 前缀内的条目按磁盘扫描重写；前缀外的用户条目原样保留。
    preserved = [x for x in existing
                 if isinstance(x, str) and x != "repos" and not x.startswith("repos/")]
    ours = ["repos"] + [rel for rel, _, _ in repos]
    settings["git.scanRepositories"] = list(dict.fromkeys([*ours, *preserved]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path, None


def warn_unignored(root: Path) -> str | None:
    """外层是 git 仓库且源码挂载点未被忽略时提醒 —— 挂进来的 checkout 会脏外层 status。

    判据是 `repos/.source`，不是整个 `repos/`：索引与画像要进 git，只有源码入口整棵忽略。
    """
    if not (root / ".git").exists():
        return None
    ok, _ = run(["git", "-C", root, "check-ignore", "-q", "repos/.source"])
    if not ok:
        return ("repos/.source/ 未被外层 git 忽略，挂进来的 checkout 会出现在外层 "
                "git status（参考工作台的 .gitignore：repos/.source/*）")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path.cwd(), help="工作区根（默认当前目录）")
    ap.add_argument("--config", type=Path, default=None, help="清单路径（默认 <root>/repos.json）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args()

    root = args.root.resolve()
    if not root.is_dir():
        print(f"ERROR: 工作区根不存在：{root}")
        return 1
    cfg_path = args.config if args.config else root / "repos.json"
    entries, cfg_err = load_config(cfg_path)
    if cfg_err:
        print(f"ERROR: {cfg_err}")
        return 1

    results: list[dict] = []
    repos_dir = root / "repos" / ".source"
    repos_dir.mkdir(parents=True, exist_ok=True)
    for e in entries:
        results.append(materialize(e, repos_dir))

    repos = collect_repos(root)
    ws_path = ensure_workspace_file(root, repos)
    vs_path, vs_warn = ensure_vscode_settings(root, repos)
    warnings = [w for w in [vs_warn, warn_unignored(root)] if w]

    # 清单里的 description 只用于显示（也是 repos/index.md 的种子）；缺失不影响挂载。
    # 仓库地图与索引校验在 wb.py status / role scopes，这里带出来省一次翻清单。
    descs = {str(e.get("name") or derive_name(e.get("remote", "") or e.get("link", ""))):
             str(e.get("description") or "").strip()
             for e in entries if isinstance(e, dict)}

    errors = [r for r in results if r["status"] == "error"]
    if args.json:
        print(json.dumps({
            "results": results,
            "descriptions": descs,
            "workspace_file": str(ws_path),
            "settings_file": str(vs_path) if vs_path else None,
            "warnings": warnings,
            "error_count": len(errors),
        }, ensure_ascii=False, indent=2))
    else:
        for r in results:
            d = f"　[{descs[r['repo']]}]" if descs.get(r["repo"]) else ""
            print(f"{r['status'].upper()}: {r['repo']} ({r['action']}) - {r['detail']}{d}")
        print(f"IDE workspace: {ws_path.relative_to(root).as_posix() if ws_path.is_relative_to(root) else ws_path}")
        if vs_path:
            print(f"IDE settings: {vs_path.relative_to(root).as_posix()}")
        for w in warnings:
            print(f"WARN: {w}")
        print(f"共 {len(results)} 项操作，{len(errors)} 个错误，{len(repos)} 个仓库已挂载")
        if repos and not (root / "repos" / "index.md").is_file():
            print("提示：repos/index.md 还没建 —— 每仓一行「仓库 | 职责 | 入口文档」，"
                  "建了 wb.py status 会给编排者一张分工图（缺行 / 死链会被点名）")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

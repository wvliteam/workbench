#!/usr/bin/env python3
"""repos-tui：curses 全屏界面管理 repos.json 仓库清单，配置完直接落地。

在工作区根跑（需要真实终端）：

    python3 scripts/repos_tui.py [--root PATH]

界面：边框 + 标题栏 + 表格（↑↓/j k 上下选中，反色高亮）+ 底部键栏。

    ↑↓/j k   上下选中        a   添加（分步模态输入）
    e        编辑选中条目     d   删除选中条目（y 确认）
    Enter    查看详情         w   写入 repos.json 并落地（clone/软链 + workspace）
    q        退出（未保存会确认）

校验、clone/软链、workspace/vscode 生成全部复用同目录 repos_apply.py，
本脚本只负责「编辑清单」这层界面，不重复实现重活。Claude 侧只跑非交互自测
`repos_tui.py --selftest`，别在工具里直接跑交互模式（需要 tty，会挂起）。
"""

from __future__ import annotations

import argparse
import curses
import json
import locale
import subprocess
import sys
from pathlib import Path

# 与 repos_apply.py 同目录，直接复用其校验与派生规则（单一来源）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from repos_apply import derive_name, load_config, validate_entries  # noqa: E402

INIT_SCRIPT = Path(__file__).resolve().parent / "repos_apply.py"

KEY_HINTS = "↑↓ Select   a Add   e Edit   d Delete   Enter Detail   w Save+Apply   q Quit"

# ---- 纯逻辑层：不碰 curses，自测直接测这里 ----

def load_entries(cfg_path: Path) -> list[dict]:
    """读 repos.json；不存在返回空列表。

    复用 repos_apply.load_config 的校验 —— 形状错误（非对象/非数组）或条目非法时
    抛 ValueError。TUI 绝不能把一个解析不了或损坏的清单静默当成空表：按 `w` 会把
    原文件覆写成 {"repos": []}。
    """
    if not cfg_path.is_file():
        return []
    entries, err = load_config(cfg_path)
    if err:
        raise ValueError(err)
    return entries


def save_entries(cfg_path: Path, entries: list[dict]) -> None:
    """写回 repos.json。已存在的对象顶层保留其它 key，只替换 repos 字段。

    裸数组顶层没有 key 可保，规范化成 {repos:[...]} 是唯一可写形式。
    """
    top: dict = {"repos": entries}
    if cfg_path.is_file():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            top = {**data, "repos": entries}
    cfg_path.write_text(json.dumps(top, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")


def entry_name(e: dict) -> str:
    return str(e.get("name") or derive_name(e.get("remote", "")))


def entry_type(e: dict) -> str:
    return "link" if e.get("link") else "clone"


def entry_source(e: dict) -> str:
    return str(e.get("link") or e.get("remote") or "")


def apply_add(entries: list[dict], new: dict) -> tuple[list[dict] | None, str | None]:
    candidate = entries + [new]
    err = validate_entries(candidate)
    return (None, err) if err else (candidate, None)


def apply_edit(entries: list[dict], idx: int, patch: dict) -> tuple[list[dict] | None, str | None]:
    """patch 覆盖到第 idx 条：值为 None 的键删除，其余写入。"""
    if not 0 <= idx < len(entries):
        return None, f"序号越界：{idx + 1}"
    candidate = [dict(e) for e in entries]
    merged = candidate[idx]
    for k, v in patch.items():
        if v is None:
            merged.pop(k, None)
        else:
            merged[k] = v
    err = validate_entries(candidate)
    return (None, err) if err else (candidate, None)


def apply_delete(entries: list[dict], idx: int) -> tuple[list[dict] | None, str | None]:
    if not 0 <= idx < len(entries):
        return None, f"序号越界：{idx + 1}"
    return entries[:idx] + entries[idx + 1:], None


def fit(s: str, n: int) -> str:
    """按显示宽度截断，超长补 ...。"""
    return s if len(s) <= n else s[: max(1, n - 3)] + "..."


# ---- TUI 基件 ----

def safe(fn, *a, **k) -> None:
    """curses addstr 写到右下角越界会抛 error，统一吞掉。"""
    try:
        fn(*a, **k)
    except curses.error:
        pass


def modal_input(scr, title: str, label: str, default: str = "") -> str | None:
    """单字段模态输入框。Enter 确认返回文本，Esc 取消返回 None。

    用 get_wch() 拿宽字符（中文整字输入），依赖 main 里 setlocale 生效。
    """
    sh, sw = scr.getmaxyx()
    if sw < 30 or sh < 8:   # 窗口太小 newwin 会抛 curses.error，直接取消
        return None
    mw, mh = min(72, max(40, sw - 4)), 5
    wy, wx = max(0, (sh - mh) // 2), max(0, (sw - mw) // 2)
    win = curses.newwin(mh, mw, wy, wx)
    win.keypad(True)  # 方向键/HOME/END 转 KEY_* 常量；不开的话先收到裸 \x1b 被当 Esc 取消
    buf: list[str] = list(default)
    pos = len(buf)
    # type-to-replace：带默认值的框，首个输入字符清掉默认值再写。
    # 回车保留默认；想基于默认改，先 Backspace 清空再输入。
    replaced = not default
    curses.curs_set(1)
    try:
        while True:
            win.erase()
            win.border()
            safe(win.addstr, 0, 2, f" {title} ", curses.A_BOLD)
            safe(win.addstr, 1, 2, fit(label, mw - 4))
            safe(win.addstr, 2, 2, fit("".join(buf), mw - 5) + " ")
            safe(win.addstr, 3, 2, "Enter 确认   Esc 取消", curses.A_DIM)
            win.move(2, 2 + min(pos, mw - 6))
            k = win.get_wch()
            if isinstance(k, str):
                # get_wch 对普通字符/Enter/Esc 返回 str，对方向键等返回 int。
                if k in ("\n", "\r"):
                    return "".join(buf).strip()
                if k == "\x1b":
                    return None
                if k == "\x7f":  # Backspace（macOS 终端送 DEL）
                    # 删除也是「开始编辑」：不清整串，之后输入改为插入。
                    replaced = True
                    if pos > 0:
                        buf.pop(pos - 1)
                        pos -= 1
                elif len(buf) < mw - 8:
                    if not replaced:
                        buf, pos, replaced = [], 0, True
                    buf.insert(pos, k)
                    pos += 1
                continue
            if k == curses.KEY_LEFT:
                replaced = True
                pos = max(0, pos - 1)
            elif k == curses.KEY_RIGHT:
                replaced = True
                pos = min(len(buf), pos + 1)
            elif k == curses.KEY_HOME:
                replaced = True
                pos = 0
            elif k == curses.KEY_END:
                replaced = True
                pos = len(buf)
    finally:
        curses.curs_set(0)


def modal_confirm(scr, msg: str, default_yes: bool = False) -> bool:
    """y/n 确认框。Esc/n/Enter(默认N) 拒绝，y 同意。"""
    sh, sw = scr.getmaxyx()
    if sw < 30 or sh < 5:   # 窗口太小 newwin 会抛 curses.error，安全拒绝
        return False
    mw = min(sw - 2, min(64, max(30, len(msg) + 8)))
    win = curses.newwin(3, mw, max(0, (sh - 3) // 2), max(0, (sw - mw) // 2))
    win.border()
    safe(win.addstr, 0, 2, " 确认 ", curses.A_BOLD)
    safe(win.addstr, 1, 2, fit(msg, mw - 4))
    hint = "[y/N]" if not default_yes else "[Y/n]"
    safe(win.addstr, 1, max(2, mw - len(hint) - 2), hint, curses.A_DIM)
    while True:
        k = win.getch()
        if k in (ord("y"), ord("Y")):
            return True
        if k in (ord("n"), ord("N"), 10, 13, 27, curses.KEY_ENTER):
            return False


def modal_detail(scr, entry: dict) -> None:
    """详情框：全字段展开不截断。任意键关闭。"""
    lines = json.dumps(entry, ensure_ascii=False, indent=2).splitlines() or ["{}"]
    sh, sw = scr.getmaxyx()
    if sw < 30 or sh < 8:   # 窗口太小 newwin 会抛 curses.error，直接关
        return
    mh = min(sh - 2, len(lines) + 4)
    mw = min(sw - 2, min(76, max(40, min(sw - 4, max(len(x) for x in lines) + 6))))
    win = curses.newwin(mh, mw, max(0, (sh - mh) // 2), max(0, (sw - mw) // 2))
    win.border()
    safe(win.addstr, 0, 2, f" {entry_name(entry)} ", curses.A_BOLD)
    for i, ln in enumerate(lines[: mh - 4]):   # 留两行给底部提示
        safe(win.addstr, 2 + i, 2, fit(ln, mw - 4))
    safe(win.addstr, mh - 2, 2, "任意键关闭", curses.A_DIM)
    win.getch()


# ---- 表格绘制 ----

def draw(scr, entries: list[dict], cursor: int, offset: int, status: str, dirty: bool) -> None:
    scr.erase()
    h, w = scr.getmaxyx()
    if h < 12 or w < 50:
        safe(scr.addstr, 1, 1, "终端太小（需 ≥12 行 × 50 列）")
        scr.border()
        scr.refresh()
        return
    scr.border()
    title = " Repo Manager "
    if dirty:
        title = " Repo Manager * "
    safe(scr.addstr, 0, 2, title, curses.A_BOLD)
    count = f" {len(entries)} repos "
    safe(scr.addstr, 0, max(3, w - 1 - len(count)), count)

    # 列宽：# 3、名称 20、类型 6、分支 10，地址列吃剩余宽度。
    # 行按 fit(line, w-4) 截（col 2 起，边框宽 w-2）；hdr 里「地址 / 来源」6 个
    # CJK 字符占 12 列，地址列预算再让 6 列，表头才不会溢出右边界。
    src_w = max(12, w - 4 - 3 - 2 - 20 - 2 - 6 - 2 - 2 - 10 - 6)
    hdr = f"{'#':>3}  {'Name':<20}  {'Type':<6}  {'地址 / 来源':<{src_w}}  {'Branch':<10}"
    safe(scr.addstr, 2, 2, fit(hdr, w - 4), curses.A_UNDERLINE)

    first_y, last_y = 4, h - 5  # 表格行区间；h-4 状态行，h-3 分隔线，h-2 键栏
    vis = last_y - first_y + 1
    for vi in range(vis):
        i = offset + vi
        if i >= len(entries):
            break
        e = entries[i]
        line = (f"{i + 1:>3}  {fit(entry_name(e), 20):<20}  {entry_type(e):<6}  "
                f"{fit(entry_source(e), src_w):<{src_w}}  {fit(str(e.get('branch') or '-'), 10):<10}")
        attr = curses.A_REVERSE if i == cursor else curses.A_NORMAL
        safe(scr.addstr, first_y + vi, 2, fit(line, w - 4), attr)

    safe(scr.addstr, h - 4, 2, fit(status, w - 4), curses.A_DIM)
    scr.hline(h - 3, 1, curses.ACS_HLINE, w - 2)
    safe(scr.addstr, h - 2, max(1, (w - len(KEY_HINTS)) // 2), fit(KEY_HINTS, w - 2))
    scr.refresh()


# ---- 交互流程 ----

def flow_add(scr) -> tuple[dict | None, str]:
    """分步收集新条目。返回 (条目, 状态文案)；取消返回 (None, 状态)。"""
    name = modal_input(scr, "添加仓库 · 名称", "名称（回车 = 从地址末段派生）")
    if name is None:
        return None, "已取消"
    kind = modal_input(scr, "添加仓库 · 接入方式", "接入方式：1 = git clone，2 = 本地软链", "1")
    if kind is None:
        return None, "已取消"
    entry: dict = {}
    if name:
        entry["name"] = name
    if kind == "2":
        link = modal_input(scr, "添加仓库 · 本地路径", "本地已有 checkout 的路径")
        if link is None:
            return None, "已取消"
        if not link:
            return None, "未填路径，未添加"
        entry["link"] = link
    else:
        remote = modal_input(scr, "添加仓库 · 远程地址", "远程地址（git@… / https://…，user@ 逐字保留）")
        if remote is None:
            return None, "已取消"
        if not remote:
            return None, "未填地址，未添加"
        entry["remote"] = remote
        branch = modal_input(scr, "添加仓库 · 分支", "分支（回车 = 默认分支）")
        if branch is None:
            return None, "已取消"
        if branch:
            entry["branch"] = branch
    return entry, ""


def flow_edit(scr, cur: dict) -> tuple[dict, bool]:
    """逐字段编辑选中条目。返回 (patch, 是否取消)。回车保留原值，输入 - 清空字段。"""
    patch: dict = {}
    labels = [("name", "名称"), ("remote", "远程地址"), ("link", "本地软链路径"), ("branch", "分支")]
    for key, label in labels:
        val = modal_input(scr, "编辑（- 清空）", f"{label}（回车保留）", str(cur.get(key) or ""))
        if val is None:
            return {}, True
        if val == "":
            continue
        patch[key] = None if val == "-" else val
    return patch, False


def run_init(scr, root: Path) -> int:
    """挂起 curses 跑 repos_apply.py（输出直接进终端），按键后回界面。"""
    cmd = [sys.executable, str(INIT_SCRIPT), "--root", str(root)]
    curses.def_prog_mode()
    curses.endwin()
    print(f"$ {' '.join(cmd)}")
    try:
        rc = subprocess.run(cmd).returncode
    except KeyboardInterrupt:
        print("已中断：repos.json 已保存，但 repos_apply.py 未跑完（可能未落地）")
        rc = 130
    except OSError as e:
        print(f"启动 {INIT_SCRIPT.name} 失败：{e}")
        rc = -1
    try:
        input("\n按回车返回界面…")
    except (EOFError, KeyboardInterrupt):
        pass
    curses.reset_prog_mode()
    scr.redrawwin()
    scr.refresh()
    return rc


def tui_loop(scr, root: Path, cfg_path: Path) -> int:
    curses.curs_set(0)
    scr.keypad(True)
    entries = load_entries(cfg_path)
    dirty = False
    cursor = 0
    offset = 0
    status = "a 添加第一个仓库" if not entries else "↑↓ 选中，a/e/d/Enter 操作，w 写入并落地"

    while True:
        if not entries:
            cursor = offset = 0
        cursor = max(0, min(cursor, len(entries) - 1))
        h, _ = scr.getmaxyx()
        vis = max(1, (h - 5) - 4 + 1)
        if cursor < offset:
            offset = cursor
        elif cursor >= offset + vis:
            offset = cursor - vis + 1

        draw(scr, entries, cursor, offset, status, dirty)
        k = scr.getch()

        if k in (ord("q"), ord("Q")):
            if dirty:
                if not modal_confirm(scr, "有未保存改动，仍退出？"):
                    continue
            return 0
        elif k in (curses.KEY_UP, ord("k")):
            cursor = max(0, cursor - 1)
        elif k in (curses.KEY_DOWN, ord("j")):
            cursor = min(len(entries) - 1, cursor + 1) if entries else 0
        elif k == curses.KEY_HOME:
            cursor = 0
        elif k == curses.KEY_END:
            cursor = max(0, len(entries) - 1)
        elif k == curses.KEY_RESIZE:
            continue
        elif k in (ord("a"), ord("A")):
            entry, st = flow_add(scr)
            if entry is None:
                status = st or "未添加"
            else:
                updated, err = apply_add(entries, entry)
                if err:
                    status = f"拒绝：{err}"
                else:
                    entries = updated
                    dirty = True
                    cursor = len(entries) - 1
                    status = f"已添加 {entry_name(entry)}（未保存，w 落地）"
        elif k in (ord("d"), ord("D")):
            if not entries:
                continue
            if modal_confirm(scr, f"删除 #{cursor + 1} {entry_name(entries[cursor])}？"):
                entries, _ = apply_delete(entries, cursor)
                dirty = True
                status = "已删除（未保存，w 落地）"
            else:
                status = "已取消"
        elif k in (ord("e"), ord("E")):
            if not entries:
                continue
            patch, cancelled = flow_edit(scr, entries[cursor])
            if cancelled:
                status = "已取消"
            elif not patch:
                status = "无改动"
            else:
                updated, err = apply_edit(entries, cursor, patch)
                if err:
                    status = f"拒绝：{err}"
                else:
                    entries = updated
                    dirty = True
                    status = "已修改（未保存，w 落地）"
        elif k in (10, 13, curses.KEY_ENTER):
            if entries:
                modal_detail(scr, entries[cursor])
        elif k in (ord("w"), ord("W")):
            err = validate_entries(entries)
            if err:
                status = f"清单不合法，未写入：{err}"
                continue
            save_entries(cfg_path, entries)
            dirty = False
            status = f"已写入 {cfg_path.name}"
            rc = run_init(scr, root)
            status = "落地完成" if rc == 0 else f"落地有错误（退出码 {rc}），见上方输出"
    return 0


def tui(root: Path) -> int:
    cfg_path = root / "repos.json"
    try:
        # 提前暴露坏 JSON / 坏形状：不在 TUI 里静默覆盖一个读不懂的清单。
        load_entries(cfg_path)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"ERROR: 无法读取 {cfg_path}：{e}")
        return 1
    try:
        locale.setlocale(locale.LC_ALL, "")   # get_wch 拿宽字符的前提
    except locale.Error:
        pass   # 无可用 locale 时中文输入退化，ASCII 照常
    try:
        return curses.wrapper(tui_loop, root, cfg_path)
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


# ---- 自测（非交互，只测纯逻辑层）----

def selftest() -> int:
    import tempfile
    # apply_add：合法
    e, err = apply_add([], {"name": "frontend", "remote": "git@h:o/f.git"})
    assert err is None and len(e) == 1, e
    # apply_add：派生名（无 name 有 remote）
    e2, err = apply_add(e, {"remote": "https://x/backend.git"})
    assert err is None and len(e2) == 2, err
    # apply_add：重名被拒
    _, err = apply_add(e, {"name": "frontend", "remote": "x"})
    assert err and "重复" in err, err
    # apply_add：name 非法（含 /）被拒
    _, err = apply_add([], {"name": "a/b", "remote": "x"})
    assert err and "非法" in err, err
    # apply_add：remote 与 link 双填被拒
    _, err = apply_add([], {"name": "c", "remote": "x", "link": "/p"})
    assert err and "二选一" in err, err
    # apply_add：都空被拒
    _, err = apply_add([], {"name": "d"})
    assert err and "缺 remote" in err, err
    # apply_edit：改 remote
    e3, err = apply_edit(e2, 0, {"remote": "git@h:o/new.git"})
    assert err is None and e3[0]["remote"] == "git@h:o/new.git", (err, e3)
    # apply_edit：清空字段（branch）
    with_branch = [{"name": "x", "remote": "r", "branch": "dev"}]
    e4, err = apply_edit(with_branch, 0, {"branch": None})
    assert err is None and "branch" not in e4[0], e4
    # apply_edit：改成重名被拒
    _, err = apply_edit(e2, 1, {"name": "frontend"})
    assert err and "重复" in err, err
    # apply_edit：越界
    _, err = apply_edit(e2, 9, {"name": "z"})
    assert err and "越界" in err, err
    # apply_delete
    e5, err = apply_delete(e2, 0)
    assert err is None and len(e5) == 1, e5
    # fit 截断
    assert fit("abc", 5) == "abc" and fit("abcdef", 5) == "ab..."
    # entry 展示辅助
    assert entry_name({"remote": "https://x/b.git"}) == "b"
    assert entry_type({"link": "/p"}) == "link" and entry_type({"remote": "r"}) == "clone"
    assert entry_source({"name": "n", "branch": "d"}) == ""
    # 读写往返
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / "repos.json"
        assert load_entries(cfg) == []          # 不存在 -> 空
        save_entries(cfg, e2)
        assert load_entries(cfg) == e2, load_entries(cfg)
        # 顶层元数据保留（save_entries 不能丢其它 key）
        cfg.write_text(json.dumps({"$schema": "x", "comment": "c", "repos": e2},
                                  ensure_ascii=False), encoding="utf-8")
        save_entries(cfg, e2[:1])
        loaded = json.loads(cfg.read_text(encoding="utf-8"))
        assert loaded["$schema"] == "x" and loaded["comment"] == "c", loaded
        assert loaded["repos"] == e2[:1], loaded
        # 坏形状 / 非 dict 条目：load_entries 必须报错，不能静默当成空表
        for bad in ('{"repos": "abc"}', '{"repos": [42]}', "42"):
            cfg.write_text(bad, encoding="utf-8")
            try:
                load_entries(cfg)
                assert False, f"坏清单 {bad} 应抛 ValueError"
            except ValueError:
                pass
    # SCP 风格 remote（无路径段）派生名：git@gitlab.com:payments-core.git -> payments-core
    assert derive_name("git@gitlab.com:payments-core.git") == "payments-core"
    assert derive_name("git@host:org/repo.git") == "repo"
    assert derive_name("https://x/b.git") == "b"
    # link 条目无 name 时从路径派生（flow_add 留空不再是死路）
    assert validate_entries([{"link": "/Users/me/code/shared-libs"}]) is None
    print("selftest OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path.cwd(), help="工作区根（默认当前目录）")
    ap.add_argument("--selftest", action="store_true", help="跑非交互自测后退出")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    root = args.root.resolve()
    if not root.is_dir():
        print(f"ERROR: 工作区根不存在：{root}")
        return 1
    return tui(root)


if __name__ == "__main__":
    raise SystemExit(main())

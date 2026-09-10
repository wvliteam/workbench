"""wb_bash — 命令行静态解析：写入目标、冻结命中、危险命令筛。

wb.py 拆分模块之一。纯函数、不读状态：只按命令行文本与 root 路径解析。被守卫
（wb_guard）、门禁执行校验（wb_core.run_check）与 config 写入校验
（wb_cli.cmd_config）共用，所以独立于任何一方。"""

from __future__ import annotations

import os
import re
import shlex
import tempfile
from pathlib import Path

from wb_const import FROZEN_ALWAYS


# --------------------------------------------------------------------------
# shell 写入目标解析：能解析就精确判，解析不了就显式退回粗检查
# --------------------------------------------------------------------------
# 借鉴 ROMA lib/shell_write_targets.py 的思路（126 行零依赖）。
# 返回 (targets, uncertain)：targets 是写入目标路径集合，uncertain 表示
# 碰到了 eval/xargs/awk/$(...) 等无法可靠抽目标的构造。
#
# cp -t DIR src... 会被末参数规则判错（rare enough to accept）。

# 取全部参数为写入目标的命令
_ALL_ARGS = frozenset({"tee", "rm", "truncate", "touch", "mkdir", "shred"})

# 取末参数为写入目标的命令（源 -> 目标 的命令族）
_LAST_ARG = frozenset({"mv", "cp", "ln", "rsync", "install"})

# git 子命令中写入工作区的。有意不含 checkout / restore：
# 还原旧版不改动内容（契约校验的是内容哈希），放行是有意的取舍。
_GIT_WRITE = frozenset({"mv", "rm", "clean", "stash"})

# 重定向正则：匹配 > >> >>> >& >>& 等，排除 2>&1 / >&2
_RE_REDIRECT = re.compile(r"(?:\d+|&)?>{1,2}\|?\s*([^\s;|&<>()]+)")

# 会触发不确定性标记的构造
_UNCERTAIN_PATTERNS = re.compile(
    r"\b(eval|xargs|awk)\b|"
    r"\bsh\s+-c\b|\bbash\s+-c\b|\bzsh\s+-c\b|"
    r"\bpython3?\s+-c\b|\bnode\s+-e\b|"
    r"\$\(|`[^`]+`"
)

# 包装命令前缀：`env cp x y` 的首 token 是 env 而不是 cp，不剥掉就整条解析成
# 「没有写入目标」，且 uncertain=False —— 精确检查因此判定「冻结路径不在写入目标里」
# 而放行。实测 `env cp /tmp/x .workbench/state.json`、`nohup cp ... 越根路径`、
# `timeout 5 cp ... .claude/hooks/wb.py` 三条全部放行。
_WRAPPERS = frozenset({
    "env", "nohup", "sudo", "command", "builtin", "nice", "ionice",
    "stdbuf", "setsid", "time", "timeout",
})

# 前缀自己的数值操作数：timeout 的时长（`5` / `5m` / `1.5h`）、nice -n 的优先级。
# 没有哪个真实命令的名字长这样，所以一律吃掉比逐个前缀记「吃几个参数」更省 ——
# `nice cp a b` 里的 `cp` 不是数字，不会被误吃。
_WRAPPER_OPERAND = re.compile(r"[0-9]+(?:\.[0-9]+)?[a-z]?$")


def _strip_wrappers(tokens: list[str]) -> list[str]:
    """剥掉 env / nohup / timeout 之类的包装前缀，返回真正的命令 tokens。

    连带吃掉前缀自己的 flag、env 的 `VAR=value` 赋值、以及时长/优先级这类数值操作数。
    剥到不认识的首 token 就停 —— 剩下的由调用方按命令名分类。
    """
    while tokens:
        if Path(tokens[0]).name not in _WRAPPERS:
            return tokens
        rest = tokens[1:]
        while rest and (rest[0].startswith("-") or
                        re.fullmatch(r"\w+=.*", rest[0]) or
                        _WRAPPER_OPERAND.match(rest[0])):
            rest = rest[1:]
        if rest == tokens:  # 防御：没吃掉任何东西就停，避免死循环
            return tokens
        tokens = rest
    return tokens


def strip_heredocs(cmd: str) -> str:
    """剥掉 heredoc 正文，只留命令部分。

    heredoc body 里的 markdown 引用 `> 注意` 会被重定向正则误判，
    冻结路径的文本匹配也会在 body 里命中。两种误判的根因一样：
    body 不是命令的一部分。
    """
    out = []
    i = 0
    while i < len(cmd):
        m = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", cmd[i:])
        if not m:
            out.append(cmd[i:])
            break
        end_delim = m.group(1)
        out.append(cmd[i:i + m.start()])
        rest = cmd[i + m.end():]
        nl = rest.find("\n")
        if nl < 0:
            break
        body_start = nl + 1
        search_from = body_start
        found_end = False
        while True:
            nl_pos = rest.find("\n", search_from)
            if nl_pos < 0:
                break
            line = rest[body_start if search_from == body_start else search_from:nl_pos].lstrip("\t")
            if line.strip() == end_delim:
                i = i + m.end() + nl_pos + 1
                found_end = True
                break
            search_from = nl_pos + 1
        if not found_end:
            break
    return "".join(out)


def _split_pipeline(cmd: str) -> list[str]:
    """按 || / && / ; / | / 换行切段，每段是一个独立命令。"""
    segments = []
    for line in cmd.split("\n"):
        parts = re.split(r"\s*(\|\||&&|;|\|)\s*", line)
        for p in parts:
            p = p.strip()
            if p and p not in ("||", "&&", ";", "|"):
                segments.append(p)
    return segments


def resolve(cmd: str, root: Path) -> tuple[set[str], set[str], bool]:
    """解析 shell 命令的写入目标。

    返回 (all_targets, outside_targets, uncertain)：
      - all_targets：所有写入目标的相对路径集合（包括项目根内）
      - outside_targets：仅项目根外的写入目标（用于越根检查）
      - uncertain：True 表示碰到无法可靠解析的构造，targets 可能不完整

    设计取舍：能解析就精确判，解析不了就退回粗检查并在拒绝信息里说明。
    """
    safe = {Path("/dev").resolve(), Path("/tmp").resolve(), Path(tempfile.gettempdir()).resolve()}
    rootr = root.resolve()
    all_targets: set[str] = set()
    outside_targets: set[str] = set()
    uncertain = False

    cleaned = strip_heredocs(cmd)
    segments = _split_pipeline(cleaned)

    for seg in segments:
        if _UNCERTAIN_PATTERNS.search(seg):
            uncertain = True

        try:
            tokens = shlex.split(seg)
        except ValueError:
            uncertain = True
            continue

        if not tokens:
            continue

        # 1. 重定向目标
        for m in _RE_REDIRECT.finditer(seg):
            raw = m.group(1)
            if raw in ("&1", "&2", "/dev/null"):
                continue
            p = Path(raw).resolve() if raw.startswith("/") else (rootr / raw).resolve()
            inside = (p == rootr or rootr in p.parents)
            if not inside and any(s == p or s in p.parents for s in safe):
                continue
            rel = os.path.relpath(p, rootr).replace(os.sep, "/")
            all_targets.add(rel)
            if not inside:
                outside_targets.add(rel)

        # 2. 按命令名分类抽操作数
        tokens = _strip_wrappers(tokens)  # env / nohup / timeout 之类的包装前缀
        if not tokens:
            continue
        cmd_name = Path(tokens[0]).name  # 处理 /usr/bin/cp 这种
        args = tokens[1:]

        if cmd_name == "git" and len(args) >= 1:
            subcmd = args[0]
            if subcmd in _GIT_WRITE:
                _collect_targets(args[1:], all_targets, outside_targets, rootr, safe,
                                 last_only=(subcmd == "mv"))
            continue

        if cmd_name in _ALL_ARGS:
            _collect_targets(args, all_targets, outside_targets, rootr, safe, last_only=False)
        elif cmd_name in _LAST_ARG:
            _collect_targets(args, all_targets, outside_targets, rootr, safe, last_only=True)
        elif cmd_name in ("chmod", "chown"):
            _collect_targets(args[1:], all_targets, outside_targets, rootr, safe, last_only=False)
        elif cmd_name == "sed" and _has_i_flag(args):
            i_idx = _find_sed_i(args)
            # -i 后面的操作数里混着脚本（`s/a/b/`）、BSD 的独立空后缀（`-i ''`）和
            # `-e` 的表达式，按「非 flag 全算」会把脚本当成写入目标 —— 于是范围内
            # 的正常改动被判成越权写 `s/a/b`。sed -i 创建不了文件，只有已存在的
            # 路径才可能是真的写入目标。
            _collect_targets(args[i_idx + 1:], all_targets, outside_targets, rootr, safe,
                             last_only=False, must_exist=True)
        elif cmd_name == "dd":
            for tok in args:
                if tok.startswith("of=") and tok[3:] and tok[3:] != "/dev/null":
                    of_raw = tok[3:]
                    p = Path(of_raw).resolve() if of_raw.startswith("/") else (rootr / of_raw).resolve()
                    inside = (p == rootr or rootr in p.parents)
                    if not inside and any(s == p or s in p.parents for s in safe):
                        continue
                    rel = os.path.relpath(p, rootr).replace(os.sep, "/")
                    all_targets.add(rel)
                    if not inside:
                        outside_targets.add(rel)

    return all_targets, outside_targets, uncertain


def _collect_targets(args: list[str], all_targets: set[str], outside_targets: set[str],
                     rootr: Path, safe: set[Path], last_only: bool,
                     must_exist: bool = False) -> None:
    """从参数列表里抽取写入目标路径。last_only=True 只取最后一个非 flag 参数。

    must_exist=True 时只保留已存在的路径，给改不了不存在文件的命令用（sed -i）。
    例外：FROZEN_ALWAYS 的文件名永不按存在性过滤 —— 新布局下 state.json 等不在
    .workbench/ 根，磁盘上不存在，漏进 must_exist 过滤会让 `sed -i` 写冻结路径
    在精确检查下放行（uncertain=False，兜底也不触发）。
    """
    filtered = [a for a in args if a and not a.startswith("-")]
    if not filtered:
        return
    candidates = [filtered[-1]] if last_only else filtered
    for raw in candidates:
        p = Path(raw).resolve() if raw.startswith("/") else (rootr / raw).resolve()
        if must_exist and not p.exists() and Path(raw).name not in FROZEN_ALWAYS:
            continue
        inside = (p == rootr or rootr in p.parents)
        if not inside and any(s == p or s in p.parents for s in safe):
            continue
        rel = os.path.relpath(p, rootr).replace(os.sep, "/")
        all_targets.add(rel)
        if not inside:
            outside_targets.add(rel)


def _has_i_flag(args: list[str]) -> bool:
    """sed 参数列表里有没有 -i（可能带后缀如 -i.bak）。"""
    return any(a == "-i" or a.startswith("-i") for a in args if a.startswith("-"))


def _find_sed_i(args: list[str]) -> int:
    """找到 -i 在参数列表中的位置。"""
    for i, a in enumerate(args):
        if a.startswith("-") and "i" in a:
            return i
    return -1


# 不可逆 / 灾难性操作，直接拒绝。
DENY_BASH = [
    (r"\brm\s+(-\S+\s+)*(/|~|/\*|\$HOME)(\s|$)", "rm 指向根目录或 HOME"),
    (r"\brm\s+-\S*r\S*\s+\S*\.\./\.\./", "rm 递归越出项目根两级以上"),
    (r"\bgit\s+push\b[^\n]*(--force\b|-f\b)", "git force push 会覆盖远端历史"),
    (r"\b(DROP|TRUNCATE)\s+(TABLE|DATABASE|SCHEMA)\b", "破坏性 SQL DDL"),
    (r"\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|fi)?sh\b", "管道直接执行远端脚本"),
    (r">\s*/dev/(sd|nvme|disk|hd)", "直写块设备"),
    (r"\bmkfs(\.\w+)?\b", "格式化文件系统"),
    (r"\bchmod\s+-?\S*\s*777\s+/(\s|$)", "对根目录放开全部权限"),
    (r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;\s*:", "fork bomb"),
    (r"\bdd\s+[^\n]*of=/dev/", "dd 写入设备"),
]

# 可能丢失未提交改动，但确有正当用途：放行并提示。
WARN_BASH = [
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard 会丢弃未提交改动"),
    (r"\bgit\s+clean\s+-\S*[fd]", "git clean 会删除未跟踪文件"),
    (r"\bgit\s+checkout\s+--\s", "git checkout -- 会覆盖工作区文件"),
    (r"\bnpm\s+publish\b|\btwine\s+upload\b", "对外发布动作，确认版本号"),
]


def catastrophic_command(cmd: str) -> str:
    """命中 DENY_BASH 就返回理由，否则空串。"""
    for pat, why in DENY_BASH:
        if re.search(pat, cmd, re.IGNORECASE):
            return why
    return ""


MAX_LOG = 500

# `config set` 允许写入的顶层 key 白名单：既是合法性校验，也是「新字段需要先在
# 代码里登记」的强制入口。之前 dotted_set 对任意 args.key 生效，只对
# `gate_commands.*` 做灾难命令内容校验，其余字段（包括 role_scopes.* 这种能
# 直接扩大自己写权限的）没有第二道防线 —— 特权层的命令名匹配一旦遗漏新字段，
# 或者主线程被诱导拼出不合理的 config set，没有东西兜底。前缀匹配（以 "." 结尾）
# 覆盖 dotted 子键（如 gate_commands.test）；不带 "." 的项精确匹配顶层 key。
CONFIG_SCHEMA = (
    "gate_commands.",
    "gate_waivers.",
    "role_scopes.",
    "allowed_skills",
    "max_parallel",
    "gate_timeout",
    "task_lease",
)


def config_key_allowed(key: str) -> bool:
    return any(key == k or (k.endswith(".") and key.startswith(k)) for k in CONFIG_SCHEMA)



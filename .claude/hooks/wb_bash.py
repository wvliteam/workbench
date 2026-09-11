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

from wb_const import FROZEN_ALWAYS, GUARDED_PREFIXES, WORKSPACE_GUARDED_PREFIXES


# --------------------------------------------------------------------------
# shell 写入目标解析：能解析就精确判，解析不了就显式退回粗检查
# --------------------------------------------------------------------------
# 借鉴 ROMA lib/shell_write_targets.py 的思路（126 行零依赖）。
# 返回 (targets, uncertain)：targets 是写入目标路径集合，uncertain 表示
# 碰到了 eval/xargs/awk/$(...) 等无法可靠抽目标的构造。

# 取全部参数为写入目标的命令。patch 的非 flag 参数就是被改的文件（补丁正文从
# stdin/重定向来，不在参数里）；只认末参数会漏掉它，整条命令解析不出目标而放行。
_ALL_ARGS = frozenset({"tee", "rm", "truncate", "touch", "mkdir", "shred", "patch"})

# 取末参数为写入目标的命令（源 -> 目标 的命令族）。ln 不在这里：它的链接指向项
# （source）本身就是攻击面，单独处理（见 _collect_ln_sources）。
_LAST_ARG = frozenset({"mv", "cp", "rsync", "install"})

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
    r"\$\(|`[^`]+`|"
    # R2：解释器从 stdin/重定向读脚本 —— 脚本正文不在命令行里，写入目标无法解析。
    # `python3 -` 从 stdin 读源码、`python3 < f.py` 把文件当源码读，正文对守卫不可见。
    r"\bpython3?\s+-\s*$|"
    r"\bpython3?\s+<(?!<)|"
    r"\b(?:ba|z|fi)?sh\s+-\s*$|"
    r"\b(?:ba|z|fi)?sh\s+(?:-[^\s]*\s+)*<(?![<(])|"
    # R5：写入目标不在命令行里的形态 —— `find -delete` 的目标是匹配结果、`-exec`
    # 运行的是任意命令、`git apply` 的目标在 diff 正文里。都解析不出目标，按
    # uncertain 处理：角色一律拒，主线程退回保守检查。
    r"\bfind\b[^\n]*\s-(?:delete|exec|execdir|ok)\b|"
    r"\bgit\s+apply\b"
)

# R2：heredoc 从 stdin 执行脚本。`<<EOF` 的正文连标记一起被 strip_heredocs 剥掉，
# 按段查不到「从 stdin 读脚本」的形态，必须在剥壳前按原文查一次 —— 正文代表的
# 执行在命令行里不可见，执行形态本身就要按 uncertain 拒绝。
_STDIN_EXEC = re.compile(r"\b(?:ba|z|fi)?sh\s+(?:-[^\s]*\s+)*<<")

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

# shlex.split 保留成独立 token 的 shell 操作符 —— 它们不是文件路径。
_SHELL_OPS = frozenset({
    "<", ">", ">>", ">>>", "<<", "<<<", "2>", "2>>", "&>", "&>>", "|", "||", "&&", ";",
})


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

    cleaned = strip_heredocs(cmd)
    segments = _split_pipeline(cleaned)

    # 段序目录状态：cd / pushd / popd 只影响**后续**段的相对路径解析（真实 shell 里
    # 同一段内的重定向在命令执行前先打开，按本段 cwd 算）。cd 目标含变量/命令替换时
    # 无法静态解析，标记 uncertain，由调用方对角色直接拒绝（宁偏拒不偏放）。
    state = {"seg_cwd": rootr, "oldpwd": None, "push_stack": [rootr], "uncertain": False}

    # `bash <<EOF` 的正文连 <<EOF 一起被剥壳，段里只剩 `bash`，按段看不到
    # 「从 stdin 执行脚本」的形态 —— 剥壳前先按原文标记 uncertain。
    if _STDIN_EXEC.search(cmd):
        state["uncertain"] = True

    for seg in segments:
        if _UNCERTAIN_PATTERNS.search(seg):
            state["uncertain"] = True

        try:
            tokens = shlex.split(seg)
        except ValueError:
            state["uncertain"] = True
            continue

        if not tokens:
            continue

        # 本段 cwd 仍在项目根内才保留 /tmp 安全豁免：cd .. / cd /tmp 已切出项目根后，
        # 后续写入是「越出项目根的逃逸」，不是 scratch，不能放行（B14 在 lab 下父目录
        # 恰是 /tmp，不设这条会被当 safe 吞掉）。
        cwd_in_root = (state["seg_cwd"] == rootr or rootr in state["seg_cwd"].parents)
        seg_safe = safe if cwd_in_root else frozenset()

        # 1. 重定向目标
        for m in _RE_REDIRECT.finditer(seg):
            raw = m.group(1)
            if raw in ("&1", "&2", "/dev/null"):
                continue
            p = _target_path(state["seg_cwd"], rootr, raw)
            inside = (p == rootr or rootr in p.parents)
            if not inside and any(s == p or s in p.parents for s in seg_safe):
                continue
            rel = os.path.relpath(p, rootr).replace(os.sep, "/")
            all_targets.add(rel)
            if not inside:
                outside_targets.add(rel)

        # 2. cd / pushd / popd：只推进目录状态，本身不产生写入目标
        stripped = _strip_wrappers(tokens)  # env / nohup / timeout 之类的包装前缀
        if not stripped:
            continue
        cmd_name = Path(stripped[0]).name  # 处理 /usr/bin/cp 这种
        if cmd_name in ("cd", "pushd", "popd"):
            _step_cwd(cmd_name, stripped[1:], state)
            continue

        # 3. 按命令名分类抽操作数
        args = stripped[1:]

        if cmd_name == "git" and len(args) >= 1:
            subcmd = args[0]
            if subcmd in _GIT_WRITE:
                _collect_targets(args[1:], all_targets, outside_targets, state["seg_cwd"],
                                 rootr, seg_safe, last_only=(subcmd == "mv"))
            continue

        if cmd_name in _ALL_ARGS:
            operands = args
            if cmd_name == "patch":
                operands = _patch_target_operands(args)
                # `patch < p.diff` / `patch -i p.diff`（只给补丁输入，不给被改文件）：
                # 目标只写在补丁正文里，与 `git apply` 同族，按 uncertain 处理，
                # 不当成「没有写入目标」放行。
                if not operands:
                    state["uncertain"] = True
            _collect_targets(operands, all_targets, outside_targets, state["seg_cwd"],
                             rootr, seg_safe, last_only=False)
        elif cmd_name == "ln":
            # ln src dst：dst（末参数）按普通写入目标检查；src（链接指向项）resolve
            # 后若落在守卫前缀下也进目标集 —— 同命令串联形态没有链接可追，src 指向
            # 守卫本体本身就要在这段拦下。
            _collect_targets(args, all_targets, outside_targets, state["seg_cwd"],
                             rootr, seg_safe, last_only=True)
            _collect_ln_sources(args, all_targets, state["seg_cwd"], rootr)
        elif cmd_name in _LAST_ARG:
            # `-t DIR`（cp/mv/install）下末参数是源不是目标：写入目标是 DIR/<源文件名>。
            td = _target_directory_flag(args)
            if td is None:
                _collect_targets(args, all_targets, outside_targets, state["seg_cwd"],
                                 rootr, seg_safe, last_only=True)
            else:
                dst, rest = td
                # 只算 DIR/<源文件名>：DIR 本身不是写入目标（`cp -t src /tmp/x.py`
                # 写的是 src/x.py，把 DIR 也算进去会因目录不在角色范围而误拦）。
                cands = [f"{dst.rstrip('/')}/{Path(s).name}"
                         for s in rest if s and not s.startswith("-")]
                _collect_targets(cands, all_targets, outside_targets, state["seg_cwd"],
                                 rootr, seg_safe, last_only=False)
        elif cmd_name in ("chmod", "chown"):
            _collect_targets(args[1:], all_targets, outside_targets, state["seg_cwd"],
                             rootr, seg_safe, last_only=False)
        elif cmd_name == "sed" and _has_i_flag(args):
            i_idx = _find_sed_i(args)
            # -i 后面的操作数里混着脚本（`s/a/b/`）、BSD 的独立空后缀（`-i ''`）和
            # `-e` 的表达式，按「非 flag 全算」会把脚本当成写入目标 —— 于是范围内
            # 的正常改动被判成越权写 `s/a/b`。sed -i 创建不了文件，只有已存在的
            # 路径才可能是真的写入目标。
            _collect_targets(args[i_idx + 1:], all_targets, outside_targets,
                             state["seg_cwd"], rootr, seg_safe,
                             last_only=False, must_exist=True)
        elif cmd_name == "dd":
            for tok in args:
                if tok.startswith("of=") and tok[3:] and tok[3:] != "/dev/null":
                    p = _target_path(state["seg_cwd"], rootr, tok[3:])
                    inside = (p == rootr or rootr in p.parents)
                    if not inside and any(s == p or s in p.parents for s in seg_safe):
                        continue
                    rel = os.path.relpath(p, rootr).replace(os.sep, "/")
                    all_targets.add(rel)
                    if not inside:
                        outside_targets.add(rel)

    return all_targets, outside_targets, state["uncertain"]


def _target_path(base: Path, rootr: Path, raw: str) -> Path:
    """把命令里的写入目标解析成绝对路径：相对路径按 base（当前段 cwd）拼接。

    `~` 先展开：不展开时 `~/evil.py` 被当成项目内的相对路径，匹配裸 `*.py` 放行，
    真实落点是 $HOME（含 ~/.claude/settings.json）。展开后落在项目根外，现有的
    越根检查直接接住。
    """
    raw = os.path.expanduser(raw)
    if raw.startswith("/"):
        return Path(raw).resolve()
    return (base / raw).resolve()


def _target_directory_flag(args: list[str]) -> tuple[str, list[str]] | None:
    """cp/mv/install 的 `-t DIR` / `--target-directory[=]DIR`。

    返回 (目标目录, 去掉该选项后的参数)；没有该选项返回 None。有它时末参数是源
    文件不是写入目标，按末参数规则会把源当目标 —— `cp -t .claude/hooks /tmp/x.py`
    判成写 /tmp/x.py（安全目录）而放行，真实落点是 .claude/hooks/x.py。
    """
    for i, a in enumerate(args):
        if a in ("-t", "--target-directory"):
            if i + 1 >= len(args):
                return None
            return args[i + 1], args[:i] + args[i + 2:]
        if a.startswith("--target-directory="):
            return a.split("=", 1)[1], args[:i] + args[i + 1:]
        if a.startswith("-t") and len(a) > 2:  # -tDIR 紧凑形态
            return a[2:], args[:i] + args[i + 1:]
    return None


def _step_cwd(cmd_name: str, args: list[str], state: dict) -> None:
    """按段推进目录状态：cd / pushd / popd。

    state 维护 seg_cwd（当前段目录）、oldpwd（cd - 用）与 push_stack（pushd/popd
    的目录栈）。cd 目标含变量/命令替换时无法静态解析，标记 uncertain，由调用方
    对角色直接拒绝。cd 目标不存在/解析失败仍按「cd 成功后的路径」解析 —— 后续
    相对写入按此目录算，宁偏拒不偏放（不赌 shell 的 cd 失败回退）。
    """
    seg_cwd = state["seg_cwd"]

    if cmd_name == "popd":
        if len(state["push_stack"]) > 1:
            state["oldpwd"] = seg_cwd
            state["seg_cwd"] = state["push_stack"].pop()
        return  # 空栈再 popd 报错，cwd 不变

    # 首个非 flag 目录参数。`-` 单独是「上一目录」（cd -），不是 flag；`-P`/`-L`
    # 这类真 flag、`+N`（pushd 的栈内移动）跳过。
    tgt = next((a for a in args if a and (a == "-" or (
        not a.startswith("-") and not a.startswith("+")))), None)
    prev = seg_cwd

    if tgt is None:
        if cmd_name == "pushd":
            # 裸 pushd：交换栈顶两个目录并 cd 到新栈顶
            if len(state["push_stack"]) >= 2:
                state["push_stack"][-1], state["push_stack"][-2] = \
                    state["push_stack"][-2], state["push_stack"][-1]
                state["oldpwd"] = prev
                state["seg_cwd"] = state["push_stack"][-1]
            return
        # 裸 cd：到 $HOME，越出项目根 —— 后续相对写入按 $HOME 解析，宁偏拒
        state["oldpwd"] = prev
        state["seg_cwd"] = Path.home().resolve()
        return

    if tgt == "-":
        # cd -：回 OLDPWD（上一次 cd 之前的目录）。无 OLDPWD 时 cd 失败，cwd 不变。
        if state.get("oldpwd") is not None:
            state["seg_cwd"] = state["oldpwd"]
        return

    if any(ch in tgt for ch in "$`"):
        state["uncertain"] = True
        return

    new_cwd = Path(tgt).resolve() if tgt.startswith("/") else (prev / tgt).resolve()
    if cmd_name == "pushd":
        state["push_stack"].append(prev)
    state["oldpwd"] = prev
    state["seg_cwd"] = new_cwd


def _operands(args: list[str]) -> list[str]:
    """参数列表里的操作数：去掉 flag 与重定向操作符。

    shlex.split 把 `<` / `>` / `|` 这类重定向保留成独立 token（`patch a.py < p.diff`
    → ['patch','a.py','<','/tmp/p.diff']），当成操作数会解析出一个叫 `<` 的写入目标
    —— 在角色范围里当然不存在，把正常命令误判成越权写。
    """
    return [a for a in args if a and not a.startswith("-") and a not in _SHELL_OPS]


def _patch_target_operands(args: list[str]) -> list[str]:
    """patch 的被改文件：排掉补丁输入（`-i`/`--input` 的值、重定向目标）后的操作数。

    排空 = 目标只写在补丁正文里，解析不到（见调用点的 uncertain 处理）。
    """
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a in ("-i", "--input") or a in _SHELL_OPS:
            skip = True  # 其后一个 token 是补丁输入文件，不是被改文件
        elif not a.startswith("-"):
            out.append(a)
    return out


def _collect_targets(args: list[str], all_targets: set[str], outside_targets: set[str],
                     base: Path, rootr: Path, safe: set[Path], last_only: bool,
                     must_exist: bool = False) -> None:
    """从参数列表里抽取写入目标路径。last_only=True 只取最后一个非 flag 参数。

    相对路径按 base（该段 cwd）拼接，rel 仍按 rootr 为坐标系 —— 调用方把 rootr
    当 cwd 传给 _check_write_target，两处坐标系一致。

    must_exist=True 时只保留已存在的路径，给改不了不存在文件的命令用（sed -i）。
    例外：FROZEN_ALWAYS 的文件名永不按存在性过滤 —— 新布局下 state.json 等不在
    .workbench/ 根，磁盘上不存在，漏进 must_exist 过滤会让 `sed -i` 写冻结路径
    在精确检查下放行（uncertain=False，兜底也不触发）。
    """
    filtered = _operands(args)
    if not filtered:
        return
    candidates = [filtered[-1]] if last_only else filtered
    for raw in candidates:
        p = _target_path(base, rootr, raw)
        if must_exist and not p.exists() and Path(raw).name not in FROZEN_ALWAYS:
            continue
        inside = (p == rootr or rootr in p.parents)
        if not inside and any(s == p or s in p.parents for s in safe):
            continue
        rel = os.path.relpath(p, rootr).replace(os.sep, "/")
        all_targets.add(rel)
        if not inside:
            outside_targets.add(rel)


def _collect_ln_sources(args: list[str], all_targets: set[str],
                        base: Path, rootr: Path) -> None:
    """ln 的链接指向项（末参数之外的非 flag 参数）按守卫前缀过滤上报。

    `ln src dst` 的 dst 在静态解析期尚不存在，同命令串联
    `ln -s .claude/hooks/wb.py x.py && echo pwn > x.py` 追不到链 —— 唯一能拦的
    就是 src 本身指向守卫本体：resolve 后落进 GUARDED_PREFIXES（含多仓库布局的
    workspace 层）就一并进目标集，_check_write_target 会因前缀收窄而拒；指向共享
    目录的正当软链（`ln -s ../shared/lib.rs src/local.rs`）不在前缀下，不误拦。
    """
    prefixes = GUARDED_PREFIXES + (
        WORKSPACE_GUARDED_PREFIXES if (rootr / "repos").is_dir() else ())
    for raw in _operands(args)[:-1]:
        p = _target_path(base, rootr, raw)
        rel = os.path.relpath(p, rootr).replace(os.sep, "/")
        if any(rel.startswith(g) if g.endswith("/")
               else (rel == g or rel.startswith(g + "/"))
               for g in prefixes):
            all_targets.add(rel)


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


# --------------------------------------------------------------------------
# R4：门禁命令越根引用校验。门禁命令以 subprocess.run(shell=True, cwd=root) 执行，
# 不经 Bash 守卫 —— 命令里引用项目根外的脚本（sh /tmp/x.sh、bash ~/x.sh、
# python3 /tmp/x.py）或外部路径（cd /tmp、cd ..、--cov=/tmp/x）都是借门禁名义的
# 任意代码执行面。token 级扫描：剥 heredoc 后按段分词，长得像路径的 token（含
# key=value 的值，~/$HOME 先展开）resolve 后落在项目根外即拒；引用项目根内的
# 路径（python3 .claude/hooks/wb.py selfcheck、(cd repos/frontend && npm test)）
# 放行。再按字面兜底挡 /tmp/、~/、$HOME —— 内联代码里的路径会被 shlex 揉进
# 更大的 token，token 解析看不到。纯函数：不读状态，root 参数化，wb_cli 写入
# 校验与 wb_core 执行前复筛共用。
# --------------------------------------------------------------------------

# 外部路径的字面标记。这三个标记在门禁命令里没有正当用法，`go test ./...` 的
# `..` 不在其中，所以不误伤多仓库子 shell 与常规测试命令。
_EXTERNAL_PATH_MARK = re.compile(r"/tmp/|~/|\$HOME")


def _path_candidates(tok: str) -> list[str]:
    """一个 token 里可能是路径的部分：整 token，以及 key=value 的值部分。

    `--cov=/tmp/x` 这类 flag=路径 形态整 token 不是路径（resolve 会按相对路径拼
    在项目根内而误放），值部分才是外部路径引用。
    """
    out = []
    if "=" in tok:
        _, _, val = tok.partition("=")
        if val:
            out.append(val)
    if not tok.startswith("-"):
        out.append(tok)
    return out


def _is_pathish(s: str) -> bool:
    """长得像路径的 token：绝对、家目录（~/ 或 ~user）、./ ../、裸 . ..、或含 /。"""
    return (s.startswith("/") or s.startswith("~") or s.startswith("./")
            or s.startswith("../") or s.startswith("$HOME")
            or s.startswith("${HOME}") or s in (".", "..") or "/" in s)


def _resolve_token(cand: str, rootr: Path) -> Path:
    """把候选路径解析成绝对路径：~ 与 $HOME 先展开，相对路径按项目根拼。"""
    c = os.path.expanduser(cand.replace("$HOME", "~").replace("${HOME}", "~"))
    if c.startswith("/"):
        return Path(c).resolve()
    return (rootr / c).resolve()


def gate_command_references_outside(cmd: str, root: Path) -> str:
    """门禁命令越根引用校验：返回拒绝理由，通过返回空串。"""
    rootr = Path(root).resolve()
    cleaned = strip_heredocs(cmd)
    for seg in _split_pipeline(cleaned):
        try:
            tokens = shlex.split(seg)
        except ValueError:
            continue  # 解析不了的段交给 catastrophic 兜底，这里不误拒
        for tok in tokens:
            for cand in _path_candidates(tok):
                if not _is_pathish(cand):
                    continue
                p = _resolve_token(cand, rootr)
                if not (p == rootr or rootr in p.parents):
                    return f"命令引用项目根外的路径 {cand}"
    # 兜底：内联代码形态（`sh -c 'sh /tmp/evil.sh'`、`python3 -c "open('/tmp/x','w')"`）
    # 里被 shlex 揉成一个更大 token 的路径，上面的 token 解析看不到。
    mark = _EXTERNAL_PATH_MARK.search(cleaned)
    if mark:
        return f"命令引用项目根外的路径（{mark.group()}）"
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



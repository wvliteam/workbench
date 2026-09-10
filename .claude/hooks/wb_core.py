"""wb_core — 状态内核：状态读写、flow 定位、锁、冻结、契约、门禁、调度。

wb.py 拆分模块之一。.workbench/ 下的持久状态只在这里读写；守卫（wb_guard）与
CLI（wb_cli）都经这一层，不各自碰文件。状态全部是纯 JSON：可以 git diff、
可以 jq、不需要数据库；state.json 只允许经 wb.py 修改（pre-tool 钩子拦截直接
写入），这样门禁与进度记录无法被 agent 绕过。"""

from __future__ import annotations

import datetime
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    import fcntl  # 只有 POSIX 有；缺它就退回无锁（Windows 上仍是旧行为）
except ImportError:  # pragma: no cover
    fcntl = None

from wb_const import (
    DEFAULT_ROLE_SCOPES, FROZEN_ALWAYS, GATES, PHASES, PHASE_CN, REPO_HINTS, STATE_SCHEMA,
)
from wb_bash import MAX_LOG, catastrophic_command


# --------------------------------------------------------------------------
# 基础设施
# --------------------------------------------------------------------------

def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def find_root(start: Path | None = None) -> Path:
    """项目根。环境变量优先，再向上查找含 .workbench/ 的目录；找不到就用起点。

    环境变量优先是给 hook 进程用的：Claude Code 给 hook 注入 CLAUDE_PROJECT_DIR，
    它是会话的项目根，比「按当前目录向上撞最近的 .workbench」可靠 —— 载荷里的
    cwd 与 shell 的 cd 都会让向上查找漂到另一份 .workbench/（多仓库工作区里
    每个仓库一份），状态归属跟着漂。WB_ROOT 允许显式钉死（脚本、自检、CI）。
    要求目录里确实有 .workbench/：环境变量指错时不静默接管，仍走向上查找。
    """
    for var in ("WB_ROOT", "CLAUDE_PROJECT_DIR"):
        v = os.environ.get(var)
        if v and (Path(v) / ".workbench").is_dir():
            return Path(v).resolve()
    cur = (start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / ".workbench").is_dir():
            return p
    return cur


def wb_dir(root: Path) -> Path:
    return root / ".workbench"


DEFAULT_FLOW = "main"
FLOW_PTR = "current-flow"   # .workbench/<指针> 内容：当前 flow 名
_FLOW_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")   # 防 ../ 穿越进别的目录


def flow_dir(root: Path, flow: str) -> Path:
    """一个 flow（需求）一份状态目录：<root>/.workbench/flows/<flow>/。

    state.json / state.lock / frozen 缓存 / unlock / disputes / gate 日志都在里面，
    多个需求并行时互不覆盖。名字先校验再拼路径 —— flow 名进 CLI（--flow）时是
    信任边界上的输入，`../x` 会把状态写到工作区外。
    """
    if not (isinstance(flow, str) and _FLOW_NAME.fullmatch(flow)):
        die(f"flow 名只能用小写字母、数字、`-`、`_`：{flow!r}")
    return wb_dir(root) / "flows" / flow


_FLOW_OVERRIDE: str | None = None   # CLI 从 WB_FLOW 读入；hook 路径在 cmd_hook 里强制清空

def set_flow_override(name: str | None) -> None:
    """设 CLI 级 flow 覆盖（WB_FLOW）。跨模块只能经此写：直接在自己模块里
    global 它只会改到本模块的副本，core 里读 _FLOW_OVERRIDE 的地方看不见。"""
    global _FLOW_OVERRIDE
    _FLOW_OVERRIDE = name


def flow_override() -> str | None:
    """当前 CLI 级 flow 覆盖；没设置时为 None。"""
    return _FLOW_OVERRIDE


def pointer_flow(root: Path) -> str:
    """指针定位的 flow。指针缺失/非法回 main —— 指针是便捷定位，不是安全边界。"""
    raw = ""
    try:
        raw = (wb_dir(root) / FLOW_PTR).read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_FLOW
    if _FLOW_NAME.fullmatch(raw):
        return raw
    return DEFAULT_FLOW


def read_current_flow(root: Path) -> str:
    """CLI 的当前 flow：WB_FLOW 显式钉死优先，否则读指针。

    WB_FLOW 只在 CLI 命令路径生效（main() 读入，cmd_hook 清空）—— 守卫与 hook
    是工作区级视角，不跟单个会话的钉死走。指针是全部会话共享的一份文件，两个
    终端并行推两条 flow 时，状态命令会被对方切的指针带跑到别的流水线上；
    各自 export WB_FLOW 是机制内唯一的并发定位手段。
    """
    if _FLOW_OVERRIDE and _FLOW_NAME.fullmatch(_FLOW_OVERRIDE):
        return _FLOW_OVERRIDE
    return pointer_flow(root)


def set_current_flow(root: Path, flow: str) -> None:
    flow_dir(root, flow)          # 先校验名字
    (wb_dir(root) / FLOW_PTR).write_text(flow + "\n", encoding="utf-8")


def all_flows(root: Path) -> list[str]:
    """磁盘上实际存在的 flow 目录，按名字排序。至少返回 [main]。

    守卫要读所有 flow 的冻结清单/窗口/争议并集 —— CLI 按指针定位到单个 flow，
    两个视角不一致时（指针切了但 hook 是老进程，或 flow 目录是手工建的），
    越权检查不能跟着单 flow 视角漏。
    """
    d = wb_dir(root) / "flows"
    out = []
    if d.is_dir():
        out = [p.name for p in sorted(d.iterdir()) if p.is_dir()
               and _FLOW_NAME.fullmatch(p.name)]
    if DEFAULT_FLOW not in out:
        out.insert(0, DEFAULT_FLOW)
    return out


def state_path(root: Path, flow: str | None = None) -> Path:
    """一个 flow 一份 state。旧布局（state.json 直接在 .workbench/ 下）按 main 读。"""
    if flow is None:
        flow = read_current_flow(root)
    if flow == DEFAULT_FLOW:
        legacy = wb_dir(root) / "state.json"
        if legacy.is_file():
            return legacy
    return flow_dir(root, flow) / "state.json"


def default_state(name: str) -> dict:
    return {
        "version": STATE_SCHEMA,
        "state_rev": 0,  # 单调递增，每次 save_state +1；phase advance 用它做门禁前后的 CAS
        "project": name,
        "created": now(),
        "phase": "clarify",
        "phases": list(PHASES),
        "max_parallel": 3,
        "seq": 0,
        "tasks": [],
        "gates": {},
        "contracts": [],
        "role_scopes": json.loads(json.dumps(DEFAULT_ROLE_SCOPES)),
        "gate_commands": {},  # 例如 {"test": "npm test", "lint": "npm run lint"}
        "gate_waivers": {},   # 显式豁免未配置的门禁，如 {"build": "纯文档项目，无构建"}
        "gate_timeout": 1800,  # 单条门禁命令的秒数上限，超时记 FAIL
        "task_lease": 3600,   # task start 授予的租约秒数；过期只在 status/next 提示，不自动抢占
        "allowed_skills": [],  # subagent 可调的 skill 白名单（`*`=全部）；空=拒全部，只主线程能改
        "log": [],
    }


_STATE_LOCK = None  # 持锁的文件对象。load_state(lock=True) 开，save_state / main 收尾关


def acquire_state_lock(root: Path, timeout: float = 20.0) -> None:
    """对当前 flow 的 state.lock 上排他锁。已持锁时直接返回，不重入自阻塞。

    锁跟着 state_path 踩点（老布局落在 .workbench/，flow 布局落在
    flows/<flow>/），A flow 的门禁不会堵 B flow 的 task done。
    """
    global _STATE_LOCK
    if fcntl is None or _STATE_LOCK is not None:
        return
    d = state_path(root).parent
    if not d.is_dir():
        return
    fh = open(d / "state.lock", "a+")
    deadline = time.time() + timeout
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _STATE_LOCK = fh
            return
        except OSError:
            if time.time() >= deadline:
                fh.close()
                die(f"等状态锁超时（{timeout:.0f}s）：另一个 wb.py 进程正在改状态，重试即可")
            time.sleep(0.02)


def release_state_lock() -> None:
    global _STATE_LOCK
    if _STATE_LOCK is None:
        return
    fh, _STATE_LOCK = _STATE_LOCK, None
    try:
        fcntl.flock(fh, fcntl.LOCK_UN)
    finally:
        fh.close()


def load_state(root: Path, lock: bool = False) -> dict:
    """lock=True 进入「读-改-写」临界区，由 save_state 出锁。**所有会改状态的命令都要 lock=True。**

    并行 develop 下每个 subagent 各自跑 wb.py，无锁的读改写会让先落盘的 task done
    被后一个进程的旧快照静默盖掉（实测 45 个并发 task done 丢 23 个），且 save_state
    顺手重写的 .workbench/frozen 会一起退回旧版 —— 刚锁的契约的两条防线同时失效。
    锁不能跨门禁命令持有，phase advance 因此先在锁外算门禁再入锁落记录。

    flow 定点：读哪个文件在这里定，写回哪个文件由 st["_flow"] 决定 —— 中途
    `flow switch` 切了指针也不会把 A flow 的状态写进 B flow 的 state.json
    （还持着 A 的锁，连锁都错位）。_flow 以下划线开头，不参与 default_state
    的字段补齐，也不会被 save_state 写进 JSON。
    """
    flow = read_current_flow(root)
    p = state_path(root, flow)
    if not p.is_file():
        die(f"未初始化工作台（flow={flow}）。先运行："
            f"python3 .claude/hooks/wb.py init --name <项目名>（状态文件应在：{p.relative_to(root)}）")
    if lock:
        acquire_state_lock(root)
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        die(f"state.json 无法解析：{e}")
    if not isinstance(st, dict):
        die("state.json 顶层必须是对象")
    ver = st.get("version", STATE_SCHEMA)
    if isinstance(ver, int) and ver > STATE_SCHEMA:
        die(f"state.json 结构版本 {ver} 比本代码（{STATE_SCHEMA}）更新，可能含看不懂的字段。"
            "读改写会丢掉它们 —— 升级 wb.py 再试，别用旧代码改新状态。")
    st["_flow"] = flow
    # 向前兼容：补齐新增字段。旧的字符串契约引用由迁移检查标记为不可安全重绑，
    # 只有显式 task reopen 才允许刷新为当前完整快照。
    base = default_state(st.get("project", "unnamed"))
    for k, v in base.items():
        st.setdefault(k, v)
    migrate_contract_refs(st)
    # 记录本次读入时的 log 长度，save_state 据此只把新增条目追加进 audit.jsonl，
    # 不是每次都把全部历史重新扫一遍。下划线前缀：不参与字段补齐，也不落盘。
    st["_log_len_before"] = len(st["log"])
    return st


def save_state(root: Path, st: dict) -> None:
    # log 只留最近 MAX_LOG 条给 status 快速展示；截断前把本次新增的条目追加进
    # audit.jsonl（append-only，不受 MAX_LOG 限制）。用长度差找「新增的」而不是
    # 整个重写 audit 文件 —— save_state 每次调用都可能加日志，重写整份文件是
    # O(n) 的浪费，追加是 O(1)。旧 state 反序列化后没有 _log_len_before 属性，
    # 缺失时保守地退回「本次全部当作新增」，不会漏记，最多首次重复写一次。
    prev_len = st.pop("_log_len_before", None)
    new_entries = st["log"][prev_len:] if isinstance(prev_len, int) else st["log"]
    if new_entries:
        flow_for_audit = st.get("_flow") or read_current_flow(root)
        audit_path = state_path(root, flow_for_audit).parent / "audit.jsonl"
        with audit_path.open("a", encoding="utf-8") as f:
            for entry in new_entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    st["log"] = st["log"][-MAX_LOG:]
    st["state_rev"] = int(st.get("state_rev", 0)) + 1
    # 写回读入时的那个 flow。state_path 不带参数时才看指针 —— 那正是会被
    # 中途切换的值，save_state 必须不受它影响。
    flow = st.pop("_flow", None) or read_current_flow(root)
    p = state_path(root, flow)
    # 临时文件名带 pid：共用一个名字时，两个进程同时写会把彼此的字节交织进去，
    # 再各自 replace —— 实测 45 个并发进程能写出语法上就无效的 state.json，
    # 那时连 status 都跑不起来。锁已经把这条路串行化了，这里是第二道保险。
    tmp = p.parent / f"{p.name}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # 派生缓存先落盘。反过来的话中途崩溃会留下「state 新、frozen 旧」——
    # 刚锁的契约不在清单里，守卫放行。这个顺序崩在中间是 frozen 比 state 新，
    # 多冻一份契约的误拒，下一次 save_state 自然纠正。
    write_frozen(root, st, flow)
    tmp.replace(p)
    release_state_lock()


def frozen_paths(st: dict) -> list[str]:
    """所有不允许用工具直接写的相对路径。

    登记只是建立元数据；首次 lock 才建立哈希基线并冻结契约正文。这样 architect
    可以先写完正文再登记/锁定，后续修改必须经过 unlock -> bump。

    flows/ 与 legacy 状态文件整目录保护：`flows/<flow>/state.json` 也是
    「状态只能经 wb.py 改」的对象，不能只保护 main 布局下的那一份。
    """
    out = [f".workbench/{n}" for n in FROZEN_ALWAYS]
    out.append(".workbench/flows/")
    if ".workbench/state.json" not in out:
        out.append(".workbench/state.json")
    out += [c["path"] for c in st.get("contracts", [])
            if c.get("path") and c.get("sha")]
    return out


def write_frozen(root: Path, st: dict, flow: str | None = None) -> None:
    """把冻结清单落成纯文本，供 hook 低成本读取。

    必须原子替换。`write_text` 是「truncate 再 write」两步，中间那一瞬文件存在但为空，
    而守卫只判文件在不在 —— 实测 4 写 6 读并行，12000 次读里 5588 次读到空清单，
    那一刻 Write/Edit 与 Bash 两条防线对 state.json、role 与全部已锁契约同时放行
    （含改 role 提权）。触发不需要谁去绕：一次 task done 与一次工具调用重叠就够。

    缓存跟着状态走：flow 布局落在 flows/<flow>/frozen，老布局留在 .workbench/。
    """
    if flow is None:
        flow = st.get("_flow") or read_current_flow(root)
    f = state_path(root, flow).parent / "frozen"
    tmp = f.parent / f"{f.name}.{os.getpid()}.tmp"
    tmp.write_text("\n".join(frozen_paths(st)) + "\n", encoding="utf-8")
    tmp.replace(f)


def read_frozen(root: Path) -> list[str]:
    """冻结清单，聚合全部 flow 的并集。

    守卫是工作区级视角：A flow 锁的契约不能在 B flow 视角下变成普通业务文件 ——
    跨 flow 契约（同名文件被两个需求登记）本来就靠 owner 拆。单 flow 的缓存缺失
    或为空时从那份状态现算，仍然只补自己那个 flow 的清单。

    空清单视同缺失：`FROZEN_ALWAYS` 那几个恒在，合法的清单不可能为空。write_frozen
    已经原子化，这条是纵深防御 —— 它不认成因，任何原因写出的空文件都接得住，
    而失效方向是误拒而非放行。
    """
    out: list[str] = []
    for flow in all_flows(root):
        f = state_path(root, flow).parent / "frozen"
        got: list[str] = []
        if f.is_file():
            try:
                got = [l.strip() for l in f.read_text(encoding="utf-8").splitlines()
                       if l.strip()]
            except OSError:
                got = []
        if got:
            out.extend(got)
            continue
        # 缓存缺失/为空：从该 flow 的 state.json 现算
        try:
            st = json.loads(state_path(root, flow).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            st = {}
        out.extend(frozen_paths(st))
    # 去重保序
    seen: set[str] = set()
    return [p for p in out if not (p in seen or seen.add(p))]


def read_unlock_records(root: Path) -> dict[str, dict]:
    """读取每份契约自己的解冻记录，聚合全部 flow。

    新格式把申报理由和窗口建立时的旧 SHA 一起写进对应文件。旧版本只有纯文本
    理由，仍可显示和关闭，但不能被 `bump` 消费；这样兼容旧状态不会把一次没有
    基线的修改伪装成正式变更。

    跨 flow 同名契约的窗口同名文件冲突时（`unlock/<名>` 只按契约名，不按 flow），
    任一 flow 有窗口就算开 —— 拒绝放行的方向，不会把 A flow 的窗口当 B flow 的用。
    """
    out: dict[str, dict] = {}
    for flow in all_flows(root):
        d = state_path(root, flow).parent / "unlock"
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if not f.is_file():
                continue
            try:
                raw = f.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            record = out.setdefault(f.name, {"reason": raw, "sha": None})
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                record["reason"] = str(payload.get("reason") or "")
                record["sha"] = payload.get("sha")
                for key in ("version", "revision", "opened_at"):
                    if key in payload:
                        record[key] = payload[key]
    return out


def read_unlocks(root: Path) -> dict[str, str]:
    """当前全部解冻窗口：{契约名: 申报理由}。无窗口返回 {}。

    一份契约一个文件，不是单个 `unlock` 文件。单文件时 `bump` 一份产物契约会给
    每个消费方建同步任务（`artifact-requirements` 的消费方是 analyst 与
    architect），两者并行改各自那份冻结产物时后一个 `unlock` 覆盖前一个 ——
    前者刚申报完就被拒，拒绝理由还是「先申报」。产物冻结让这条路径从
    「理论可能」变成「bump 之后必然发生」，所以窗口必须按契约分开。

    这里保留旧的字符串返回形状给显示路径；需要验证窗口基线的变更命令使用
    `read_unlock_records()`，避免把 SHA 丢掉。
    """
    return {name: record.get("reason", "")
            for name, record in read_unlock_records(root).items()}


def close_unlock(root: Path, name: str = "", flow: str | None = None) -> None:
    """关掉解冻窗口：给 name 只关那一份，不给关全部。

    flow 给了只关那个 flow 的窗口（CLI 与 SubagentStop 都按定点 flow 关）；
    不给则关全部 flow 里同名的那份（workspace 级显式清理）。SubagentStop 曾对
    全部 flow 关窗兜底，理由是「宁可多关不能悬挂」—— 实测多关会把别的 flow 里
    改到一半的契约拆成「bump 没窗口、unlock 说正文漂移」的死局，误关不是重做
    一遍申报，是死锁。清理必须跟 doing 判断用同一个 flow 定点。
    """
    flows = [flow] if flow else all_flows(root)
    for fl in flows:
        d = state_path(root, fl).parent / "unlock"
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.is_file() and (not name or f.name == name):
                f.unlink(missing_ok=True)


def read_disputes(root: Path) -> dict[str, str]:
    """当前全部争议（聚合全部 flow）：{契约名: 争议理由}。无争议返回 {}。

    一份契约一个文件。争议是「全线停工」信号，比解冻窗口更重 —— 解冻只放行
    owner 改一份文件，争议是拦住所有 developer 的所有写入（执行记录与 /tmp 除外）。
    争议在工作区级生效：A flow 的契约争议，别的 flow 的 developer 一起停工 ——
    这是「全线停工」的本来语义。
    """
    out: dict[str, str] = {}
    for flow in all_flows(root):
        d = state_path(root, flow).parent / "disputes"
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if not f.is_file():
                continue
            try:
                out[f.name] = f.read_text(encoding="utf-8").strip()
            except OSError:
                continue
    return out


def close_dispute(root: Path, name: str = "", flow: str | None = None) -> None:
    """关掉争议哨兵：给 name 只关那一份，不给关全部。

    flow 给了关那个 flow（CLI 路径）；不给则关全部 flow 的同名哨兵 —— 争议的
    显示与解除都是按契约名说话的，残留哨兵会让全线继续停工。
    """
    flows = [flow] if flow else all_flows(root)
    for fl in flows:
        d = state_path(root, fl).parent / "disputes"
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.is_file() and (not name or f.name == name):
                f.unlink(missing_ok=True)


def log(st: dict, event: str, **fields) -> None:
    st["log"].append({"at": now(), "event": event, **fields})


def die(msg: str, code: int = 1) -> "None":
    release_state_lock()  # 半途退出不留着锁：selfcheck 全程同进程，留着会自阻塞
    print(f"错误：{msg}", file=sys.stderr)
    sys.exit(code)


def find_task(st: dict, tid: str) -> dict | None:
    """按 ID 或标题查找任务。ID 匹配不区分大小写。"""
    tid_upper = tid.upper()
    for t in st["tasks"]:
        if t["id"].upper() == tid_upper or t.get("title") == tid:
            return t
    return None


def find_contract(st: dict, name: str) -> dict | None:
    for c in st["contracts"]:
        if c["name"] == name:
            return c
    return None


def sha256_file(p: Path) -> str | None:
    if not p.is_file():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


# --------------------------------------------------------------------------
# 本地契约与任务绑定
# --------------------------------------------------------------------------


def contract_revision(c: dict) -> int:
    """Return a usable revision for current and legacy local contracts."""
    revision = c.get("revision")
    if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 1:
        return revision
    version = c.get("version")
    if isinstance(version, int) and not isinstance(version, bool) and version >= 1:
        return version
    return 1


def contract_binding(c: dict) -> dict:
    """Snapshot the exact local contract revision a task was created against."""
    version = c.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        version = 1
    return {
        "name": c["name"],
        "version": version,
        "revision": contract_revision(c),
        "sha": c.get("sha"),
    }


def contract_ref_name(ref) -> str:
    if isinstance(ref, str):
        return ref
    if isinstance(ref, dict):
        return str(ref.get("name") or "")
    return ""


def task_contract_names(t: dict) -> list[str]:
    return [name for name in (contract_ref_name(r) for r in t.get("contracts", [])) if name]


def migrate_contract_refs(st: dict) -> None:
    """Validate legacy refs without guessing the revision they were created against.

    A legacy string contains only a contract name, so resolving it to the current
    contract would silently claim that an old task was created against today's
    version. Keep the string visible and require an explicit task reopen to refresh
    it into a complete snapshot.
    """
    for c in st.get("contracts", []):
        if not isinstance(c, dict):
            continue
        c["revision"] = contract_revision(c)

    for t in st.get("tasks", []):
        refs = t.get("contracts", [])
        if not isinstance(refs, list):
            refs = [refs]
        errors = []
        for ref in refs:
            if isinstance(ref, str):
                c = find_contract(st, ref)
                if c and c.get("sha") and c.get("path"):
                    errors.append(
                        f"契约 {ref} 使用旧字符串引用，无法证明任务创建时的版本；"
                        "请 task reopen 刷新当前快照"
                    )
                elif not c:
                    errors.append(f"契约 {ref} 不存在，无法迁移旧字符串引用")
                elif not c.get("sha"):
                    errors.append(f"契约 {ref} 尚未锁定，无法迁移旧字符串引用")
                else:
                    errors.append(f"契约 {ref} 没有本地路径，无法迁移旧字符串引用")
            elif not isinstance(ref, dict):
                errors.append("契约引用不是字符串或绑定对象")
        t["contracts"] = refs
        if errors:
            t["contract_binding_errors"] = errors
        else:
            t.pop("contract_binding_errors", None)


def contract_binding_check(root: Path, st: dict, ref, include_window: bool = True) -> tuple[bool, str]:
    """Validate one task binding against the current local contract and file."""
    if not isinstance(ref, dict):
        name = contract_ref_name(ref) or "<未知>"
        return False, f"契约 {name} 引用未完成迁移，不能安全重新绑定；请重建任务"
    required = ("name", "version", "revision", "sha")
    if any(key not in ref for key in required):
        return False, f"契约引用字段不完整：需要 name/version/revision/sha（{ref!r}）"
    if (not isinstance(ref.get("name"), str) or not ref.get("name") or
            not isinstance(ref.get("version"), int) or isinstance(ref.get("version"), bool) or
            ref.get("version") < 1 or not isinstance(ref.get("revision"), int) or
            isinstance(ref.get("revision"), bool) or ref.get("revision") < 1 or
            not isinstance(ref.get("sha"), str) or
            not re.fullmatch(r"[0-9a-fA-F]{64}", ref.get("sha"))):
        return False, f"契约引用字段类型或值非法：需要有效的 name/version/revision/sha（{ref!r}）"
    name = ref.get("name")
    c = find_contract(st, name)
    if not c:
        return False, f"契约 {name or '<未知>'} 不存在"
    if not c.get("path"):
        return False, f"契约 {name} 没有本地路径，当前版本不支持该契约"
    if not c.get("sha"):
        return False, f"契约 {name} 尚未锁定"
    current = contract_binding(c)
    for key in required:
        if ref.get(key) != current.get(key):
            return False, (f"契约 {name} 绑定已变化：任务绑定 v{ref.get('version')}"
                           f"/r{ref.get('revision')} 与当前 v{current['version']}"
                           f"/r{current['revision']} 不一致")
    if include_window and name in read_unlocks(root):
        return False, f"契约 {name} 解冻窗口仍开启，必须先 bump 重新锁定"
    current_sha = sha256_file(root / c["path"])
    if current_sha is None:
        return False, f"契约 {name} 文件缺失：{c['path']}"
    if current_sha != ref["sha"]:
        return False, f"契约 {name} 文件已漂移，任务绑定 SHA 不再匹配"
    return True, "一致"


def task_contract_errors(root: Path, st: dict, t: dict, include_window: bool = True) -> list[str]:
    errors = list(t.get("contract_binding_errors") or [])
    for ref in t.get("contracts", []):
        ok, detail = contract_binding_check(root, st, ref, include_window=include_window)
        if not ok:
            errors.append(detail)
    return list(dict.fromkeys(errors))


def task_binding_for_name(root: Path, st: dict, name: str) -> dict:
    """解析一次任务契约引用并固定为当前完整快照。"""
    c = find_contract(st, name)
    if not c:
        die(f"契约不存在：{name}")
    if not c.get("path"):
        die(f"契约 {name} 没有本地路径，当前版本不支持该契约")
    if not c.get("sha"):
        die(f"契约 {name} 尚未首次 lock，任务不能绑定未锁定契约")
    binding = contract_binding(c)
    ok, detail = contract_binding_check(root, st, binding)
    if not ok:
        die(f"契约 {name} 当前不可绑定：{detail}")
    return binding


def validate_task_contracts(root: Path, st: dict, t: dict, action: str) -> None:
    errors = task_contract_errors(root, st, t)
    if errors:
        die(f"任务 {t['id']} {action} 被拒：" + "; ".join(errors))


def task_dependency_errors(st: dict, t: dict) -> list[str]:
    """返回任务尚未满足的依赖。

    只有 done/skipped 是完成语义。特别不能把「任务存在」或 todo/doing 当成
    满足，否则下游可以在上游尚未产出时开始甚至完成。
    """
    errors = []
    for dep_id in t.get("deps", []):
        dep = find_task(st, dep_id)
        if not dep:
            errors.append(f"依赖任务 {dep_id} 不存在")
        elif dep.get("status") not in ("done", "skipped"):
            errors.append(f"依赖任务 {dep_id} 当前为 {dep.get('status')}，只有 done/skipped 满足")
    return errors


def task_check_errors(root: Path, st: dict, t: dict) -> list[str]:
    """统一的任务开工/收工校验，不改变状态。"""
    errors = []
    if t.get("status") in ("blocked", "stale"):
        errors.append(f"任务当前为 {t['status']}，不能继续")
    errors.extend(task_dependency_errors(st, t))
    errors.extend(task_contract_errors(root, st, t))
    return list(dict.fromkeys(errors))


def refresh_task_contracts(root: Path, st: dict, t: dict) -> list[str]:
    """显式 reopen 时把任务绑定刷新为当前已锁定快照。"""
    refs = t.get("contracts", [])
    if not isinstance(refs, list):
        return [f"任务 {t['id']} 的契约引用不是列表，不能恢复"]
    refreshed = []
    errors = []
    for ref in refs:
        name = contract_ref_name(ref)
        if not name:
            errors.append(f"任务 {t['id']} 有空契约引用")
            continue
        c = find_contract(st, name)
        if not c or not c.get("path") or not c.get("sha"):
            errors.append(f"契约 {name} 不存在、没有路径或尚未锁定")
            continue
        binding = contract_binding(c)
        ok, detail = contract_binding_check(root, st, binding)
        if not ok:
            errors.append(detail)
        else:
            refreshed.append(binding)
    if not errors:
        t["contracts"] = refreshed
        # 旧版字符串引用只允许通过显式 reopen 刷新；成功后清掉迁移时留下的
        # 不可安全重绑定错误，否则任务即使已经拿到完整快照也会永久失败。
        t.pop("contract_binding_errors", None)
    return list(dict.fromkeys(errors))


def dotted_set(obj: dict, key: str, value) -> None:
    parts = key.split(".")
    for k in parts[:-1]:
        obj = obj.setdefault(k, {})
    obj[parts[-1]] = value


def dotted_get(obj, key: str):
    for k in key.split("."):
        if not isinstance(obj, dict) or k not in obj:
            return None
        obj = obj[k]
    return obj


# --------------------------------------------------------------------------
# 门禁引擎
# --------------------------------------------------------------------------

def artifact_path(root: Path, phase: str, fname: str) -> Path:
    """阶段产物路径，带 flow 维度：<root>/.workbench/artifacts/<flow>/<phase>/<fname>。

    CLI 按当前 flow 定位。老布局（artifacts/<phase>/ 直接在 .workbench/ 下）回退
    读取，读写都指同一个位置 —— 存量项目的路径不变。
    """
    flow = read_current_flow(root)
    p = wb_dir(root) / "artifacts" / flow / phase / fname
    legacy = wb_dir(root) / "artifacts" / phase / fname
    if flow == DEFAULT_FLOW and legacy.is_file() and not p.is_file():
        return legacy
    return p


def contract_drift(root: Path, st: dict) -> list[str]:
    """返回发生漂移、缺失或仍在修改窗口中的本地契约。"""
    bad = []
    opened = read_unlocks(root)
    for c in st.get("contracts", []):
        path = c.get("path")
        if not path:
            bad.append(f"{c.get('name', '<未知>')}：契约缺少本地路径")
            continue
        # 未锁定的契约尚未建立基线，允许在首次 lock 前编辑；锁定后才检查哈希。
        if c.get("sha"):
            cur = sha256_file(root / path)
            if cur is None:
                bad.append(f"{c['name']}：文件缺失 {path}")
            elif cur != c["sha"]:
                bad.append(f"{c['name']}：漂移，内容已变更但未 bump（当前 v{c.get('version', 1)}）")
        if c.get("name") in opened:
            bad.append(f"{c['name']}：解冻窗口开启，修改尚未 bump")
    return bad


def _ts_epoch(ts):
    """把 now() 写出的时间戳字符串转 epoch 秒；解析不了返回 None。"""
    if not ts:
        return None
    try:
        return datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except (ValueError, TypeError):
        return None


def lease_expired(t: dict) -> bool:
    """doing 任务的租约是否已过期。非 doing、无租约、解析不了时间都算未过期
    （宁可不误报抢占，租约只是可见性提示，不自动改状态）。"""
    if t.get("status") != "doing":
        return False
    exp = _ts_epoch(t.get("lease_until"))
    return exp is not None and time.time() > exp


def retro_enter_epoch(st: dict):
    """本 flow 最近一次进入 retro 的时间（epoch 秒），拿不到返回 None。

    knowledge/ 是跨 flow 共享的长期知识库（一个目录好 grep，见 README），所以
    `knowledge_written` 不能只问「目录里有没有条目」—— 任何历史条目都会让所有后续
    flow 的门禁白蹭过去，沉淀出口的强制性就没了。用「进入 retro 的时刻」当锚点，
    只有本轮复盘期间新写/更新的条目才算数。log 里没有进 retro 的记录时返回 None，
    调用方退回旧行为（不新增拦截），不因锚点缺失把门禁变严。"""
    anchor = None
    for e in st.get("log", []):
        if e.get("event") in ("phase_advance", "phase_set") and e.get("to") == "retro":
            anchor = e.get("at")
    if not anchor:
        return None
    try:
        return datetime.datetime.strptime(anchor, "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except (ValueError, TypeError):
        return None


def knowledge_entries(root: Path) -> list[Path]:
    """知识库条目：`knowledge/` 下递归取全部条目文件。

    条目按知识类别分目录（类别与边界见 knowledge/README.md），所以这里必须递归：
    只 glob 顶层会让分目录后的条目对 `knowledge_written` 门禁与 status 计数隐形 ——
    沉淀明明写了，门禁却说没有。类别索引 `index.md` 与库自身的 `README.md` 是路由
    文件不是条目，不计数。"""
    kdir = root / "knowledge"
    if not kdir.is_dir():
        return []
    return sorted(p for p in kdir.rglob("*.md")
                  if p.name not in ("README.md", "index.md"))


def run_check(root: Path, st: dict, phase: str, spec: str) -> tuple[bool, str, str]:
    """执行单条门禁断言，返回 (通过, 标签, 说明)。"""
    kind, _, rest = spec.partition(":")

    if kind == "artifact_contains":
        fname, _, needle = rest.partition(":")
        p = artifact_path(root, phase, fname)
        label = f"{fname} 包含「{needle}」"
        if not p.is_file():
            return False, label, "产物文件不存在"
        ok = needle in p.read_text(encoding="utf-8", errors="replace")
        return ok, label, "已覆盖" if ok else "缺少该章节"

    if kind == "contracts_locked":
        # 只数真正的接口契约。阶段产物自动登记的那些（kind="artifact"）不算 ——
        # 否则 clarify / analyze 过完门禁后契约列表永远非空，这条断言就再也逼不出
        # 「并行开发前先把接口定下来」。
        real = [c for c in st["contracts"] if c.get("kind") != "artifact"]
        unlocked = [c["name"] for c in real if not c.get("sha")]
        if not real:
            return False, "契约已锁定", "尚未登记任何接口契约（无接口的纯本地改动可 --force 跳过）"
        return (not unlocked), "契约已锁定", "全部锁定" if not unlocked else f"未锁定：{', '.join(unlocked)}"

    if kind == "contracts_intact":
        bad = contract_drift(root, st)
        return (not bad), "契约无漂移", "一致" if not bad else "; ".join(bad)

    if kind == "tasks_exist":
        n = len(st["tasks"])
        return n > 0, "已拆解任务", f"{n} 个任务" if n else "任务列表为空"

    if kind == "tasks_done":
        target = rest
        pool = st["tasks"] if target == "*" else [t for t in st["tasks"] if t["phase"] == target]
        # done 和 skipped 算完成；stale 算未完成（上游被推翻，需要重跑）
        left = [t["id"] for t in pool if t["status"] not in ("done", "skipped")]
        label = f"{target} 任务全部完成"
        if not pool:
            return True, label, "无任务（视为通过）"
        stale_ids = [t["id"] for t in pool if t["status"] == "stale"]
        if left:
            detail = f"未完成：{', '.join(left)}"
            if stale_ids:
                detail += f"（其中 stale：{', '.join(stale_ids)}，上游被推翻需重跑）"
            return False, label, detail
        return True, label, "全部完成"

    if kind == "no_blocked":
        pool = st["tasks"] if rest == "*" else [t for t in st["tasks"] if t["phase"] == rest]
        blocked = [t["id"] for t in pool if t["status"] in ("blocked", "stale")]
        label = "无阻塞/失效任务" if rest == "*" else f"{rest} 无阻塞/失效任务"
        return (not blocked), label, "无" if not blocked else f"阻塞/失效：{', '.join(blocked)}"

    if kind == "knowledge_written":
        # 沉淀出口：knowledge/ 有【本轮复盘写的】条目，或 retro.md 显式声明无可沉淀。
        # 两个出口都没有 = 复盘学到的经验跟着 artifacts 一起归档了，下个需求重新踩。
        # knowledge/ 是跨 flow 共享目录，只数「有没有条目」会让任何历史条目替所有后续
        # flow 白过门禁 —— 按进入 retro 的时刻过滤，只认本轮新写/更新的（mtime >= 锚点）。
        # 目录不存在按「无条目」处理而不是报错 —— 存量项目第一次跑 retro 门禁时还没有它。
        all_entries = knowledge_entries(root)
        anchor = retro_enter_epoch(st)
        # 锚点拿不到（log 无进 retro 记录）退回旧行为：全部条目都算，不因缺锚点变严。
        entries = [p for p in all_entries
                   if anchor is None or p.stat().st_mtime >= anchor]
        label = "经验已沉淀（knowledge/）"
        if entries:
            names = ", ".join(p.relative_to(root / "knowledge").as_posix()
                              for p in entries[:5])
            if len(entries) > 5:
                names += f" 等 {len(entries)} 条"
            return True, label, f"{len(entries)} 条：{names}"
        p = artifact_path(root, phase, "retro.md")
        if p.is_file() and "无可沉淀" in p.read_text(encoding="utf-8", errors="replace"):
            return True, label, "retro.md 显式声明无可沉淀"
        stale = ("；knowledge/ 里已有 %d 条但都不是本轮写的（跨 flow 历史条目不替本轮过门禁）"
                 % len(all_entries)) if all_entries else ""
        return False, label, ("knowledge/ 无本轮沉淀条目。把可复用经验按判据写成条目"
                              "（按类别放进 knowledge/<类别>/，判据、分类与格式见"
                              " knowledge/README.md，或派 knowledger 角色）；"
                              "确无可沉淀时在 retro.md 沉淀章节写明「无可沉淀：<理由>」" + stale)

    if kind == "improvements_tracked":
        # 改进项要能落地：每条要么已转成任务（T<ID>），要么当场改完，要么明确放弃。
        # 没有落地标识的改进项 = 写进散文就消失 —— 实证：flow main 的 retro.md 里
        # 「role scopes 分节」「门禁噪音标注」两条改进项有落地动作、有验收判据，
        # 之后无人跟踪、至今未做。与 knowledge_written 同构：给「无改进项」一个显式
        # 出口，避免为过门禁造假条目。
        p = artifact_path(root, phase, "retro.md")
        label = "改进项已跟踪落地"
        if not p.is_file():
            return False, label, "产物文件不存在"
        m = re.search(r"^#+[^\n]*改进项[^\n]*\n(.*?)(?=^#+\s|\Z)",
                      p.read_text(encoding="utf-8", errors="replace"), re.M | re.S)
        if not m:
            return False, label, "缺少「改进项」章节"
        section = m.group(1)
        rows = [l.strip() for l in section.splitlines() if l.strip().startswith("|")]
        # 表格首行是表头，分隔行只含 | - : 空格
        data_rows = [l for l in rows if re.search(r"[^\s|:\-]", l)][1:]
        if not data_rows:
            if "无改进项" in section:
                return True, label, "已声明无改进项"
            return False, label, ("改进项章节没有条目；确实没有时在该章节写明「无改进项」"
                                  "（有改进项则用表格逐条列出）")
        bad = [r for r in data_rows
               if not (re.search(r"T\d+", r) or "已落地" in r or "不修" in r)]
        if bad:
            return False, label, ("未跟踪：" + " / ".join(b[:40] for b in bad[:3])
                                  + " —— 转成任务写 `T<ID>`，当场改完写「已落地」，"
                                    "放弃写「不修：<理由>」")
        return True, label, f"{len(data_rows)} 条均有落地标识"

    if kind == "cmd":
        cmd = st["gate_commands"].get(rest)
        label = f"命令门禁 {rest}"
        if not isinstance(cmd, str) or not cmd.strip():
            # 三态：未配置 / 明确不适用（waiver）/ 已通过。未配置仍不阻断（纯文档项目
            # 没有 build 命令），但要显著提示 —— 否则「没测试」是隐形绿灯，verify 门禁形同
            # 虚设。项目确实没有该门禁时用 `config set gate_waivers.<名> '<理由>'` 显式声明，
            # 把「碰巧没配」和「明确不需要」区分开。
            waiver = st.get("gate_waivers", {}).get(rest)
            if isinstance(waiver, str) and waiver.strip():
                return True, label, f"已豁免（gate_waivers.{rest}）：{waiver}"
            return True, label, ("未配置，跳过 —— 该门禁未生效。配置："
                                 f"config set gate_commands.{rest} '<命令>'；"
                                 f"项目确实不需要则显式豁免：config set gate_waivers.{rest} '<理由>'")
        # 老 state 里可能已经存着灾难性命令（新校验只管新写入），执行前再筛一遍。
        why = catastrophic_command(cmd)
        if why:
            return False, label, (f"拒绝执行（{why}）：`{cmd}`；"
                                  f"先 config set gate_commands.{rest} 换成安全命令")
        # 完整输出必须落盘。门禁刚跑过一遍，若只留汇总行，诊断就得再跑一遍。
        logf = state_path(root).parent / f"gate-{rest}.log"
        rel_log = os.path.relpath(logf, root)

        def _text(v) -> str:
            if v is None:
                return ""
            return v.decode("utf-8", "replace") if isinstance(v, bytes) else v

        def _record(out: str, verdict: str) -> str:
            logf.write_text(f"$ {cmd}\n[{verdict}] {now()}\n\n{out}", encoding="utf-8")
            tail = out.strip().splitlines()[-5:]
            # selfcheck 类命令的输出里满是「拒绝」行 —— 那是它自己的负向用例（故意触发
            # 守卫验证拦截生效），不是门禁失败。不注明会让人愣一下（flow main 的 retro
            # 改进项 3）。
            note = ("\n      （输出中的「拒绝」行是被测命令自身的负向用例，属预期）"
                    if "selfcheck" in cmd else "")
            return (f"完整输出见 {rel_log}"
                    + ("\n" + "\n".join("      " + l[:200] for l in tail) if tail else "")
                    + note)

        limit = st.get("gate_timeout") or 1800
        try:
            r = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True, timeout=limit)
        except subprocess.TimeoutExpired as e:
            # 超时是一条 FAIL 结论，不是崩溃。CLI 路径没有兜底 try，
            # 不捕获会让 `gate check` / `phase advance` 打出 Traceback。
            body = _record(_text(e.stdout) + _text(e.stderr), "TIMEOUT")
            return False, label, f"`{cmd}` 超时（>{limit}s）{body}"
        out_combined = r.stdout + r.stderr
        if r.returncode == 0:
            # 退出码 0 不等于测试跑过了 —— 主 Agent 同时是命令的选择者、执行者和判定者。
            # 退出码因此不是独立证据，需要额外扫描。
            body = _record(out_combined, f"exit={r.returncode}")
            # 1. 命令文本含跳过测试的标志
            skip_flags = re.search(
                r"-DskipTests|-Dmaven\.test\.skip|--skipTests|--passWithNoTests"
                r"|--ignore-skipped|-Dskip\.tests",
                cmd, re.IGNORECASE)
            if skip_flags:
                return False, label, f"`{cmd}` 含跳过测试标志（{skip_flags.group()}），记 unverified{body}"
            # 2. 日志匹配零用例执行
            zero_tests = re.search(
                r"0\s+(tests?|passed|specs?)|No\s+tests?\s+ran|"
                r"collected\s+0\s+items|0\s+selected\s+0\s+collected",
                out_combined, re.IGNORECASE)
            if zero_tests:
                return False, label, f"`{cmd}` 退出码 0 但零用例执行，记 unverified{body}"
            return True, label, f"`{cmd}` exit={r.returncode} " + body
        return False, label, f"`{cmd}` exit={r.returncode} " + _record(out_combined, f"exit={r.returncode}")

    return False, spec, "未知门禁类型"


def gate_check(root: Path, st: dict, phase: str) -> list[tuple[bool, str, str]]:
    rules = GATES.get(phase, {})
    out = []
    for fname in rules.get("artifacts", []):
        p = artifact_path(root, phase, fname)
        ok = p.is_file() and p.stat().st_size > 0
        out.append((ok, f"产物 {phase}/{fname}", "已产出" if ok else "缺失或为空"))
    for spec in rules.get("checks", []):
        out.append(run_check(root, st, phase, spec))
    return out


def print_gate(phase: str, results: list[tuple[bool, str, str]]) -> bool:
    # 门禁命令的完整输出已经落 gate-<key>.log（run_check 内部写的），但 detail
    # 摘要只带最后 5 行 —— 摘要不够诊断时，人工抽查要能一眼找到日志路径，不能
    # 逼着重跑一次命令才能看全量输出。detail 里已经嵌了 `完整输出见 <path>`，
    # 这里不重复解析，只是让路径本身单独成行，方便脚本 grep。
    print(f"门禁 · {phase}（{PHASE_CN.get(phase, phase)}）")
    for ok, label, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label} — {detail}")
    passed = all(ok for ok, _, _ in results)
    print(f"结论：{'通过' if passed else '未通过'}")
    return passed


# --------------------------------------------------------------------------
# 调度：就绪任务
# --------------------------------------------------------------------------

def ready_tasks(st: dict, phase: str | None = None, role: str | None = None) -> list[dict]:
    done = {t["id"].upper() for t in st["tasks"] if t.get("status") in ("done", "skipped")}
    out = []
    for t in st["tasks"]:
        if t["status"] != "todo":
            continue
        if phase and t["phase"] != phase:
            continue
        if role and t["role"] != role:
            continue
        if all(d.upper() in done for d in t.get("deps", [])):
            out.append(t)
    order = {p: i for i, p in enumerate(PHASES)}
    out.sort(key=lambda t: (order.get(t["phase"], 99), t["id"]))
    return out


# --------------------------------------------------------------------------
# CLI 命令实现
# --------------------------------------------------------------------------

def repo_layout_scopes(root: Path) -> dict[str, list[str]] | None:
    """跨仓库布局（`repos/<仓库>/`）下的默认角色范围；不是这个布局返回 None。

    默认值在这个布局下同时错两个方向，而且静默：`fnmatch` 的 `*` 跨 `/`，所以
    `*.py` 放行任意仓库里的 Python 文件；`migrations/**` 又要求字符串以它开头，
    匹配不到 `repos/backend/migrations/`。于是默认值退化成「按语言隔离」——
    后端写不了自己的迁移（看起来像守卫抽风），却能写前端仓库（没人会发现）。

    跨仓库时仓库本身就是边界，所以按目录名认领仓库：认领到了就整个仓库放行，
    `repos/<仓库>/**` 是那些裸扩展名模式的超集。

    认不出仓库名的角色（`qa` 永远如此 —— 它没有仓库提示词）退回「任意仓库的对应
    位置」，模式逐条加 `repos/*/` 前缀。这里必须**带上裸扩展名模式**，否则 qa 只
    剩四个测试目录，配不了 `repos/frontend/vitest.config.ts` —— 与单仓库下同一个
    误拦，只是布局 B 下更难发现。跨仓库放行是这个分支本来就有的性质
    （`repos/*/src/**` 一样跨），加裸扩展名没有新破的边界。

    ponytail: 认领靠目录名。谁都没认领的仓库（`shared` / `payments-svc`）落在所有
    角色范围之外 —— 宁可拦住也不跨仓库放行，由 `unclaimed_repos()` 在 init 与
    `role scopes` 里点名，手写前缀认领。
    """
    d = root / "repos"
    repos = sorted(p.name for p in d.iterdir() if p.is_dir()) if d.is_dir() else []
    if not repos:
        return None
    out = {}
    for role, pats in DEFAULT_ROLE_SCOPES.items():
        mine = [r for r in repos if any(h in r.lower() for h in REPO_HINTS.get(role, ()))]
        extra = [f"repos/{r}/**" for r in mine] or \
                [f"repos/*/{p}" for p in pats
                 if not p.startswith((".workbench/", "knowledge/"))]
        # knowledge/ 与 .workbench/ 同免改写：知识库挂在工作区根（一个工作区一份），
        # 改写成 repos/*/knowledge/** 会跟 knowledge_written 门禁检查点错位。
        out[role] = [p for p in pats
                     if p.startswith((".workbench/", "knowledge/"))] + extra
    return out


def unclaimed_repos(root: Path, scopes: dict[str, list[str]]) -> list[str]:
    """`repos/` 下没有开发角色能写代码的仓库。

    只要有一个仓库被认领，`repo_layout_scopes` 就走 `repos/<仓库>/**` 分支，于是
    认不出名字的仓库谁都写不了 —— 是硬拦，不是跨仓库放行。这个失败要到 develop
    阶段才暴露成一次权限拒绝，所以 init 与 `role scopes` 提前点名。

    判定按守卫的方式拿探路径去撞模式，只看写代码的两个角色：`qa` 的
    `repos/*/tests/**` 覆盖所有仓库，但「只有 qa 能写它的测试目录」不是认领。
    全都认不出名字时两个角色都拿到 `repos/*/src/**`，探路径命中，不会误报。
    """
    d = root / "repos"
    if not d.is_dir():
        return []
    pats = [p for r in ("frontend-developer", "backend-developer")
            for p in scopes.get(r, ())]
    return [r.name for r in sorted(d.iterdir()) if r.is_dir() and not any(
        fnmatch.fnmatch(f"repos/{r.name}/src/probe{ext}", p)
        for ext in (".ts", ".py") for p in pats)]


# 新 flow 从 main 继承的工作区级配置。这些键描述的是「这个工作区怎么构建、怎么
# 测、仓库怎么认领」，不是单条需求线的属性 —— 但它们存在每条 flow 各自的 state.json
# 里，不继承就等于每条新 flow 都要重抄一遍，漏抄的仓库认领会让角色范围在指针
# 切换后整个换掉。
INHERIT_KEYS = ("role_scopes", "gate_commands", "gate_timeout", "max_parallel")


def inherit_flow_config(root: Path, st: dict, flow: str) -> str:
    """新 flow 从 main 继承工作区级配置，返回来源 flow 名（没有可继承的则空串）。

    main 是配置的事实标准源：`config set` 在哪条 flow 上跑，别的 flow 都看不到，
    而守卫的角色范围检查读的是指针 flow 的 state —— 不同 flow 各配一套，同一批
    agent 会随指针切换被按不同范围判定。任务、契约、阶段不继承：那是每条需求线
    自己的进度。
    """
    if flow == DEFAULT_FLOW:
        return ""
    try:
        donor = json.loads(state_path(root, DEFAULT_FLOW).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(donor, dict):
        return ""
    for key in INHERIT_KEYS:
        if key in donor:
            st[key] = json.loads(json.dumps(donor[key]))
    return DEFAULT_FLOW



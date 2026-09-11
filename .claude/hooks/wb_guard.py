"""wb_guard — Workflow Guard：PreToolUse / PostToolUse / SessionStart / SubagentStop。

wb.py 拆分模块之一。状态、契约、任务快照、争议熔断和特权子命令检查都在这里；
状态经 wb_core 读写，命令解析经 wb_bash。不提供 CLI 入口 —— 由
wb_cli.cmd_hook（hook 子命令）与 selfcheck 调用。"""

from __future__ import annotations

import json
import fnmatch
import os
import re
import shlex
import sys
from pathlib import Path

from wb_const import (
    ARTIFACT_LOG, BASH_WRITE, DEFAULT_ROLE_SCOPES, DEVELOPER_ROLES, FROZEN_ALWAYS,
    GUARDED_PREFIXES, PHASE_CN, READ_TOOL, ROLES, SHELL_TOOL, WORKSPACE_GUARDED_PREFIXES,
    WRITE_TOOL,
)
from wb_bash import (
    _split_pipeline, _strip_wrappers, resolve, strip_heredocs,
)
from wb_core import (
    all_flows, close_unlock, contract_drift, die, find_contract, find_root, load_state,
    log, now, pointer_flow, read_current_flow, read_disputes, read_frozen, read_unlocks,
    ready_tasks, save_state, set_flow_override, state_path, task_contract_errors, wb_dir,
)


# --------------------------------------------------------------------------
# Hook 实现
# --------------------------------------------------------------------------

def hook_deny(reason: str) -> "None":
    """PreToolUse：退出码 2 = 阻止调用，stderr 回灌给模型。"""
    print(f"[工作台 Workflow Guard] 拒绝：{reason}", file=sys.stderr)
    sys.exit(2)


def resolve_target(cwd: Path, raw: str) -> Path:
    p = Path(raw)
    if not p.is_absolute():
        p = cwd / p
    try:
        return p.resolve()
    except OSError:
        return p


def nested_roots(target: Path, session_root: Path) -> list[Path]:
    """从写入目标向上找嵌套在会话根之内的其它工作台根（多仓库布局 A）。

    会话 cwd 在工作区外层时 find_root() 命中外层，但目标可能落在某个自带
    .workbench/ 的仓库里 —— 那个仓库锁的契约与状态文件在外层清单里不存在，
    只查外层会静默放行。范围只到会话根为止：不走到文件系统根，否则会把
    用户 home 下不相干的工作区也捡进来。
    """
    rootr = session_root.resolve()
    out: list[Path] = []
    for p in target.parents:
        if p == rootr:
            break
        if (p / ".workbench").is_dir():
            out.append(p)
    return out


def _is_dispute_exempt_bash(cmd: str, root: Path) -> bool:
    """争议熔断下 Bash 命令是否放行。

    只放行两样：/tmp 下的操作、.workbench/artifacts/<flow>/develop/ 下自己的执行记录。
    粗判：命令里提到放行路径就放行。争议时全线停工是第一优先级。
    """
    # 只涉及 /tmp 且不碰 .workbench/.claude
    if re.search(r'\b/tmp/\S', cmd) and not re.search(r'\.(workbench|claude)', cmd):
        return True
    # 写自己的执行记录（任意 flow）
    if re.search(r'\.workbench/artifacts/[^/]+/develop/', cmd):
        return True
    return False


def _is_dispute_exempt_write(rel: str) -> bool:
    """争议熔断下 Write/Edit 是否放行。只放行 .workbench/artifacts/<flow>/develop/ 下的文件。"""
    return re.match(r"\.workbench/artifacts/[^/]+/develop/", rel) is not None


def _dispute_deny(disputes: dict[str, str]) -> "None":
    """争议熔断的拒绝话术。终止令风格，不是权限错误。

    ROMA 的源码注释写得直白：A denied tool call is not a kill: the wording must read
    as a termination order, not a permission error, or the agent just retries another path.
    """
    names = ", ".join(disputes.keys())
    reasons = "; ".join(f"{k}: {v}" for k, v in disputes.items())
    hook_deny(
        f"契约争议熔断生效（{names}）。立即停止开发。\n"
        f"争议：{reasons}\n"
        "不要重试、不要改用其他写入路径、"
        "不要在实现侧加兼容层绕过冲突。\n"
        "把已完成到哪一步、哪些文件已改、还差什么写进你自己的执行记录"
        "（.workbench/artifacts/<flow>/develop/ 下），然后立即返回，"
        "由主 Agent 决定是否重新派发架构角色修订契约。"
    )


def frozen_hits(root: Path, cmd: str) -> list[str]:
    """命令文本里提到的全部冻结路径。

    返回全部而不是第一个：只比对第一个命中时，`sed -i s/a/b/ a.json b.json`
    里若 a.json 正处于解冻窗口，b.json 就被静默放行。

    只匹配相对路径。早期版本还按 basename 匹配，但 `role` / `frozen` /
    `state.json` 这几个词在业务代码里太常见（SQL 的 role 字段、web/state.json），
    误拦率高到把「误拦显式、漏拦静默」这个原则本身推翻。先切目录再写的写法
    由调用方的 `.workbench` 兜底覆盖。
    """
    return [rel for rel in read_frozen(root) if rel in cmd]


def unlocked_paths(root: Path) -> set[str]:
    """当前全部解冻窗口对应的契约路径。窗口按契约分开，状态文件永不可解冻。

    正常状态下一个正文路径只会对应一份契约（contract add 会拒绝重复路径）。
    旧状态若存在重复登记，必须让同一路径的所有契约都解冻后才放行，不能让
    解冻其中一个名字顺带解除另一个名字的冻结。
    """
    names = read_unlocks(root)
    if not names:
        return set()
    by_path = {}
    for flow in all_flows(root):
        try:
            st = json.loads(state_path(root, flow).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for c in st.get("contracts", []):
            path = c.get("path")
            name = c.get("name")
            if path and name:
                by_path.setdefault(path, []).append(name)
    return {path for path, contract_names in by_path.items()
            if all(name in names for name in contract_names)}


def contracts_for(root: Path, rels: list[str]) -> list[dict]:
    """反查这些冻结路径对应的契约。只在即将拒绝时调用，读一次 state.json 不在热路径上。

    查不到就是空列表（`FROZEN_ALWAYS` 那几个不是契约），由调用方退回「只能用 wb.py
    子命令改」那句 —— 给不存在的契约名让人去申报比不给更坏。
    """
    out, seen = [], set()
    for flow in all_flows(root):
        try:
            cur = json.loads(state_path(root, flow).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for c in cur.get("contracts", []):
            if c.get("path") in rels and c.get("name") not in seen:
                seen.add(c.get("name"))
                out.append(c)
    return out


def frozen_advice(root: Path, rels: list[str], role: str = "") -> str:
    """撞上冻结文件时该怎么办。按撞上的人是不是 owner 分岔。

    统一给 `contract unlock` 命令错在两处。一是 `FROZEN_ALWAYS` 不是契约，读的人
    会去申报一个不存在的名字。二是非 owner 角色自己申报也不对：`bump` 会给每个
    消费方建返工任务，那是编排者的调度决定；而 `SubagentStop` 会在它结束时关掉
    悬挂窗口，留下一个改过但没定版的文件，下次 `contract verify` 报漂移。

    名字必须填实的 —— 只有 `pm` 的定义里硬编码了 `artifact-requirements`，其余角色
    撞上自己那份阶段产物时只能猜，而 CLAUDE.md 不许换等价写法绕。
    """
    cs = contracts_for(root, rels)
    if not cs:
        return "状态与进度只能用 wb.py 子命令改。"
    names = ",".join(c["name"] for c in cs)
    owners = {c["owner"] for c in cs}
    if role and role not in owners and role != CONTRACT_STEWARD:
        return (f"它的 owner 是 {'/'.join(sorted(owners))}，不是你 —— 不要自己申报解冻。"
                f"报回编排者：要改 {names}，为什么。由编排者决定是否 "
                f"`wb.py contract unlock --name {names} --reason '<理由>'`，"
                f"或者用 `wb.py task block <ID> --reason '<缺什么>'` 把任务打回。")
    return (f"先申报：`wb.py contract unlock --name {names} --reason '<为什么要改>'`，"
            f"改完 `wb.py contract bump --name {names}` 重新锁定并通知消费方。")


# 内置非角色 subagent 类型白名单：这些 `agent_type` 是 harness 自带的通用工具身份
# （不在 ROLES 里），不是伪造或未知调用者，退回读 role 文件兜底是安全的 —— 文件空时
# 它们本来就该落在「无角色」范围而非被当成越权写手。凡不在此列的非角色 `agent_type`
# 都判 UNKNOWN_ROLE 拒写，堵住「子 worker 顶陌生身份越权」缺口。
BUILTIN_AGENT_TYPES = ("Explore", "general-purpose", "Plan")

# 载荷有 subagent 迹象（`agent_id`）但拿不到可识别身份时的哨兵。它**不是**角色：
# 既不查 role_scopes 也不回落到「无角色放行」，而是直接拒绝写入 —— 把「身份识别坏了」
# 伪装成「这是主线程」是最坏的失败模式（静默降级）。
UNKNOWN_ROLE = "__unknown__"

# 装守卫本体的前缀，拒绝话术要单独说明（交回主线程，别给角色开范围）。
GUARD_BODY_PREFIXES = (".claude/", ".codex/", ".agents/")

# 契约管理员。接口契约由 architect 定义，但 `--owner` 填的是实现方
# （architect.md 里就是 `--owner backend-developer`），所以 owner 校验必须放它一条路，
# 否则 architect.md 写明的「contract impact -> unlock -> 改 -> bump」直接走不通。
# 放行的是「定义接口的人能改接口」，挡住的是「实现方自己改掉要对齐的接口」。
CONTRACT_STEWARD = "architect"


def current_role(root: Path, data: dict) -> str:
    """当前角色：subagent 优先取 hook 载荷里的 `agent_type`，主线程退回读 `.workbench/role`。

    载荷里的 `agent_type` 就是 agent 定义 frontmatter 的 `name`，与 ROLES 同名 ——
    实测（Claude Code 2.1.252）subagent 的 PreToolUse / PostToolUse / SubagentStop
    都带 `agent_type` 与 `agent_id`，主线程两个都没有。所以并行 subagent 各自判定，
    不再抢 `.workbench/role` 那个单文件：谁写的由谁的载荷说，与启动顺序无关。

    三态而非两态：
    - 有 `agent_type` 且是角色名 → 用它
    - 有 `agent_type` 且是内置白名单类型（Explore / general-purpose / Plan）→ 读文件兜底
    - `agent_type` 是陌生值，或无 `agent_type` 但有 `agent_id` → UNKNOWN_ROLE（拒写）
    - 两者都无 → 读 `.workbench/role`（真正的主线程兜底）

    陌生 `agent_type` 曾经和内置白名单走同一条退回文件兜底分支：文件为空时
    `_check_write_target` 的「无角色」直接放行，等于任何顶着陌生身份 spawn 出的子 worker
    都能写到角色范围之外。现在陌生值单独判 UNKNOWN，与 `agent_id`-only 汇入同一拒绝出口。
    """
    at = (data.get("agent_type") or "").strip()
    if at in ROLES:
        return at
    if at in BUILTIN_AGENT_TYPES:
        f = wb_dir(root) / "role"
        return f.read_text(encoding="utf-8").strip() if f.is_file() else ""
    if at or data.get("agent_id"):
        return UNKNOWN_ROLE
    f = wb_dir(root) / "role"
    return f.read_text(encoding="utf-8").strip() if f.is_file() else ""


def active_task_contract_errors(root: Path, rel: str) -> list[str]:
    """返回活动 developer 任务的契约失效，供产品代码写入 hook 使用。

    执行记录是停工协议的一部分，必须能记录阻塞原因，因此对该目录不触发这条
    检查。其余仓库内产品文件只要有一个 doing developer 任务绑定旧快照，就先
    停止写入，避免继续产生无法完成的实现。
    """
    # 活动任务契约检查读当前 flow 的任务表；develop 执行记录按 flow 维度放行。
    if re.match(r"\.workbench/artifacts/[^/]+/develop/", rel):
        return []
    try:
        st = load_state(root)
    except SystemExit:
        return []
    errors = []
    for t in st.get("tasks", []):
        if t.get("status") not in ("doing", "stale", "blocked") or \
                t.get("role") not in DEVELOPER_ROLES:
            continue
        bad = task_contract_errors(root, st, t)
        if bad:
            errors.append(f"{t.get('id', '<未知>')}: {'; '.join(bad)}")
    return errors


def _guarded_prefix(root: Path, rel: str) -> str:
    """命中的守卫前缀；目录条目按前缀匹配，文件条目精确匹配（`repos.json` 是文件）。

    workspace 层条目（`scripts/` `repos.json` `.vscode/`）只在 workbench 布局（存在
    `repos/`）下生效 —— 单仓库适配场景里它们是项目自己的目录，见 wb_const 的说明。
    """
    prefixes = GUARDED_PREFIXES + (
        WORKSPACE_GUARDED_PREFIXES if (root / "repos").is_dir() else ())
    for g in prefixes:
        if g.endswith("/"):
            if rel.startswith(g):
                return g
        elif rel == g or rel.startswith(g + "/"):
            return g
    return ""


def _role_scope_allows(root: Path, scopes: dict, role: str, rel: str) -> list[str]:
    """角色 role 能在 rel 上落笔的模式表（空表 = 不许写）。

    受守前缀（`GUARDED_PREFIXES` / `WORKSPACE_GUARDED_PREFIXES`）下的路径只认**显式以该
    前缀打头**的模式：`fnmatch` 的 `*` 跨 `/`，不收窄的话 `*.md` 会跨进别的阶段的产物、
    `*.py` 会跨进守卫本体。
    """
    globs = scopes.get(role, DEFAULT_ROLE_SCOPES.get(role, []))
    if not isinstance(globs, list):
        globs = []
    guarded = _guarded_prefix(root, rel)
    if guarded:
        if guarded.endswith("/"):
            globs = [g for g in globs if g.startswith(guarded)]
        else:
            globs = [g for g in globs if g == guarded or g.startswith(guarded + "/")]
    return [g for g in globs if fnmatch.fnmatch(rel, g)]


# 受守卫的公共脚本：它们的写入不走 Bash 能解析的形态（`python3 x.py` 不是写命令），
# 脚本内部却写 .vscode/、.workbench/*.code-workspace、repos.json 与 repos/* 软链 ——
# 角色执行就把「工作区材料角色只读」整个绕开。脚本没有角色用得上的合法形态，非主线程一律拒。
# 注意这**不是**通用的脚本执行管控：只有这两个具名脚本在此列，`bash /tmp/x.sh`、
# `./deploy.sh` 这类一律放行（见 _check_write_target 第 3 步的边界说明）。
GUARDED_SCRIPTS = frozenset({"repos_apply.py", "repos_tui.py"})
_SCRIPT_INTERPRETERS = frozenset({"python3", "python", "py", "bash", "sh", "zsh"})


def _guarded_script_exec(cmd: str) -> bool:
    """命令里是否执行了受守卫的公共脚本（角色执行 = 绕过工作区材料的只读收窄）。

    只认「脚本被当成程序执行」：脚本名作为命令首 token，或解释器后紧跟脚本路径
    （`python3 scripts/repos_apply.py`）。`grep` / `cat` / 读日志把脚本名当参数用不算执行。
    """
    for seg in _split_pipeline(strip_heredocs(cmd)):
        try:
            tokens = shlex.split(seg)
        except ValueError:
            continue
        if not tokens:
            continue
        if Path(tokens[0]).name in GUARDED_SCRIPTS:
            return True
        for i, t in enumerate(tokens[:-1]):
            if Path(t).name in _SCRIPT_INTERPRETERS and Path(tokens[i + 1]).name in GUARDED_SCRIPTS:
                return True
    return False


def _check_write_target(cwd: Path, root: Path, raw_path: str, data: dict) -> None:
    """检查单个写入目标：活动契约 → 争议 → 冻结 → 工作流核心路径的角色范围。"""
    target = resolve_target(cwd, raw_path)
    rootr = root.resolve()
    rel = os.path.relpath(target, rootr).replace(os.sep, "/")

    # 活动任务的旧契约先阻止产品代码继续写入；执行记录仍可写。
    active_errors = active_task_contract_errors(rootr, rel)
    if active_errors:
        hook_deny(
            f"活动开发任务绑定的契约快照已失效，拒绝写入 {rel}："
            + " | ".join(active_errors)
            + "。立即停止实现，先 task check，再 reopen 并重新绑定当前快照。"
        )

    # 争议熔断：developer 角色全线停工（执行记录除外）。
    disputes = read_disputes(rootr)
    if disputes:
        role = current_role(rootr, data)
        if role in DEVELOPER_ROLES and not _is_dispute_exempt_write(rel):
            _dispute_deny(disputes)

    # 冻结文件：状态、进度、契约、以及被登记为契约的方案文档。
    #    会话根之外还有嵌套工作台（布局 A：repos/<仓库>/.workbench/）——那个仓库
    #    锁的契约与状态文件只在内层清单里，外层视角必须反查目标所在的根。
    frozen_roots = [(rootr, rel)] + [
        (nr, os.path.relpath(target, nr).replace(os.sep, "/"))
        for nr in nested_roots(target, rootr)
    ]
    for fro_root, fro_rel in frozen_roots:
        frozen = read_frozen(fro_root)
        # 清单里的目录条目带尾斜杠（.workbench/flows/）；拼 f + "/" 会双斜杠失配，
        # flow 布局下 state.json 因此整类漏拦。归一化成无尾斜杠再比。
        dirs = {f.rstrip("/") for f in frozen}
        if fro_rel in frozen or any(fro_rel.startswith(d + "/") for d in dirs):
            if fro_rel not in unlocked_paths(fro_root):
                wbrel = os.path.relpath(wb_dir(fro_root), fro_root).replace(os.sep, "/")
                always = {f"{wbrel}/{c}" for c in FROZEN_ALWAYS}
                if fro_rel in always or any(fro_rel.startswith(a + "/") for a in always):
                    hook_deny(f"{fro_rel} 只能通过 wb.py 命令修改（保证门禁与进度不可绕过）")
                where = f"（工作台 {fro_root}）" if fro_root != rootr else ""
                hook_deny(
                    f"{fro_rel} 是已冻结的契约文档，不能直接改。{where}"
                    + frozen_advice(fro_root, [fro_rel], current_role(fro_root, data))
                )

    # 3. 角色写入范围：**只对工作流核心路径强制执行** —— 受守前缀下的东西
    #    （.workbench/ 的阶段产物与契约、knowledge/ 知识库、references/ 规范、
    #    .claude/ 等守卫本体、workbench 布局下的工作区材料）。这些文件坏了，工作流
    #    本身就跑不下去，所以守卫兜住。
    #
    #    其余路径一律不判角色：仓库代码（server/ web/ …）、/tmp、项目根之外 ——
    #    写业务代码越界、在 /tmp 建测试脚本、跑根外的临时脚本，都是开发过程中的
    #    常态，且不影响工作流推进。这层规范（谁该写哪里、别乱写文件乱执行脚本）
    #    交给 harness 与模型自己，不是本工作台的职责；工作台管得越宽，越容易在
    #    正常开发动作上误拦，把「守卫」变成流程的阻力。
    role = current_role(rootr, data)
    guarded = _guarded_prefix(rootr, rel)
    if not guarded:
        return
    if role == UNKNOWN_ROLE:
        hook_deny(
            f"无法验证调用者身份，拒绝写入工作流核心路径 {rel}：载荷有 agent_id 但"
            "没有可识别的 agent_type（门禁失效告警，不是权限错误）。"
            "升级 Claude / Codex CLI 后重试；主线程不带 agent_id，不该落到这一条。"
        )
    if not role or not state_path(rootr).is_file():
        return   # 主线程 / 无角色：只剩冻结与特权子命令两层约束
    # references/workspace/<角色>/ 是角色私有知识：只有对应角色可改，别的角色看着也一样。
    private_match = re.match(r"references/workspace/([^/]+)/", rel)
    if private_match:
        if private_match.group(1) != role:
            hook_deny(
                f"角色 {role} 无权写角色 {private_match.group(1)} 的私有知识 {rel}。"
                "只能改 references/workspace/<自己的角色>/。"
            )
        return   # 自己那份是「references/ 角色只读」的例外，不再过范围匹配
    scopes = load_state(rootr).get("role_scopes") or {}
    # 范围缺 key 回落到默认值；显式的空清单是「什么都不能写」，不是「不限制」。
    # 反过来读会让 `config set role_scopes.<角色> '[]'` 变成一键解除范围的开关 ——
    # 空值当放行时，越权路径连 GUARDED_PREFIXES 收窄都走不到。
    allowed = _role_scope_allows(rootr, scopes, role, rel)
    if not allowed:
        if guarded == "knowledge/":
            extra = ("（knowledge/ 是跨 flow 的长期知识库，只有 knowledger 角色可写。"
                     "沉淀或查找走 wb-knowledge skill，或交回主线程派 knowledger。）")
        elif guarded == "references/":
            extra = "（references/ 是公共操作规范，任何角色只读。要改规范交回主线程。）"
        elif guarded in GUARD_BODY_PREFIXES:
            extra = (f"（{guarded} 装的是守卫本体：权限引擎、hook 注册表与角色定义。"
                     "要改它交回主线程，别给角色开范围。）")
        elif guarded == ".workbench/":
            extra = ("（产物目录按阶段隔离：只能写自己阶段的产物，上游文档要改走 "
                     "contract unlock / 派对应角色。）")
        else:
            extra = (f"（{guarded} 是工作区级公共材料（公共脚本、清单、仓库索引、"
                     "单仓笔记或本机 IDE 配置），由主线程与对应角色维护。）")
        scopes_shown = scopes.get(role, DEFAULT_ROLE_SCOPES.get(role, []))
        hook_deny(
            f"角色 {role} 无权写 {rel}（不在其写入范围内）。{extra}"
            f"该角色的范围：{', '.join(scopes_shown) or '（空）'}。"
            "确需跨界交给对应角色；范围本身要改，报回编排者"
            f"（`wb.py config set role_scopes.{role} '<JSON 数组>'` 角色跑不了）。"
        )

# 角色 subagent 一律不能跑的 wb.py 子命令：改的是守卫自己的规则、门禁结论或状态
# 基线，全部属于编排者决策。`(子命令, action)` -> 理由。
PRIVILEGED_WB = {
    ("phase", "set"): "它不跑门禁直接改阶段",
    ("role", "set"): "它改的是主线程与非角色 agent 的写入范围兜底",
    ("role", "clear"): "它会清掉写入范围兜底",
    ("task", "skip"): "跳过的任务在 tasks_done 门禁里等同完成",
    ("flow", "new"): "需求线是编排者的调度决定，角色在当前 flow 里干活",
    ("flow", "switch"): "切 flow 会让后续状态命令落到另一条流水线",
    ("flow", "remove"): "删除的是整条流水线的状态与产物",
}


def _wb_invocations(cmd: str) -> list[list[str]]:
    """挑出命令里对 wb.py 的调用，返回各自的参数（不含解释器与脚本路径本身）。"""
    calls = []
    for seg in _split_pipeline(strip_heredocs(cmd)):
        try:
            tokens = shlex.split(seg)
        except ValueError:
            continue
        for i, tok in enumerate(tokens):
            if Path(tok).name in ("wb.py", "wb"):
                calls.append(tokens[i + 1:])
                break
    return calls


def _flag_value(args: list[str], flag: str) -> str:
    """取 `--flag v` 或 `--flag=v` 的值，取不到返回空串。"""
    for i, a in enumerate(args):
        if a == flag:
            nxt = args[i + 1] if i + 1 < len(args) else ""
            return "" if nxt.startswith("-") else nxt
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return ""


def _contract_owner(root: Path, name: str) -> str | None:
    """契约的 owner；契约不在登记表里返回 None。"""
    sp = state_path(root)
    if not name or not sp.is_file():
        return None
    try:
        st = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    c = find_contract(st, name)
    return (c.get("owner") or "") if c else None


def privileged_wb_calls(cmd: str, root: Path, role: str) -> list[str]:
    """角色 subagent 不该跑的 wb.py 子命令，返回拒绝理由（空列表 = 都能跑）。

    `.claude/settings.json` 的 `permissions.allow` 把 `Bash(python3 .claude/hooks/wb.py:*)`
    对所有角色开放，而 wb.py 自己拿不到 agent_type —— 它只知道被调用，不知道被谁
    调用。于是「状态只能经 wb.py 改」这条设计等价于：wb.py 能改的一切，任何角色都
    能改。实测一条 `config set role_scopes.<自己> '[".claude/**"]'` 就让守卫引擎本身
    变成可写，`init --force` 能清空阶段与契约基线，`config set gate_commands.test`
    能拿到不经 Bash 守卫的任意 shell。这层是那道缺失的调用者校验，只有 hook 拿得到
    身份，所以只能放这里。
    """
    hint = "报回编排者，由主线程决定。"
    out = []
    for args in _wb_invocations(cmd):
        pos = [a for a in args if not a.startswith("-")]
        flags = {a.split("=", 1)[0] for a in args if a.startswith("-")}
        sub_cmd = pos[0] if pos else ""
        action = pos[1] if len(pos) > 1 else ""

        if sub_cmd == "config" and action == "set":
            key = pos[2] if len(pos) > 2 else "<键>"
            # gate_commands.* / gate_waivers.* 是 qa 的既定流程（.claude/agents/qa.md）；
            # 其余键 ——尤其 role_scopes.* —— 改的是守卫自己的规则。豁免和命令同属门禁
            # 配置：qa 判定「这个项目不需要某门禁」就是它的活。
            if not (role == "qa" and (key.startswith("gate_commands.")
                                      or key.startswith("gate_waivers."))):
                out.append(
                    f"角色 {role} 不能跑 `config set {key}`：它改的是守卫与调度自己的"
                    f"配置（role_scopes.* 能直接给自己开范围）。只有 qa 能设"
                    f" gate_commands.* / gate_waivers.*。{hint}")
        elif sub_cmd == "init" and "--force" in flags:
            out.append(f"角色 {role} 不能跑 `init --force`：它清空阶段、契约基线、"
                       f"门禁记录与冻结清单。{hint}")
        elif sub_cmd == "phase" and action == "advance" and "--force" in flags:
            out.append(f"角色 {role} 不能跑 `phase advance --force`：强推门禁前要先问"
                       f"用户（CLAUDE.md 硬规则 3）。{hint}")
        elif sub_cmd == "role" and action == "scopes" and "--reset" in flags:
            out.append(f"角色 {role} 不能跑 `role scopes --reset`：它重写全部角色的"
                       f"写入范围。{hint}")
        elif (sub_cmd, action) in PRIVILEGED_WB:
            out.append(f"角色 {role} 不能跑 `{sub_cmd} {action}`："
                       f"{PRIVILEGED_WB[(sub_cmd, action)]}。{hint}")
        elif sub_cmd == "contract" and action == "dispute" and "--clear" in flags:
            out.append(f"角色 {role} 不能跑 `contract dispute --clear`：解除争议熔断是"
                       f"编排者决策。{hint}")
        elif sub_cmd == "contract" and action in ("unlock", "bump", "consumers"):
            # 冻结层的拒绝信息按 owner 分岔提示「不要自己申报解冻」，但 unlock / bump /
            # consumers 本身不校验 owner —— 实测 backend-developer 能解冻、改写并重新基线化
            # architect 的契约，事后 contract verify 干净。consumers 改的是通知目标，同样
            # 该由 owner 定。这里补成硬拦。
            name = _flag_value(args, "--name")
            owner = _contract_owner(root, name)
            if owner is None:
                out.append(
                    f"角色 {role} 跑 `contract {action}` 必须带登记表里的 --name："
                    f"{name or '(缺)'} 查不到，无法核对 owner。先 `contract list` 看"
                    f"实名。{hint}")
            elif owner != role and role != CONTRACT_STEWARD:
                tail = "别自己申报解冻。" if action in ("unlock", "bump") else "别自己改消费方。"
                out.append(
                    f"角色 {role} 不能 `contract {action} --name {name}`：这份契约的"
                    f" owner 是 {owner}。要改它把需求报给 {owner}，{tail}{hint}")
    return out


def _patch_targets(cmd: str) -> list[str]:
    """Extract every target named by an apply_patch command."""
    targets = []
    for marker in ("*** Add File:", "*** Update File:", "*** Delete File:", "*** Move to:"):
        for line in cmd.splitlines():
            line = line.strip()
            if line.startswith(marker):
                raw = line[len(marker):].strip()
                if raw:
                    targets.append(raw)
    return targets


def _is_apply_patch(cmd: str) -> bool:
    """Return whether a shell command invokes apply_patch."""
    try:
        tokens = shlex.split(strip_heredocs(cmd))
    except ValueError:
        return False
    return bool(tokens) and Path(tokens[0]).name == "apply_patch"


def _is_task_start(cmd: str, task_id: str) -> bool:
    """Return whether a shell command invokes wb.py task start <ID>."""
    for seg in _split_pipeline(strip_heredocs(cmd)):
        try:
            tokens = shlex.split(seg)
        except ValueError:
            continue
        tokens = _strip_wrappers(tokens)
        try:
            wb_index = next(i for i, tok in enumerate(tokens)
                            if Path(tok).name in ("wb.py", "wb"))
            args = tokens[wb_index + 1:]
        except StopIteration:
            continue
        if args[:3] == ["task", "start", task_id]:
            return True
    return False


def hook_pre_tool(data: dict) -> None:
    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}
    cwd = Path(data.get("cwd") or os.getcwd())
    root = find_root(cwd)

    cmd = ti.get("command", "") or ""
    if SHELL_TOOL.search(tool):
        rootr = root.resolve()

        # --- wb.py 特权子命令：只有这里拿得到调用者身份 ---
        wb_role = current_role(rootr, data)
        if wb_role in ROLES:
            for reason in privileged_wb_calls(cmd, rootr, wb_role):
                hook_deny(reason)

        # --- 受守卫公共脚本的执行绕过 ---
        # `python3 scripts/repos_apply.py` 没有 Bash 能解析的写目标（不带 -c 的 python3
        # 不是写命令），脚本内部却写 .vscode/、.workbench/*.code-workspace、repos.json
        # 与 repos/* 软链 —— 角色执行就把「工作区材料角色只读」整个绕开。
        # 只认这两个具名脚本；**执行其他脚本（含 /tmp 下的临时脚本、项目根外的脚本）
        # 不做位置判定** —— 那是开发过程的常态，不属于工作流核心的管控范围。
        if (data.get("agent_id") or data.get("agent_type")) and _guarded_script_exec(cmd):
            hook_deny(
                "repos_apply.py / repos_tui.py 的写入不被 Bash 守卫解析，subagent 执行"
                "会绕过 scripts/、repos.json、.vscode/ 的只读收窄。"
                "初始化清单与 IDE 配置交回主线程跑。"
            )

        # --- 争议熔断 ---
        # 任何争议哨兵存在时，developer 角色全线停工。
        # 放行：自己的执行记录（.workbench/artifacts/develop/）与 /tmp。
        disputes = read_disputes(rootr)
        if disputes:
            role = current_role(rootr, data)
            if role in DEVELOPER_ROLES and not _is_dispute_exempt_bash(cmd, rootr):
                _dispute_deny(disputes)

        if _is_apply_patch(cmd):
            for raw in _patch_targets(cmd):
                _check_write_target(cwd, root, raw, data)
        if data.get("agent_id"):
            for t in load_state(root).get("tasks", []):
                if t.get("status") == "todo" and _is_task_start(cmd, t["id"]):
                    with (wb_dir(root) / "task-agents.jsonl").open("a", encoding="utf-8") as fh:
                        # flow 进绑定：任务 ID 每条 flow 独立从 T1 编起，绑定文件是
                        # 工作区共享的，不记 flow 时 A flow 的 T1 会认领 B flow 的 agent
                        entry = {"at": now(), "id": t["id"], "role": t["role"],
                                 "flow": read_current_flow(root)}
                        for key in ("agent_id", "agent_type", "session_id", "turn_id"):
                            if data.get(key):
                                entry[key] = data[key]
                        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    break

        # 解析写入目标：能精确判就精确判，解析不了退回粗检查。
        all_targets, _, uncertain = resolve(cmd, root)

        # --- 冻结检查 ---
        # heredoc body 已被 strip_heredocs 剥掉，frozen_hits 不再命中 body 里的路径。
        # uncertain=False 时，冻结路径不在 all_targets 里就放行（如 cp 契约 /tmp/bak）。
        # uncertain=True 时退回旧行为（BASH_WRITE + frozen_hits 文本匹配）。
        if BASH_WRITE.search(cmd) or all_targets:
            cleaned_cmd = strip_heredocs(cmd)
            mentioned = frozen_hits(root, cleaned_cmd)
            unlocked = unlocked_paths(root)
            hits = [h for h in mentioned if h not in unlocked]
            if hits:
                if not uncertain:
                    # 精确模式：冻结路径必须在写入目标里才拦
                    real_hits = [h for h in hits if h in all_targets]
                    if real_hits:
                        hits = real_hits
                        hint = ""
                    else:
                        hits = []  # 全部是误报（如 cp 契约 /tmp/bak），放行
                else:
                    # 不确定模式：退回旧行为，但说明原因
                    hint = "（写入目标无法解析，已一并拦截）"
            else:
                hint = ""
            if hits:
                hook_deny(
                    f"{', '.join(hits)} 是冻结文件，不能用 shell 直接写"
                    f"（这会绕过守卫与哈希校验）。{hint}"
                    f"{frozen_advice(root, hits, current_role(root, data))}命令：{cmd[:120]}"
                )
            # 先切目录再写的兜底：uncertain 时仍生效
            if not mentioned and re.search(
                    r"\b(?:cd|pushd)\s+[^\s;|&]*\.workbench\b", cleaned_cmd):
                hook_deny(
                    "先 cd 进 .workbench/ 再写文件这条路不通：切了目录守卫就看不到完整"
                    "相对路径，所以整类写法一并拒绝，换 sed/tee/重定向都一样。"
                    "状态与进度只能用 wb.py 子命令改；改已锁定的契约先 "
                    "`wb.py contract unlock --name <契约名> --reason '<为什么要改>'` 申报"
                    "（契约名用 `wb.py contract list` 查）；写还没登记的新文件用相对"
                    f"仓库根的完整路径，别 cd。命令：{cmd[:120]}"
                )

        for rel_tgt in sorted(all_targets):
            _check_write_target(rootr, root, rel_tgt, data)
        return

    if READ_TOOL.search(tool):
        return

    # apply_patch：Codex 的写入工具，目标藏在 *** Add/Update/Delete File: 标记里
    if tool == "apply_patch":
        cmd_text = ti.get("command", "") or ti.get("content", "") or ""
        for raw in _patch_targets(cmd_text):
            _check_write_target(cwd, root, raw, data)
        return

    if not WRITE_TOOL.search(tool):
        return

    raw = ti.get("file_path") or ti.get("notebook_path")
    if not raw:
        return
    _check_write_target(cwd, root, str(raw), data)


def hook_post_tool(data: dict) -> None:
    """把改动追加到产物流水账，由 `task done` 归并进任务（见 merge_artifacts）。

    这里绝不能读改写 state.json：并行 develop 下每个 subagent 的每次文件写入
    都会触发本钩子，旧快照回写会静默吞掉期间落盘的 `task done`，连带把
    save_state 顺手重写的冻结清单退回旧版 —— 于是「门禁与进度不可绕过」
    在并发下失效，不需要谁去绕。纯 append 无竞态，也把全量 JSON 读写
    从每次工具调用的热路径上挪走了。

    每行的 role 取自本次调用的载荷（见 current_role），不是那个被并行 subagent
    互相覆盖的 `.workbench/role` —— 归属记录只在 develop 并行时才有价值，读单文件
    会让两个开发角色的改动全挂到最后一次 `role set` 的那个角色名下。
    """
    ti = data.get("tool_input") or {}
    tool = data.get("tool_name", "")
    cwd = Path(data.get("cwd") or os.getcwd())
    root = find_root(cwd)
    if not state_path(root).is_file():
        return
    rootr = root.resolve()
    role = current_role(root, data)

    def append_entry(rel: str) -> None:
        # flow 随行：归并侧（merge_artifacts）按任务所在 flow 过滤，没有这个字段
        # 时旧行视为本 flow，新行必须带，否则跨 flow 同名任务 ID 互相认领产物。
        entry = {"at": now(), "path": rel, "role": role,
                 "flow": read_current_flow(root)}
        for key in ("agent_id", "agent_type", "session_id", "turn_id", "tool_use_id"):
            value = data.get(key)
            if value:
                entry[key] = value
        with (wb_dir(root) / ARTIFACT_LOG).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Bash/Codex shell 的文件变更也进入流水账。只能记录静态解析出的目标，
    # 只记录静态解析出的写入目标。
    if SHELL_TOOL.search(tool):
        cmd = ti.get("command", "") or ""
        targets, _, _ = resolve(cmd, root)
        if _is_apply_patch(cmd):
            for raw in _patch_targets(cmd):
                try:
                    rel = os.path.relpath(
                        resolve_target(cwd, raw), rootr).replace(os.sep, "/")
                    targets.add(rel)
                except ValueError:
                    pass
        for rel in sorted(targets):
            append_entry(rel)
        return

    # Read 只是读取，不应伪造一条产物改动记录。
    if READ_TOOL.search(tool):
        return

    # apply_patch：从标记里提取所有文件路径
    if tool == "apply_patch":
        cmd_text = ti.get("command", "") or ti.get("content", "") or ""
        for raw in _patch_targets(cmd_text):
            try:
                rel = os.path.relpath(
                    resolve_target(cwd, raw), rootr).replace(os.sep, "/")
                append_entry(rel)
            except ValueError:
                pass
        return

    raw = ti.get("file_path") or ti.get("notebook_path")
    if not raw:
        return
    try:
        rel = os.path.relpath(resolve_target(cwd, str(raw)), rootr).replace(os.sep, "/")
    except ValueError:
        return
    append_entry(rel)


def hook_session_start(data: dict) -> None:
    root = find_root(Path(data.get("cwd") or os.getcwd()))
    if not state_path(root).is_file():
        print("工作台未初始化。要走全链路流程，先运行 /wb-flow 或 "
              "`python3 .claude/hooks/wb.py init --name <项目名>`。")
        return
    st = load_state(root)
    cur = st["phase"]
    done = sum(1 for t in st["tasks"] if t["status"] == "done")
    lines = [
        "## 工作台状态",
        f"项目 {st['project']}｜阶段 {cur}（{PHASE_CN.get(cur, cur)}）｜任务 {done}/{len(st['tasks'])} 完成",
        f"根 {root}",
    ]
    doing = [t["id"] for t in st["tasks"] if t["status"] == "doing"]
    blocked = [f"{t['id']}({t['notes'][:30]})" for t in st["tasks"] if t["status"] == "blocked"]
    stale = [t["id"] for t in st["tasks"] if t["status"] == "stale"]
    if doing:
        lines.append(f"进行中：{', '.join(doing)}")
    if blocked:
        lines.append(f"阻塞：{', '.join(blocked)} — 需要先解阻塞")
    if stale:
        lines.append(f"失效（stale）：{', '.join(stale)} — 上游被推翻，需 reopen 后重跑")
    bad = contract_drift(root, st)
    if bad:
        lines.append(f"契约漂移 {len(bad)} 处：{'; '.join(bad[:3])} — 用 `contract bump` 走正式变更")
    rt = ready_tasks(st, phase=cur)
    if rt:
        lines.append("就绪：" + ", ".join(f"{t['id']}/{t['role']}" for t in rt[: st["max_parallel"]]))
    elif not doing and not blocked:
        lines.append(f"本阶段无待办，可跑门禁：`wb.py gate check`")
    lines.append("推进流程用 /wb-flow，自动排空任务用 /wb-loop。")
    print("\n".join(lines))


def hook_subagent_stop(data: dict, fmt: str = "claude") -> None:
    """子 agent 结束：解除角色锁与解冻窗口，避免下一个 agent 继承上一个的权限。

    只在没有别的任务仍处于 doing 时才解除。并行派发下先结束的那个 subagent
    会把仍在运行的兄弟的角色锁与解冻窗口一并清掉。Codex 的 SubagentStop 要求 JSON
    输出，`fmt="codex"` 时
    把清理提示包装成 `{"systemMessage": ...}`；Claude 保持原文本。
    """
    root = find_root(Path(data.get("cwd") or os.getcwd()))
    if not state_path(root).is_file():
        if fmt == "codex":
            print(json.dumps({"systemMessage": ""}, ensure_ascii=False))
        return
    st = load_state(root, lock=True)
    flow = st.get("_flow") or pointer_flow(root)
    rolef = wb_dir(root) / "role"
    role = current_role(root, data)
    doing = [t["id"] for t in st["tasks"] if t["status"] == "doing"]
    log(st, "subagent_stop", role=role, doing=",".join(doing))
    save_state(root, st)

    lines = []
    if doing:
        lines.append(
            f"[工作台] 子 agent（{role or '未标注角色'}）结束，但 {', '.join(doing)} 仍为 doing，"
            f"角色锁与解冻窗口保持不变 —— 并行下清掉会打断仍在运行的兄弟 agent。"
            f"确认产物后执行 `wb.py task done <id>`，最后一个任务收尾时自动解除。")
    else:
        rolef.unlink(missing_ok=True)
        # 只关本 flow 的窗口，与上面 doing 判断同一个定点。曾经全 flow 关窗兜底，
        # 实测会把别的 flow 里 architect 改到一半的契约拆成死局：窗口没了 bump 被
        # 拒，正文已改 unlock 也被拒，只能手工恢复旧正文。悬挂窗口的代价是重报
        # 一次，跨 flow 误关的代价是死锁 —— 定点收窄是唯一正确方向。
        ud = state_path(root, flow).parent / "unlock"
        opened = sorted(p.name for p in ud.iterdir() if p.is_file()) if ud.is_dir() else []
        close_unlock(root, flow=flow)
        if opened:
            names = ", ".join(opened)
            lines.append(
                f"[工作台] 解冻窗口 {names} 已随子 agent 结束关闭。"
                f"若已改动这些文件，跑 `wb.py contract verify` 确认状态，"
                f"需要定版就逐个 `wb.py contract bump --name <名> --reason '<理由>'`。")
    msg = "\n".join(lines)
    if fmt == "codex":
        print(json.dumps({"systemMessage": msg}, ensure_ascii=False))
    elif msg:
        print(msg)


def cmd_hook(args) -> None:
    set_flow_override(None)   # 守卫与 hook 是工作区级视角，不跟调用方 shell 的 WB_FLOW 走
    raw = sys.stdin.read() if not sys.stdin.isatty() else "{}"
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data = {}
    try:
        {
            "pre-tool": lambda d: hook_pre_tool(d),
            "post-tool": lambda d: hook_post_tool(d),
            "session-start": lambda d: hook_session_start(d),
            "subagent-stop": lambda d: hook_subagent_stop(d, fmt=args.format),
        }[args.event](data)
    except KeyError:
        die(f"未知 hook 事件：{args.event}")
    except SystemExit:
        raise
    except Exception as e:
        print(f"[工作台 hook 异常] {type(e).__name__}: {e}", file=sys.stderr)
            # 未初始化目录不应被 hook 影响；已初始化工作台暴露 hook 故障。
        try:
            root = find_root(Path(data.get("cwd") or os.getcwd()))
            if state_path(root).is_file():
                sys.exit(2)
        except Exception:
            pass
        sys.exit(0)

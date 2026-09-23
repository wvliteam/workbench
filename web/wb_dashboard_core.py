#!/usr/bin/env python3
"""wb_dashboard_core: Workbench 状态与执行细节提取、DAG 拓扑分层计算引擎。

作为可视化看板（wb-dashboard）的基础数据层，本模块提供纯状态解析与算法计算，
不依赖外部 HTTP 服务或第三方库，支持离线分析与高并发只读访问（load_state(lock=False)）。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# 动态引入 .claude/hooks 下的工作台核心支持模块
_CUR = Path(__file__).resolve().parent
_HOOKS_DIR = None
for _p in (_CUR.parent.parent, _CUR.parent, _CUR):
    if (_p / ".claude" / "hooks").is_dir():
        _HOOKS_DIR = _p / ".claude" / "hooks"
        break
if not _HOOKS_DIR:
    for _p in _CUR.parents:
        if (_p / ".claude" / "hooks").is_dir():
            _HOOKS_DIR = _p / ".claude" / "hooks"
            break

if _HOOKS_DIR and str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

try:
    from wb_core import (
        find_root,
        wb_dir,
        flow_dir,
        read_current_flow,
        all_flows,
        load_state,
        state_path,
        read_unlock_records,
        read_disputes,
        DEFAULT_FLOW,
    )
    from wb_const import (
        ARTIFACT_LOG,
        GATES,
        PHASE_CN,
        WB_VERSION,
    )
except ImportError:
    DEFAULT_FLOW = "main"
    ARTIFACT_LOG = "artifacts.jsonl"
    WB_VERSION = "0.1.0"
    PHASE_CN = {
        "clarify": "需求澄清",
        "analyze": "现状分析",
        "design": "方案设计",
        "develop": "开发实现",
        "verify": "测试验证",
        "retro": "总结复盘",
    }
    GATES = {}
    find_root = None  # type: ignore
    wb_dir = None  # type: ignore
    flow_dir = None  # type: ignore
    read_current_flow = None  # type: ignore
    all_flows = None  # type: ignore
    load_state = None  # type: ignore
    state_path = None  # type: ignore
    read_unlock_records = None  # type: ignore
    read_disputes = None  # type: ignore


# --------------------------------------------------------------------------
# 安全与路径防穿越守卫
# --------------------------------------------------------------------------

class SecurityError(ValueError):
    """当检测到非法的跨目录或路径穿越访问时抛出。"""
    pass


def safe_resolve_path(root: Path, target: str | Path) -> Path:
    """安全解析目标路径，确保其严格落在 root 目录树以内。

    若检测到以 `..` 试图越界，或绝对路径指向 root 外，立即抛出 SecurityError。
    """
    root_resolved = Path(root).resolve()
    target_p = Path(target)
    if not target_p.is_absolute():
        resolved = (root_resolved / target_p).resolve()
    else:
        resolved = target_p.resolve()

    try:
        if not resolved.is_relative_to(root_resolved):
            raise SecurityError(f"安全拦截：路径越界访问 '{target}'（项目根：'{root}'）")
    except AttributeError:
        # 兼容低于 Python 3.9 的环境
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            raise SecurityError(f"安全拦截：路径越界访问 '{target}'（项目根：'{root}'）")
    return resolved


# 需求线名称格式守卫（防穿越与底层 die() DoS）
FLOW_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def validate_flow_name(flow: str | None) -> str | None:
    """校验需求线 flow 名称合法性。

    若为 None 则返回 None（允许调用方使用默认当前 flow）；
    若包含路径穿越字符（'..', '/', '\\'），抛出 SecurityError 映射为 403 Forbidden；
    若包含其他非法字符（大写、空格、特殊标点等），抛出 ValueError 映射为 400 Bad Request。
    """
    if flow is None:
        return None
    if not isinstance(flow, str) or not flow:
        raise ValueError("flow 名称不能为空")
    if "/" in flow or "\\" in flow or ".." in flow:
        raise SecurityError(f"安全拦截：flow 名称包含非法路径越界字符: {flow!r}")
    if not FLOW_NAME_PATTERN.fullmatch(flow):
        raise ValueError(f"非法的 flow 需求线名称: {flow!r}（仅允许小写字母、数字、破折号与下划线）")
    return flow


# --------------------------------------------------------------------------
# 状态读取（无锁）
# --------------------------------------------------------------------------

def load_workbench_state(root: Path, flow: str | None = None) -> dict[str, Any]:
    """无锁加载工作台 state.json，规避并发锁竞争。"""
    validate_flow_name(flow)
    resolved_root = Path(root).resolve()
    target_flow = flow or (read_current_flow(resolved_root) if read_current_flow else DEFAULT_FLOW)
    if load_state:
        return load_state(resolved_root, lock=False, flow=target_flow)

    # 兜底直接只读加载
    sp = resolved_root / ".workbench" / "flows" / target_flow / "state.json"
    if not sp.is_file() and target_flow == DEFAULT_FLOW:
        sp = resolved_root / ".workbench" / "state.json"
    if not sp.is_file():
        raise FileNotFoundError(f"State file not found: {sp}")
    return json.loads(sp.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# DAG 拓扑分层与坐标排版算法
# --------------------------------------------------------------------------

def calculate_dag(
    tasks: list[dict[str, Any]],
    card_width: int = 220,
    card_height: int = 80,
    gap_x: int = 60,
    gap_y: int = 30,
    offset_x: int = 40,
    offset_y: int = 40,
) -> dict[str, Any]:
    """基于任务 deps 构建有向边，使用拓扑排序计算排版深度 L(v) = max_{u in deps(v)}(L(u) + 1)。

    具备环路容错机制：检测到循环依赖时标记节点 has_cycle=True 并平滑降级，防止死循环。
    输出包含 nodes、edges、max_depth、has_cycle 以及各节点的网格坐标 (col, row) 和像素坐标 (x, y)。
    """
    if not tasks:
        return {
            "nodes": [],
            "edges": [],
            "max_depth": 0,
            "total_nodes": 0,
            "has_cycle": False,
        }

    nodes_dict: dict[str, dict[str, Any]] = {
        t["id"]: dict(t) for t in tasks if isinstance(t, dict) and "id" in t
    }

    # 收集有向边与依赖关系
    # 若 v 依赖 u，即 u -> v (u 必须先于 v 完成)
    preds: dict[str, list[str]] = {tid: [] for tid in nodes_dict}
    succs: dict[str, list[str]] = {tid: [] for tid in nodes_dict}
    edges: list[dict[str, str]] = []
    cycle_nodes: set[str] = set()

    for tid, node in nodes_dict.items():
        raw_deps = node.get("deps", [])
        if not isinstance(raw_deps, list):
            raw_deps = []
        for dep in raw_deps:
            if not isinstance(dep, str) or dep not in nodes_dict:
                # 忽略不存在的前置依赖，保持容错
                continue
            if dep == tid:
                # 自依赖环路
                cycle_nodes.add(tid)
                continue
            preds[tid].append(dep)
            succs[dep].append(tid)
            edges.append({"source": dep, "target": tid})

    # Kahn 算法拓扑排序与深度计算
    in_degree = {tid: len(preds[tid]) for tid in nodes_dict}
    depth: dict[str, int] = {tid: 0 for tid in nodes_dict}

    queue = [tid for tid, deg in in_degree.items() if deg == 0 and tid not in cycle_nodes]
    processed: list[str] = []

    while queue:
        u = queue.pop(0)
        processed.append(u)
        for v in succs[u]:
            depth[v] = max(depth[v], depth[u] + 1)
            in_degree[v] -= 1
            if in_degree[v] == 0 and v not in cycle_nodes:
                queue.append(v)

    # 循环依赖容错降级
    has_cycle = len(processed) < len(nodes_dict) or bool(cycle_nodes)
    if has_cycle:
        unprocessed = [tid for tid in nodes_dict if tid not in processed]
        cycle_nodes.update(unprocessed)
        curr_max = max(depth.values(), default=0)
        # 为环中节点分配平滑深度，避免重叠或无限循环
        for v in unprocessed:
            valid_preds = [depth[u] for u in preds[v] if u in processed]
            depth[v] = (max(valid_preds) + 1) if valid_preds else (curr_max + 1)

    # 按层级分组并计算排版网格 (col, row) 及坐标 (x, y)
    layers: dict[int, list[str]] = {}
    # 按照 tasks 原有顺序分配，以保证视觉确定性
    for t in tasks:
        tid = t.get("id")
        if tid in nodes_dict:
            d = depth[tid]
            layers.setdefault(d, []).append(tid)

    nodes_list: list[dict[str, Any]] = []
    for col in sorted(layers.keys()):
        for row, tid in enumerate(layers[col]):
            orig = nodes_dict[tid]
            card = {
                "id": tid,
                "title": orig.get("title", ""),
                "role": orig.get("role", ""),
                "phase": orig.get("phase", ""),
                "status": orig.get("status", "todo"),
                "deps": orig.get("deps", []),
                "contracts": orig.get("contracts", []),
                "artifacts_count": len(orig.get("artifacts", [])),
                "notes": orig.get("notes", ""),
                "started": orig.get("started"),
                "updated": orig.get("updated"),
                "created": orig.get("created"),
                "layer": col,
                "col": col,
                "row": row,
                "x": offset_x + col * (card_width + gap_x),
                "y": offset_y + row * (card_height + gap_y),
                "width": card_width,
                "height": card_height,
                "has_cycle": tid in cycle_nodes,
            }
            nodes_list.append(card)

    return {
        "nodes": nodes_list,
        "edges": edges,
        "max_depth": max(depth.values(), default=0),
        "total_nodes": len(nodes_list),
        "has_cycle": has_cycle,
    }


# --------------------------------------------------------------------------
# 业务数据聚合 API
# --------------------------------------------------------------------------

def get_overview(root: Path, flow: str | None = None) -> dict[str, Any]:
    """聚合工作台全局概览数据，包括 Flow 列表、六阶段状态、角色锁状态与未配置门禁等。"""
    root_resolved = Path(root).resolve()
    target_flow = flow or (read_current_flow(root_resolved) if read_current_flow else DEFAULT_FLOW)
    st = load_workbench_state(root_resolved, flow=target_flow)

    # 角色锁状态
    role_file = wb_dir(root_resolved) / "role" if wb_dir else root_resolved / ".workbench" / "role"
    role_locked = bool(role_file.is_file() and role_file.read_text(encoding="utf-8").strip())

    # 六阶段阶梯卡片
    cur_phase = st.get("phase", "")
    gates = st.get("gates", {})
    phases_progress: list[dict[str, Any]] = []
    phases = st.get("phases", ["clarify", "analyze", "design", "develop", "verify", "retro"])

    for p in phases:
        g = gates.get(p, {})
        passed = bool(g.get("passed"))
        forced = bool(g.get("forced"))
        status = "current" if p == cur_phase else ("forced" if forced else ("passed" if passed else "pending"))
        phases_progress.append({
            "phase": p,
            "name_cn": PHASE_CN.get(p, p) if 'PHASE_CN' in globals() else p,
            "is_current": (p == cur_phase),
            "passed": passed,
            "forced": forced,
            "status": status,
        })

    # 豁免与未配置门禁
    waivers = {k: v for k, v in st.get("gate_waivers", {}).items() if isinstance(v, str) and v.strip()}
    configured = set(st.get("gate_commands", {}))
    unconfigured: list[str] = []
    if 'GATES' in globals():
        unconfigured = sorted(set(
            rest for phase_info in GATES.values()
            for spec in phase_info.get("checks", [])
            if spec.startswith("cmd:")
            for rest in [spec.partition(":")[2]]
            if rest not in configured and rest not in waivers
        ))

    all_f = all_flows(root_resolved) if all_flows else [DEFAULT_FLOW]
    tasks = st.get("tasks", [])
    done_count = sum(1 for t in tasks if isinstance(t, dict) and t.get("status") == "done")

    gate_logs: list[str] = []
    wbd = root_resolved / ".workbench"
    search_dirs = [wbd / "flows" / target_flow, wbd]
    seen_logs: set[str] = set()
    for s_dir in search_dirs:
        if s_dir.is_dir():
            for lp in s_dir.glob("gate-*.log"):
                if lp.is_file():
                    stem = lp.stem
                    if stem.startswith("gate-"):
                        gname = stem[5:]
                        if gname and gname not in seen_logs:
                            seen_logs.add(gname)
                            gate_logs.append(gname)

    return {
        "project": st.get("project", "workbench"),
        "root": str(root_resolved),
        "current_flow": target_flow,
        "flows": all_f,
        "current_phase": cur_phase,
        "phases": phases_progress,
        "role_locked": role_locked,
        "role_lock_warning": not role_locked,
        "unconfigured_gates": unconfigured,
        "configured_gates": sorted(list(configured)),
        "gate_waivers": waivers,
        "gate_logs": sorted(gate_logs),
        "total_tasks": len(tasks),
        "done_tasks": done_count,
        "version": WB_VERSION if 'WB_VERSION' in globals() else "0.1.0",
    }


def get_tasks_dag(root: Path, flow: str | None = None) -> dict[str, Any]:
    """从 state 加载任务并计算 DAG 拓扑图。"""
    root_resolved = Path(root).resolve()
    st = load_workbench_state(root_resolved, flow=flow)
    tasks = st.get("tasks", [])
    return calculate_dag(tasks)


def _extract_task_artifacts(root: Path, task: dict[str, Any], flow: str) -> list[str]:
    """聚合任务产物：结合 task.artifacts 与 artifacts.jsonl 的归属流。"""
    artifacts: set[str] = set(task.get("artifacts", []))

    # 读取任务对应的 agent_id
    wbd = wb_dir(root) if wb_dir else root / ".workbench"
    binding_file = wbd / "task-agents.jsonl"
    agent_ids: set[str] = set()
    if binding_file.is_file():
        try:
            for line in binding_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if (record.get("id") == task.get("id")
                            and record.get("flow", DEFAULT_FLOW) == flow
                            and record.get("agent_id")):
                        agent_ids.add(record["agent_id"])
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass

    # 从 artifacts.jsonl 读取额外改动
    art_log = wbd / (ARTIFACT_LOG if 'ARTIFACT_LOG' in globals() else "artifacts.jsonl")
    since = task.get("started") or task.get("created") or ""
    if art_log.is_file():
        try:
            for line in art_log.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("flow", DEFAULT_FLOW) != flow:
                    continue
                path = e.get("path")
                if not path:
                    continue
                if agent_ids and e.get("agent_id") in agent_ids:
                    artifacts.add(path)
                elif not agent_ids and task.get("role") and e.get("role") == task.get("role"):
                    if since and e.get("at", "") >= since:
                        artifacts.add(path)
        except OSError:
            pass

    return sorted(artifacts)


def _parse_verification_section(ver_content: str, task_id: str) -> dict[str, Any]:
    """从 verification.md 解析指定任务的复核内容与命令证据。"""
    if not ver_content or not task_id:
        return {"raw": "", "commands": []}

    lines = ver_content.splitlines()
    section_lines: list[str] = []
    in_target = False

    # 匹配 ## T1 ... 或 ## T2-T5 ... 或 ## T1, T2 ...
    h2_pattern = re.compile(r"^##\s+(.*)$")
    tid_num_match = re.search(r"\d+", task_id)
    tid_num = int(tid_num_match.group(0)) if tid_num_match else None

    for line in lines:
        m = h2_pattern.match(line)
        if m:
            heading = m.group(1).strip()
            # 判断当前标题是否包含该任务 ID
            matches = False
            if task_id in heading:
                matches = True
            elif tid_num is not None:
                # 检查类似 T2-T5 范围
                range_m = re.search(r"T(\d+)-T(\d+)", heading)
                if range_m:
                    start_n, end_n = int(range_m.group(1)), int(range_m.group(2))
                    if start_n <= tid_num <= end_n:
                        matches = True

            if matches:
                in_target = True
                section_lines.append(line)
                continue
            elif in_target:
                # 进入了下一个不匹配的二级标题，退出
                break

        if in_target:
            section_lines.append(line)

    raw_text = "\n".join(section_lines).strip()

    # 提取命令行与结论标记
    commands: list[dict[str, str]] = []
    cmd_pattern = re.compile(r"[-*]\s+`([^`]+)`(?:\s*→\s*(.*))?")
    for line in section_lines:
        cm = cmd_pattern.search(line)
        if cm:
            commands.append({
                "command": cm.group(1).strip(),
                "verdict": (cm.group(2) or "").strip(),
            })

    return {
        "raw": raw_text,
        "commands": commands,
    }


def get_task_detail(root: Path, task_id: str, flow: str | None = None) -> dict[str, Any] | None:
    """获取单个任务的微观执行细节（代码变动树、现场笔记 Markdown、复核命令）。"""
    validate_flow_name(flow)
    root_resolved = Path(root).resolve()
    if "/" in task_id or "\\" in task_id or ".." in task_id:
        safe_resolve_path(root_resolved, task_id)
    target_flow = flow or (read_current_flow(root_resolved) if read_current_flow else DEFAULT_FLOW)
    st = load_workbench_state(root_resolved, flow=target_flow)

    tasks = st.get("tasks", [])
    target_task = next((t for t in tasks if isinstance(t, dict) and t.get("id") == task_id), None)
    if not target_task:
        return None

    # 1. 代码改动聚合
    artifacts = _extract_task_artifacts(root_resolved, target_task, target_flow)
    changes_by_project: dict[str, list[str]] = {}
    for p in artifacts:
        # 分组：如 repos/.source/<project>/... 或根目录
        parts = p.replace("\\", "/").split("/")
        if len(parts) >= 3 and parts[0] == "repos" and parts[1] == ".source":
            proj = parts[2]
        elif len(parts) > 1:
            proj = parts[0]
        else:
            proj = "root"
        changes_by_project.setdefault(proj, []).append(p)

    # 2. 现场笔记 Markdown (artifacts/<flow>/develop/tasks/<id>-<role>.md)
    note_markdown = ""
    note_path = None
    tasks_note_dir = root_resolved / ".workbench" / "artifacts" / target_flow / "develop" / "tasks"
    if tasks_note_dir.is_dir():
        # 寻找形如 T1-*.md 或 T1.md
        for f in sorted(tasks_note_dir.iterdir()):
            if f.is_file() and (f.stem == task_id or f.stem.startswith(f"{task_id}-")):
                try:
                    checked_p = safe_resolve_path(root_resolved, f)
                    note_markdown = checked_p.read_text(encoding="utf-8", errors="replace")
                    note_path = str(checked_p.relative_to(root_resolved))
                    break
                except (SecurityError, OSError):
                    pass

    # 3. 人工复核命令从 verification.md 解析
    verification_info: dict[str, Any] = {"raw": "", "commands": []}
    ver_path = root_resolved / ".workbench" / "artifacts" / target_flow / "develop" / "verification.md"
    if ver_path.is_file():
        try:
            checked_ver = safe_resolve_path(root_resolved, ver_path)
            content = checked_ver.read_text(encoding="utf-8", errors="replace")
            verification_info = _parse_verification_section(content, task_id)
        except (SecurityError, OSError):
            pass

    # 4. 读取任务代码具体改动快照 (artifacts/<flow>/<phase>/tasks/<id>.diff.json)
    diff_info: dict[str, Any] = {
        "files": {},
        "summary": {"total_files": 0, "additions": 0, "deletions": 0},
        "recorded_at": None,
    }
    task_phase = target_task.get("phase", "develop")
    candidate_phases = [task_phase]
    if task_phase != "develop":
        candidate_phases.append("develop")

    for ph in candidate_phases:
        diff_file = root_resolved / ".workbench" / "artifacts" / target_flow / ph / "tasks" / f"{task_id}.diff.json"
        if diff_file.is_file():
            try:
                checked_diff = safe_resolve_path(root_resolved, diff_file)
                diff_data = json.loads(checked_diff.read_text(encoding="utf-8"))
                files_map = {}
                raw_files = diff_data.get("files", [])
                if isinstance(raw_files, list):
                    for f_entry in raw_files:
                        if isinstance(f_entry, dict) and "path" in f_entry:
                            files_map[f_entry["path"]] = f_entry
                elif isinstance(raw_files, dict):
                    files_map = raw_files

                diff_info = {
                    "files": files_map,
                    "summary": diff_data.get("summary", {
                        "total_files": len(files_map),
                        "additions": sum(item.get("additions", 0) for item in files_map.values()),
                        "deletions": sum(item.get("deletions", 0) for item in files_map.values()),
                    }),
                    "recorded_at": diff_data.get("recorded_at"),
                }
                break
            except (SecurityError, OSError, json.JSONDecodeError):
                pass

    return {
        "id": task_id,
        "title": target_task.get("title", ""),
        "role": target_task.get("role", ""),
        "phase": target_task.get("phase", ""),
        "status": target_task.get("status", "todo"),
        "deps": target_task.get("deps", []),
        "owner": target_task.get("owner", ""),
        "write_scopes": target_task.get("write_scopes") or [],
        "notes": target_task.get("notes", ""),
        "note_file": note_path,
        "note_markdown": note_markdown,
        "artifacts": artifacts,
        "artifacts_by_project": changes_by_project,
        "diffs": diff_info.get("files", {}),
        "diff_summary": diff_info.get("summary", {}),
        "diff_recorded_at": diff_info.get("recorded_at"),
        "verification": verification_info,
        "started": target_task.get("started"),
        "updated": target_task.get("updated"),
        "created": target_task.get("created"),
    }


def get_gate_log(root: Path, gate_name: str, flow: str | None = None) -> str:
    """读取指定门禁日志（gate-*.log），经过 safe_resolve_path 安全检查。"""
    validate_flow_name(flow)
    root_resolved = Path(root).resolve()
    if "/" in gate_name or "\\" in gate_name or ".." in gate_name:
        safe_resolve_path(root_resolved, gate_name)
    target_flow = flow or (read_current_flow(root_resolved) if read_current_flow else DEFAULT_FLOW)

    # 规范化 gate 文件名，防路径穿越
    safe_name = os.path.basename(gate_name)
    fname = f"gate-{safe_name}.log" if not safe_name.startswith("gate-") else f"{safe_name}.log"
    if not fname.endswith(".log"):
        fname = f"{fname}.log"

    f_path = root_resolved / ".workbench" / "flows" / target_flow / fname
    if not f_path.is_file():
        # 回退至 .workbench/ 根目录（main flow 兼容）
        f_path = root_resolved / ".workbench" / fname

    if not f_path.is_file():
        return ""

    checked = safe_resolve_path(root_resolved, f_path)
    try:
        return checked.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def get_contracts(root: Path, flow: str | None = None) -> dict[str, Any]:
    """获取契约列表、当前开窗与变更审计流水。"""
    validate_flow_name(flow)
    root_resolved = Path(root).resolve()
    target_flow = flow or (read_current_flow(root_resolved) if read_current_flow else DEFAULT_FLOW)
    st = load_workbench_state(root_resolved, flow=target_flow)

    contracts = st.get("contracts", [])
    unlocks = read_unlock_records(root_resolved) if read_unlock_records else {}
    disputes = read_disputes(root_resolved) if read_disputes else {}

    # 从 audit.jsonl 提取契约跃迁历史
    audit_history: dict[str, list[dict[str, Any]]] = {}
    audit_path = root_resolved / ".workbench" / "flows" / target_flow / "audit.jsonl"
    if not audit_path.is_file():
        audit_path = root_resolved / ".workbench" / "audit.jsonl"

    if audit_path.is_file():
        try:
            checked_audit = safe_resolve_path(root_resolved, audit_path)
            for line in checked_audit.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    evt = entry.get("event", "")
                    if evt.startswith("contract_") and "name" in entry:
                        audit_history.setdefault(entry["name"], []).append(entry)
                except json.JSONDecodeError:
                    continue
        except (SecurityError, OSError):
            pass

    # 组装输出
    result_contracts = []
    for c in contracts:
        c_copy = dict(c)
        c_name = c.get("name", "")
        c_copy["unlock"] = unlocks.get(c_name)
        c_copy["is_unlocked"] = c_name in unlocks
        c_copy["is_disputed"] = c_name in disputes
        c_copy["audit_history"] = audit_history.get(c_name, [])
        result_contracts.append(c_copy)

    return {
        "contracts": result_contracts,
        "unlocks": unlocks,
        "disputes": disputes,
    }


def get_audit_log(root: Path, flow: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    """读取并安全解析全量审计流水。"""
    validate_flow_name(flow)
    root_resolved = Path(root).resolve()
    target_flow = flow or (read_current_flow(root_resolved) if read_current_flow else DEFAULT_FLOW)

    audit_path = root_resolved / ".workbench" / "flows" / target_flow / "audit.jsonl"
    if not audit_path.is_file():
        audit_path = root_resolved / ".workbench" / "audit.jsonl"

    if not audit_path.is_file():
        return []

    checked = safe_resolve_path(root_resolved, audit_path)
    entries: list[dict[str, Any]] = []
    try:
        for line in checked.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []

    if limit is not None and limit > 0:
        return entries[-limit:]
    return entries

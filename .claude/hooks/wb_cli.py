"""wb_cli — CLI 命令实现、参数解析与 main()。

wb.py 拆分模块之一。init / status / phase / gate / task / next / contract /
artifact / role / flow / config / log / report / hook / selfcheck 的入口都在
这里；main() 是 wb.py 唯一的转发目标。本模块不提供 __main__：状态写入必须经
wb.py 入口，守卫按命令行里的 wb.py/wb 认调用（_wb_invocations）。"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from wb_const import (
    ARTIFACT_LOG, DEFAULT_ROLE_SCOPES, GATES, PHASES, PHASE_ARTIFACT_CONTRACTS, PHASE_CN,
    REPO_HINTS, ROLES, WB_VERSION,
)
from wb_bash import CONFIG_SCHEMA, catastrophic_command, config_key_allowed
from wb_core import (
    DEFAULT_FLOW, _FLOW_NAME, all_flows, artifact_path, close_dispute, close_unlock,
    contract_binding, contract_drift, contract_ref_name, contract_revision, default_state,
    die, dotted_get, dotted_set, find_contract, find_root, find_task, flow_dir,
    flow_override, gate_check, inherit_flow_config, knowledge_entries, lease_expired,
    load_state, log, now, pointer_flow, print_gate, read_current_flow, read_disputes,
    read_frozen, read_unlock_records, read_unlocks, ready_tasks, refresh_task_contracts,
    release_state_lock, repo_layout_scopes, save_state, set_current_flow,
    set_flow_override, sha256_file, state_path, task_binding_for_name, task_check_errors,
    task_contract_names, task_dependency_errors, unclaimed_repos, wb_dir,
    normalize_write_scopes, select_task_batch,
)
from wb_guard import cmd_hook


def cmd_init(args) -> None:
    root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    flow = args.flow or DEFAULT_FLOW
    fd = flow_dir(root, flow)          # 校验名字；.workbench/flows/<flow>/
    fd.mkdir(parents=True, exist_ok=True)
    for sub in ("contracts", "artifacts"):
        (wb_dir(root) / sub).mkdir(parents=True, exist_ok=True)
    for ph in PHASES:
        (wb_dir(root) / "artifacts" / flow / ph).mkdir(parents=True, exist_ok=True)
    set_current_flow(root, flow)
    if state_path(root, flow).is_file() and not args.force:
        die(f"flow {flow} 已存在 state.json，如需重建请加 --force")
    st = default_state(args.name or root.name)
    scopes = repo_layout_scopes(root)
    if scopes:
        st["role_scopes"] = scopes
    inherited = inherit_flow_config(root, st, flow)
    log(st, "init", project=st["project"], flow=flow,
        **({"inherited_from": inherited} if inherited else {}))
    save_state(root, st)
    print(f"工作台已初始化：{root}")
    print(f"项目：{st['project']}  flow：{flow}  当前阶段：clarify（需求澄清）")
    if inherited:
        print(f"工作区级配置（角色范围 / 门禁命令 / 并行度）已从 flow {inherited} 继承；"
              f"本 flow 的覆盖用 config set，不会写回 {inherited}")
    if flow != DEFAULT_FLOW:
        print(f"其他 flow 并行开发时切换：wb.py flow switch <名>")
    if scopes:
        print("检测到 repos/ 跨仓库布局，角色范围已按仓库前缀重算 —— 默认值在这个布局下"
              "会放行别人仓库的同语言文件，又匹配不到自己仓库的 migrations/。")
        print("核对一遍：wb.py role scopes。仓库与角色不是按名字对应时手写前缀，例如 "
              "wb.py config set role_scopes.backend-developer "
              "'[\"repos/backend/**\",\"repos/shared/**\"]'")
        print("门禁命令也要各自 cd：config set gate_commands.test "
              "'(cd repos/frontend && npm test) && (cd repos/backend && pytest)'")
        print_unclaimed(root, scopes)


def print_unclaimed(root: Path, scopes: dict[str, list[str]]) -> None:
    """认不出名字的仓库谁都写不了，得当场说 —— 否则要到 develop 才撞成权限拒绝。"""
    un = unclaimed_repos(root, scopes)
    if un:
        print(f"\n没有角色认领这些仓库，任何角色都写不了：{', '.join(un)}")
        print("按名字认不出来（认领靠 " + " / ".join(
            sorted({h for hs in REPO_HINTS.values() for h in hs})) + "）。手写认领：")
        print("  wb.py config set role_scopes.backend-developer "
              f"'[\".workbench/artifacts/*/develop/tasks/**\",\"repos/{un[0]}/**\"]'")
        print("  （连自己原有的前缀一起写进去，config set 是整条覆盖不是追加）")


def cmd_status(args) -> None:
    root = find_root()
    st = load_state(root)
    if args.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return
    # 根路径必须显示：工作区里可以有多个仓库各带一份 .workbench/，
    # 只看项目名分不清当前操作的是哪一份。
    print(f"项目：{st['project']}　根：{root}　flow：{read_current_flow(root)}　wb {WB_VERSION}")
    # 角色锁只兜底主线程与非角色 agent（subagent 的角色按 hook 载荷的 agent_type 判定）。
    # 没设时角色越权守卫对这两类调用者整层跳过，只剩冻结与门禁两道防线 ——
    # 降级模式（harness 派不出角色 subagent，见 wb-flow）下这是常态，
    # 不在这里说破，就没人知道守卫已经少了一道。
    role_file = wb_dir(root) / "role"
    if not (role_file.is_file() and role_file.read_text(encoding="utf-8").strip()):
        print("⚠ 角色锁未设置：角色越权守卫对主线程与非角色 agent 不生效"
              "（角色 subagent 按载荷判定，不受影响）")
    cur = st["phase"]
    line = []
    for p in st["phases"]:
        g = st["gates"].get(p, {})
        # 强推的阶段必须与真正过门禁的区分开 —— status 是最常看的看板，
        # 只有 report 能看出区别等于看不出。
        mark = "*" if p == cur else ("!" if g.get("forced") else ("v" if g.get("passed") else "-"))
        line.append(f"{mark}{p}")
    print("阶段：" + "  ".join(line) + "   （* = 当前，v = 门禁已过，! = 强推）")
    print(f"当前：{cur}（{PHASE_CN.get(cur, cur)}）")
    # unconfigured 与 waived-explicit 曾经在 status 里完全不可区分（run_check
    # 的 detail 字符串里有差异，但 status 从不展示 detail）。新负责人接手时
    # 无法从这里判断「忘配置」还是「确认不需要」，只能翻 gate-<键>.log。
    waivers = {k: v for k, v in st.get("gate_waivers", {}).items() if isinstance(v, str) and v.strip()}
    configured = set(st.get("gate_commands", {}))
    unconfigured = sorted(set(rest for phase in GATES.values()
                              for spec in phase.get("checks", [])
                              if spec.startswith("cmd:")
                              for rest in [spec.partition(":")[2]]
                              if rest not in configured and rest not in waivers))
    if waivers:
        print("豁免门禁：" + ", ".join(f"{k}（{v}）" for k, v in waivers.items()))
    if unconfigured:
        print(f"⚠ 未配置门禁（隐形放行，不代表不需要）：{', '.join(unconfigured)}")

    by_status: dict[str, int] = {}
    for t in st["tasks"]:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1
    total = len(st["tasks"])
    donen = by_status.get("done", 0)
    pct = int(donen * 100 / total) if total else 0
    print(f"任务：{total} 个，完成 {donen}（{pct}%）"
          + (f"，进行中 {by_status.get('doing', 0)}" if by_status.get("doing") else "")
          + (f"，阻塞 {by_status.get('blocked', 0)}" if by_status.get("blocked") else ""))
    expired = [t["id"] for t in st["tasks"] if lease_expired(t)]
    for t in st["tasks"]:
        if args.all or t["status"] in ("doing", "blocked") or t["phase"] == cur:
            dep = f" 依赖:{','.join(t['deps'])}" if t.get("deps") else ""
            note = f" — {t['notes']}" if t.get("notes") else ""
            own = f" @{t['owner']}" if t.get("owner") else ""
            lease = " ⏰租约过期" if lease_expired(t) else ""
            print(f"  {t['id']:<5} [{t['status']:<7}] {t['phase']:<8} {t['role']:<19} {t['title']}{own}{dep}{note}{lease}")
    if expired:
        print(f"⏰ 租约过期（doing 超时，可能 agent 已中断）：{', '.join(expired)} —— "
              f"读 tasks/<id>-<角色>.md 执行记录，决定 reopen 重派或续做")

    if st["contracts"]:
        bad = contract_drift(root, st)
        print(f"契约：{len(st['contracts'])} 份" + (f"，漂移 {len(bad)} 份" if bad else "，一致"))
        for b in bad:
            print(f"  ! {b}")
    # 知识库要出现在每轮 status 里，否则 analyze/design 派发时没人记得查它
    n = len(knowledge_entries(root))
    if n:
        print(f"知识库：{n} 条（knowledge/ —— analyze/design 派发前先检索，"
              f"判据与分类见 knowledge/README.md）")
    for uname, ureason in read_unlocks(root).items():
        print(f"解冻窗口开启中：{uname} —— {ureason}")
        print(f"  改完必须 `contract bump --name {uname}`，否则窗口悬挂、文档处于无主状态")
    for dname, dreason in read_disputes(root).items():
        print(f"⚠ 争议中：{dname} —— {dreason}")
        print(f"  所有 developer 写入已停工。解除：`wb.py contract dispute --clear --name {dname}`")
    rt = ready_tasks(st, phase=cur)
    if rt:
        print(f"就绪可派发（{cur}）：" + ", ".join(t["id"] for t in rt[: st["max_parallel"]]))


def freeze_phase_artifacts(root: Path, st: dict, phase: str) -> list[str]:
    """把刚过门禁的阶段产物登记成契约并锁定，返回新登记的契约名。

    只在门禁真通过时调用：强推的阶段产物不冻结 —— 那个阶段并没有真的做完。

    为什么复用契约而不是另造一套「产物冻结」：产物被改的场景与契约完全同形 ——
    qa 打回要改需求、开发中途发现方案有问题要改 design.md。契约这条路径已经有
    申报理由必填、哈希校验、bump 通知下游三件事，另造一套只会造出第二个半成品。

    kind="artifact" 把它们与真正的接口契约区分开，见 run_check 的 contracts_locked。
    """
    owner, consumers = PHASE_ARTIFACT_CONTRACTS.get(phase, ("", []))
    if not owner:
        return []
    added = []
    for fname in GATES.get(phase, {}).get("artifacts", []):
        p = artifact_path(root, phase, fname)
        if not p.is_file():
            continue
        rel = os.path.relpath(p, root).replace(os.sep, "/")
        name = f"artifact-{Path(fname).stem}"
        # architect 手工登记过的（design.md -> design-doc）不重复登记
        if find_contract(st, name) or any(c["path"] == rel for c in st["contracts"]):
            continue
        st["contracts"].append({
            "name": name, "path": rel, "owner": owner, "consumers": list(consumers),
            "kind": "artifact", "version": 1, "revision": 1,
            "sha": sha256_file(p), "locked_at": now(), "created": now(),
        })
        log(st, "contract_lock", name=name, version=1, kind="artifact")
        added.append(name)
    return added


def cmd_phase(args) -> None:
    root = find_root()
    if args.action == "get":
        print(load_state(root)["phase"])
        return
    if args.action == "set":
        # set 不跑门禁 —— 它是回退通道，不是 advance 的快捷方式。理由必填且入日志，
        # 否则「门禁不通过不推进」有一条不留痕的旁路：任何角色都能 `phase set develop`
        # 直接跳过 clarify / analyze / design 的全部准出条件，而 `status` 只显示
        # 「当前阶段 develop」，被跳过的阶段既没有 gates 记录也没有 forced 标记。
        if not (args.reason or "").strip():
            die("phase set 必须带 --reason '<为什么直接跳阶段>'。"
                "正常推进用 `phase advance`（跑门禁）；门禁项确实不适用时用 "
                "`phase advance --force`（记 forced 标记，status 里打 !）。"
                "set 只用于回退，理由必须在跳之前写。")
        st = load_state(root, lock=True)
        if args.name not in st["phases"]:
            die(f"未知阶段 {args.name}，可选：{', '.join(st['phases'])}")
        old, st["phase"] = st["phase"], args.name
        forward = st["phases"].index(args.name) > st["phases"].index(old)
        # 跳过的阶段留下显式记录，否则 status 与 report 看不出这些阶段的门禁从未跑过。
        for skipped in st["phases"][st["phases"].index(old):st["phases"].index(args.name)]:
            if skipped not in st["gates"]:
                st["gates"][skipped] = {
                    "passed": False, "at": now(), "forced": True, "skipped_by_set": True,
                    "failures": [f"门禁未运行：phase set 直接跳到 {args.name}（{args.reason}）"],
                }
        log(st, "phase_set", **{"from": old, "to": args.name,
                                "reason": args.reason, "forward": forward})
        save_state(root, st)
        print(f"阶段：{old} -> {args.name}（理由：{args.reason}）")
        if forward:
            print(f"注意：向前跳过了 {old} 到 {args.name} 之间的门禁，"
                  f"这些阶段在 status / report 里标记为未运行门禁。")
        return
    # advance：门禁里的 cmd: 断言可能跑几分钟的 npm test，不能攥着状态锁跑 ——
    # 那会把并行 subagent 的 task done 全堵在等锁上。先无锁算门禁，再入锁落记录：
    # 期间落盘的 task done 因此不会被门禁前的旧快照盖掉。
    st = load_state(root)
    cur = st["phase"]
    rev_before = int(st.get("state_rev", 0))
    results = gate_check(root, st, cur)
    passed = print_gate(cur, results)
    if args.force and not os.environ.get("WB_ALLOW_FORCE"):
        # --force 本身已经被特权命令层限制（只有主线程/特定角色能跑到这一步），
        # 但主线程自己的误操作没有第二道防线 —— 复制粘贴出来的 --force 会让阶段
        # 无声推进，事后唯一线索是日志里的 forced: true。这里要求人在 shell 里
        # 显式开一个环境变量，把「打算强推」和「不小心打了这个参数」分开。
        die("--force 需要先在 shell 里设置 WB_ALLOW_FORCE=1 才生效"
            "（防止误拼参数导致无声强推，需要人工确认这是有意的）。", code=1)
    if not passed and not args.force:
        die("门禁未通过，阶段未推进。修完再来，或 --force 强推（会记入日志）", code=1)
    st = load_state(root, lock=True)
    if st["phase"] != cur or int(st.get("state_rev", 0)) != rev_before:
        # 门禁在锁外跑（可能几分钟），期间别人可能推了阶段或落了 task done/block。
        # 只比对 phase 漏掉后者：门禁算的是那一刻的 tasks 快照，期间新 block 的任务
        # 不算进 tasks_done 结论，据此推进就把未完成的活当完成了。state_rev 每次
        # save_state 自增，比对它能一并抓住 phase 与 tasks 两类改动。作废重跑即可。
        die(f"状态在门禁期间被另一个进程改动（phase {cur}->{st['phase']}，"
            f"rev {rev_before}->{st.get('state_rev', 0)}），这次门禁结论作废，重跑 phase advance")
    st["gates"][cur] = {
        "passed": passed,
        "at": now(),
        "forced": bool(args.force and not passed),
        "failures": [l for ok, l, _ in results if not ok],
    }
    for name in (freeze_phase_artifacts(root, st, cur) if passed else []):
        print(f"已把 {cur} 阶段产物冻结为契约 {name}：之后要改它先 "
              f"`contract unlock --name {name} --reason '<为什么>'`，改完 `contract bump` 通知下游")
    idx = st["phases"].index(cur)
    if idx + 1 >= len(st["phases"]):
        log(st, "flow_complete")
        save_state(root, st)
        print("已是最后阶段，全链路完成。")
        return
    st["phase"] = st["phases"][idx + 1]
    log(st, "phase_advance", **{"from": cur, "to": st["phase"], "forced": bool(args.force and not passed)})
    save_state(root, st)
    print(f"阶段推进：{cur} -> {st['phase']}（{PHASE_CN.get(st['phase'], '')}）")


def cmd_gate(args) -> None:
    root = find_root()
    st = load_state(root)
    phase = args.phase or st["phase"]
    results = gate_check(root, st, phase)
    if args.json:
        print(json.dumps(
            {"phase": phase, "passed": all(o for o, _, _ in results),
             "checks": [{"ok": o, "label": l, "detail": d} for o, l, d in results]},
            ensure_ascii=False, indent=2))
        ok = all(o for o, _, _ in results)
    else:
        ok = print_gate(phase, results)
    sys.exit(0 if ok else 1)


def merge_artifacts(root: Path, t: dict, flow: str) -> int:
    """把产物流水账里属于这个任务的改动并进 t["artifacts"]，返回新增条数。

    优先按 `task start` 的 PreToolUse hook 写入的 agent_id 认领；没有绑定记录时
    退回「角色 + 任务开始时间」。不要改回「读一个 current_task 文件」—— 单文件
    在并行下内容永远是最后启动的那个任务，据它归属会把两个 subagent 的改动
    全挂到一个任务上。绑定与产物流水账都只追加、从不重写：重写又是一次读改写竞态，
    去重让重复归并幂等。

    flow 定点：任务 ID 每条 flow 独立从 T1 编起，而绑定与流水账是工作区级共享
    文件 —— 不按 flow 过滤，A flow 的 T1 会把 B flow 同名任务的产物认领进来
    （实测复现）。无 flow 字段的旧行按 main 归属：字段引入前存量日志都写在
    main 线上；在别的 flow 里它们匹配不到任何任务 —— 跨 flow 的旧归属本来就是
    串扰的，宁可少归并不认错账。
    """
    logf = wb_dir(root) / ARTIFACT_LOG
    if not logf.is_file():
        return 0
    bindingf = wb_dir(root) / "task-agents.jsonl"
    agent_ids: set[str] = set()
    since = t.get("started") or t.get("created") or ""
    if bindingf.is_file():
        for raw in bindingf.read_text(encoding="utf-8").splitlines():
            try:
                binding = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if (binding.get("id") == t["id"] and binding.get("agent_id")
                    and binding.get("flow", DEFAULT_FLOW) == flow):
                agent_ids.add(binding["agent_id"])
    n = 0
    for raw in logf.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            e = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if e.get("flow", DEFAULT_FLOW) != flow:
            continue
        if agent_ids:
            if e.get("agent_id") not in agent_ids:
                continue
        elif e.get("role") != t["role"] or (e.get("at") or "") < since:
            continue
        rel = e.get("path") or ""
        if rel and rel not in t["artifacts"]:
            t["artifacts"].append(rel)
            n += 1
    return n


def _propagate_stale(st: dict, blocked_id: str) -> None:
    """沿依赖反向图传播 stale，直到完整传递闭包。

    blocked/stale/doing/todo 的依赖都必须阻断下游。skipped 是明确的完成语义，不能
    因为无关的上游 block 被改写，否则后续无法区分「跳过」和「需要重跑」。已经是
    blocked 的节点仍继续向其下游传播，避免 A -> B -> C 中 B 恰好先被 block 时漏掉 C。
    """
    queue = [blocked_id.upper()]
    seen = set()
    while queue:
        upstream = queue.pop(0)
        if upstream in seen:
            continue
        seen.add(upstream)
        for t in st["tasks"]:
            deps = [str(d).upper() for d in t.get("deps", [])]
            if upstream not in deps:
                continue
            if t.get("status") not in ("blocked", "skipped", "stale"):
                t["status"] = "stale"
                t["updated"] = now()
            queue.append(str(t["id"]).upper())


def _restore_stale(root: Path, st: dict, restored_id: str) -> None:
    """按依赖和当前契约快照恢复 stale 的传递闭包。

    一个节点只有在所有依赖均为 done/skipped 且自己的契约快照已刷新并一致时才
    能恢复。某个分支仍 blocked/stale 时，其他分支可以恢复，但该节点和它的下游
    必须继续 stale，直到所有依赖都恢复。
    """
    queue = [restored_id.upper()]
    seen = set()
    while queue:
        upstream = queue.pop(0)
        if upstream in seen:
            continue
        seen.add(upstream)
        for t in st["tasks"]:
            if upstream not in [str(d).upper() for d in t.get("deps", [])]:
                continue
            if t.get("status") != "stale":
                continue
            if task_dependency_errors(st, t):
                continue
            if refresh_task_contracts(root, st, t):
                # 不能让一个未更新的契约快照恢复任务；后续 bump/reopen 再次触发时
                # 仍有机会恢复。
                continue
            t["status"] = "todo"
            t["updated"] = now()
            queue.append(str(t["id"]).upper())


def cmd_task(args) -> None:
    root = find_root()
    st = load_state(root, lock=True)

    if args.action == "add":
        phase = args.phase or st["phase"]
        if phase not in st["phases"]:
            die(f"未知阶段 {phase}")
        st["seq"] += 1
        tid = args.id.upper() if args.id else f"T{st['seq']}"
        if find_task(st, tid):
            die(f"任务 {tid} 已存在")
        deps = [d.strip().upper() for d in (args.deps or "").split(",") if d.strip()]
        if tid in deps:
            die(f"任务 {tid} 不能依赖自己")
        for d in deps:
            if not find_task(st, d):
                die(f"依赖的任务 {d} 不存在")
        contract_names = [c.strip() for c in (args.contracts or "").split(",") if c.strip()]
        contract_refs = [task_binding_for_name(root, st, name) for name in contract_names]
        t = {
            "id": tid, "title": args.title, "role": args.role, "phase": phase,
            "status": "todo", "deps": deps,
            "contracts": contract_refs,
            "artifacts": [], "notes": "", "created": now(), "updated": now(),
        }
        write_scopes = normalize_write_scopes(args.write_scopes)
        if write_scopes:
            t["write_scopes"] = write_scopes
        st["tasks"].append(t)
        log(st, "task_add", id=tid, role=args.role, phase=phase, title=args.title)
        save_state(root, st)
        print(f"{tid}  {phase}/{args.role}  {args.title}")
        return

    if args.action == "list":
        for t in st["tasks"]:
            if args.status and t["status"] != args.status:
                continue
            if args.role and t["role"] != args.role:
                continue
            print(f"{t['id']:<5} [{t['status']:<7}] {t['phase']:<8} {t['role']:<19} {t['title']}")
        return

    t = find_task(st, args.id) if getattr(args, "id", None) else None
    if not t:
        die(f"任务不存在：{getattr(args, 'id', '')}")

    if args.action == "check":
        errors = task_check_errors(root, st, t)
        if errors:
            for error in errors:
                print(f"[FAIL] {error}")
            die(f"任务 {t['id']} check 未通过：" + "; ".join(errors))
        print(f"[PASS] 任务 {t['id']} 的依赖、契约快照和正文一致")
        return

    if args.action == "start":
        if t.get("status") != "todo":
            die(f"任务 {t['id']} 当前为 {t.get('status')}，只能从 todo（reopen 后）开始")
        errors = task_check_errors(root, st, t)
        if errors:
            die(f"任务 {t['id']} start 被拒：" + "; ".join(errors))
        t["status"] = "doing"
        t["started"] = now()
        t["attempts"] = int(t.get("attempts", 0)) + 1
        if args.owner:
            t["owner"] = args.owner
        lease = int(st.get("task_lease", 3600))
        t["lease_until"] = time.strftime(
            "%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() + lease))
        if args.role_lock:
            (wb_dir(root) / "role").write_text(t["role"], encoding="utf-8")
    elif args.action == "done":
        if t.get("status") != "doing":
            die(f"任务 {t['id']} 当前为 {t.get('status')}，只能完成 doing 任务")
        errors = task_check_errors(root, st, t)
        if errors:
            die(f"任务 {t['id']} done 被拒：" + "; ".join(errors))
        t["status"] = "done"
        if args.note:
            t["notes"] = args.note
        _restore_stale(root, st, t["id"])
        merged = merge_artifacts(root, t, st.get("_flow") or pointer_flow(root))
        if merged:
            print(f"归并 {merged} 个改动到 {t['id']}.artifacts")
    elif args.action == "block":
        if t.get("status") in ("done", "skipped"):
            die(f"任务 {t['id']} 当前为 {t.get('status')}，不能标记 blocked")
        t["status"] = "blocked"
        t["notes"] = args.reason or t["notes"]
        _propagate_stale(st, t["id"])
    elif args.action == "reopen":
        if t.get("status") not in ("blocked", "stale"):
            die(f"任务 {t['id']} 当前为 {t.get('status')}，只能 reopen blocked/stale 任务")
        dependency_errors = task_dependency_errors(st, t)
        if dependency_errors:
            die(f"任务 {t['id']} 恢复被拒：" + "; ".join(dependency_errors))
        refresh_errors = refresh_task_contracts(root, st, t)
        if refresh_errors:
            die(f"任务 {t['id']} 恢复被拒：" + "; ".join(refresh_errors))
        t["status"] = "todo"
        t["notes"] = args.note or t["notes"]
        _restore_stale(root, st, t["id"])
    elif args.action == "skip":
        if not args.reason:
            die("skip 必须带 --reason")
        t["status"] = "skipped"
        t["notes"] = args.reason
        _restore_stale(root, st, t["id"])
    t["updated"] = now()
    # 离开 doing 就没有活动租约了。留着过期的 lease_until 会让 status 对一个已 done
    # 的任务打「租约过期」，虚惊。attempts 保留（它是累计重试次数，不随状态清零）。
    if args.action in ("done", "block", "reopen", "skip"):
        t.pop("lease_until", None)
    # skip / block 的理由必须进流水账。只写进 t["notes"] 的话，下一次 reopen --note
    # 就把它覆盖掉，日志里只剩一行 task_skip，「为什么跳过」从此查不到 ——
    # 而跳过全部任务能让 tasks_done 门禁变绿。
    extra_log = {}
    if args.action in ("skip", "block") and t.get("notes"):
        extra_log["reason"] = t["notes"]
    log(st, f"task_{args.action}", id=t["id"], role=t["role"], **extra_log)
    save_state(root, st)
    print(f"{t['id']} -> {t['status']}" + (f"（{t['notes']}）" if t.get("notes") else ""))


def cmd_next(args) -> None:
    root = find_root()
    st = load_state(root)
    phase = args.phase or st["phase"]
    rt = ready_tasks(st, phase=phase if not args.any_phase else None, role=args.role)
    if not rt:
        blocked = [t for t in st["tasks"] if t["status"] == "blocked" and t["phase"] == phase]
        doing = [t for t in st["tasks"] if t["status"] == "doing"]
        # stale 与 blocked 同为停机信号（wb-loop 见到就停下交人）：上游被推翻但还没
        # reopen。漏了它会让只剩 stale 的阶段报「可以跑门禁了」，门禁的 tasks_done
        # 撞上 stale 又是 FAIL —— 下一轮 loop 空转。
        stale = [t for t in st["tasks"] if t["status"] == "stale"]
        if args.json:
            print(json.dumps({"tasks": [], "doing": [t["id"] for t in doing],
                              "blocked": [t["id"] for t in blocked],
                              "stale": [t["id"] for t in stale]}, ensure_ascii=False))
        else:
            print("无就绪任务。"
                  + (f" 进行中：{', '.join(t['id'] for t in doing)}." if doing else "")
                  + (f" 阻塞：{', '.join(t['id'] for t in blocked)}." if blocked else "")
                  + (f" 失效：{', '.join(t['id'] for t in stale)} — 上游被推翻，需 reopen 后重跑." if stale else "")
                  + (" 该阶段可以跑门禁了。" if not doing and not blocked and not stale else ""))
        sys.exit(0 if not (doing or blocked or stale) else 3)
    deferred = []
    batch = rt if args.all else rt[:1]
    if args.all:
        batch, deferred = select_task_batch(rt, st["max_parallel"])
    if args.json:
        payload = {"tasks": batch}
        if deferred:
            payload["deferred_write_scope_conflicts"] = deferred
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for t in batch:
        names = [contract_ref_name(ref) for ref in t.get("contracts", [])]
        cs = f"  契约:{','.join(name for name in names if name)}" if names else ""
        print(f"{t['id']}\t{t['role']}\t{t['title']}{cs}")
    for item in deferred:
        print(f"延后 {item['id']}：write_scopes 与 {item['with']} 重叠")


def cmd_contract(args) -> None:
    root = find_root()
    # 只在会写状态的分支上锁。impact 在锁里跑 `git grep` 子进程，大仓库要几秒 ——
    # 而 wb-contract 要求改契约前先跑 impact，此时结束的 subagent 的 SubagentStop
    # 会等在锁上，超时后角色锁与解冻窗口都不清理，下一个写入被限制在上一个角色的范围里。
    st = load_state(root, lock=args.action in ("add", "lock", "unlock", "bump", "consumers"))

    if args.action == "add":
        p = Path(args.path)
        rel = os.path.relpath((root / p).resolve() if not p.is_absolute() else p.resolve(), root)
        if rel.startswith(".."):
            # 越根的契约会同时锁死两头：Bash 提到它就被拦，Write 又先撞越根检查，
            # 契约进入无法维护的状态。
            die(f"契约必须在项目根内：{args.path} 解析为 {rel}")
        name = args.name or Path(rel).stem
        # 契约名会被当成解冻窗口的文件名（`.workbench/unlock/<名>`），所以它是
        # 一个信任边界上的输入：`--name ../../x` 能让 unlock 写到项目根外。
        # 在名字进入 state 的这一处校验，不在每个使用点做转义。
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            die(f"契约名只能用字母、数字、`.`、`_`、`-`，且首字符是字母或数字：{name}")
        if find_contract(st, name):
            die(f"契约 {name} 已存在，改动请用 contract bump")
        duplicate = next((c for c in st.get("contracts", [])
                          if c.get("path") == rel), None)
        if duplicate:
            die(f"契约路径 {rel} 已由 {duplicate['name']} 登记，不能重复登记；"
                "同一正文只能有一个冻结契约")
        if not (root / rel).is_file():
            die(f"契约文件不存在：{rel}（先写好接口定义再登记）")
        c = {
            "name": name, "path": rel, "owner": args.owner or "architect",
            "consumers": [x.strip() for x in (args.consumers or "").split(",") if x.strip()],
            "version": 1, "revision": 1, "sha": None, "locked_at": None, "created": now(),
        }
        st["contracts"].append(c)
        log(st, "contract_add", name=name, path=rel, owner=c["owner"])
        save_state(root, st)
        print(f"已登记契约 {name} v1  owner={c['owner']}  consumers={','.join(c['consumers']) or '-'}")
        print("确认定稿后执行：contract lock " + name)
        return

    if args.action == "list":
        if not st["contracts"]:
            print("尚无契约。")
            return
        opened = set(read_unlocks(root))
        disputed = set(read_disputes(root))
        for c in st["contracts"]:
            path = c.get("path")
            if not c.get("sha"):
                state = "未锁定"
            elif not path:
                state = "文件缺失"
            else:
                cur = sha256_file(root / path)
                state = "文件缺失" if cur is None else ("一致" if cur == c["sha"] else "漂移!")
            if c["name"] in opened:
                state += "/解冻中"
            if c["name"] in disputed:
                state += "/争议中"
            print(f"{c['name']:<20} v{c['version']:<3} {state:<12} "
                  f"{c['owner']:<19} -> {','.join(c['consumers']) or '-'}  "
                  f"{path or '(缺少本地路径)'}")
        return

    if args.action == "lock":
        targets = st["contracts"] if args.all else [find_contract(st, args.name or "")]
        if not args.all and not targets[0]:
            die(f"契约不存在：{args.name}")
        for c in targets:
            path = c.get("path")
            if not path:
                die(f"契约 {c['name']} 缺少本地路径")
            sha = sha256_file(root / path)
            if sha is None:
                die(f"文件缺失：{path}")
            if c.get("sha"):
                if sha != c["sha"]:
                    die(f"契约 {c['name']} 已锁定但正文发生漂移，contract lock 不能覆盖旧 SHA；"
                        "请先 unlock --reason，修改后执行 contract bump")
                c["revision"] = contract_revision(c)
            else:
                # 首次 lock 才建立不可变基线；登记阶段允许 architect 继续编辑正文。
                c["version"] = c.get("version") if isinstance(c.get("version"), int) else 1
                c["revision"] = 1
                c["sha"] = sha
                c["locked_at"] = now()
            log(st, "contract_lock", name=c["name"], version=c["version"],
                revision=c["revision"], sha=sha[:12])
            print(f"已锁定 {c['name']} v{c['version']}  r{c['revision']}  {sha[:12]}")
            # 只关自己那一份窗口，且只在本 flow 里关。`lock --all` 逐个关等于全关，
            # 但 `lock --name X` 不能顺手收掉兄弟 agent 正在用的窗口；跨 flow 同名
            # 窗口是另一条流水线的申报，本 flow 的 lock 无权替它收尾。
            close_unlock(root, c["name"], flow=st.get("_flow"))
        save_state(root, st)
        return

    if args.action == "unlock":
        c = find_contract(st, args.name)
        if not c:
            die(f"契约不存在：{args.name}")
        if not args.reason:
            die("unlock 必须给 --reason —— 冻结文档的改动理由要在改之前留痕，不是改完补")
        if not c.get("sha"):
            die(f"契约 {c['name']} 尚未首次 lock，无需 unlock；先完成首次 lock")
        path = c.get("path")
        if not path:
            die(f"契约 {c['name']} 缺少本地路径")
        current_sha = sha256_file(root / path)
        if current_sha is None:
            die(f"文件缺失：{path}")
        if current_sha != c["sha"]:
            die(f"契约 {c['name']} 正文已经漂移，不能事后 unlock；先恢复旧正文或由 architect 处理")
        records = read_unlock_records(root)
        if c["name"] in records:
            die(f"契约 {c['name']} 已有解冻窗口，必须先完成 bump 或关闭该窗口")
        d = state_path(root).parent / "unlock"
        # 老版本可能留下单一 unlock 文件。它没有契约名和旧 SHA，不能安全迁移为可消费
        # 的窗口，宁可明确阻断，也不把未知基线伪装成正式变更。
        if d.is_file():
            die("发现旧版单文件解冻窗口，无法安全迁移；请先由主线程清理该窗口")
        d.mkdir(parents=True, exist_ok=True)
        record = {
            "reason": args.reason, "sha": c["sha"], "version": c.get("version", 1),
            "revision": contract_revision(c), "opened_at": now(),
        }
        (d / c["name"]).write_text(json.dumps(record, ensure_ascii=False) + "\n",
                                     encoding="utf-8")
        log(st, "contract_unlock", name=c["name"], version=c["version"],
            revision=contract_revision(c), sha=c["sha"][:12], reason=args.reason)
        save_state(root, st)
        print(f"已开启解冻窗口：{c['name']} v{c['version']}  {c['path']}")
        print(f"理由：{args.reason}")
        print("现在可以改这一个文件。改完必须执行："
              f"wb.py contract bump --name {c['name']}")
        print("窗口在 bump / 子 agent 结束时自动关闭；不能用命令行理由替代窗口。")
        return

    if args.action == "verify":
        bad = contract_drift(root, st)
        for b in bad:
            print(f"[FAIL] {b}")
        if not bad:
            print(f"[PASS] {len(st['contracts'])} 份契约与锁定版本一致")
        sys.exit(1 if bad else 0)

    if args.action == "bump":
        c = find_contract(st, args.name)
        if not c:
            die(f"契约不存在：{args.name}")
        record = read_unlock_records(root).get(c["name"])
        if not record:
            die("bump 必须消费预先存在的 contract unlock 窗口；不能用命令行理由替代")
        old_sha = c.get("sha")
        if not old_sha:
            die(f"契约 {c['name']} 尚未首次 lock，不能 bump")
        if record.get("sha") != old_sha:
            die(f"契约 {c['name']} 的 unlock 基于旧 SHA {record.get('sha')!r}，"
                f"当前锁定基线为 {old_sha!r}，窗口已失效")
        if record.get("version") is not None and record.get("version") != c.get("version"):
            die(f"契约 {c['name']} 的 unlock 版本已过期")
        if record.get("revision") is not None and record.get("revision") != contract_revision(c):
            die(f"契约 {c['name']} 的 unlock 修订号已过期")
        reason = record.get("reason", "")
        if not reason:
            die(f"契约 {c['name']} 的 unlock 缺少 reason，不能 bump")
        path = c.get("path")
        if not path:
            die(f"契约 {c['name']} 缺少本地路径")
        sha = sha256_file(root / path)
        if sha is None:
            die(f"文件缺失：{path}")
        if sha == old_sha:
            die(f"{c['name']} 内容未变（哈希相同），不能只刷版本号；请先修改正文")

        old_binding = contract_binding(c)
        old_version, old_revision = old_binding["version"], old_binding["revision"]
        c["version"] = old_version + 1
        c["revision"] = old_revision + 1
        c["sha"], c["locked_at"] = sha, now()
        new_binding = contract_binding(c)

        invalidated = []
        for t in st["tasks"]:
            refs = t.get("contracts", [])
            if not isinstance(refs, list):
                continue
            matched = any(
                contract_ref_name(ref) == c["name"] and
                (not isinstance(ref, dict) or all(ref.get(k) == old_binding[k]
                                                 for k in old_binding))
                for ref in refs
            )
            if matched and t.get("status") != "skipped":
                t["status"] = "stale"
                t["updated"] = now()
                invalidated.append(t["id"])
        for tid in invalidated:
            _propagate_stale(st, tid)

        log(st, "contract_bump", name=c["name"], **{
            "from": old_version, "to": c["version"],
            "from_revision": old_revision, "to_revision": c["revision"],
            "from_sha": old_sha[:12], "to_sha": sha[:12], "reason": reason,
        })
        created = []
        for role in c["consumers"]:
            if role not in ROLES:
                continue
            st["seq"] += 1
            tid = f"T{st['seq']}"
            st["tasks"].append({
                "id": tid, "title": f"同步契约 {c['name']} v{c['version']} 变更：{reason}",
                "role": role, "phase": st["phase"], "status": "todo", "deps": [],
                "contracts": [new_binding.copy()], "artifacts": [],
                "notes": "由 contract bump 自动创建", "created": now(), "updated": now(),
            })
            created.append(f"{tid}({role})")
        # 只消费并关闭本契约、本 flow 的窗口；兄弟契约与别的 flow 的同名窗口必须
        # 继续存在 —— 那是别人正在进行的申报。
        close_unlock(root, c["name"], flow=st.get("_flow"))
        close_dispute(root, c["name"])
        save_state(root, st)
        print(f"{c['name']} v{old_version}/r{old_revision} -> "
              f"v{c['version']}/r{c['revision']}  {sha[:12]}  理由：{reason}")
        print("已为消费方创建返工任务：" + (", ".join(created) or "无消费方"))
        return

    if args.action == "impact":
        c = find_contract(st, args.name)
        if not c:
            die(f"契约不存在：{args.name}")
        print(f"契约 {c['name']} v{c['version']}  owner={c['owner']}")
        print("消费方角色：" + (", ".join(c["consumers"]) or "无"))
        rel = [t for t in st["tasks"] if c["name"] in task_contract_names(t)]
        print("关联任务：" + (", ".join(f"{t['id']}[{t['status']}]" for t in rel) or "无"))
        hits = grep_repo(root, c["name"])
        print("代码引用：" + (f"{len(hits)} 处" if hits else "无"))
        for h in hits[:15]:
            print(f"  {h}")
        return

    if args.action == "dispute":
        if args.clear:
            if not args.name:
                close_dispute(root)
                log(st, "dispute_clear_all")
                save_state(root, st)
                print("已解除全部契约争议。开发可恢复。")
            else:
                close_dispute(root, args.name)
                log(st, "dispute_clear", name=args.name)
                save_state(root, st)
                print(f"已解除 {args.name} 的争议。开发可恢复。")
            return
        c = find_contract(st, args.name)
        if not c:
            die(f"契约不存在：{args.name}")
        if not args.reason:
            die("dispute 必须给 --reason —— 冲突在哪要说清楚，否则架构师无法判断")
        d = state_path(root).parent / "disputes"
        d.mkdir(parents=True, exist_ok=True)
        (d / c["name"]).write_text(args.reason, encoding="utf-8")
        log(st, "dispute", name=c["name"], reason=args.reason)
        save_state(root, st)
        print(f"已落争议哨兵：{c['name']}")
        print(f"理由：{args.reason}")
        print("所有 developer 角色的写入已全线停工（执行记录与 /tmp 除外）。")
        print(f"解除：wb.py contract dispute --clear --name {c['name']}")
        print(f"或修订契约后：wb.py contract bump --name {c['name']}")
        return

    if args.action == "consumers":
        # 消费方角色随流程演进漂移（新增 knowledger 角色后 knowledge-convention 仍写着旧的
        # reviewer）。consumers 只驱动 `contract impact` 的通知目标、不进契约正文哈希，
        # 所以改它不动冻结文件、不走 unlock/bump、也不刷版本号 —— 单独一条元数据修正。
        # 与 add 一致不校验角色名（bump 通知时会跳过非 ROLES），空串即清空。
        c = find_contract(st, args.name)
        if not c:
            die(f"契约不存在：{args.name}")
        if args.consumers is None:
            die("consumers 修正必须带 --consumers '逗号分隔的角色名'（清空传空串）")
        new = [x.strip() for x in args.consumers.split(",") if x.strip()]
        old = list(c.get("consumers", []))
        c["consumers"] = new
        log(st, "contract_consumers", name=c["name"],
            **{"from": ",".join(old), "to": ",".join(new)})
        save_state(root, st)
        print(f"{c['name']} 消费方：{','.join(old) or '-'} -> {','.join(new) or '-'}")
        return


def grep_repo(root: Path, needle: str) -> list[str]:
    """尽量用 git grep（自动尊重 .gitignore），否则退回 Python 扫描。"""
    if (root / ".git").exists() and shutil.which("git"):
        r = subprocess.run(["git", "grep", "-n", "-I", "--", needle],
                           cwd=root, capture_output=True, text=True)
        if r.returncode in (0, 1):
            return [l for l in r.stdout.splitlines() if l][:200]
    hits = []
    skip = {".git", "node_modules", ".workbench", "dist", "build", "__pycache__", ".venv"}
    for p in root.rglob("*"):
        if not p.is_file() or any(s in p.parts for s in skip) or p.stat().st_size > 512_000:
            continue
        try:
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="strict").splitlines(), 1):
                if needle in line:
                    hits.append(f"{p.relative_to(root)}:{i}:{line.strip()[:120]}")
        except (UnicodeDecodeError, OSError):
            continue
    return hits[:200]


def cmd_artifact(args) -> None:
    root = find_root()
    st = load_state(root)
    phase = args.phase or st["phase"]
    d = wb_dir(root) / "artifacts" / read_current_flow(root) / phase
    d.mkdir(parents=True, exist_ok=True)
    if args.action == "path":
        print(d / args.name if args.name else d)
        return
    # list
    for p in sorted(d.iterdir()) if d.is_dir() else []:
        print(f"{p.relative_to(root)}  {p.stat().st_size}B")


def cmd_role(args) -> None:
    root = find_root()
    f = wb_dir(root) / "role"
    if args.action == "get":
        print(f.read_text(encoding="utf-8").strip() if f.is_file() else "(未设置：主线程，仅路径与危险命令守卫生效)")
    elif args.action == "set":
        if args.name not in ROLES:
            die(f"未知角色 {args.name}，可选：{', '.join(ROLES)}")
        f.write_text(args.name, encoding="utf-8")
        print(f"当前角色：{args.name}（写入范围已收紧）")
    elif args.action == "clear":
        f.unlink(missing_ok=True)
        print("角色已清除")
    elif args.action == "scopes":
        st = load_state(root, lock=True)
        if args.reset:
            # 必须跟 init 走同一条路径。只写 DEFAULT_ROLE_SCOPES 会把跨仓库项目的
            # 范围刷成裸默认值 —— 后端从此写不了自己仓库的 migrations/，却能写别人
            # 仓库的同语言文件，两个方向同时破，而输出看起来只是「刷成默认值」。
            layout = repo_layout_scopes(root)
            st["role_scopes"] = layout or json.loads(json.dumps(DEFAULT_ROLE_SCOPES))
            log(st, "role_scopes_reset", repo_layout=bool(layout))
            save_state(root, st)
            print("角色范围已刷成当前默认值。" +
                  ("检测到 repos/ 跨仓库布局，已按仓库前缀重算。" if layout else ""))
        # 按角色分节：原来一行一个角色把范围挤在一起，下面紧跟着冻结清单与解冻窗口，
        # 找单个角色要在混排里翻（flow main 的 retro 改进项 1）。
        print("角色写入范围：")
        for r, globs in st["role_scopes"].items():
            print(f"\n  {r}")
            for g in globs:
                print(f"    {g}")
        print_unclaimed(root, st["role_scopes"])
        print("\n冻结文件（任何角色都不能用工具直接写）：")
        for fr in read_frozen(root):
            print(f"  {fr}")
        opened = read_unlocks(root)
        if opened:
            print("\n解冻窗口开启中：")
            for uname, ureason in opened.items():
                print(f"  {uname} —— {ureason}")


def cmd_flow(args) -> None:
    """flow（需求线）管理：list / new / switch / remove。

    一条流水线一个 flow：state、门禁记录、产物目录、锁都按 flow 隔离。
    指针只影响 CLI 的定位，不影响守卫 —— 守卫读全部 flow 的并集，A flow 锁的
    契约不会在 B flow 视角下变成可写文件。
    """
    root = find_root()
    # flow 管理看指针真值，不跟 WB_FLOW：switch 的对象是共享指针本身，
    # list 显示的也必须是指针位置，否则钉死 WB_FLOW 的会话看不到自己切了什么。
    if args.action == "list":
        cur = pointer_flow(root)
        for f in all_flows(root):
            mark = "（当前）" if f == cur else ""
            phase = ""
            try:
                phase = json.loads(state_path(root, f).read_text(encoding="utf-8"))["phase"]
            except (OSError, json.JSONDecodeError, KeyError):
                phase = "（未初始化）"
            print(f"{f:<20} {phase:<12}{mark}")
        return
    if args.action == "new":
        flow = args.name
        if not flow:
            die("flow new 需要 <flow 名>")
        if state_path(root, flow).is_file():
            die(f"flow {flow} 已存在，要用它直接 switch")
        args2 = argparse.Namespace(root=str(root), name=None, force=False, flow=flow)
        cmd_init(args2)
        return
    if args.action == "switch":
        flow = args.name
        if not flow:
            die("flow switch 需要 <flow 名>")
        if not state_path(root, flow).is_file():
            die(f"flow {flow} 不存在（目录：{flow_dir(root, flow).relative_to(root)}）。"
                f"新建：wb.py flow new {flow}")
        set_current_flow(root, flow)
        try:
            st = json.loads(state_path(root, flow).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            die(f"flow {flow} 的 state.json 无法读取：{e}")
        print(f"当前 flow：{flow}（项目 {st['project']}，阶段 {st['phase']}）")
        print("此后 status / task / contract / artifact 都作用于这个 flow。")
        ov = flow_override()
        if ov and ov != flow:
            print(f"注意：本会话 WB_FLOW={ov} 生效中，状态命令仍作用于 "
                  f"{ov}；要先解除再操作。")
        return
    if args.action == "remove":
        flow = args.name
        if not flow:
            die("flow remove 需要 <flow 名>")
        if flow == DEFAULT_FLOW:
            die(f"main flow 不可删除 —— 它是老布局 state.json 的所在地与指针回退点")
        d = flow_dir(root, flow)
        if not d.is_dir():
            die(f"flow {flow} 不存在")
        if pointer_flow(root) == flow:
            die("不能删除当前 flow，先 wb.py flow switch main")
        if not args.force:
            die(f"remove 会删除 {d.relative_to(root)}/ 下的全部状态与产物，确认请加 --force")
        shutil.rmtree(d)
        if (wb_dir(root) / "artifacts" / flow).is_dir():
            shutil.rmtree(wb_dir(root) / "artifacts" / flow, ignore_errors=True)
        print(f"已删除 flow {flow}（状态、锁、产物）")


def cmd_config(args) -> None:
    root = find_root()
    st = load_state(root, lock=True)
    if args.action == "get":
        v = dotted_get(st, args.key) if args.key else st["gate_commands"]
        print(json.dumps(v, ensure_ascii=False, indent=2))
        return
    if not config_key_allowed(args.key):
        die(f"拒绝写入 {args.key}：不在 CONFIG_SCHEMA 白名单里。"
            f"可写字段：{', '.join(CONFIG_SCHEMA)}。新增字段需要先在 wb.py 的 "
            f"CONFIG_SCHEMA 里登记，不能隐式接受任意路径。")
    try:
        val = json.loads(args.value)
    except json.JSONDecodeError:
        val = args.value
    # 门禁命令最终是 subprocess.run(shell=True) 的输入，等价于一条绕开 Bash hook
    # 的 shell —— DENY_BASH 看不到它。写入时先筛一遍。
    if args.key.startswith("gate_commands.") and isinstance(val, str):
        why = catastrophic_command(val)
        if why:
            die(f"拒绝写入门禁命令：{why}。门禁命令会以 shell 直接执行，不经 Bash 守卫。")
    dotted_set(st, args.key, val)
    log(st, "config_set", key=args.key)
    save_state(root, st)
    print(f"{args.key} = {json.dumps(val, ensure_ascii=False)}")


def cmd_log(args) -> None:
    root = find_root()
    st = load_state(root, lock=bool(args.message))  # --tail 只读，不占锁
    if args.message:
        log(st, "note", message=args.message, role=args.role or "")
        save_state(root, st)
        print("已记录")
        return
    for e in st["log"][-args.tail:]:
        extra = " ".join(f"{k}={v}" for k, v in e.items() if k not in ("at", "event"))
        print(f"{e['at']}  {e['event']:<16} {extra}")


def cmd_report(args) -> None:
    """给复盘阶段用：把状态渲染成可粘进 retro.md 的 Markdown。"""
    root = find_root()
    st = load_state(root)
    out = [f"# {st['project']} 交付报告", "", f"生成时间：{now()}", "",
           "## 阶段门禁", ""]
    for p in st["phases"]:
        g = st["gates"].get(p)
        if not g:
            out.append(f"- {p}（{PHASE_CN.get(p,'')}）：未进入")
        else:
            flag = "强制通过" if g.get("forced") else "通过"
            fails = f"，遗留：{', '.join(g['failures'])}" if g.get("failures") else ""
            out.append(f"- {p}（{PHASE_CN.get(p,'')}）：{flag} @ {g['at']}{fails}")
    out += ["", "## 任务", "",
            "| ID | 阶段 | 角色 | 状态 | 标题 | 备注 |",
            "| --- | --- | --- | --- | --- | --- |"]
    for t in st["tasks"]:
        # skipped / blocked 的理由不渲染出来，报告里就看不出「完成」是干出来的
        # 还是跳出来的。
        note = (t.get("notes") or "").replace("|", "\\|").replace("\n", " ")
        out.append(f"| {t['id']} | {t['phase']} | {t['role']} | {t['status']} | "
                   f"{t['title']} | {note or '-'} |")
    out += ["", "## 契约", ""]
    if st["contracts"]:
        out += ["| 契约 | 版本 | Owner | 消费方 | 路径 |", "| --- | --- | --- | --- | --- |"]
        for c in st["contracts"]:
            out.append(f"| {c['name']} | v{c['version']} | {c['owner']} | "
                       f"{', '.join(c['consumers']) or '-'} | {c['path']} |")
    else:
        out.append("无。")
    bumps = [e for e in st["log"] if e["event"] == "contract_bump"]
    if bumps:
        out += ["", "### 契约变更历史", ""]
        for b in bumps:
            out.append(f"- {b['at']} {b['name']} v{b['from']}→v{b['to']}：{b.get('reason','')}")
    text = "\n".join(out) + "\n"
    if args.write:
        p = artifact_path(root, "retro", "delivery-report.md")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"已写入 {p.relative_to(root)}")
    else:
        print(text)


# --------------------------------------------------------------------------
# 参数解析
# --------------------------------------------------------------------------

def _run_selfcheck(args) -> None:
    """惰性加载自检模块：hook 与日常 CLI 不该为 wb_selfcheck（1700+ 行）付启动开销。"""
    from wb_selfcheck import cmd_selfcheck
    cmd_selfcheck(args)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="wb", description="软件开发工作台状态内核")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="初始化 .workbench/")
    p.add_argument("--name")
    p.add_argument("--root")
    p.add_argument("--flow", help="初始化指定的 flow（默认 main），同一工作区可并行多条需求线")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("flow", help="需求线管理：list / new / switch / remove")
    p.add_argument("action", choices=["list", "new", "switch", "remove"])
    p.add_argument("name", nargs="?")
    p.add_argument("--force", action="store_true", help="remove 的确认开关")
    p.set_defaults(func=cmd_flow)

    p = sub.add_parser("status", help="总览：阶段 / 任务 / 契约 / 就绪队列")
    p.add_argument("--json", action="store_true")
    p.add_argument("--all", action="store_true", help="列出所有阶段的任务")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("phase", help="阶段管理")
    p.add_argument("action", choices=["get", "set", "advance"])
    p.add_argument("name", nargs="?")
    p.add_argument("--force", action="store_true", help="门禁不通过仍推进（记入日志与报告）")
    p.add_argument("--reason", help="set 必填：为什么直接跳阶段（不跑门禁，入日志）")
    p.set_defaults(func=cmd_phase)

    p = sub.add_parser("gate", help="门禁校验（退出码 1 = 未通过）")
    p.add_argument("action", choices=["check"])
    p.add_argument("--phase", help="查别的阶段的门禁。注意有副作用：该阶段配置的 "
                                   "cmd:* 命令会真的执行（build / test 都会跑）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("task", help="任务与进度")
    p.add_argument("action", choices=["add", "list", "check", "start", "done", "block", "reopen", "skip"])
    p.add_argument("id", nargs="?")
    p.add_argument("--title")
    p.add_argument("--role", choices=ROLES)
    p.add_argument("--phase")
    p.add_argument("--deps", help="逗号分隔的前置任务 ID")
    p.add_argument("--contracts", help="逗号分隔的契约名")
    p.add_argument("--write-scopes", help="逗号分隔的写入路径/目录前缀；目录可用末尾 /**")
    p.add_argument("--note")
    p.add_argument("--reason")
    p.add_argument("--status")
    p.add_argument("--force", action="store_true")
    p.add_argument("--role-lock", action="store_true", help="start 时同时把写入范围锁到该任务角色")
    p.add_argument("--owner", help="start 时记录认领者（如 agent_id），并行下辅助归属")
    p.set_defaults(func=cmd_task)

    p = sub.add_parser("next", help="调度：返回依赖已满足的就绪任务")
    p.add_argument("--all", action="store_true", help="返回一批（受 max_parallel 限制）用于并行派发")
    p.add_argument("--phase")
    p.add_argument("--any-phase", action="store_true")
    p.add_argument("--role", choices=ROLES)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("contract", help="契约登记 / 锁定 / 漂移校验 / 申报变更 / 争议熔断")
    p.add_argument("action",
                   choices=["add", "list", "lock", "unlock", "verify", "bump", "impact",
                            "dispute", "consumers"])
    p.add_argument("path", nargs="?")
    p.add_argument("--name")
    p.add_argument("--owner", choices=ROLES)
    p.add_argument("--consumers", help="逗号分隔的角色名")
    p.add_argument("--reason", help="unlock / bump / dispute 必填：为什么要改这份冻结文档 / 冲突在哪")
    p.add_argument("--all", action="store_true")
    p.add_argument("--clear", action="store_true",
                   help="dispute：解除争议哨兵（不给 --name 则全部解除）")
    p.set_defaults(func=cmd_contract)

    p = sub.add_parser("artifact", help="产物目录")
    p.add_argument("action", choices=["path", "list"])
    p.add_argument("name", nargs="?")
    p.add_argument("--phase")
    p.set_defaults(func=cmd_artifact)

    p = sub.add_parser("role", help="角色锁：收紧当前写入范围")
    p.add_argument("action", choices=["get", "set", "clear", "scopes"])
    p.add_argument("name", nargs="?")
    p.add_argument("--reset", action="store_true",
                   help="scopes：把 state.json 里的角色范围刷成当前代码默认值（老项目迁移用）")
    p.set_defaults(func=cmd_role)

    p = sub.add_parser("config", help="配置门禁命令、并行度、角色范围")
    p.add_argument("action", choices=["get", "set"])
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("log", help="审计日志（带 message 则写入一条备注）")
    p.add_argument("message", nargs="?")
    p.add_argument("--role")
    p.add_argument("--tail", type=int, default=30)
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("report", help="渲染交付报告 Markdown")
    p.add_argument("--write", action="store_true", help="写入 artifacts/retro/delivery-report.md")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("hook", help="hook 入口，从 stdin 读 JSON")
    p.add_argument("event", choices=["pre-tool", "post-tool", "session-start", "subagent-stop"])
    p.add_argument("--format", choices=["claude", "codex"], default="claude",
                   help="调用端格式（claude 默认，codex 走 apply_patch 等差异）")
    p.set_defaults(func=cmd_hook)

    p = sub.add_parser("selfcheck", help="自检：跑一遍全链路并断言")
    p.set_defaults(func=_run_selfcheck)

    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    # WB_FLOW 只钉 CLI 命令路径；selfcheck 在进程内反复调 main()，环境里残留的
    # WB_FLOW 会把整条自检带跑到别的 flow 上，显式排除。hook 在 cmd_hook 里清空。
    set_flow_override(None)
    env_flow = os.environ.get("WB_FLOW")
    if env_flow and args.cmd not in ("selfcheck", "hook"):
        if not _FLOW_NAME.fullmatch(env_flow):
            die(f"WB_FLOW 只能用小写字母、数字、`-`、`_`：{env_flow!r}")
        set_flow_override(env_flow)
    if args.cmd == "task" and args.action == "add" and not (args.title and args.role):
        die("task add 需要 --title 与 --role")
    if args.cmd == "phase" and args.action == "set" and not args.name:
        die("phase set 需要阶段名")
    if args.cmd == "config" and args.action == "set" and (not args.key or args.value is None):
        die("config set 需要 <key> <value>")
    if args.cmd == "contract" and args.action == "add" and not args.path:
        die("contract add 需要契约文件路径")
    if args.cmd == "contract" and args.action in ("bump", "impact", "unlock") and not args.name:
        die(f"contract {args.action} 需要 --name")
    if args.cmd == "contract" and args.action == "dispute" and not args.clear and not args.name:
        die("contract dispute 需要 --name（或 --clear 解除）")
    try:
        args.func(args)
    finally:
        release_state_lock()


#!/usr/bin/env python3
"""定向回归：漂移保留态解冻窗口（kept_for_bump）只准主线程写，子 agent 命中即拒。

覆盖 #1 死锁修复引入的权限外溢加固（见 wb_core.kept_for_bump_paths、
wb_guard.hook_subagent_stop 的 kept 分支与两条冻结检查）。全程走 wb.py CLI +
`wb.py hook` 子进程，faithful 地过真实分发。无框架，assert 即测。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
WB = [sys.executable, str(HOOKS / "wb.py")]


def wb(root, *args, check=True):
    r = subprocess.run(WB + list(args), cwd=root, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"wb {' '.join(args)} 失败({r.returncode})：{r.stdout}{r.stderr}")
    return r


def fire_hook(root, event, payload, fmt="claude"):
    r = subprocess.run(WB + ["hook", event, "--format", fmt],
                       cwd=root, input=json.dumps(payload),
                       capture_output=True, text=True)
    return r


def write_pretool(root, abs_path, agent=False):
    """构造一次 Write 到 abs_path 的 PreToolUse 载荷；agent=True 模拟子 agent。"""
    payload = {"cwd": str(root), "tool_name": "Write",
               "tool_input": {"file_path": str(abs_path), "content": "x"}}
    if agent:
        payload["agent_id"] = "a-test-worker"
        payload["agent_type"] = "backend-developer"
    return fire_hook(root, "pre-tool", payload)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="wb-kfb-"))
    wb(tmp, "init", "--name", "t")
    wb(tmp, "flow", "new", "f")

    # 登记并锁定一份契约（其正文路径随即进入冻结清单）
    cfile = tmp / "design.md"
    cfile.write_text("v1\n", encoding="utf-8")
    wb(tmp, "contract", "add", "design.md", "--name", "C", "--owner", "architect")
    wb(tmp, "contract", "lock", "--name", "C")

    # 开解冻窗口 + 制造漂移
    wb(tmp, "contract", "unlock", "--name", "C", "--reason", "改字段")
    cfile.write_text("v1\nv2-drift\n", encoding="utf-8")

    # 场景 3：普通 active 窗口（还没被 SubagentStop 标记 kept）——子 agent 写应放行
    r = write_pretool(tmp, cfile, agent=True)
    assert r.returncode == 0, f"[场景3] active 窗口子 agent 写应放行，却拦了：{r.stderr}"

    # 触发 SubagentStop（无 doing 任务）→ 漂移窗口应保留并打 kept_for_bump 标记
    r = fire_hook(tmp, "subagent-stop", {"cwd": str(tmp), "agent_id": "a-test-worker"})
    assert r.returncode == 0, f"subagent-stop 应正常返回：{r.stderr}"
    win = tmp / ".workbench" / "flows" / "f" / "unlock" / "C"
    if not win.is_file():  # 单 flow 布局兜底：窗口可能在默认 unlock 目录
        cands = list((tmp / ".workbench").rglob("unlock/C"))
        assert cands, "窗口文件应仍存在（漂移保留）"
        win = cands[0]
    rec = json.loads(win.read_text(encoding="utf-8"))
    assert rec.get("kept_for_bump") is True, f"窗口应被标记 kept_for_bump：{rec}"

    # 场景 1：kept 窗口 + 子 agent 写 → 拒绝
    r = write_pretool(tmp, cfile, agent=True)
    assert r.returncode == 2, f"[场景1] 子 agent 写 kept 窗口应被拒(exit2)，得到 {r.returncode}：{r.stdout}{r.stderr}"
    assert "报回编排者" in r.stderr or "bump" in r.stderr, f"拒绝文案应指向编排者/bump：{r.stderr}"

    # 场景 2：kept 窗口 + 主线程写（无 agent_id）→ 放行
    r = write_pretool(tmp, cfile, agent=False)
    assert r.returncode == 0, f"[场景2] 主线程写 kept 窗口应放行，却拦了：{r.stderr}"

    # 场景 4：kept 窗口正文恢复到基线后，下一次 SubagentStop 应关窗（不再保留）
    cfile.write_text("v1\n", encoding="utf-8")  # 回到 lock 时的正文
    r = fire_hook(tmp, "subagent-stop", {"cwd": str(tmp), "agent_id": "a-test-worker"})
    assert r.returncode == 0, f"subagent-stop 应正常返回：{r.stderr}"
    assert not win.is_file(), "正文恢复基线后窗口应被关闭，而非继续保留"

    print("PASS: kept_for_bump 四场景全过")


if __name__ == "__main__":
    main()

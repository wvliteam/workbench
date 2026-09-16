"""Static layout checks used by wb_selfcheck."""

from __future__ import annotations

import subprocess
import json
from pathlib import Path


def check_static_layout(real_root: Path) -> None:
    hook_dir = real_root / ".claude" / "hooks"
    for module in ("wb_const", "wb_bash", "wb_core", "wb_guard", "wb_cli", "wb_selfcheck", "wb_selfcheck_static"):
        path = hook_dir / f"{module}.py"
        assert path.is_file(), f"分发不完整：{path} 缺失 —— .claude/ 要整目录拷贝"

    codex_hook = real_root / ".codex" / "hooks" / "wb.py"
    if codex_hook.exists() or codex_hook.is_symlink():
        assert codex_hook.resolve() == (hook_dir / "wb.py").resolve(), \
            f"{codex_hook} 应软链到 .claude/hooks/wb.py，实际指向 {codex_hook.resolve()}"
        tracked = subprocess.run(
            ["git", "-C", str(real_root), "ls-files", "--error-unmatch", "--", ".codex/hooks/wb.py"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        assert tracked.returncode == 0, f"{codex_hook} 未跟踪 —— 干净 checkout 上 Codex 守卫会静默失效"

    codex_hooks = real_root / ".codex" / "hooks.json"
    if codex_hooks.is_file():
        hooks = json.loads(codex_hooks.read_text(encoding="utf-8")).get("hooks", {})
        for event, hook_name in {
            "PreToolUse": "pre-tool",
            "PostToolUse": "post-tool",
            "UserPromptSubmit": "user-prompt",
            "SubagentStop": "subagent-stop",
        }.items():
            assert event in hooks, f".codex/hooks.json 缺少 {event}，Codex 端工作台守卫会漂移"
            entries = hooks[event]
            assert entries and entries[0].get("hooks"), f".codex/hooks.json 的 {event} 未配置命令"
            cmd = entries[0]["hooks"][0].get("command", "")
            assert f"hook {hook_name} --format codex" in cmd, \
                f".codex/hooks.json 的 {event} 未走 Codex 格式 {hook_name}"
        for event in ("PreToolUse", "PostToolUse", "UserPromptSubmit"):
            assert hooks[event][0].get("matcher") == ".*", \
                f".codex/hooks.json 的 {event} 必须 catch-all，枚举工具名会漏掉新写入工具"

    claude_skills = real_root / ".claude" / "skills"
    agents_skills = real_root / ".agents" / "skills"
    if agents_skills.is_symlink():
        assert agents_skills.resolve() == claude_skills.resolve(), \
            f".agents/skills 应软链到 .claude/skills，实际指向 {agents_skills.resolve()}"
    elif all(d.is_dir() for d in (claude_skills, agents_skills)):
        left = {f.relative_to(claude_skills): f for f in claude_skills.rglob("*") if f.is_file()}
        right = {f.relative_to(agents_skills): f for f in agents_skills.rglob("*") if f.is_file()}
        assert set(left) == set(right), "skills 双份拷贝文件清单不一致"
        for rel in sorted(left):
            assert left[rel].read_bytes() == right[rel].read_bytes(), f"skills 双份拷贝内容不一致：{rel}"

    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python < 3.11
        return
    for path in sorted((real_root / "agents").glob("*.toml")):
        try:
            tomllib.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise AssertionError(f"agents/{path.name} 不是合法 TOML：{exc}") from exc

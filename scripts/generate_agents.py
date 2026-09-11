#!/usr/bin/env python3
"""Generate Claude-style agent markdown from the canonical agent TOML files."""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path


def render(data: dict) -> str:
    description = data["description"]
    model = data.get("claude_model", "opus" if data.get("model_reasoning_effort") == "high" else "sonnet")
    tools = data.get("claude_tools", "Read, Grep, Glob, Bash, Write, Edit, Skill")
    return (
        "---\n"
        f"name: {data['name']}\n"
        f"description: {description}\n"
        f"tools: {tools}\n"
        f"model: {model}\n"
        "---\n\n"
        f"{data['developer_instructions'].rstrip()}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--check", action="store_true", help="只检查是否需要重新生成")
    args = parser.parse_args()

    agents = args.root / "agents"
    changed = []
    for source in sorted(agents.glob("*.toml")):
        data = tomllib.loads(source.read_text(encoding="utf-8"))
        target = source.with_suffix(".md")
        content = render(data)
        if target.read_text(encoding="utf-8") != content:
            changed.append(target.name)
            if not args.check:
                target.write_text(content, encoding="utf-8")

    if changed:
        print(("需要生成" if args.check else "已生成") + "：" + ", ".join(changed))
        return 1 if args.check else 0
    print("角色文件已同步")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

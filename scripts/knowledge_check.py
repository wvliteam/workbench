#!/usr/bin/env python3
"""检查 knowledge 分类索引及它与 references 的路由边界。"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE = ROOT / "knowledge"
REFERENCES = ROOT / "references"
INDEX_HEADINGS = ("## Current Knowledge", "## Knowledge Map")


def error(message: str) -> None:
    print(f"ERROR: {message}")


def main() -> int:
    errors: list[str] = []
    knowledge_readme = KNOWLEDGE / "README.md"
    output_contract = REFERENCES / "output-contract.md"

    for path in (knowledge_readme, output_contract):
        if not path.is_file():
            errors.append(f"缺少必需路由文档：{path.relative_to(ROOT)}")

    if not errors:
        if "references/output-contract.md" not in knowledge_readme.read_text(encoding="utf-8"):
            errors.append("knowledge/README.md 缺少 references/output-contract.md 路由链接")
        if "knowledge/README.md" not in output_contract.read_text(encoding="utf-8"):
            errors.append("references/output-contract.md 缺少 knowledge/README.md 路由链接")

    for category in sorted(path for path in KNOWLEDGE.iterdir() if path.is_dir()):
        index = category / "index.md"
        if not index.is_file():
            errors.append(f"类别缺少索引：{index.relative_to(ROOT)}")
            continue
        content = index.read_text(encoding="utf-8")
        for heading in INDEX_HEADINGS:
            if heading not in content:
                errors.append(f"{index.relative_to(ROOT)} 缺少 {heading}")
        linked = set(re.findall(r"\]\(([^)]+\.md)\)", content))
        for entry in sorted(category.glob("*.md")):
            if entry.name == "index.md":
                continue
            if entry.name not in linked:
                errors.append(f"{index.relative_to(ROOT)} 未索引 {entry.name}")

    if errors:
        for message in errors:
            error(message)
        return 1
    print("knowledge check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

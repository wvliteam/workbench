#!/usr/bin/env python3
"""wb — 软件开发工作台的状态内核（入口）。

实现按职责拆在同目录，本文件只做一件事：把真实所在目录放进 sys.path，转发
main()。

  wb_const.py      常量表（零依赖：阶段 / 门禁规则 / 角色范围 / 守卫前缀）
  wb_bash.py       命令行静态解析（纯函数）
  wb_core.py       状态 / flow / 锁 / 冻结 / 契约 / 门禁 / 调度
  wb_guard.py      权限守卫与四个 hook 事件
  wb_cli.py        CLI 命令、参数解析与 main()
  wb_selfcheck.py  自检（wb.py selfcheck）

sys.path 用 resolve() 后的目录：Codex 端经 .codex/hooks/wb.py 软链调用本文件，
sys.path[0] 是软链所在的 .codex/hooks/，不 resolve 就 import 不到兄弟模块。
**本文件是唯一入口** —— 守卫按命令行里的 `wb.py` / `wb` 认 wb 调用
（_wb_invocations），子模块一律不提供 __main__，不要直接执行它们。

用法约定：所有文档中统一以 `python3 .claude/hooks/wb.py` 调用。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from wb_cli import main
except ImportError as e:  # 分发缺文件时给一条能看懂的出路，别让 hook 静默瘫
    sys.stderr.write(
        f"wb.py 加载失败：{e}\n"
        "wb.py 的实现已拆分为同目录 wb_*.py，请整目录拷贝 .claude/hooks/"
        "（只拷 wb.py 一个文件会缺模块）。\n")
    raise SystemExit(1)

if __name__ == "__main__":
    main()

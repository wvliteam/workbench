"""wb_const — 常量表：阶段、门禁规则、角色写入范围、危险命令与守卫前缀。

wb.py 拆分模块之一，零依赖的 layer 0：只放数据化的规则（GATES /
DEFAULT_ROLE_SCOPES / GUARDED_PREFIXES 等），改规则动表不动逻辑。被
wb_bash / wb_core / wb_guard / wb_cli 共同引用，所以独立成模块 —— 并进
任何一方都会让另一方反向依赖。"""

from __future__ import annotations

import re


# --------------------------------------------------------------------------
# 常量：阶段、门禁规则、角色写入范围、危险命令
# --------------------------------------------------------------------------

# state.json 结构版本。default_state 写这个值，load_state 拒绝「比本代码更新」的
# state —— 更新的版本可能有本代码看不懂的字段，读改写会把它们丢掉。旧版本靠
# load_state 的 setdefault 补齐字段容忍，不需要单独迁移函数。
STATE_SCHEMA = 1

# 工作台本体版本。与 STATE_SCHEMA 是两件事：那个是数据结构版本，这个是工具版本 ——
# 多个项目各部署一份工作台时用它对齐（`status` 根行显示），升级步骤见 README「升级」。
# 改动分发内容（内核 / agents / skills）时递增。
WB_VERSION = "0.1.0"

PHASES = ["clarify", "analyze", "design", "develop", "verify", "retro"]

PHASE_CN = {
    "clarify": "需求澄清",
    "analyze": "现状分析",
    "design": "方案设计",
    "develop": "开发实现",
    "verify": "测试验证",
    "retro": "总结复盘",
}

ROLES = [
    "pm",
    "analyst",
    "architect",
    "frontend-developer",
    "backend-developer",
    "qa",
    "reviewer",
    "knowledger",
]

# 争议熔断只拦 developer：pm / analyst / architect / qa / reviewer 不在列。
# architect 需要改契约解除争议，qa 需要跑测试，pm 需要改需求 —— 全拦死没人能善后。
DEVELOPER_ROLES = ("frontend-developer", "backend-developer")

# 每个阶段的准出条件。artifacts 是必须存在且非空的产物文件，
# checks 是可执行的断言（见 run_check）。想改规则只动这张表。
GATES = {
    "clarify": {
        "artifacts": ["requirements.md"],
        "checks": [
            "artifact_contains:requirements.md:验收标准",
            "artifact_contains:requirements.md:非目标",
        ],
    },
    "analyze": {
        "artifacts": ["current-state.md"],
        "checks": ["artifact_contains:current-state.md:风险"],
    },
    "design": {
        "artifacts": ["design.md"],
        "checks": [
            "artifact_contains:design.md:方案对比",
            "contracts_locked",
            "tasks_exist",
            "no_blocked:*",
        ],
    },
    "develop": {
        # 产物是最小可运行校验的记录。没有它，develop 门禁在未配 gate_commands 的
        # 项目里四条全 PASS —— 阶段可以在零代码证据下推进。
        "artifacts": ["verification.md"],
        "checks": [
            "contracts_intact",
            "tasks_done:develop",
            "cmd:lint",
            "cmd:build",
        ],
    },
    "verify": {
        "artifacts": ["test-report.md"],
        "checks": ["contracts_intact", "tasks_done:verify", "cmd:test"],
    },
    "retro": {
        "artifacts": ["retro.md"],
        "checks": [
            "artifact_contains:retro.md:改进项",
            "artifact_contains:retro.md:可复用",
            # 沉淀出口（ROMA 对比第八节 / 落地顺序 9）：沉淀章节必须存在，
            # 且经验真的落进 knowledge/（或显式声明无可沉淀）。
            "artifact_contains:retro.md:沉淀",
            "knowledge_written",
            # 改进项出口：每条要么转成任务（T<ID>）、要么当场落地、要么明确放弃。
            # 只查章节存在等于「写进散文就消失」—— flow main 的两条改进项即为实例。
            "improvements_tracked",
            "tasks_done:*",
        ],
    },
}

# 阶段产物的契约化：门禁一通过就把该阶段产物登记成契约并锁定。值是 (owner, consumers)。
# 上游产物此前只在「恰好有角色锁」时才受保护 —— 角色范围检查在 role 缺失时整层跳过，
# 主线程与非角色 subagent 随时能重写 requirements.md 且不留痕。登记成契约后走的是
# design-doc 那条现成的路：哈希冻结、改动先 `contract unlock --reason` 申报、
# `bump` 给下游发同步任务，一行新机制都不用造。
# develop 不在表里：verification.md 由编排者写，没有角色 owner。
PHASE_ARTIFACT_CONTRACTS = {
    "clarify": ("pm", ["analyst", "architect"]),
    "analyze": ("analyst", ["architect"]),
    "design": ("architect", ["frontend-developer", "backend-developer", "qa"]),
    "verify": ("qa", ["reviewer"]),
    "retro": ("reviewer", []),
}

# 角色默认可写范围（相对项目根的 fnmatch 模式）。
# 产物目录带 flow 维度：`.workbench/artifacts/<flow>/<phase>/`，模式中间一层用 `*`
# 通配（照 ROMA 的 `[^/]+` 写法），规则对所有需求一视同仁，不为每个需求改配置。
# 产物目录按阶段隔离：每个角色只能写自己阶段的产物，写不了上游的方案与需求文档。
# 项目布局不同时用 `wb.py config set role_scopes.<role> <json>` 覆盖，
# 或 `wb.py role scopes --reset` 把老项目的 state.json 刷成当前默认值。
#
# 三类范围值得单独说明，它们都是补实测出来的误拦：
#
# `*.md` 给开发与 reviewer：写 README、补接口说明、落 ADR 都是本职。之前只有
# architect 含 `docs/**`，于是 develop 阶段的开发碰 README 会被拒，而拒绝信息给的
# 第一条出路「交给对应角色」在那时不存在 —— architect 已经下场了。放宽碰不到已定稿
# 的产物：阶段产物过门禁后是冻结契约，守卫第二层先拦，与角色范围无关。
#
# 测试框架配置给 qa：按约定放仓库根，而 qa 原本只有四个测试目录。拦住它等于拦住
# 「配 e2e」这件事本身，堵的是这个角色的本职而不是跨界。`pytest.ini` / `tox.ini` 与
# `*.config.*` 一起列，否则 qa 配得了 vitest 配不了 pytest。`pyproject.toml` 与
# `setup.cfg` 故意不给 —— 那两个同时装着依赖与打包配置，不是测试专属文件。
#
# 前端的根级布局与扩展名：`components/` `pages/` `lib/` `styles/` 是 Next.js / Nuxt /
# Vite 的标准位置，`.js` / `.jsx` / `.vue` / `.html` / `.scss` 是同样常见的技术栈。
# 原来的列表默认了「源码在 src/ 或 web/ 下且用 TypeScript」。
DEFAULT_ROLE_SCOPES = {
    "pm": [".workbench/artifacts/*/clarify/**"],
    "analyst": [".workbench/artifacts/*/analyze/**"],
    "architect": [
        ".workbench/artifacts/*/design/**", ".workbench/contracts/**", "docs/**",
    ],
    "frontend-developer": [
        ".workbench/artifacts/*/develop/tasks/**",
        "web/**", "frontend/**", "app/**", "src/**", "public/**",
        "components/**", "pages/**", "lib/**", "styles/**",
        "*.json", "*.ts", "*.tsx", "*.js", "*.jsx", "*.vue",
        "*.css", "*.scss", "*.html", "*.md",
    ],
    "backend-developer": [
        ".workbench/artifacts/*/develop/tasks/**",
        "server/**", "backend/**", "api/**", "src/**", "migrations/**",
        "*.json", "*.py", "*.go", "*.java", "*.md",
    ],
    "qa": [
        ".workbench/artifacts/*/verify/**",
        "tests/**", "test/**", "e2e/**", "spec/**",
        "*.config.ts", "*.config.js", "*.config.mjs", "pytest.ini", "tox.ini",
    ],
    "reviewer": [".workbench/artifacts/*/retro/**", "docs/**", "*.md"],
    # 知识库写权限专属（ROMA 对比第八节的沉淀出口）。knowledge/ 在 GUARDED_PREFIXES
    # 里，别的角色（含持有 *.md 的 reviewer 与开发）写不进 —— 否则沉淀会退化成
    # 「谁顺手谁写」，查找的人不知道哪条可信。
    "knowledger": ["knowledge/**"],
}

# 跨仓库布局下按目录名认领仓库。只用于生成默认范围，认领不到的仓库谁都写不了 ——
# init 与 `role scopes` 会点名让你手写前缀，见 unclaimed_repos()。
REPO_HINTS = {
    "frontend-developer": ("frontend", "web", "client", "ui", "www"),
    "backend-developer": ("backend", "server", "api", "service", "svc"),
}

# 产物流水账。post-tool 只往这里追加，由 task done 归并进任务的 artifacts ——
# 不能在 hook 里读改写 state.json，见 hook_post_tool 的说明。
ARTIFACT_LOG = "artifacts.jsonl"

# 角色范围里「只认显式前缀」的目录。这几处的路径不参与通配匹配：范围里没有以该
# 前缀打头的模式，就是谁都不能写。
#
# `.workbench/`：否则 `*.md` / `*.json` 会跨进别的阶段的产物与契约目录，把阶段隔离绕开。
#
# 其余三个装的是守卫自己：`.claude/hooks/wb.py` 是权限引擎，`.claude/settings.json`
# 是 hook 注册表，`.claude/agents/*.md` 是角色定义，`.codex/` `.agents/` 是 Codex 端
# 的同一套东西。`fnmatch` 的 `*` 跨 `/`，所以 `*.py` 放行任意目录下的 .py、`*.json`
# 放行 settings.json、`*.md` 放行 agent 定义 —— 实测 backend-developer 能写
# `.claude/hooks/wb.py`、frontend-developer 能写 `.claude/settings.json`，两者都不在
# 任何哈希基线里，改完 `contract verify` 也发现不了。防线保护 state，却不保护防线自己。
#
# `knowledge/` 不是守卫本体，是沉淀知识库（ROMA 对比第八节的沉淀出口）：同样的
# 前缀收窄解决同一类问题 —— reviewer 与两个开发都持有 `*.md`，裸扩展名跨 `/`，
# 不收窄的话谁都能写知识条目，「knowledger 角色对沉淀质量负责」就落空了。它的
# 拒绝话术与守卫本体不同，见 _check_write_target。
# `references/` 是公共操作规范层（ROMA references 借鉴，见 docs/references-extraction.md）：
# 性质等同角色定义 —— 规范由主线程维护、角色只读，reviewer 的裸 `*.md` 不收窄就能写它。
# `scripts/` 是工作区级公共脚本（repos_apply.py / repos_tui.py 等）与 `repos.json` 清单：
# 不收窄的话 backend-developer 的裸 `*.py` 能改 init 脚本、frontend-developer 的裸
# `*.json` 能改清单 —— 都是初始化流程被静默改坏的形态。守卫本体在 `.claude/`，这层
# 只是「公共脚本与清单同样由主线程维护、角色只读」的收窄。`.vscode/` 同理：机器本地
# 的 IDE 配置由 repos_apply.py 生成，裸 `*.json` 一样跨得进去。
# 这三条只在 workbench 布局（存在 repos/）下生效：README「适配到自己的项目」的单仓库
# 场景里 scripts/ 是项目自己的代码目录、.vscode/ 是项目自己的配置，不归工作台管。
GUARDED_PREFIXES = (".workbench/", ".claude/", ".codex/", ".agents/",
                    "knowledge/", "references/")
WORKSPACE_GUARDED_PREFIXES = ("scripts/", "repos.json", ".vscode/")

# 冻结文件：任何角色（含主线程、含 owner）都不能用工具直接写，只能经 wb.py 命令改。
# `.workbench/frozen` 由 save_state 生成，是这份清单的落盘缓存 ——
# hook 每次工具调用都要读它，读一个纯文本列表比解析整个 state.json 便宜一个量级。
# 流水账在列表里是因为归属判定读它：能追加一行就能把别人的改动记到自己名下。
# wb.py 自己写它不受影响 —— 守卫只拦工具调用，不拦这个进程内的文件写。
FROZEN_ALWAYS = ["state.json", "role", "unlock", "frozen", ARTIFACT_LOG, "audit.jsonl"]

# 写入型 shell 动作。仍保留用于 uncertain=True 时的兜底匹配。
BASH_WRITE = re.compile(
    r"(>>?|\btee\b|\bsed\s+-i|\bperl\s+-\S*i|\btruncate\b|\bpatch\b|\bdd\b|"
    r"\bshred\b|\bpython3?\s+-c\b|\bnode\s+-e\b|\bln\s+-\S*[sf]|"
    r"\bcp\b|\bmv\b|\binstall\b)"
)

# 跨端工具识别：覆盖 Claude 与 Codex 两套工具名。
# 借鉴 ROMA lib/hookio.py 的做法 —— 一套实现同时吃两套端。
WRITE_TOOL = re.compile(
    r"Write|Edit|MultiEdit|NotebookEdit|"
    r"apply_patch|write_file|edit_file", re.I)
# Monitor：与 Bash 同一个 shell 环境跑 tool_input.command（ws 模式无 command，
# 取到空串后在 pre/post 两侧自然 no-op）。它的 command 是完整脚本字符串（多行、
# 可含 heredoc），下游 strip_heredocs/_split_pipeline/resolve 与 Bash 同一条路径。
SHELL_TOOL = re.compile(
    r"Bash|Monitor|shell|exec_command|unified_exec", re.I)
READ_TOOL = re.compile(r"^(?:Read|read_file|file_read)$", re.I)


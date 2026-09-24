# 工作台 Agents 工作流合理性深度审计与修复方案报告（2026-09-22）

> **审计基准**：基于软件开发工作台（Workbench）核心协作规范（[AGENTS.md](../AGENTS.md)）、架构设计文档（[docs/](../docs/)）、角色定义体系（[agents/](../agents/)）、核心技能（[.claude/skills/](../.claude/skills/)）与 Python 状态内核实现（[.claude/hooks/](../.claude/hooks/)）。
>
> **报告目的**：对工作流在实际运行、并发调度、安全拦截、门禁校验及人机工程中的**合理性缺陷、逻辑割裂与演进瓶颈**进行系统性技术分析，并提供具备完整代码蓝图、架构演进路径与配置调整建议的实施指南。

---

## 目录

- [一、审计摘要与问题等级矩阵](#一审计摘要与问题等级矩阵)
- [二、P0 级问题：角色权限守卫与业务代码管控的严重割裂（“幽灵硬拦”与死代码）](#二p0-级问题角色权限守卫与业务代码管控的严重割裂幽灵硬拦与死代码)
  - [1. 现象与代码证据](#1-现象与代码证据)
  - [2. 根因追溯与演进断层](#2-根因追溯与演进断层)
  - [3. 实际危害与工程摩擦](#3-实际危害与工程摩擦)
  - [4. 修复实施方案（方案 A 与方案 B）](#4-修复实施方案方案-a-与方案-b)
- [三、P1 级问题：核心阶段门禁断言基于“裸子串匹配”的脆弱性与误判风险](#三p1-级问题核心阶段门禁断言基于裸子串匹配的脆弱性与误判风险)
  - [1. 现象与代码证据](#1-现象与代码证据-1)
  - [2. 失效模式（False Positive 与 False Negative）](#2-失效模式false-positive-与-false-negative)
  - [3. 修复实施方案：结构化章节与非空内容校验引擎](#3-修复实施方案结构化章节与非空内容校验引擎)
- [四、P1 级问题：流程负荷过重与缺乏分级流水线（Tiered Pipeline）的生产阻力](#四p1-级问题流程负荷过重与缺乏分级流水线tiered-pipeline的生产阻力)
  - [1. 现象与机制阻力](#1-现象与机制阻力)
  - [2. 成本与摩擦量化分析](#2-成本与摩擦量化分析)
  - [3. 修复实施方案：三级流水线架构设计（Fast-Track / Standard / Full）](#3-修复实施方案三级流水线架构设计fast-track--standard--full)
- [五、P2 级问题：全局共享指针 `.workbench/current-flow` 在多终端/多会话下的并发竞态](#五p2-级问题全局共享指针-workbenchcurrent-flow-在多终端多会话下的并发竞态)
  - [1. 现象与代码证据](#1-现象与代码证据-2)
  - [2. 状态踩踏机理分析](#2-状态踩踏机理分析)
  - [3. 修复实施方案：基于 Session 的无感 Flow 路由绑定](#3-修复实施方案基于-session-的无感-flow-路由绑定)
- [六、P2 级问题：契约仅有哈希物理冻结而缺乏语法与语义合法性校验](#六p2-级问题契约仅有哈希物理冻结而缺乏语法与语义合法性校验)
  - [1. 现象与落地脱节](#1-现象与落地脱节)
  - [2. 修复实施方案：契约锁定预检与规范 Lint 挂载](#2-修复实施方案契约锁定预检与规范-lint-挂载)
- [七、演进路线图与修复排期清单](#七演进路线图与修复排期清单)

---

## 一、审计摘要与问题等级矩阵

工作台在“确定性外部状态机”、“POSIX 文件锁并发控制”、“契约哈希冻结”以及“主干+旁路双轨角色模型”上展现了优秀的工程成熟度。然而，在**规范声明与代码执法的一致性**、**门禁语义严谨度**、**流水线分级灵活性**以及**多会话路由隔离**上，存在以下 5 项需要重点修改的问题：

| 优先级 | 问题定义 | 涉及模块 / 文件 | 核心影响 |
| :---: | :--- | :--- | :--- |
| **P0** | **角色权限守卫与业务代码管控严重割裂** | `wb_guard.py`<br>`wb_const.py`<br>`wb_core.py`<br>`AGENTS.md`<br>`roles.md` | 文档与警告宣称对未认领仓库和越界代码“硬拦截”，但实际内核完全放行仓库源码，造成虚假安全感与大量无效报警噪音。 |
| **P1** | **核心阶段门禁断言采用“裸子串匹配”** | `wb_core.py`<br>`wb_const.py` | 仅靠 `artifact_contains:requirements.md:验收标准` 判定，模型极易用“待定/暂无”等占位符蒙混过关，或因中英文同义词被误杀。 |
| **P1** | **六阶段流水线缺乏分级机制（缺少 Fast-Track）** | `wb_core.py`<br>`wb_cli.py`<br>`wb_const.py`<br>`wb-flow` | 缺乏轻量级流水线，单文件修改或简单 Bug 修复面临沉重的 6 阶段、6 产物以及首写归属闸门摩擦，投入产出比失衡。 |
| **P2** | **全局共享单指针 `current-flow` 并发竞态** | `wb_core.py`<br>`wb_cli.py`<br>`wb_guard.py` | 多终端或多会话并发执行不同需求线时，`flow switch` 会篡改全局共享指针，引发跨 Flow 的命令误操作与状态混淆。 |
| **P2** | **契约仅有哈希冻结而无语法/语义校验** | `wb_core.py`<br>`wb_cli.py` | 契约锁定只校验 SHA-256，语法畸变（如格式错误的 YAML/JSON）的伪契约仍会被锁定，导致前后端并行开发在下游联调崩溃。 |

---

## 二、P0 级问题：角色权限守卫与业务代码管控的严重割裂（“幽灵硬拦”与死代码）

### 1. 现象与代码证据

在工作台体系中，文档、警告提示与底层 Hook 实现之间存在显式的“前后矛盾”：

#### 证据 A：文档宣称具备严格的物理源码隔离
* [`AGENTS.md#L88-L90`](file:///Users/wangpenghao/code/personal/workbench/AGENTS.md#L88-L90)：
  > “**认不出项目名的仓库谁都写不了。** 只要有一个项目被认领，认不出的那些就落在所有角色范围之外 —— **是硬拦，不是跨仓库放行**。`init` 与 `role scopes` 会点名，照它给的命令认领...”
* [`docs/roles.md#L62-L75`](file:///Users/wangpenghao/code/personal/workbench/docs/roles.md#L62-L75)：
  > “在工作台体系中，多达 7 个角色不拥有业务产品代码的修改权限... `analyst`、`qa`、`reviewer` 虽然配置了 Write 工具，但写入范围严格排除了业务代码... **权限守卫硬编码这个映射**。”
* [`wb_core.py#L1377-L1387`](file:///Users/wangpenghao/code/personal/workbench/.claude/hooks/wb_core.py#L1377-L1387) 注释与实现：
  > “只要有一个仓库被认领，`repo_layout_scopes` 就走 `repos/<仓库>/**` 分支，于是认不出名字的仓库谁都写不了 —— **是硬拦，不是跨仓库放行**。这个失败要到 develop 阶段才暴露成一次权限拒绝，所以 init 与 `role scopes` 提前点名。”

#### 证据 B：底层 Guard 实现完全放行仓库代码
在 [`wb_guard.py#L598-L601`](file:///Users/wangpenghao/code/personal/workbench/.claude/hooks/wb_guard.py#L598-L601) 中：
```python
role = current_role(rootr, data)
guarded = _guarded_prefix(rootr, rel)
if not guarded:
    return  # <-- 核心漏洞：只要路径不属于受守前缀，直接无条件 return 放行！
```
检查 `guarded_prefixes(root)`（[`wb_bash.py#L362`](file:///Users/wangpenghao/code/personal/workbench/.claude/hooks/wb_bash.py#L362)）：
```python
GUARDED_PREFIXES = (
    ".workbench/", ".claude/", ".codex/", ".agents/", ".comate/",
    "agents/", "skills/", "plugins/", "mcps/", "knowledge/", "references/"
)
WORKSPACE_GUARDED_PREFIXES = ("scripts/", "repos.json", "repos/index.md", ".vscode/")
```
**产品源码路径（如 `repos/.source/**`、`server/**`、`web/**`）完全不在 `guarded_prefixes` 中！**

#### 证据 C：自检用例明确断言“代码不判角色”
在 [`wb_selfcheck.py#L1740-L1742`](file:///Users/wangpenghao/code/personal/workbench/.claude/hooks/wb_selfcheck.py#L1740-L1742) 中：
```python
assert guard({"tool_name": "Write", "cwd": cw, "agent_type": "qa",
              "tool_input": {"file_path": "server/api.py"}}) == 0, \
    "仓库代码不做角色判定"
```

### 2. 根因追溯与演进断层

通过 Git 提交历史追溯（commit `f7e0a15`，*“仓库索引与单仓画像；角色范围守卫恢复并收窄到工作流核心路径”*）：
1. **历史背景**：早期工作台尝试用 `role_scopes` 对所有文件（含业务源码）做通配匹配。但由于 Python `fnmatch` 的通配符 `*` 跨目录分隔符 `/`，导致后端开发在多仓布局下写不了自己的 `migrations/`，却能写别人仓库的 Python 文件；在 `/tmp` 创建测试脚本也会被频繁误拦。
2. **架构妥协**：2026-09-12 提交中，设计者决定“收窄防线：只对工作流核心路径（受守前缀）判角色；仓库代码、/tmp、项目根外一律放行，别乱写文件交由模型与 Harness 层规范”。
3. **遗留割裂**：防线收窄后，**文档、状态警告和默认配置未同步收敛**：
   * `wb_const.py` 的 `DEFAULT_ROLE_SCOPES` 依然保留了几十条针对业务代码的模式（`web/**`, `server/**`, `*.py`, `*.ts` 等），纯属死代码；
   * `unclaimed_repos()` 依然在依据这个死配置，在 `status` 和 `role scopes` 中发出恐吓式警告：“未认领仓库谁都写不了”，但实际上任何角色都能写！
   * `docs/roles.md` 依然信誓旦旦声称“权限守卫硬编码排除了 7 个角色的业务代码写入”。

### 3. 实际危害与工程摩擦

1. **虚假安全感**：编排者误以为分析师（`analyst`）或 QA 绝对无法篡改后端代码，但实际上若 Prompt 失控或模型幻觉，`analyst` 的 `Write` 工具可以随心所欲修改 `repos/.source/` 下的任何业务核心源码。
2. **严重的开发者心智噪音**：每次执行 `wb.py status` 或 `wb.py init`，控制台都会因 `unclaimed_repos()` 打印大段警告与修复命令（如 `config set role_scopes.backend-developer ...`）。开发者花费大量时间配置了复杂的 glob 数组，结果在底层是纯粹的空转逻辑。

### 4. 修复实施方案

#### 方案 A（强烈推荐：诚实定调，文档与逻辑闭环）
保持现有“守卫不阻断正常业务开发代码”的高容忍度原则，彻底清除虚假警告与死代码：
1. **更新规范文档**：
   * 修改 [AGENTS.md](../AGENTS.md) 与 [docs/roles.md](../docs/roles.md)，明确声明：“守卫的物理硬拦截仅覆盖工作流基础设施（`.workbench/`、守卫本体、规范与知识库）。角色对业务源码的读写边界属于 Harness 派发与 Agent 自律层，守卫不进行盲目阻断”。
2. **重构 `unclaimed_repos()`**：
   * 将 `unclaimed_repos()` 从“硬拦截警报”降级为“分工推荐提示”，话术由 `“认不出名字的仓库谁都写不了 —— 是硬拦”` 改为 `“仓库暂无推荐开发角色，开发任务请在 task add 时指定角色”`。
3. **清理 `DEFAULT_ROLE_SCOPES` 死代码**：
   * 移除各角色中无效的代码 glob（如 `frontend-developer` 中的 `web/**`, `*.tsx`，`backend-developer` 中的 `server/**`, `*.py` 等），仅保留其真正具有防御意义的受守路径（如各角色合法的 `.workbench/artifacts/*/phase/**` 产物目录）。

#### 方案 B（严谨收拢：建立物理角色源码闸门）
若业务场景要求“必须由 Hook 机械性阻断越权写代码”：
1. 修改 `wb_guard.py` 的 `_check_write_target`，增加**角色级产品源码拦截规则**：
```python
# 明确禁止修改产品代码的角色列表
NO_CODE_ROLES = frozenset({"pm", "analyst", "qa", "reviewer", "knowledger"})

if role in NO_CODE_ROLES and rel.startswith("repos/.source/"):
    hook_deny(f"角色 {role} 严格禁止修改产品源码（{rel}）。分析、测试或评审发现问题请打回任务或报回编排者。")
```
2. 对开发角色，按项目前缀建立正向白名单匹配，让 `role_scopes` 中的 `repos/.source/<project>/**` 真正具备执法能力。

---

## 三、P1 级问题：核心阶段门禁断言基于“裸子串匹配”的脆弱性与误判风险

### 1. 现象与代码证据

在 [`wb_const.py#L76-L134`](file:///Users/wangpenghao/code/personal/workbench/.claude/hooks/wb_const.py#L76-L134) 的 `GATES` 规则表中，前置三阶段的核心准出逻辑高度依赖 `artifact_contains`：
* `clarify`: `artifact_contains:requirements.md:验收标准`、`artifact_contains:requirements.md:非目标`
* `analyze`: `artifact_contains:current-state.md:风险`
* `design`: `artifact_contains:design.md:方案对比`
* `retro`: `artifact_contains:retro.md:改进项`、`可复用`、`沉淀`

查看其实现 [`wb_core.py#L967-L974`](file:///Users/wangpenghao/code/personal/workbench/.claude/hooks/wb_core.py#L967-L974)：
```python
if kind == "artifact_contains":
    fname, _, needle = rest.partition(":")
    p = artifact_path(root, phase, fname)
    label = f"{fname} 包含「{needle}」"
    if not p.is_file():
        return False, label, "产物文件不存在"
    ok = needle in p.read_text(encoding="utf-8", errors="replace")
    return ok, label, "已覆盖" if ok else "缺少该章节"
```

### 2. 失效模式（False Positive 与 False Negative）

1. **偷工减料直接 PASS（False Positive）**：
   * Subagent 在输出文档时，若遇到时间紧张或上下文不足，生成如下文本：
     ```markdown
     ## 验收标准
     待定 / 暂无 / TBD
     ```
   * 门禁因为文本中包含`"验收标准"`四个字，直接给出 `[PASS]`，允许 `phase advance` 推进到后续阶段。这直接架空了硬规则 7（“clarify 与 design 推进前确认需求与设计”）。
2. **中英文同义词误杀（False Negative）**：
   * 若模型响应英文提示输出 `# Acceptance Criteria`，或写成 `## 验收指标`、`## 方案权衡`，门禁立刻给出 `[FAIL]` 并阻断流水线。
3. **与成熟门禁的实现深度严重脱节**：
   * 同样在 `wb_core.py` 中，后补的 `analyze_parts_complete`（[L995](file:///Users/wangpenghao/code/personal/workbench/.claude/hooks/wb_core.py#L995)）完整解析了 `manifest.json` 并验证了引用关系；`improvements_tracked`（[L1114](file:///Users/wangpenghao/code/personal/workbench/.claude/hooks/wb_core.py#L1114)）细致解析了 Markdown 表格首行、字段内容并排查了 `T<ID>`/`已落地`。相比之下，处于流水线源头的 `artifact_contains` 简陋得不合情理。

### 3. 修复实施方案：结构化章节与非空内容校验引擎

将 `artifact_contains` 升级为具备 Markdown 结构感知与占位符排查的断言函数：

```python
# wb_core.py 改进蓝图
SECTION_ALIASES = {
    "验收标准": ("验收标准", "验收准则", "acceptance criteria"),
    "非目标": ("非目标", "out of scope", "non-goals"),
    "风险": ("风险", "风险评估", "risks"),
    "方案对比": ("方案对比", "方案权衡", "alternatives", "options considered"),
}

PLACEHOLDER_WORDS = frozenset({"待定", "待补充", "tbd", "todo", "暂无", "无", "-"})

def run_check_section(root: Path, phase: str, fname: str, section_key: str) -> tuple[bool, str, str]:
    p = artifact_path(root, phase, fname)
    label = f"{fname} 包含有效「{section_key}」"
    if not p.is_file():
        return False, label, "产物文件不存在"
    
    text = p.read_text(encoding="utf-8", errors="replace")
    aliases = SECTION_ALIASES.get(section_key, (section_key,))
    
    # 匹配 Markdown 标题：# ~ ####
    pattern = rf"^(?:#{{1,4}})\s*.*?(?:{'|'.join(re.escape(a) for a in aliases)}).*?$\n(.*?)(?=^(?:#{{1,4}})\s|\Z)"
    match = re.search(pattern, text, re.M | re.S | re.I)
    if not match:
        return False, label, f"缺少「{section_key}」章节（支持别名：{', '.join(aliases)}）"
    
    body = match.group(1).strip()
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    if not lines:
        return False, label, f"「{section_key}」章节内容为空"
    
    # 排查纯占位符内容
    content_words = set(re.findall(r"\w+", "".join(lines).lower()))
    if len(lines) <= 2 and (content_words.issubset(PLACEHOLDER_WORDS) or len("".join(lines)) < 8):
        return False, label, f"「{section_key}」章节仅包含占位符（{lines[0][:30]}），未提供实质内容"
    
    return True, label, "已覆盖且内容有效"
```

---

## 四、P1 级问题：流程负荷过重与缺乏分级流水线（Tiered Pipeline）的生产阻力

### 1. 现象与机制阻力

当前工作台设计深受“大企业严格研发流程”思维主导，强调“流程是默认路径，不走是例外”：
* **阶段链条强锁定**：`clarify` → `analyze` → `design` → `develop` → `verify` → `retro` 缺一不可。
* **物理闸门强前置**：在 [`wb_guard.py#L129`](file:///Users/wangpenghao/code/personal/workbench/.claude/hooks/wb_guard.py#L129) 中，主线程首次修改任何产品源码（`repos/.source/**`）前，必须执行 `flow switch/new/attribute` 完成归属，否则直接 Exit 2 阻断。

### 2. 成本与摩擦量化分析

对于日常编码中占比 **60%~70% 的低复杂度任务**（如：修正一个正则错误、为前端页面加一个 loading 状态、更新一条配置）：
1. **调度延迟**：串行派发 `pm` 产出需求文档 → `analyst` 产出现状分析 → `architect` 产出设计与契约锁定 → 才能进入 `develop`。全套执行需要 5~8 轮模型往返，耗时往往在 15~30 分钟。
2. **Token 浪费**：生成大量格式化的 Markdown 冗余文本，消耗数万甚至数十万 Token。
3. **开发者被动“绕过”**：因为完整流程过于繁重，开发者往往会习惯性使用 `--force` 强推，或者在主线程利用 `flow attribute --adhoc` 逃避流水线，导致工作台在日常开发中被逐渐边缘化。

### 3. 修复实施方案：三级流水线架构设计（Fast-Track / Standard / Full）

工作台应支持**“分级流水线（Tiered Pipeline）”**，在创建 Flow 时声明其敏捷级别：

```
Tier 1: fast (快捷/缺陷)    develop ──────────> verify (仅需实现 + 单测)
Tier 2: standard (标准功能) design ───> develop ───> verify (设计方案 + 实现 + 测试)
Tier 3: full (重大跨仓特性) clarify ──> analyze ──> design ──> develop ──> verify ──> retro
```

#### 具体实施路径：
1. **Flow 初始化参数扩展**：
   ```bash
   python3 .claude/hooks/wb.py flow new fix-login-null --tier fast --desc '修复登录为空指针异常'
   ```
2. **`state.json` 记录 `tier` 字段**：
   在 `default_state()` 中保存 `tier`（默认为 `full`）。
3. **动态阶段裁切与门禁映射**：
   * 当 `tier == "fast"` 时：
     * `PHASES` 动态调整为 `["develop", "verify"]`；
     * 跳过 `clarify`、`analyze`、`design` 阶段及其所有文档检查；
     * 首写归属闸门（`_attribution_gate`）直接放行该 Flow 下的代码改动；
     * `verify` 阶段跑通过后直接允许交付。
   * 当 `tier == "standard"` 时：
     * 从 `design` 阶段开局，跳过前期需求调研产物。

---

## 五、P2 级问题：全局共享指针 `.workbench/current-flow` 在多终端/多会话下的并发竞态

### 1. 现象与代码证据

* 工作台设计了完善的 Flow 状态隔离：每条 Flow 拥有独立的 `.workbench/flows/<flow>/state.json`、锁文件与日志。
* 但当前 Flow 的定位，默认依赖**全局唯一的单文件指针**：
  * [`wb_core.py#L66`](file:///Users/wangpenghao/code/personal/workbench/.claude/hooks/wb_core.py#L66)：`FLOW_PTR = "current-flow"`
  * [`wb_core.py#L96-L119`](file:///Users/wangpenghao/code/personal/workbench/.claude/hooks/wb_core.py#L96-L119)：`read_current_flow` 优先读 `_FLOW_OVERRIDE`，未设置时读取 `.workbench/current-flow`。

### 2. 状态踩踏机理分析

在现代 Agent 协同场景中，开发者经常在一个 IDE 窗口中开辟两个终端，或同时启动多个 Agent 会话分别推进不同任务：
1. 终端 A 正在推进 `flow-login`，已进入 `develop` 阶段；
2. 终端 B 启动并执行 `wb.py flow switch flow-pay`，准备处理支付需求；
3. 全局共享的 `.workbench/current-flow` 被立刻改写为 `flow-pay`；
4. 此时终端 A 里的开发 Agent 完成了代码编写，执行 `wb.py task done T1` —— **该命令会读取全局指针，导致 T1 的完成状态被错误提交到 `flow-pay` 的状态机中，甚至导致任务 ID 越界崩溃！**
5. 虽然文档提供了 `export WB_FLOW=xxx` 的应对手段，但这依赖严格的人工心智防范，极易出现疏漏。

### 3. 修复实施方案：基于 Session 的无感 Flow 路由绑定

工作台内部已经实现了非常稳健的会话识别机制（[`wb_guard.py#L58-L70`](file:///Users/wangpenghao/code/personal/workbench/.claude/hooks/wb_guard.py#L58-L70) 的 `_session_key`）。应当将该机制延伸至 Flow 路由：

1. **会话级 Flow 指针存储**：
   * 当会话内执行 `wb.py flow switch <name>` 或 `wb.py flow new <name>` 时，不仅修改全局指针，同时在 `.workbench/sessions/<session_key>.flow` 中记录当前会话绑定的 Flow 名。
2. **分级路由解析算法**：
   重构 `read_current_flow(root: Path, data: dict | None = None)`：
   * **优先级 1**：显式环境变量 `WB_FLOW`（手动钉死最高）；
   * **优先级 2**：读取当前 Session 绑定的 `.workbench/sessions/<session_key>.flow`；
   * **优先级 3**：回退读取全局 `.workbench/current-flow`（兼容旧单终端习惯）。
3. **彻底终结跨终端指针踩踏**，无需开发者手动反复 `export WB_FLOW`。

---

## 六、P2 级问题：契约仅有哈希物理冻结而缺乏语法与语义合法性校验

### 1. 现象与落地脱节

在工作台体系中，契约锁定的核心判定在 [`wb_core.py#L648`](file:///Users/wangpenghao/code/personal/workbench/.claude/hooks/wb_core.py#L648)：
```python
c["sha"] = hashlib.sha256(path.read_bytes()).hexdigest()
```
* **现状**：只要文件存在，无论其内容是标准的 OpenAPI 3.0，还是严重缩进错误的 YAML、畸变 JSON，甚至是一句空话，`wb.py contract lock` 都会正常记录哈希并宣告锁定。
* **脱节后果**：
  * Architect Agent 产出的契约文档如果存在语法错误，`contracts_locked` 门禁将顺利 PASS 并推进到 `develop` 阶段。
  * 前后端 Agent 并发启动后，各自尝试解析契约文件均报错崩溃，此时不得不触发昂贵的 `contract dispute` 熔断或打回重做。

### 2. 修复实施方案：契约锁定预检与规范 Lint 挂载

在 `contract lock` 执行前增加**格式预检与语义校验拦截**：

```python
# wb_cli.py cmd_contract_lock 增强蓝图
import json
try:
    import yaml
except ImportError:
    yaml = None

def validate_contract_content(path: Path) -> tuple[bool, str]:
    raw = path.read_text(encoding="utf-8")
    ext = path.suffix.lower()
    
    if ext == ".json":
        try:
            json.loads(raw)
        except Exception as e:
            return False, f"JSON 语法解析失败：{e}"
    elif ext in (".yaml", ".yml"):
        if yaml:
            try:
                yaml.safe_load(raw)
            except Exception as e:
                return False, f"YAML 语法解析失败：{e}"
    
    # 基础字段完备性（针对 API 契约）
    if "api" in path.name.lower() or "openapi" in path.name.lower():
        if not re.search(r"\b(paths|endpoints|methods|GET|POST)\b", raw):
            return False, "API 契约未声明任何接口路由定义（paths/endpoints）"
            
    return True, "校验通过"
```
同时在 `contracts_locked` 门禁中加入该断言，杜绝语法畸变的伪契约进入开发阶段。

---

## 七、演进路线图与修复排期清单

建议按三个迭代周期推进上述修复：

```
[迭代 1: 消除割裂与加固]
  ├─ 统一 P0: 对齐角色范围守卫事实，更新 AGENTS.md/roles.md，清理死配置与虚假警告
  └─ 加固 P1: 门禁断言升级为结构化标题+非空内容校验（artifact_section）

[迭代 2: 敏捷降阻]
  └─ 落地 P1: 建立三级流水线机制（Fast-track / Standard / Full），降低简单任务心智成本

[迭代 3: 并发与质量深化]
  ├─ 解决 P2: Session 级 Flow 路由隔离，终结 current-flow 指针竞态
  └─ 完善 P2: 契约锁定轻量语法与路由完整性预检
```

本报告梳理的 5 项问题直击当前工作台在“规范严苛性”与“工程现实性”之间的摩擦核心。完成上述优化后，Workbench 将在保持顶尖确定性状态约束的同时，显著提升运行透明度、开发吞吐率与多 Agent 协同的安全韧性。

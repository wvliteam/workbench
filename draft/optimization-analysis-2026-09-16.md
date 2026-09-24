# 工作台项目优化分析与建议清单（2026-09-16）

**评估范围**：
- 状态内核与调度引擎：`.claude/hooks/wb_core.py`、`wb_cli.py`、`wb_const.py`
- 权限守卫与命令解析：`.claude/hooks/wb_guard.py`、`wb_bash.py`
- 角色定义与提示词体系：`agents/*.toml`、`agents/*.md`、`.claude/agents/`、`.codex/agents/`
- 辅助脚本与工具链：`scripts/repos_apply.py`、`scripts/repos_tui.py`、`scripts/generate_agents.py`
- 流程规范与文档约定：`AGENTS.md`、`docs/`

---

## 一、总结与总体评价

当前工作台是一套高度成熟、设计克制的**本地研发流程治理内核**：
1. **轻量与自洽**：零常驻后台、纯标准库、单个 Python 内核拆分模块，依赖 `flock` 与原子重命名保障状态与冻结缓存的一致性。
2. **防线与闭环**：通过契约冻结、阶段门禁、角色权限收窄与首写归属闸门，将规范从“模型提示词规劝”沉淀为“PreToolUse 机制硬前置”。
3. **测试充分**：`wb_selfcheck.py` 具备 60+ 断言覆盖及变异测试（Mutation Testing），测试网拦截能力扎实。

但在经历多轮演进后，项目在**提示词残留、挂载点感知、契约深度校验、门禁自适应能力、任务图算法稳健性**等方面，仍有显著的优化空间。

---

## 二、高优缺陷与直接摩擦点（P0 / P1）

### 1. 角色 Subagent 提示词中残留无效特权命令 `role set` (P0)

* **代码事实**：
  全部 8 个角色的基准定义文件（`agents/pm.toml`、`analyst.toml`、`architect.toml`、`backend-developer.toml`、`frontend-developer.toml`、`qa.toml`、`reviewer.toml`、`knowledger.toml`）及其派生的 Markdown 文件中，开头指令均为：
  ```markdown
  第一件事：`python3 .claude/hooks/wb.py role set <role>`
  ```
* **冲突分析**：
  在权限守卫中，`("role", "set")` 已在 [`wb_guard.py:627`](../.claude/hooks/wb_guard.py#L627) 被硬编码列入 `PRIVILEGED_WB` 特权命令表，非主线程（带有 `agent_type` / `agent_id`）调用直接返回 **exit code 2（权限拦截）**。工作台当前设计早已演进为**由 hook 根据载荷的 `agent_type` 自动确定角色**，角色 subagent 无需且不能调用 `role set`。
* **负面影响**：
  每个角色 subagent 被派发后的第一轮操作，均会机械执行该命令并被守卫拦截，产生无谓的错误上下文、消耗模型调用轮次，且容易使模型产生“权限不足，试图寻找绕过方法”的歧义。
* **优化建议**：
  1. 修改 `agents/*.toml`，删除 `role set` 命令，改为：“工作台已根据会话载荷自动识别你的角色为 `<role>`，写入范围已受守卫保护，无需手动执行 role set”。
  2. 运行 `python3 scripts/generate_agents.py` 重新生成 `agents/*.md` 及各端软链引用。

---

### 2. 挂载与初始化操作被首写闸门误拦（Attribution Gate 误伤）(P1)

* **代码事实**：
  `_attribution_gate`（[`wb_guard.py:72-110`](../.claude/hooks/wb_guard.py#L72-L110)）对未做 flow 归属的主线程拦截所有对 `repos/.source/**` 的写操作。实测在未归属会话下：
  - `ln -s /path/to/repo repos/.source/proj/repo` → 拦截（exit 2）
  - `mkdir -p repos/.source/proj/repo` → 拦截（exit 2）
  - `rm -rf repos/.source/proj/repo` → 拦截（exit 2）
* **体验问题**：
  拦截话术为：“`先别写产品源码 repos/.source/...（主线程首写闸门拦截）`”。但使用者或初始化脚本此时执行的是**仓库软链挂载与目录初始化**，根本不是在修改产品代码，该话术严重误导用户。
* **优化建议**：
  - **方案 A（精准语义识别）**：在 `wb_bash.py` 的命令分析中，将对 `repos/.source` 的符号链接创建（`ln -s`）、目录结构创建（`mkdir`）识别为非代码写入操作，仅在发生真实源码文件写入（`Edit`、`Write`、重定向、`sed -i`）时施加闸门。
  - **方案 B（明确话术）**：若判定初始化操作也必须明确 flow 归属，则修改话术为：“工作区挂载与产品源码操作均需先做会话归属，请先运行 `wb flow switch/new` 或 `wb flow attribute --adhoc --reason '初始化挂载'`”。

---

### 3. 历史主需求线（`main` flow）状态挂起未收口 (P1)

* **事实**：
  `.workbench/flows/main/state.json` 当前停留在 `phase: design`，`tasks: []`，且无 `design.md` 产物。原本计划在该需求线中落地的 `wb repo` 命令组，最终以独立脚本 [`scripts/repos_apply.py`](../scripts/repos_apply.py) 与 [`repos_tui.py`](../scripts/repos_tui.py) 实现。
* **优化建议**：
  主工作流处于未完成的中间悬挂状态。应通过 `wb flow remove main --force` 或补充正式归档，重置/清理该历史遗留 flow，使新用户或新会话执行 `wb status` 时看到干净明确的工作区状态。

---

## 三、控制面与核心机制演进（P1 / P2）

### 4. 契约校验增强：从“内容哈希”扩展至“语法规范与兼容性校验” (P1)

* **现状与缺口**：
  当前 `wb contract lock` / `verify` 仅做 SHA-256 哈希比对。架构师在 `bump` 契约时，若引入了非法的 OpenAPI YAML 语法，或删除了关键必填字段（Breaking Change），哈希比对只能确认“文件变了”，无法识别破坏性语义变更。
* **优化建议**：
  1. **语法校验**：在 `contract lock` / `bump` 执行时，按文件扩展名触发轻量校验钩子（如 `.yaml`/`.json` 调用基础解析器，检查是否存在语法错误）。
  2. **兼容性断言**：在项目初始化模板中支持接入 OpenAPI Diff 等工具，作为可选的门禁检查项 `gate_commands.contract-compat`。

---

### 5. 门禁机制完善：项目类型自动探测与 CI Attestation 接入 (P1)

* **现状与缺口**：
  1. **未配置隐形跳过**：未配置 `gate_commands.test/lint/build` 时，阶段推进默认跳过。虽然 `status` 增加了未配置警告，但新项目接入极易漏配。
  2. **本地执行信任边界**：门禁命令通过 `subprocess.run(shell=True)` 在本地执行，编排者同时具备配置权、执行权与结果判定权，缺乏隔离证明。
* **优化建议**：
  1. **工程脚手架探测**：在 `wb init` 或 `status` 时，自动探测目录中的构建元数据（如 `package.json` → `npm test`、`pyproject.toml` → `pytest`、`Cargo.toml` → `cargo test`），若尚未配置命令则直接生成一键配置建议。
  2. **CI 状态接入**：增加 `ci:<provider>` 门禁类型（如 GitHub Actions 运行状态查询），支持从远程 CI 系统拉取真实构建与测试结果，作为替代本地直接执行的高可信证明。

---

### 6. 任务依赖图显式环检测算法（DAG 拓扑排序）(P2)

* **现状与缺口**：
  目前任务防循环依赖完全基于 `wb_core.py` 中的隐式逻辑：添加任务时其依赖必须先存在，且不能依赖自己。这一逻辑仅在“严格按拓扑顺序串行创建任务”时成立。一旦未来支持修改任务依赖（`task edit --deps`）、动态插入上游任务或任务图模板批量导入，隐式约束将失去防护能力。
* **优化建议**：
  在 `wb_core.py` 中引入标准的 Kahn 算法（入度表拓扑排序）或 DFS 环检测函数，在任务新增和修改依赖时进行全图校验，从算法层面提供硬保障。

---

### 7. 路径匹配收紧：解决 `fnmatch` 跨目录分隔符扩散问题 (P2)

* **现状与缺口**：
  角色范围校验使用 `fnmatch.fnmatch(rel, pattern)`。Python 的 `fnmatch` 将 `*` 翻译为 `.*`，因此 `*.py` 会同时匹配 `backend/api.py` 与 `frontend/tests/mock.py`。虽然工作台已对核心受守前缀进行了显式前缀收窄，但在多仓库复杂代码树中，通配符仍然偏宽松。
* **优化建议**：
  在 Python 3.13+ 环境下，角色范围匹配全面升级使用 `pathlib.PurePath.full_match()`，支持标准 Glob 行为（单星号 `*` 仅匹配单层目录文件名，双星号 `**` 递归跨层匹配）。

---

## 四、跨端适配与会话治理（P2 / P3）

### 8. 多 Agent 环境标准化适配（Antigravity / Gemini / Codex）(P2)

* **现状**：
  项目通过 `AGENTS.md` 作为统一协作规范，针对 Claude Code（`.claude/`）和 Codex（`.codex/`）建立了较完整的目录软链和 hook 适配。但对于其他现代 Agent 平台（如当前运行的 Google Antigravity、Gemini CLI 等），缺少原生的 hook 映射与 subagent 自动桥接工具。
* **优化建议**：
  - 扩展 `scripts/generate_agents.py`，支持根据平台标准导出不同 Agent Harness 格式。
  - 在 `docs/` 中建立通用 Agent 适配规范（Adapter Spec），定义环境变量、输入输出载荷与 hook 注册的映射标准。

---

### 9. 长周期会话标记保活（Activity Touch）(P3)

* **代码事实**：
  会话归属标记存放在 `.workbench/sessions/<sha256(session_id)[:16]>`，落盘后由 `_prune_session_marks` 定期清理 30 天前的标记文件。
* **边界情况**：
  标记仅在执行 `flow switch/new/attribute` 时创建一次，在会话进行中不随操作刷新 mtime。若一个会话持续运行超过 30 天，另一会话执行归属时会触发 prune 将其标记删除，导致原长周期会话突然无法写入源码（虽可自愈，但会造成困惑）。
* **优化建议**：
  在 `PreToolUse` 或 `PostToolUse` 正常放行产品代码写入时，对当前会话的标记文件进行轻量的 `touch` 更新，确保活跃会话的生命周期与实际工作时长同步。

---

## 五、工程效率与工具链易用性（P3）

### 10. 仓库清单管理的非交互式 CLI 增强 (P3)

* **现状与痛点**：
  当前多仓清单管理为两极化形态：要么手写 `repos.json`，要么在终端启动交互式的全屏 curses TUI（`scripts/repos_tui.py`）。AI Agent 无法在无 tty 环境下运行 TUI，当需要动态添加/配置新仓库时，不得不直接覆写 JSON。
* **优化建议**：
  在 [`scripts/repos_apply.py`](../scripts/repos_apply.py) 中增加非交互式修改子命令，如：
  ```bash
  python3 scripts/repos_apply.py --add '{"project":"bddev","name":"newrepo","remote":"git@..."}'
  python3 scripts/repos_apply.py --delete 'newrepo'
  ```
  让编排 Agent 能安全、原子地操作清单条目。

---

### 11. 仓库画像脚手架生成工具 (P3)

* **现状**：
  每个仓库接入后，均需由 `analyst` 手写画像三件套（`overview.md`、`setup.md`、`test.md`）。
* **优化建议**：
  在 `repos_apply.py` 中增加 `--init-profiles` 开关，在软链或 clone 完成后，根据仓库文件特征自动生成结构完整的模板占位文件（包含预填的依赖安装命令、测试运行入口猜测），大幅提升 analyst 角色画像建档效率。

---

## 六、优化排期路线图

| 序号 | 优化事项 | 影响维度 | 实施成本 | 建议排期 |
| :--- | :--- | :--- | :--- | :--- |
| **1** | 清理角色定义中的无效 `role set` 指令 | 消除 Subagent 首轮必崩/权限拦截 | 极低（改 TOML + 跑生成脚本） | **第一迭代（P0）** |
| **2** | 软链挂载操作与首写闸门解耦 / 修正话术 | 消除初始化工作区的误拦与困惑 | 低（调整 wb_bash 或拒绝提示） | **第一迭代（P1）** |
| **3** | 重置/归档悬挂的历史 `main` flow | 保持 `status` 状态干净无噪声 | 极低（CLI 操作） | **第一迭代（P1）** |
| **4** | 增加项目构建/测试类型自动探测 | 防止测试门禁被隐式跳过 | 中（wb_cli 增加探测启发式规则） | **第二迭代（P1）** |
| **5** | 任务图显式环检测算法（DAG 拓扑排序） | 加固任务图模型，防止依赖成环 | 低（引入 Kahn 算法） | **第二迭代（P2）** |
| **6** | 契约增加 OpenAPI / 模式语法校验 | 防止破坏性接口流入开发阶段 | 中（接入可选 schema lint） | **第二迭代（P2）** |
| **7** | 长周期会话标记 touch 刷新保活 | 避免超长活跃会话被意外重锁 | 极低（放行时 touch） | **第二迭代（P3）** |
| **8** | `repos_apply.py` 增加非交互修改参数 | 便于 AI Agent 自动化管理仓库清单 | 中（扩展 argparse 与清单方法） | **第三迭代（P3）** |

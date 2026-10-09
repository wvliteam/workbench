# Workbench 内核代码审查结论（2026-10-06）

## 元信息

- **审查对象**：workbench 开发工作台内核，`/home/work/workspace/code/workbench`
- **范围**：约 1.3 万行 Python（`.claude/hooks/` 核心、`web/` dashboard、`scripts/`），HTML 模板，测试与 CI 现状
- **方法与基线**：初稿记录了三路并行审查；本次于 2026-10-06 对照 `30907ef` 的源码、调用点、AST 统计和定向检查复核。文本搜索只能辅助确认引用，不能单凭命中次数证明无死代码、无漏洞或性能收益。
- **性质**：本文区分已确认行为、条件性风险和可选重构。部分安全修复会有意收紧归属条件、敏感文件读取或网络访问行为，不能统一视为“零行为变化”；后续实现应按具体行为补定向验收。

## 优先级总览

原 P0/P1/P2 编号保留作条目引用，**不再直接代表最终严重程度**。修正后的排序如下；本次复核不足以将全部原 P0 项统一定为紧急安全漏洞。

| 原条目 | 修正后优先级 | 判断与前提 |
|---|---|---|
| P0-2 的 role 写入 | P1，优先修复 | 依赖 role 文件兜底的调用者存在并发空读放行窗口；显式角色载荷不受此窗口影响 |
| P0-3 dashboard 暴露 | P1；实际敏感数据已暴露时应优先处置 | 非回环无认证属条件性泄露风险；缺 Host 校验另需加固，尚未做浏览器利用验证 |
| P0-1、P0-4 | P1，先确认收紧边界 | 分别是归属成功条件缺失、敏感路径规则覆盖不足，不等于全部权限守卫失效 |
| P1 各项、P2 中的明确小缺陷与口径去重 | P2，按维护需求逐步处理 | 先补可独立运行的测试，再做必要拆分；不因行数多而整批重构 |
| P2 中的缓存与扫描优化 | P3，测量后决定 | 已发现重复读取，但未量化这些调用点的耗时及优化收益 |

---

## 正确性 / 安全（沿用原 P0 编号）

### P0-1 失败的归属命令也能解锁会话归属闸门

- **位置**：`.claude/hooks/wb_guard.py:73`、`:753`、`:986`（标记写入、归属命令识别及 PreToolUse 调用点）
- **问题**：归属标记在命令**执行前**（PreToolUse）就落盘。`wb.py flow switch <不存在的名>` 会失败退出，但会话归属检查此时已满足，后续产品源码写入不再被这一层拦截。代码注释（第 761 行 ponytail 标注）也记录了这一限制。
- **影响**：第 47–53 行声称的「硬前置闸门」可被一条注定失败的归属命令架空。它会让主线程跳过 flow 归属审计，但不会直接绕过冻结文件、活动契约、争议熔断、任务 write-scopes 或角色范围检查，因此不等于整个守卫体系失效。
- **改法**：候选方案是移到 PostToolUse，在确认归属命令成功后落标记。先核对各端完成事件和退出码载荷；对复合 shell 命令，整条命令 `exit 0` 不足以证明其中的归属命令成功，需定义支持的命令形式及失败处理。新增成功、失败、未执行分支和结果缺失的定向测试后再收紧。

### P0-2 role 文件非原子写，存在角色检查放行窗口；flow 文件另有一致性风险

- **位置**：`.claude/hooks/wb_cli.py:791`（cmd_task start 写 role）、`wb_cli.py:1279`（cmd_role set 写 role）、`wb_core.py:142-144`（`set_current_flow` 写共享指针 `current-flow`）、`wb_core.py:160-163`（`set_strict_flow_routing` 写标记）
- **问题**：`write_text` 会先截断再写入，存在空内容窗口。`current_role()` 对主线程及内置非角色 agent 从 role 文件兜底取值，空读会使 `_check_write_target` 跳过角色范围检查；Bash 的特权命令判断也使用该角色值。明确携带有效 `agent_type` 的角色 subagent 不依赖这个文件。
- **影响**：role 的两处写入值得修复，但冻结检查仍独立生效。`current-flow` 空读会回退到 `main`，属于路由一致性风险；`strict-flow-routing` 只检查 `is_file()`，空内容不关闭开关，本次没有发现它因非原子内容写入导致的放行问题。
- **改法**：role 的两处写入复用 `atomic_write_text`，配并发读取回归；`current-flow` 也可原子化，但这不解决多个会话主动切换共享指针的问题，仍需显式 flow 路由。strict 标记不列为必修漏洞。

### P0-3 dashboard `--host` 可绑非回环且全服务零鉴权、无 Host 头校验

- **位置**：`web/wb_dashboard.py:740`（`--host` 参数）+ `create_app`（`:289`，未配置认证或 Host 校验）
- **问题**：默认监听 `127.0.0.1` 限制了远程直连范围，但不代表完整的访问控制。使用 `--host 0.0.0.0` 后，在网络可达的条件下，其他客户端可无认证访问源码 diff、门禁日志等 API。Host 未校验还留下 DNS rebinding 防护缺口；本次未在具体浏览器和网络环境中复现利用链。
- **影响**：是否已构成敏感数据泄露取决于监听地址、网络可达性、实际数据和部署层访问控制，不能把默认本地模式与主动对外暴露等同定级。
- **改法**：保留默认回环监听。若支持非回环访问，应要求有效认证，并覆盖页面、API、SSE 和文档入口；仅打印告警或启动确认不构成访问控制。Host 白名单作为独立防线，按实际访问名称配置；`0.0.0.0` 是监听地址，不应直接当成合法访问主机清单。认证方案与 Host 拒绝策略需要分别验收。

### P0-4 敏感文件读取防护覆盖窄，`.env` 与 `secrets/` 的嵌套路径漏网

- **位置**：`.claude/hooks/wb_guard.py:220-223`
- **问题**：单文件 `.env`/`.env.*` 只在 `len(parts)==1` 时命中，因此 `sub/.env` 和 `../../.env` 都不拦。`secrets/` 也只检查 `parts[0]`，所以嵌套的 `sub/secrets/x` 同样漏网；`*.pem`、`*.key`、`id_rsa` 则按 basename 判定。这里既有仓库子目录漏网，也有工作区外路径是否属于本守卫责任边界的定义问题。
- **影响**：静态命名的凭据路径未被一致识别。现有根外 `.pem`、`.key`、`id_rsa` 已受该读取规则约束，不能直接用“根外写入不受守卫约束”推导根外读取也应全部放行。
- **改法**：建议按 basename 匹配 `.env`/`.env.*`，并明确是否把任意目录分量 `secrets` 都视为敏感目录。这是规则覆盖面的扩展，需核对示例文件和普通目录的误拦边界，补仓库子目录、嵌套 secrets、根外路径的正反例；不应只当作无行为变化的一行替换。

---

## 可维护性 / 可测性（沿用原 P1 编号）

### P1-1 `cmd_selfcheck` 过长，测试场景难以独立运行

- **位置**：`.claude/hooks/wb_selfcheck.py:51`
- **问题**：本文件只有一个顶层函数，51–2478 行共 2428 行。AST 统计为 513 个 `assert`、156 个 `guard()` 调用点、235 个 `quiet()` 调用点；这些是源码数量，不是独立测试用例数。整体在临时目录运行，具有真实工作区隔离，但大量主题场景共享状态和执行顺序。
- **影响**：难以独立运行或复用某组回归。断言失败本身有 traceback 行号和提示，困难主要是重建前置状态，而非必须“人肉数行”。静态布局检查已抽到 `wb_selfcheck_static.py`，不应把全套测试都描述成毫无拆分。
- **改法**：先抽测试辅助函数和少量主题场景，保留现有总入口；在消除依赖后逐步引入独立临时目录与场景选择。仅把同构输入/预期表格化，不压缩状态转换测试，也不承诺未经验证的“消掉过半断言”收益。

### P1-2 两个长分发函数与一个长 diff 函数，可按复杂分支逐步拆分

- **`run_check`**（`wb_core.py:986-1275`，290 行）：有 12 个 `kind` 分支，包含简短判定与较长的命令执行逻辑。当前可以用不同 `spec` 直接测试单个分支；优先抽复杂分支即可，不必为了可测性立刻增加整套 `CHECK_HANDLERS` 注册表。
- **`cmd_contract`**（`wb_cli.py:955-1233`，279 行）：处理十个子命令。bump 与 readopt 共享部分路径/哈希校验，最终变更已复用 `_rebaseline_contract()`；但 bump 必须校验预先申报的窗口，readopt 使用显式恢复理由，两者前置安全条件并不相同。可分 action 拆函数，不应把不同校验强行合成“相同前缀”。
- **`record_task_diff`**（`wb_cli.py:480-662`，183 行）：有 7 处 Git 子进程调用和多处异常降级，异常处理主要赋空 diff 或默认值，并非“7 处 `except Exception: pass`”。可与 `capture_task_baselines` 共同收敛命令执行重复，但必须保留各命令退出码、超时、回退和文件分类的差异。
- **共性**：重构以保持行为为目标，不代表修改天然无风险。静默降级可能令 diff 不完整；若要新增失败标记、告警或阻止任务完成，应作为单独的行为变更评估和验收，避免与函数拆分混做。

### P1-3 核心判定需要更多可独立运行的定向测试

- **现状纠正**：selfcheck 已覆盖 guard、契约、状态机与门禁。`quiet()` 在进程内调用 `main()`，`guard()` 直接调用 `hook_pre_tool()`，也有直接调用 `task_contract_errors()` 的断言；不能说全靠子进程端到端测试。另有 `test_kept_for_bump.py` 脚本，但当前 `flow new f` 调用缺少必填 `--desc`，接入回归入口前需修正。
- **真实缺口**：不少安全判定依赖大型 selfcheck 的共享状态，缺少独立夹具和单独运行入口。`hook_pre_tool()` 会读文件、写会话标记并退出，`task_contract_errors()` 的调用链会读解冻记录和文件哈希，两者都不是现成纯函数。
- **优先补充**：围绕冻结/越权/路径判定、契约绑定与正文漂移、依赖满足条件补可独立运行的测试。CLI 通过“依赖必须已存在、不能自依赖、没有修改已有 deps 的入口”维持无环，selfcheck 已覆盖这一前提；不能声称 `wb_core` 已有一个缺单测的通用环检测函数。独立测试的首要收益是隔离与定位，性能收益需测量，不承诺“快几个数量级”。

### P1-4 单文件职责过载，有现成拆分边界

- **位置**：`wb_core.py`（1772 行）、`wb_cli.py`
- **问题**：wb_core 把「状态内核/锁」「门禁引擎」（~990-1308）「调度」「仓库地图与 index/notes 校验」（source_projects/source_repos/read_repos_manifest/repo_claims/`_index_*`/repo_index_issues/repo_note_issues/repo_layout_scopes/unclaimed_repos/ensure_repo_orientation_tasks，约 1385-1740 共 ~350 行）四块揉在一起；wb_cli 里 `merge_artifacts`/`capture_task_baselines`/`record_task_diff`（361-662，~300 行）是任务 diff/基线逻辑，不属 CLI 解析层。
- **影响**：模块边界与注释（`# 门禁引擎`、`# 仓库地图与索引`、`# CLI 命令实现`）已画好但没落成文件；越大越难定位、越容易让无关改动互相牵连。
- **改法**：先在原模块内抽清函数边界，有明确维护收益后再考虑 `wb_gate.py`、`wb_repos.py`、`wb_taskdiff.py`。不建议把 task-diff 简单塞回已经很大的 core。模块移动需检查循环 import、入口加载和分发完整性，并保留 selfcheck 静态布局检查；不能承诺“纯移动即可零风险”。

### P1-5 过长 / 深嵌套函数（可维护性）

- `hook_pre_tool`（`wb_guard.py:945-1151`）：可考虑抽出 SHELL 处理，但必须保留归属、特权命令、冻结与任务范围检查的执行顺序。
- `_check_write_target`（`:576-721`）、`privileged_wb_calls`（`:796-871`）：已有静态规则表；依赖 owner、参数和状态的分支不应仅因长度而表格化，优先补行为测试。
- `hook_subagent_stop`（`:1287-1361`）：跨 flow 的 kept/closed 处理值得保留定向回归，是否拆函数取决于夹具和共享状态能否清楚分离。
- 长提示只有在重复且语义一致时才抽共享模板；一次性提示留在拒绝分支旁，有利于核对条件和修复指引。

---

## 性能 / 去重与小缺陷（沿用原 P2 编号）

### P2-1 条件路径存在重复 flow 状态扫描，收益待测量

- **位置**：`wb_guard.py:298-320`（`unlocked_paths`）、`323-339`（`contracts_for`）、`wb_core.py:561-581`（`kept_for_bump_paths`）
- **问题**：三处都存在「遍历 `all_flows` → 读每个 state.json → 走 contracts」的逻辑，但触发条件不同：`unlocked_paths` 无窗口时直接返回；`_check_write_target` 仅在命中冻结路径时调用它，Bash 还有一次整条命令级调用。`contracts_for` 主要在拒绝路径调用，`kept_for_bump_paths` 无保留窗口时也直接返回。
- **影响**：确有可优化的重复 I/O，最值得先看的是冻结检查中的多目标命令；当前证据不足以把三处都称为同等热路径问题。
- **改法**：先复用现有 `read_state_raw` 消除适合共用的裸读取，再测多 flow、多写入目标且有解冻窗口的场景。确认重复计算值得优化后，才增加单次 hook 范围缓存；保留 `enable_memo` 的清空/关闭边界，不引入跨请求缓存。

### P2-2 多处裸 `json.loads(...read_text())` 绕过 `read_state_raw`

- **位置**：`wb_cli.py:1335/1362/1396`、`wb_core.py:574/1764`（≥5 处）
- **问题**：已有 `read_state_raw`（`wb_core.py:449`）支持只读容错和按作用域 memo，但这些调用点各自读取和捕获错误。错误处理不同有时是必要的：展示摘要可降级，切 flow 失败则应报错。CLI 默认未开启 memo，单纯替换读取函数不会自动减少 CLI 的 I/O。
- **改法**：优先统一只读、容错语义一致的调用点；`flow list`、`flow switch` 等需要向用户报告损坏 state 的命令不能直接改成静默返回 `{}`，应保留各自的错误提示。

### P2-3 未配置门禁推导在两处重复

- **位置**：`web/wb_dashboard_core.py:314-324` 与 `.claude/hooks/wb_cli.py:161-167`
- **问题**：同一段 `GATES.values()→checks→cmd:` 推导 + waiver 过滤复制两份；`GATES` 结构或 waiver 语义一改，看板显示与 `wb.py status` 口径会悄悄漂移。
- **改法**：抽成 `wb_core` 的 `unconfigured_gates(state)`，两处调用（`wb_dashboard_core` 已依赖 `wb_core`，零新增耦合）。

### P2-4 CLI 只读命令内重复文件系统扫描

- **位置**：`cmd_status`（`wb_cli.py:131-227`）、`cmd_role scopes`（`1284-1325`）
- **问题**：一次 `status` 里 `source_projects`、`repo_claims`（内部又调 `source_projects`）、`repo_index_issues`/`repo_note_issues`（各调 `source_repos`）各自重新 `iterdir()` 扫 `repos/.source/`；`contract_drift`（210）又对每份契约全量 `sha256_file`。CLI 路径没开 memo。
- **影响**：目录扫描确有重复，但本段调用链不足以证明同一契约在一次 status 中被重复哈希；检查正文是否漂移本身就需要当前哈希，不能直接算作浪费。总体收益尚未测量。
- **改法**：有测量依据后优先在单次命令中复用目录枚举结果。若扩展 memo，限定在该命令的只读作用域并可靠清理，避免影响同进程后续的状态变更或看板请求。

### P2-5 明确小缺陷与需要保留边界的建议

- `wb_cli.py:413`：`capture_task_baselines(...) -> dict[str, Any]` 用了 `Any` 但未 `from typing import Any`，靠 `from __future__ import annotations` 把注解当字符串才没崩；任何 `get_type_hints()`/运行期注解求值会 `NameError`。→ 补 import 或把返回注解改成 `dict`。
- `web/dashboard_template.html:3817`（`codespan`）、`:3821`（`table`）：撤回“直接补 `escapeHtml`”的建议。当前 vendored Marked v12.0.2 传给 `codespan` 的字符串已经转义；本次 Node 检查中，反引号内的 `<img src=x>` 对应参数为 `&lt;img src=x&gt;`，再次转义会改变显示。table 接收的也是渲染片段，不能当普通文本整体转义。保留外层 `DOMPurify.sanitize`，升级 renderer API 时补输出与净化测试；当前证据不能推出新的 XSS，也不能证明所有输出均无漏洞。
- `scripts/repos_tui.py:74`：`entry_name` 用 `derive_name(e.get("remote",""))`，对 link 条目不回退到 `link` 派生名，与 `repos_apply.validate_entries`/`materialize` 的 `derive_name(remote or link)` 口径不一致，列表显示名可能与落地名不符。→ `entry_name` 也传 `remote or link`。
- `wb_guard.py:295` 的 `frozen_hits` 是命令文本匹配，`:1094` 另有 cd 兜底；但完整链路还包含 Bash 目标解析、逐目标检查与嵌套根冻结检查。不能只看该正则就断言所有嵌套 cd 都漏拦。若要认定漏洞，需提供具体命令和完整 hook 判定的反例。
- `wb_guard.py:612`、`:475` 与 `_guarded_prefix:448` 存在相似的路径前缀处理，可在语义一致时去重；`frozen_hits` 比较的是命令文本子串，不适合一并替换成 `path_under(rel, entry)`。先区分路径、范围模式和命令文本，避免统一函数改变匹配行为。

---

## 已有合理设计与保留建议

- **guard 无明显的静态未引用函数**：逐个 grep `wb_guard.py` 顶层函数，当前都能找到至少一个调用点；这不能证明不存在动态不可达分支或语义死代码。
- **拦截提示可操作**：多处提示区分权限拒绝与契约停工，并给出实名契约及处理命令；优先保留这些信息，是否抽模板按重复程度决定。
- **dashboard 路径与静态导出边界处理较好**：`safe_resolve_path`（`wb_dashboard_core.py:85`）用 `is_relative_to` 判边界、`validate_flow_name`（`:113`）拒绝路径穿越、vendor 路由（`wb_dashboard.py:333`）也校验解析后的目录边界。JSON 注入处实际使用 `.replace("<", r"\u003c")`（`:274,667`），将 `<` 写成转义序列，防止数据提前闭合 script 标签；初稿的 `r"<"` 是错误抄录。未开放 CORS 是合理的同源默认值，但这些措施不代表服务具有认证或适合对外暴露。
- **scripts 基本无明显重复**：`repos_tui.py:32` 已复用 `repos_apply.py` 的 `derive_name/derive_project/load_config/validate_entries`（单一来源）；`generate_agents.py`、`knowledge_check.py` 职责独立。但 `repos_tui.entry_name()` 仍有 link 条目显示口径问题，复用函数不等于调用参数已经统一。
- **模板全内联是刻意设计**：`dashboard_template.html` 4343 行（CSS≈2060、JS≈1880）全内联，是为「静态单文件离线导出」（`export_static_dashboard` 把 vendor 一并内联，`wb_dashboard.py:675`），**不建议强拆**。可选优化：把内联 CSS/JS 拆成 `web/` 下独立文件，live 模式按 `/vendor` 式路由引用、仅 export 时内联——优先级低，属权衡项。
- **轻微占位 API 可接受**：`StateWatcher.notify`（`wb_dashboard.py:155`）仅测试调用、`DashboardServer.server_close`（`:516`）空实现——均有注释说明，不建议改。

---

## 工程现状（非代码单点，但值得补）

- **当前仓库未发现 CI 配置**：未核实外部托管平台是否另有流水线。本地 `.githooks/pre-commit` 需要启用，并且仅匹配部分暂存路径运行 selfcheck，单改 `web/` 不触发它；仓库已有 Web 测试，不应称 selfcheck 是唯一测试。
- **CI 接入需要独立测试数据**：可以先接入 selfcheck，但部分 `web/tests/` 用固定 `ROOT` 读取本地 `.workbench`，并断言存在任务或 T1。接入干净 checkout 前应准备明确的临时工作台夹具，避免依赖开发者状态；Node 相关测试也需安排对应运行环境。不能直接承诺“加一条 CI 命令即可完整运行现有测试”。
- **直接依赖较少**：`requirements.txt` 仅声明 fastapi + uvicorn；这说明直接依赖面不大，不等于完整解析后的依赖树已经审计干净。
- **门禁配置需补齐**：本次 `status` 显示当前 `task-write-scope-guard` flow 的 test/lint/build 未配置。应分别配置适用命令或明确豁免理由；一次手动 selfcheck 通过不代表这些门禁已经建立。

## 本次复核的证据与限制

- 2026-10-06 复核期间执行 `python3 .claude/hooks/wb.py selfcheck`，动态链路与静态布局检查均通过。此结果证明现有断言通过，不证明上述缺陷不存在；本轮文档更正没有改动内核实现。
- 定向检查确认了敏感路径匹配差异、`get_type_hints(capture_task_baselines)` 的 `Any` 名称错误、link 条目的 TUI 显示名差异，以及当前 Marked 对 codespan 参数的预转义行为。
- 归属失败复现实验的临时初始化命令被当前 `submitter` 角色守卫拒绝，未绕过或调整权限继续；该条依据是源码执行顺序及现有注释，不能记为已完成端到端复现。
- 未重新运行全部 Web 测试，未完成浏览器 DNS rebinding 实验，也未对所列性能优化做基准测量。

---

## 建议落地顺序

1. **处理已明确的权限与暴露风险**：先修 P0-2 的 role 原子写；若 dashboard 已对不可信网络开放，则优先处理 P0-3 的访问控制，Host 校验与认证分别验收。
2. **确认边界后收紧规则**：P0-1 先确认命令成功证据与跨端 hook 载荷，P0-4 明确敏感路径覆盖及误拦边界，再补正反例并实现。
3. **建立稳定回归入口**：接入 selfcheck，修正独立回归脚本的过时参数，并为 Web 测试准备独立状态夹具；同步补齐适用的工作台门禁。
4. **逐步改善可测性与结构**：先做 P1-3 的独立定向测试，再小步拆 P1-1 的共享场景；P1-2/P1-4/P1-5 只处理有明确收益的边界，不批量机械拆文件。
5. **处理明确小缺陷，按数据决定优化**：修 `Any`、link 显示名和必要的门禁口径重复；P2 的缓存/扫描优化等待测量，撤回没有证据支持的 renderer 转义和统一路径匹配建议。

> 本文是审查结论与候选改进顺序，不是已批准的实现方案。函数拆分以保持行为为目标；归属条件、读取规则、认证及 diff 失败策略的调整要单独验收。后续按工作台约定判断 flow 归属并推进，不能把“等价重构”作为跳过契约、任务与门禁的理由。

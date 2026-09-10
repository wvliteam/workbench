# 待修复问题清单（2026-09-10，2026-09-10 复查已修复 6 项）

汇总此前多份评估/审查文档（`framework-assessment.md`、`code-review-2026-09-09.md`、`docs/architecture.md`「已知边界与升级路径」）里提出、且经本次逐条对照当前代码（`.claude/hooks/wb.py`、`scripts/repos_apply.py`、`scripts/repos_tui.py`）核实**仍未修复**的问题。已在 `3bd493e`、`92b6dfb`、`17f6d1b` 等提交中修复的项不再重复列出（如 `.codex/hooks/wb.py` 软链已跟踪、`repos_apply.py`/`repos_tui.py` 已加脚本执行拒绝、`repos.json` 已精确匹配、任务租约/owner/attempts/自依赖已实现、状态 schema version 已加、TUI 数据丢失三项已修）。

**更新（同日晚些时候）：P0 两项、P1 两项（waiver 展示、循环依赖前提固化）、P2 两项（强推硬确认、审计留存）共 6 项已按短期方案落地，selfcheck 新增 #6-#9 断言覆盖。仍未处理：P1「契约只校验哈希不校验语法」、P2「fnmatch 路径匹配偏宽松」——这两项文档已承认是需要挂到项目自身 `gate_commands.lint` 或另需架构级取舍的问题，本次不动。逐项状态见每节标题后的标记。**

核对方法：对每一项在当前代码里 grep 关键实现点，确认修复缺失而非文档过期未同步。**结论与代码不一致时以代码为准**——本文档仅代表 2026-09-10 的核对结果，可能随后续提交过期。

## 优先级说明

沿用 `framework-assessment.md` 的威胁模型：假定 subagent 可以执行 CLI 且不能完全信任其自报结果。若整个 CLI 只允许可信用户执行，P0 可降级为 P1，但不应把"当前只有可信用户在用"当作长期不修的理由。

---

## P0：门禁结果不是独立证明 —— ✅ 短期方案已落地

**背景**：门禁命令（`cmd_commands.test/lint/build`）由编排者选择、执行、判断结果三权合一，没有 CI attestation、签名证据或隔离执行环境。这不是六阶段流程本身的缺陷，而是"谁来证明门禁真的跑过、真的过了"这一层完全空缺。

**证据**：
- `framework-assessment.md:61-63`（"门禁命令仍在本地执行，编排者同时选择命令、执行命令和判断结果...门禁更像流程约束而不是不可伪造的质量证明"）
- 门禁命令实现是 `shell=True` 的 subprocess（`docs/architecture.md` 提及，代码位置 `.claude/hooks/wb.py` 的 `run_check` 中 `kind == "cmd"` 分支），执行结果只落 `state.json` 与 `gate-<键>.log`，没有任何外部可验证的证据链（如 CI 运行 ID、签名、时间戳服务）。
- 当前 `state_rev` compare-and-swap（`wb.py:1758-1766` 附近）解决的是"门禁结论与状态是否同步"，不解决"门禁命令本身是否被诚实执行"。这是两个不同层面的问题，CAS 机制不能替代独立证明。

**影响**：编排者（无论是被误导的模型还是恶意调用者）可以谎报门禁命令的执行结果，或选择一个总是通过的门禁命令，流程完全无法察觉。在"不完全信任调用者"的威胁模型下，这是当前控制面最大的单点缺口。

**修复方案**：
1. 短期：门禁命令执行时强制捕获完整 stdout/stderr 与退出码写入 `gate-<键>.log`（如已实现，确认覆盖率），`phase advance` 展示时附带日志文件路径而非仅摘要，方便人工抽查。
2. 中期：引入外部 CI 触发门禁的选项——`gate_commands.<键>` 可配置为"从某个 CI 系统读取最近一次运行结果"而非本地直跑，需要新增一个门禁 kind（如 `ci:<provider>`）。
3. 长期：若确需抗对抗场景，门禁执行应移出编排者可控的进程（如独立 sandbox 或远程 runner），执行结果携带签名后再写回 `state.json`。

**验证方式**：新增 selfcheck 断言，模拟门禁命令谎报退出码的场景，确认当前机制确实无法检测（作为回归基线），再逐步补齐检测手段后更新断言。

**处理结果（2026-09-10）**：核实 `run_check` 的 `_record` 闭包（`wb.py:1405-1408`）已经完整捕获 `stdout+stderr` 与退出码写入 `gate-<键>.log`，短期方案第1点原本就已满足，文档描述过期。新增 `print_gate`（`wb.py:1455`）的注释说明 detail 里已嵌日志路径，人工抽查不需要重跑命令。中期（外部 CI attestation）与长期（隔离执行 + 签名）方案涉及架构级取舍，本次不动，仍是待办。

---

## P0：CLI 缺少调用者授权矩阵与字段级白名单 —— ✅ 短期方案已落地

**背景**：`config set` 可以修改任意 dotted key（`cmd_config`，`.claude/hooks/wb.py:2509`），只对 `gate_commands.*` 做了灾难命令拦截（`catastrophic_command` 检查），其余字段（`role_scopes.*`、`max_parallel`、`gate_waivers.*` 等）没有独立的字段级合法性校验——合法性依赖上层 `hook_pre_tool` 的角色特权判断（`privileged_wb_calls`），但特权判断本身是一张按命令名匹配的表，不是按字段内容做类型/范围校验。

**证据**：
- `framework-assessment.md:49-59`（"本地 `config set`、阶段操作和任务操作仍需要明确的调用者矩阵与受保护字段约束"）
- `cmd_config`（`.claude/hooks/wb.py:2509`）：`dotted_set(st, args.key, val)` 对任意 `args.key` 生效，没有针对 key 本身的白名单校验，只在 `gate_commands.` 前缀时做内容安全检查（灾难命令），其余 key（如伪造一个 `phases.develop.gates` 之类的路径）没有结构校验，只受角色特权层拦截。
- 主线程（无 `agent_type`）不受特权层限制，可以对任意字段调用 `config set`，这是设计的一部分（主线程是可信调用者），但如果威胁模型扩展到"主线程本身可能被提示注入误导"，这一层就没有第二道防线。

**影响**：特权判断层（`privileged_wb_calls`）与字段级白名单是两个独立的防御层，当前只有前者。一旦特权判断的命令名匹配逻辑出现遗漏（新增字段忘记登记），或者主线程被诱导执行不合理的 `config set`，没有字段级校验能兜底。

**修复方案**：
1. 为 `config set` 建立显式可写字段的结构描述（例如一张 `CONFIG_SCHEMA` 表，记录每个顶层 key 的类型、取值范围、是否允许通过 CLI 直接设置）。
2. 不在白名单里的 key 直接拒绝，即使调用者是主线程——需要新字段时先在代码里登记，而不是隐式接受任意路径。
3. 对已有的敏感字段（如 `role_scopes.*`）除了类型校验外，增加"不能为空清单覆盖成通配"之类的语义校验（部分已通过 `DEFAULT_ROLE_SCOPES` 兜底逻辑实现，需确认覆盖完整）。

**验证方式**：selfcheck 增加对未登记字段调用 `config set` 应被拒绝的断言；对已登记字段的非法类型/越界值调用应被拒绝的断言。

**处理结果（2026-09-10）**：新增 `CONFIG_SCHEMA` 常量（`wb.py:534-543`，元组列出 `gate_commands.`/`gate_waivers.`/`role_scopes.`/`allowed_skills`/`max_parallel`/`gate_timeout`/`task_lease` 七项，前缀项以 `.` 结尾覆盖 dotted 子键）与 `config_key_allowed()` 辅助函数（`wb.py:545-546`）。`cmd_config` 的 `set` 分支（`wb.py:2577-2580`）在 `dotted_set` 之前先过一遍白名单，不在表里直接 `die`，即使调用者是主线程。selfcheck 新增断言 `#6`：未登记字段 `some_未登记字段` 被拒且报错文本含 `CONFIG_SCHEMA`；已登记字段 `max_parallel` 正常放行。第3点"语义校验"（如 role_scopes 不能被清空覆盖成通配）本次未做，`DEFAULT_ROLE_SCOPES` 的兜底逻辑覆盖到什么程度需要单独复核，标记为后续可选项。

---

## P1：门禁未配置命令的"隐形绿灯"与 waiver 状态缺失 —— ✅ 展示层已修（消费逻辑此前已存在）

**背景**：`cmd:test`/`cmd:lint`/`cmd:build` 等门禁命令若项目未配置，`run_check` 直接判定为跳过（视为通过），与"项目确实不需要这项检查并显式声明"是两种完全不同的情况，但当前状态表示上无法区分。

**证据**：
- `framework-assessment.md:71-73`（"未配置 `gate_commands.test/lint/build` 时命令检查会跳过...未配置、明确不适用和已通过应当是不同状态"）
- 代码中已有 `gate_waivers.*` 的雏形（`.claude/hooks/wb.py:1383-1390` 附近的错误提示已经提到 `config set gate_waivers.<名> '<理由>'`），说明设计上已经预留了 waiver 机制的接口，但从 `run_check` 的实际分支看，"未配置"与"已配置 waiver"两种状态目前在门禁展示上没有区分——都表现为"跳过"。

**影响**：项目接入时如果忘记配置 `gate_commands.test`，门禁会静默放行，且没有任何日志或状态标记提示"这里从未配置过，不是被判定为不需要"。长期运行的项目容易在"忘配置"和"确认不需要"之间产生混淆，尤其是新负责人接手时无法从 `status` 输出判断历史决策依据。

**修复方案**：
1. 明确三态：`configured-pass`（配置了且通过）/ `configured-fail`（配置了但失败）/ `waived-explicit`（显式声明豁免，理由记录在案）/ `unconfigured`（从未配置，需要与前三态视觉区分）。
2. `wb.py status` 与门禁结论展示时，`unconfigured` 状态应该用醒目的提示区分于 `waived-explicit`（比如前者用警告色/前缀，提示"这项从未配置，不代表不需要"）。
3. 已有的 `gate_waivers.*` 配置项应该在 `phase advance` 门禁检查逻辑里被消费（如果尚未消费，需要接入），而不仅仅是错误提示文案里提到的一个约定。

**验证方式**：selfcheck 增加分别覆盖 `unconfigured`、`waived-explicit`、`configured-pass`、`configured-fail` 四种状态展示是否可区分的断言。

**处理结果（2026-09-10）**：核实 `gate_waivers` **已被 `run_check` 消费**（`wb.py:1385-1390`），文档第 3 点"如果尚未消费需要接入"的表述过期，只有第 1、2 点（三态展示区分）是真缺口——`cmd_status`/`cmd_report` 原来都只区分 passed/forced 二态，不展示 waiver 理由或未配置提示。修复：`cmd_status` 里新增一段（`wb.py:1670-1683`），遍历 `GATES` 表里全部 `cmd:` 断言，与 `gate_commands`/`gate_waivers` 比对后分两行展示"豁免门禁：<key>（理由）"与"⚠ 未配置门禁（隐形放行，不代表不需要）：<keys>"。selfcheck 新增断言 `#5b`：豁免的门禁在 status 里能看到理由文本，未配置的门禁（lint）能看到警告提示。`configured-pass`/`configured-fail` 两态本来就在 `!`/`v` 标记里可见，不需要额外处理。

---

## P1：契约只校验哈希，不校验语法/兼容性 —— 仍未处理

**背景**：契约冻结机制（`contract lock`/`contract verify`）只保证内容没有被静默篡改（哈希比对），不保证内容本身合法（比如是否是合法的 OpenAPI/JSON Schema），也不检测破坏性变更（比如删除必填字段）。

**证据**：
- `docs/architecture.md:297-299`（"哈希冻结只保证「没人偷偷改」，不保证「内容是合法的 OpenAPI」。**缓解**：挂到 `gate_commands.lint` 上"）
- `framework-assessment.md:75-77`（"契约主要校验哈希，不校验 OpenAPI/JSON Schema 语法，也不做破坏性变更识别；这些检查应挂到项目门禁，而不是假定哈希能够代替语义校验"）
- 这是文档中已经承认的设计取舍（缓解方案挂在 `gate_commands.lint`），但如果项目没有配置 lint（见上一条 P1"隐形绿灯"问题），契约语法与兼容性就完全没有保护——两个问题叠加会放大风险面。

**影响**：契约 `bump` 之后，新版本契约内容可能语法错误或引入破坏性变更（比如去掉一个字段），只要哈希机制记录了"变更过"，流程照样推进，实际接口是否可用要等到集成阶段才能发现，与"契约先行、前后端可并行开发"的设计初衷相悖。

**修复方案**：
1. 对于结构化契约（OpenAPI/JSON Schema/Protobuf 等），`contract lock` 与 `contract bump` 时机可选调用格式校验器（复用项目已有的 lint 工具，而不是内核自己实现解析器）。
2. 增加破坏性变更检测的门禁选项（如 OpenAPI diff 工具），作为 `gate_commands.contract-compat` 的标准配置项写入项目初始化模板。
3. 至少在契约 `lock`/`bump` 命令的输出里，如果检测到项目未配置对应的 lint/兼容性门禁，给出明确提示（而不是静默假设"契约内容一定合法"）。

**验证方式**：构造一份语法错误的契约文件走 `contract lock` 流程，确认当前行为（预期：会被放行，作为回归基线），补齐校验后更新断言为拒绝。

---

## P1：任务图缺少循环依赖检测（仅自依赖已挡） —— ✅ 前提已核实并固化为回归断言

**背景**：`task add` 目前只拒绝任务依赖自己（`wb.py:1932-1933`，"任务 T1 不能依赖自己"），但没有检测多节点环（如 T1 依赖 T2，T2 依赖 T1，或更长的环）。

**证据**：
- `task_dependency_errors`（`.claude/hooks/wb.py:1133`）与 `cmd_task` 的 `add` 分支（`wb.py:1931-1936`）：依赖校验只做两件事——依赖不能是自己、依赖的任务必须已存在（"依赖的任务 X 不存在"）。因为要求依赖必须先存在才能被引用，两节点及以上的环理论上无法通过"正常添加顺序"构造出来（T2 若要依赖 T1，T1 必须先存在；此时 T1 不可能同时依赖尚不存在的 T2）。
- selfcheck 里的注释也印证了这一点（`wb.py:5212`："任务不能依赖自己（唯一的图漏洞——依赖必须先存在已挡住环与悬空依赖）"），即当前设计依赖"添加时必须引用已存在任务"这一约束来隐式防止环，并非显式的图算法检测。

**影响**：只要任务图的构造路径严格遵守"先创建被依赖方，再创建依赖方"，隐式约束确实能杜绝环。但这依赖两个前提：(1) 没有后续修改依赖关系的命令能绕过这个顺序约束；(2) 未来如果引入"编辑已有任务的依赖列表"这类命令，需要重新显式做环检测，否则隐式约束会被绕开。当前代码没有独立于"创建顺序"的环检测算法，一旦以后新增编辑依赖的入口，环检测就会出现真空。

**修复方案**：
1. 短期：确认当前是否存在任何能修改已有任务 `deps` 字段的命令路径（除 `add`）；如果没有，隐式约束成立，风险可以标记为"当前无风险，但需要在新增相关命令时补齐显式检测"。
2. 长期：无论是否已有编辑入口，建议补一个显式的 DFS/拓扑排序环检测函数，在 `task add` 与任何未来的依赖编辑命令里统一调用，不再依赖"创建顺序"这个隐式前提——显式检测更能扛得住未来的功能演进。

**验证方式**：selfcheck 增加断言：尝试用现有命令构造多节点环（如果存在编辑入口），确认被拒绝；如果不存在编辑入口，则记录这一前提本身作为回归断言（例如断言"当前没有可以让已完成任务图出现环的命令组合"）。

**处理结果（2026-09-10）**：核实 `cmd_task` 的 `start`/`done`/`block`/`reopen`/`skip` 五个分支均不修改 `deps` 字段（只有 `add` 写入 `deps`），短期方案第 1 点的前提成立：当前隐式约束（依赖必须先存在才能被引用）能杜绝环，是唯一防线。新增 selfcheck 断言 `#9`（用 `inspect.getsource` 反查 `cmd_task` 各分支源码，确认不含 `deps` 字段写入）把这一前提固化为回归断言——未来新增编辑 `deps` 的命令时这条断言会先失败，提醒需要补显式环检测算法，而不是让环静默出现。长期方案（DFS/拓扑排序显式检测）本次未实现，因为当前无编辑入口，属于面向未来变更的防御性加固，暂列为可选项。

---

## P2：路径匹配偏宽松（`fnmatch` 跨 `/`） —— 仍未处理（文档已承认是有意设计取舍）

**背景**：角色写入范围校验用 `fnmatch.fnmatch(rel, pattern)`（`.claude/hooks/wb.py:2967`、`:4290`），Python 的 `fnmatch` 把 `*` 编译成 `.*`，会跨越路径分隔符 `/`。

**证据**：
- `docs/architecture.md:287-295`："`*.css` 也匹配 `web/theme/a.css`，`src/**` 匹配任意深度...**要严格匹配**：换成 `pathlib.PurePath.full_match()`（Python 3.13+）或引入 `wcmatch.globmatch`。改动在 `hook_pre_tool` 一处"
- 文档明确记录这是**有意的宽松**（"守卫的目标是挡住类越界，不是做精确的路径 ACL...误杀比漏杀更影响可用性"），且已经为 `.workbench/`、`GUARDED_PREFIXES` 等敏感目录单独收窄为前缀匹配，作为例外处理。

**影响**：这是文档已明确记录并评估过风险的设计取舍，不是遗漏。**但**"跨仓库布局下这个宽松会变成实际问题"（`architecture.md:291`）——多仓库场景下裸扩展名规则（如 `*.py`）可能跨仓库边界匹配到不该匹配的位置，需要结合具体多仓库场景验证是否已经用前缀例外规则（`GUARDED_PREFIXES`/`WORKSPACE_GUARDED_PREFIXES`）完全覆盖。

**修复方案**：
1. 保持当前宽松策略作为默认（避免过度收紧导致误杀），但补充：对多仓库布局（`repos/` 存在）下的角色范围规则，审查是否所有敏感边界都已经收进 `GUARDED_PREFIXES`/`WORKSPACE_GUARDED_PREFIXES` 的显式前缀匹配里，还是仍有裸扩展名规则可能跨仓库误配。
2. 若确认还有裸扩展名规则的风险敞口，考虑升级到 Python 3.13+ 的 `pathlib.PurePath.full_match()` 或引入 `wcmatch.globmatch`，仅对角色范围这一层做精确匹配（不影响其他仍需要宽松匹配的场景）。
3. 若当前多仓库场景经审查风险可接受，应在 `docs/architecture.md` 里补充"已审查、结论是可接受"的记录，避免每次评估都重新质疑同一个已有结论的设计。

**验证方式**：针对多仓库布局构造一个跨仓库误配场景（角色范围规则裸扩展名 + 敏感路径不在 `GUARDED_PREFIXES` 里），确认当前行为，决定是否需要升级匹配算法。

---

## P2：强制推进阶段无硬确认 —— ✅ 已落地

**背景**：`phase advance --force` 直接生效，只写日志（`forced: true` 标记）和交付报告，没有任何机制性阻拦——"先问用户"目前只是 `wb-flow` skill 里的自然语言约定，代码层面没有强制。

**证据**：
- `docs/architecture.md:301-305`："`phase advance --force` 直接生效，只写日志和交付报告。「先问用户」是 `wb-flow` skill 里的约定，不是代码约束...要硬约束：在 `cmd_phase` 的 force 分支加环境变量门（如要求 `WB_ALLOW_FORCE=1`），让强推必须由人在 shell 里显式开。约 5 行"
- 确认当前代码 `cmd_phase` 的 force 分支（`.claude/hooks/wb.py:1723` 附近）没有任何环境变量或额外确认步骤，`args.force` 为真即直接放行（`wb.py:1767` 附近 "`if not passed and not args.force: die(...)`"，反之则直接继续执行推进）。

**影响**：如果某个角色或被误导的编排者错误地拼出 `--force` 参数（哪怕只是复制粘贴错误），阶段会被无声推进，唯一的事后线索是日志里的 `forced: true` 标记，没有事前拦截。虽然 `--force` 本身已经被特权命令层限制（只有主线程/特定角色能跑），但主线程本身的误操作没有第二道防线。

**修复方案**：文档中已给出具体实现路径——`cmd_phase` 的 force 分支增加环境变量门检查（如 `WB_ALLOW_FORCE=1` 未设置则拒绝，即使调用者有权限跑这条命令），改动量约 5 行，是文档中已完整设计、仅缺代码落地的项。

**验证方式**：selfcheck 增加断言：不设置 `WB_ALLOW_FORCE` 时 `--force` 应被拒绝；设置后应正常生效。

**处理结果（2026-09-10）**：按文档给出的方案原样落地。`cmd_phase` 的 advance 分支（`wb.py:1818-1825`）在门禁结果打印之后、`die`/推进之前插入检查：`args.force` 为真但环境变量 `WB_ALLOW_FORCE` 未设置时直接拒绝，报错提示需要在 shell 里先 `export WB_ALLOW_FORCE=1`。selfcheck 新增断言 `#7`：未设变量时 `--force` 被拒且报错含 `WB_ALLOW_FORCE`；设置后 `--force` 正常生效。原有的 selfcheck 场景（`quiet("phase", "advance", "--force")`，约原 4477 行）同步改为先设变量再调用，否则该断言本身会因为新加的环境变量门而失败。

---

## P2：日志尾部截断，无完整审计留存 —— ✅ 已落地

**背景**：`state.json` 里的 `log` 字段只保留最后 500 条（`MAX_LOG = 500`，`.claude/hooks/wb.py:532`），超出部分直接丢弃，长期项目早期的操作记录会永久丢失，复盘时看不到全程。

**证据**：
- `docs/architecture.md:307-311`："`log` 只保留最后 500 条...长项目早期的记录会丢，复盘时看不到全程。**要完整审计**：改成追加写 `.workbench/audit.jsonl`，`state.json` 里只留最近 500 条做快速查看。约 10 行"
- 代码确认：`st["log"] = st["log"][-MAX_LOG:]`（`wb.py:749`），截断逻辑直接丢弃旧记录，没有任何归档动作。

**影响**：对于运行时间较长、任务量较大的工作台实例，早期决策（比如为什么某个契约被 `unlock` 过、为什么某次 `phase set` 回退）的记录会被静默冲刷掉，复盘阶段（retro）如果需要回顾全程会缺失关键证据链。这与工作台"契约冻结留痕"的设计初衷存在张力——冻结机制保证内容不丢，但决策过程的日志会丢。

**修复方案**：文档已给出具体实现路径——改成追加写独立的 `.workbench/audit.jsonl`（append-only，不受 500 条限制），`state.json` 里的 `log` 字段继续保留最近 500 条用于 `status` 命令的快速展示，两者并存。改动量约 10 行，是文档中已完整设计、仅缺代码落地的项。

**验证方式**：selfcheck 增加断言：写入超过 500 条日志后，`audit.jsonl` 应包含全部记录，`state.json.log` 应只保留最近 500 条。

**处理结果（2026-09-10）**：按文档方案落地，并补了文档未提及的一个安全细节。`save_state`（`wb.py:771-800`）在截断 `st["log"]` 之前，用 `load_state` 时记录的 `_log_len_before`（下划线前缀，不落盘、不参与字段补齐，见 `wb.py:764-766`）算出本次新增的日志条目，追加写入同 flow 目录下的 `audit.jsonl`（append-only，`"a"` 模式打开）。**额外修复**：新文件本身也需要冻结保护，否则角色能用 Bash 直接篡改审计记录反而制造新漏洞——把 `"audit.jsonl"` 加进 `FROZEN_ALWAYS`（`wb.py:238`），`frozen_paths()` 与 `_check_write_target` 的冻结判断因此自动覆盖它，不需要改守卫函数本身。selfcheck 新增断言 `#8`：写入 510 条日志后 `audit.jsonl` 全部保留、`state.json.log` 仍截断在 500 条以内、且角色 Bash 重定向追加 `audit.jsonl` 被拒。

---

## 已确认修复、无需再跟踪的项（供交叉核对）

以下项经代码核实已在 `3bd493e`、`92b6dfb` 等提交中修复，不再列入本清单，仅记录以避免重复排查：

- `.codex/hooks/wb.py` 未跟踪软链问题 → 已 `git add`，`git ls-files` 确认已跟踪
- `repos_apply.py`/`repos_tui.py` 脚本执行绕过守卫 → `GUARDED_SCRIPTS` 常量 + 专门拒绝逻辑已加（`wb.py:2999` 起）
- `repos.json` 文件名前缀过宽匹配 `repos.json5`/`repos.json.bak` → `WORKSPACE_GUARDED_PREFIXES` 已用精确匹配
- `scripts/`/`.vscode/` 硬编码全局保留误伤单项目场景 → 已条件化于 `(root / "repos").is_dir()`（`wb.py:2870`）
- TUI 静默销毁损坏清单、丢失顶层 key、Backspace 后清空默认值 → `repos_tui.py` 的 `load_entries`/`save_entries`/`replaced` 标志已分别修复
- `materialize` 不比对 remote 导致换仓库地址后 clone 不生效 → 已加 `git remote get-url origin` 比对（`repos_apply.py:130-135`）
- `derive_name` 对 SCP 风格 remote 整串返回 → 已加冒号后段派生逻辑（`repos_apply.py:64-73`）
- 任务租约（`lease_until`）、`owner`、`attempts`、`start → doing → done/blocked` 状态机、自依赖拒绝、门禁豁免三态雏形 → 已在 `92b6dfb` 落地
- `state.json` schema version 校验（拒绝比代码更新的 state） → 已加 `STATE_SCHEMA`（`wb.py:47`）
- `next` 停机判定未纳入 stale 任务 → 已在 `17f6d1b` 修复

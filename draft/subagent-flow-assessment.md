# Subagent Flow 设计评估（2026-09-15）

> 对 workbench 的六阶段 subagent 工作流做的一次只读设计评估。评估基于 README、`docs/roles.md`、`docs/scheduling.md`、`docs/gates.md`、`docs/architecture.md`、`docs/contracts.md`、`docs/permissions.md` 与 `.claude/hooks/` 内核代码。行为以 `wb.py` 与 `selfcheck` 断言为准，本文记的是判断与取舍。

## 总体判断

这套 subagents flow 设计**合理且成熟**，成熟度明显高于常见的多 agent 编排。根本正确之处：**没有把流程约束寄托在提示词上，而是落到了退出码和 hook 拦截上**。三条真约束——门禁用退出码挡阶段推进、守卫用 hook 退出码挡越权写入、契约用哈希冻结挡偷改——都不是「大部分时候会遵守」的软提醒。

## 设计上真正合理的地方

1. **按角色而非任务类型划分，是正确的抽象选择。** 只有按角色才能固定三件事：写入范围（守卫可硬编码）、产物路径与格式（下游按固定路径读、门禁按固定章节校验）、交接格式。按任务类型划分则边界随任务漂移，什么都固定不了。（`roles.md:5-13`）

2. **契约先于代码 + 哈希冻结，解决了并行开发的真问题。** 前后端 subagent 上下文隔离，并行最典型的翻车是各自猜接口、联调才发现字段对不上。解法：design 阶段锁定契约 → 哈希冻结 + 文件转只读 → 双方对着同一份冻结定义写 → 偷改被 `contracts_intact` 门禁抓漂移。**靠哈希而不是靠约定**是最漂亮的一笔。

3. **最终验收权收归主线程，是关键防呆。** 所有 developer/architect 禁止自行 `task done`，`verification.md` 由编排者写而非 subagent 自报。直接针对自动化循环最危险的失效模式——乐观确认级联（subagent 报成功 → 标 done → 门禁因 `tasks_done` 通过 → 阶段推进 → verify 才发现前三个都没做完）。

4. **develop 与 verify 门禁分离，测试只在 verify 跑，并有兜底。** develop 只跑 lint+build，`verification.md` 作为「编排者复核过的证据」兜底，防止未配 `gate_commands` 的项目里 develop 四条断言全 PASS、零代码证据推进。（`gates.md:70-72`，`wb_const.py:83-93`）

5. **纵深防御自洽。** 冻结哈希 + PreToolUse 四层 + Bash 独立写入路径也查冻结清单（否则一行 shell 绕过全部）+ 特权子命令身份校验 + 绝大多数已知绕过路径都有对偶的 selfcheck 断言盯着。（`permissions.md:352-367`）

## 值得警惕的薄弱点（按严重程度排序）

### ① 工具层三条防线目前缺失（最实质的缺口）
`permissions.md:131-140` 明确写「非主线程禁用 spawn 类工具（Task/Agent/SendMessage）」与「Skill 审核门」在一次重构（commit `d606944`）中被移除、尚未恢复（P0 记在 `draft/open-issues-2026-09-10.md`）。**当前角色 subagent 理论上仍能 spawn 子 agent 或调未审核 skill**，绕过角色隔离。这不是设计缺陷，是回归缺口，但在恢复前「非主线程不能 spawn」在代码层不成立。

### ② 整套约束的天花板押在编排者纪律上，且无代码强制
「最终验收在主线程」的安全性，完全依赖编排者*真的*重跑验证命令、*真的*不重复派发、*真的*遇七种必停条件停下。这些全是 `wb-flow` skill 里的提示词约定，没有退出码兜底。**门禁能防 subagent 说谎，但防不了编排者偷懒**——这是这类「主线程做裁判」架构的固有代价。

### ③ 路径匹配用 fnmatch，`*` 跨 `/` 导致隔离静默降级
单体项目里 frontend/backend 默认范围都含 `src/**`，实际不隔离；跨仓库布局会「歪成按语言隔离」（后端写不了自己 migrations 却能写别人仓库的同语言文件）。依赖使用者手工按仓库前缀改配置，**改错是静默的**。init 之后必须调 `role_scopes`，「不调是静默出错」。（`roles.md:160`，`architecture.md:180-194`）

### ④ 门禁 `cmd:*` 未配置 = PASS，是全套里最容易静默失效的一环
docs 三处写「有测试就配上」的提醒，但没有任何机制强制项目必须配。「没测试」和「配了但没跑」在门禁看来都是绿灯——依赖人。（`gates.md:74`）

### ⑤ 非角色 agent 会静默丢失范围隔离
general-purpose / Explore / Plan 的 `agent_type` 不在 ROLES 里，守卫退回读单文件兜底，并发下非确定性。用非角色 agent 干开发 = 隐形失去隔离。（`architecture.md:283`）

## 次要观察

- **文档矛盾（小）：** `gates.md:29` 说 retro 门禁查 `改进项/可复用/沉淀` 三节，但 `reviewer.md` 只写查 `改进项/沉淀`，模板里也没有叫「可复用」的章节。实际以 `wb_const.py` 的 GATES 为准，但会误导写 retro 的人，值得对齐。
- **过度设计倾向（轻微）：** 「阶段产物即契约」+ `kind:"artifact"` 特判 + `contracts_locked` 只数接口契约 这条链，为了不让自动登记的产物污染门禁而反复打补丁，分支已相当密集，是可读性负担；功能上自洽。（`gates.md:58`，`contracts.md:53`）
- **环境依赖：** fcntl 缺失时锁退化为 no-op（非 POSIX 环境并发 `task done` 会丢），门禁约束在该环境实际失效但不报错。（`architecture.md:110`）

## 一句话结论

设计思路完全合理，是把「AI 研发流程」真正做成有状态机、有门禁、有契约约束的少数认真实现之一，核心三大约束机制（门禁退出码 / 守卫拦截 / 哈希冻结）自洽且纵深。**主要风险不在设计本身，而在两处：工具层三条防线的回归缺失（待办），以及整套约束对编排者纪律的强依赖是无代码兜底的信任面。**

---

## 问题分级与修复建议

分级轴：**影响面**（失效时波及范围）× **紧急程度**（利用/触发难易 + 是否已有缓解）。综合优先级 P0（尽快）> P1（近期）> P2（有余力）> P3（可选/记录即可）。

| # | 问题 | 影响面 | 紧急度 | 优先级 | 状态 |
|---|---|---|---|---|---|
| ① | 工具层两层防线缺失（skill 白名单 + 非主线程工具管控） | 大 | 高 | **P0** | 待恢复（`d606944` 删除） |
| ④ | 门禁 `cmd:*` 未配置=PASS，无强制 | 大 | 中 | **P1** | 展示层已修，无强制 |
| ③ | fnmatch `*` 跨 `/`，角色隔离静默降级 | 中 | 中 | **P1** | 已承认取舍，未处理 |
| ⑤ | 非角色 agent（general-purpose 等）静默丢隔离 | 中 | 中 | **P1** | 与 ① 同源 |
| ② | 编排者纪律无代码强制（信任面） | 大 | 低 | **P2** | 架构固有代价 |
| — | 契约只校验哈希不校验语法 | 中 | 低 | **P2** | 未处理（挂 lint 缓解） |
| — | fcntl 缺失锁退化 no-op | 中 | 低 | **P2** | 环境相关（本机 Linux 不触发） |
| — | retro 门禁章节文档矛盾（`可复用`） | 小 | 低 | **P3** | 零成本可修 |
| — | 「阶段产物即契约」分支密集（可读性） | 小 | 低 | **P3** | 功能自洽，不动 |

> 威胁模型前提（沿用 `framework-assessment.md`）：假定 subagent 能执行 CLI 且不完全可信。**若该工作台只在完全可信的单人环境使用，① 可从 P0 降为 P1** —— 但不应把「当前只有可信用户」当作长期不修的理由。

### P0 — ① 恢复工具层两层防线

**问题：** `allowed_skills` 白名单与「非主线程工具管控」（`Agent`/`Task`/`SendMessage`/`CronCreate`/`ScheduleWakeup`/`Workflow`/`Artifact`）在 `d606944` 被删。角色 subagent 当前可自行 spawn worker、排定时任务、对外发布、调任意 skill —— 角色隔离在工具维度不成立。

**建议（约 30–50 行，`wb_guard.py`）：**
1. 在 `hook_pre_tool` 增一个工具名维度的判定：`agent_type` 判为角色（非主线程）且工具 ∈ spawn/发布类集合 → 退出码 2 拒绝，话术复用现有「交回主线程」分岔。
2. `Skill` 调用查 `config.allowed_skills`（`CONFIG_SCHEMA` 里已登记该键，基础设施现成），不在白名单则拒。
3. 直接从 `d606944^` 回移对应实现，再按现行边界（受守前缀 / 三态调用者）对齐，与角色范围层的恢复走同一条路径。
4. 补一对 selfcheck 断言：模拟 `agent_type=pm` 调 `Agent` 应被拒、调白名单外 skill 应被拒 —— 防止再次被静默删除。

### P1 — ④ 门禁命令未配置的隐形绿灯

**问题：** 「没测试」和「配了没跑」在门禁看来都是绿灯，无机制强制项目配 `gate_commands.test`。

**建议（低成本，分两步）：**
1. `init` 模板默认写入占位 `gate_commands.test` + 引导注释，把「显式豁免」变成默认动作而非遗忘项。
2. `status` / `gate check` 在 `cmd:*` 处于「纯未配置」（既非通过也非 `gate_waivers` 显式豁免）时打一行醒目 WARN，区别于「已豁免」。**不强制 FAIL** —— 那会卡死确无测试的早期项目；目标是让「隐形」变「显形」。

### P1 — ③ fnmatch 跨 `/` 隔离降级

**问题：** `src/**`、裸 `*.md` 跨目录匹配，单体项目 fe/be 不隔离、跨仓库歪成按语言隔离，改错静默。

**建议（择一）：**
- **低成本：** 强化 `init` 后的引导与 `role scopes --reset` 的跨仓库前缀重算提示，让「必须按仓库前缀改」从文档提醒变成命令输出里的显式 TODO（当前「不调是静默出错」）。
- **中成本（治本）：** 把范围匹配从 `fnmatch` 换成 segment-aware 匹配（`*` 不跨 `/`、`**` 才跨），消除裸扩展名跨目录的整类洞。`architecture.md` 已把它列为架构级取舍，需单独评估对存量配置的兼容影响。

### P1 — ⑤ 非角色 agent 丢隔离

随 ① 一并处理：陌生 `agent_type` 已判 `UNKNOWN_ROLE` 拒写核心路径（`d606944` 恢复部分已做）。**补充约定：** 开发类写操作只派角色 agent，`wb-flow` / `wb-loop` skill 里显式写明「不要用 general-purpose 干 develop」，并在派发协议里点名。

### P2 — ② 编排者信任面

架构固有，无法用退出码根除，只能收窄：
- 关键动作（`task done`、`phase advance`）后由内核输出一行「复核清单」提示，把 skill 里的纪律前移到命令输出。
- 复盘阶段 `reviewer` 已靠 `wb.py log` 的 `forced/task_reopen/task_block` 还原过程 —— 保持每轮记日志的硬要求即是这条的现有防线。

### P3 — 文档矛盾（零成本先修）

对齐 `gates.md:29` 与 `reviewer.md`：以 `wb_const.py` 的 GATES 为准确认 retro 是否真的断言 `可复用` 章节，然后统一两处文档与 reviewer 的产物模板章节名。

## 推荐处理顺序

1. **先修 P3 文档矛盾**（零成本，顺手）。
2. **P0 ①** —— 若威胁模型确认需要（非纯单人可信环境），这是唯一的实质代码缺口，优先恢复。
3. **P1 ④/⑤** —— 低成本的引导层加固，一起做。
4. **P1 ③ 治本版** 与 **P2 ②** —— 需要架构级评估，排到有余力时。

---

## 修复落地记录（2026-09-15）

按推荐顺序执行，`wb.py selfcheck` 全绿，`reviewer.toml` TOML 合法。

| 项 | 状态 | 改动 |
|---|---|---|
| P3 文档矛盾 | ✅ 已修 | `agents/reviewer.{md,toml}`：门禁说明补 `可复用`，`## 做对了什么` 改为 `## 做对了什么（可复用的做法）`，内嵌「别删该子串」提示 |
| P0 ① 工具层两层守卫 | ✅ 已恢复 | `wb_const.py` 加回 `NON_MAIN_THREAD_DENIED_TOOLS`；`wb_guard.py` 加 `load_allowed_skills()` + `hook_pre_tool` 顶部两段判定（非主线程禁用 spawn/发布类工具、Skill 白名单）；`wb_selfcheck.py` 加正负例断言并翻转 `d606944` 留下的「不应拦截」旧断言 |
| P1 ④ 未配置门禁显形 | ✅ 已修 | `wb_core.py` `print_gate`：`cmd:*` 纯未配置渲染为 `[WARN]` 而非 `[PASS]`，与 `已豁免`（仍 PASS）区分；不改 `passed` 逻辑。status 的 `⚠ 未配置门禁` 警告此前已有 |
| P1 ⑤ 只派角色 agent | ✅ 已修 | 代码层由 P0 ① 的 `UNKNOWN_ROLE` + 工具拒绝覆盖；`wb-flow` skill 派发段显式写明「不要用 general-purpose/Explore/Plan 干开发或写产物，只读探查才可」 |
| P2 ② 编排者纪律提示 | ✅ 已覆盖 | `wb-flow` 现有文本已含「命令自己跑一遍」「不要只依据 subagent 自报结果标记完成」，不重复添加 |
| P1 ③ fnmatch 治本 | ⏸ 本轮不做 | segment-aware 匹配是架构级改动，会静默影响存量 `role_scopes` 配置的匹配语义，需单独评估兼容性与迁移。低成本引导（派发只用角色 agent）已随 P1⑤ 落地 |
| 契约不校验语法 / fcntl no-op | ⏸ 本轮不做 | P2，分别靠 `gate_commands.lint` 缓解 / 环境相关（本机 Linux 不触发） |

**改动文件：** `.claude/hooks/{wb_const,wb_core,wb_guard,wb_selfcheck}.py`、`.claude/skills/wb-flow/SKILL.md`、`agents/reviewer.{md,toml}`。Codex 端经 `.codex/hooks/wb.py` 软链 + `resolve()` 复用同一份内核，改动自动生效。

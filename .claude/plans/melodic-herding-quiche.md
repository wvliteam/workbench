# Context

当前工作台的核心目标是让 architect 发布并锁定方案/接口契约后，前后端 subagent 能并行实现；一旦发现契约问题，开发角色只能阻塞并反馈，由主线程或 architect 修改并通知所有消费者。现有 `wbsvr` 通过 Go 服务、专用系统账户、sudoers 和远端存储增加了部署与同步复杂度，不适合个人开发工作台。需要移除整个托管实现，同时把本地方案中最关键的生命周期和版本一致性语义补齐。

目标是：只保留本地 `.workbench`、SHA-256、冻结清单、解锁窗口、`contract bump`、任务调度和 hook；锁定后的契约不能通过 relock 覆盖漂移，bump 必须消费修改前已存在的 unlock；任务绑定契约版本/revision/SHA，契约变更后旧任务变为 stale，开发 agent 在检查、工具边界或 `task done` 时不能静默以旧契约完成。纯本地方案仍不宣称能防止拥有宿主权限的恶意 agent 修改 hook 源码或通过未受信任的外部程序暂时改回文件。

# Recommended Approach

1. **移除 wbsvr 运行时和 CLI 分支**
   - 在 `.claude/hooks/wb.py` 删除 `WBSVRD`、`SVC_USER`、`SEALED_KEYS`、托管缓存、`WbsvrError`、`_svr`、`hosted`、`wc_path`、`sealed_audit`、`hosted_drift`、`hosted_lock_args`、`sealed_payload`、`selfcheck_hosted` 及所有调用点。
   - 将 `status`、`gate_check`、`phase`、`contract`、`main` 异常处理恢复为纯本地逻辑；删除 `--hosted`、`--sealed`，以及仅服务托管契约使用的 `read` / `checkout` / `commit` action。
   - 保留本地 `contract add/list/lock/unlock/verify/bump/impact/dispute`，并让 `doctor` 改为本地诊断：状态文件可解析、冻结缓存可重建、契约漂移/开放窗口、角色范围和门禁配置等，不再检查账户、sudo、Go 或系统路径。
   - 删除 `wbsvr/`（Go 服务、测试、模块、安装脚本和 sudoers）。不修改系统账户、`/var/lib/wbsvr` 或其他系统资源；已有托管项目的导出/迁移步骤只写入历史文档。

2. **收紧本地契约生命周期**
   - 复用 `sha256_file`、`read_unlocks`、`close_unlock`、`contract_drift` 和原子 `save_state/write_frozen`。
   - 为契约保留现有展示用 `version`，新增内部单调 `revision`；初始登记/首次 lock 为 1，每次合法 bump 同步递增，并把 revision 与 SHA 一起作为任务绑定快照。
   - `contract lock` 允许未锁契约首次建立 SHA；对已有锁定契约重新计算 SHA 后，只要内容发生变化就拒绝覆盖基线并提示先走 unlock → bump，即使调用者提供了理由或窗口已打开也不能用 lock 冒充 bump；内容未变时只做幂等关闭窗口。
   - `contract bump` 必须满足：契约已有锁定基线、修改前存在对应 unlock 文件、当前内容确实不同、契约文件存在。理由优先继承 unlock 记录，命令行理由不能绕过预先窗口；成功后写入 old/new revision 与 SHA、关闭该契约窗口和争议、为消费者创建带新快照的同步任务。
   - 让 `contract_drift` 同时报告内容漂移和任何开放 unlock 窗口；因此 `contract verify`、`contracts_intact`、`gate check`、`status` 和 session-start 都会阻断或显式提示“尚未 bump”，避免修改中间态通过门禁。
   - 对契约 owner/consumer、路径、锁定状态和契约名继续做输入校验；开发角色仍不能写 `.workbench/contracts/` 或 `.workbench/artifacts/design/`，不扩大其角色范围。

3. **把契约快照内核化到任务**
   - 增加共享的契约引用解析/规范化辅助函数。新任务的 `contracts` 存为 `{name, version, revision, sha}` 对象数组，而不是仅存名字；`task add --contracts` 必须检查契约存在且已锁定，并在创建时复制当前快照。
   - 在 `load_state` 或一次性迁移路径兼容旧 state：把历史字符串契约引用解析为当前契约快照；无法解析或契约未锁定的引用标记为无效并由任务检查/门禁报告，不静默放行。
   - `task start` 检查依赖只满足于 `done`/`skipped`、任务状态可启动、所有引用契约仍存在且锁定，并且版本/revision/SHA 与当前契约一致；通过后记录启动时间和绑定快照。
   - 增加显式 `task check <ID>`（或等价的 contract/task heartbeat 检查命令），供开发 agent 在工作过程中重复确认契约；检查失败返回非零并把任务标为 stale/contract-changed 语义，提示重新读取契约和 reopen。
   - `task done` 只允许合法的 doing 任务完成，并在归并流水账前再次校验契约快照、漂移、开放窗口和 stale 状态；发现契约已 bump 时拒绝完成，确保旧实现不能静默落为 done。
   - `contract bump` 遍历所有任务引用：将绑定旧 revision/SHA 的 done、doing 或受影响任务标为 `stale` 并记录原因，同时创建消费者同步任务；不要只依赖任务标题或字符串匹配。
   - `task reopen` 仅用于 blocked/stale 任务，显式刷新其契约快照为当前锁定版本后回到 todo；多个依赖的任务只有在没有 blocked/stale 契约依赖时才可恢复。补齐 `_propagate_stale` / `_restore_stale` 的传递闭包，覆盖 `A → B → C`，避免只失效一层。
   - 收紧 `task start/done/block/reopen/skip` 的状态转移和错误信息，统一 `skipped` 在调度与门禁中的语义；保持任务流水账归并和并发状态锁设计不变。

4. **让开发中的 agent 看见契约变化**
   - 在 hook 写入检查复用任务契约校验：开发角色对产品代码的下一次 Write/Edit/apply_patch 或可解析 shell 写入，若存在其活动任务的旧契约绑定则拒绝，并给出 `task check`、停止实现、交回主线程的明确提示；执行记录目录仍可用于记录阻塞信息。
   - 对无法关联具体任务的并行同角色场景采用偏保守策略：只要该角色有活动任务发现契约不一致就阻断实现写入；`task check <ID>`、`task block`、`task reopen` 等工作台命令仍可执行。
   - 更新 `.claude/agents/{frontend-developer,backend-developer}.md`、`.codex/agents/{frontend-developer,backend-developer}.toml` 以及 `.claude/.agents` 的 `wb-contract`、`wb-flow`、`wb-loop`：开工后读取任务返回的契约快照，定期运行 `task check <ID>`，发现缺字段只 `task block`，bump 后重新读取并 reopen，完成前必须通过契约检查；明确本地模式不再使用 hosted/checkout/commit。
   - 同步 architect 的本地变更流程和任务绑定说明，确保设计文档、接口契约和消费任务的版本关系可操作。

5. **更新文档并保留历史经验**
   - 修改 `docs/framework-assessment.md`：当前能力只描述本地契约、版本绑定、冻结和 hook；将 wbsvr 改为已移除的历史方案，不再列为当前能力、selfcheck 内容或后续必选路线。
   - 修改 `docs/architecture.md`、`docs/README.md` 及必要的契约/调度/权限文档：删除当前托管归因，说明本地 bump、任务 stale 和开放窗口门禁；README 中保留 `wbsvr.md` 链接但标注“历史设计，已移除”。
   - 在 `docs/wbsvr.md` 顶部加醒目的历史设计声明，保留其设计决策和失败经验，并新增已有 hosted 项目迁移到本地契约的步骤；明确文档不是当前安装或操作手册。
   - 全仓库搜索 hosted/sealed/wbsvrd/sudoers 等引用，除历史文档和迁移说明外不保留可执行能力描述。

6. **回归自检和验证**
   - 修改 `cmd_selfcheck`：删除 Go 编译和托管生命周期检查，保留本地状态、门禁、并发、权限、争议和报告断言。
   - 新增/补充 selfcheck 场景：已锁契约直接改后 `lock` 失败；无预先 unlock 的 `bump` 失败；开放窗口使 `verify`/门禁失败；任务保存对象化契约快照；bump 后旧任务 stale 且 `task done` 失败；`task check` 能发现 revision/SHA 变化；`A → B → C` stale 传播与多依赖恢复；新任务拒绝未锁/不存在契约；开发角色不能写契约和设计文档。
   - 运行 `python3 -m py_compile .claude/hooks/wb.py`、`python3 .claude/hooks/wb.py selfcheck`，按需运行现有 Python 测试；用 `rg` 确认 wbsvr 可执行引用已清理；检查删除目录、CLI `--help` 和文件清单。
   - 不初始化当前仓库的 `.workbench`，不运行系统账户、sudoers、Go 服务安装或系统级迁移操作。

# Critical Files

- `.claude/hooks/wb.py`：唯一核心实现，托管分支删除、本地契约生命周期、任务契约快照、stale 传播、hook 边界和 selfcheck。
- `.claude/agents/architect.md`、`.claude/agents/frontend-developer.md`、`.claude/agents/backend-developer.md`：Claude 角色流程和契约变更纪律。
- `.agents/skills/wb-contract/SKILL.md`、`.agents/skills/wb-flow/SKILL.md`、`.agents/skills/wb-loop/SKILL.md` 及 `.claude/skills/` 对应文件：两端编排说明。
- `.codex/agents/*.toml`：Codex 角色提示同步。
- `docs/framework-assessment.md`、`docs/architecture.md`、`docs/README.md`、`docs/wbsvr.md`：当前能力、架构归因和历史迁移说明。
- `wbsvr/`：整体删除。

# Verification

1. 静态检查：`python3 -m py_compile .claude/hooks/wb.py`；`rg -n "hosted|WBSVRD|SVC_USER|SEALED_KEYS|Wbsvr|sealed_payload|sealed_audit|wbsvrd|--sealed|--hosted"`，确认只剩历史文档/迁移说明。
2. CLI 检查：`python3 .claude/hooks/wb.py --help`、`contract --help`、`doctor --help`，确认无托管选项且本地命令仍存在。
3. 内核回归：`python3 .claude/hooks/wb.py selfcheck`，验证生命周期、任务快照、stale 闭包、hook 拒绝和并发状态写保护。
4. 若仓库已有独立 Python 测试，运行其测试命令；记录失败输出并修复后重跑。不要把 `wbsvr` Go 测试作为当前门禁。
5. 最终检查变更文件和文档链接，确认没有 `.workbench` 运行态被意外初始化，也没有创建/删除系统账户、sudoers 或 `/var/lib/wbsvr`。

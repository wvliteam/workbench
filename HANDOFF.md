# HANDOFF：移除 wbsvr，收敛为本地契约工作流

- 交接日期：2026-09-04
- 项目：个人软件开发工作台（subagent 驱动 + 六阶段流程）
- 交接目的：记录本会话关于移除 `wbsvr` 的设计结论、已执行改动、验证事实和未完成事项，供后续会话直接接续。
- 当前分支：`main`

## 1. 用户目标

用户要求移除 `wbsvr`。核心原因是：个人开发工作台需要让 architect 发布方案和接口契约后，前后端可以基于同一版本并行开发；开发 agent 发现契约有疑问时只能反馈主线程或 architect，不能自行修改冻结文档；契约被正式修改后，其他正在开发的 agent 必须能发现变化，不能让基于旧契约的任务静默完成。

用户明确认为 `wbsvr` 会把系统复杂度整体提高，并额外引入系统账号、sudoers、常驻 Go 服务和部署运维成本，因此不划算。最终决策是：不引入托管控制面，保留并加强本地 `.workbench`、SHA-256、冻结清单、解冻窗口、任务快照、stale 传播、hook 和门禁。

## 2. 方案结论

当前方案只使用项目内文件和单一 Python 内核：

```text
architect 写方案/接口正文
        |
        v
contract add -> contract lock -> 前后端按同一快照并行开发
                                      |
                 发现契约问题 -> task block / dispute -> 反馈主线程
                                      |
主线程或 architect：impact -> unlock --reason -> 修改 -> bump
                                      |
        更新 version/revision/SHA，旧任务 stale，创建消费者同步任务
                                      |
            消费方重新读取契约 -> task reopen -> task start
```

任务中的契约引用必须保存完整快照，而不能只保存名称：

```json
{
  "name": "user-api",
  "version": 2,
  "revision": 3,
  "sha": "<sha256>"
}
```

字段含义：

- `name`：契约名称；
- `version`：面向人的版本号；
- `revision`：内部单调递增修订号；
- `sha`：契约正文的 SHA-256 内容指纹。

这样可以防止任务动态解析到新契约后，把旧实现错误地当作新契约实现完成。

## 3. 契约生命周期规则

### 首次建立

1. architect 先写好本地契约正文；
2. `contract add` 登记路径、owner 和 consumers；
3. `contract lock` 首次建立哈希基线，revision 从 1 开始；
4. 锁定后正文进入冻结清单，Write/Edit/Bash/apply_patch 等工具路径不能直接修改。

登记但尚未首次 lock 的正文允许 architect 完成初稿；首次 lock 之后才建立不可变 SHA 基线。

### 正式变更

锁定契约只能按以下顺序修改：

```bash
python3 .claude/hooks/wb.py contract impact --name user-api
python3 .claude/hooks/wb.py contract unlock --name user-api \
  --reason "说明为什么必须变更，以及影响哪些消费者"
# 仅此时修改对应契约正文
python3 .claude/hooks/wb.py contract bump --name user-api
```

硬规则：

- `unlock --reason` 必须先于正文修改，理由不能事后补；
- 解冻窗口按契约分别保存，不能用一个窗口放开所有契约；
- `state.json`、`role`、`frozen`、`unlock` 和 `artifacts.jsonl` 永不解冻；
- 已锁定正文发生漂移时，`contract lock` 不能覆盖旧基线；
- `bump` 不能仅凭命令行理由替代预先存在的 unlock；
- `bump` 时正文必须确实发生变化，不能只刷版本号；
- `bump` 关闭对应 unlock/dispute，递增 version/revision 并重新记录 SHA；
- 旧 version/revision/SHA 的任务必须变为 `stale`；
- 每个 consumer 获得带新快照的同步任务；
- `contract verify`、gate、status、session-start 和 task 完成检查都应发现漂移或开放窗口。

建议在 unlock 时记录当时的旧 SHA。bump 应确认 unlock 建立在旧基线之上，避免“先直接改文件、再 unlock、再 bump”伪装成合法流程。

## 4. 任务状态和开发纪律

任务状态语义：

- `todo`：等待启动；
- `doing`：正在执行；
- `blocked`：因契约、依赖或其他问题暂停；
- `stale`：实现或依赖基于已失效的契约，不能继续完成；
- `done`：通过当前契约快照校验并完成；
- `skipped`：带明确理由跳过，并按完成语义参与调度/门禁。

开发 agent 的协议：

1. 读取任务绑定的契约路径和 version/revision/SHA；
2. 开工前运行 `task start <ID>`，写入前运行 `task check <ID>`；
3. 开发期间定期运行 `task check <ID>` 或 heartbeat；
4. 发现缺字段、类型冲突、错误码不完整或语义不明确时立即停止实现；
5. 用 `task block <ID> --reason "..."` 或契约 dispute 反馈主线程；
6. 不直接写 `.workbench/contracts/` 或 `.workbench/artifacts/design/design.md`；
7. 不用 Bash、sed、tee、cp、mv、apply_patch、外部编辑器等替代正式变更流程；
8. bump 后停止旧快照实现，重新读取正文；
9. 仅在重新绑定并 `task reopen` 后再次 `task start`；
10. 收工前再次运行 `task check`，再由编排者复核产物、校验命令和输出后执行 `task done`。

`task done` 不能绕过契约快照检查。任务依赖必须把 `skipped` 视为已完成，但 `blocked`/`stale` 仍然阻断下游。

当任务依赖链为 `A -> B -> C` 时，A 被 block 或因契约变化 stale，B 和 C 都必须递归失效；即使 C 之前是 done，也不能继续作为有效完成。恢复时只有在所有依赖都不再 blocked/stale 后，才允许递归把 stale 任务恢复为 todo，并刷新每个任务的当前契约快照。

## 5. 权限与发现机制

本地方案按纵深防御工作：

1. PreToolUse hook 阻止写出项目根；
2. hook 阻止写入永久冻结文件和已锁契约；
3. hook 按 `agent_type` 检查角色写入范围；
4. `resolve()` 静态解析重定向、`cp`、`mv`、`install`、`sed -i`、`dd` 等 shell 写入目标；
5. 动态 shell 目标无法可靠解析时保守拒绝 subagent 写入；
6. `contract verify` 用 SHA-256 检出外部编辑器、git 恢复、rsync 或人工修改造成的当前漂移；
7. `task check` 和 `task done` 检查任务绑定是否仍匹配当前契约；
8. gate 检查契约漂移、开放 unlock、blocked/stale 任务；
9. session-start/status 显示 stale、漂移和开放窗口；
10. `bump` 正式传播失效并创建消费者同步任务。

开发中若存在活动任务的旧契约绑定，下一次针对产品代码的 Write/Edit/apply_patch 或可解析 shell 写入应被 hook 拒绝；执行记录目录仍应允许用于记录阻塞原因和已完成进度。

边界必须如实表述：本地方案防的是正常工作流中的模型绕过和误操作，不是强对抗安全系统。拥有宿主权限的程序或 agent 仍可能修改 hook 源码、伪造状态、通过外部程序临时修改再还原文件。外部路径由 SHA 和门禁发现，但哈希不能证明文件中途是否曾被改过又恢复。

## 6. 本会话已经执行的改动

### 已落盘或已由 agent 报告完成

- 删除仓库内 `wbsvr/` 服务树，git 状态中显示以下文件已删除：
  - `wbsvr/go.mod`
  - `wbsvr/install.sh`
  - `wbsvr/main.go`
  - `wbsvr/main_test.go`
  - `wbsvr/wbsvr.sudoers`
- 未创建或删除系统账号；未修改系统 sudoers；未操作 `/var/lib/wbsvr`；未安装或启动常驻服务。
- `.claude/hooks/wb.py` 已由后台 agent 清理托管入口，报告中包括：
  - 删除 `wbsvr` / hosted / sealed 逻辑和符号；
  - 删除契约 `--hosted`、`read`、`checkout`、`commit` 入口；
  - 删除 `doctor --sealed` 入口；
  - 删除 `main` 中的 `WbsvrError` 捕获；
  - `python3 -m py_compile .claude/hooks/wb.py` 通过；
  - agent 报告残留搜索为空。
- `.claude/hooks/wb.py` 已有本地契约辅助函数：`contract_revision`、`contract_binding`、`contract_ref_name`、`task_contract_names`、`migrate_contract_refs`、`contract_binding_check`、`task_contract_errors`、`task_binding_for_name`、`validate_task_contracts`。
- `load_state()` 已增加 JSON 解析和顶层类型检查，并尝试迁移旧字符串契约引用。
- `frozen_paths()` 已改为只把已建立 SHA 基线的契约正文加入冻结清单；首次 lock 前允许 architect 完成正文。
- `read_disputes()` 和 `contract_drift()` 已收敛为本地文件和本地 SHA 逻辑。
- `.claude/skills/wb-contract/SKILL.md` 与 `.agents/skills/wb-contract/SKILL.md` 已同步部分本地契约协议，包括任务快照、revision、stale、reopen、block/dispute 和无 hosted 路径。
- `.claude/skills/wb-flow/SKILL.md` 已同步部分本地流程，包括每轮 status、并行 develop、任务快照、task check、bump 后 stale 和门禁阻断。
- `docs/README.md` 已有变更，但尚未确认是否已完整改为“wbsvr 历史设计”。
- `.claude/plans/melodic-herding-quiche.md` 已写入项目内计划路径，记录了完整改造步骤。

### 已执行的验证事实

- `python3 -m py_compile .claude/hooks/wb.py` 曾成功返回，无语法错误。
- 原 `wbsvr` Go 服务测试曾通过：`go -C wbsvr test -buildvcs=false ./...`，但服务现在已删除，该结果不是当前实现的门禁结果。
- `python3 .claude/hooks/wb.py status` 在当前仓库未初始化 `.workbench` 时正确提示先 init，但当时暴露了运行时 `NameError: name 'WbsvrError' is not defined`；该残留已由清理 agent 报告删除，仍需主线程重新运行 CLI 复核。
- 当前仓库没有初始化业务 `.workbench`，这是有意的，避免把工作台自身改造污染成一个业务流程实例。

## 7. 当前未完成和待复核事项

以下事项在生成本交接文档时不能宣称完成：

### 核心内核

- `task add` 是否已经把 `--contracts` 转成对象化 `{name, version, revision, sha}`，并拒绝不存在/未锁契约，尚未最终确认；
- `task start` 是否校验任务状态、依赖和契约快照，尚未最终确认；
- `task check` 是否实现并接入 parser，尚未最终确认；
- `task done` 是否强制校验契约快照，尚未最终确认；
- `contract lock` 是否拒绝锁定后漂移覆盖旧 SHA，尚未最终确认；
- `contract unlock` 是否记录旧 SHA，`contract bump` 是否严格要求预先 unlock，尚未最终确认；
- `contract bump` 是否递增 revision、标记所有旧绑定任务 stale、创建新快照同步任务，尚未最终确认；
- `_propagate_stale()` / `_restore_stale()` 是否实现传递闭包和多依赖恢复，尚未最终确认；
- `cmd_next`、`contract impact`、selfcheck 中仍可见旧的字符串契约成员检查，需要在对象化后统一修复；
- hook 是否已对活动开发任务的旧契约产品代码写入做阻断，尚未最终确认；
- `doctor` 是否需要保留为纯本地诊断，或已被整体移除，尚未最终确认。

### 协议、配置和文档

- `.claude/agents/` 和 `.codex/agents/` 的 architect/frontend/backend 协议仍需补齐任务快照、task check、heartbeat、stale/reopen 和 bump 后停工规则；
- `.claude/skills/wb-loop/SKILL.md` 与 `.agents/skills/wb-loop/SKILL.md` 仍需核对一致性；
- `docs/framework-assessment.md` 当前仍有旧的 wbsvr 能力描述，需要改为历史方案；
- `docs/architecture.md`、`docs/contracts.md`、`docs/scheduling.md`、`docs/permissions.md` 仍需去除当前托管归因并补充本地边界；
- `docs/README.md` 应把 `docs/wbsvr.md` 标为“历史设计，已移除”；
- `docs/wbsvr.md` 应保留，但顶部需声明不是当前安装手册，并补充从 hosted 导出到本地 `contract add`/`lock` 的迁移背景；
- 文档同步 agent 在本会话中曾因提示过长失败，后续是否完成必须以 git diff 和文件内容复核为准。

### 验证

待运行且不能预先声称通过：

```bash
python3 -m py_compile .claude/hooks/wb.py
python3 .claude/hooks/wb.py --help
python3 .claude/hooks/wb.py contract --help
python3 .claude/hooks/wb.py doctor --help
python3 .claude/hooks/wb.py selfcheck
```

还需用临时目录验证：

- 首次 add/lock/verify；
- 锁定后直接修改再 lock 必须失败；
- 无预先 unlock 的 bump 必须失败；
- unlock 后修改再 bump 必须成功；
- bump 后旧任务变 stale，task done 必须失败；
- task check 能发现 version/revision/SHA 变化和开放窗口；
- A -> B -> C 的 stale 递归传播；
- 多依赖 stale 的正确恢复；
- 新任务拒绝未锁/不存在契约；
- 开发角色不能写契约和 design 文档；
- hook、Bash 解析、apply_patch 和角色范围回归；
- 最终残留扫描只允许历史文档和迁移说明保留 `wbsvr` 文字。

## 8. 下一次会话建议顺序

1. 先读取本文件和 `CLAUDE.md`，执行 `python3 .claude/hooks/wb.py status`，确认当前根和未初始化状态；
2. 读取 `.claude/hooks/wb.py` 的任务命令、契约命令、hook 和 selfcheck 段落；
3. 先补齐任务契约快照和状态转移，再补 lock/unlock/bump 的严格生命周期；
4. 再实现 stale 传播/恢复闭包与活动任务 hook 检查；
5. 修复 selfcheck 中所有旧字符串 contracts 断言；
6. 同步 agent prompts、skills 和 docs；
7. 运行语法检查、CLI help、selfcheck 和临时目录回归；
8. 用 `git diff --check`、`rg` 和 `git status` 做最终清单复核；
9. 只有所有验证真实通过后，才把任务 #1/#2 标记 completed。

## 9. 不可违反的边界

- 不执行系统级迁移；
- 不创建或删除系统账户；
- 不修改系统 sudoers；
- 不操作 `/var/lib/wbsvr`；
- 不安装或启动常驻服务；
- 不把本地方案宣称为强对抗或不可伪造；
- 不通过修改 settings、扩大 role scope 或等价 shell 写法绕过权限守卫；
- 契约修改必须先 unlock，再修改，再 bump；
- 开发 agent 发现契约问题必须 block/dispute 并反馈主线程；
- 不把 subagent 自报结果当作主线程验证；
- 不把未运行的测试描述为通过；
- 不初始化当前仓库的业务 `.workbench`，除非用户明确要求把它作为一个工作台项目运行。

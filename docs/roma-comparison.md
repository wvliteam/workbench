# 与 ROMA 的对比：可借鉴项

**本文全部是未实施的提案。** 记的是「另一套系统解了什么我们没解的问题、抄过来要付什么代价」，不是已有行为。任何一条落地前，`wb.py` 与 `selfcheck` 的断言仍是唯一权威。

对比材料是 ROMA v0.3.6 的一份源码快照（百度 `roma-team`，`.claude/` + `.codex/` 双端插件树，约 180 个文件）。快照放在 `output/`，已在 `.gitignore` 里 —— **不进本仓库**，本文是它留下的唯一痕迹。对比日期 2026-09-03。

局限先说清楚：只读了代码与 SKILL 文档，没有在真实工作区跑过它，也没有它的失效数据。下面每条判断都基于「代码里写了什么、注释里承认了什么教训」，不是实测对比。

## 两套系统的定位

| | ROMA | 本工作台 |
| --- | --- | --- |
| 层级 | workspace **运行时内核** | 开发**流程编排** |
| 载体 | 三端插件树（Claude Code / Codex / Comate），10 个 skill + 3 个 hook + 共享 lib | 单个 `wb.py`（2257 行）+ 7 个角色 subagent |
| 管什么 | 仓库路由 `repos/`、知识体系 `docs/`、执行产物 `artifacts/`、健康检查、自更新、跨端适配 | 六阶段流水线、契约冻结、门禁、角色隔离 |
| 唯一的流水线 | `env-init`（环境初始化），G0–G8 门禁 + `INIT-PLAN.json` 任务图 + evidence ledger | clarify → analyze → design → develop → verify → retro |
| 门禁强制性 | 规则写在 SKILL.md 里靠模型遵守，校验脚本只做 schema 检查 | `phase advance` 退出码挡住阶段推进 |

**互补大于重叠。** 我们有的它没有：契约哈希冻结、解冻申报窗口、`fnmatch` 角色范围、门禁即命令。重叠区只有权限守卫和任务状态机 —— 而重叠的那部分，它解掉了我们在 [permissions.md](permissions.md) 和 [architecture.md](architecture.md) 里明写「做不到」的三个问题。

## 一、shell 写入目标真解析 + `uncertain` 三态

**我们的问题。** 从任意 shell 命令抽出全部写入目标，`CLAUDE.md` 与 `permissions.md` 都写了「做不到，做半个检查比不做更坏」。于是现状是二选一：

- `frozen_hits()` 只做 `rel in cmd` 文本匹配 —— 会把 `cat docs/design.md > /tmp/x` 这种纯读误判成写；
- 越根检查只认 `>` / `>>` 的绝对路径目标 —— `cp` / `mv` 全漏（`BASH_WRITE` 至今不含这两个，见 [wbsvr.md](wbsvr.md) 阶段 0）。

**ROMA 的做法。** `lib/shell_write_targets.py`，126 行零依赖，返回 `(targets, uncertain)`：

1. `strip_heredocs()` 先剥掉 heredoc 正文 —— 否则 body 里的 markdown 引用 `> 注意` 会被当成重定向；
2. 按 `||` / `&&` / `;` / `|` / 换行切段，每段 `shlex.split`；
3. 重定向正则 `(?:\d+|&)?>{1,2}\|?\s*([^\s;|&<>()]+)` 刻意排除 `2>&1` 与 `>&2`；
4. 按命令名分类抽操作数：`tee`/`rm`/`truncate`/`touch`/`mkdir` 取全部参数，`mv`/`cp`/`ln`/`rsync`/`install` 取末参数，`chmod`/`chown` 跳过首参数，`sed -i` 跳过脚本参数，`dd` 取 `of=`，`git` 只在 `checkout`/`restore`/`mv`/`rm`/`clean`/`stash` 后取参数；
5. 命中 `eval` / `xargs` / `awk` / `sh -c` / `python -c` / `$(...)` / 反引号，或目标含 `$VAR` 时置 `uncertain=True`。

**这就是第三条路。** 不是「精确解析」也不是「粗匹配」，是**能解析就精确判，解析不了就退回粗检查并在拒绝信息里说明「写入目标无法解析，已一并拦截」**。既不漏，也不假装精确 —— 我们当初拒绝做的理由（做半个检查会让人误以为有防护）被 `uncertain` 这个显式出口消掉了。

它的注释里留了实测误报清单，可以直接当 `selfcheck` 的用例搬：

```
cat docs/development/progress-tracking.md > /tmp/x.md   # 老规则 deny，实际是纯读
grep -R X agents/references/ > /tmp/o.log 2>&1          # 老规则 deny
cp repos/index.md /tmp/idx.md                           # 老规则 deny
```

也留了它自己接受的缺陷：`cp -t DIR src...` 会被末参数规则判错，注释写「rare enough to accept」。抄的时候连这条注释一起抄 —— 已知缺陷写下来才不会变成第二个人重新踩的坑。

**落地。** `frozen_hits()` 换成 `resolve()`：`targets` 非空且 `uncertain=False` 时精确比对冻结清单；`uncertain=True` 时退回现在这套文本匹配，并在拒绝话术里加一句说明。同时补掉 heredoc 误判（architect 用 `cat > design.md <<'EOF'` 写文档是文档里写明的正常操作）。

**代价。** 126 行新代码 + 一批用例。`_LAST_ARG` / `_ALL_ARGS` 这几张表会随 shell 用法漂移，得当成需要维护的清单，不是一次写完的常量。

## 二、契约争议熔断

**我们的问题。** 前端按锁定契约写代码，发现契约和现实对不上 —— 比如后端字段实际不可能返回。现有机制只有 `contract unlock --reason` 申报**改契约**，没有「冻结全线开发」的开关。subagent 此时怎么做全靠自觉，而「先在实现侧加个兼容层跑起来」是契约先行并行开发的头号失效模式：它让契约与实现悄悄分叉，`contract verify` 检不出来（契约文件没被改）。

**ROMA 的做法。** `artifacts/<work-item>/.contract-disputed` 哨兵文件。`PreToolUse` 里 `Guard.halt()` 检测到它存在时，把所有实现类 agent 的写入全部拦下，只放行两样：它自己的执行记录、`/tmp`。

关键不在机制在**话术**。源码注释写得直白：

> A denied tool call is not a kill: the wording must read as a termination order, not a permission error, or the agent just retries another path.

拒绝信息因此写成终止令：

> 立即停止开发…不要重试、不要改用其他写入路径、**不要在实现侧加兼容层绕过冲突**。把已完成到哪一步、哪些文件已改、还差什么写进你自己的执行记录，然后立即返回，由主 Agent 决定是否重新派发架构角色修订契约。

这条对我们同样适用 —— 我们现有的拒绝信息按 owner 分岔给了该跑的命令，方向对，但那是「怎么继续」的指引；熔断场景要的是「停下来别继续」，两种语气不能混。

**落地。** `wb.py contract dispute --name <契约> --reason '<冲突在哪>'` 落哨兵到 `.workbench/disputes/<契约名>`；`hook_pre_tool` 在角色属于两个 developer 时命中即拒（放行 `.workbench/artifacts/develop/**` 下自己的记录与 `/tmp`）；`contract bump` 或用户显式 `dispute --clear` 时解除。

**代价。** 多一个状态维度，`status` 要显示。误触发的成本高（全线停工），所以哨兵只能由 developer 主动落，不能靠推断。

## 三、`__unknown__` 调用者 = 门禁失效告警

**我们的问题。** `current_role()` 拿不到载荷里的 `agent_type` 时退回读 `.workbench/role` 文件兜底。[architecture.md](architecture.md) 的已知边界承认了这条，但兜底的实际效果是：**把「身份识别坏了」伪装成「这是主线程」**，静默降级。

**ROMA 的做法。** 三态而不是两态：

| 载荷 | 判定 |
| --- | --- |
| 有 `agent_type` | 用它 |
| 无 `agent_type` 但有 `agent_id` / `agent_transcript_path` | `UNKNOWN` —— 证明来自**某个** subagent，但类型被隐藏了 |
| 两者都无 | `MAIN` |

`UNKNOWN` 命中受管路径时既不放行也不静默拒，而是发 `ask`，文案是「**门禁失效告警**：本次要写入 X，但载荷未携带可识别的调用者身份…该产物本应只允许 Y 写入，现在无法验证发起者是谁」。Codex 不支持 `PreToolUse` 的 `ask` 决策，那边降级成 `deny` 并说明原因。

**为什么值得抄。** 「静默降级是最坏的失败模式」这条我们在 [wbsvr.md](wbsvr.md) D13 已经认过一次（解锁窗口靠 agent 侧 hook 关，不触发就永久开着）。身份识别是同一个模式的另一个实例，同样该显式化。

**落地。** `current_role()` 加 `UNKNOWN` 分支：载荷里有 subagent 迹象但无 `agent_type` 时不读 `.workbench/role`，直接按告警处理。文件兜底只留给真正的主线程。

**代价。** 会多出一批 ask。如果 Claude Code 某些路径本来就不带 `agent_type`，这条会很吵 —— 落地前得先采样真实载荷确认 `agent_id` 是否可靠区分。

## 四、`stale` 状态与下游自动失效

**我们的问题，也是真漏洞。** 任务只有 `todo` / `doing` / `done` / `blocked`，`deps` 只在派发前挡一次。qa 把某任务打回成 `blocked` 后，**依赖它的任务仍然是 `done`**，`tasks_done:*` 照过。上游被推翻不会让下游失效。

**ROMA 的做法。** 状态七态：`PENDING` / `IN_PROGRESS` / `PASS` / `FAIL` / `BLOCKED` / `STALE` / `NOT_APPLICABLE`。规则三条：

- `FAIL` / `BLOCKED` / `STALE` **自动把全部下游标 `STALE`**，修好只重跑受影响的，不重跑整条链；
- `PASS` 必须带 `--output`（产物路径），`NOT_APPLICABLE` 必须带 `--reason`；
- 依赖未达 `PASS` / `NOT_APPLICABLE` 时脚本直接拒绝设置 `IN_PROGRESS`。

它还明确写了不做什么：

> 这里没有指纹、没有文件级失效计算，也不打算做缓存一致性：无法判断旧结果是否仍然有效时，直接把该任务标为 `STALE` 重跑，而不是去推断。

这个取舍对 —— 失效传播只需要图，不需要内容哈希。

**落地。** 加 `stale` 状态；`task block` 与打回时沿 `deps` 反向图把下游 `done` 改 `stale`；`tasks_done` 断言把 `stale` 算作未完成。约 10 行。

顺带值得加 `skipped`（对应它的 `NOT_APPLICABLE`，必须带 `--reason`）—— 现在遇到不适用的任务只能 `--force` 整个门禁，粒度太粗，而 `--force` 是要问用户的。

## 五、`unverified` 档与反自证校验

**我们的问题。** `run_check` 的 `cmd:` 分支只看退出码。`npm test -- --passWithNoTests` 退 0，`pytest --collect-only` 退 0，`mvn -DskipTests` 退 0 —— 全部记 PASS。门禁在这三种情况下形同虚设，而这正是 `gate_commands` 存在的理由。

**ROMA 的做法。** 验证项五态，「测试没跑」单独一档 `UNVERIFIED`，且和 `FAIL` 一样卡准出。`validate_verification_result.py` 拒四类自证：

1. 标 `PASS` 却没有把命令追溯到仓库证据的 `sourceRef`；
2. 命令里带 `-DskipTests` / `-Dmaven.test.skip=true` / `--skipTests` 等弱化测试的标志；
3. 测试日志显示**零用例执行**；
4. 日志中仍匹配失败信号（`Found ... errors` / `Format issues` / `Failed tasks` / `NOT RUN` / `404`）。

它给的理由很准：

> 主 Agent 同时是命令的选择者、执行者和判定者。

这句话是整套设计的支点 —— 只要执行者和判定者是同一个，退出码就不是独立证据。

**落地。** `run_check` 的 `cmd:` 分支在退出码 0 之外加两条扫描：命令文本含跳过测试的标志 → 记 `unverified`；日志匹配零用例或失败信号 → 记 `unverified`。`unverified` 在 `print_gate` 里显示为独立档，和 FAIL 一样挡 `phase advance`。

日志本来就全量落在 `.workbench/gate-<名>.log`，扫描是免费的。

**代价。** 失败信号正则会误报（业务日志里出现 `404` 是常事）。所以第 4 条要么只对已知测试框架的输出格式生效，要么降级成 `status` 里的提示而不是硬拦。先做 1、2、3 三条，第 4 条按项目配。

## 六、执行与判定的角色分离，落到权限层

**我们的问题。** 硬规则第 4 条「子 agent 说做完了不等于做完了」只是**给编排者的文字约定**。`GATES["develop"]` 的注释也写明 `verification.md` 由编排者写、没有角色 owner —— 但 `DEFAULT_ROLE_SCOPES` 里两个 developer 都有 `.workbench/artifacts/develop/**`。**开发 agent 能自己写那份复核记录。** 意图和机制在这里对不上。

**ROMA 的做法。** 进度文件对主 Agent 独占，拒绝话术直接讲原理：

> 步骤是否完成属于**判定**，而你只负责回报退出码与 OBSERVED 事实 —— 同一角色不能既执行又判定。把你的执行结果写进自己的执行记录并正常返回，由主 Agent 读取后回写步骤状态与证据指针。

配套的是「执行记录按调用者自己的名字命名」：文件名必须是 `<NN>-<自己的 agent 名>.md`，并行撞车时加实例后缀。这样每个 subagent 有专属写入位，不需要靠时间戳猜归属。

**落地。** 两处改动：

1. `verification.md` 从两个 developer 的范围里摘出去 —— 给 develop 产物目录做文件级例外，或把 developer 的产物范围收成 `.workbench/artifacts/develop/tasks/**`，`verification.md` 留在上一层只给主线程。
2. subagent 的执行记录按 `<任务号>-<角色名>.md` 命名并只允许写自己那份。这顺带解掉 [scheduling.md](scheduling.md) 里那条已知边界 —— 「产物归属按角色 + 任务 `started` 时间认领，同一角色两个任务并行时分不开」，按文件名归属就不用猜。

## 七、证据账本与分片归并

**ROMA 的做法。** `evidence-ledger.jsonl` 是事实级 append-only 日志，一条一个断言：

```json
{"stage":"discovery","scope":"api","claim":"入口是 /v1/chat/completions",
 "status":"CONFIRMED","source":"/repo/routes.py:42","artifact":null,"supersedes":null}
```

`status` 八态：`CONFIRMED` / `INFERRED` / `CONFLICT` / `UNKNOWN` / `OBSERVED` / `FAIL` / `BLOCKED` / `SUPERSEDED`。三条规则：

- 每条必须能追溯到源码位置、命令结果或 artifact；秘密只记变量名不记值；
- **委派出去的调查写自己的分片文件 `explore-<taskId>.jsonl`，冲突事实只标 `CONFLICT`**；
- **`supersedes` 裁决只能主 Agent 做**，在专门的归并步骤统一处理。

被推翻的旧结论用 `supersedes` 标记而不删除。

**对我们的价值。** 分片按 taskId 而非角色，正面解掉上面提到的并行归属问题。「subagent 只报事实、主线程才做裁决」和第六条是同一个原则的两处应用。

**代价。** 这是最重的一条。我们的 `artifacts.jsonl` 是文件写入流水账（低频判断、高频写入），证据账本是断言账本（低频写入、高判断价值），两者不是一个东西，要新造。**建议只在证据归属真的出过问题时再做** —— 现在的痛点是归属不清，那用第六条的文件名归属就够，不需要整套账本。

## 八、长期知识与本次执行的分界

**我们的问题。** `.workbench/artifacts/` 全是本次执行的产物，**没有沉淀出口**。同一仓库走第二个需求要 `init --force` 重开，上个需求学到的「这个仓库跑测试前必须先起 docker」「声明的 JDK 版本实际不可用，得用另一个」跟着状态一起没了。下次重新踩。

**ROMA 的做法。** 一句话判据：

> 换一台机器、下个月再做一次，这条结论还成立吗？

成立的进 `repos/<repo>/setup.md` 与 `docs/`，只对本次成立的留在 `artifacts/`。配一张双列对照表，把边界划到具体条目：

| 长期知识 | 本次执行 |
| --- | --- |
| 实测确认的 runtime 版本约束，含「声明版本不可用、必须用哪个」 | `java -version` 的原始输出与退出码 |
| 正确的安装、构建、启动、测试命令与工作目录 | 本次命令的耗时、时间戳、日志路径 |
| 真实的服务依赖、启动顺序与 readiness 判据 | 本次的 PID、端口占用、临时目录 |
| 必需的配置键名与凭据来源（只记名称） | 本次注入的具体值 |
| 每次都会遇到的已知问题与规避方式 | 本次的一次性网络抖动 |
| 主验证入口、适用验证项与成功标准 | 本次的 PASS/FAIL 矩阵与失败诊断过程 |

它还额外规定：「本次验证通过」不是长期知识；写入前必须完整读取现有文件，只改被实测推翻的条目；实测与文档冲突时以实测为准，**并写清原记录错在哪里**。

**落地。** retro 阶段的门禁加一条产物：把本轮学到的可复用约束写进仓库内的持久文件（跟着仓库走 git，不在 `.workbench/` 里）。判据就用上面那一句。

**代价。** 几乎为零 —— 一条 `artifact_contains` 断言加一段角色提示词。这条性价比最高的部分在于它是**一句判据**，不是一套目录规范（后者见「明确不抄的」）。

## 九、提醒的会话级去重

**ROMA 的做法。** `PostToolUse` 提醒每会话每类只发一次，靠临时目录里的 marker 文件（`roma-guard-<session>-<platform>-<category>`）去重；版本同步类例外，永远重发。

注释里点了教训：老规则是 `"docs/" in cmd and writeish.search(cmd)`，任何提到 `docs/` 又带 `>` 的命令都误报，结果是「**训练模型忽略这条提醒**」。

**对我们的价值。** 提醒的边际效用递减得极快，重复的无效提醒会连带降低有效提醒的权重。我们 `hook_post_tool` 目前没有会话级去重。改动很小（marker 文件），但前提是先有分类。

## 跨端与自更新（战略层，非现在必抄）

两件与我们正在做的事直接相关，记在这里备查：

**`lib/hookio.py` 的跨端抽象。** 我们仓库已有 `docs/codex-agent-migration.md` 与未提交的 `.codex/`、`.agents/`，在做同一件事。它的做法是松匹配正则一次覆盖两端工具名：

```python
WRITE_TOOL = re.compile(r"edit|write|patch|notebook|delete|move|rename", re.I)
SHELL_TOOL = re.compile(r"shell|bash|exec|run_command|process", re.I)
```

一套实现同时吃 Claude 的 `Edit`/`Write`/`MultiEdit`/`NotebookEdit`/`Bash` 和 Codex 的 `apply_patch`/`write_file`/`edit_file`/`shell`/`exec_command`/`unified_exec`。它选这条路的理由写在文件头：两端载荷只差 key 别名、两端只差认哪些输出字段，**每端一份 guard 买不到任何东西，而且已经漂移过**（两份 post-tool guard 曾是近乎相同的文件，只有 Codex 那份认得 `apply_patch`）。

更值钱的是它记下来的实测输出 schema 差异，这几条不看源码不会知道：

- `PostToolUse` 的输出**不能带 `decision` key** —— 它只接受 `decision: "block"`，填 `"allow"` 会导致根级校验失败、整个载荷被丢弃，提醒一起没了；
- `SubagentStop` 在 Codex 0.142.2 没有 additional-context 字段，只认 `systemMessage`；
- `PreToolUse` 的 `ask` 决策 Codex 能解析但没实现，必须降级成 `deny`。

**`roma-workspace-update` 的三方比对。** `.roma/installation.json` 记 `baselineVersion` 和每个组件的 `baseHash` / `acceptedHash`，组件策略分 `semantic-merge` / `guidance` / `ignore`，状态里 `safe-replace`（本地哈希等于 `baseHash` 才允许直接覆盖）、`merge-upstream-changed`（必须语义合并）、`accepted-local`。带旧工作区 adopt、backup、rollback。

它解决的是「工作台本体升级，下游改过的文件怎么办」。我们现在是代码库 clone 进工作区共享一份 `.claude/`，还没这问题；真要把工作台分发给别人用时，这套三方比对是现成答案，比「覆盖」和「不覆盖」都强。

## 明确不抄的

| 它有的 | 为什么不抄 |
| --- | --- |
| G0–G8 散文式门禁 | 规则写在 SKILL.md 里靠模型遵守，校验脚本只做 schema 检查。我们把门禁做成 `phase advance` 的退出码 —— 强制性严格更高，别为了对齐它退化成提示词 |
| `roma-knowledge-taxonomy` 整套知识分类学 | Knowledge Object / 开放式 taxonomy 演进 / 分类索引维护，对多人多仓 workspace 成立，对单人工作台是纯开销。只取第八条那句判据 |
| `roma-task` | SaaS 侧任务 CRUD（HTTP 调后端 `assignment`），与本地流水线无关 |
| `run_with_evidence.py` | 它强制所有需退出码的命令走这个包装器以保住真实退出码。我们 `run_check` 已经全量落盘 + 说明里带最后 5 行，同样目的更省事。差别只在 subagent 自跑的校验命令没有这层 —— 那条靠硬规则第 4 条的复核补 |
| 常驻健康检查与三端插件同步 | 我们只有一份 `.claude/`，没有同步对象 |

## 落地顺序

按性价比，不按上文顺序：

**先做（各自独立，互不依赖）**

1. **`stale` 状态 + 下游失效**（约 10 行）—— 补的是现在会静默放过的真漏洞，改动最小。
2. **`run_check` 加 skip 标志与零用例检测**（约 20 行）—— 日志已落盘，扫描免费；修的是 `gate_commands` 的实际盲区。
3. **`verification.md` 移出 developer 范围**（一行 scope）—— 让机制和 `GATES` 注释里已经写明的意图一致。
4. **retro 沉淀出口**（一条断言 + 一段提示词）—— 用「换台机器下个月还成立吗」当判据。

**再做（要改交互约定，单独一轮）**

5. **搬 `shell_write_targets.py`** —— 收益最大但要配一批用例，且 `frozen_hits` 的行为变化要同步改 `permissions.md` 的已知边界一节。
6. **契约争议熔断** —— 多一个状态维度，`status` 要显示，拒绝话术要重写。

**观望**

7. `UNKNOWN` 调用者告警 —— 落地前先采样真实载荷，确认 `agent_id` 在我们的运行环境里真的能区分 subagent 与主线程，否则会很吵。
8. 证据账本 —— 现在的痛点是归属不清，第 3 条的文件名归属就够；等归属之外的问题真的出现再说。

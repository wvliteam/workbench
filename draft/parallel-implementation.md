# 并行开发改造记录（2026-09-06）

三步改造的定点记录：多仓库 / 多需求并行隔离。**需求与方案对比的背景在 [roma-comparison.md](roma-comparison.md) 第十节，本文记实现过程、判错复盘与遗留边界。** 行为以 `wb.py` 与 `selfcheck` 断言为准 —— 本文不复述断言，只记为什么这样做、改的时候发现了什么。

改动范围：`wb.py`（+669/-271 行量级）+ 7 个 agent 定义 + 3 个 skill + `CLAUDE.md` / `README.md` + 5 份设计文档，全部未 commit 时写就本文。

## 需求

这个 workbench 类似 VSCode workspace：**当前业务的所有代码库放在一个 workbench 里，多个 agent 并行修改多个不同代码库、并行跑多条需求**。对照材料是 ROMA v0.3.6 源码快照（`output/agents.tgz`，对比日期 2026-09-03）。

改前的三个并行缺口：

| 缺口 | 触发场景 | 后果 |
| --- | --- | --- |
| hook 根随 cwd 漂移 | subagent 在 `repos/foo` 里跑工具，`find_root(cwd)` 从子仓库向上找不到外层 `.workbench/` | 外层工作区的契约与状态保护双双落空，守卫把子仓库当未初始化目录放行 |
| 一份 state 只装一条流水线 | 同仓库第二个需求 | 只能 `git worktree` 把代码也复制一份；或 `init --force` 重开（串行） |
| 冻结检查只查会话根 | 布局 A（嵌套 `.workbench/`）+ 会话 cwd 在外层根 + 写的恰好是内层冻结契约 | 内层清单里有的路径在外层清单不存在，静默放行；失效方向是放行不是误拒，主线程没有角色检查兜底 |

三步对应：1 环境变量钉根、2 flow（需求线）维度、3 嵌套根反查。三步都已落地，每步跑过 `selfcheck` 全绿。

## 第 1 步：`find_root()` 环境变量优先

ROMA `hookio.py` 的 `_resolve_root()` 把环境变量放在向上查找之前。照此改 `find_root()`：

```python
for var in ("WB_ROOT", "CLAUDE_PROJECT_DIR"):
    v = os.environ.get(var)
    if v and (Path(v) / ".workbench").is_dir():
        return Path(v).resolve()
```

- **为什么可行**：Claude Code 给 hook 进程自动注入 `CLAUDE_PROJECT_DIR`（会话的项目根），settings.json 用 `$CLAUDE_PROJECT_DIR` 绝对路径注册 hook —— 这层在子仓库 cwd 下天然生效，不需要新机制。
- **两个刻意约束**：要求目录里确实有 `.workbench/`（环境变量指错时不静默接管，仍走向上查找）；`WB_ROOT` 留作显式钉死（脚本、自检、CI）。
- **为什么比向上查找可靠**：载荷里的 cwd 与 shell 的 `cd` 都会让向上查找漂到另一份 `.workbench/`（多仓库布局里每个仓库一份），状态归属跟着漂。会话的项目根是注入的、不变的。

## 第 2 步：flow（需求线）维度

一份 `.workbench/` 同时跑多条流水线，每条一个 flow。**指针方案，不是 ROMA 那种每命令选择器**：

| 项 | 落点 |
| --- | --- |
| 状态 | `.workbench/flows/<flow>/state.json`（锁、门禁日志、解冻窗口、争议哨兵、frozen 缓存同目录） |
| 产物 | `.workbench/artifacts/<flow>/<阶段>/` |
| CLI 定位 | `.workbench/current-flow` 指针；`load_state` 以 `st["_flow"]` 定点，命令中途切指针不会把 A flow 状态写进 B flow 文件 |
| 守卫视角 | **读全部 flow 的并集** —— A flow 锁的契约在 B flow 视角照样冻结 |
| 角色范围 | 产物模式带 flow 通配（`artifacts/*/<phase>/**`），跨 flow 复用，不为每条需求改配置 |
| 特权层 | `flow new/switch/remove` 进 `PRIVILEGED_WB` 表，角色 subagent 跑不了 |

**指针方案否掉选择器方案的理由**：每命令一个 `--flow` flag 是全量接口翻新，而指针只动读与写两处；更重要的是 hook 场景指针是唯一可行的 —— subagent 跑 shell 时不会记得带 flag，而守卫必须不依赖它。指针的漂移风险（命令中途 `flow switch`）用 `st["_flow"]` 定点消掉：`load_state` 读哪个文件、`save_state` 写回哪个文件都在读入时定死，中途切指针不影响进行中的命令（还持着 A 的锁，连锁都错位 —— 这是当时发现的第一处顺带 bug）。

legacy 布局兼容：`flows/main` 的 state 若缺失，回落读 `.workbench/state.json` 旧位置；老项目零迁移。

### 第 2 步顺带修掉的三个 bug

1. **unlock / disputes 写侧漏改 flow**：`read_unlocks` / `read_disputes` 聚合逻辑已按 flow，但 `cmd_contract` 写侧还在写老路径，窗口与哨兵写下去守卫读不到。
2. **`_collect_targets` 的 must_exist 滤掉不存在的 legacy state.json**：flow 布局下 legacy `.workbench/state.json` 不存在，`sed -i` 写它被 must_exist 过滤后精确检查放行。修法：`if must_exist and not p.exists() and Path(raw).name not in FROZEN_ALWAYS: continue` —— 冻结文件名永不按存在性过滤。
3. **`save_state` 的 `_flow` 时序**：`write_frozen` 不带 flow 参数时读指针，而指针此刻可能已被切走 —— 加显式 flow 形参，由 `save_state` 传入读入时的定点。

## 第 3 步：冻结检查按写入目标反查嵌套根

**布局 A 的洞**：会话 cwd 在工作区外层时 `find_root()` 命中外层，写入目标却可能落在某个自带 `.workbench/` 的仓库里。内层锁的契约与状态文件在外层冻结清单里不存在，只查外层静默放行。**Bash 精确通道的失效形态**：`frozen_hits` 在外层清单里找不到该路径（`mentioned=[]`），但外层清单有 `.workbench/flows/` 目录条目、`rel in cmd` 文本匹配命中，接着精确模式 `real_hits = [h for h in hits if h in all_targets]` —— `.workbench/flows/` 不在 `all_targets`（那是 `repos/foo/.workbench/flows/main/state.json`），过滤后 `hits=[]` 放行。**漏**，不是误拒。

### 实现

1. **`nested_roots(target, session_root)`**：从写入目标向上收集会话根之内、含 `.workbench/` 的全部嵌套根。**只走到会话根为止** —— 走到文件系统根会把用户 home 下不相干的工作区捡进来，误拦方向。
2. **`_check_write_target` 冻结段多根循环**：会话根之外，逐个嵌套根按**该根的相对路径**查 `read_frozen` / `unlocked_paths`（豁免按同一根聚合 —— 内层 unlock 的窗口只豁免内层那份契约）。Bash 精确通道经 `all_targets` 循环（`for rel_tgt in sorted(all_targets): _check_write_target(...)`）自动走同一套，无需另改。
3. **拒绝话术带内层标识**：`（工作台 /…/repos/foo）` + `frozen_advice` 用内层根算（契约实名才对）。否则撞上的人不知道该查哪份状态、该去哪份 `contract list` 找名字。

### 顺带修掉的存量洞（第 3 步最有价值的发现）

嵌套断言先失败在 `echo > repos/foo/.workbench/flows/main/state.json` 上，追查发现与嵌套无关的**双斜杠失配**：

- `frozen_paths()` 输出目录条目 `.workbench/flows/`（带尾斜杠）；
- 检查处写 `rel.startswith(f + "/")` → 拼出 `.workbench/flows//`，**永远不中**；
- flow 布局下 `flows/<flow>/state.json` 整类漏拦 —— 任何根上都漏，不只是嵌套场景；
- **为什么 selfcheck 此前没抓到**：legacy 路径 `.workbench/state.json` 是完整条目，`rel in frozen` 直接命中，从没走到 startswith 分支。断言只测了「会拦」的具体形态，没测「目录条目 + 子路径」的通配形态。

修法：归一化成无尾斜杠再比（`dirs = {f.rstrip("/") for f in frozen}`）。

**教训**：守卫这类静默失效的机制，断言要覆盖**每一条匹配分支**，不是覆盖每一个「已知危险路径」—— 危险路径的形态一变（legacy → flow），中过的那条分支换成了另一条没测过的。

## 判错复盘

| 当时的判断 | 实际 | 为什么错 |
| --- | --- | ---|
| 「预留第 3 步影响大，约 100–120 行」 | 落地约 30 行（helper 15 + 循环改造 15） | 估的是「Bash 分支也要改一遍」—— 实际 `all_targets` 循环早已统一走 `_check_write_target`，Bash 精确通道零改动。第 2 步的重构本身把第 3 步的成本消掉了 |
| Bash 嵌套漏拦只是「外层清单不含该路径」 | 双因素：文本匹配能命中外层的 `.workbench/flows/` 条目，但精确过滤又把它滤掉 —— 看着拦了，其实放行 | 只推演了「清单缺条目」一种形态，没推演「文本命中后被精确通道二次过滤」的形态。Bash 冻结检查有**文本匹配 + 精确过滤**两层，漏拦可以发生在任一层 |
| 调试时第一版断言块写了 `contract add --root` | `contract` 子命令没有 `--root`，只有 `init` 有 | 凭「init 有 --root」外推了所有子命令。argparse 每个子命令的参数表是独立的 —— 写断言前先 grep 参数表 |
| `resolve()` 会处理 `cd repos/foo && sed -i … .workbench/…` | 完全不追踪 cd，目标按会话根解析成错误路径后被 must_exist 滤掉，`all_targets` 为空 | 把 resolve() 当成了 shell 语义。它只做静态解析，`cd` 是运行时状态。这条维持既有边界（见下） |

## 遗留边界（有意保留，review 时别当 bug 提）

1. **`cd repos/foo && sed -i … .workbench/…` 仍漏。** resolve() 不追踪 cd，目标按会话根解析成错路径。单根下 `cd .workbench/ && …` 同型写法本就是既有边界 —— 兜底正则只认切入 `.workbench` 的 cd（`cd repos/…` 不在覆盖内）。守卫文档一直教「写已冻结路径用相对会话根的完整路径，别 cd」。要扩就把 `_UNCERTAIN_PATTERNS` 加 `\bcd\b`（命令里出现 cd 就退 uncertain 模式，不漏拦但误报面宽）或兜底正则扩到任意切入点 —— 两条都是误报面换漏报面，暂不做。
2. **角色范围层维持会话根单根。** 外层会话写内层仓库的产品代码由**外层**角色的范围判定 —— `repos/**` 前缀在外层范围里（布局 B）或在布局 A 下外层会话不该写子仓库产品代码（该进子仓库会话）。嵌套反查只做冻结层：契约与状态文件的越权是全局性的（改哪份 state.json 都是在改某条流水线的门禁），产品代码的越权是上下文相关的（哪个范围管它取决于会话视角）。
3. **跨根同名契约提示取并集第一个。** 两个仓库各自登记同名契约 `user-api` 时，拒绝话术里的实名取查到的第一份。撞上的人 `contract list` 一查便知，不为此加复杂度。
4. **调试命令会被自己的守卫拦。** 复现脚本里写 `.workbench/flows/` 字面量 + `python3 -c` 触发 uncertain 文本匹配整条拦截。这是守卫按设计工作（宁可误拦），调试时拆字符串绕开即可，不是缺陷。

## 与 ROMA 的差异记录

ROMA work-item 方案 vs 本实现的三处刻意不同，理由都在 [roma-comparison.md](roma-comparison.md) 第十节：

| 维度 | ROMA | 本实现 | 为什么不同 |
| --- | --- | --- | --- |
| 定位方式 | 每命令显式传 work-item 选择器 | `current-flow` 指针 + `st["_flow"]` 定点 | hook 场景 subagent 不会带 flag，守卫不能依赖它 |
| 守卫视角 | 每条 work-item 自己的守卫规则 | 全部 flow 并集 | A flow 锁的契约在 B flow 视角不能变成普通文件；争议本就是全线停工信号 |
| 嵌套根 | 无此场景（无嵌套 `.workbench/`） | `nested_roots()` 反查 | ROMA 是单根 workspace 运行时，我们是 VSCode-workspace 式多仓库 |

## 验证方式

- `python3 .claude/hooks/wb.py selfcheck` 全绿，尾行检查名清单含「嵌套根」。嵌套断言块：外层 Write/Bash 写内层契约拦、内层 state.json 拦、内层 unlock 后外层放行、内层正常文件不误拦、外层自己冻结不受影响、话术含内根标识与实名。
- selfcheck 之外，用真实子进程走 `hook pre-tool` 分发路径复验过三种场景（argparse / hook 分发是 selfcheck 直接调函数覆盖不到的层）。
- 双斜杠存量洞的验证：单独立临时目录复现 `read_frozen` 输出与 `startswith` 比较行为，确认修复前后差异。

## 第二轮：跨 flow 修复（同日）

三步落地当天做了一次代码事实核查 + 临时目录探针复验，发现四处跨 flow 缺口并当日修复：SubagentStop 与 `contract lock` / `bump` 的关窗按 flow 定点（全 flow 关窗曾把别的 flow 改到一半的契约拆成死局）、归属文件带 flow 字段（跨 flow 同名任务 ID 原本互相认领产物）、新 flow 从 main 继承工作区级配置、CLI 增加 `WB_FLOW` 钉 flow（hook 与守卫不受影响）。**发现、根因、判错复盘与修复后的边界完整记录在 [cross-flow-review.md](cross-flow-review.md)** —— 本文不复述。

## 验证方式

- `python3 .claude/hooks/wb.py selfcheck` 全绿，尾行检查名清单含「嵌套根」。嵌套断言块：外层 Write/Bash 写内层契约拦、内层 state.json 拦、内层 unlock 后外层放行、内层正常文件不误拦、外层自己冻结不受影响、话术含内根标识与实名。
- selfcheck 之外，用真实子进程走 `hook pre-tool` 分发路径复验过三种场景（argparse / hook 分发是 selfcheck 直接调函数覆盖不到的层）。
- 双斜杠存量洞的验证：单独立临时目录复现 `read_frozen` 输出与 `startswith` 比较行为，确认修复前后差异。

## 第二轮：跨 flow 修复（同日）

第一轮把 flow 做出来之后做了一次代码事实核查（对照本文逐条验证 + 临时目录探针），发现三步改造解决了「单 flow 内并行」与「守卫跨 flow 视角」，但 **flow 的生命周期与身份层没有跟着分仓**。四处修复：

1. **SubagentStop 全 flow 关窗 → 死局（最重）。** 「是否清理」判断只看指针 flow 的 doing 任务表，`close_unlock` 却关全部 flow 的同名窗口。探针复现了完整死局：B flow 的 architect 开窗改契约改到一半，A flow 最后一个 subagent 结束把窗口关了 —— bump 被拒（没有窗口可消费）、unlock 再申报也被拒（正文已漂移），只能手工恢复旧正文。第一轮写 `close_unlock` 时把「宁可多关」当成了代价可接受的兜底，实测多关的代价不是重报一遍，是死锁。修法：清理与 doing 判断用同一个 flow 定点；`contract lock` / `bump` 的关窗同样只限本 flow（A 的 lock 不能替 B 收尾）。
2. **归属文件没有 flow 维度。** `task-agents.jsonl` / `artifacts.jsonl` 是工作区共享文件，任务 ID 每条 flow 独立从 T1 编起 —— 实测 main 的 T1 把 feature-b 同名任务的 agent 与产物认领了进去。修法：写入侧带 `flow` 字段，`merge_artifacts` 按任务所在 flow 过滤。**本轮判错**：第一版把「无 flow 字段的旧行」默认为本 flow，selfcheck 断言当场打回 —— 那等于旧行在每条 flow 里都匹配，串扰换了个方向还在。改成按 main 归属：存量日志都写在 main 线上，别的 flow 宁可少归并不认错账。
3. **`flow new` 丢配置。** `role_scopes` / `gate_commands` / `gate_timeout` / `max_parallel` 存在每条 flow 各自的 state 里，但它们描述的是「这个工作区怎么干活」。不继承则每条 flow 回裸默认值，而守卫的角色范围按指针 flow 的 state 读 —— 切了指针，同一批 agent 被按另一套范围判定。修法：新 flow 从 main 继承这四个键；任务、契约、阶段不继承（那是进度，不是配置）。
4. **指针竞态如实声明 + WB_FLOW。** 指针是全部会话共享的一份文件，CLI 状态命令在调用时刻按它定位：并发推两条 flow 时，A 的 `task start` 会落到 B 的 state 上（ID 撞了就是启动别人的任务）。第一轮选指针否选择器的理由（subagent 不带 flag、守卫不能依赖 flag）只对守卫成立，对状态层不成立。修法分两层：CLI 增加 `WB_FLOW` 环境变量显式钉 flow（hook 与守卫不受它影响，selfcheck 摘掉该变量保证环境无关）；文档如实降级 —— 不钉 WB_FLOW 时，多 flow 只支持「编排者串行交错」。

### 修复后的遗留边界（更新第一轮清单）

- **争议哨兵仍是工作区级**：A flow 的 dispute 停 B flow 的工是文档写明的语义；但 `dispute --clear --name` 不带 flow 定点，跨 flow 清理时「已解除」的提示可能与哨兵实情不符 —— 争议是全线停工信号，全局清理方向是保守的，暂不动。
- **两条 flow 共享同一份契约文件时，先 bump 的那条会让另一条陷入 P1 同型死局**（对方的基线哈希与文件不符，unlock 被拒）。这是「契约按 flow 各记一份、文件只有一份」的模型张力，不是本轮修复能消的 —— 跨 flow 契约本来就靠 owner 拆，同文件跨 flow 是工作流味道，先文档警告。
- **subagent 的 flow 仍靠指针**：subagent 不会带 `WB_FLOW`，编排者「切好指针再派、一批一条 flow」的纪律仍是并发的隐含前提。

selfcheck 新增三组断言：跨 flow SubagentStop 关窗、跨 flow 归属过滤（含旧行 main 语义）、WB_FLOW 钉 CLI 不钉 hook。全部在 feature-b 存活的完整链路里跑，尾行清单含「跨 flow 窗口与归属」。

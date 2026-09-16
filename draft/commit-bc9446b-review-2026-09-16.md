# Commit bc9446b 评审：flow 归属首写闸门 + selfcheck 环境隔离修复（2026-09-16）

**评审对象**：`bc9446b feat(guard): flow 归属首写闸门 + 修复 selfcheck 环境隔离` —— `.claude/hooks/wb_guard.py`（+76/-5）、`.claude/hooks/wb_cli.py`（+19/-2）、`.claude/hooks/wb_selfcheck.py`（+20/-2）。

**方法与安全约束**：全部验证在 `/Users/wangpenghao/.cache/wbtmp/` 下的夹具与工作区快照副本（`wb-copy`）上完成，验后即删；真实工作区前后 `git status` 一致，`.workbench` 状态未动（任务数 13/11 不变）。验证手段：直接调用 `hook_pre_tool` 断言 exit code、经 `main()` 跑 CLI 断言输出、对比 `keep_source_mount` / `find_root` / `state_path` 的返回值。写入类探针全部用无害 dummy 文件名，未执行任何被拦命令。

**总体结论：方向正确，但有三处放行口让闸门实际强度显著低于代码表面，且 commit message 里的验证声明在本机不成立** —— `python3 .claude/hooks/wb.py selfcheck` 在本机默认环境下直接 `AssertionError`（`wb_selfcheck.py:538`），换 `TMPDIR` 到无软链分量路径才转绿。按本仓库自己的标准（AGENTS.md 是协作约定唯一正文；门禁声明要求可复现），建议补完下列 1–6 再合，或至少把「22/22 通过」的运行环境写清楚。

---

## 一、selfcheck 在本机是红的（可复现），根因是新闸门踩中了坐标系老毛病

```
$ python3 .claude/hooks/wb.py selfcheck        # TMPDIR=/var/folders/...（macOS 默认）
AssertionError: 未归属的主线程写产品源码应被首写闸门拦下    # wb_selfcheck.py:538

$ TMPDIR=/Users/wangpenghao/.cache/wbtmp/ python3 .claude/hooks/wb.py selfcheck
selfcheck 全部通过：……（22 项）
```

差别只在临时目录落在哪：本机 `/var` 是 `/private/var` 的软链。

**根因链**（不在新代码，是老毛病被新闸门第一次踩到）：

1. `wb_guard.py:453-457`（`_check_write_target`）：`target = resolve_target(cwd, raw, root)`，其中 `cwd` 来自 hook 载荷、**未 resolve**；`root` 来自 `find_root`、**已 resolve**。
2. `wb_bash.py:335-359`（`keep_source_mount`）：先 `norm.relative_to(rootr)` —— 未解析的 `/var/...` 对已解析的 `/private/var/...` 直接 `ValueError`；退到反查表后，表里存的是 `repo.resolve()`（已解析），拿它比未解析的输入字符串，仍不中 → 返回 `None`。
3. 于是 `resolve_target` 落到兜底 `p.resolve()`（`wb_guard.py:115-118`），跟随 `.source` 软链跑到根外，`rel` 不再是 `repos/.source/...` 形态。
4. `_attribution_gate` 的前缀判断（`wb_guard.py:86`）不命中，早退放行。夹具实测：root 不含软链分量时 `keep_source_mount` 正常返回挂载形态、闸门正常拦（exit 2）；含软链分量时返回 `None`、闸门放行（exit 0）。

**影响面不止 selfcheck**：任何 root 路径含软链分量的机器上，这道闸门是 **fail-open** 的（本仓库本体在 `/Users` 下没有软链分量，真跑能拦；但「整目录拷贝分发」到别的布局时就裸了）。旧断言「`repos/.source` 外部真实路径不应被 Workflow Guard 拦截」（`wb_selfcheck.py:532-534`）恰好不关心 `rel` 形态，所以同样的坐标系错位以前从不报错 —— 新闸门是第一个以 `rel.startswith(SOURCE_MOUNT)` 为判据的功能，把它暴露了。

**修复建议**（任选其一，均在 5 行内）：

- `resolve_target` 里把基目录先解析掉：`p = cwd.resolve() / raw` —— 注意解析基目录即可，尾部保持词法拼接，否则会把挂载软链本身跟随掉（挂载判定要的就是不跟随）。
- 或 `keep_source_mount` 两侧都归一：`norm = Path(os.path.normpath(str(p))).resolve(strict=False)` 与 `rootr` 同坐标系后再比。

**commit message 的严重性说轻了**。message 把修复定性为「selfcheck 在真实会话内跑不再被 find_root 劫持 tmp 隔离而假红」。实际验证（在 `wb-copy` 快照副本上跑修复前版本）：

- `CLAUDE_PROJECT_DIR=<副本>` 时确实红，但红在 `wb_selfcheck.py:142`（门禁断言），**红得早，没走到后面会改状态的 `task add` / `contract lock` 那批调用** —— 副本 `.workbench` 文件数前后不变（15 → 15）。
- 但 `quiet()` 走 `main()`，全部 CLI 命令的 root 取自 `find_root()`（env 优先：`wb_cli.py:121/254/335/457/583/624/…` 共 12 处），一旦前面的断言通过，这些状态写入就会**打到 env 指向的根（真实工作区）**，而不是 cwd 所在的 tmp 夹具。实测 `cd /tmp && CLAUDE_PROJECT_DIR=<副本> wb.py task add --title "env-first proof" …` 成功写进了副本的 state.json —— 命令落点完全由 env 决定。
- 所以这不是「只是假红」：修复前 selfcheck 把「命令落到真实工作区」这类事故（本仓库 T8 执行记录原文就写着「主线程隔离测试夹具 WB_ROOT 未生效……命令落到真实工作区」）从夹具层挪到了 selfcheck 层。修复本身值得做且方向正确，但定性应为「修复 selfcheck 的 root 劫持导致隔离失效、状态写入打到真实工作区」，而不是「修复假红」。

---

## 二、闸门强度：只看命令文本形式，不看归属是否真发生

解锁标记在 `PreToolUse` 里落盘（`wb_guard.py:787-790`，命令执行**之前**），判据是对原始 `cmd` 跑裸正则 `\bwb\.py\s+flow\s+(?:switch|new|attribute)\b`（`wb_guard.py:51`）。实测四个方向：

| 动作（Bash 工具，带 session_id） | 实测结果 |
| --- | --- |
| `echo 'see docs: wb.py flow new <name>'` —— 纯回显提及 | 解锁，随后写产品源码放行 |
| `wb.py flow switch definitely-not-a-flow` —— CLI 本身 exit 1 失败 | 解锁，放行 |
| 终端里直接跑 `wb.py flow attribute --adhoc --reason r`（不经 Bash 工具，无 hook 载荷） | 无标记，写仍被拦 |
| `WB_FLOW=<不存在的 flow>` | 解锁（`_session_attributed` 只校验 `_FLOW_NAME` 格式，不校验 flow 存在） |

即「本会话**提到过**归属命令」= 已归属。`hook_pre_tool` 自己就解释了为什么裸正则不够（`wb_guard.py:792-795`）：heredoc 正文不是命令的一部分、正文里的灾难字面量不该误拦 —— 同一逻辑对归属命令同样成立，`grep -rn 'wb.py flow new' docs/` 也实测解锁了。

仓库里已有现成的正确写法没被复用：`_is_task_start`（`wb_guard.py:710-726`）用 `_split_pipeline` + `shlex` 分词、按 `wb.py` token 后面的实际参数判定，并且同时认 `wb` 这个软链名。新闸门用裸正则的结果是 `wb flow new x` 反而不解锁（`wb.py` 字面匹配不上）。

**修复建议**：照抄 `_is_task_start` 的词法解析（并补 `wb` 别名）；更彻底的做法是把标记落盘挪到 PostToolUse、只看 exit 0 —— 「命令真的成功执行过」才是归属发生的证据。`_session_attributed` 的 `WB_FLOW` 分支可顺手加 `flow_dir(root, v).is_dir()` 校验。

---

## 三、CLI 提示语断言了它做不到的事

`wb_cli.py:1064`：

```
已登记本会话为 ad-hoc 归属（不建 flow）：<reason>
```

但标记只能由 hook 写（CLI 拿不到 session_id —— 同函数上方注释 `wb_cli.py:1061` 自己说了「标记由 hook 见到本命令时写（那里才有 session_id）」）。用户在**自己终端**里跑这条命令（不经 Bash 工具载荷），得到成功提示，回到会话里写产品源码照样被拦。实测确认：CLI exit 0、审计落账，`sessions/` 无标记，同 session_id 的写入仍 exit 2。

顺带：`log(st, "flow_attribute_adhoc", flow=pointer_flow(root), …)` 把这条「不建 flow」的豁免记到了**指针当前 flow** 的 `audit.jsonl` 里（实测落 `flow: main`）—— 流水账语义自相矛盾：声明不属于任何需求线的动作，账记在某条需求线名下。

**修复建议**：要么把提示语改准（「已记账到审计流水；本会话解锁须经由会话内 Bash 工具执行时生效」），要么改成 flow 级持久记录（如 `.workbench/sessions/` 之外的显式豁免台账），闸门改为读它 —— 后者同时消灭第二条「终端 CLI 不解锁」的割裂。

---

## 四、`session_id` 直接当路径分量，无校验

`wb_guard.py:66`：

```python
(d / session_id).write_text(now(), encoding="utf-8")
```

`session_id` 未过任何格式校验。夹具实测：`session_id="../flows/main/state.json"` 时，`Path` 拼接把 `<root>/.workbench/flows/main/state.json` 覆盖成一个时间戳（夹具当场报「state.json 无法解析」），且随后闸门因「文件存在」而放行。同样形态可以覆盖 `frozen`、`unlock`、任意 `.workbench` 下的状态文件。

定性：hook 载荷由 harness 给、agent 自己控制不了这个字段，**不是可越权路径**；但它是「经 PreToolUse 写进冻结状态目录」的动作，且同一个工作台在 `wb_core.py:67` 就为 flow 名备好了 `_FLOW_NAME`（注释原文：「防 ../ 穿越进别的目录」）—— 同类输入两种标准。

**修复建议**：一行 —— `if not _FLOW_NAME.fullmatch(session_id): return`（`mark_session_attributed` 与 `_session_attributed` 两处都要）。

**治理问题**：`.workbench/sessions/` 是新引入的状态目录，(a) 不在 frozen 清单里，任何角色都能直接写（在受守前缀 `.workbench/` 下，但裸 `*` glob 跨 `/` 的收窄只对「显式以该前缀开头」的模式生效 —— 自查 `role_scopes` 是否有误伤面）；(b) 每个会话一个文件，无回收策略；(c) 「状态只能经 wb.py 改」（AGENTS.md 硬规则 1）现在多了一个例外目录，语义边界应明写一条。

---

## 五、`flow attribute` 漏进特权子命令层

`PRIVILEGED_WB`（`wb_guard.py:566-574`）列了 `flow new/switch/remove`，新增的 `flow attribute` 没进。实测：

- backend-developer 跑 `python3 wb.py flow new x` → exit 2（拦）
- backend-developer 跑 `python3 wb.py flow attribute --adhoc --reason y` → exit 0（放行）

`flow attribute` 与 `flow new` 同为「调度决定 + 状态写入」（声明本会话不建 flow，直接改产品源码的口子），角色不该有。要么补进表，要么在表旁写明为什么角色可以有。

---

## 六、文档与代码现在互相打脸

1. `AGENTS.md:127`：「`export WB_FLOW=<名>` 钉死（**只影响 wb.py 命令，hook 与守卫不受它影响**）」—— 现在 `_session_attributed`（`wb_guard.py:73`）直接读 `os.environ["WB_FLOW"]`。同文件 `cmd_hook`（`wb_guard.py:1114`）刚 `set_flow_override(None)`，注释原文：「守卫与 hook 是工作区级视角，不跟调用方 shell 的 WB_FLOW 走」。
2. 新闸门、`flow attribute --adhoc` 这个「不走流程」的新出口，`AGENTS.md`（「何时走流程」节、权限守卫清单）与 `docs/permissions.md` 全无记录 —— `grep -rn "首写闸门\|flow attribute\|adhoc" AGENTS.md docs/` 零命中。AGENTS.md 自我规定是协作约定唯一正文，行为变了正文必须跟着变。
3. 工作区里未提交的 `AGENTS.md` / `docs/permissions.md` 改动是 Codex matcher 那条，与本次 commit 无关，但说明文档层已经有排队中的修订 —— 本次该一起改。

---

## 七、零碎

- **闸门把挂载点创建/删除也拦了**。实测未归属会话下：`ln -s /srv/repo repos/.source/proj/repo1` → 2；`mkdir repos/.source/proj/repo3` → 2；`rm -rf repos/.source/proj/repo1` → 2；`git clone … repos/.source/proj/repo2` → 0（clone 目标解析不出写形态）。拒绝话术是「先别写**产品源码** repos/.source/proj/repo1」—— 用户在初始化工作区（`repos_apply.py` 之外的 `ln -s` 手工挂载）时会撞上且话术误导。要么接受并写进文档（「挂载点操作也要求先归属」），要么闸门只在 Write/Edit/apply_patch 路径启用（Bash 路径的写目标判定粒度本来就粗，见 AGENTS.md「已知边界」的自述）。
- `.workbench/sessions/` 不在 frozen 清单里（`FROZEN_ALWAYS` 未含），与第四节的治理问题合并处理。

---

## 做得对的地方

- **selfcheck 隔离修复是真 bug 修复**，不是粉饰：T8 执行记录原文就是同一失效模式的真实事故（「主线程隔离测试夹具 WB_ROOT 未生效……命令落到真实工作区」），`find_root` 的 env 优先设计（`wb_core.py:41-58`）在「真实会话里 CLAUDE_PROJECT_DIR 恒设」的前提下确实让 tmp 隔离失效。pop 三个变量 + 注释解释为什么 pop，写法克制。
- `session_id` 缺失时 fail-open 而非制造无 escape 死锁（`wb_guard.py:91-93`），方向正确 —— 守卫的逃生设计一贯如此。
- 三条新断言覆盖了闸门的三个分支（未归属拦 / 无 session_id 放 / 归属后放），断言写法与既有风格一致。
- `flow attribute` 强制 `--reason`、无 `--adhoc` 时给出指路错误信息，CLI 侧防御完整。
- 闸门挂在 `_check_write_target` 顶部（`wb_guard.py:459-460`），Write/Edit、Bash 解析目标、apply_patch 三条路径一并覆盖，接入点选对了。
- 注释延续了本仓库「写为什么、写边界」的风格，`_attribution_gate` 头部的动机说明（SessionStart 只是文字规劝，拦不住）准确有力。

---

## 建议的最小补丁（合计约 30 行 + 文档）

1. **坐标系**（第一节）：`resolve_target` 解析基目录，或 `keep_source_mount` 两侧归一 → selfcheck 本机转绿，闸门在含软链分量的 root 上真生效。这是唯一一条「不改就不算功能完成」的。
2. **归属判据**（第二节）：词法解析（复用 `_is_task_start` 的做法 + `wb` 别名），标记落 PostToolUse 只看 exit 0；`WB_FLOW` 分支校验 flow 存在。
3. **提示语**（第三节）：`flow attribute` 输出改准，或改 flow 级台账让闸门读它。
4. **输入校验**（第四节）：`_FLOW_NAME.fullmatch(session_id)`，两处。
5. **特权层**（第五节）：`PRIVILEGED_WB` 补 `("flow", "attribute")`。
6. **文档**（第六节）：AGENTS.md 修 127 行那句 + 「何时走流程」与权限守卫清单补 ad-hoc 出口；docs/permissions.md 补闸门；`.workbench/sessions/` 的治理（frozen 与否、回收策略）写一条边界。

---

## 复现附录

环境：macOS 14（Darwin 23.2.0），Python 3.13，本机 `TMPDIR=/var/folders/lf/…/T/`（`/var` 软链到 `/private/var`）。

```bash
# 1. 本机默认环境：selfcheck 红
python3 .claude/hooks/wb.py selfcheck        # AssertionError @ wb_selfcheck.py:538

# 2. TMPDIR 无软链分量：全绿
mkdir -p /Users/<you>/.cache/wbtmp
TMPDIR=/Users/<you>/.cache/wbtmp/ python3 .claude/hooks/wb.py selfcheck

# 3. 坐标系根因（直接调 hook 函数）
#    root 含软链分量时 keep_source_mount(未解析路径) 返回 None
#    → resolve_target 跟随软链出根 → rel 非 repos/.source/** → 闸门放行

# 4. 裸正则解锁（夹具，root 无软链分量）
#    Bash: echo 'see docs: wb.py flow new <name>'   → sessions/<sid> 落盘，后续产品码写入 exit 0
#    Bash: wb.py flow switch <不存在>（CLI exit 1）→ 同上
#    终端直跑 wb.py flow attribute --adhoc（无载荷）→ 无标记，写入仍 exit 2

# 5. session_id 穿越写（夹具）
#    mark_session_attributed(root, "../flows/main/state.json")
#    → <root>/.workbench/flows/main/state.json 内容变为时间戳，随后写入 exit 0

# 6. 特权层缺口（夹具）
#    agent_type=backend-developer:
#      wb.py flow new x                    → exit 2
#      wb.py flow attribute --adhoc        → exit 0
```

探针脚本与副本验后已清理；本评审未改动本仓库任何文件。

---

# 复审：`d7dd7ac` 修复（2026-09-16）

**复审对象**：`d7dd7ac fix(guard): 修复 bc9446b 评审 P0–P3 六项归属闸门缺陷` —— `.claude/hooks/wb_guard.py`（+48/-10）、`.claude/hooks/wb_cli.py`（+9/-5）、`.claude/hooks/wb_selfcheck.py`（+39/-0）。

**方法与约束**：与首次评审同法。夹具在 `/Users/wangpenghao/.cache/wbtmp/`，工作区快照副本 `wb-copy2`（用于「把修复回退掉」的对照实验），验后即删；真实工作区状态未动。

**结论：六项里 5 项修对且已实测确认（#1 #2 #3 #5 #6），#4 挡住了穿越但引入了一个新的硬锁面；上一轮 §6 的文档项只完成一半（代码与文档的矛盾消解了，闸门本身仍未进正文）；§7 未处理（本就列为「零碎」，可接受）。修复手法克制 —— 坐标系那处是 5 行，词法判定复用既有 `_wb_invocations`，没有新增抽象。发现 1 个新 P1（会话 id 格式不符即无 escape 死锁）与 1 个新 P2（被特权层拒绝的角色调用仍会解锁主会话）。**

---

## 逐条核对

| # | 上一轮问题 | 状态 | 实测证据 |
| --- | --- | --- | --- |
| 1 | 坐标系 fail-open（软链 root 上闸门放行） | **已修** | 默认环境 `python3 .claude/hooks/wb.py selfcheck` 转绿（改前 `AssertionError @ wb_selfcheck.py:538`）；把 `resolve_target` 里新增的 `cwd.resolve()` 回退掉后 selfcheck 立刻复红 |
| 2 | 裸正则解锁（echo/grep 提及即解锁） | **已修** | 14 条命令真值表全对：`echo 'run: wb.py flow new x'`、`grep`、`cat <<EOF` 正文、`wb.py flow list/remove` 均不解锁；`wb flow new x`（软链别名）、`python3 .claude/hooks/wb.py flow switch main`、`cd repos && wb flow new x` 正确解锁 |
| 3 | CLI 提示语断言做不到的事 + 审计字段自相矛盾 | **已修** | 文案改为「解锁在 AI 经会话内工具执行本命令时由 hook 落标记生效；在终端手动直跑只记账、不解锁」；审计字段 `flow=` 改 `pointer_at=`（语义变成「声明时指针停在哪」） |
| 4 | `session_id` 当路径分量无校验 | **已修，但引入新问题** | `session_id="../flows/main/state.json"` 现在 exit 2 且不落标记（改前会把 state.json 覆盖成时间戳）；新增 `_SESSION_ID` 白名单 —— 代价见「新发现 A」 |
| 5 | `flow attribute` 漏进特权层 | **已修** | `agent_type=backend-developer` 跑 `flow attribute --adhoc` 现在 exit 2（改前 0） |
| 6 | 与 `AGENTS.md:127` / `set_flow_override(None)` 矛盾 | **已修（Option B）** | `_session_attributed` 不再读 `WB_FLOW`；实测置 `WB_FLOW=main` 后写产品码仍 exit 2。代码与文档现在一致 |
| 6b | 闸门 / ad-hoc 出口未进协作约定正文 | **未做** | `grep -rn "首写闸门\|flow attribute\|ad-hoc\|adhoc" AGENTS.md docs/ .claude/skills/` 仍零命中 |
| 7 | 挂载点创建/删除被误拦 | **未做** | 未归属会话下 `ln -s … repos/.source/proj/repo1`、`mkdir repos/.source/proj/repo3`、`rm -rf repos/.source/proj/repo1` 仍 exit 2（`git clone` 仍 0） |

---

## 新发现 A（P1）：`session_id` 格式不符 = 无 escape 死锁

`_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")` 挡穿越是对的，但它是**白名单 + fail-closed**：`mark_session_attributed` 遇到不匹配直接 `return`（静默），`_session_attributed` 也判 False。而闸门的逃生口只覆盖「`session_id` 缺失」（`wb_guard.py:91-93`），**不覆盖「存在但不符格式」**。

夹具实测（每种格式都跑了归属命令再写产品码）：

```
sid='4d8a43af-…-cd5b765197ec'   标记=True   归属后写=0
sid='session_2026-09-16_abc'    标记=True   归属后写=0
sid='sess@host:1234'            标记=False  归属后写=2   <-- 死锁
sid='a b'                       标记=False  归属后写=2   <-- 死锁
sid='会话-1'                     标记=False  归属后写=2   <-- 死锁
```

`session_id` 是 harness 给的、用户和模型都改不了，命令却会报告成功（CLI exit 0、审计落账、提示「已记账」），随后每一次产品源码写入都被拒且没有出口。这与 `_attribution_gate` 自己写下的原则（「绝不把无 escape 的死锁塞给用户」）冲突。

同类死锁还有一个入口：`mark_session_attributed` 的 `except OSError: pass`。夹具把 `.workbench/sessions` 占位成普通文件后，`flow attribute` 仍 exit 0，产品码写入仍 exit 2 —— 磁盘满、权限、只读挂载都会走到这里。

**修法（比白名单更省事）**：不要校验格式，改为**哈希成安全文件名** —— `name = hashlib.sha256(sid.encode()).hexdigest()[:16]`。穿越问题从根上消失（输出恒为 16 位 hex），任何 harness 的 id 格式都能用，白名单带来的锁死面一并消失。两处（`mark_session_attributed` / `_session_attributed`）改成同一个 helper 即可。

或者保留白名单但补一条 fail-open：sid 存在却不匹配时按「无法追踪」放行，与缺 sid 同一条路径。

## 新发现 B（P2）：被拒绝的角色调用照样解锁主会话

落标记在 `hook_pre_tool` 的 SHELL 分支**开头**（`wb_guard.py:824-826`），而特权层检查在同分支的**后面**（`wb_guard.py:~860`）。实测：

```
agent_type=backend-developer 跑 `python3 wb.py flow new sneaky`
  -> exit=2（特权层正确拦下）
  -> 但 sessions/<sid> 标记已落 = True
```

而 `session_id` 是主线程与 subagent **共享**的 —— 这是本仓库自己的文档写明的（`docs/permissions.md:273`：「`session_id` 反而是共享的」；`docs/architecture.md:279` 同）。于是一个角色 subagent 只要发起一次（哪怕被拒）`flow new/switch/attribute`，编排者的归属闸门就被解开了。闸门的立意是「把归属从 prompt 规劝变成机制硬前置」，这一条让它的前置可以被下游角色单方面解除。

**修法**：把 `_is_attribution_cmd` 的落标记挪到特权层检查之后，或限定 `not (data.get("agent_id") or data.get("agent_type"))`（与 `_attribution_gate` 只管主线程对称）。

## 附带修好的第二处（commit message 未提）

同一个 `cwd.resolve()` 顺手修好了产物流水账里的路径。挂载点是软链、`cwd` 又是软链路径时，改前 `hook_post_tool` 记下的是**根外路径**：

```
已修复    artifact-log path = 'repos/.source/proj/repo/main.py'
修复前    artifact-log path = '../wb-checkout-808lfclj/main.py'
```

`merge_artifacts` 是按写入范围前缀匹配的，`../wb-checkout-…` 这种 rel 匹配不上任何 `repos/.source/**` 范围 —— 也就是说在软链布局 + 软链 cwd 下，`task done` 的产物归属归并会静默丢记录。这条值得写进 commit message 或回归断言（现在没有断言覆盖 artifact-log 的 rel 形态）。

## 值得肯定的地方

- **#1 的修法选得准**：解析基目录、尾部保持词法拼接 —— 正是 `keep_source_mount` 需要的坐标系（跟随后缀会把挂载软链本身跟掉，判定就没了）。注释把这个取舍写清楚了。
- **#2 复用 `_wb_invocations`** 而不是再造一套解析，词法判定与 `_is_task_start` 同一套；真值表 14/14 正确，`wb` 别名、heredoc 正文、pipeline 串联都覆盖到了。
- **#6 选了 Option B（WB_FLOW 不解锁）**，而不是给闸门加特例 —— 守卫回到「工作区级不变量」，与 `cmd_hook` 的 `set_flow_override(None)` 和 `AGENTS.md:127` 三处一致，这是正确的一侧。
- **把「发起过 ≠ 成功」的残余上限用 `ponytail:` 注释显式标注并给出升级路径**（挪 PostToolUse 只看 exit 0），符合本仓库既有惯例（`wb_core.py:1334` 同款注释），没有假装已经解决。
- 六条新断言落进 selfcheck，覆盖软链 cwd / echo 不解锁 / wb 别名 / 穿越 sid / 角色 attribute / WB_FLOW 不解锁，每条都对得上一个修复点。

## 一处注释事实错误

`wb_selfcheck.py` 新增断言的注释写「本机 /var 非软链，故造一个指向 tmp 的软链当 cwd 复现」—— **本机 `/var` 是软链**：

```
$ ls -ld /var
755 /var -> private/var  11B
```

所以 `mkdtemp` 出来的 tmp 路径本身就带软链分量（这也正是修复前 selfcheck 在本机红的原因），新加的 `cw_link` 夹具在本机并不增加覆盖（把 `cwd.resolve()` 回退后，是上一轮就有的 `wb_selfcheck.py:538` 断言先红，新断言根本没轮到）。夹具本身无害且对「TMPDIR 不含软链」的机器有价值，但注释里的事实判断是错的，改一句即可。

## 残余（上一轮已列、本轮未动，均可接受但该记账）

1. **闸门仍未进协作约定正文**（上一轮 §6b）。行为已经变了 —— 主线程写 `repos/.source/**` 前多了一道硬前置，`flow attribute --adhoc` 成了新的「不走流程」出口 —— 而 `AGENTS.md` 的「何时走流程」节与权限守卫清单里都没有它。本仓库自己规定 AGENTS.md 是协作约定唯一正文。
2. **`.workbench/sessions/` 是新状态目录**：不在 `FROZEN_ALWAYS`（`wb_const.py:288`）里；每个会话一个文件、无回收；「状态只能经 wb.py 改」（硬规则 1）现在多了一个由 hook 直写的例外目录。建议在 AGENTS.md 的已知边界里写一条。
3. **挂载点误拦（§7）**：拒绝话术是「先别写产品源码 repos/.source/proj/repo1」，而用户在做的是 `ln -s` 挂载，话术误导；要么放行 Bash 路径的挂载形态操作，要么把话术改成「挂载点操作也算归属前置」。
4. **标记只存时间戳，不记 flow**：闸门只判有无，事后无法回答「本会话归属到哪条 flow」。并行场景下 `flow switch` 会移动共享指针，而唯一不动共享状态的 `flow attribute --adhoc` 语义上恰恰是「不建 flow」—— 钉了 `WB_FLOW` 的并行会话现在只能靠这条「假声明」过闸门。若要收严，可让 hook 用同一套词法把 flow 名写进标记内容（`flow switch B` → 标记内容 `B`），既留证也给了非侵入的确认入口。

## 复审后的最小补丁（3 处，约 10 行）

1. **A**：`session_id` 改哈希成安全文件名（或补 fail-open 分支）—— 消掉新引入的硬锁。
2. **B**：落标记挪到特权层检查之后 / 限定主线程 —— 堵住「被拒的调用也解锁」。
3. **注释**：`wb_selfcheck.py` 里「本机 /var 非软链」改掉；顺手给 artifact-log 的 rel 形态补一条断言。

## 复审复现附录

```bash
# 1. 默认环境 selfcheck 已绿（改前红在 wb_selfcheck.py:538）
python3 .claude/hooks/wb.py selfcheck

# 2. 回退验证：副本里删掉 resolve_target 的 cwd.resolve() 后 selfcheck 复红
# 3. 词法判定真值表：14 条命令（echo/grep/heredoc/别名/pipeline）全部符合预期
# 4. sid 格式：'sess@host:1234' / 'a b' / '会话-1' → 归属命令后仍 exit 2（死锁）
# 5. sessions/ 占位成普通文件 → flow attribute exit 0 但产品码写入仍 exit 2（死锁）
# 6. 角色 subagent 跑 flow new → exit 2 但标记已落（主会话被解锁）
# 7. artifact-log rel：软链布局下改前 '../wb-checkout-…/main.py'、改后 'repos/.source/…'
```

本轮同样未改动本仓库任何文件；探针与副本已清理。

---

# 三审：`b1b3a34` 修复（2026-09-16）

**三审对象**：`b1b3a34 fix(guard): 归属闸门评审后续修复（session_id/主子线程/sessions 治理/诚实并行归属）` —— `.claude/hooks/wb_guard.py`（+48/-12）、`wb_cli.py`（+25/-6）、`wb_const.py`（+4/-1）、`wb_selfcheck.py`（+50/-3），文档 `AGENTS.md`、`docs/permissions.md`、`draft/open-issues-2026-09-10.md`。

**方法**：在二轮基础上加了一道**变异测试** —— 把每处修复逐个改坏，看 selfcheck 是否变红。这一层能区分「断言写了」与「断言真的在守」，本轮的两个主要发现都来自它。夹具与副本验后即删，真实工作区状态未动（13 任务 / 11 完成，与开工一致）。

**结论：上轮 6 项 + 2 处残余全部落地，方向与实现都对，文档也同步到位（这是三轮里第一次代码与文档完全自洽）。变异测试 10 处里 8 处被断言捕获。剩两个值得修的问题：一个「断言写了但没在守」（§N1，变异能证明），一个「hook 会在未初始化的目录里建 `.workbench/`」（§N2，实测复现）。**

---

## 逐项核对（含变异测试）

| 上轮问题 | 状态 | 证据 |
| --- | --- | --- |
| 二轮 A：`session_id` 白名单 fail-closed 死锁 | **已修（换哈希）** | 5 种 id（穿越、`a/b`、`sess@host:1234 会话-1`、300 字符、uuid）全部映射成 16 位 hex；特殊字符 id 跑归属命令后写产品码 exit 0（改前死锁）；把 `_session_key` 改回原样落盘 → selfcheck 变红（M5 捕获） |
| 二轮 B：被拒的角色调用解锁主会话 | **已修** | 角色跑 `flow new` exit 2 且**不再**落标记，主线程随后写产品码仍 exit 2；有 `agent_id` 无 `agent_type` 的载荷同样不落标记；去掉主线程限定 → selfcheck 变红（M2 捕获） |
| 残余 2：`sessions/` 治理 | **已修** | `.workbench/sessions` 已在 frozen 清单（`frozen_paths` 实测含该条）；四种伪造形态全拦：Write / 角色 `python3 -c` / 角色 `echo >` / 主线程 `mv` 到 sessions；移出 `FROZEN_ALWAYS` → selfcheck 变红（M6 捕获） |
| 残余 2b：标记回收 | **已修** | 40 天前标记在落新标记时被删、新标记保留、同目录的子目录不被当文件删；去掉 `_prune_session_marks` → selfcheck 变红（M4 捕获） |
| 残余 4：并行会话只能用「不建 flow」的假声明 | **已修** | `flow attribute --flow feature-b`：校验存在性（不存在 exit 1）、穿越名 `../../etc` 被 `flow_dir` 的名字校验 die、与 `--adhoc` 互斥、**指针 main → main 未动**、经 hook 能解锁；分别去掉存在性校验 / 互斥 → selfcheck 变红（M7、M8 捕获） |
| 残余 1：闸门未进协作约定正文 | **已修** | `AGENTS.md:31`（新增「产品源码写入有机制闸门」段）、`:131`（`flow new/switch/remove/attribute` 特权）、`:188`（闸门条目）；`docs/permissions.md` 新增「归属首写闸门与 `.workbench/sessions/`」节；`draft/open-issues-2026-09-10.md` 记 R2/R3 处理结果。抽查文档断言与实际行为逐条对得上（标记路径 `<sha256(sid)[:16]>`、fail-open 两个触发条件、只主线程落标记、WB_FLOW 不解锁） |
| 二轮 §7：挂载点创建/删除被误拦 | **未做（上轮即列为「可接受」）** | `ln -s` / `mkdir` / `rm -rf` 到 `repos/.source/**` 仍 exit 2；`git clone` 仍 0 |

**变异测试总表**（10 处，★ = 断言没守住）：

```
M1  --flow 移动共享指针                  ★漏过   ← §N1
M2  子线程也能落标记                      捕获
M3  去掉 sessions 非目录时的 fail-open    ★漏过   ← §N4（该分支无断言）
M4  去掉标记回收                          捕获
M5  哈希退回原样落盘                      捕获
M6  sessions 移出 FROZEN_ALWAYS          捕获
M7  去掉 --flow 存在性校验                捕获
M8  去掉 --flow/--adhoc 互斥              捕获
M9  WB_FLOW 重新解锁闸门                  捕获
M10 去掉 resolve_target 的 cwd.resolve()  捕获
M11 归属判据退回裸正则                    捕获
M12 闸门整体失效                          捕获
M13 去掉「工作台未初始化则不拦」早退        ★漏过   ← 该早退无断言（见 §N2 相关）
M14 去掉 session_id 缺失放行              捕获（由既有断言兜到）
```

---

## N1（P2）：`--flow` 的「不移动共享指针」断言是空的

`wb_selfcheck.py:1921` 起的 R3 断言段，在断言前指针**已经停在 `feature-b`**（上面第 1891 行刚 `flow switch feature-b` 过），而归属目标也是 `feature-b`。于是「指针没动」这个断言恒真 —— 实现若真的移动指针，指针也还是 `feature-b`。

变异证明：把 `flow attribute --flow X` 改成顺手 `set_current_flow(root, X)`（真实代码指针 main → main 未动，变异版 main → feature-b **移动了**），selfcheck 依然**全绿**。

**修法**（两行）：让归属目标与当前指针对不上 —— 先 `flow switch main`，再 `flow attribute --flow feature-b`，然后断言指针仍是 `main`。当前写法把被测行为与前置状态设成了同一个值，等于没测。

## N2（P2）：hook 会在**未初始化**的目录里创建 `.workbench/`

`hook_pre_tool` 里落标记的调用（`wb_guard.py:857-862`）在任何 `state_path` 检查**之前**，而 `mark_session_attributed` 是 `d.mkdir(parents=True, exist_ok=True)`。夹具实测：

```
空目录 /…/wb-for3-xxxx（无 .workbench，也无任何祖先有）
  跑之前: []
  主线程 Bash 执行 `python3 wb.py flow <归属命令>` 之后: ['.workbench']
    .workbench/sessions/1df2388ac298de49      ← = sha256("s-foreign-3")[:16]
  （`flow list` 这类非归属命令不会触发）
```

`find_root` 在找不到 `.workbench/` 时返回 cwd，于是标记落到了「一个不是工作台的目录」里，凭空多出一份 `.workbench/` 结构。这恰好是本仓库自己警惕的那类污染 —— AGENTS.md 的「已知边界」专门写了「`repos/` 下若出现自带的 `.workbench/`（如误操作 init 进仓库）」。此时闸门本身是早退放行的（`state_path` 不存在），标记毫无用处，纯副作用。

**修法**（一行）：`mark_session_attributed` 开头加 `if not state_path(root).is_file(): return`（与 `_attribution_gate` 的早退判据对齐），或把调用点移进同一条件。

顺带：变异 M13（去掉闸门里「工作台未初始化则不拦」的早退）也没有断言守着。这处早退在真实路径上很重要 —— 没有它，非工作台仓库里写 `repos/.source/**` 会被拦，而拒绝话术给的三条出路（`flow list`/`switch`/`new`）在那个目录里全都跑不通。

## N3（P3）：`--flow X` 的审计落在**指针 flow** 的账上

夹具实测（指针 = main）：

```
wb.py flow attribute --flow feature-b
  main/audit.jsonl      ← {"event":"flow_attribute","flow":"feature-b"}   ★记在 main 账上
  feature-b/audit.jsonl ← 不含 flow_attribute
```

`cmd_flow` 用的是 `load_state(root, lock=True)`（= 指针 flow 的 state）。上一轮刚把 `--adhoc` 的 `flow=` 改成 `pointer_at=` 来消除「审计自相矛盾」，`--flow` 却把「本会话归属到 feature-b」这件事记进了 main 的流水账 —— 而 `--flow` 存在的唯一理由就是给**指针不在自己这条线**上的并行会话用。于是并行场景下两个会话的归属记录都堆在 main 的 audit 里，目标 flow 的账上一片空白。open-issues 里写的「归属的诚实记录落在审计流水」方向对，但落错了账本。

**修法**：`load_state(root, flow=args.attr_flow)` 写目标 flow 的 state（该 flow 存在性已校验过），或同时记两处。

## N4（P3）：fail-open 分支无断言

`_session_attributed` 里「`sessions/` 被占成普通文件 → 放行」是二轮新增的逃生分支，没有任何断言覆盖（M3 变异后 selfcheck 仍全绿）。该分支当前**触发面是被堵死的** —— 夹具实测三种造法全被冻结规则拦下：

```
rm -rf .workbench/sessions && touch .workbench/sessions   -> 2
rmdir .workbench/sessions && touch .workbench/sessions    -> 2
ln -sf /dev/null .workbench/sessions                      -> 2
```

所以它现在是一段「防守性的、不可达的」代码。不可达没问题，但要么补一条断言（直接构造 `sessions` 为普通文件后断言放行），要么在注释里写明「当前由冻结规则保证不可达，留作纵深」。另外 `Path.exists()` 对**断链软链**返回 False，那种形态会掉进 `(d/key).is_file()` → 拒 → 而 `mkdir(exist_ok=True)` 又会抛 `FileExistsError`（被吞）→ 死锁；只是造出这个形态同样被冻结规则挡住。

## N5（P3）：二轮指出的错误注释仍在

`wb_selfcheck.py:550` 仍写着「本机 `/var` 非软链」，而本机 `ls -ld /var` = `→ private/var`。相应地，新增的 `cw_link` 软链断言在本机是被**更早的**那条断言挡在前面的（M10 变异触发的是 `wb_selfcheck.py:538` 的老断言，新断言没轮到）——它在 TMPDIR 不含软链分量的机器上才是真正生效的那条。夹具本身有价值，改掉注释里的事实判断即可。

## N6 / N7（P4）

- `flow attribute --flow feature-b --reason zz` 静默接受（exit 0），`--reason` 被忽略；`--adhoc` 缺 `--reason` 是硬错。要么对 `--flow` 也拒掉这个无意义组合，要么在 help 里说明它只对 `--adhoc` 生效。
- `AGENTS.md:31`（本轮新增的闸门摘要段）只列了 `flow switch`/`new` 与 `flow attribute --adhoc`，漏了新的 `--flow` 出口；`:188` 那条是完整的。摘要段是读者第一眼看到的地方，建议补齐。

---

## 这轮做得好的地方

- **`session_id` 从「白名单」换成「哈希」是选对了抽象**：白名单要同时解决穿越与死锁两个方向，怎么调都是二选一；哈希一次性消掉两类问题，还顺手删掉了那个正则与它的一堆注释。改动比被替换掉的代码更短。
- **落标记限定主线程**放在正确的位置（`hook_pre_tool` 的调用点，而不是 `mark_session_attributed` 内部），语义清楚：「谁有资格表态」是载荷层的事，不是落盘层的事。
- **`FROZEN_ALWAYS` 加 `sessions` 而不动 hook 的写路径**：`_check_write_target` 只管工具与 Bash 的写目标，hook 的进程内直写不受影响 —— 实测 `mark_session_attributed` 在冻结后仍能落标记。用既有机制收严、不加特例，是这轮最干净的一处。
- **`_prune_session_marks` 的防御写得到位**：逐个吞 `OSError`（竞态删除不打断回收）、只删 `is_file()`（不误删目录）、跑在归属命令路径上（不进热路径）。
- **文档第一次做到与代码完全自洽**：三份文档 + open-issues 的记账，抽查的每条断言（标记文件名、两个 fail-open 条件、只主线程、WB_FLOW 不解锁、`--flow` 不动指针）都与实测一致；并且**诚实写下取舍** ——「会话标记仍是存在性判定，未把 flow 名写进标记内容」。
- **open-issues 的写法值得保留**：先写「为什么可接受」，再写「将来怎么收严」，最后写「处理结果」——同一节里能看到判断的演进，而不是把结论覆盖掉。

## 三审后的最小补丁（4 处，约 8 行）

1. **N1**：R3 断言改成「指针在 main、归属到 feature-b、断言指针仍是 main」。
2. **N2**：`mark_session_attributed` 开头加 `if not state_path(root).is_file(): return`。
3. **N3**：`--flow X` 的审计写进 X 的 state（`load_state(root, flow=args.attr_flow)`）。
4. **N5**：删掉「本机 /var 非软链」那句；顺带 N4 的 fail-open 补一条断言或注释说明不可达。

## 三审复现附录

```bash
# 1. 默认环境 selfcheck 全绿（三轮均在此环境验证）
python3 .claude/hooks/wb.py selfcheck

# 2. 变异测试：改坏一处 → selfcheck 应变红；M1/M3/M13 变不红
#    M1  在 cmd_flow 的 --flow 分支前插 set_current_flow(root, args.attr_flow)
#        → 独立验证：真实代码指针 main→main，变异版 main→feature-b，selfcheck 仍全绿
#    M3  删 _session_attributed 里的 `if d.exists() and not d.is_dir(): return True`
#    M13 删 _attribution_gate 里的 `if not state_path(root).is_file(): return`

# 3. 哈希文件名：5 种 session_id 全映射为 16 位 hex；特殊字符 id 归属后能解锁
# 4. 未初始化目录：空目录里跑归属命令 → 凭空出现 .workbench/sessions/<sha256(sid)[:16]>
# 5. 伪造标记：Write / 角色 python3 -c / 角色 echo> / 主线程 mv 四种形态全部 exit 2
# 6. fail-open 触发面：三种把 sessions 变成普通文件的写法全部 exit 2
# 7. --flow 审计落点：指针 main 时 --flow feature-b → main/audit.jsonl 记 flow=feature-b
# 8. 主/子线程：角色跑 flow new exit 2 且不落标记；主线程随后写产品码仍 exit 2
```

三轮合计：**首轮 7 项发现（其中 1 项 P0 级 fail-open）→ 二轮 6 项修复 + 2 项新发现 → 三轮 6 项 + 2 项残余全部落地**，闸门从「形式判定 + 软链 root 上 fail-open」收敛到「词法判定 + 哈希会话键 + 主线程限定 + 冻结标记目录 + 诚实并行出口」。剩下的是断言质量（N1）、一处目录污染（N2）与三处记账/注释瑕疵，都不影响闸门本身是否成立。

---

# 剩余问题清单（三轮评审合并，2026-09-16）

按「要不要动手」分档。每条都给当前代码事实与验证方式；三轮里已修的不再列出。

## P2 —— 建议修（各 1–2 行）

| # | 问题 | 事实 | 修法 |
| --- | --- | --- | --- |
| R1 | `--flow` 的「不移动共享指针」断言恒真 | `wb_selfcheck.py:1921` 起：断言前指针已停在 `feature-b`（第 1891 行刚 switch 过），归属目标也是 `feature-b`。变异测试证明：把实现改成顺手 `set_current_flow`（指针 main→feature-b 真的动了），selfcheck 仍全绿 | 先 `flow switch main` 再 `flow attribute --flow feature-b`，断言指针仍是 `main` |
| R2 | hook 会在**未初始化**目录里创建 `.workbench/` | 落标记在任何 `state_path` 检查之前，`mark_session_attributed` 是 `mkdir(parents=True)`。夹具：空目录里主线程跑一次归属命令 → 出现 `.workbench/sessions/<sha256(sid)[:16]>`（`flow list` 不触发）。此时闸门本身早退放行，标记无用纯副作用，且与 AGENTS.md「已知边界」警惕的「repos/ 下出现自带 `.workbench/`」同类 | `mark_session_attributed` 开头加 `if not state_path(root).is_file(): return` |
| R3 | 两处文档说工具层两层守卫「不生效」，实际已生效 | 代码：`29f9255` 恢复，实测角色调 `Agent` → exit 2、角色调白名单外 `Skill` → exit 2，selfcheck 有对应断言。文档：`AGENTS.md:191`（「⚠ 这层在 d606944 重构中被移除，尚未恢复 —— 当前不生效」）、`AGENTS.md:192`（「⚠ 同上，这层当前也不生效」）、`docs/permissions.md:132`（「尚未恢复」）、`:138`（「**待恢复**」）、`:140`（「**待恢复**」）；`draft/open-issues-2026-09-10.md:177` 起 P0 节仍写「仍未恢复的两层」。**AGENTS.md 是协作约定唯一正文，读者会据此以为这两层没有执法** | 五处去掉过期声明，open-issues 的 P0 节按「已恢复」收尾（可沿用该文既有的「处理结果」写法） |

## P3 —— 记账即可

| # | 问题 | 事实 |
| --- | --- | --- |
| R4 | `--flow X` 的审计落在**指针 flow** 的账上 | `cmd_flow` 用 `load_state(root, lock=True)`（= 指针 flow）。夹具：指针 main 时 `flow attribute --flow feature-b` → `main/audit.jsonl` 记 `{"event":"flow_attribute","flow":"feature-b"}`，`feature-b/audit.jsonl` 不含该事件。`--flow` 唯一要服务的正是「指针不在自己这条线」的并行会话，于是并行归属全堆在 main 账上。修法：`load_state(root, flow=args.attr_flow)` |
| R5 | fail-open 分支无断言，且当前不可达 | `_session_attributed` 里「`sessions/` 被占成普通文件 → 放行」无断言（变异 M3 后 selfcheck 仍绿）。触发面被冻结规则堵死：三种造法（`rm -rf && touch`、`rmdir && touch`、`ln -sf /dev/null`）全部 exit 2。要么补断言，要么注释写明「由冻结规则保证不可达，留作纵深」 |
| R6 | 错误注释仍在 | `wb_selfcheck.py:550` 写「本机 `/var` 非软链」，实际 `ls -ld /var` = `→ private/var`。新加的 `cw_link` 断言在本机被更早的 538 行断言挡在前面（TMPDIR 不含软链分量的机器上才生效） |
| R7 | 挂载点创建/删除被闸门拦（二轮 §7，三轮未做） | 未归属会话下 `ln -s … repos/.source/proj/repo1`、`mkdir repos/.source/proj/repo3`、`rm -rf repos/.source/proj/repo1` 全 exit 2（`git clone` 仍 0）。拒绝话术说「先别写**产品源码** …」，而用户在做的是挂载，话术误导。要么放行 Bash 路径的挂载形态，要么把话术改成「挂载点操作也算归属前置」 |

## P4 —— 设计取舍 / 卫生

- **标记只存时间戳、不记 flow**：open-issues 已明写取舍（「闸门只需存在性，够用」）。副作用是事后无法回答「本会话归属到哪条线」。
- **标记不刷新 + 30 天 mtime 回收 → 长会话可能被重新锁**（实测）：`_prune_session_marks` 按标记文件 mtime 删，而标记只在归属命令时写一次、不随活动刷新。夹具：给会话标记设 31 天前的 mtime，另一会话做一次归属即触发回收 → 该会话标记消失、写产品码 exit 2。可自愈（重跑一次归属即可），不是死锁，但「活跃会话的标记被别人清掉」这条值得知道。
- **判定的是「发起过」而非「成功」**：`_is_attribution_cmd` 在 `PreToolUse` 落标记，实测 `flow switch no-such-flow`（CLI exit 1）照样解锁。代码里已用 `ponytail:` 注释标注上限与升级路径（挪 `PostToolUse` 只看 exit 0），属已知取舍。
- **仓库卫生**：根目录两个未跟踪文件 —— `payload.json`（内容是 `{"messages":[…],"model":"moonshotai/kimi-k3",…}` 形态的请求体，像一次 API 调用的落盘）、`.DS_Store`；另外工作区有未提交的 `.codex/hooks.json`（matcher 改 `.*` + timeout 30→15）与 `AGENTS.md`/`docs/permissions.md` 两处文案改动，其中后两个文件 `b1b3a34` 也改过（不同段落，不冲突，但同批文档改动建议一起收口）。

## 验证方式（本清单每条的复现命令）

```bash
# R1 变异：在 cmd_flow 的 --flow 分支前插 set_current_flow(root, args.attr_flow)
#     独立验证：真实代码指针 main→main，变异版 main→feature-b，selfcheck 均全绿（断言没守）
# R2 空目录里主线程跑 `python3 wb.py flow attribute --flow main` → 出现 .workbench/sessions/<hex>
# R3 直接调 hook：角色调 Agent / 角色调白名单外 Skill 均 exit 2（文档说 exit 0）
# R4 指针 main 时 --flow feature-b → main/audit.jsonl 记 flow=feature-b
# R5 三种把 sessions 变成普通文件的写法 → 全部 exit 2（分支不可达）
# R6 ls -ld /var
# R7 未归属会话 ln -s / mkdir / rm -rf 到 repos/.source/** → 全 exit 2
# P4 标记 mtime 设 31 天前 + 另一会话归属 → 标记被回收，原会话写产品码 exit 2
```

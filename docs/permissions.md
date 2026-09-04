# 权限模型

权限守卫是 `PreToolUse` hook，在每次 Write / Edit / NotebookEdit / MultiEdit / Bash 调用**之前**同步执行。退出码 2 阻止调用，stderr 内容回灌给模型作为拒绝理由。

## 为什么需要这一层

Claude Code 原生有两种权限机制，都不够：

| 原生机制 | 能做 | 不能做 |
| --- | --- | --- |
| agent 定义的 `tools:` 白名单 | 「这个角色不能用 Write」 | 「这个角色只能写 `tests/`」 |
| `settings.json` 的 `permissions.deny` | 静态路径与命令前缀黑名单 | 随当前角色变化的动态范围 |

角色隔离本质上是**动态**的：同一个 Write 工具，`pm` 调用时只能写产物目录，`backend-developer` 调用时可以写 `migrations/`。这只能在 hook 里判断。

另一个原生机制无法覆盖的点：`pm` 需要 Write（写 `requirements.md`），所以不能靠不给 Write 来阻止它改代码。必须按路径判。

## 四层拦截

`hook_pre_tool()` 按顺序检查，任一条命中即退出码 2。

### 第一层：项目根边界

```python
if target != rootr and rootr not in target.parents:
    hook_deny(f"写入越出项目根 {rootr}：{target}")
```

路径先 `resolve()`（展开符号链接与 `..`），再比对是否在项目根之下。挡住 `/etc/passwd`、`~/.ssh/`、`../../其他项目/`。

这是**信任边界上的检查，不做简化**。模型写错路径、被提示注入诱导、或误解相对路径基准时，这是唯一的物理防线。

### 第二层：冻结清单

```python
rel = os.path.relpath(target, rootr).replace(os.sep, "/")
frozen = read_frozen(rootr)
if rel in frozen or any(rel.startswith(f + "/") for f in frozen):   # unlock/ 是目录
    if rel not in unlocked_paths(rootr):
        hook_deny(...)
```

冻结清单 = 五个状态文件（`FROZEN_ALWAYS`）+ **所有已锁定的契约**。状态文件不可写是整套机制的地基：

- 能写 `state.json` → 能把 `gates` 全标成 `passed: true`，或改契约的 `sha` → 门禁与契约冻结一起作废。
- 能写 `role` → 能给自己换个权限大的角色 → 角色隔离作废。
- 能写 `frozen` → 能把自己想改的文件从清单里删掉 → 冻结作废。
- 能追加 `artifacts.jsonl` → 能把别人的改动记到自己名下 → 产物归属作废。
- 能写 `unlock/<契约名>` → 能给自己签发申报 → 申报制度作废。所以守卫连冻结路径的**子路径**一起拦。

`wb.py` 自己写它们不受影响，守卫只拦工具调用。

契约不可写是「技术方案与接口定义不能被随意修改」的实现方式，**对 owner 和主线程同样生效**，唯一写入路径是先申报解冻（[contracts.md](contracts.md#锁定即只读)）。清单里除了接口契约与 `design.md`，还有各阶段过门禁后自动登记的产物，所以「回头改上游需求」也是一次要写理由的申报。

`save_state()` 每次把清单落成 `.workbench/frozen`（纯文本一行一条），让 hook 不必解析整个 `state.json`。这个文件只是缓存，缺失**或为空**时 `read_frozen()` 从 `state.json` 现算。缺失那一半是升级路径：升级前建的项目没有 `frozen`，退化成「只保护状态文件」后契约的整条防线消失，且不报错。为空那一半是并发路径 —— 旧版就地重写这个文件，`truncate` 与 `write` 之间那一瞬清单是空的，而守卫只判路径在不在清单里，那一刻五条防线同时放行（含改 `role` 提权）。两半都有断言（删掉缓存后契约仍受保护；清空缓存后 Write 与 Bash 两条路仍拒绝）。清单本身现在原子替换，为空这条是纵深防御，见 [architecture.md](architecture.md#写入原子性与并发)。

### 第三层：解冻窗口

`.workbench/unlock/` 是目录，一份契约一个文件：文件名是契约名，内容是申报理由。第二层命中后放行的唯一条件：

```python
def unlocked_paths(root):
    names = read_unlocks(root)            # {契约名: 理由}，无窗口是 {}
    ...
    return {c["path"] for c in st.get("contracts", []) if c["name"] in names}
```

窗口的三个性质，每一个都是刻意的：

| 性质 | 为什么 |
| --- | --- |
| 一份窗口只对一份契约生效，但多份可以并存 | 解冻 `user-api` 不会顺带放开 `design-doc`，范围最小；而 `bump` 一份产物契约会给每个消费方各建同步任务，它们并行申报是常态，不是边界情况。分片键是契约不是 agent —— 按 agent 分片会把「两个 agent 同时改一份契约」变成合法操作，正好放开唯一真该拦的那种（[architecture.md](architecture.md#解冻窗口按契约分片曾是单文件记录一次纠错)） |
| 状态文件永不可解冻 | `unlocked_paths()` 只查 `contracts` 列表，`FROZEN_ALWAYS` 里那五个查不到 |
| 理由必填，且先于改动 | 事后补的理由都是给已发生的事找解释。`contract unlock` 不给 `--reason` 直接拒绝 |

`contract bump` / `contract lock` 只关自己那一份，不会收掉兄弟 agent 的窗口。`SubagentStop` 关全部，但只在没有任务处于 doing 时才关，否则先结束的 subagent 会把仍在跑的兄弟的窗口一起收掉。串行下这条兜住「一个 subagent 申报的窗口敞着让下一个用」；并行下要靠 `bump` / `lock` 自己关。

### 第四层：角色写入范围

```python
role = current_role(rootr, data)          # 载荷 agent_type 优先，取不到才读 .workbench/role
globs = st["role_scopes"].get(role)
rel = os.path.relpath(target, rootr).replace(os.sep, "/")
if rel.startswith(".workbench/"):         # 裸扩展名模式不得跨进状态目录
    globs = [g for g in globs if g.startswith(".workbench/")]
if not any(fnmatch.fnmatch(rel, g) for g in globs):
    hook_deny(f"角色 {role} 无权写 {rel}。允许范围：{', '.join(globs)}。…")
```

**角色取自本次调用的载荷，不是那个会被并行 subagent 互相覆盖的单文件。** subagent 的载荷带 `agent_type`（值等于 agent 定义 frontmatter 的 `name`，与 `ROLES` 同名），主线程不带。所以并行 develop 下前后端各自判定，与谁后启动无关（[architecture.md](architecture.md#角色锁曾经也是单文件已解决记录一次纠错)）。

**`.workbench/` 下的路径只认显式以 `.workbench/` 开头的模式。** 没有这一条时裸扩展名模式会跨进状态目录 —— `fnmatch` 的 `*` 跨 `/`（见 [architecture.md](architecture.md#路径匹配偏宽松)），所以 `*.md` 匹配 `.workbench/artifacts/clarify/requirements.md`，`*.json` 匹配 `.workbench/contracts/events.json`。两者都绕开本层的设计意图：产物目录按阶段隔离、契约只有 architect 能写。

第二层补不上这个缺口：它只认**已锁定**的契约，而强推过的阶段产物不冻结（那个阶段并没真做完）、还没 `lock` 的契约也不在清单里。所以「开发角色的写入范围不含 `.workbench/contracts/`」这条断言在收窄之前对 `*.json` 并不成立 —— 收窄不只是为新加的 `*.md` 铺路，它同时补掉了 `*.json` 一直存在的同类缺口。收窄只影响裸扩展名，各角色显式写出的 `.workbench/artifacts/<阶段>/**` 照常放行，两个方向都有断言。

角色取不到时**不做角色限制** —— 主线程如此，`agent_type` 不是角色名的内置 agent（`Explore` / `general-purpose` / `Plan`）在 `.workbench/role` 也缺失时同样如此。前三层仍生效，而阶段产物过门禁后是冻结契约（第二层），所以「无角色 = 无约束」不再意味着上游产物可以被随手重写。

各角色的默认范围见 [roles.md](roles.md#角色矩阵)。这里只记它的形状：**产物目录按阶段隔离**，不是给所有角色一个 `.workbench/artifacts/**`。这是第二层之外的纵深 —— 契约冻结挡「已定稿的东西被改」，阶段隔离挡「下游角色去改上游产物」，包括还没定稿的当前阶段产物。两者独立互补：`qa` 改 `design.md` 会被两层各自拦一次；阶段隔离只在守卫能判出角色时生效，冻结不依赖角色。

升级前建的项目 `role_scopes` 里存的是老的宽范围：

```bash
wb.py role scopes            # 看当前配置 + 冻结清单 + 解冻窗口
wb.py role scopes --reset    # 刷成 DEFAULT_ROLE_SCOPES（会覆盖定制过的范围，先存一份）
                             # 跨仓库布局下改按仓库前缀算 —— 只写裸默认值会把隔离改坏
wb.py config set role_scopes.backend-developer \
    '["server/**","migrations/**","internal/**",".workbench/artifacts/develop/**"]'
```

**跨仓库布局下「谁都没认领的仓库」会撞成本层的拒绝。** `repos/shared` / `repos/payments-core` 这类按目录名认不出归属的仓库落在所有角色范围之外 —— 是硬拦，不是放行。`init` 与 `role scopes` 会当场点名并给出手写认领的命令（`unclaimed_repos()`），所以撞上这类拒绝先跑一遍 `role scopes` 看有没有点名，而不是去改本层的判定。为什么宁可硬拦见 [architecture.md](architecture.md#跨仓库同一个语义的反面)。

### 拒绝信息要可操作

```
[工作台权限守卫] 拒绝：角色 qa 无权写 src/app.ts。
允许范围：.workbench/artifacts/verify/**, tests/**, test/**, e2e/**, spec/**, *.config.ts, *.config.js, *.config.mjs, pytest.ini, tox.ini。
确需跨界请交给对应角色，或 wb.py config set role_scopes.qa '<JSON 数组>'
```

三段：拒绝了什么、允许什么、怎么正确地做。只说「拒绝」会让 subagent 反复试同一件事。

「允许什么」那段打的是**对这个路径实际生效的**模式集合，所以撞上 `.workbench/` 收窄时它只列 `.workbench/artifacts/develop/**` 一条，而不是把二十个模式全倒出来让读的人自己排除。

**冻结文件的第三段按 owner 分岔。** 契约名从 `state.json` 反查填实，不给 `<契约名>` 占位符 —— 只有 `pm` 的定义里硬编码了 `artifact-requirements`，其余角色撞上自己那份阶段产物时只能猜，而「不许换等价写法绕」这条要求拒绝信息把该跑的命令给全。分岔的三种：

| 撞上的人 | 给什么 | 为什么不给另一种 |
| --- | --- | --- |
| 这份契约的 owner，或主线程（载荷无 `agent_type`） | 完整的 `contract unlock --name <实名> --reason` 与 `contract bump` | —— |
| 非 owner 的角色 | owner 是谁 + 报回编排者 + `task block <ID>` | 教它自己申报是错的：`bump` 会给每个消费方建返工任务，那是编排者的调度决定；而 `SubagentStop` 会在它结束时关掉悬挂窗口，留下一个改过但没定版的文件，下次 `contract verify` 报漂移 |
| `FROZEN_ALWAYS` 里那五个（不是契约） | 「只能用 wb.py 子命令改」 | 给 `contract unlock` 会让读的人去申报一个不存在的契约名 |

这段判断放在守卫里而不是抄进三个 agent 定义：一处代码覆盖七个角色、主线程，以及以后新增的任何契约。

**被拦时不许绕。** agent 定义与 `CLAUDE.md` 里都写明：不要改 `settings.json`、不要换等价命令、不要用 Bash 的 `cat >` 代替 Write。要么交给有权限的角色，要么说明理由让用户决定。

## Bash 分支：绕过检查

前四层挂在 Write / Edit / NotebookEdit / MultiEdit 上。Bash 是**另一条完全独立的写入路径**，早期版本只查危险命令、不查写入目标，结果是四层拦截可以被一行 shell 全部绕过（实测这三条当时全部 `exit=0`）：

```bash
echo '{}' > .workbench/state.json                   # 门禁作废
echo architect > .workbench/role                    # 提权
sed -i 's/int/str/' .workbench/contracts/api.json   # 契约漂移，且无人申报
```

所以 Bash 分支现在也查冻结清单，且有写入目标精确解析：

```python
BASH_WRITE = re.compile(r">\s*[^\s]|>>\s*[^\s]|\btee\b|\bsed\b.*-i|\bchmod\b|\bchown\b|\brm\b|\bmv\b|\bcp\b")

if BASH_WRITE.search(cmd) or all_targets:
    all_targets, outside_targets, uncertain = resolve(cmd, root)
    # 精确模式：只拦 frozen ∩ all_targets
    # uncertain 模式：退回旧行为（文本匹配 + 基名误报）
```

三段式：**先用 `resolve()` 解析写入目标，再用冻结清单过滤，最后按角色范围检查。** `resolve()` 按命令名分类处理：重定向取 `>` 右侧，`cp`/`mv` 取最后一个非 flag 参数（目标），`sed -i` 取 `-i` 之后的参数，`tee` 取全部参数。`strip_heredocs()` 剥掉 heredoc body，避免 body 里提到的冻结路径被误判为写入目标。

`resolve()` 返回三元组 `(all_targets, outside_targets, uncertain)`：
- `all_targets`：所有写入目标的相对路径（用于冻结检查）
- `outside_targets`：仅项目根外的目标（用于越根检查）
- `uncertain`：碰到 `eval`/`xargs`/`$(...)` 等无法可靠解析的构造时为 True，此时退回旧行为

**`uncertain` 退回旧行为**：`BASH_WRITE` + `frozen_hits()` 文本匹配。误报面比精确模式宽（`cp`/`mv` 不分源和目标），但不漏拦。拒绝信息里会注明「写入目标无法解析，已一并拦截」。

先切目录再改（`cd .workbench/contracts && sed -i ... user-api.json`）靠一条兜底覆盖：命中 `BASH_WRITE`、没提到任何完整冻结路径、**且命令里有 `cd`/`pushd` 切进某个 `.workbench` 路径**时拒绝。hook 拿不到命令执行时的 cwd（`tool_input.cwd` 是会话的 cwd，不含命令内部的 `cd`），只能这么兜。

**这条的触发条件从「命令里出现 `.workbench`」收窄到「切进 `.workbench`」，是修两处误拦。** 宽版本会拦下 `echo '.workbench/' >> .git/info/exclude`（多仓库布局 A 的第二步，`.workbench` 是被写的**内容**不是写入目标）与 architect 用 heredoc 新建一份还没登记的契约文件（`contract add` 要求文件已存在，所以「先写文件」这一步必须走得通，Write 工具那条路本来就通）。两个都是文档写明的正常操作，撞上「被拦时不许绕、不许换等价写法」那条约定后没有出路 —— 而 `cd` 是这类漏检的**唯一**成因：不切目录时完整相对路径就在命令文本里，`frozen_hits()` 直接抓到。

早期版本还按 basename 匹配（`os.path.basename(rel) in cmd`），已删除。不是「误报方向偏保守」这条原则不成立，而是这几个词的误报率高到推翻了原则本身：`role` / `state.json` / `unlock` / `frozen` 在业务代码里太常见 —— `echo 'ALTER TABLE users ADD COLUMN role text' >> migrations/002.sql`、`echo '{}' > web/state.json` 全被拦。而且拒绝理由说的是「契约改动走 unlock 申报」，与真实原因无关：**误拦要算「显式」，前提是错误信息指向真实原因。** 兜底那条把误报面收进「切进 `.workbench/` 的写入型命令」，理由也能说准。

`wb.py` 自身不会被这条挡住：它的命令行里不出现 `>`、`tee`、`sed -i` 之类。`python3 -c` 在 `BASH_WRITE` 里但 `python3 .claude/hooks/wb.py` 不是 `-c`。

这条挡不住外部编辑器 / `git checkout` / 用户手改，那是刻意的取舍，兜底是 `contract verify` 的哈希校验 —— 见 [architecture.md](architecture.md#冻结防线覆盖不到的写入路径)。

`cp` / `mv` / `install` 的源和目标区分已由 `resolve()` 精确处理：`_LAST_ARG` 类命令只取最后一个非 flag 参数作为写入目标。`cp .workbench/contracts/api.yaml /tmp/bak` 的目标是 `/tmp/bak`（safe 目录，跳过），契约路径只出现在源位置，不会被误拦。`uncertain` 模式下退回旧的文本匹配，误报面略宽但不漏拦。

**safe 目录与项目根的关系**：`resolve()` 跳过 `/dev`、`/tmp` 等系统目录，但先检查路径是否在项目根内。当项目根本身位于 `/tmp/` 下时（如自检的临时目录），项目内部路径不会被 safe 目录过滤掉。

## 危险命令分级

Bash 调用除了冻结检查，还按命令文本查危险命令，两级：`DENY_BASH` 退出码 2（删根删家目录、`rm -r ../../`、force push、`DROP`/`TRUNCATE`、`curl|sh`、直写块设备、`mkfs`、`chmod 777 /`、fork bomb、`dd of=/dev/`），`WARN_BASH` 放行并把提示写到 stdout（`git reset --hard`、`git clean -fd`、`git checkout --`、`npm publish` / `twine upload`）。完整正则读 `wb.py` 里那两张表，这里不抄。

分级的依据：不可逆或灾难性的进 DENY，开发中确有正当用途的进 WARN —— 后四条拒绝会很烦人，而提示出现在 transcript 里模型能看见。

**误杀控制**：DENY 的模式都要求具体的危险目标，不笼统匹配命令名。`rm -rf build/`、`rm -rf node_modules` 正常通过，自检有专门断言。误杀比漏杀更影响可用性 —— 它会让 agent 开始想办法绕过守卫。

**为什么不做 ask 级别**：`PreToolUse` 的 JSON 输出支持 `permissionDecision: "ask"`，但退出码 2 是所有 Claude Code 版本都支持的机制。选退出码换兼容性，代价是只能二分 deny/allow。要三态就改用 JSON 输出协议。

## hook 载荷与失败语义

### 载荷

`PreToolUse` hook 从 stdin 读 JSON：

```jsonc
{
  "session_id": "...",               // 主线程与 subagent 共享，不能用来区分
  "transcript_path": "...",
  "cwd": "/home/work/workbench",     // 用于定位项目根
  "agent_type": "backend-developer",  // 只有 subagent 带；值 = agent 定义的 name
  "agent_id": "a...",                // 同上，只有 subagent 带
  "tool_name": "Write",
  "tool_input": { "file_path": "src/app.ts", "content": "..." }
}
```

路径字段按工具不同取 `file_path`（Write/Edit/MultiEdit）或 `notebook_path`（NotebookEdit）；Codex `apply_patch` 取 `tool_input.command` 中的 patch 标记；Bash 取 `tool_input.command`。已识别 subagent 但缺少 `agent_type` 时拒绝受管写入，未知且无工作台状态的输入才放行。

**载荷里有 subagent 标识，`agent_type` 就是角色名。** 实测（Claude Code 2.1.252、Codex CLI 0.152.1）subagent 的 `PreToolUse` / `PostToolUse` / `SubagentStop` 都带 `agent_type` 与 `agent_id`，主线程两个都没有；`session_id` 反而是共享的。`current_role()` 优先按 `agent_type` 判定；已带 `agent_id` 但缺少 `agent_type` 的旧/异常载荷会拒绝受管写入，避免把 subagent 误当主线程放行。

### 失败语义

```python
except Exception as e:  # 未初始化目录放行；已初始化工作台阻断并暴露故障
    print(f"[工作台 hook 异常] {type(e).__name__}: {e}", file=sys.stderr)
    try:
        initialized = state_path(find_root(Path(data.get("cwd") or os.getcwd()))).is_file()
    except Exception:
        initialized = False
    sys.exit(2 if initialized else 0)
```

hook 自身出 bug 时，未初始化目录放行；已初始化工作台**拒绝**（退出码 2）并将异常写到 stderr。这样工作台启用后不会因守卫异常静默放行敏感写入。

`SystemExit` 单独 re-raise，否则 `hook_deny()` 的退出码 2 会被这个 except 吞掉变成 0 —— 那会让所有拒绝静默失效。这是实现中最容易写错的一处。

`state.json` 不存在或解析失败时，第三层直接 return（放行）。工作台未初始化的仓库不该被守卫影响。

## 其余三个 hook

| 事件 | 匹配 | 作用 |
| --- | --- | --- |
| `PostToolUse` | Write / Edit / NotebookEdit / MultiEdit / apply_patch / Bash | 把静态可解析的改动路径、角色和可用 agent 身份字段追加一行到 `.workbench/artifacts.jsonl`，由 `task done` 归并进任务的 `artifacts` |
| `SessionStart` | — | 输出当前阶段、任务进度、阻塞项、契约漂移、就绪任务，注入上下文 |
| `SubagentStop` | — | 无任务处于 doing 时清除 `role` 与 `unlock`；有 doing 任务则保留并打印原因；记审计日志 |

`PostToolUse` **绝不能读改写 `state.json`**。并行 develop 下每个 subagent 的每次文件写入都触发它，旧快照回写会静默吞掉期间落盘的 `task done`，连带把 `save_state` 顺手重写的冻结清单退回旧版 —— 于是「门禁与进度不可绕过」在并发下失效，不需要谁去绕。状态锁把这条路封在了 CLI 那一侧，但对 hook 不是出路：`load_state(lock=True)` 会把每次工具调用都串行化到状态锁上，延迟直接叠加到并行写入的每一笔。纯 append 无竞态，也把全量 JSON 读写从每次工具调用的热路径上挪走了。每行的角色取自本次调用的载荷；归属按「角色 + 任务 `started` 时间」在归并时认领，重复归并幂等。

`SubagentStop` 清 `role` 是必需的：不清的话，`pm` 跑完后主线程的写入会继续受 `pm` 的范围限制（只能写 `artifacts/clarify/`），整个会话瘫掉。清 `unlock` 同样必需，理由相反 —— 不清的话窗口一直敞着，下一个 subagent 白捡一个可写的契约。清窗口时打一行提示（哪份契约的窗口被关了），让忘了 bump 的情况可见。

但它只在无 doing 任务时清，主要为的是解冻窗口 —— 并行下先结束的那个会把兄弟正在用的窗口一起收掉。角色这一半的风险随 `current_role()` 降了一级：角色 subagent 按自己的 `agent_type` 判定，兄弟的 `role` 文件被清也不会让它变成无限制；仍受影响的是主线程与非角色 agent。代价不变：串行下忘了 `task done`，角色锁会留到下一次 `role set`。

三者都不阻断流程 —— `PostToolUse` 与 `SubagentStop` 只写状态，`SessionStart` 只输出文本。

## settings.json 层的兜底

hook 是主要机制，`settings.json` 做粗粒度兜底：

```jsonc
"deny": [
  "Read(./.env)", "Read(./.env.*)",           // 密钥不进上下文
  "Read(./**/*.pem)", "Read(./**/*.key)", "Read(./**/id_rsa*)",
  "Read(./secrets/**)",
  "Edit(./.workbench/state.json)",             // 与 hook 第二层重复，故意的
  "Edit(./.workbench/role)",
  "Bash(git push --force:*)", "Bash(git push -f:*)"
]
```

`Read` 类的拦截**只能在这一层做** —— hook 只挂在 Write/Edit/Bash 上，不挂 Read（每次读文件都跑一个 Python 进程太贵）。密钥文件靠 `permissions.deny` 挡。

`Edit(./.workbench/state.json)` 与 hook 第二层重复是故意的：`permissions.deny` 是静态规则，不依赖 hook 进程正常工作。hook 因自身 bug 放行时，这一层还在。

这里必须写 `Edit(...)` 而**不是** `Write(...)`：Claude Code 的文件权限检查只匹配 `Edit(path)` 规则，而 `Edit(path)` 覆盖全部文件编辑工具（Write / Edit / NotebookEdit / MultiEdit）。写成 `Write(path)` 匹配不到任何工具调用，规则静默失效 —— 启动时会报 `is not matched by file permission checks`，那层纵深防御等于不存在。

已锁定的契约**没有**列在 `permissions.deny` 里 —— 契约文件名随项目而定，写死在配置里会让每个项目都要改 `settings.json`。契约的保护完全靠 hook 的冻结清单。

`allow` 列表里放了 `wb.py` 与常用只读命令减少权限弹窗，但**没有** `git commit` 与 `git push` —— 提交与推送该由用户决定时机。

## 自检覆盖

守卫的实际拦截边界**以 `selfcheck` 的断言为准**，这里不复述 —— 抄一份就是造一份会漂移的副本，而这份副本没人会去更新。跑 `python3 .claude/hooks/wb.py selfcheck` 看它跑什么，或读 `wb.py` 里 `cmd_selfcheck` 的「权限守卫」到「产物挂载」几段。

要知道的只有断言的组织方式：**正例与反例成对。** 只测「该拦的拦住了」会漏掉「不该拦的也拦了」，那种失效表现为 agent 无法工作，比漏拦更快被发现但同样是 bug。误报用例专门覆盖 `role` / `state.json` 这类在业务代码里高频出现的词。

`guard()` 在自检里直接调函数，不走子进程。改动 hook 事件分发或 argparse 之后要另外用真实载荷跑一遍：

```bash
echo '{"tool_name":"Bash","cwd":"'"$PWD"'","tool_input":{"command":"echo {} > .workbench/state.json"}}' \
  | python3 .claude/hooks/wb.py hook pre-tool; echo "exit=$?"
```

事件名是 `pre-tool` / `post-tool` / `session-start` / `subagent-stop`，不是 Claude Code 的 `PreToolUse`。名字写错时 argparse 也退出 2，看起来像「拦住了」—— 验证绕过路径时要确认拒绝信息是守卫发的，不是 argparse 发的。

改动 `DENY_BASH`、`WARN_BASH`、`BASH_WRITE`、`FROZEN_ALWAYS` 或 `role_scopes` 逻辑后必须跑自检。守卫失效是静默的：正则写错不会报错，只会让拦截永远不命中。

# 契约机制

契约（contract）是**一份多方依赖、不能被单方悄悄改的文件**。最初为接口定义而做，后来技术方案文档 `design.md` 与各阶段产物也走同一套 —— 它们要的是同一件事，不需要三套机制。

## 问题

多个开发 subagent 并行时，前端与后端各自需要知道接口长什么样。没有契约时会发生：

1. 主线程在 prompt 里口述接口 → 两个 subagent 各自理解，字段名拼写不同。
2. 让前端等后端做完 → 并行退化成串行，多 agent 的收益归零。
3. 让后端先定接口写在某个文件里 → 有效，但后端中途改文件时前端不知道，联调才发现。

契约机制解决第三种情况的漏洞：**冻结 + 检出变更 + 变更时自动通知消费方**。

方案文档面对的是同一个漏洞的另一个形态：设计定稿后，开发过程中发现方案某处不好实现，顺手把 `design.md` 改成自己实现的样子。结果是**文档与实现永远一致，因为文档跟着实现改** —— 评审、QA、复盘全部失去基准。

## 三类契约

### 接口契约

放 `.workbench/contracts/`，形式随项目：OpenAPI 片段（`user-api.yaml`）、JSON Schema（`events.json`）、TypeScript 类型（`types.ts`）、Protobuf（`rpc.proto`）。

内容必须具体到**字段名、类型、可选性、错误码、分页形状、时间格式**。「返回用户列表」不是契约 —— 它在联调时提供不了任何判断依据。一份最小契约的样子：

```json
{
  "GET /users": {
    "query": { "page": "int, >=1, 默认 1", "size": "int, <=100, 默认 20" },
    "200": {
      "items": [{ "id": "int", "name": "str", "created_at": "RFC3339 str" }],
      "total": "int",
      "next_cursor": "str | null"
    },
    "400": { "code": "INVALID_PAGE", "message": "str" },
    "403": { "code": "FORBIDDEN", "message": "str" }
  }
}
```

`str | null` 与「字段缺失」是两回事，契约里必须写清 —— 这是前后端联调最常见的分歧点。

### 技术方案文档

`.workbench/artifacts/design/design.md`，由 `architect` 在写完方案后自己登记：

```bash
wb.py contract add .workbench/artifacts/design/design.md \
    --name design-doc --owner architect \
    --consumers frontend-developer,backend-developer,qa
wb.py contract lock --name design-doc
```

登记后它获得的完全是接口契约那一套：哈希冻结、守卫拦直接写、漂移检出、改动要申报理由、bump 给三个消费方各建一条同步任务。**零新代码** —— 复用现成机制而不是为方案文档另写一套「文档保护」，是这个设计里最省的一处。

消费方选这三个角色的理由：开发照着方案实现、QA 照着方案与验收标准验证，方案变了三方都要重新对齐。`reviewer` 不在列 —— 它在 retro 阶段读最终版，不需要中途返工。

### 阶段产物

`requirements.md`、`current-state.md`、`test-report.md`、`retro.md` 由 `phase advance` 在门禁**真**通过时自动登记并锁定，owner 与消费方取自 `PHASE_ARTIFACT_CONTRACTS` 表，名字是 `artifact-<文件名去扩展>`。不需要谁手工 `contract add` —— 阶段过了就是定稿，定稿就该只读。

```
$ python3 .claude/hooks/wb.py phase advance
门禁 · clarify（需求澄清）
  [PASS] 产物 clarify/requirements.md — 已产出
  ...
结论：通过
已把 clarify 阶段产物冻结为契约 artifact-requirements：之后要改它先
`contract unlock --name artifact-requirements --reason '<为什么>'`，改完 `contract bump` 通知下游
阶段推进：clarify -> analyze（现状分析）
```

**为什么复用契约而不另造一套「产物冻结」**：产物被改的场景与契约完全同形 —— qa 打回要改需求、开发中途发现方案有问题要改 `design.md`。契约这条路径已经有理由必填、哈希校验、`bump` 通知下游三件事，另造一套只会造出第二个半成品。

它补的洞是**上游产物此前只在「恰好有角色锁」时才受保护**：角色范围检查在角色取不到时整层跳过，主线程与非角色 subagent 随时能重写 `requirements.md`，且 `artifacts.jsonl` 里不留角色。冻结不依赖角色，所以这条对谁都成立。

三处与接口契约不同：

| 项 | 接口契约 | 阶段产物 |
| --- | --- | --- |
| 登记 | `architect` 手工 `contract add` | `phase advance` 自动（强推不登记） |
| `kind` 字段 | 无 | `"artifact"` —— `contracts_locked` 不数它，否则 clarify 一过那条门禁就永远 PASS |
| `develop` 阶段 | 照常 | 不登记：`verification.md` 由编排者写，没有角色 owner |

`design.md` 两边都不算：architect 在 design 阶段自己登记为 `design-doc`（`contracts_locked` 要求至少一份接口契约，靠它满足），自动登记那步按路径查重跳过它。

## 冻结原理

契约的约束力来自两个独立的东西：**内容哈希**（事后检出）与**只读守卫**（事前拦截）。

### 内容哈希

```python
c["sha"] = hashlib.sha256(path.read_bytes()).hexdigest()
```

`contract lock` 把当前内容的 SHA-256 存进 `state.json`。此后 `contract verify` 重算哈希比对，不一致即「漂移」，退出码 1；develop 与 verify 两个阶段的门禁都包含 `contracts_intact` 断言；`state.json` 本身在冻结清单里，改不了记录的哈希。

为什么用哈希而不是语法感知的 diff：哈希对格式无关（YAML / JSON / proto / TS / Markdown 一视同仁 —— 这也是方案文档与阶段产物能零成本复用的原因）、零依赖、一行代码。代价是查不出语法错误，那个挂到命令门禁上：

```
wb.py config set gate_commands.lint 'npx @redocly/cli lint .workbench/contracts/*.yaml'
```

### 锁定即只读

`lock` 的第二个作用：把文件路径写进冻结清单，`PreToolUse` 守卫据此拒绝一切直接写入 —— Write/Edit 系工具按目标路径拦，Bash 按「有写入意图且提到冻结路径」拦。完整机制见 [permissions.md](permissions.md#第二层冻结清单)。

**没有豁免角色。** owner 不行，主线程不行。理由：能豁免的机制等于没有机制 —— 「我是 owner 所以我可以直接改」正是要防的那件事。owner 与其他人的区别只在**有权申报**，不在能跳过申报。

叠起来的结果：**私自改契约会在动手时就被拦下，拦不住的（外部编辑器、`git checkout`、用户手改）会在门禁时被抓出。**

### 申报窗口

改冻结文件的唯一合法路径：

```bash
wb.py contract unlock --name user-api --reason "分页要返回 total，前端无法渲染页码"
# 现在这一个文件可以写了
wb.py contract bump --name user-api
```

`--reason` 必填，不给直接拒绝。**理由必须在改之前留痕** —— 事后补的理由都是给已发生的事找解释，那时人已经知道自己改了什么，写出来的是辩护而不是动机。`bump` 不给 `--reason` 时继承申报时的理由：同一次变更只写一次理由，写两遍的机制最后会有一遍是敷衍的。

窗口存在 `.workbench/unlock/`，一份契约一个文件，多份可以并存；开关时机与分片理由见 [permissions.md 第三层](permissions.md#第三层解冻窗口)。

`bump` 时内容没变会被拒绝：

```
契约 user-api 内容未变，无需 bump（当前 v2）
```

否则「改坏了不想改回来 → bump 一下把哈希刷成新的」会成为消掉漂移的标准操作，冻结就作废了。

## 生命周期

```
                  ┌─────────┐
   contract add   │ 未锁定  │  sha = null，文件可自由改
   ──────────────>│         │  design 门禁 FAIL
                  └────┬────┘
                       │ contract lock
                       ▼
                  ┌─────────┐
                  │ 已锁定  │  sha 冻结 + 守卫只读
                  │  v1     │  开发可并行
                  └────┬────┘
             ┌─────────┴─────────────────┐
             │ contract unlock --reason  │ 守卫之外的路径改了文件
             ▼                            ▼
        ┌─────────┐                  ┌─────────┐
        │ 解冻中  │  这一个文件可写   │ 漂移！  │  verify 退出码 1
        │  v1     │  理由已入日志     │         │  develop 门禁 FAIL
        └────┬────┘                  └────┬────┘
             │ 改文件                      │
             │ contract bump          ┌────┴──────────────┐
             ▼                        │ unlock+bump       │ git checkout（误改）
        ┌─────────┐                   ▼                    ▼
        │ 已锁定  │              ┌─────────┐          ┌─────────┐
        │  v2     │              │ 已锁定  │          │ 已锁定  │
        └─────────┘              │  v2     │          │  v1     │
     + 消费方返工任务             └─────────┘          └─────────┘
     + 审计日志一条
     + 窗口关闭
```

左路是正常路径（申报 → 改 → bump），右路是异常路径（漂移 → 补申报或还原）。守卫把大部分改动逼到左路，右路留给守卫覆盖不到的情况。

### 命令

全部子命令与参数见 `wb.py contract --help`，操作顺序见 [wb-contract skill](../.claude/skills/wb-contract/SKILL.md)。两处约束值得单独记：

`add` 要求文件**已存在** —— 先写好接口定义再登记，不允许登记一个占位。`--name` 省略时取文件名主干，且只能含字母数字与 `.`、`_`、`-`：契约名会成为 `.workbench/unlock/` 下的文件名，不校验就能用 `--name ../../x` 让 `unlock` 写到项目根之外。

## bump 的影响面传播

`bump` 是这套机制真正有牙齿的地方。它做四件事：

```python
c["version"] += 1                    # 1. 面向人的版本递增
c["revision"] += 1                  # 2. 内部修订号单调递增
c["sha"], c["locked_at"] = sha, now()
snapshot = {                         # 3. 任务绑定完整快照，不按名称动态解析
    "name": name,
    "version": c["version"],
    "revision": c["revision"],
    "sha": c["sha"],
}
for role in c["consumers"]:
    st["tasks"].append({
        "title": f"同步契约 {name} v{snapshot['version']} 变更：{reason}",
        "role": role, "phase": "develop", "status": "todo",
        "contracts": [snapshot], "notes": "由 contract bump 自动创建",
    })
log(st, "contract_bump", name=..., **{"from": old, "to": c["version"], "reason": ...})  # 4. 审计
```

第三步是关键。契约变更最常见的失败模式不是「没人发现契约变了」，而是「发现了但忘了通知下游」。自动建任务让下游的返工进入任务表 —— 任务表是主线程每轮都读的东西，报告不是。任务携带创建时的完整 `{name, version, revision, sha}` 快照，执行者必须核对该快照与当前契约一致；不能只保存契约名称并在执行时解析最新版本。

`impact` 给出三类影响面：

```
$ wb.py contract impact --name user-api
契约 user-api v2  owner=backend-developer
消费方角色：frontend-developer
关联任务：T2[blocked], T4[todo]
代码引用：3 处
  web/api/users.ts:12:import type { UserList } from '../../.workbench/contracts/user-api'
  ...
```

代码引用用 `git grep -n -I` 搜契约名（自动尊重 `.gitignore`）；非 git 仓库退回 Python 递归扫描，跳过 `node_modules`、`dist`、`.git` 等目录与 512 KB 以上的文件。

## 权责划分

| 动作 | 谁 | 强制方式 |
| --- | --- | --- |
| 写契约文件、`add`、`lock` | `owner`（默认 `architect`） | `role_scopes` 里只有 architect 含 `.workbench/contracts/**` 与 `.workbench/artifacts/design/**` |
| `unlock` + 改 + `bump` | `owner` | 冻结守卫拦所有人的直接写，包括 owner |
| 读契约、按契约实现 | 开发角色 | 提示词：契约是唯一事实来源 |
| `verify`、字段级人工核对 | `qa` | qa agent 定义里的必做项 |
| 发现契约不够用 | 开发角色 `task block` | 冻结守卫 + 提示词：禁止直接改契约文件 |

开发角色的写入范围**不含** `.workbench/contracts/` 与 `.workbench/artifacts/design/`。这是有意的：**契约由单一角色统一定义，才叫契约。** 谁都能改的接口定义文件只是一份注释。

这条断言依赖守卫第四层的一处收窄：开发角色的范围里有 `*.json`，而 `fnmatch` 的 `*` 跨 `/`，所以不收窄的话 `.workbench/contracts/events.json` 是匹配得上的 —— 冻结那层也补不上，它只认已 `lock` 的契约。守卫因此对 `.workbench/` 下的路径只认显式以 `.workbench/` 开头的模式（[permissions.md](permissions.md#第四层角色写入范围)）。

「owner 也要申报」看起来多余，但它是这套机制唯一没有后门的原因。owner 直接改的场景恰好是最需要留痕的场景 —— 他是唯一有能力独自改动、且最容易觉得「这点小改不用记」的人。

## 开发中发现契约不够用

操作流程（`task block` → 报回主线程 → architect 走 `impact` / `unlock` / 改 / `bump` → `task reopen`）在 [wb-contract skill](../.claude/skills/wb-contract/SKILL.md) 里，那是主线程执行时读的地方。这里只记三件它不解释的事：

**为什么必须回传而不是自己改。** 开发角色适配一个契约里没有的字段不会立刻报错，它把 bug 推迟到联调，且届时没人记得是谁加的。而契约变更的影响面要由能看到全局的人确认 —— `bump` 会给每个消费方建返工任务，那是调度决定。

**三类契约走的是同一条路。** 方案文档只是换成 `--name design-doc`（三个消费方各一条同步任务，设计变更本来就该三方重新对齐）；qa 打回要改需求换成 `--name artifact-requirements`，`impact` 会列出 `analyst` 与 `architect` —— 需求变了这两个阶段的产物也过期了。

**改需求那一步要派 `pm`。** 它是 `artifact-requirements` 的 owner，且写入范围里只有它含 `artifacts/clarify/`。主线程解冻后自己改不会被拦（它没有角色限制），但那样落进 `artifacts.jsonl` 的记录里没有角色，事后追不到是谁改的需求。

## 失效模式与处置

| 现象 | 根因 | 处置 |
| --- | --- | --- |
| `verify` 报漂移 | 改动走了守卫覆盖不到的路径：外部编辑器、`git checkout`、`rsync`、用户手改 | `git diff` 看改了什么。有意变更 → `unlock --reason` 补申报再 `bump`；误改 → 还原文件 |
| 守卫拦住了但该改 | 忘了申报 | `contract unlock --name X --reason '...'`。**不要换等价写法绕** —— Bash 路径也被拦，且绕过意图会留在日志里 |
| `bump` 说「内容未变」 | 申报了但没真改，或改完又改回去了 | 确认要不要改。不改就 `contract lock --name X` 关掉窗口 |
| 门禁「尚未登记任何契约」 | 确实没有跨角色接口，或漏了登记 | 前者 `phase advance --force` 并说明；后者补 `add` + `lock`。`design-doc` 本身该登记，所以 design 之后这条基本不出现 |
| 契约锁了但联调还是不一致 | 实现没逐字段对齐契约 | 这是 `qa` 的字段级核对该抓的。契约保证「双方看同一份」，不保证「双方读对了」 |
| 契约文件语法错误 | 哈希不校验语法 | 挂 `gate_commands.lint` |
| `bump` 后消费方任务堆积 | 契约设计不稳定，改动过频 | 复盘信号：设计阶段对接口的思考不足。看 `log` 里 `contract_bump` 的条数 |
| 升级 `wb.py` 后契约的 Bash 防线失效 | 老项目没有 `.workbench/frozen` 缓存 | 已修：缺失**或为空**时从 `state.json` 现算。老项目顺手跑 `role scopes --reset` 刷新角色范围 |
| 契约明明锁了，某次工具调用却放行了 | `.workbench/frozen` 被读到中间态（旧版就地截断重写它） | 已修：`write_frozen` 改成原子替换，见 [architecture.md](architecture.md#写入原子性与并发) |

冻结机制本身覆盖不到的写入路径（外部编辑器 / `git checkout` / `rsync` / 用户手改）与那些取舍的理由，见 [architecture.md](architecture.md#冻结防线覆盖不到的写入路径)。共同点：**守卫防的是模型主动绕过，不是防人。** 兜底始终是 `contract verify` 的哈希校验 —— 它不管改动从哪来。

## 版本策略

- **向后兼容优先**：加可选字段，而不是改已有字段的类型或名字。
- 破坏性变更在 `--reason` 里写清「破坏性」，并检查**所有**消费方 —— `bump` 只会为 `consumers` 列表里的角色建任务，列表漏了就传播不到。
- 契约里不放示例密钥、真实用户数据、内网地址。契约文件进 git，也可能被贴到 issue 里。

## 复盘价值

`log` 里的 `contract_unlock` 与 `contract_bump` 条目在 `wb.py report` 里渲染成变更历史：

```markdown
### 契约变更历史
- 2026-08-31T19:15 user-api v1→v2：加 next_cursor 支持无限滚动
- 2026-08-31T20:03 design-doc v1→v2：缓存层改用现有 Redis，不引入新组件
```

**每次 bump 都说明设计阶段漏了什么。** 一次是正常，三次以上说明 design 阶段对接口的推敲不够，是 retro 阶段应该产出改进项的地方。

`design-doc` 的 bump 记录格外有价值 —— 它把「方案在开发中被改了几次、每次为什么」变成可查的清单。没有冻结机制时这段历史根本不存在：文档被静默改成最终样子，看起来像是一次就设计对了。

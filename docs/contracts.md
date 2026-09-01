# 契约机制

契约（contract）是**一份多方依赖、不能被单方悄悄改的文件**。最初为接口定义而做，后来技术方案文档 `design.md` 也走同一套 —— 它们要的是同一件事，不需要两套机制。

## 问题

多个开发 subagent 并行时，前端与后端各自需要知道接口长什么样。没有契约时会发生：

1. 主线程在 prompt 里口述接口 → 两个 subagent 各自理解，字段名拼写不同。
2. 让前端等后端做完 → 并行退化成串行，多 agent 的收益归零。
3. 让后端先定接口写在某个文件里 → 有效，但后端中途改文件时前端不知道，联调才发现。

契约机制解决第三种情况的漏洞：**冻结 + 检出变更 + 变更时自动通知消费方**。

方案文档面对的是同一个漏洞的另一个形态：设计定稿后，开发过程中发现方案某处不好实现，顺手把 `design.md` 改成自己实现的样子。结果是**文档与实现永远一致，因为文档跟着实现改** —— 评审、QA、复盘全部失去基准。

## 契约是什么

### 接口契约

放 `.workbench/contracts/`，形式随项目：

| 形式 | 例子 | 适用 |
| --- | --- | --- |
| OpenAPI 片段 | `user-api.yaml` | HTTP 接口 |
| JSON Schema | `events.json` | 消息体、事件 |
| TypeScript 类型 | `types.ts` | 前后端共享类型（Node 项目） |
| Protobuf | `rpc.proto` | gRPC |

内容必须具体到**字段名、类型、可选性、错误码、分页形状、时间格式**。「返回用户列表」不是契约 —— 它在联调时提供不了任何判断依据。

一份最小契约的样子：

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

登记为契约后它获得的完全是接口契约那一套：哈希冻结、守卫拦直接写、漂移检出、改动要申报理由、bump 给三个消费方各建一条同步任务。**零新代码** —— 复用现成机制而不是为方案文档另写一套「文档保护」，是这个设计里最省的一处。

消费方选这三个角色的理由：开发照着方案实现、QA 照着方案与验收标准验证，方案变了三方都要重新对齐。`reviewer` 不在列 —— 它在 retro 阶段读最终版，不需要中途返工。

## 冻结原理

契约的约束力来自两个独立的东西：**内容哈希**（事后检出）与**只读守卫**（事前拦截）。

### 内容哈希

```python
c["sha"] = hashlib.sha256(path.read_bytes()).hexdigest()
```

`contract lock` 把当前内容的 SHA-256 存进 `state.json`。此后：

- `contract verify` 重算哈希比对，不一致即「漂移」，退出码 1。
- develop 与 verify 两个阶段的门禁都包含 `contracts_intact` 断言。
- `state.json` 本身在冻结清单里，改不了记录的哈希。

为什么用哈希而不是语法感知的 diff：哈希对格式无关（YAML / JSON / proto / TS / Markdown 一视同仁 —— 这也是方案文档能零成本复用的原因）、零依赖、一行代码。代价是查不出语法错误 —— 那个挂到命令门禁上：

```
wb.py config set gate_commands.lint 'npx @redocly/cli lint .workbench/contracts/*.yaml'
```

### 锁定即只读

`lock` 的第二个作用：把文件路径写进冻结清单，`PreToolUse` 守卫据此拒绝一切直接写入。

```python
FROZEN_ALWAYS = ["state.json", "role", "unlock", "frozen"]

def frozen_paths(st):
    out = [f".workbench/{n}" for n in FROZEN_ALWAYS]
    out += [c["path"] for c in st.get("contracts", [])]
    return out
```

拦的范围：

| 路径 | 拦法 |
| --- | --- |
| Write / Edit / MultiEdit / NotebookEdit | 目标路径在冻结清单里就拒绝 |
| Bash 里的 `>` `>>` `tee` `sed -i` `perl -i` `truncate` `patch` `dd` `python3 -c` `node -e` `ln -sf` | 命令有写入意图且提到冻结路径就拒绝 |

**没有豁免角色。** owner 不行，主线程不行。理由：能豁免的机制等于没有机制 —— 「我是 owner 所以我可以直接改」正是要防的那件事。owner 与其他人的区别只在**有权申报**，不在能跳过申报。

三层叠起来的结果：**私自改契约会在动手时就被拦下，拦不住的（外部编辑器、`cp`、用户手改）会在门禁时被抓出。** 详见 [permissions.md](permissions.md#bash-分支绕过检查)。

### 申报窗口

改冻结文件的唯一合法路径：

```bash
wb.py contract unlock --name user-api --reason "分页要返回 total，前端无法渲染页码"
# 现在这一个文件可以写了
wb.py contract bump --name user-api
```

窗口存在 `.workbench/unlock`（`<契约名>\n<理由>`）。四条规则：

| 规则 | 为什么 |
| --- | --- |
| `--reason` 必填，不给直接拒绝 | **理由必须在改之前留痕。** 事后补的理由都是给已发生的事找解释 —— 那时人已经知道自己改了什么，写出来的是辩护而不是动机 |
| 只对一份契约生效 | 解冻 `user-api` 不会顺带放开 `design-doc`。`unlocked_path()` 只解析 `unlock` 里那一个名字 |
| 状态文件永不可解冻 | `unlocked_path()` 查的是 `contracts` 列表，`FROZEN_ALWAYS` 那四个不在里面，查不到 |
| `bump` / `lock` / `SubagentStop` 关闭窗口 | 忘了关也不会一直敞着 |

`bump` 不给 `--reason` 时继承申报时的理由 —— 同一次变更只写一次理由，写两遍的机制最后会有一遍是敷衍的。

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

```bash
# 登记
wb.py contract add .workbench/contracts/user-api.yaml \
    --name user-api --owner backend-developer --consumers frontend-developer
wb.py contract add .workbench/artifacts/design/design.md \
    --name design-doc --owner architect \
    --consumers frontend-developer,backend-developer,qa

# 定稿冻结（design 阶段门禁要求全部锁定）
wb.py contract lock --all
wb.py contract lock --name user-api

# 校验（退出码 1 = 有漂移）
wb.py contract verify

# 看影响面（改之前先看）
wb.py contract impact --name user-api

# 申报解冻（理由必填）
wb.py contract unlock --name user-api --reason "响应加 next_cursor 支持无限滚动"

# 正式变更（不给 --reason 就继承申报时的理由）
wb.py contract bump --name user-api

# 列表与状态（解冻中的会标出来）
wb.py contract list
```

`--name` 省略时取文件名主干（`user-api.yaml` → `user-api`）。`--owner` 省略时默认 `architect`。

`add` 要求文件已存在 —— 先写好接口定义再登记，不允许登记一个占位。

## bump 的影响面传播

`bump` 是这套机制真正有牙齿的地方。它做四件事：

```python
c["version"] += 1                    # 1. 版本递增
c["sha"], c["locked_at"] = sha, now()  # 2. 重新冻结
for role in c["consumers"]:           # 3. 给每个消费方角色建同步任务
    st["tasks"].append({
        "title": f"同步契约 {name} v{new} 变更：{reason}",
        "role": role, "phase": "develop", "status": "todo",
        "contracts": [name], "notes": "由 contract bump 自动创建",
    })
log(st, "contract_bump", name=..., **{"from": old, "to": new, "reason": ...})  # 4. 审计
```

第三步是关键。契约变更最常见的失败模式不是「没人发现契约变了」，而是「发现了但忘了通知下游」。自动建任务让下游的返工进入任务表 —— 任务表是主线程每轮都读的东西，报告不是。

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

开发角色的写入范围**不含** `.workbench/contracts/` 与 `.workbench/artifacts/design/`，守卫会拦。这是有意的：**契约由单一角色统一定义，才叫契约。** 谁都能改的接口定义文件只是一份注释。

「owner 也要申报」看起来多余，但它是这套机制唯一没有后门的原因。owner 直接改的场景恰好是最需要留痕的场景 —— 他是唯一有能力独自改动、且最容易觉得「这点小改不用记」的人。

## 开发中发现契约不够用

标准流程：

```bash
# 1. 开发角色阻塞任务，写清原因
wb.py task block T2 --reason "契约 user-api 响应缺 total，无法做分页"

# 2. 报回主线程 → 主线程派 architect

# 3. architect 先看影响面
wb.py contract impact --name user-api

# 4. 申报，理由必填
wb.py contract unlock --name user-api --reason "响应加 total 支持分页"

# 5. 改契约文件，然后正式 bump（继承上一步的理由）
wb.py contract bump --name user-api
#    → 自动为 frontend-developer 创建同步任务，窗口关闭

# 6. 解除阻塞
wb.py task reopen T2 --note "契约已 bump 到 v2"
```

**禁止的做法**：开发角色适配一个契约里没有的字段。这不会立刻报错，但把 bug 推迟到联调，且届时没人记得是谁加的。

方案文档不够用（发现设计有问题）走完全一样的六步，只是 `--name design-doc`。第 5 步的 bump 会给三个消费方各建一条同步任务 —— 设计变更本来就该三方都重新对齐。

## 失效模式与处置

| 现象 | 根因 | 处置 |
| --- | --- | --- |
| `verify` 报漂移 | 改动走了守卫覆盖不到的路径：外部编辑器、`cp`/`mv`、`git checkout`、用户手改 | `git diff` 看改了什么。有意变更 → `unlock --reason` 补申报再 `bump`；误改 → 还原文件 |
| 守卫拦住了但该改 | 忘了申报 | `contract unlock --name X --reason '...'`。**不要换等价写法绕** —— Bash 路径也被拦，且绕过意图会留在日志里 |
| `bump` 说「内容未变」 | 申报了但没真改，或改完又改回去了 | 确认要不要改。不改就 `contract lock --name X` 关掉窗口 |
| 门禁「尚未登记任何契约」 | 确实没有跨角色接口，或漏了登记 | 前者 `phase advance --force` 并说明；后者补 `add` + `lock`。`design-doc` 本身该登记，所以 design 之后这条基本不出现 |
| 契约锁了但联调还是不一致 | 实现没逐字段对齐契约 | 这是 `qa` 的字段级核对该抓的。契约保证「双方看同一份」，不保证「双方读对了」 |
| 契约文件语法错误 | 哈希不校验语法 | 挂 `gate_commands.lint` |
| `bump` 后消费方任务堆积 | 契约设计不稳定，改动过频 | 复盘信号：设计阶段对接口的思考不足。看 `log` 里 `contract_bump` 的条数 |
| 升级 `wb.py` 后契约的 Bash 防线失效 | 老项目没有 `.workbench/frozen` 缓存 | 已修：缺失时从 `state.json` 现算。自检有断言。老项目顺手跑 `role scopes --reset` 刷新角色范围 |

### 冻结机制本身的边界

| 边界 | 说明 |
| --- | --- |
| `cp` / `mv` / `install` 未纳入 Bash 写入检测 | 加进去会拦掉大量正常的构建与资源拷贝。靠 `verify` 兜 |
| basename 匹配偏保守 | 项目里另有同名文件时写它也会被拦。误拦是显式的（模型收到拒绝理由），漏拦是静默的，方向刻意选保守 |
| 解冻窗口在并行下不隔离 | `.workbench/unlock` 是单文件，两个 subagent 同时申报会互相覆盖。缓解：窗口只对一份契约生效，覆盖的结果是后者生效而非两者都开 |
| 用户自己能改任何东西 | 有意为之。用户是机制的所有者，不是被约束的对象 |

这些边界的共同点：**守卫防的是模型主动绕过，不是防人。** 兜底始终是 `contract verify` 的哈希校验 —— 它不管改动从哪来。

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

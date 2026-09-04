---
name: wb-contract
description: 工作台本地契约管理。登记、锁定、漂移校验、影响面分析、版本变更与争议熔断，保证前后端对着同一份冻结的接口定义并行开发。当涉及接口定义、契约变更、前后端联调不一致、"接口改了"或契约争议时使用。
---

# 契约管理

契约存在的唯一理由：让前后端**不等对方**就能并行开发，且联调时不会发现字段名不一致。当前工作流只使用项目内的 `.workbench` 与仓库文件，契约正文不走外部控制面。

内核命令：`python3 .claude/hooks/wb.py contract ...`

## 契约是什么

一个文件，描述一份**多方依赖、不能被单方悄悄改**的约定。接口契约、方案文档和阶段产物都使用同一套本地冻结机制：

**接口契约**，放 `.workbench/contracts/`，形式随项目：

- `user-api.yaml` — OpenAPI 片段
- `events.json` — 消息体 / 事件 schema
- `types.ts` — 共享 TypeScript 类型
- `rpc.proto` — protobuf

内容必须具体到**字段名、类型、可选性、错误码、分页形状、时间格式**。「返回用户列表」不是契约，会在联调时炸。

**技术方案文档** `.workbench/artifacts/design/design.md` —— 由 `architect` 在 design 阶段登记为 `design-doc`，消费方是三个开发/测试角色。它和接口契约一样需要「多方对着同一版本干活、改动要通知所有人」，所以走同一套冻结与 revision 机制。

**阶段产物** `requirements.md` / `current-state.md` / `test-report.md` / `retro.md` —— `phase advance` 在门禁真通过时自动登记并锁定，名字是 `artifact-<文件名去扩展>`。阶段过了就是定稿，回头改也必须走申报和 bump。`develop` 不在里面：`verification.md` 由编排者写，没有角色 owner。

## 生命周期

```
登记 add -> 首次 lock -> 开发中反复 verify
                     -> 需要改时先 unlock（申报）-> 改 -> bump
                     -> 受影响任务读取新契约 -> reopen -> 重新 start
```

### 登记

```
python3 .claude/hooks/wb.py contract add .workbench/contracts/user-api.yaml \
  --name user-api --owner backend-developer --consumers frontend-developer

python3 .claude/hooks/wb.py contract add .workbench/artifacts/design/design.md \
  --name design-doc --owner architect \
  --consumers frontend-developer,backend-developer,qa
```

`--owner` 是有权定义它的角色，`--consumers` 是依赖它的角色（逗号分隔）。`task add --contracts` 的命令行参数可以写契约名，但任务保存的不是名称列表，而是创建时复制的快照对象：

```json
{"name": "user-api", "version": 2, "revision": 3, "sha": "<sha256>"}
```

`version` 用于面向人的显示；`revision` 是内部单调编号；`sha` 是内容指纹。任务开始、写入前、heartbeat 和完成复核都必须比较这个对象，不能把名称重新解析到新内容后继续假定自己仍然有效。

### 锁定

```
python3 .claude/hooks/wb.py contract lock --all
```

首次 `lock` 建立 revision 1 的内容哈希，**并让这个文件对所有工具调用变成只读** —— Write / Edit 会被守卫拒绝，shell 重定向、`tee`、`sed -i` 等写法也会被拒绝。连 owner 自己和主线程都不例外。

已锁定契约再次 `lock` 只允许内容未变的幂等调用（同时关闭该契约的窗口）。如果内容已经变化，`lock` 必须拒绝，不能用 relock 覆盖旧基线；请走 `unlock` -> 修改 -> `bump`。

**design 阶段门禁要求所有接口契约已锁定** —— 没锁的契约等于没有契约，因为它随时会变。

### 校验

```
python3 .claude/hooks/wb.py contract verify      # 退出码 1 = 有漂移或开放窗口
```

漂移 = 文件内容变了但没有合法 `bump`。开放的 unlock 窗口也会被报告为待定变更，不能让门禁把修改中间态当成有效状态；外部编辑器和用户手改仍由哈希校验兜底。

### 变更与争议

锁定后的契约不能直接改。理由必须在改之前留下，且 `bump --reason` 不能替代预先存在的 unlock：

```
python3 .claude/hooks/wb.py contract impact --name user-api               # 1. 先看影响面
python3 .claude/hooks/wb.py contract unlock --name user-api \
    --reason "分页要返回 total，前端无法渲染页码"                          # 2. 预先申报
# 现在这一个文件可以改（hook 校验：unlock/bump 只有 owner 与 architect 跑得了，
# 你是主线程不受影响；派 subagent 去改时派 architect，不是实现者）
python3 .claude/hooks/wb.py task check <受影响任务ID>                       # 3. 工作中的任务确认快照
python3 .claude/hooks/wb.py contract bump --name user-api                 # 4. 重新锁定并传播失效
```

开发者发现契约与现实冲突、缺字段、类型不一致或语义不明确时，先停止实现，并同时留下任务阻塞与契约争议：

```
python3 .claude/hooks/wb.py task block <任务ID> --reason "说明无法继续的冲突"
python3 .claude/hooks/wb.py contract dispute --name user-api \
  --reason "说明冲突、证据与受影响的实现"
```

争议由编排者交给 `architect` 判断；不能用实现侧兼容层掩盖冲突。契约变更完成并传播后，按新快照恢复任务。

| 规则 | 为什么 |
| --- | --- |
| `unlock --reason` 必填且必须先于改动 | 改动理由必须在改之前留痕；事后补理由不能替代申报 |
| 窗口只对那一份契约生效 | 最小化可写范围；多份契约可以同时申报 |
| `state.json` / `role` / `frozen` / `unlock` / `artifacts.jsonl` 永不可解冻 | 它们是机制本身的地基 |
| 已锁契约内容变化时 `lock` 会拒绝 | 不能用 relock 覆盖漂移基线 |
| `bump` 时内容没变会被拒绝 | 不能靠刷版本号消掉一次漂移 |
| `bump` 必须消费修改前已经存在的 unlock | 命令行理由本身不能伪造变更授权 |

`bump` 做五件事：版本与 revision +1、重新锁定哈希、将绑定旧快照的任务标为 `stale`、给每个消费方角色创建带新快照的同步任务、把变更记入日志。成功后关闭该契约的 unlock 窗口与争议。

`impact` 给出：消费方角色、关联任务及其快照状态、代码里对该契约名的引用位置。

### 任务绑定、失效与恢复

`task add --contracts` 不接受不存在或未锁定的契约，并在任务中保存完整快照对象。`task start` 检查依赖、任务状态、契约仍存在且锁定，并核对 version、revision、SHA；开发中在开始写入、收到契约变更提示、heartbeat 和完成任务前都运行：

```bash
python3 .claude/hooks/wb.py task check <任务ID>
```

如果检查失败，立即停止实现写入并把任务标为 blocked 或 stale。契约 `bump` 后，引用旧 revision 或 SHA 的任务不能继续以旧实现完成，`task done` 也不能绕过 stale。

依赖语义固定如下：

- `done` 与带理由的 `skipped` 都满足下游依赖；`blocked` 与 `stale` 都不满足。
- 任务变为 `blocked` 或 `stale` 时，沿依赖图递归把全部传递下游标为 `stale`，包括原先已经 `done` 的任务。
- `ready`、`task start` 和门禁使用同一语义，不能只把 `done` 当作满足依赖，也不能让 `skipped` 在调度和门禁中出现两种含义。
- 只有在所有依赖都不再 `blocked` / `stale` 后，才允许恢复 stale 任务；恢复时刷新任务的全部契约快照，再 `task reopen` 和 `task start`。

重新读取当前契约并确认实现需要重做后：

```bash
python3 .claude/hooks/wb.py task reopen <任务ID> --note "已按新契约重新对齐"
python3 .claude/hooks/wb.py task start <任务ID>
python3 .claude/hooks/wb.py task check <任务ID>
```

`reopen` 只用于 blocked/stale 任务，并刷新任务保存的契约快照。存在 blocked/stale 契约依赖时，不要强行恢复下游任务。

## 谁能做什么

| 动作 | 谁 |
| --- | --- |
| 写契约文件、add、lock、unlock、bump | `owner`（接口契约通常是 backend/architect，`design-doc` 是 architect） |
| 读契约、按契约实现、运行 `task check` | 开发角色 |
| verify、字段级核对 | `qa` |
| 发现契约不够用 | 开发角色 `task block` + `contract dispute`，报回主线程 |

开发角色的写入范围不含 `.workbench/contracts/` 与 `.workbench/artifacts/design/`，守卫会拦。这是有意的：契约由一个角色统一定义，才叫契约。

## 常见状况

**开发中发现契约缺字段** — 开发角色先运行 `task check`，失败后停止写入并执行 `task block` 与 `contract dispute`。主线程派 `architect` 走 `impact` -> `unlock --reason` -> 改文件 -> `bump`；bump 会使旧快照任务 stale 并创建消费者同步任务，相关任务重新读取契约后再 `task reopen`。

**被守卫拦了** — 拒绝信息里就写着该跑哪条命令，契约名已经填好，照抄即可。它按你是不是这份契约的 owner 分岔：**是 owner** 就申报后修改；**不是 owner** 就报回编排者或 `task block`，不要自己申报。bump 会使旧快照失效并给消费方建返工任务，那是编排者的调度决定。**不要换等价写法绕**（不要用 Bash 代替 Write，不要改 `settings.json`）。

**verify 报漂移或开放窗口** — 先看改了什么：
- 是有意变更 -> 先 `unlock --reason` 补申报，再 `bump`，接受它标记旧任务 stale 并生成返工任务。
- 是误改 -> 还原文件，并确认没有留下开放窗口。

**门禁说「尚未登记任何契约」** — 确实没有跨角色接口（纯本地重构、纯文档）时，用 `phase advance --force` 并在报告说明原因。有接口却没登记，就是漏了这一步，补上。当前契约操作只通过上述本地子命令完成，正文就是仓库内登记的文件。

**契约文件本身格式错误** — 契约内核只管哈希不管语法。想加语法校验挂到门禁上：

```
python3 .claude/hooks/wb.py config set gate_commands.lint 'npx @redocly/cli lint .workbench/contracts/*.yaml'
```

## 底线

- 契约先于代码。契约没锁就不能创建绑定任务，等于两边各写一套猜想。
- 契约向后兼容优先：加可选字段而不是改已有字段的类型或名字。破坏性变更要在申报理由里写清，并检查所有消费方。
- 任务完成前必须通过当前契约快照检查；旧 revision/SHA 的任务先 reopen，不得静默完成。
- 契约里不放示例密钥、真实用户数据、内网地址。

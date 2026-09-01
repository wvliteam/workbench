---
name: wb-contract
description: 工作台契约管理。登记、锁定、漂移校验、影响面分析与版本变更（bump），保证前后端对着同一份冻结的接口定义并行开发。当涉及接口定义、契约变更、前后端联调不一致、"接口改了"时使用。
---

# 契约管理

契约存在的唯一理由：让前后端**不等对方**就能并行开发，且联调时不会发现字段名不一致。

内核命令：`python3 .claude/hooks/wb.py contract ...`

## 契约是什么

一个文件，描述一份**多方依赖、不能被单方悄悄改**的约定。两类都用同一套机制：

**接口契约**，放 `.workbench/contracts/`，形式随项目：

- `user-api.yaml` — OpenAPI 片段
- `events.json` — 消息体 / 事件 schema
- `types.ts` — 共享 TypeScript 类型
- `rpc.proto` — protobuf

内容必须具体到**字段名、类型、可选性、错误码、分页形状、时间格式**。「返回用户列表」不是契约，会在联调时炸。

**技术方案文档** `.workbench/artifacts/design/design.md` —— 由 `architect` 在 design 阶段登记为 `design-doc`，消费方是三个开发/测试角色。它和接口契约一样需要「多方对着同一版本干活、改动要通知所有人」，所以走同一套冻结与 bump，没有第二套机制。

## 生命周期

```
登记 add → 定稿 lock → 开发中反复 verify → 需要改时 unlock（申报）→ 改 → bump
```

### 登记

```
python3 .claude/hooks/wb.py contract add .workbench/contracts/user-api.yaml \
  --name user-api --owner backend-developer --consumers frontend-developer

python3 .claude/hooks/wb.py contract add .workbench/artifacts/design/design.md \
  --name design-doc --owner architect \
  --consumers frontend-developer,backend-developer,qa
```

`--owner` 是有权定义它的角色，`--consumers` 是依赖它的角色（逗号分隔）—— bump 时靠这个算影响面并自动建返工任务。

### 锁定

```
python3 .claude/hooks/wb.py contract lock --all
```

做两件事：冻结内容哈希，**并让这个文件对所有工具调用变成只读** —— Write / Edit 会被守卫拒绝，shell 重定向、`tee`、`sed -i`、`python3 -c` 之类的写法也会被拒绝。连 owner 自己和主线程都不例外。

**design 阶段门禁要求所有契约已锁定** —— 没锁的契约等于没有契约，因为它随时会变。

### 校验

```
python3 .claude/hooks/wb.py contract verify      # 退出码 1 = 有漂移
```

漂移 = 文件内容变了但版本没 bump。develop 与 verify 门禁都会跑这一条。锁定后有守卫，漂移基本只会来自守卫之外的路径（外部编辑器、`git checkout`、用户手改）—— 所以这条校验仍然必须留着，守卫不是唯一入口。

### 变更

锁定后的契约不能直接改。三步，理由先行：

```
python3 .claude/hooks/wb.py contract impact --name user-api               # 1. 先看影响面
python3 .claude/hooks/wb.py contract unlock --name user-api \
    --reason "分页要返回 total，前端无法渲染页码"                          # 2. 申报
# 现在这一个文件可以写了 —— 改它
python3 .claude/hooks/wb.py contract bump --name user-api                 # 3. 重新锁定
```

| 规则 | 为什么 |
| --- | --- |
| `unlock --reason` 必填 | **改动理由必须在改之前留痕。** 事后补的理由都是给已发生的事找解释 |
| 窗口只对那一份契约生效 | 解冻 `user-api` 不会顺带放开 `design-doc` |
| `state.json` / `role` / `frozen` / `unlock` 永不可解冻 | 它们是机制本身的地基，解冻了整套约束都能被绕 |
| 窗口在 `bump` / `lock` / 子 agent 结束时自动关闭 | 忘了关也不会一直敞着 |
| `bump` 时内容没变会被拒绝 | 不能靠刷版本号消掉一次漂移 |
| `bump` 不给 `--reason` 就继承 unlock 时申报的理由 | 同一次变更只写一次理由 |

`bump` 做四件事：版本 +1、重新锁定哈希、给每个消费方角色建一个同步任务、把变更记入日志（复盘时可查）。

`impact` 给出：消费方角色、关联任务、代码里对该契约名的引用位置。

## 谁能做什么

| 动作 | 谁 |
| --- | --- |
| 写契约文件、add、lock、unlock、bump | `owner`（接口契约通常是 backend/architect，`design-doc` 是 architect） |
| 读契约、按契约实现 | 开发角色 |
| verify、字段级核对 | `qa` |
| 发现契约不够用 | 开发角色 `task block`，报回主线程 |

开发角色的写入范围不含 `.workbench/contracts/` 与 `.workbench/artifacts/design/`，守卫会拦。这是有意的：契约由一个角色统一定义，才叫契约。

## 常见状况

**开发中发现契约缺字段** — 开发角色 `task block <ID> --reason "契约 X 缺 Y 字段，因为…"`，主线程派 architect 走 `impact` → `unlock --reason` → 改文件 → `bump`，然后 `task reopen`。

**被守卫拦了** — 拒绝信息里就写着该跑哪条命令。**不要换等价写法绕**（不要用 Bash 代替 Write，不要改 `settings.json`）—— 这些路径也被拦，且绕过冻结的意图会留在日志里。契约确实该改就申报，不该你改就 `task block` 交给 owner。

**verify 报漂移** — 说明改动走的是守卫之外的路径（外部编辑器、`git checkout`、用户手改）。先 `git diff` 看改了什么：
- 是有意变更 → `unlock --reason` 补申报，再 `bump`，接受它自动生成的返工任务。
- 是误改 → 还原文件。

**门禁说「尚未登记任何契约」** — 确实没有跨角色接口（纯本地重构、纯文档）时，`phase advance --force` 并在报告说明原因。有接口却没登记，就是漏了这一步，补上。注意 `design-doc` 本身就该被登记，所以 design 阶段之后这条基本不会出现。

**契约文件本身格式错误** — 契约内核只管哈希不管语法。想加语法校验挂到门禁上：
```
python3 .claude/hooks/wb.py config set gate_commands.lint 'npx @redocly/cli lint .workbench/contracts/*.yaml'
```

## 底线

- 契约先于代码。契约没锁就并行开发，等于两边各写一套猜想。
- 契约向后兼容优先：加可选字段而不是改已有字段的类型或名字。破坏性变更要在 `--reason` 里写清，并检查所有消费方。
- 契约里不放示例密钥、真实用户数据、内网地址。

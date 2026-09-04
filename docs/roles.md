# 角色设计

七个角色 subagent，每个对应一个阶段（develop 阶段两个，reviewer 兼代码评审）。

## 为什么按角色划分而不按任务类型

按任务类型划分（「写代码的 agent」「查资料的 agent」）会让每个 agent 的职责边界随任务变化，无法固定写入范围，也无法固定产物格式。

按角色划分带来三个可强制的东西：

1. **固定的写入范围** —— `pm` 永远只写产物目录，`qa` 永远只写测试目录。权限守卫可以硬编码这个映射。
2. **固定的产物路径与格式** —— 下游 agent 按固定路径读上游产物，门禁按固定章节校验。
3. **固定的交接格式** —— 每个 agent 的定义末尾都规定了「交回主线程的报告」包含什么，编排者不需要猜。

## 角色矩阵

这里是角色写入范围的唯一出处，权威值以 `wb.py` 的 `DEFAULT_ROLE_SCOPES` 为准（`wb.py role scopes` 打当前项目的实际值）。

| 角色 | 阶段 | 产出 | 可写 | 模型 |
| --- | --- | --- | --- | --- |
| `pm` | clarify | `artifacts/clarify/requirements.md` | `artifacts/clarify/**` | sonnet |
| `analyst` | analyze | `artifacts/analyze/current-state.md` | `artifacts/analyze/**` | sonnet |
| `architect` | design | `design.md` + 契约 + 任务图 | `artifacts/design/**` / `contracts/**` / `docs/**` | opus |
| `frontend-developer` | develop | 前端代码 + 校验（命令与输出报回编排者） | `web/ frontend/ app/ src/ public/ components/ pages/ lib/ styles/` + 前端扩展名 + `*.md` + `artifacts/develop/**` | sonnet |
| `backend-developer` | develop | 后端代码 + 迁移 + 校验（命令与输出报回编排者） | `server/ backend/ api/ src/ migrations/` + 后端扩展名 + `*.md` + `artifacts/develop/**` | sonnet |
| `qa` | verify | `artifacts/verify/test-report.md` | `tests/ test/ e2e/ spec/` + 测试框架配置 + `artifacts/verify/**` | sonnet |
| `reviewer` | retro + 临时评审 | `artifacts/retro/retro.md` + 交付报告 | `artifacts/retro/**` / `docs/**` / `*.md` | opus |

**模型分配**：`architect` 与 `reviewer` 用 opus —— 方案取舍与复盘归因是判断密度最高的两件事，做错的成本由后面所有阶段承担。其余用 sonnet。

**三处范围是补实测出来的误拦**，每一条堵的都是该角色的本职而不是跨界：

| 加的 | 给谁 | 不加会怎样 |
| --- | --- | --- |
| `*.md` | 开发两个角色、`reviewer` | `docs/**` 原本只在 architect 名下，于是 develop 阶段开发碰 `README.md` 被拒 —— 而拒绝信息给的第一条出路「交给对应角色」那时不存在，architect 已经下场了 |
| `*.config.{ts,js,mjs}` 与 `pytest.ini` / `tox.ini` | `qa` | 测试框架配置按约定放仓库根，而 qa 原本只有四个测试**目录** —— 配 e2e 第一步就走不通。两族要一起给，否则 qa 配得了 vitest 配不了 pytest。`pyproject.toml` / `setup.cfg` 故意不给：那两个同时装着依赖与打包配置，不是测试专属文件 |
| `components/ pages/ lib/ styles/` 与 `.js` `.jsx` `.vue` `.html` `.scss` | `frontend-developer` | 原列表默认了「源码在 `src/` 或 `web/` 下且用 TypeScript」，Next.js / Nuxt / Vite 的标准布局全在范围外 |

放宽的是**仓库内的文件，不是状态目录**。裸扩展名模式（`*.md` / `*.json`）在 `fnmatch` 下跨 `/`，所以守卫对 `.workbench/` 下的路径只认显式以 `.workbench/` 开头的模式 —— 否则 `*.md` 会匹配 `artifacts/clarify/requirements.md`、`*.json` 会匹配 `contracts/events.json`，把下面那两段的隔离整个绕开。这条收窄同时补掉了 `*.json` 一直存在的同类缺口。同样的收窄也覆盖 `.claude/` `.codex/` `.agents/` —— 那里装的是权限引擎、hook 注册表与角色定义，任何角色都写不到，要改交回主线程（见 [permissions.md](permissions.md#第四层角色写入范围)）。

**产物目录按阶段隔离**，不是给所有角色一个 `.workbench/artifacts/**`。这挡的是下游角色去改上游产物 —— `qa` 发现需求写得不清楚，顺手把 `requirements.md` 改成自己理解的样子，之后就没人知道原始需求是什么了。改上游产物要走上游角色，或者报回主线程。

阶段过了门禁之后还多一道：那份产物被登记成契约并锁定，连 owner 自己都要先 `contract unlock --reason` 申报才能改（见 [contracts.md](contracts.md#阶段产物)）。阶段隔离只在守卫判得出角色时生效，冻结不依赖角色 —— 主线程与非角色 agent 也拦得住。

## 三个不许动手的角色

`analyst`、`qa`、`reviewer` 都能用 Write，但写入范围不含产品代码。这不是疏忽：

| 角色 | 为什么不许改代码 |
| --- | --- |
| `analyst` | 分析阶段动手改代码是最常见的流程破坏 —— 边看边改会跳过方案设计，改完也没人评审 |
| `qa` | 自己顺手改会让缺陷统计失真，也绕过了开发的自检责任。缺陷要打回成任务 |
| `reviewer` | 评审者改代码就没人评审那次改动了 |

`qa` 能写 `tests/` 与测试框架配置（`*.config.ts` / `pytest.ini` 之类）—— 搭测试与补测试是它的职责，改产品代码不是。`reviewer` 能写 `docs/**` 与 `*.md` 是同一个道理：落 ADR、补说明属于评审产出，动代码不属于。

## 每个角色的开工三步

所有 agent 定义的开头都是同一个模式：

```
1. python3 .claude/hooks/wb.py role set <自己>     # 收紧写入范围
2. 读上游产物（有明确路径）
3. python3 .claude/hooks/wb.py task start <ID>     # 开发/测试角色
```

`role set` 放在第一步而不是由编排者代设，原因是 subagent 一定会执行自己的第一条指令，而编排者可能忘。**但守卫并不依赖它** —— subagent 的写入按 hook 载荷里的 `agent_type` 判定，与谁最后 `role set` 过无关（见 [permissions.md](permissions.md#第四层角色写入范围)）。留着这一步的用处有两个：`role scopes` 能看出当前范围，以及活万一派给了非角色 agent（`general-purpose` 之类）时它是唯一的兜底。

## 产物的门禁耦合

每个产物的必备章节硬编码在 `GATES` 表里（对照表见 [gates.md](gates.md#六个阶段的门禁)），所以 agent 定义里给出的 Markdown 模板不是建议，是**接口**。每个 agent 定义里都写明「门禁会检查 X 章节存在，缺则无法进入下一阶段」—— 让 subagent 知道这是硬要求而不是格式偏好。

门禁一过这些产物就转为只读契约，所以那句「门禁会检查」还有第二层含义：**过了门禁再想补一段，要走 `contract unlock --reason` 申报**。写的时候一次写全比事后申报便宜。

`verification.md`（develop 的产物）是唯一一个不由角色 agent 产出的 —— 由编排者写，两个原因见 [gates.md](gates.md#实现要点)。两个 developer agent 的定义因此只要求把校验命令原文与完整输出报回来。

`design.md` 还多一步：`architect` 写完要把它登记成契约并锁定，之后连自己都不能直接改。见 [contracts.md](contracts.md#技术方案文档)。

## 协作协议

### 上下游交接

```
pm ──requirements.md──> analyst ──current-state.md──> architect
                                                          │
                            ┌─────────────────────────────┤
                            │ design.md + 锁定的契约 + 任务图
                            ▼
              ┌──── fe-dev ────┐
              │                │  （并行，对着同一份契约）
              └──── be-dev ────┘
                            │
                            ▼
                           qa ──test-report.md──> reviewer
```

每个下游 agent 的定义里明确写了要读哪些上游产物的**具体路径**。`analyst` 的定义甚至规定：

> 先读 `.workbench/artifacts/clarify/requirements.md`。没有它就停下来告知主线程 —— 无需求的分析是浪费。

### 阻塞回传

开发角色发现契约不够用时不能自己解决 —— 契约已冻结，守卫会拦。走：

```
task block <ID> --reason "契约 X 缺 Y 字段，因为…"
        ↓ 报回主线程
主线程派 architect：contract impact → contract unlock --reason → 改文件 → contract bump
        ↓
task reopen <ID>
```

三个开发/测试角色的定义里都有这一段，且都明确写了「**禁止直接改契约文件与 `design.md`**，Write / Edit 和 shell 写入都会被守卫拒绝，不要试等价写法」。守卫在拒绝时也会按 owner 分岔给出该走的路径（[permissions.md](permissions.md#拒绝信息要可操作)），所以非 owner 角色撞上冻结产物时不必依赖记住这一段。

### 打回

`qa` 发现缺陷时建任务而不是只写报告：

```bash
wb.py task add --title "修复：分页 total 恒为 0" \
    --role backend-developer --phase develop --contracts user-api
wb.py task reopen T1 --note "分页 total 恒为 0"     # 或者已完成的任务做错了
```

理由写在 qa 定义里：「报告没人当待办看，任务表才是」。主线程每轮读 `status`，不读 `test-report.md`。

## 交回主线程的报告

每个 agent 定义的最后一节规定报告内容。共同点：**结论优先**，不复述过程；**不复述整篇产物**（编排者会读文件）；带上 `gate check` 的结果；明确列出需要用户决策的事项。

例如 `pm` 的：「阻塞待确认清单（若有）、需求条数、验收标准条数、门禁结果、你做的关键假设」。

编排者的汇报规则对应地写在 `wb-flow` 里：「不要复述 subagent 的完整报告 —— 用户看不到 subagent 输出，你转述关键结论就够，别转述过程」。

## 跨角色共享的硬规则

契约是唯一事实来源、非平凡逻辑留一个可运行校验、优先复用既有资产、不为「以后可能需要」加抽象、每个论断给 `file:line`、以及各角色的「不可简化清单」（后端的输入校验与密钥处理、前端的可访问性基础与异步态）—— 这些**逐字重复在多个 agent 定义里**，具体哪条出现在哪个角色里读那些定义文件。

重复是刻意的：**subagent 只看自己的定义，不看别人的，也不看这份文档。** 写进共享文档等于没写。代价是改一条规则要改多个文件 —— 接受这个代价，因为漏一处的后果是那个角色少一条底线，而不是文档不一致。

## 定制角色

### 调整写入范围

```bash
wb.py role scopes            # 看当前配置 + 冻结清单 + 解冻窗口
wb.py role scopes --reset    # 刷成 DEFAULT_ROLE_SCOPES（会覆盖定制，先存一份）
                             # 跨仓库布局下按仓库前缀重算，不是裸默认值
wb.py config set role_scopes.backend-developer \
    '["server/**","internal/**","migrations/**",".workbench/artifacts/develop/**"]'
```

单体项目里 `frontend-developer` 与 `backend-developer` 的默认范围都含 `src/**`，实际上不隔离。按真实目录改掉。跨仓库工作区的默认范围会歪成按语言隔离，必须改成按仓库前缀 —— 原因见 [architecture.md](architecture.md#跨仓库同一个语义的反面)。

改定制范围时**别把产物目录放宽回 `.workbench/artifacts/**`** —— 那会撤掉阶段隔离。要给某个角色额外的产物目录就明确列出来（`".workbench/artifacts/develop/**"`, `".workbench/artifacts/verify/**"`）。

### 加一个角色

1. 把名字加进 `wb.py` 的 `ROLES` 列表（`task add --role` 与 `contract --owner` 的 choices 由它生成）。
2. 在 `DEFAULT_ROLE_SCOPES` 加写入范围。
3. 写 `.claude/agents/<名字>.md`，照现有 agent 的结构：frontmatter（`name` / `description` / `tools` / `model`）+ 开工三步 + 职责 + 产物模板 + 规则 + 交回报告格式。**`name` 必须与 `ROLES` 里的名字一字不差** —— 守卫按载荷 `agent_type` 查 `role_scopes`，对不上就退回读 `.workbench/role`，角色隔离静默降级。
4. 在 `wb-flow` 的阶段-角色对应表里加一行。
5. 跑 `wb.py selfcheck`。

`description` 决定 Claude 什么时候自动选用这个 agent，要写清「什么情况下用」而不只是「它是什么」。

加之前先问：这个角色的写入范围与现有角色重叠吗？重叠说明不该拆 —— 两个 agent 改同一批文件会互相覆盖，且没有机制能检出。

# 角色设计

七个角色 subagent，每个对应一个阶段（develop 阶段两个，reviewer 兼代码评审）。

## 为什么按角色划分而不按任务类型

按任务类型划分（「写代码的 agent」「查资料的 agent」）会让每个 agent 的职责边界随任务变化，无法固定写入范围，也无法固定产物格式。

按角色划分带来三个可强制的东西：

1. **固定的写入范围** —— `pm` 永远只写产物目录，`qa` 永远只写测试目录。权限守卫可以硬编码这个映射。
2. **固定的产物路径与格式** —— 下游 agent 按固定路径读上游产物，门禁按固定章节校验。
3. **固定的交接格式** —— 每个 agent 的定义末尾都规定了「交回主线程的报告」包含什么，编排者不需要猜。

## 职责矩阵

| 角色 | 阶段 | 产出 | 可写 | 模型 |
| --- | --- | --- | --- | --- |
| `pm` | clarify | `artifacts/clarify/requirements.md` | `artifacts/clarify/**` | sonnet |
| `analyst` | analyze | `artifacts/analyze/current-state.md` | `artifacts/analyze/**` | sonnet |
| `architect` | design | `design.md` + 契约 + 任务图 | `artifacts/design/**` / `contracts/**` / `docs/**` | opus |
| `frontend-developer` | develop | 前端代码 + 校验 | `web/ frontend/ app/ src/ public/` + `artifacts/develop/**` | sonnet |
| `backend-developer` | develop | 后端代码 + 迁移 + 校验 | `server/ backend/ api/ src/ migrations/` + `artifacts/develop/**` | sonnet |
| `qa` | verify | `artifacts/verify/test-report.md` | `tests/ test/ e2e/ spec/` + `artifacts/verify/**` | sonnet |
| `reviewer` | retro + 临时评审 | `artifacts/retro/retro.md` + 交付报告 | `artifacts/retro/**` | opus |

**模型分配**：`architect` 与 `reviewer` 用 opus —— 方案取舍与复盘归因是判断密度最高的两件事，做错的成本由后面所有阶段承担。其余用 sonnet。

**产物目录按阶段隔离**，不是给所有角色一个 `.workbench/artifacts/**`。这挡的是下游角色去改上游产物 —— `qa` 发现需求写得不清楚，顺手把 `requirements.md` 改成自己理解的样子，之后就没人知道原始需求是什么了。改上游产物要走上游角色，或者报回主线程。

## 三个不许动手的角色

`analyst`、`qa`、`reviewer` 都能用 Write，但写入范围不含产品代码。这不是疏忽：

| 角色 | 为什么不许改代码 |
| --- | --- |
| `analyst` | 分析阶段动手改代码是最常见的流程破坏 —— 边看边改会跳过方案设计，改完也没人评审 |
| `qa` | 自己顺手改会让缺陷统计失真，也绕过了开发的自检责任。缺陷要打回成任务 |
| `reviewer` | 评审者改代码就没人评审那次改动了 |

`qa` 能写 `tests/` —— 补测试是它的职责，改产品代码不是。

## 每个角色的开工三步

所有 agent 定义的开头都是同一个模式：

```
1. python3 .claude/hooks/wb.py role set <自己>     # 收紧写入范围
2. 读上游产物（有明确路径）
3. python3 .claude/hooks/wb.py task start <ID>     # 开发/测试角色
```

`role set` 放在第一步而不是由编排者代设，原因是 subagent 一定会执行自己的第一条指令，而编排者可能忘。守卫是兜底，提示词是主要遵守路径 —— 详见 [permissions.md](permissions.md#hook-载荷与失败语义)。

## 产物的门禁耦合

每个产物的必备章节是硬编码在 `GATES` 表里的，所以 agent 定义里给出的 Markdown 模板不是建议，是**接口**：

| 产物 | 门禁要求 | 在 agent 定义里 |
| --- | --- | --- |
| `requirements.md` | 含 `验收标准`、`非目标` | 模板里两个章节 + 「门禁会检查」的说明 |
| `current-state.md` | 含 `风险` | 模板里 `## 风险` + 要求覆盖四类风险 |
| `design.md` | 含 `方案对比` | 模板 + 「至少两个候选方案」 |
| `retro.md` | 含 `改进项` | 模板 + 「可执行、有归属、能验证」 |

每个 agent 定义里都写明「门禁会检查 X 章节存在，缺则无法进入下一阶段」—— 让 subagent 知道这是硬要求而不是格式偏好。

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

三个开发/测试角色的定义里都有这一段，且都明确写了「**禁止直接改契约文件与 `design.md`**，Write / Edit 和 shell 写入都会被守卫拒绝，不要试等价写法」。

### 打回

`qa` 发现缺陷时建任务而不是只写报告：

```bash
wb.py task add --title "修复：分页 total 恒为 0" \
    --role backend-developer --phase develop --contracts user-api
# 或者已完成的任务做错了
wb.py task reopen T1 --note "分页 total 恒为 0"
```

理由写在 qa 定义里：「报告没人当待办看，任务表才是」。主线程每轮读 `status`，不读 `test-report.md`。

## 交回主线程的报告

每个 agent 定义的最后一节规定报告内容。共同点：

- **结论优先**，不复述过程。
- **不复述整篇产物** —— 编排者会读文件，不需要转述。
- 带上 `gate check` 的结果。
- 明确列出需要用户决策的事项。

例如 `pm` 的：「阻塞待确认清单（若有）、需求条数、验收标准条数、门禁结果、你做的关键假设」。

编排者的汇报规则对应地写在 `wb-flow` 里：「不要复述 subagent 的完整报告 —— 用户看不到 subagent 输出，你转述关键结论就够，别转述过程」。

## 跨角色共享的硬规则

写在多个 agent 定义里的重复内容，是刻意的重复 —— subagent 只看自己的定义，不看别人的：

| 规则 | 出现在 |
| --- | --- |
| 契约是唯一事实来源，不许直接改 | fe-dev、be-dev、qa、architect |
| 非平凡逻辑留一个可运行校验，不搭框架 | fe-dev、be-dev |
| 优先复用既有资产，别重写几个文件之外就有的东西 | analyst（找出来）、architect（方案里用）、fe/be-dev（实现时用） |
| 不为「以后可能需要」加抽象 / 配置项 | architect、fe-dev、be-dev |
| 每个论断给 `file:line` | analyst、reviewer |

### 不可简化的清单

在多个角色定义里明确列为「底线，不是加分项」：

- **后端**：信任边界输入校验、参数化查询、密钥只从环境变量读、日志不打敏感数据、鉴权在服务端。
- **前端**：可访问性基础（语义化标签、label 关联、键盘可聚焦、alt、焦点可见）、异步请求的加载态与错误态。
- **通用**：防数据丢失的错误处理、用户明确要求的功能。

其余按最小可用实现。

## 定制角色

### 调整写入范围

```bash
wb.py role scopes            # 看当前配置 + 冻结清单 + 解冻窗口
wb.py role scopes --reset    # 刷成 DEFAULT_ROLE_SCOPES（会覆盖定制，先存一份）
wb.py config set role_scopes.backend-developer \
    '["server/**","internal/**","migrations/**",".workbench/artifacts/develop/**"]'
```

单体项目里 `frontend-developer` 与 `backend-developer` 的默认范围都含 `src/**`，实际上不隔离。按真实目录改掉。

改定制范围时**别把产物目录放宽回 `.workbench/artifacts/**`** —— 那会撤掉阶段隔离。要给某个角色额外的产物目录就明确列出来（`".workbench/artifacts/develop/**"`, `".workbench/artifacts/verify/**"`）。

### 加一个角色

1. 把名字加进 `wb.py` 的 `ROLES` 列表（`task add --role` 与 `contract --owner` 的 choices 由它生成）。
2. 在 `DEFAULT_ROLE_SCOPES` 加写入范围。
3. 写 `.claude/agents/<名字>.md`，照现有 agent 的结构：frontmatter（`name` / `description` / `tools` / `model`）+ 开工三步 + 职责 + 产物模板 + 规则 + 交回报告格式。
4. 在 `wb-flow` 的阶段-角色对应表里加一行。
5. 跑 `wb.py selfcheck`。

`description` 决定 Claude 什么时候自动选用这个 agent，要写清「什么情况下用」而不只是「它是什么」。

### 常见的加法

| 角色 | 阶段 | 说明 |
| --- | --- | --- |
| `devops` | develop / verify | CI 配置、部署脚本、可观测性。写入范围 `.github/ deploy/ infra/` |
| `data-engineer` | develop | 数据管道与 ETL，与 backend 分开以免范围重叠 |
| `security-reviewer` | verify | 专门的安全评审，只读 + 产物目录 |
| `tech-writer` | retro | 对外文档。写入范围 `docs/**` |

加之前先问：这个角色的写入范围与现有角色重叠吗？重叠说明不该拆 —— 两个 agent 改同一批文件会互相覆盖，且没有机制能检出。

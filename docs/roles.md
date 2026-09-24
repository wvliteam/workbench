# 角色设计

主干核心 subagent（6 阶段流水线）+ 旁路与按需 subagent（跨仓侦查、线上排障、DBA、DevOps、安全审计）。

## 架构：主干流水线与旁路角色双轨制

为了避免常规简单需求面临过重流程负担，同时确保复杂与高危工程动作（如数据库结构迁移、安全合规、线上事故排障与生产发布）具备严密防线，工作台采用**主干与旁路双轨制**：
1. **主干流水线角色（In-Flow Backbone）**：负责必须闭环的 6 阶段核心链路（`pm` → `analyst` → `architect` → `fe/be-dev` → `qa` → `submitter` → `reviewer`/`knowledger`）。
2. **旁路与按需角色（Off-Flow / Sidecar / On-Demand）**：
   - **前置只读旁路**：`impact-scout`（跨仓规模与影响面调研）、`debugger`（线上告警与故障堆栈根因归因）；
   - **开发条件旁路**：`dba`（仅当方案涉及数据库 Schema/DDL/迁移变更时由 architect 动态插入任务）；
   - **只读审计旁路**：`security-auditor`（威胁建模、OWASP 与权限数据合规静态审查）；
   - **交付部署旁路**：`devops`（代码 push 后的远端 CI/CD 感知、K8s/容器物料校验与发布健康巡检）。

## 为什么按角色划分而不按任务类型

按任务类型划分（「写代码的 agent」「查资料的 agent」）会让每个 agent 的职责边界随任务变化，无法固定写入范围，也无法固定产物格式。

按角色划分带来三个可强制的东西：

1. **固定的写入范围** —— `pm` 永远只写产物目录，`qa` 永远只写测试与验证产物，`dba` 专职写迁移脚本。守卫把这个映射**硬编码执法在工作流核心路径（受守前缀）上**：`.workbench/` 的阶段产物与契约、`knowledge/`、`references/`、守卫本体等。**注意执法边界**：产品源码（`repos/.source/**`）不在受守前缀内，守卫**不按角色**判它（见 [permissions.md](permissions.md) 与下节「执法边界」）——那里的越权防护走的是另一套：主线程首写归属闸门、子 agent 的任务契约绑定、以及只读旁路角色的「无 Write 工具」物理隔离。
2. **固定的产物路径与格式** —— 下游 agent 按固定路径读上游产物，门禁按固定章节校验。
3. **固定的交接格式** —— 每个 agent 的定义末尾都规定了「交回主线程的报告」包含什么，编排者不需要猜。

## 角色矩阵

这里是角色写入范围的唯一出处，权威值以 `wb_const.py` 的 `DEFAULT_ROLE_SCOPES` 为准（`wb.py role scopes` 打当前项目的实际值）。

| 角色 | 阶段 / 属性 | 产出 | 可写 | 模型 |
| --- | --- | --- | --- | --- |
| `pm` | clarify (主干) | `artifacts/<flow>/clarify/requirements.md` | `artifacts/*/clarify/**` | sonnet |
| `analyst` | analyze (主干) | `artifacts/<flow>/analyze/current-state.md` + 单仓画像三件套 | `artifacts/*/analyze/**` / `repos/*/*/{overview,setup,test}.md` | sonnet |
| `architect` | design (主干) | `design.md` + 契约 + 任务图 | `artifacts/*/design/**` / `contracts/**` | opus |
| `frontend-developer` | develop (主干) | 前端代码 + 校验命令输出 + 异常执行记录 | 前端源码目录与扩展名 + `*.md` + `tasks/**` | sonnet |
| `backend-developer` | develop (主干) | 后端代码 + 校验命令输出 + 异常执行记录 | 后端源码目录与扩展名 + `*.md` + `tasks/**` | sonnet |
| `dba` | develop (条件旁路) | 数据库双向迁移脚本 + 回滚校验 | `migrations/**`, `schemas/**`, `schema/**`, `sql/**`, `tasks/**` | sonnet |
| `qa` | verify (主干) | `artifacts/<flow>/verify/test-report.md` | `tests/**` + 测试框架配置 + `artifacts/*/verify/**` | sonnet |
| `submitter` | verify (主干) | `artifacts/<flow>/verify/submit-report.md` + git commit/push | `artifacts/*/verify/**` | sonnet |
| `devops` | verify/release (交付旁路) | `artifacts/<flow>/verify/deploy-report.md` | `deploy/**`, `k8s/**`, `docker/**`, `.github/workflows/**`, `ci/**`, `helm/**`, `artifacts/*/verify/**` | sonnet |
| `reviewer` | retro (主干) | `artifacts/<flow>/retro/retro.md` + 交付报告 | `artifacts/*/retro/**` | opus |
| `knowledger` | retro (主干) | `knowledge/<类别>/` 经验条目与类别 `index.md` | `knowledge/**` | sonnet |
| `impact-scout` | pre-flow (只读旁路) | 跨仓影响面清单、契约依赖与漂移 | **只读**（无 Write/Edit） | sonnet |
| `debugger` | pre-flow (只读旁路) | 故障根因分析报告（触发机理、`file:line`、处置建议） | **只读**（无 Write/Edit） | opus |
| `security-auditor` | advisory (只读旁路) | 安全与合规审计报告（阻断项、CVE、权限隐患） | **只读**（无 Write/Edit） | opus |

**模型分配**：`architect`、`reviewer`、`debugger` 与 `security-auditor` 用 opus/high-reasoning —— 方案架构、复盘归因、故障定位与安全审计是判断密度最高的环节。其余用 sonnet。

**三处范围是补实测出来的误拦**，每一条堵的都是该角色的本职而不是跨界：

| 加的 | 给谁 | 不加会怎样 |
| --- | --- | --- |
| `*.md` | 开发两个角色 | `docs/**` 原本只在 architect 名下，于是 develop 阶段开发碰 `README.md` 被拒 —— 而拒绝信息给的第一条出路「交给对应角色」那时不存在，architect 已经下场了 |
| `*.config.{ts,js,mjs}` 与 `pytest.ini` / `tox.ini` | `qa` | 测试框架配置按约定放仓库根，而 qa 原本只有四个测试**目录** —— 配 e2e 第一步就走不通。两族要一起给，否则 qa 配得了 vitest 配不了 pytest。`pyproject.toml` / `setup.cfg` 故意不给：那两个同时装着依赖与打包配置，不是测试专属文件 |
| `components/ pages/ lib/ styles/` 与 `.js` `.jsx` `.vue` `.html` `.scss` | `frontend-developer` | 原列表默认了「源码在 `src/` 或 `web/` 下且用 TypeScript」，Next.js / Nuxt / Vite 的标准布局全在范围外 |

放宽的是**仓库内的文件，不是状态目录**。裸扩展名模式（`*.md` / `*.json`）在 `fnmatch` 下跨 `/`，所以守卫对 `.workbench/` 下的路径只认显式以 `.workbench/` 开头的模式 —— 否则 `*.md` 会匹配 `artifacts/main/clarify/requirements.md`、`*.json` 会匹配 `contracts/events.json`，把下面那两段的隔离整个绕开。这条收窄同时补掉了 `*.json` 一直存在的同类缺口。同样的收窄也覆盖 `.claude/` `.codex/` `.agents/` —— 那里装的是权限引擎、hook 注册表与角色定义，任何角色都写不到，要改交回主线程（见 [permissions.md](permissions.md#第四层角色写入范围)）。多仓库工作区布局（存在 `repos/`）下还叠加 `scripts/` `repos.json` `.vscode/` 三个工作区级前缀，同属「主线程维护、角色只读」。`knowledge/` 是第四处：两个开发持有 `*.md`，不收窄的话他们能写知识条目，「knowledger 角色对沉淀质量负责」就落空了（`reviewer` 原来也持有 `*.md`，已按同一理由从范围里移除）。`references/` 是第五处：公共操作规范（全体角色必读的输出信封），任何角色只读 —— 改规范等于改角色定义，交回主线程。

**产物目录按阶段隔离**，不是给所有角色一个 `.workbench/artifacts/**`。这挡的是下游角色去改上游产物 —— `qa` 发现需求写得不清楚，顺手把 `requirements.md` 改成自己理解的样子，之后就没人知道原始需求是什么了。改上游产物要走上游角色，或者报回主线程。

阶段过了门禁之后还多一道：那份产物被登记成契约并锁定，连 owner 自己都要先 `contract unlock --reason` 申报才能改（见 [contracts.md](contracts.md#阶段产物)）。阶段隔离只在守卫判得出角色时生效，冻结不依赖角色 —— 主线程与非角色 agent 也拦得住。

## 不应修改业务代码的角色（含执法边界）

在工作台体系中，多达 7 个角色**按约定**不修改业务产品代码，分为两类。**先说清执法边界**，避免把「约定」误当成「守卫硬拦」：

- **守卫硬拦的只有两处**：① 受守前缀（`.workbench/`、`knowledge/`、`references/`、守卫本体等工作流核心路径）按角色范围执法；② 纯只读旁路角色（见第 2 类）根本没有 Write/Edit 工具，物理写不了任何文件。
- **产品源码（`repos/.source/**`）不按角色执法**：`_check_write_target` 在非受守前缀上直接放行（[wb_guard.py](../.claude/hooks/wb_guard.py) 的 `if not guarded: return`），这是有意的高容忍度设计，避免在正常开发动作上误拦。这里的越权防护靠：主线程**首写归属闸门**（`_attribution_gate` 硬拦未归属的产品源码写入）、子 agent 的**任务契约绑定与产物边界**、以及 agent 定义里的自律提示——不是角色范围硬编码。因此下表是**职责约定**，对**带 Write 工具的 subagent**（`analyst`/`qa`/`reviewer`）而言，若 Prompt 失控它在机制上仍能写产品源码；真要机械堵死需另加 subagent 源码闸门（当前未启用）。

### 1. 主干流水线中的无业务代码角色（约定，非产品源码硬拦）
`analyst`、`qa`、`reviewer` 配置了 Write 工具，其**默认范围不含业务代码**（`knowledger` 更被守卫收窄至仅 `knowledge/**`，`pm` 只写自己的产物目录）——但如上所述，范围只在受守前缀上被守卫强制，产品源码层面属职责约定。

| 角色 | 为什么不许改业务代码 |
| --- | --- |
| `analyst` | 分析阶段动手改代码是最常见的流程破坏 —— 边看边改会跳过方案设计，改完也没人评审 |
| `qa` | 自己顺手改会让缺陷统计失真，也绕过了开发的自检责任。缺陷必须打回成任务交开发修复 |
| `reviewer` | 评审者改代码就没人评审那次改动了 |
| `knowledger` | 知识条目是它唯一的产出。让它顺手改别的，沉淀就从「专职判断」退化成「谁顺手谁写」 |

`qa` 能写 `tests/**` 与测试框架配置（`*.config.ts` / `pytest.ini` 等）—— 搭测试与补测试是其本职，改业务代码不是。`reviewer` 的范围只剩自己阶段的 retro 产物。

### 2. 纯只读旁路执行体（Pure Read-only Sidecars）
`impact-scout`、`debugger` 与 `security-auditor` 的工具集直接被物理配置为：
`claude_tools = "Read, Grep, Glob, Bash"`（完全没有 Write 与 Edit 工具）。
守卫对非角色 agent 默认阻断受守前缀的写入，Bash 工具中的静态重定向与流写入同样受到深度解析拦截。三者只做探查、归因与审计，纯依靠结构化分析文本向编排者汇报，从机制层面杜绝任何越权修改或副作用。

## 每个角色的开工步骤

所有角色 agent 定义的开头都是同一个模式：

```
1. 读上游产物（有明确路径）
2. python3 .claude/hooks/wb.py task start <ID>     # 开发/测试角色
```

角色 subagent 的写入范围由 hook 载荷里的 `agent_type` 自动判定（值等于 agent 定义的 `name`，与 `ROLES` 同名），既不需要也不能自己 `role set` —— 跑了会被守卫当特权命令拦（见 [permissions.md](permissions.md#第四层角色写入范围)）。`role set` 只是编排者给非角色 agent（`general-purpose` 之类）收紧范围的兜底开关，不是角色 subagent 的开工步骤。

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
                           qa ──test-report.md──> reviewer ──沉淀候选──> knowledger
                                                                    │
                                                                    ▼
                                                             knowledge/ 条目（跨 flow 存活）
```

每个下游 agent 的定义里明确写了要读哪些上游产物的**具体路径**。`analyst` 的定义甚至规定：

> 先读 `.workbench/artifacts/<flow>/clarify/requirements.md`（当前 flow，指针见 `status` 根行）。没有它就停下来告知主线程 —— 无需求的分析是浪费。

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

## 旁路角色的运行机制与调度场景

旁路角色（Sidecar / On-Demand Roles）不绑定在默认的 6 阶段强制链条中，按需唤醒：

### 1. `impact-scout`（跨仓影响面调研，只读旁路）
* **定位**：在主 Agent 决定是否走完整 flow 之前使用。
* **场景**：起点只有一个模糊的业务需求、现象或跨仓改动，不确定涉及哪些仓库、跨仓契约在哪。
* **机制**：纯只读（`Read, Grep, Glob, Bash`）。输出四段式影响面清单，不下业务决策，由编排者据此决定直接改还是拉起 flow。

### 2. `debugger`（线上排障与故障归因，只读旁路）
* **定位**：在主 Agent 接收到线上 Bug、报警、崩溃堆栈或偶发异常时使用。
* **场景**：面对线上故障与调用异常，需快速定位到具体源码行，并判定是否为系统性缺陷。
* **机制**：纯只读。提取堆栈关键帧、根据路由表定位源码、分析触发条件并可选在本地执行无副作用的只读复现单测。输出故障表现、根因 `file:line`、影响链路与处置建议（微小修复直接修，复杂缺陷作为 clarify 输入拉起 flow）。

### 3. `dba`（数据库与平滑迁移专家，开发条件旁路）
* **定位**：`develop` 阶段按需动态插入。
* **场景**：方案设计涉及数据库 DDL/DML 变更、分库分表、大表加索引或历史数据回填。
* **机制**：写入范围限定在 `migrations/**`, `schemas/**`, `schema/**`, `sql/**`。强制执行双向对称迁移（Up/Down）、遵循 Expand & Contract 零停机演进模式、规避长事务与全表锁。

### 4. `security-auditor`（安全合规审计员，只读旁路）
* **定位**：在 `design`（威胁建模）或 `verify`（静态代码安全审计）阶段调用。
* **场景**：涉及认证授权系统、外部开放接口、加解密算法或用户隐私 PII 数据。
* **机制**：纯只读。针对 OWASP Top 10、水平/垂直越权（IDOR）、SQL/命令注入、硬编码凭据与依赖库高危 CVE 进行静态排查，输出安全阻断清单（Blockers）。

### 5. `devops`（发布与环境运维专家，交付旁路）
* **定位**：在 `submitter` 提交推送后，或在独立发布流程中调用。
* **场景**：代码已提交至分支，需确认远程 CI/CD 结果、核对容器与 K8s 编排并执行上线巡检。
* **机制**：写入部署物料配置与 `artifacts/<flow>/verify/deploy-report.md`。轮询远程构建状态、检查探针与环境变量凭据安全，并在异常时执行预备的回滚指令。

---

## 业务自定义 References 知识钩子机制

工作台采用**“业务事实与通用 Agent Prompt 解耦”**的设计原则：
1. **为什么不写死在 Prompt 中**：
   各个业务项目的数据库版本（MySQL vs Postgres）、监控平台（Prometheus vs Noah）、CI/CD 流水线（GitHub Actions vs GitLab）各不相同。若硬编码在 `agents/*.toml` 中，工作台在多项目分发时会造成严重污染。
2. **知识钩子分层约定**：
   每个角色在开工第一步统一读取 `references/workspace/<role>/index.md`，该目录下提供三件套标准模板：
   - `index.md`：参考文档索引与通用协议关联（如 `common.md`, `toolchain.md`, `repo-routing.md`）；
   - `role.md`：业务自定义规则（项目方言、大表清单、环境拓扑、监控平台地址、合规基线），支持业务项目在本地直接覆盖；
   - `skills.md`：该角色建议使用的 Skill 工具推荐表及优先级，由业务 workspace 按需选配。

---

## 定制与新增角色

### 调整写入范围

```bash
wb.py role scopes            # 看当前配置 + 冻结清单 + 解冻窗口
wb.py role scopes --reset    # 刷成 DEFAULT_ROLE_SCOPES（会覆盖定制，先存一份）
                             # 跨仓库布局下按仓库前缀重算，不是裸默认值
wb.py config set role_scopes.backend-developer \
    '["server/**","internal/**","migrations/**",".workbench/artifacts/*/develop/tasks/**"]'
```

改定制范围时**别把产物目录放宽回 `.workbench/artifacts/**`** —— 那会撤掉阶段隔离。要给某个角色额外的产物目录就明确列出来（如 `".workbench/artifacts/*/develop/tasks/**"`, `".workbench/artifacts/*/verify/**"`）。

### 新增一个角色的标准流程

1. **确定角色类型（主干 vs 旁路）**：
   - **主干角色**：承担 6 阶段必要产物，进入流水线状态推进依赖；
   - **旁路角色**：按需调用，若为纯只读则不配 Write/Edit 工具，若涉及受控写入（如 DBA/DevOps）则精确分配范围；
2. **单一事实源定义（TOML）**：
   在根目录创建 `agents/<角色名>.toml`，配置 `name`、`description`、`model`、`claude_tools` 与 `developer_instructions`。在 Instructions 开头加入 `references/workspace/<角色名>/index.md` 业务知识钩子；
3. **编译并同步全平台**：
   运行 `python3 scripts/generate_agents.py` 编译生成对应的 `agents/<角色名>.md`；
   为各端创建入口软链：
   - Claude 端：`.claude/agents/<角色名>.md -> ../../agents/<角色名>.md`
   - Codex 端：`.codex/agents/<角色名>.toml -> ../../agents/<角色名>.toml`
4. **配置工作台内核（如涉及状态或写入）**：
   - 若角色具备写入权限或需在任务图中分配（`wb.py task add --role <名>`），将名字加入 `wb_const.py` 的 `ROLES`；
   - 若角色参与代码/迁移编写，加入 `DEVELOPER_ROLES`；
   - 配置角色的自然阶段 `ROLE_NATURAL_PHASE` 与默认可写范围 `DEFAULT_ROLE_SCOPES`；
5. **初始化业务自定义 References 钩子**：
   在 `references/workspace/<角色名>/` 下创建 `index.md`、`role.md` 与 `skills.md`，并在 `references/workspace/roles.md` 登记索引；
6. **自检验证**：
   运行 `python3 scripts/generate_agents.py --check` 与 `python3 .claude/hooks/wb.py selfcheck` 确保全链路通过。

# Workbench 可视化看板 (Dashboard)

Workbench 可视化看板是一套为软件开发工作台量身打造的高性能、轻量级状态透视系统。它将多需求线（Flow）、主干六阶段流水线、任务有向无环图（DAG）、契约演进历史、Subagent 现场执行笔记以及门禁 ANSI 终端日志全面可视化。

---

## 核心设计原则

1. **高性能自包含 Web 服务（Self-Contained Full-Featured Dashboard）**：
   - 后端位于 `web/wb_dashboard.py`，基于业界成熟的 FastAPI + Uvicorn 框架驱动，提供高性能、类型安全且规范的声明式 REST API 与 SSE 事件推送服务，自带 `/docs` 交互式 Swagger 文档。
   - 根路径 `/` 与 `/index.html` 直接交付基于 `web/dashboard_template.html` 渲染的高性能自包含工业看板；API 探测与服务元数据清单收敛至 `/api`。
   - 刻意**不下发 CORS 跨域头**：页面由本服务同源交付，跨域读取不是需求；一旦放开 `Access-Control-Allow-Origin: *`，任意网页都能 fetch `127.0.0.1:<port>` 上的 `/api/task-detail`（返回源码 diff）并读出响应。
2. **纯原生零依赖架构（Zero-Dependency Pure Native Stack）**：
   - 彻底摒弃厚重的前端工程构建链与 Node.js 依赖，全看板采用标准现代 Web 栈（原生 ES6+ JavaScript、纯 CSS 变量体系、原生矢量 SVG 拓扑画布）。
   - 贯彻 Anti-Slop 工业品控准则：彻底摒弃系统原生彩色 Emoji（全量几何矢量图标）、几何圆角（4px/6px）、WCAG AA 高对比度、原生 `prefers-reduced-motion` 动效降级支持。
3. **无锁只读（Zero-Lock Read-Only Safety）**：
   - 严格以 `load_state(root, flow=flow, lock=False)` 提取主状态，彻底避免与主编排流程或并发 Subagent 竞争文件锁。
4. **安全路径守卫（Strict Path Traversal Guard）**：
   - 所有任务笔记、门禁日志与契约路径读取均强制经过 `safe_resolve_path(root, target)` 与 `Path.is_relative_to(root)` 校验，严防 `../` 越界读取与路径穿越。
5. **动静两用（Dual Mode: Live SSE & Static Baking）**：
   - **动态实时模式**：内置 SSE（Server-Sent Events）长连接通道，后台毫秒级监听 `.workbench` 关键文件变动并实时驱动前端平滑无刷重绘。
   - **静态导出模式 (`--export`)**：基于独立模板 `web/dashboard_template.html` 将所有 Flow 概览、DAG 拓扑、任务细节笔记、门禁日志、契约与审计流水预先烘焙注入单文件 HTML，断网环境下随时双击浏览或随 Git 归档。

---

## 核心功能组件

### 1. 响应式顶部与六阶段 Pipeline 阶梯
- **需求线切换器 (Flow Selector)**：支持在多个并行需求线（`main`、`feature-x` 等）间秒级切换。
- **阶段进度卡片 (Phase Ladder)**：按 `clarify → analyze → design → develop → verify → retro` 顺序展示当前处于哪个阶段。已完成阶段高亮绿色，进行中阶段呈现呼吸边框与高亮。
- **守卫状态哨兵 (Guard Status)**：实时检测是否存在全局角色范围锁（Role Lock）、契约争议熔断（Dispute Circuit-Breaker）或未认领仓库。

### 2. 原生 SVG 交互式 DAG 拓扑画布
- **拓扑分层算法**：基于任务前置依赖 `deps` 计算有向边，通过动态规划与拓扑分层算法分配任务卡片坐标深度 $L(v) = \max_{u \in deps(v)} (L(u) + 1)$。
- **循环依赖容错**：具备环路检测机制，发现循环依赖时优雅降级并标识黄色环路告警，绝不发生死循环。
- **节点状态视觉编码**：
  - `done`：稳定翠绿边框，显示产出代码文件计数与阶段归属。
  - `doing`：动态呼吸金光边框（带 CSS keyframe 动画），显示当前认领角色与剩余租约。
  - `blocked` / `stale`：警示红色，提示阻塞原因并高亮前置依赖。
  - `todo` / `skipped`：工业冷灰与幽灵紫徽标。
- **交互控制**：
  - 画布支持鼠标滚轮缩放（Zoom 0.25x ~ 2.5x）与按住平移（Pan）。
  - 快捷键支持：`F`（适配视图适应屏幕）、`0`（重置 100% 视图）、`+` / `-`（放大/缩小）、`Esc`（收起所有抽屉）。
  - 悬停任意任务节点，自动以渐变蓝/紫高亮其所有直接前置与后置依赖链。

### 3. 执行细节侧边抽屉 (Detail Drawer)
点击画布中任意任务节点即可唤出右侧详情抽屉，包含三个透视 Tab：
- **Tab 1: 现场与笔记 (Notes)**：
  - 内嵌轻量纯 JS Markdown 渲染器（支持标题、粗斜体、内联代码、表格、有序/无序列表、引用块）。
  - 自动渲染 Subagent 现场生成的 `artifacts/<flow>/develop/tasks/<id>-<role>.md`。
- **Tab 2: 代码改动树 (Code Changes)**：
  - 自动按多仓库项目聚合展示当前任务修改的产品源码清单，支持一键点击复制文件路径。
- **Tab 3: 复核依据 (Verification)**：
  - 自动呈现编排者在 `verification.md` 中记录的人工复核命令与验证输出证据。

### 4. 底部多功能控制台 (Bottom Console)
可折叠的工业暗黑风格底栏，包含五个专属面板：
- **门禁日志 (Gate Logs)**：
  - 内嵌终端黑底查看器，集成轻量纯 JS ANSI 转义解析状态机，完美还原 pytest、npm test、selfcheck 终端输出中的 16 色、256 色、粗体、暗色与成功/失败提示。
  - 支持下拉切换查看 `test`、`lint`、`build` 等不同门禁命令的原始执行日志。
- **契约与争议 (Contracts & Disputes)**：
  - 实时展示当前需求线已锁定的所有接口契约清单、Owner 角色、消费方列表、文件 SHA 散列及版本号。
  - 显式高亮显示当前处于开窗解冻（Unlocked）状态的契约与申报理由，以及争议熔断（Disputed）契约。
- **审计流水瀑布 (Audit Stream)**：
  - 时间轴倒序流式呈现 `audit.jsonl` 中的所有流水事件（阶段推进、任务流转、契约锁定、门禁校验等）。
- **实时事件 (Live Events)**：
  - 呈现通过 SSE 接收到的实时文件变动事件日志。
- **API 探测 (REST Probes)**：
  - 提供各 REST 接口的快捷测试入口与参数说明。

---

## 命令行与使用指南

### 1. 依赖安装 (Python Web)

看板后端基于 FastAPI + Uvicorn 驱动，首次运行前请先安装依赖：

```bash
pip install -r requirements.txt
```

### 2. 工作台主命令 (`wb dashboard`)

```bash
# 启动本地看板服务（默认端口 8088）
python3 .claude/hooks/wb.py dashboard

# 启动并自动在默认浏览器中打开页面
python3 .claude/hooks/wb.py dashboard --open

# 指定自定义端口号
python3 .claude/hooks/wb.py dashboard --port 8999

# 指定查看特定的 Flow 需求线
python3 .claude/hooks/wb.py dashboard --flow feature-b

# 导出静态单文件 HTML 离线报告（不启动 Web 服务）
python3 .claude/hooks/wb.py dashboard --export /path/to/report.html
```

### 2. 独立脚本命令 (`web/wb_dashboard.py`)

除 `wb.py` 转发外，也可直接运行脚本：

```bash
# 本地服务启动
python3 web/wb_dashboard.py --port 8088 --open

# 导出静态单文件报告
python3 web/wb_dashboard.py --export ./artifacts/dashboard_snapshot.html
```

### 3. CLI 参数完整说明

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--host` | `127.0.0.1` | 监听的主机绑定地址。 |
| `--port` | `8088` | HTTP 服务监听的本地端口。 |
| `--root` | 自动探测 | Workbench 工作区根目录（优先使用当前或父级含 `.workbench/` 的路径）。 |
| `--open` | `False` | 服务启动后自动调用系统默认浏览器打开看板。 |
| `--export <file>` | `None` | 将当前工作区完整状态烘焙为静态单文件 HTML 并退出，不启动 HTTP 服务。 |
| `--flow <name>` | 当前 Flow | 显式指定要查看或作为默认展示的需求线 Flow。 |
| `--quiet` | `False` | 静默模式，不输出访问日志。 |
| `--poll-interval` | `0.5` | SSE 状态轮询线程探测 `.workbench/` 关键文件变动的周期秒数。 |

---

## 静态单文件烘焙机制 (`--export`)

在 CI/CD 流水线构建、需求归档、阶段复盘或离线协同场景下，可生成完全自包含的静态单文件报告：

1. **数据烘焙（Data Baking）**：
   Python 后端将工作区内所有 Flow 的概览信息、任务 DAG、任务现场笔记、门禁日志、契约列表与审计流水聚合为 JSON 数据，并注入到 HTML 的 `<script id="__INITIAL_DATA__" type="application/json">` 标签中。
2. **闭合安全防注入**：
   在序列化过程中，自动对内容中的 `</script>` 进行安全转义（`<\/script>`），避免 HTML 解析器提前闭合脚本标签。
3. **前端自动离线适配**：
   前端在加载时探测到 `window.__INITIAL_DATA__`，将跳过网络 `fetch` 与 SSE `EventSource` 连接，直接使用烘焙数据即时渲染。在离线快照模式下：
   - 顶部状态徽标显示 `📸 静态快照 (生成时间: YYYY-MM-DD HH:MM:SS)`。
   - 依然支持切换不同 Flow、缩放与平移 DAG 画布、点击查看任务抽屉三 Tab、查看 ANSI 门禁终端日志与审计流水。

---

## 前端架构与极简体验指南

工作台看板采用**单文件自包含（Single-File Self-Contained）**架构，所有 HTML 骨架、样式、SVG 图元与交互脚本集中维护于 `web/dashboard_template.html`：

### 1. 终端用户产品形态 (Zero-Dependency User Experience)

在任何已配置 Python 3 依赖的环境下，执行主命令即可直接拉起并浏览看板，无需预装 Node.js、npm 或执行任何构建命令：

```bash
# 方式 1：工作台主入口命令（推荐）
python3 .claude/hooks/wb.py dashboard --open

# 方式 2：独立后端脚本
python3 web/wb_dashboard.py --open
```

服务在 `http://127.0.0.1:8088` 启动，浏览器直接呈现包含原生 SVG DAG 拓扑画布、代码 Diff 折叠透视、ANSI 彩色终端日志以及 SSE 实时双向联动的完整看板。

### 2. 动静两用双轨机制 (Dual-Mode Design)

模板文件原生内建了动态与静态双重运行分支：
- **动态实时模式 (Live Mode)**：当通过 HTTP 访问时，页面自动建立 `/api/events` SSE 长连接，并向 `/api/overview`、`/api/tasks` 拉取最新状态；文件变动时后台毫秒级广播并触发前端无刷新平滑重绘。
- **离线快照模式 (Offline Export)**：执行 `wb dashboard --export /path/to/report.html` 时，Python 后端将全 Flow 拓扑、任务 Diff、门禁日志与审计流水预先烘焙至 `<script id="__INITIAL_DATA__">`，生成的文件无需任何 Web 服务，双击即可脱网完整浏览。

### 3. 前端结构与模块分工

```
web/dashboard_template.html
├── <style>                    # 工业暗黑调色盘、几何规范、动画关键帧与 prefers-reduced-motion
├── <header> & <nav>           # Flow 切换器、六阶段 Pipeline 阶梯、角色锁与 SSE 状态哨兵
├── <svg id="dag-svg">         # 原生贝塞尔拓扑图，支持祖先/后代依赖高亮与视口缩放平移
├── <aside id="detail-drawer">  # 任务现场抽屉（Markdown 笔记 / 源码改动 Diff / 复核命令）
├── <footer id="bottom-console"># 多功能控制台（ANSI 终端 / 契约争议 / 审计流水 / SSE 日志）
└── <script>                   # 纯原生状态机、DAG 拓扑分层排版、Markdown 解析器与 ANSI 转换器
```

---

## REST API 接口清单

动态服务模式下提供以下标准端点：

| 端点 | 方法 | 查询参数 | 返回内容 |
| --- | --- | --- | --- |
| `/` 或 `/index.html` | `GET` | `flow=<name>` | 交付完整的可视化看板单文件前端（HTML）。 |
| `/api` | `GET` | — | 纯 API 服务的健康状态与元数据（JSON，含可用端点清单、版本）。 |
| `/api/overview` | `GET` | `flow=<name>` | 当前 Flow、Flow 列表、六阶段准出进度、角色锁与契约争议状态。 |
| `/api/tasks` | `GET` | `flow=<name>` | 经分层布局算法计算后的 DAG 节点（含坐标、宽高、深度）与有向边。 |
| `/api/task-detail` | `GET` | `id=<TID>&flow=<name>` | 任务详细信息（现场笔记 Markdown、代码改动树、人工复核命令）。 |
| `/api/gate-log` | `GET` | `name=<gate>&flow=<name>&format=json\|text` | 门禁执行日志原始文本或 JSON 包装。 |
| `/api/contracts` | `GET` | `flow=<name>` | 契约清单、开窗记录与争议熔断信息。 |
| `/api/audit` | `GET` | `flow=<name>&limit=<N>` | 审计事件流水列表。 |
| `/api/events` | `GET` | — | SSE 实时事件流，文件变动时广播 `event: state_change`。 |

---

## 自动化测试与质量保障

仪表板模块配备完整的全套测试矩阵（82 项测试全绿）：

```bash
# 全量测试自动发现与运行 (82 项单测)
python3 -m unittest discover -s web/tests -p "test_dashboard_*.py"

# 或单独运行指定模块单测：
python3 web/tests/test_dashboard_core.py     # 核心算法与数据提取单测
python3 web/tests/test_dashboard_server.py   # FastAPI 服务与 SSE 实时事件单测
python3 web/tests/test_dashboard_ui.py       # 画布布局与状态视觉编码单测
python3 web/tests/test_dashboard_details.py  # 执行细节抽屉与 ANSI 终端状态机单测
python3 web/tests/test_dashboard_export.py   # 静态离线单文件导出单测

# 工作台全量全链路自检
python3 .claude/hooks/wb.py selfcheck
```


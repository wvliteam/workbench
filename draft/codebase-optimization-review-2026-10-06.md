# 当前项目实现优化审查

日期：2026-10-06

审查范围：工作台核心脚本、Workflow Guard、任务与契约机制、Web 看板、角色定义、测试与前端静态资源。

审查方式：只读代码审查，重点关注过度复杂、重复实现、无效抽象、兼容分支、无引用资源和维护成本。正确性、安全性和性能问题只在与复杂度直接相关时记录。

## 结论

当前项目没有需要整体重构的问题。状态机、契约冻结、并发锁、CAS、跨 flow 聚合和权限守卫的复杂度大多有明确的并发或安全依据，应该保留。

优先级更高的优化集中在以下区域：

1. Web 看板的静态导出存在重复数据和重复扫描。
2. Web 层重复维护了一部分内核规则和兼容逻辑。
3. `selfcheck` 过度集中在单个超大函数中。
4. 任务 diff 基线与采集流程启动了过多 Git 子进程。
5. 部分前端 vendor 资源没有运行时引用。
6. 看板服务生命周期代码存在空实现和过宽异常捕获。

## 详细发现

### 1. 看板静态导出重复保存当前 flow 数据

标签：`shrink`

`build_static_export_data()` 已经把每条 flow 的完整数据放进 `flows_data[flow]`，随后又把当前 flow 的 `overview`、`tasks`、`task_details`、`contracts`、`audit` 和 `gate_logs` 复制到导出对象顶层。

证据：

- [web/wb_dashboard.py:634](/home/work/workspace/code/workbench/web/wb_dashboard.py:634)

影响：

- 导出 HTML 携带重复数据。
- 前端需要同时兼容两套字段位置。
- 顶层字段和 `flows_data` 未来可能出现同步漂移。

建议：

- 统一从 `flows_data[current_flow]` 读取当前 flow 数据。
- 删除导出对象顶层的重复字段。
- 保留 `current_flow` 和 `flows` 作为索引元数据。

预期收益：

- 减少静态导出体积。
- 减少一套数据契约。
- 降低模板与导出逻辑的同步成本。

### 2. 静态导出对任务详情重复扫描日志和文件

标签：`shrink`

静态导出遍历每个任务并调用 `get_task_detail()`。详情提取函数每次都会重新读取任务绑定日志、改动流水账、现场笔记、复核文档和 diff 文件。

证据：

- [web/wb_dashboard.py:595](/home/work/workspace/code/workbench/web/wb_dashboard.py:595)
- [web/wb_dashboard_core.py:380](/home/work/workspace/code/workbench/web/wb_dashboard_core.py:380)
- [web/wb_dashboard_core.py:484](/home/work/workspace/code/workbench/web/wb_dashboard_core.py:484)

影响：

- 任务数量增加后，导出成本接近“任务数 × 日志扫描次数”。
- 每个任务都重复解析相同的 JSONL 文件。
- 看板数据层和导出层之间的职责边界不清晰。

建议：

- 在 flow 级别一次性加载并索引：
  - `task-agents.jsonl`
  - `artifacts.jsonl`
  - 现场笔记
  - `verification.md`
  - `*.diff.json`
- 由批量索引组装所有任务详情。
- `get_task_detail()` 保留为单任务 API，但内部复用索引或共享读取结果。

预期收益：

- 降低静态导出耗时。
- 避免重复文件读取。
- 让在线 API 和静态导出共享同一份数据聚合逻辑。

### 3. Web 核心保留了一套过重的动态导入和 fallback 体系

标签：`yagni`

`wb_dashboard_core.py` 会扫描多个父目录寻找 `.claude/hooks`，随后捕获 `ImportError`，并创建一整套默认常量、空函数和 `None` fallback。调用侧继续通过 `getattr()`、`globals()` 和条件分支适配“内核存在”和“内核缺失”两种模式。

证据：

- [web/wb_dashboard_core.py:20](/home/work/workspace/code/workbench/web/wb_dashboard_core.py:20)
- [web/wb_dashboard_core.py:33](/home/work/workspace/code/workbench/web/wb_dashboard_core.py:33)
- [web/wb_dashboard_core.py:286](/home/work/workspace/code/workbench/web/wb_dashboard_core.py:286)
- [web/wb_dashboard_core.py:317](/home/work/workspace/code/workbench/web/wb_dashboard_core.py:317)
- [web/wb_dashboard_core.py:360](/home/work/workspace/code/workbench/web/wb_dashboard_core.py:360)

影响：

- 缺少工作台内核时可能静默降级为不完整行为。
- 看板与 CLI 的状态、flow 和兼容规则容易逐渐分叉。
- 类型和控制流被大量防御性分支打散。

建议：

- 将“工作台内运行”和“独立脚本运行”拆成明确的入口。
- Web 核心模块直接依赖共享内核。
- 独立入口只负责设置模块路径；依赖缺失时给出明确错误。
- 删除 `globals()`、`getattr()` 和空函数 fallback。

预期收益：

- 减少兼容分支。
- 提高失败可见性。
- 让核心规则保持单一来源。

### 4. `selfcheck` 集中在一个约 2,400 行的函数中

标签：`shrink`

`cmd_selfcheck()` 同时承担临时目录创建、环境变量清理、动态全链路测试、静态布局检查、兼容性检查和资源回收。

证据：

- [selfcheck.py:51](/home/work/workspace/code/workbench/.claude/hooks/wb_selfcheck.py:51)
- [selfcheck.py:2399](/home/work/workspace/code/workbench/.claude/hooks/wb_selfcheck.py:2399)
- [selfcheck.py:2465](/home/work/workspace/code/workbench/.claude/hooks/wb_selfcheck.py:2465)

额外问题：

- 部分检查通过 `inspect.getsource(cmd_task)` 读取实现源码，验证的是代码文本结构而不是外部行为。
- 一个场景失败会让定位成本上升。
- 新增测试容易继续把单函数做大。

建议：

- 按主题拆成独立场景函数：
  - `check_state_and_gates()`
  - `check_contracts()`
  - `check_guard()`
  - `check_flow_isolation()`
  - `check_concurrency()`
  - `check_compatibility()`
- 用统一 runner 收集场景名、失败信息和退出码。
- 将源码结构断言改成行为断言。

预期收益：

- 降低单次修改风险。
- 缩短失败定位时间。
- 让自检覆盖更容易扩展。

### 5. 任务 diff 基线实现偏重

标签：`shrink`

任务启动时会遍历外层仓库和 `repos/.source/` 下的仓库，执行 `git stash create`、`git status --porcelain`，并复制未跟踪文件。任务完成时又会按文件执行 `rev-parse`、`ls-files` 和 `diff`。

证据：

- [wb_cli.py:413](/home/work/workspace/code/workbench/.claude/hooks/wb_cli.py:413)
- [wb_cli.py:480](/home/work/workspace/code/workbench/.claude/hooks/wb_cli.py:480)
- [wb_cli.py:518](/home/work/workspace/code/workbench/.claude/hooks/wb_cli.py:518)
- [wb_cli.py:532](/home/work/workspace/code/workbench/.claude/hooks/wb_cli.py:532)
- [wb_cli.py:545](/home/work/workspace/code/workbench/.claude/hooks/wb_cli.py:545)

判断：

任务级改动隔离本身是必要能力，不能直接删除；可以优化实现。

建议：

- 每个仓库只采集一次基线。
- 一次性获得变更文件集合，减少逐文件 Git 探测。
- 只对最终变更文件生成 patch。
- 未跟踪文件优先记录内容哈希，只有确实需要生成 diff 时才复制备份。
- 将仓库发现、基线采集、diff 计算拆成三个小函数。

预期收益：

- 减少 `task start` / `task done` 的进程启动次数。
- 降低大工作区下的任务收尾成本。
- 让基线逻辑更容易测试。

### 6. 删除未被引用的前端 vendor 资源

标签：`delete`

模板实际引用的资源包括：

- `bootstrap.purged.css`
- `marked.min.js`
- `prism.min.js`
- `fuse.min.js`
- `purify.min.js`
- `anser.min.js`
- `diff2html.min.js`
- `cytoscape.min.js`
- `dagre.min.js`
- `cytoscape-dagre.min.js`

当前未发现运行时引用的候选资源：

- `web/vendor/bootstrap.min.css`
- `web/vendor/popper.min.js`
- `web/vendor/tippy.css`
- `web/vendor/tippy.umd.min.js`
- `web/vendor/diff2html.min.css`

证据：

- 模板引用位置：[web/dashboard_template.html:8](/home/work/workspace/code/workbench/web/dashboard_template.html:8)
- JavaScript vendor 引用位置：[web/dashboard_template.html:2451](/home/work/workspace/code/workbench/web/dashboard_template.html:2451)
- 静态导出内联清单：[web/wb_dashboard.py:675](/home/work/workspace/code/workbench/web/wb_dashboard.py:675)

预期可删除体积：

- 约 300 KB。

建议：

- 再执行一次全仓库引用检查。
- 删除确认无引用的文件。
- 同步删除对应的无效构建或维护说明。

### 7. 看板服务生命周期存在空实现和过宽异常捕获

标签：`yagni`

`DashboardServer.server_close()` 为空实现，资源关闭依赖 Uvicorn 的隐式处理；多个生命周期回调使用 `except Exception: pass`。

证据：

- [web/wb_dashboard.py:442](/home/work/workspace/code/workbench/web/wb_dashboard.py:442)
- [web/wb_dashboard.py:497](/home/work/workspace/code/workbench/web/wb_dashboard.py:497)
- [web/wb_dashboard.py:516](/home/work/workspace/code/workbench/web/wb_dashboard.py:516)

另外，`render_live_dashboard_html()` 存在重复 docstring。

建议：

- 在 `server_close()` 显式关闭底层 socket，并保证重复调用安全。
- 将 watcher 停止、SSE 唤醒和 socket 关闭收敛到统一的 shutdown 路径。
- 对可预期异常捕获具体类型；未知异常至少记录上下文。
- 删除重复 docstring。

预期收益：

- 生命周期语义更清楚。
- 降低异常被静默吞掉的风险。
- 测试失败时更容易定位资源泄漏或服务提前退出。

### 8. Web 层重复维护内核规则

标签：`reuse`

看板层自行实现了 flow 名校验、路径安全、state fallback、旧布局兼容和 gate 日志定位，而这些规则已经存在于 `wb_core` / `wb_const`。

证据：

- [web/wb_dashboard_core.py:85](/home/work/workspace/code/workbench/web/wb_dashboard_core.py:85)
- [web/wb_dashboard_core.py:113](/home/work/workspace/code/workbench/web/wb_dashboard_core.py:113)
- [web/wb_dashboard_core.py:603](/home/work/workspace/code/workbench/web/wb_dashboard_core.py:603)

建议：

- 在共享内核增加少量只读适配函数：
  - 指定 flow 的状态读取。
  - 指定 flow 的门禁日志定位。
  - 安全资源路径解析。
- Web 层只负责 HTTP 参数、响应格式和展示数据。
- 删除看板中重复的旧布局兼容分支。

预期收益：

- CLI 和看板共享同一套边界规则。
- 减少跨模块行为漂移。
- 后续内核升级时只需维护一处。

## 不建议优先削减的复杂度

### 状态锁和 CAS

`.claude/hooks/wb_core.py` 中的 `flock`、`state_rev` 和门禁前后重读是并发状态一致性的基础。并行 `task done`、契约更新和阶段推进都可能同时发生，这些机制属于必要保护。

### 冻结清单与解冻窗口

契约冻结、阶段产物冻结、解冻申报和 `bump` 通知消费方共同构成工作流的约束边界，不能为了减少文件或函数数量而合并掉。

### Workflow Guard 的多层解析

`Write/Edit`、Bash 重定向、`cd` 后写入、软链接挂载、未知 agent 身份和特权命令分别覆盖不同绕过面。除非有新的统一解析器和等价测试，不建议直接删除这些分支。

### 任务级 baseline/diff 功能

该功能实现较重，但解决了并行任务之间的改动归属问题。建议做内部收敛，不建议移除能力。

## 验证结果

### 工作台自检

命令：

```bash
python3 .claude/hooks/wb.py selfcheck
```

结果：通过。

自检输出包含：

- 动态全链路通过。
- 静态布局检查通过。
- selfcheck 全部通过。

### Web 测试

命令：

```bash
python3 -m unittest discover -s web/tests -p 'test_dashboard_*.py'
```

结果：未全通过，93 个测试中有 6 个失败、4 个错误。

已观察到的问题：

- vendor JavaScript 的 MIME 类型断言与当前响应头不一致。
- gate 日志读取为空。
- 部分测试出现 HTTP 连接拒绝。
- `test_dashboard_core` 中真实工作区任务角色与测试预期不一致。

### pytest 运行环境

命令：

```bash
python3 -m pytest -q web/tests
```

结果：当前环境发生 Python 段错误，崩溃位置在 pytest capture 初始化阶段，尚未进入业务测试执行。

判断：

- Web 测试失败需要单独建立验证任务。
- 当前失败不能直接归因于上述复杂度问题。
- 在优化看板前，建议先修复测试服务器生命周期、gate 日志 fixture、MIME 类型断言和测试数据基线。

## 推荐实施顺序

### 第一批：低风险清理

1. 删除确认无引用的 vendor 文件。
2. 删除重复 docstring 和空的 `server_close()`，补充显式资源关闭。
3. 收紧看板服务中的宽泛异常捕获。

### 第二批：看板数据路径收敛

1. 删除静态导出的顶层重复字段。
2. 为任务详情建立 flow 级批量索引。
3. 将 Web 层重复的 flow、路径和日志规则下沉到共享内核。

### 第三批：内核维护性优化

1. 拆分 `selfcheck` 场景函数。
2. 将源码结构断言改为行为断言。
3. 收敛任务 baseline/diff 的 Git 调用。

## 结论性建议

当前最值得投入的方向是“减少重复数据读取和重复规则实现”，而不是继续扩展权限守卫的功能面。先处理看板和自检的维护成本，可以在不改变工作流语义的前提下减少代码量、缩短验证时间，并降低后续功能变更的回归风险。

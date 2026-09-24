# `web/dashboard_template.html` 页面设计审计

## 文档信息

- 审计日期：2026-09-24
- 审计对象：`web/dashboard_template.html`
- 关联资源：`web/vendor/`、`web/wb_dashboard.py`、`web/wb_dashboard_core.py`
- 审计类型：只读静态审计
- 本轮未修改产品源码，也未进行浏览器截图或 Lighthouse 实测

## 设计定位

### Design Read

这是一个面向高频技术用户的本地开发工作台 Dashboard，核心任务是查看 Flow 阶段、浏览任务 DAG、追踪状态、读取门禁日志和查看任务细节。当前页面采用 GitHub 风格的深色工业控制台语言，而不是营销页或内容型产品页面。

`design-taste-frontend` 技能明确不覆盖 Dashboard、密集型产品 UI 和数据表格，因此本文不套用营销页的 Hero、Bento、图片资产等规则，而是使用该技能中的反模板、层级、动效、可访问性和性能原则进行产品界面审计。

### 建议设计参数

| 参数 | 建议值 | 原因 |
| --- | ---: | --- |
| `DESIGN_VARIANCE` | 3 | DAG、工具栏和控制台需要稳定的工程化布局，不适合强不对称构图 |
| `MOTION_INTENSITY` | 3 | 实时状态需要反馈，但不应让高亮、阴影和持续动画干扰阅读 |
| `VISUAL_DENSITY` | 8 | 任务、阶段、日志、契约和 Diff 都是高密度信息 |

## 总体结论

### 结论

页面的桌面端信息架构是合理的，主任务区域明确，视觉风格也与 Workbench 的工程属性匹配。当前主要问题不是需要换一套设计，而是现有方案在窄屏、键盘操作、屏幕阅读器、持续运行和大规模 DAG 下暴露出结构性短板。

建议采用“保留架构，做局部收敛”的方式，不建议换成 React、Tailwind 或新的 UI 组件库，也不建议重做成营销型 Bento 页面。

### 维度判断

| 维度 | 判断 | 说明 |
| --- | --- | --- |
| 桌面信息架构 | 良好 | Header、Pipeline、Toolbar、DAG、抽屉和控制台职责清楚 |
| 桌面视觉一致性 | 良好 | 深色工业风统一，但近黑色表面过多，层级略浑浊 |
| 移动端布局 | 较弱 | 主要依靠横向滚动，没有真正的内容重排 |
| 可访问性 | 中等偏弱 | 基础语义存在，但抽屉、Tabs、搜索和动态状态缺少完整语义 |
| 首屏性能 | 中等 | 本地环境可接受，但第三方资源和内联资源偏重 |
| 长时间运行稳定性 | 中等 | SSE、重排、搜索和事件日志需要进一步限流或增量化 |
| 可维护性 | 中等 | 当前渲染器已经切换到 Cytoscape，但仍保留部分旧 SVG CSS 和状态字段 |

## 现有设计的优点

### 1. 主次关系明确

页面从上到下依次提供项目上下文、六阶段流程、任务统计与过滤工具，剩余空间交给 DAG。这条路径符合用户的主要工作顺序，见 `web/dashboard_template.html:2007`。

`main.canvas-wrapper` 使用剩余空间承载任务拓扑，避免日志和详情面板默认抢占首屏，见 `web/dashboard_template.html:2107`。

### 2. 状态反馈覆盖较完整

页面已经提供：

- 空任务引导
- 日志和契约的骨架屏
- SSE 连接中、已连接和重连状态
- 门禁失败、超时、豁免和未执行状态
- 抽屉内的笔记、代码变更和复核证据空状态
- `prefers-reduced-motion` 的降级规则

这比只完成“成功数据态”的界面完整很多。

### 3. DAG 的交互模型合适

Cytoscape 负责缩放、平移、选择和依赖关系，高亮逻辑使用 `predecessors()` 和 `successors()`，没有继续维护一套手写 SVG 拓扑，见 `web/dashboard_template.html:2642` 和 `web/dashboard_template.html:2939`。

### 4. SSE 刷新已经考虑了真实使用场景

页面会合并短时间内的多个文件变更事件，并在刷新时保留当前视角，见 `web/dashboard_template.html:3825`。这是正确方向，避免每次 `state.json`、审计日志和任务笔记同时变化时连续重绘。

## 详细问题与建议

## P0：应优先处理

### 1. 响应式布局不是重排，而是隐藏和横向滚动

当前响应式规则主要位于 `web/dashboard_template.html:1914`：

- `900px` 以下 Toolbar 允许横向滚动
- `640px` 以下只隐藏品牌文字
- 详情抽屉改为全宽

但 Header、Toolbar、Pipeline 控件和底部控制台没有针对窄屏重新组织。实际结果是：

- 任务过滤器、搜索框、缩放控件可能挤在同一行
- 重要操作被藏在横向滚动区域内
- 控制台 Tabs 在手机上容易变成不可见的溢出内容
- 26px 高的按钮不适合触屏操作
- 任务详情抽屉虽然铺满屏幕，但顶部和内容区没有考虑移动端安全区

建议：

1. `900px` 以下将 Toolbar 改为两行 Grid。
2. 搜索框占满第一行，过滤器和缩放控件放第二行。
3. `640px` 以下将高级视图控制放入一个“更多”菜单，而不是依赖横向滚动。
4. 触屏按钮的有效点击区域提升到 40px 至 44px，视觉图标可以保持较小。
5. 使用 `100dvh` 替代 `100vh`，避免移动端浏览器地址栏变化造成画布裁切，当前定义见 `web/dashboard_template.html:47`。

### 2. 抽屉、Tabs 和搜索缺少完整键盘语义

当前抽屉结构位于 `web/dashboard_template.html:2297`，但缺少：

- `role="dialog"`
- `aria-modal="true"`
- `aria-labelledby`
- 打开时的焦点转移
- 关闭后的焦点恢复
- 抽屉内部焦点约束

详情 Tabs 位于 `web/dashboard_template.html:2339`，当前只通过 CSS class 切换状态，没有：

- `role="tablist"`
- `role="tab"`
- `aria-selected`
- `aria-controls`
- `role="tabpanel"`

任务搜索的结果项使用可点击 `div`，见 `web/dashboard_template.html:2813`。依赖任务使用可点击 `span`，见 `web/dashboard_template.html:2994`。这些元素无法通过 Tab、Enter 和 Space 完成同样的操作。

建议优先将它们改为原生 `button`，复杂场景再补 ARIA。原生控件比给 `div` 和 `span` 拼交互语义更简单可靠。

### 3. 动态状态没有完整暴露给辅助技术

进度条只设置了 `role="progressbar"` 和 `aria-label`，见 `web/dashboard_template.html:2065`，但没有动态更新：

- `aria-valuemin="0"`
- `aria-valuemax="100"`
- `aria-valuenow`

SSE 状态、门禁结果和统计数字也没有统一的 `aria-live` 区域。建议只对重要变化设置 `aria-live="polite"`，不要让每条实时事件都朗读。

### 4. 小字号低对比度

页面大量使用 10px 和 11px 文字。待处理阶段英文标签的颜色是 `#636e7b`，背景接近 `#111622`，对比度约为 `3.48:1`，不满足小字号正文的 WCAG AA 要求，见 `web/dashboard_template.html:313`。

建议：

- 普通辅助文本最低使用 11.5px 至 12px。
- 10px 只用于非关键信息，例如版本号或代码标签。
- 将待处理阶段英文颜色提高到接近 `--text-muted` 的级别。
- 不要只依赖边框颜色表达节点状态，保留可读的状态文本或图标。

## P1：建议在下一轮处理

### 5. DAG 更新路径仍然偏重

`renderDAG()` 在数据签名变化后会执行完整的元素删除、重新添加和 Dagre 布局，见 `web/dashboard_template.html:2835` 和 `web/dashboard_template.html:2910`。

当前签名包含状态、标题、角色、阶段、产物数量和边关系，因此普通任务状态变化也会触发全量重建。任务数量较少时没有问题，但任务达到数百个后会带来：

- Cytoscape 元素重新创建
- 节点布局重新计算
- 选中态和部分临时交互状态重置
- CPU 和主线程耗时增加

建议分三类更新：

1. 只有状态变化：只更新节点 style。
2. 只有产物数量变化：只更新节点 data 和 label。
3. 依赖关系、标题长度或节点尺寸变化：才重新运行 Dagre。

当前的签名短路逻辑仍应保留，它对 SSE 高频事件合并是有价值的。

### 6. 搜索需要 debounce 和事件委托

搜索输入事件当前每次输入都直接调用 `handleTaskSearch()`，见 `web/dashboard_template.html:4005`。函数内部会：

- 执行 Fuse 搜索
- 遍历全部节点
- 遍历全部边并添加淡化 class
- 重建下拉结果 HTML
- 为每个结果重新绑定 click 事件

建议：

- 增加 100ms 至 150ms debounce。
- 使用 `requestAnimationFrame` 合并 Cytoscape class 更新。
- 结果列表使用一个父容器事件委托，不要每次给 6 个结果重新绑定事件。
- 任务数量很大时限制 Fuse 返回数量和高亮节点数量。

### 7. 首屏加载了过多第三方库

模板在 `web/dashboard_template.html:2380` 一次性加载了所有 vendor 资源。当前 `web/vendor/` 下的原始资源总量约为 1.2MB，主要包括：

- `cytoscape.min.js`
- `dagre.min.js`
- `diff2html.min.js`
- `marked.min.js`
- `prism.min.js`
- `purify.min.js`
- `anser.min.js`
- `fuse.min.js`
- `popper.min.js`
- `tippy.umd.min.js`

建议保留首屏依赖：

- Cytoscape
- Dagre
- Fuse
- Purify

按需加载：

- 打开 Diff 时加载 Diff2Html
- 打开笔记代码块时加载 Marked 和 Prism
- 打开门禁日志时加载 Anser

Tippy 和 Popper 也可以直接删除，恢复原生 `title`，或只保留 CSS tooltip。当前页面是本地工程工具，不需要为了少量提示引入额外运行时。

### 8. 事件日志会无限增长

`logEvent()` 使用 `prepend()` 持续添加节点，见 `web/dashboard_template.html:2456`。用户长时间打开页面时，事件 DOM 会不断增长。

建议保留最近 200 条事件：

1. 插入新事件。
2. 超过上限时删除末尾节点。
3. 清空按钮继续保留。

这是一个简单的固定上限，足以覆盖常规调试场景，不需要复杂日志虚拟化。

### 9. 刷新请求存在竞态

Flow 切换、手动刷新和 SSE 重载都可能调用 `loadData()`。如果旧请求晚于新 Flow 请求返回，旧响应可能覆盖当前界面。

建议给 `loadData()` 增加一个请求序号或 `AbortController`：

- 新请求开始时取消旧请求。
- 或只接受最后一次请求的响应。
- 渲染前确认响应 Flow 仍等于 `AppState.currentFlow`。

### 10. resize 处理可以合并

窗口 resize 当前直接调用 `cy.resize()`，见 `web/dashboard_template.html:4061`。拖动窗口时会触发大量事件。

建议使用 `ResizeObserver` 监听 `#dag-cy`，或通过一个 `requestAnimationFrame` 标记把同一帧内的多次 resize 合并成一次。

## P2：视觉和维护性优化

### 11. 深色表面层级过多

当前页面使用了很多接近的表面颜色，例如：

- `#090d13`
- `#0c1018`
- `#0d121b`
- `#0e131b`
- `#101520`
- `#111622`
- `#131822`

这些颜色单独看都合理，但组合后容易让用户分不清“页面背景”“容器”“浮层”和“选中内容”。

建议收敛成三层：

| 层级 | 用途 |
| --- | --- |
| Surface 0 | 画布和页面底色 |
| Surface 1 | Header、Pipeline、Toolbar、控制台 |
| Surface 2 | 抽屉、弹层、选中卡片、搜索结果 |

边框只保留一套默认颜色和一套强调颜色，减少每个模块自行定义近似颜色。

### 12. 嵌套边框和卡片略多

详情抽屉里同时存在抽屉背景、Meta 容器、依赖容器、Tabs、Markdown 容器、代码容器和文件卡片。高密度信息适合使用少量分组线和留白，不需要每个层级都加背景、边框和圆角。

建议：

- Meta 信息保留一个容器。
- 依赖关系使用分组标题和底部分隔线，不再单独包两层卡片。
- Markdown、Diff 和复核证据保留内容边界，但降低阴影和嵌套背景。

### 13. 持续阴影动画可能造成额外绘制

进行中节点使用 `box-shadow` 脉冲动画，见 `web/dashboard_template.html:643`。节点较多时，持续阴影比只改变边框颜色更容易触发绘制压力。

建议把动态反馈集中到：

- 顶部 SSE 状态点
- 当前 Pipeline 阶段
- 当前选中节点

普通 `doing` 节点使用静态黄色边框即可。

### 14. 旧渲染器 CSS 可以清理

当前 Cytoscape 样式定义在 `web/dashboard_template.html:2488`，但模板前部仍保留 `.dag-edge`、`.dag-node-wrapper`、`.node-card` 等旧 SVG DOM 渲染器 CSS，见 `web/dashboard_template.html:583`。

通过搜索可以看到这些选择器只出现在 CSS 和 reduced-motion 覆盖规则中，没有当前 HTML 或 JavaScript 渲染结果使用。清理前需要同步更新仍检查这些字符串的测试，例如 `web/tests/test_dashboard_ui.py:162`。

同时，`AppState` 中的 `scale`、`translateX`、`translateY`、`isDragging`、`dragStartX` 和 `dragStartY` 当前也像是旧手写平移逻辑遗留字段，见 `web/dashboard_template.html:2408`，可以在确认无外部依赖后删除。

### 15. 内联 style 较多

骨架屏、按钮和动态容器中存在多处 inline style，例如 `web/dashboard_template.html:2190`。这会让主题调整和响应式覆盖变得困难。

建议只将真正动态的值留在 inline style 中，例如进度百分比；固定的尺寸、间距和背景色迁移到 CSS class。

## 安全与数据边界

### 1. 日志事件不要直接拼接 HTML

当前 `logEvent()` 将传入文本直接写入 `innerHTML`，见 `web/dashboard_template.html:2461`。该函数会处理 SSE 文件变更和错误信息，文件路径或错误字符串理论上可以来自外部状态。

建议改为：

- 时间节点使用 `textContent`
- 消息节点使用 `textContent`
- 只有确实需要图标时才单独创建受控 SVG 节点

### 2. 实时页面 JSON 注入应与静态导出保持一致

静态导出路径已经将 `</script>` 替换为安全形式，见 `web/wb_dashboard.py:667`。实时页面路径只使用 `json.dumps()`，见 `web/wb_dashboard.py:274`，建议补上同等处理，避免任务标题、Flow 名称或其他数据提前结束 JSON script 节点。

### 3. 动态 HTML 入口应保持统一转义策略

页面已经正确使用 `escapeHtml()` 和 DOMPurify 的位置应继续保留。后续修改时不要把以下数据直接插入模板字符串：

- 任务标题
- 文件路径
- Flow 名称
- 契约名称
- 门禁日志
- Diff 文本

最好将“可信的固定 UI 片段”和“外部数据文本”分开创建，减少手工拼接 HTML 的范围。

## 推荐的最小实施计划

### 第一阶段：低风险可见收益

1. 修复移动端 Toolbar 和 Header 重排。
2. 将搜索结果和依赖项改成原生按钮。
3. 为抽屉和 Tabs 补语义属性与焦点处理。
4. 补充进度条的 ARIA 数值属性。
5. 提高待处理阶段小字号文本对比度。

### 第二阶段：稳定性和性能

1. 给日志增加 200 条上限。
2. 给搜索增加 debounce 和事件委托。
3. 给 `loadData()` 加请求取消或请求序号。
4. 用 `ResizeObserver` 或 RAF 合并 resize。
5. 将 Diff、Markdown、ANSI 渲染器改为按需加载。

### 第三阶段：结构收敛

1. 把表面颜色收敛成三层 Token。
2. 减少详情抽屉中的嵌套卡片。
3. 把持续阴影动画改成静态状态边框。
4. 删除旧 SVG 渲染器 CSS 和未使用状态字段。
5. 让 DAG 状态更新走增量路径，避免不必要的 Dagre 重排。

## 验收清单

- [ ] 375px、640px、900px 和 1280px 宽度下 Header、Toolbar、Pipeline 和控制台都可操作
- [ ] 所有可点击任务结果和依赖项均可通过键盘触发
- [ ] 抽屉打开后焦点进入，关闭后焦点回到触发节点
- [ ] Tabs 可通过方向键或 Tab 操作，当前状态对屏幕阅读器可见
- [ ] 进度条和 SSE 状态变化具有合适的 ARIA 语义
- [ ] 搜索输入不再每个字符立即触发完整 DOM 和 Cytoscape 更新
- [ ] 连续 SSE 事件不会导致完整 DAG 重排风暴
- [ ] 长时间运行后事件日志 DOM 数量保持上限
- [ ] 实时页面和静态导出都安全处理 `</script>` 字符串
- [ ] Lighthouse 或浏览器性能面板确认首屏资源和交互延迟没有明显回归

## 不建议做的事情

- 不建议为了视觉升级引入新的前端框架。
- 不建议把开发工具改造成营销页式的卡片墙或 Bento 布局。
- 不建议为少量 tooltip 继续扩大第三方运行时依赖。
- 不建议在没有真实性能数据前引入 WebGL、复杂滚动动画或全局粒子背景。
- 不建议为了支持所有主题先重写一套完整 Design System，当前最有价值的是先收敛三层表面 Token。

## 最终判断

当前页面作为桌面端本地 Workbench Dashboard 是合理的，应该做“工程化收敛”而不是“视觉重做”。优先修复窄屏可用性、键盘语义、动态 HTML 安全和 DAG 增量更新，完成后再处理颜色层级与旧 CSS 清理，投入产出比最高。

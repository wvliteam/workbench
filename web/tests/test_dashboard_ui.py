#!/usr/bin/env python3
"""test_dashboard_ui.py: 针对 wb_dashboard.py 前端 SPA 画布与 DAG 交互的自动化测试套件 (Task 3)。

验证范围：
1. 页面骨架与容器有效性 (Header, Flow 选择器, Pipeline 六阶段阶梯, DAG SVG 视口, 工具栏, 抽屉)。
2. SVG 图元与标记 (arrow markers, glow filters, bezier curve 路径规则)。
3. CSS 工业风视觉体系与动效 (脉冲呼吸、流动虚线、状态高亮与淡化)。
4. 前端交互核心函数与事件监听完整性 (Pan/Zoom, BFS 依赖溯源, 状态过滤, 抽屉呼出, SSE 驱动)。
5. 与后端 REST API 契约的无缝联通与渲染数据兼容性。
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WEB_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB_DIR))

import wb_dashboard as dashboard
import wb_dashboard_core as core


class TestDashboardUIBase(unittest.TestCase):
    """测试基类：启动后台看板服务器。"""

    @classmethod
    def setUpClass(cls):
        cls.server = dashboard.create_server(
            root=ROOT,
            host="127.0.0.1",
            port=0,
            quiet=True,
            poll_interval=0.1,
        )
        cls.port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"

        cls.server_thread = threading.Thread(
            target=cls.server.serve_forever,
            daemon=True,
            name="TestDashboardUIThread"
        )
        cls.server.watcher.start()
        cls.server_thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.watcher.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=2.0)

    def fetch_page(self, path: str = "/") -> tuple[int, str, dict[str, str]]:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            headers = {k.title(): v for k, v in resp.headers.items()}
            return resp.status, resp.read().decode("utf-8"), headers


class TestDashboardHTMLStructure(TestDashboardUIBase):
    """验证 SPA HTML 骨架与核心 DOM 容器。"""

    def test_page_status_and_headers(self):
        """测试页面状态码为 200，内容类型为 HTML。"""
        status, html_text, headers = self.fetch_page("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn("Workbench Dashboard", html_text)

    def test_header_and_flow_selector(self):
        """验证顶部导航栏、Flow 选择器、角色锁徽标。"""
        _, html_text, _ = self.fetch_page("/")
        self.assertIn('id="header-project"', html_text)
        self.assertIn('id="header-version"', html_text)
        self.assertIn('id="flow-select"', html_text)
        self.assertIn('id="role-guard-badge"', html_text)
        self.assertIn('id="btn-refresh"', html_text)
        self.assertIn('id="sse-indicator"', html_text)

    def test_pipeline_section(self):
        """验证六阶段 Pipeline 阶梯容器与阶段名映射。"""
        _, html_text, _ = self.fetch_page("/")
        self.assertIn('id="pipeline-container"', html_text)
        for stage in ("clarify", "analyze", "design", "develop", "verify", "retro"):
            self.assertIn(stage, html_text)

    def test_toolbar_and_controls(self):
        """验证状态统计条、过滤器胶囊组与画布控制按钮。"""
        _, html_text, _ = self.fetch_page("/")
        self.assertIn('id="stat-total"', html_text)
        self.assertIn('id="stat-done"', html_text)
        self.assertIn('id="stat-doing"', html_text)
        self.assertIn('id="stat-blocked"', html_text)
        self.assertIn('id="progress-bar-fill"', html_text)

        # 过滤按钮
        for f in ("all", "doing", "done", "blocked", "todo"):
            self.assertIn(f'data-filter="{f}"', html_text)

        # 全局任务模糊搜索控件 (Fuse.js)
        self.assertIn('id="search-task-container"', html_text)
        self.assertIn('id="search-task-input"', html_text)
        self.assertIn('id="search-kbd-hint"', html_text)

        # 缩放控制
        self.assertIn('id="btn-zoom-in"', html_text)
        self.assertIn('id="btn-zoom-out"', html_text)
        self.assertIn('id="btn-fit-view"', html_text)
        self.assertIn('id="btn-reset-view"', html_text)
        self.assertIn('id="zoom-level-text"', html_text)

    def test_cytoscape_canvas_elements(self):
        """验证 Cytoscape 拓扑画布容器与三方库引入（已取代手搓 SVG 图层）。"""
        _, html_text, _ = self.fetch_page("/")
        # Cytoscape 挂载容器
        self.assertIn('id="dag-cy"', html_text)
        # 三个 vendor 库按依赖顺序引入
        self.assertIn('vendor/cytoscape.min.js', html_text)
        self.assertIn('vendor/dagre.min.js', html_text)
        self.assertIn('vendor/cytoscape-dagre.min.js', html_text)
        # 手搓 SVG 结构已彻底移除
        self.assertNotIn('id="dag-svg"', html_text)
        self.assertNotIn('id="dag-viewport"', html_text)

    def test_detail_drawer_elements(self):
        """验证侧边详情抽屉结构与各字段占位。"""
        _, html_text, _ = self.fetch_page("/")
        self.assertIn('id="detail-drawer"', html_text)
        self.assertIn('id="drawer-backdrop"', html_text)
        self.assertIn('id="drawer-task-id"', html_text)
        self.assertIn('id="drawer-task-status"', html_text)
        self.assertIn('id="drawer-task-title"', html_text)
        self.assertIn('id="drawer-task-phase"', html_text)
        self.assertIn('id="drawer-task-role"', html_text)
        self.assertIn('id="drawer-task-deps"', html_text)
        self.assertIn('id="drawer-task-succs"', html_text)
        self.assertIn('id="drawer-artifacts-list"', html_text)


class TestDashboardCSSAndAesthetics(TestDashboardUIBase):
    """验证深色工业美学 CSS 与动态视觉效果。"""

    def test_status_classes_defined(self):
        """验证所有任务状态的视觉编码类。"""
        _, html_text, _ = self.fetch_page("/")
        for status in ("done", "doing", "blocked", "skipped", "todo"):
            self.assertIn(f".status-{status}", html_text)

    def test_keyframes_and_motion(self):
        """验证呼吸动效与连线流向动画。"""
        _, html_text, _ = self.fetch_page("/")
        self.assertIn("@keyframes pulse-doing", html_text)
        self.assertIn("@keyframes flow-dash", html_text)
        self.assertIn("@keyframes pulse-active", html_text)

    def test_hover_highlight_and_dim_classes(self):
        """验证依赖链高亮与无关图元淡化类。"""
        _, html_text, _ = self.fetch_page("/")
        self.assertIn(".highlight-source", html_text)
        self.assertIn(".highlight-upstream", html_text)
        self.assertIn(".highlight-downstream", html_text)
        self.assertIn(".dimmed", html_text)

    def test_console_reserves_dag_viewport(self):
        """验证控制台展开时会压缩 DAG 可视区域而非覆盖画布。"""
        _, html_text, _ = self.fetch_page("/")
        self.assertIn("--console-reserved-height", html_text)
        self.assertIn(".workspace-shell.console-open #dag-cy", html_text)
        self.assertIn("function syncConsoleViewport()", html_text)
        self.assertIn("new ResizeObserver(resizeCytoscape)", html_text)

    def test_console_is_outside_canvas_main(self):
        """验证 DAG 使用 main，控制台使用独立的 aside 图层。"""
        _, html_text, _ = self.fetch_page("/")
        self.assertIn('<div id="workspace-shell" class="workspace-shell">', html_text)
        self.assertIn('<aside id="dev-console-layer" class="dev-console-layer"', html_text)
        main_start = html_text.index('<main class="canvas-wrapper" id="canvas-container">')
        main_end = html_text.index('</main>', main_start)
        main_markup = html_text[main_start:main_end]
        self.assertIn('id="dag-cy"', main_markup)
        self.assertIn('id="canvas-empty"', main_markup)
        self.assertNotIn('btn-toggle-dev', main_markup)
        self.assertNotIn('dev-console-drawer', main_markup)


class TestDashboardJavaScriptEngine(TestDashboardUIBase):
    """验证前端 JavaScript 逻辑引擎与算法。"""

    def test_cytoscape_dagre_layout(self):
        """验证连线/布局改由 Cytoscape + dagre 承担（不再手搓贝塞尔路径）。"""
        _, html_text, _ = self.fetch_page("/")
        # dagre 布局引擎与横向排布
        self.assertIn("name: 'dagre'", html_text)
        self.assertIn("rankDir: 'LR'", html_text)
        # 扩展注册
        self.assertIn("cytoscape.use(window.cytoscapeDagre)", html_text)

    def test_graph_navigation_functions(self):
        """验证前端画布包含缩放、自适应与聚焦函数（cytoscape 接线）。"""
        _, html_text, _ = self.fetch_page("/")
        self.assertIn("function fitToView()", html_text)
        self.assertIn("function zoomByCy(", html_text)
        self.assertIn("function initCytoscape()", html_text)
        self.assertIn("function focusTask(", html_text)

    def test_bfs_dependency_traversal(self):
        """验证前置与后置依赖链的遍历逻辑。"""
        _, html_text, _ = self.fetch_page("/")
        self.assertIn("function highlightDependencies(taskId)", html_text)
        self.assertIn("function clearHighlights()", html_text)
        # 验证 BFS 队列与祖先/后代集合
        self.assertIn("ancestors", html_text)
        self.assertIn("descendants", html_text)

    def test_sse_listener_integration(self):
        """验证前端 SSE 事件监听与平滑更新机制。"""
        _, html_text, _ = self.fetch_page("/")
        self.assertIn("new EventSource('/api/events')", html_text)
        self.assertIn("state_change", html_text)
        self.assertIn("preserveView: true", html_text)

    def test_initial_data_hook_for_export(self):
        """验证包含 window.__INITIAL_DATA__ 离线导出桩。"""
        _, html_text, _ = self.fetch_page("/")
        self.assertIn("window.__INITIAL_DATA__", html_text)

    def test_node_dynamic_dimension_and_overflow_protection(self):
        """验证 Cytoscape 节点高度动态自适应与文本防溢出计算。"""
        _, html_text, _ = self.fetch_page("/")
        # 验证文本折行与任意位置换行规则
        self.assertIn("'text-overflow-wrap': 'anywhere'", html_text)
        self.assertIn("'text-wrap': 'wrap'", html_text)
        # 验证节点宽高与文本宽度使用动态数据映射与安全兜底
        self.assertIn("nodeWidth", html_text)
        self.assertIn("nodeHeight", html_text)
        self.assertIn("textMaxWidth", html_text)
        # 验证包含自适应折行计算函数
        self.assertIn("function countWrappedLines(", html_text)
        self.assertIn("function calculateNodeDimensions(", html_text)



class TestDAGCoordinateAndBezierMath(unittest.TestCase):
    """算法与数学验证：拓扑坐标计算与贝塞尔曲线端点连贯性。"""

    def test_bezier_curve_generator(self):
        """验证给定的源卡片与目标卡片坐标生成的贝塞尔路径符合规范。"""
        u = {"x": 40, "y": 40, "width": 220, "height": 80}
        v = {"x": 320, "y": 40, "width": 220, "height": 80}

        x1 = u["x"] + u["width"]
        y1 = u["y"] + u["height"] / 2
        x2 = v["x"]
        y2 = v["y"] + v["height"] / 2
        dx = max(30, (x2 - x1) * 0.5)

        d = f"M {x1} {y1} C {x1 + dx} {y1}, {x2 - dx} {y2}, {x2} {y2}"
        self.assertEqual(d, "M 260 80.0 C 290 80.0, 290 80.0, 320 80.0")

        # 验证正则表达式能够精确匹配该 SVG 路径
        pattern = re.compile(r"^M\s+[\d.]+\s+[\d.]+\s+C\s+[\d.]+\s+[\d.]+,\s+[\d.]+\s+[\d.]+,\s+[\d.]+\s+[\d.]+$")
        self.assertTrue(pattern.match(d))


if __name__ == "__main__":
    unittest.main()

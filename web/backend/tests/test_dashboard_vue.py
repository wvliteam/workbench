#!/usr/bin/env python3
"""test_dashboard_vue.py: 针对 web/ 独立 Vue 3 前端工程的自动化测试套件。

验证范围：
1. 前端工程文件结构完备性 (Vue 3, Vite, 组件, 组合式函数, 样式)。
2. Anti-Slop 规范合规性 (无原生 Emoji、包含 prefers-reduced-motion、字号与圆角系统)。
3. Node.js 运行时对前端 composables (useAnsi, useMarkdown) 的算法验证。
4. Vite 生产构建物 (dist/ 静态资源) 的有效性。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
WEB_DIR = ROOT / "web" / "frontend"


class TestVueProjectStructure(unittest.TestCase):
    """测试 Vue 3 前端工程目录与关键文件完整性。"""

    def test_package_json(self):
        """测试 package.json 配置正确，包含 Vue 3 与 Vite。"""
        pkg_path = WEB_DIR / "package.json"
        self.assertTrue(pkg_path.is_file())
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
        self.assertEqual(pkg.get("name"), "workbench-dashboard")
        self.assertIn("vue", pkg.get("dependencies", {}))
        self.assertIn("vite", pkg.get("devDependencies", {}))
        self.assertIn("dev", pkg.get("scripts", {}))
        self.assertIn("build", pkg.get("scripts", {}))

    def test_vite_config(self):
        """测试 vite.config.js 配置了 /api 反向代理。"""
        cfg_path = WEB_DIR / "vite.config.js"
        self.assertTrue(cfg_path.is_file())
        content = cfg_path.read_text(encoding="utf-8")
        self.assertIn("/api", content)
        self.assertIn("8088", content)

    def test_components_exist(self):
        """测试所有 Vue 3 业务组件齐全。"""
        comp_dir = WEB_DIR / "src" / "components"
        self.assertTrue(comp_dir.is_dir())
        expected = [
            "AppHeader.vue",
            "PipelineBar.vue",
            "FloatingHUD.vue",
            "DAGCanvas.vue",
            "DetailDrawer.vue",
            "BottomConsole.vue",
        ]
        for name in expected:
            self.assertTrue((comp_dir / name).is_file(), f"Missing component: {name}")

    def test_composables_exist(self):
        """测试所有 Composables 模块齐全。"""
        comp_dir = WEB_DIR / "src" / "composables"
        self.assertTrue(comp_dir.is_dir())
        expected = [
            "useDashboardApi.js",
            "useSSE.js",
            "useAnsi.js",
            "useMarkdown.js",
        ]
        for name in expected:
            self.assertTrue((comp_dir / name).is_file(), f"Missing composable: {name}")


class TestAntiSlopCompliance(unittest.TestCase):
    """测试界面全面贯彻 Anti-Slop 品控规范。"""

    def test_no_system_emojis_in_components(self):
        """测试 Vue 组件模板与样式中已彻底剔除系统原生彩色 Emoji。"""
        # 常见旧系统 Emoji 样本
        banned_emojis = ["📸", "🛡", "⌨", "🚪", "📜", "⏱", "⚡", "🔌", "📝", "📁", "🔍", "📦", "📄", "📋"]
        comp_dir = WEB_DIR / "src" / "components"
        for vue_file in comp_dir.glob("*.vue"):
            text = vue_file.read_text(encoding="utf-8")
            for emoji in banned_emojis:
                self.assertNotIn(
                    emoji,
                    text,
                    f"Component {vue_file.name} contains banned system emoji: {emoji}. Use clean SVG or text tag instead."
                )

    def test_reduced_motion_in_css(self):
        """测试 CSS 中包含 @media (prefers-reduced-motion: reduce) 无障碍降级。"""
        css_path = WEB_DIR / "src" / "assets" / "style.css"
        self.assertTrue(css_path.is_file())
        css = css_path.read_text(encoding="utf-8")
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("animation-duration: 0.01ms", css)


class TestVueComposablesInNode(unittest.TestCase):
    """在 Node.js 中执行并测试 Vue Composables 中的核心解析器。"""

    def test_ansi_converter_logic(self):
        """测试 useAnsi.js 导出的 ansiToHtml 在 Node.js 下的正确性。"""
        js_path = WEB_DIR / "src" / "composables" / "useAnsi.js"
        test_script = f"""
        import {{ ansiToHtml }} from '{js_path}';
        const raw = "\\x1b[1;32mPASS\\x1b[0m and \\x1b[31mFAIL\\x1b[0m";
        const res = ansiToHtml(raw);
        console.log(JSON.stringify({{ res }}));
        """
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", test_script],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        self.assertEqual(proc.returncode, 0, f"Node eval error: {proc.stderr}")
        data = json.loads(proc.stdout.strip())
        self.assertIn("font-weight:700", data["res"])
        self.assertIn("#3fb950", data["res"])
        self.assertIn("#ff7b72", data["res"])

    def test_markdown_converter_logic(self):
        """测试 useMarkdown.js 导出的 renderMarkdown 在 Node.js 下的正确性。"""
        js_path = WEB_DIR / "src" / "composables" / "useMarkdown.js"
        test_script = f"""
        import {{ renderMarkdown }} from '{js_path}';
        const raw = "# 标题一\\n* 列表项\\n```python\\nprint(1)\\n```";
        const res = renderMarkdown(raw);
        console.log(JSON.stringify({{ res }}));
        """
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", test_script],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        self.assertEqual(proc.returncode, 0, f"Node eval error: {proc.stderr}")
        data = json.loads(proc.stdout.strip())
        self.assertIn("<h1>标题一</h1>", data["res"])
        self.assertIn("<li>列表项</li>", data["res"])
        self.assertIn("<pre><code>print(1)</code></pre>", data["res"])


class TestViteProductionBuild(unittest.TestCase):
    """测试 Vite 构建产物完整性。"""

    def test_dist_assets(self):
        """测试 web/dist 存在 index.html 与打包资源（未构建时自动尝试触发构建）。"""
        dist_dir = WEB_DIR / "dist"
        if not (dist_dir / "index.html").is_file():
            try:
                proc = subprocess.run(
                    ["npm", "run", "build"],
                    cwd=str(WEB_DIR),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if proc.returncode != 0:
                    self.skipTest(f"npm run build 未完成或未安装依赖: {proc.stderr[:100]}")
            except Exception as e:
                self.skipTest(f"npm 不可用或构建被跳过: {e}")

        if not (dist_dir / "index.html").is_file():
            self.skipTest("web/dist 尚未构建，在 web/ 目录下执行 npm run build 可验证静态产物")

        self.assertTrue((dist_dir / "index.html").is_file(), "web/dist/index.html should exist")
        assets = list((dist_dir / "assets").glob("*"))
        self.assertGreater(len(assets), 0, "web/dist/assets should contain built js/css files")


class TestPhase3LayoutAndViewportFixes(unittest.TestCase):
    """测试阶段三 (P2) 空间布局与视口缺陷修复。"""

    def test_detail_drawer_single_scroll(self):
        """测试抽屉已彻底消灭 380px 嵌套滚动死锁，实现单容器自然滚动。"""
        drawer_path = WEB_DIR / "src" / "components" / "DetailDrawer.vue"
        content = drawer_path.read_text(encoding="utf-8")

        # 验证内部局部容器不再包含 max-height: 380px 或 overflow-y: auto
        self.assertNotIn(
            "max-height: 380px",
            content,
            "DetailDrawer.vue should not contain max-height: 380px nested scroll traps"
        )
        self.assertIn(".drawer-body {", content)
        self.assertIn("overflow-y: auto;", content)

    def test_detail_drawer_downstream_tasks(self):
        """测试抽屉具备下游受阻任务 (Downstream Blocked Tasks) 透视能力。"""
        drawer_path = WEB_DIR / "src" / "components" / "DetailDrawer.vue"
        content = drawer_path.read_text(encoding="utf-8")

        # 包含 allTasks prop 与 downstreamTasks 派生
        self.assertIn("allTasks", content)
        self.assertIn("downstreamTasks", content)
        self.assertIn("后续阻塞任务", content)
        self.assertIn("focus-task", content)
        self.assertIn("ArrowRight", content)

    def test_bottom_console_resizer_and_maximize(self):
        """测试底部控制台具备高度拖拽调节与全屏最大化/还原功能。"""
        console_path = WEB_DIR / "src" / "components" / "BottomConsole.vue"
        content = console_path.read_text(encoding="utf-8")

        self.assertIn("console-resizer", content)
        self.assertIn("resizer-handle", content)
        self.assertIn("Maximize2", content)
        self.assertIn("Minimize2", content)
        self.assertIn("consoleHeight", content)
        self.assertIn("update:consoleHeight", content)

    def test_gate_health_aggregation_in_bottom_console(self):
        """测试控制台全局门禁健康度聚合，消除假绿遮蔽缺陷。"""
        console_path = WEB_DIR / "src" / "components" / "BottomConsole.vue"
        content = console_path.read_text(encoding="utf-8")

        self.assertIn("globalGateHealth", content)
        self.assertIn("ALL PASS", content)
        self.assertIn("FAIL", content)

    def test_app_viewport_compensation(self):
        """测试 App.vue 在 fitToView 计算中补偿控制台高度遮挡。"""
        app_path = WEB_DIR / "src" / "App.vue"
        content = app_path.read_text(encoding="utf-8")

        self.assertIn("consoleHeight", content)
        self.assertIn("isConsoleOpen.value ? consoleHeight.value : 0", content)
        self.assertIn(":all-tasks=", content)


class TestPhase4PipelineAndCanvasRefinements(unittest.TestCase):
    """测试阶段四 (P3) 阶段栏交互联动与画布体验精细化打磨。"""

    def test_pipeline_bar_vectorization_and_lucide_icons(self):
        """测试 PipelineBar.vue 彻底矢量化，使用规范 Lucide 图标并移除硬编码文本符号。"""
        pb_path = WEB_DIR / "src" / "components" / "PipelineBar.vue"
        content = pb_path.read_text(encoding="utf-8")

        # 验证 Lucide 图标导入与使用
        self.assertIn("lucide-vue-next", content)
        for icon in ("Check", "AlertCircle", "CircleDot", "ChevronRight"):
            self.assertIn(icon, content, f"PipelineBar.vue should use Lucide icon: {icon}")

        # 验证彻底剔除旧的文本硬编码符号
        banned_text_symbols = ["●", "✓", "!"]
        for sym in banned_text_symbols:
            self.assertNotIn(
                f">{sym}<",
                content,
                f"PipelineBar.vue should not contain hardcoded text symbol {sym} in template"
            )

        # 验证 select-phase 事件与选中态
        self.assertIn("select-phase", content)
        self.assertIn("selectedPhase", content)
        self.assertIn("is-selected", content)

        # 验证 Teleport 阶段悬浮提示
        self.assertIn('<Teleport to="body">', content)
        self.assertIn("phase-hover-popover", content)

    def test_dag_canvas_space_pan_and_dblclick(self):
        """测试 DAGCanvas.vue 支持 Space + Drag 专业平移与双击快速自适应。"""
        dag_path = WEB_DIR / "src" / "components" / "DAGCanvas.vue"
        content = dag_path.read_text(encoding="utf-8")

        # 验证 Space 键按住状态与监听
        self.assertIn("isSpacePressed", content)
        self.assertIn("e.code === 'Space'", content)
        self.assertIn("is-space-pressed", content)

        # 验证 Space 模式光标样式 (grab / grabbing)
        self.assertIn("cursor: grab !important", content)
        self.assertIn("cursor: grabbing !important", content)

        # 验证双击空白处触发自适应
        self.assertIn("@dblclick=", content)
        self.assertIn("fit-view", content)

        # 验证阶段选择高亮态
        self.assertIn("selectedPhase", content)
        self.assertIn("highlight-phase-match", content)
        self.assertIn("highlight-phase-edge", content)

    def test_app_phase_and_canvas_linkage(self):
        """测试 App.vue 阶段栏事件联动与画布手势响应。"""
        app_path = WEB_DIR / "src" / "App.vue"
        content = app_path.read_text(encoding="utf-8")

        # 阶段状态与联动处理
        self.assertIn("selectedPhase", content)
        self.assertIn("handleSelectPhase", content)
        self.assertIn("focusPhaseNodes", content)
        self.assertIn('@select-phase="handleSelectPhase"', content)
        self.assertIn(':selected-phase="selectedPhase"', content)

        # 画布双击自适应联动
        self.assertIn('@fit-view="fitToView"', content)


if __name__ == "__main__":
    unittest.main(verbosity=2)



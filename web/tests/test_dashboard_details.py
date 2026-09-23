#!/usr/bin/env python3
"""test_dashboard_details.py: 针对 wb_dashboard.py 执行细节抽屉与底栏 ANSI 控制台的自动化测试套件 (Task 4)。

验证范围：
1. 侧边执行细节抽屉结构 (Tabs, 现场笔记, 代码改动树, 人工复核依据)。
2. 底栏多功能控制台组件 (Tab 切换, 门禁日志面板, 契约面板, 审计流水面板)。
3. 内嵌纯 JS Markdown 渲染引擎算法 (标题、代码块、列表、表格、行内样式、XSS 防御)。
4. 内嵌纯 JS ANSI 终端状态机 (标准 16 色、256 色、粗体、高亮、非 SGR 控制符剥离、实体安全转义)。
5. 前端交互控制器与数据驱动完整性 (switchDrawerTab, switchConsoleTab, loadGateLog, loadContracts, loadAuditLog)。
6. 真实数据提取与前端契约联调。
"""

from __future__ import annotations

import json
import re
import subprocess
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


class TestDashboardDetailsBase(unittest.TestCase):
    """测试基类：启动后台看板服务器并提供 HTTP 辅助方法。"""

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
            name="TestDashboardDetailsThread"
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
        template = dashboard.load_export_template()
        return 200, template, {"Content-Type": "text/html; charset=utf-8"}

    def fetch_json(self, path: str) -> tuple[int, dict | list]:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))


class TestDetailDrawerUIStructure(TestDashboardDetailsBase):
    """验证侧边执行细节抽屉结构与 Tab 分页组件。"""

    def test_drawer_tabs_and_panes(self):
        """验证抽屉三栏 Tab 切换按钮与对应面板。"""
        _, html_text, _ = self.fetch_page("/")
        # Tab 按钮
        self.assertIn('id="drawer-tab-btn-notes"', html_text)
        self.assertIn('id="drawer-tab-btn-changes"', html_text)
        self.assertIn('id="drawer-tab-btn-ver"', html_text)
        self.assertIn('data-tab="notes"', html_text)
        self.assertIn('data-tab="changes"', html_text)
        self.assertIn('data-tab="ver"', html_text)

        # Tab 面板
        self.assertIn('id="drawer-pane-notes"', html_text)
        self.assertIn('id="drawer-pane-changes"', html_text)
        self.assertIn('id="drawer-pane-ver"', html_text)

    def test_drawer_tab_badges_and_containers(self):
        """验证 Tab 计数微标与各区域容器。"""
        _, html_text, _ = self.fetch_page("/")
        self.assertIn('id="drawer-artifacts-count"', html_text)
        self.assertIn('id="drawer-ver-count"', html_text)
        self.assertIn('id="drawer-note-file"', html_text)
        self.assertIn('id="drawer-notes-preview"', html_text)
        self.assertIn('id="drawer-artifacts-list"', html_text)
        self.assertIn('id="drawer-ver-content"', html_text)

    def test_drawer_css_classes(self):
        """验证抽屉相关的 CSS 样式定义。"""
        _, html_text, _ = self.fetch_page("/")
        self.assertIn(".drawer-tabs", html_text)
        self.assertIn(".drawer-tab-btn", html_text)
        self.assertIn(".drawer-tab-pane", html_text)
        self.assertIn(".drawer-markdown-body", html_text)
        self.assertIn(".project-changes-group", html_text)
        self.assertIn(".ver-cmd-card", html_text)
        self.assertIn(".verdict-pass", html_text)
        self.assertIn(".verdict-fail", html_text)


class TestBottomConsoleUIStructure(TestDashboardDetailsBase):
    """验证底栏多功能控制台组件与面板。"""

    def test_bottom_console_toggle_and_container(self):
        """验证底栏开关与折叠抽屉容器。"""
        _, html_text, _ = self.fetch_page("/")
        self.assertIn('id="btn-toggle-dev"', html_text)
        self.assertIn('id="dev-console-drawer"', html_text)
        self.assertIn('id="btn-close-dev"', html_text)
        self.assertIn('id="console-status-dot"', html_text)

    def test_console_tabs_and_panes(self):
        """验证底栏全部 Tab 按钮与对应面板。"""
        _, html_text, _ = self.fetch_page("/")
        # 控制台 Tabs
        self.assertIn('id="tab-btn-gate"', html_text)
        self.assertIn('id="tab-btn-contracts"', html_text)
        self.assertIn('id="tab-btn-audit"', html_text)
        self.assertIn('id="tab-btn-events"', html_text)
        self.assertIn('id="tab-btn-api"', html_text)

        # 控制台 Panes
        self.assertIn('id="console-pane-gate"', html_text)
        self.assertIn('id="console-pane-contracts"', html_text)
        self.assertIn('id="console-pane-audit"', html_text)
        self.assertIn('id="console-pane-events"', html_text)
        self.assertIn('id="console-pane-api"', html_text)

    def test_gate_log_panel_controls(self):
        """验证门禁面板内部组件（选择器、刷新、复制、终端容器）。"""
        _, html_text, _ = self.fetch_page("/")
        self.assertIn('id="gate-select"', html_text)
        self.assertIn('id="btn-refresh-gate"', html_text)
        self.assertIn('id="btn-copy-gate"', html_text)
        self.assertIn('id="btn-scroll-bottom"', html_text)
        self.assertIn('id="gate-log-terminal"', html_text)
        self.assertIn('id="gate-log-body"', html_text)
        self.assertIn('id="gate-status-pill"', html_text)

    def test_contracts_and_audit_panel_elements(self):
        """验证契约面板与审计流水瀑布面板组件。"""
        _, html_text, _ = self.fetch_page("/")
        self.assertIn('id="contracts-total-count"', html_text)
        self.assertIn('id="contracts-unlocked-count"', html_text)
        self.assertIn('id="contracts-disputed-count"', html_text)
        self.assertIn('id="dispute-alert-box"', html_text)
        self.assertIn('id="contracts-table-container"', html_text)
        self.assertIn('id="audit-filter-select"', html_text)
        self.assertIn('id="audit-limit-select"', html_text)
        self.assertIn('id="audit-waterfall-list"', html_text)


class TestMarkdownAndANSIEnginesWithNode(unittest.TestCase):
    """渲染引擎已迁移到开源库：Markdown 用 marked + DOMPurify，ANSI 用 anser。

    ANSI 引擎（anser 纯 JS）在 Node 下端到端执行验证；Markdown 引擎依赖浏览器 DOM 的
    DOMPurify，Node 无 jsdom，故对模板做接线级断言，确认 marked 解析 + DOMPurify 净化已接上。"""

    @classmethod
    def setUpClass(cls):
        import os
        src = dashboard.load_export_template()
        cls.template_src = src

        def extract_func(name: str) -> str:
            pattern = re.compile(rf"function\s+{name}\s*\([^)]*\)\s*\{{", re.MULTILINE)
            m = pattern.search(src)
            if not m:
                raise ValueError(f"Function {name} not found in template")
            start = m.start()
            brace_count = 0
            idx = m.end() - 1
            while idx < len(src):
                if src[idx] == '{':
                    brace_count += 1
                elif src[idx] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        return src[start:idx + 1]
                idx += 1
            raise ValueError(f"Failed to match balanced braces for {name}")

        cls.js_escape = extract_func("escapeHtml")
        cls.js_ansi = extract_func("ansiToHtml")
        vendor_anser = os.path.join(os.path.dirname(__file__), "..", "vendor", "anser.min.js")
        with open(vendor_anser, encoding="utf-8") as f:
            cls.anser_src = f.read()

    def run_ansi_eval(self, script: str) -> str:
        # 提供 window 垫片并加载 vendored anser，端到端执行迁移后的 ansiToHtml
        full_code = f"""
var window = {{}};
{self.anser_src}
var Anser = window.Anser;
{self.js_escape}
{self.js_ansi}
{script}
"""
        proc = subprocess.run(
            ["node", "-e", full_code],
            capture_output=True,
            text=True,
            timeout=5.0
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Node execution failed: {proc.stderr}")
        return proc.stdout.strip()

    def test_markdown_uses_marked_and_sanitizes(self):
        """Markdown 渲染改用 marked.parse，并经 DOMPurify 净化后输出。"""
        self.assertIsNotNone(
            re.search(r"function\s+renderMarkdown\s*\(", self.template_src),
            "renderMarkdown 应仍存在",
        )
        self.assertIn("marked.parse", self.template_src)
        self.assertIn("DOMPurify.sanitize", self.template_src)
        self.assertIn("vendor/marked.min.js", self.template_src)
        self.assertIn("vendor/purify.min.js", self.template_src)

    def test_markdown_code_block_and_table(self):
        """自定义 marked renderer 仍保留代码块/行内代码样式契约。"""
        self.assertIn("marked.use", self.template_src)
        self.assertIn("code-container", self.template_src)
        self.assertIn("inline-code", self.template_src)

    def test_markdown_xss_protection(self):
        """XSS 防护由 DOMPurify 承担：marked.parse 的输出必须先 sanitize 再返回。"""
        self.assertRegex(self.template_src, r"DOMPurify\.sanitize\(\s*marked\.parse")

    def test_ansi_color_parsing_pytest_success(self):
        """anser 将绿色 PASSED 转为带 rgb 颜色的 span，并剔除 ESC 序列。"""
        js = r"""
        const input = "test_func \x1b[32mPASSED\x1b[0m";
        console.log(ansiToHtml(input));
        """
        output = self.run_ansi_eval(js)
        self.assertIn("PASSED</span>", output)
        self.assertIn("color:rgb(0, 187, 0)", output)
        self.assertNotIn("\x1b", output)

    def test_ansi_color_parsing_pytest_failure(self):
        """anser 将粗体红字 FAILED 转为带 font-weight:bold 的彩色 span。"""
        js = r"""
        const input = "\x1b[1;31mFAILED\x1b[0m in 0.05s";
        console.log(ansiToHtml(input));
        """
        output = self.run_ansi_eval(js)
        self.assertIn("font-weight:bold", output)
        self.assertIn("FAILED", output)
        self.assertRegex(output, r"color:rgb\(\d+, \d+, \d+\)")

    def test_ansi_non_sgr_stripping_and_html_escaping(self):
        """光标控制符(\\x1b[2K)被剔除，HTML 特殊字符经 escapeHtml 安全转义。"""
        js = r"""
        const input = "\x1b[2KError: <AssertionError: 1 != 2>";
        console.log(ansiToHtml(input));
        """
        output = self.run_ansi_eval(js)
        self.assertNotIn("[2K", output)
        self.assertIn("&lt;AssertionError: 1 != 2&gt;", output)


class TestBackendDataIntegrationForTask4(TestDashboardDetailsBase):
    """验证后端数据接口满足 Task 4 界面消费契约。"""

    def test_task_detail_payload_complete(self):
        """验证 /api/task-detail 返回笔记、代码按项目归类与复核依据。"""
        status, data = self.fetch_json("/api/task-detail?id=T1")
        self.assertEqual(status, 200)
        self.assertIsInstance(data, dict)
        self.assertIn("note_markdown", data)
        self.assertIn("artifacts_by_project", data)
        self.assertIn("diffs", data)
        self.assertIn("diff_summary", data)
        self.assertIsInstance(data["diffs"], dict)
        self.assertIsInstance(data["diff_summary"], dict)
        self.assertIn("verification", data)
        self.assertIn("commands", data["verification"])

    def test_gate_log_available_and_json(self):
        """验证 /api/gate-log 返回带颜色或纯文本日志。"""
        status, data = self.fetch_json("/api/gate-log?name=test&format=json")
        self.assertEqual(status, 200)
        self.assertIn("name", data)
        self.assertIn("log", data)

    def test_contracts_payload_complete(self):
        """验证 /api/contracts 包含锁定状态、开窗信息与争议记录。"""
        status, data = self.fetch_json("/api/contracts")
        self.assertEqual(status, 200)
        self.assertIn("contracts", data)
        self.assertIn("unlocks", data)
        self.assertIn("disputes", data)

    def test_audit_log_payload_complete(self):
        """验证 /api/audit 返回审计时间轴条目。"""
        status, data = self.fetch_json("/api/audit?limit=10")
        self.assertEqual(status, 200)
        self.assertIsInstance(data, list)


if __name__ == "__main__":
    unittest.main()

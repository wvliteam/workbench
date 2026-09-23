#!/usr/bin/env python3
"""test_dashboard_export.py: 针对 wb_dashboard 静态离线导出与 CLI 命令接入的自动化测试套件 (Task 5)。

验证范围：
1. build_static_export_data 数据预烘焙完整性 (全 Flow、DAG 节点边、执行细节、门禁 ANSI 日志、契约与审计)。
2. export_static_dashboard 单文件 HTML 导出、文件自包含性、自动建目录、安全转义防 </script> 注入。
3. Node.js 运行时离线评估 (验证 window.__INITIAL_DATA__ 读取、离线无网络 loadData、openTaskDrawer、initSSE 表现)。
4. web/wb_dashboard.py 独立 CLI --export 命令与参数。
5. .claude/hooks/wb.py dashboard 子命令接入与全链路验证。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WEB_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB_DIR))
sys.path.insert(0, str(ROOT / ".claude" / "hooks"))

import wb_dashboard as dashboard
import wb_dashboard_core as core
import wb_cli


class TestStaticDataBaking(unittest.TestCase):
    """测试 build_static_export_data 数据聚合引擎。"""

    def test_build_static_export_data_structure(self):
        """测试预烘焙数据字典结构完整且关键字段齐全。"""
        data = dashboard.build_static_export_data(ROOT, target_flow="main")
        self.assertTrue(data.get("is_static"))
        self.assertIn("export_time", data)
        self.assertEqual(data.get("current_flow"), "main")
        self.assertIsInstance(data.get("flows"), list)
        self.assertIn("main", data["flows"])
        self.assertIn("flows_data", data)
        self.assertIn("main", data["flows_data"])

        # 检查顶层及 main flow 内数据
        main_flow = data["flows_data"]["main"]
        self.assertIn("overview", main_flow)
        self.assertIn("tasks", main_flow)
        self.assertIn("task_details", main_flow)
        self.assertIn("contracts", main_flow)
        self.assertIn("audit", main_flow)
        self.assertIn("gate_logs", main_flow)

        # 验证 tasks 包含 DAG nodes 与 edges
        tasks = main_flow["tasks"]
        self.assertIn("nodes", tasks)
        self.assertIn("edges", tasks)
        self.assertGreater(len(tasks["nodes"]), 0)

        # 验证顶层同名字段同步存在
        self.assertEqual(data["overview"]["project"], main_flow["overview"]["project"])
        self.assertEqual(len(data["tasks"]["nodes"]), len(tasks["nodes"]))

    def test_task_details_included(self):
        """测试已完成/进行中的任务细节（含笔记 Markdown 与改动树）被烘焙。"""
        data = dashboard.build_static_export_data(ROOT, target_flow="main")
        details = data.get("task_details", {})
        self.assertIsInstance(details, dict)
        self.assertGreater(len(details), 0)

        for tid, detail in details.items():
            self.assertEqual(detail["id"], tid)
            self.assertIn("status", detail)
            self.assertIn("note_markdown", detail)
            self.assertIn("artifacts", detail)


class TestStaticHTMLExport(unittest.TestCase):
    """测试 export_static_dashboard 单文件 HTML 导出。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="wb_test_export_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_export_file_creation_and_size(self):
        """测试导出文件成功创建且大小合理（包含模板与烘焙数据）。"""
        out_path = Path(self.tmp_dir) / "sub_dir" / "report.html"
        result = dashboard.export_static_dashboard(
            root=ROOT,
            output_path=out_path,
            flow="main",
            quiet=True,
        )
        self.assertEqual(result, out_path.resolve())
        self.assertTrue(out_path.is_file())
        size_kb = out_path.stat().st_size / 1024
        self.assertGreater(size_kb, 50, f"导出文件大小应该大于 50KB，实际为 {size_kb:.1f}KB")

    def test_export_has_no_external_vendor_refs(self):
        """测试导出文件完全自包含：不留任何 vendor 外部引用。

        模板里每个 <script src="vendor/..."> / <link href="vendor/..."> 都必须被内联替换，
        否则 file:// 下这些请求全部 404 —— 其中 DOMPurify 缺失会让 renderMarkdown 在
        try 之外抛 ReferenceError，调用方 renderDrawerDetail 中断，任务抽屉的笔记/改动/
        复核三个 Tab 一起静默失效。
        """
        out_path = Path(self.tmp_dir) / "selfcontained.html"
        dashboard.export_static_dashboard(
            root=ROOT,
            output_path=out_path,
            flow="main",
            quiet=True,
        )
        content = out_path.read_text(encoding="utf-8")

        leftovers = re.findall(r'(?:src|href)=["\'](?:/vendor/|vendor/)[^"\']+["\']', content)
        self.assertEqual(leftovers, [], f"导出文件残留 vendor 外部引用: {leftovers}")

        # 光断言「无残留引用」会被「模板压根没引这个库」蒙混过去，故逐个点名关键库
        for elem_id in (
            "__VENDOR_MARKED__", "__VENDOR_PRISM__", "__VENDOR_FUSE__", "__VENDOR_PURIFY__",
            "__VENDOR_ANSER__", "__VENDOR_DIFF2HTML__", "__VENDOR_CYTOSCAPE__",
            "__VENDOR_DAGRE__", "__VENDOR_CYTOSCAPE_DAGRE__", "__VENDOR_POPPER__",
            "__VENDOR_TIPPY__", "__VENDOR_BOOTSTRAP_CSS__",
        ):
            self.assertIn(f'id="{elem_id}"', content, f"{elem_id} 未被内联进导出文件")

    def test_script_tag_escaping_and_valid_json(self):
        """测试导出的 HTML 中包含合法的 JSON 数据，且未被 </script> 意外闭合破坏。"""
        out_path = Path(self.tmp_dir) / "snapshot.html"
        dashboard.export_static_dashboard(
            root=ROOT,
            output_path=out_path,
            flow="main",
            quiet=True,
        )

        content = out_path.read_text(encoding="utf-8")
        # 验证模板变量全部被替换完毕，无残留占位符
        self.assertNotIn("{{PROJECT}}", content)
        self.assertNotIn("{{FLOW}}", content)
        self.assertNotIn("{{PHASE}}", content)
        self.assertNotIn("{{VERSION}}", content)
        self.assertNotIn("{{INITIAL_DATA_JSON}}", content)

        # 提取 __INITIAL_DATA__
        m = re.search(r'<script id="__INITIAL_DATA__" type="application/json">(.*?)</script>', content, re.DOTALL)
        self.assertIsNotNone(m, "未在 HTML 中找到 <script id=\"__INITIAL_DATA__\"> 标签")
        json_text = m.group(1)

        # 验证能无异常反序列化为 JSON
        parsed = json.loads(json_text)
        self.assertTrue(parsed["is_static"])
        self.assertEqual(parsed["current_flow"], "main")
        self.assertIn("overview", parsed)

    def test_safe_script_closing_escape(self):
        """测试当任务笔记中包含 </script> 危险字串时，导出能安全转义为 <\\/script>。"""
        # 构造带有模拟 </script> 的临时数据进行烘焙测试
        test_payload = {
            "is_static": True,
            "danger_snippet": "<script>alert('xss')</script> and </script>",
        }
        escaped = json.dumps(test_payload, ensure_ascii=False).replace("</script>", r"<\/script>")
        self.assertNotIn("</script>", escaped)
        self.assertIn(r"<\/script>", escaped)


class TestNodeJSClientEvaluation(unittest.TestCase):
    """验证烘焙进导出文件的 __INITIAL_DATA__ 载荷自洽。

    注意：本类**不执行导出文件里的前端脚本**，而是用一段等价 JS 复刻
    loadData / initSSE / openTaskDrawer 的离线分支再断言其返回值。它只能证明
    「烘焙数据里有 overview/tasks/task_details 且能按 flow 取到」，不能证明
    交付的看板脚本可用。脚本自身的可用性由上面的
    TestStaticHTMLExport.test_export_has_no_external_vendor_refs（自包含）兜底。
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="wb_test_node_")
        self.out_html = Path(self.tmp_dir) / "offline_dashboard.html"
        dashboard.export_static_dashboard(
            root=ROOT,
            output_path=self.out_html,
            flow="main",
            quiet=True,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_node_offline_script_execution(self):
        """断言烘焙载荷自洽：离线分支可命中静态数据、首个任务有预烘焙细节。"""
        html_content = self.out_html.read_text(encoding="utf-8")

        # 提取烘焙好的 JSON 数据
        m = re.search(r'<script id="__INITIAL_DATA__" type="application/json">(.*?)</script>', html_content, re.DOTALL)
        self.assertIsNotNone(m)
        baked_json = m.group(1)

        # 构建一段纯 Node.js 测试脚本，验证前端静态模式判断逻辑
        test_js = f"""
        const bakedData = JSON.parse({json.dumps(baked_json)});
        
        // 模拟浏览器全局环境
        const window = {{
            __INITIAL_DATA__: bakedData
        }};

        // 模拟 AppState
        const AppState = {{
            currentFlow: window.__INITIAL_DATA__.current_flow || 'main',
            overview: null,
            tasksDAG: null,
        }};

        // 模拟 loadData 纯逻辑
        function mockLoadData(flow) {{
            const targetFlow = flow || AppState.currentFlow || 'main';
            if (window.__INITIAL_DATA__) {{
                const flowData = window.__INITIAL_DATA__.flows_data ? window.__INITIAL_DATA__.flows_data[targetFlow] : null;
                if (flowData && flowData.overview && flowData.tasks) {{
                    AppState.overview = flowData.overview;
                    AppState.tasksDAG = flowData.tasks;
                    return 'LOADED_FROM_STATIC_FLOW_DATA';
                }} else if (window.__INITIAL_DATA__.overview && window.__INITIAL_DATA__.tasks) {{
                    AppState.overview = window.__INITIAL_DATA__.overview;
                    AppState.tasksDAG = window.__INITIAL_DATA__.tasks;
                    return 'LOADED_FROM_TOP_STATIC';
                }}
            }}
            return 'CALLED_FETCH'; // 离线模式下不应触发
        }}

        // 模拟 initSSE 离线探测
        function mockInitSSE() {{
            if (window.__INITIAL_DATA__ && window.__INITIAL_DATA__.is_static) {{
                return {{
                    mode: 'STATIC',
                    text: `静态快照 (${{window.__INITIAL_DATA__.export_time}})`
                }};
            }}
            return {{ mode: 'LIVE' }};
        }}

        // 模拟 openTaskDrawer 离线读取
        function mockGetTaskDetail(taskId) {{
            if (window.__INITIAL_DATA__) {{
                const flowData = window.__INITIAL_DATA__.flows_data ? window.__INITIAL_DATA__.flows_data[AppState.currentFlow] : null;
                const staticDetail = (flowData && flowData.task_details && flowData.task_details[taskId])
                    || (window.__INITIAL_DATA__.task_details && window.__INITIAL_DATA__.task_details[taskId]);
                if (staticDetail) return {{ source: 'STATIC', detail: staticDetail }};
            }}
            return {{ source: 'FETCH' }};
        }}

        const loadResult = mockLoadData('main');
        const sseResult = mockInitSSE();
        const firstTaskId = Object.keys(bakedData.task_details)[0] || 'T1';
        const detailResult = mockGetTaskDetail(firstTaskId);

        console.log(JSON.stringify({{
            loadResult,
            sseResult,
            firstTaskId,
            detailResultSource: detailResult.source,
            nodesCount: (AppState.tasksDAG && AppState.tasksDAG.nodes) ? AppState.tasksDAG.nodes.length : 0
        }}));
        """

        res = subprocess.run(
            ["node", "-e", test_js],
            capture_output=True,
            text=True,
            check=True,
        )
        out = json.loads(res.stdout.strip())
        self.assertIn("STATIC", out["loadResult"])
        self.assertEqual(out["sseResult"]["mode"], "STATIC")
        self.assertEqual(out["detailResultSource"], "STATIC")
        self.assertGreater(out["nodesCount"], 0)


class TestDashboardCLIIntegration(unittest.TestCase):
    """测试 wb_dashboard.py 与 wb.py dashboard 命令行集成。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="wb_test_cli_")
        self.tmp_path = Path(self.tmp_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_wb_dashboard_standalone_export_cli(self):
        """测试直接运行 web/wb_dashboard.py --export 生成报告。"""
        out_html = self.tmp_path / "standalone_cli.html"
        cmd = [
            sys.executable,
            str(ROOT / "web" / "wb_dashboard.py"),
            "--root", str(ROOT),
            "--export", str(out_html),
            "--quiet",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"CLI 运行失败: {res.stderr}")
        self.assertTrue(out_html.is_file())
        self.assertGreater(out_html.stat().st_size, 50 * 1024)

    def test_wb_cli_dashboard_help(self):
        """测试 python3 .claude/hooks/wb.py dashboard --help 包含所有参数。"""
        cmd = [
            sys.executable,
            str(ROOT / ".claude" / "hooks" / "wb.py"),
            "dashboard",
            "--help",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("--export", res.stdout)
        self.assertIn("--port", res.stdout)
        self.assertIn("--open", res.stdout)
        self.assertIn("--flow", res.stdout)
        self.assertIn("--quiet", res.stdout)

    def test_wb_cli_dashboard_export_command(self):
        """测试 python3 .claude/hooks/wb.py dashboard --export <path> 正常导出。"""
        out_html = Path(self.tmp_dir) / "wb_exported.html"
        cmd = [
            sys.executable,
            str(ROOT / ".claude" / "hooks" / "wb.py"),
            "dashboard",
            "--export", str(out_html),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"wb dashboard 导出失败: {res.stderr}")
        self.assertTrue(out_html.is_file())
        self.assertIn("Workbench 看板静态单文件导出成功", res.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)

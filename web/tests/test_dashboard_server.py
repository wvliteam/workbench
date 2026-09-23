#!/usr/bin/env python3
"""test_dashboard_server.py: 针对 wb_dashboard.py 的 HTTP 与 SSE 自动化测试套件。

覆盖测试范围：
1. 各 REST API 端点响应格式与数据有效性 (/api/overview, /api/tasks, /api/contracts 等)。
2. 边界与参数校验（缺少必要参数返回 400，不存在资源返回 404）。
3. 跨目录与路径穿越安全防御（SecurityError 映射为 403 Forbidden）。
4. CORS 预检与跨域头。
5. SSE 实时事件流与文件变更触发广播。
6. 优雅停机与资源回收。
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WEB_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB_DIR))

import wb_dashboard as dashboard
import wb_dashboard_core as core


class TestDashboardServerBase(unittest.TestCase):
    """测试基类：在后台线程启动独立端口的看板服务器。"""

    @classmethod
    def setUpClass(cls):
        # 绑定端口 0，由操作系统分配随机空闲端口
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
            name="TestDashboardServerThread"
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

    def fetch_json(self, path: str) -> tuple[int, dict | list]:
        """发起 HTTP GET 请求并解析 JSON 响应。"""
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                status = resp.status
                body = resp.read().decode("utf-8")
                return status, json.loads(body)
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8")
            try:
                data = json.loads(body)
            except Exception:
                data = {"raw": body}
            return err.code, data

    def fetch_text(self, path: str) -> tuple[int, str, dict[str, str]]:
        """发起 HTTP GET 请求并返回文本内容与响应头。"""
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                headers = {k.lower(): v for k, v in resp.headers.items()}
                headers.update({k.title(): v for k, v in resp.headers.items()})
                return resp.status, resp.read().decode("utf-8"), headers
        except urllib.error.HTTPError as err:
            headers = {k.lower(): v for k, v in err.headers.items()}
            headers.update({k.title(): v for k, v in err.headers.items()})
            return err.code, err.read().decode("utf-8"), headers


class TestDashboardRESTEndpoints(TestDashboardServerBase):
    """测试所有 REST API 端点功能与格式。"""

    def test_overview_endpoint(self):
        """测试 /api/overview 返回正确的概览结构。"""
        status, data = self.fetch_json("/api/overview")
        self.assertEqual(status, 200)
        self.assertIsInstance(data, dict)
        self.assertIn("project", data)
        self.assertIn("current_flow", data)
        self.assertIn("phases", data)
        self.assertEqual(len(data["phases"]), 6)
        self.assertIn("total_tasks", data)

    def test_tasks_dag_endpoint(self):
        """测试 /api/tasks 返回 DAG 节点与拓扑边。"""
        status, data = self.fetch_json("/api/tasks")
        self.assertEqual(status, 200)
        self.assertIsInstance(data, dict)
        self.assertIn("nodes", data)
        self.assertIn("edges", data)
        self.assertIn("max_depth", data)
        self.assertGreater(len(data["nodes"]), 0)

    def test_task_detail_valid(self):
        """测试 /api/task-detail 查询有效任务返回细节。"""
        status, data = self.fetch_json("/api/task-detail?id=T1")
        self.assertEqual(status, 200)
        self.assertIsInstance(data, dict)
        self.assertEqual(data["id"], "T1")
        self.assertIn("role", data)
        self.assertIn("status", data)
        self.assertIn("artifacts_by_project", data)
        self.assertIn("verification", data)

    def test_task_detail_missing_id(self):
        """测试 /api/task-detail 缺少 id 参数返回 400。"""
        status, data = self.fetch_json("/api/task-detail")
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_task_detail_not_found(self):
        """测试 /api/task-detail 查询不存在的任务返回 404。"""
        status, data = self.fetch_json("/api/task-detail?id=NON_EXISTENT_T999")
        self.assertEqual(status, 404)
        self.assertIn("error", data)

    def test_gate_log_text(self):
        """测试 /api/gate-log 默认返回原始纯文本日志。"""
        status, text, headers = self.fetch_text("/api/gate-log?name=test")
        self.assertEqual(status, 200)
        self.assertIn("text/plain", headers.get("Content-Type", ""))
        self.assertIn("selfcheck", text)

    def test_gate_log_json_format(self):
        """测试 /api/gate-log?format=json 返回 JSON 封装。"""
        status, data = self.fetch_json("/api/gate-log?name=test&format=json")
        self.assertEqual(status, 200)
        self.assertIsInstance(data, dict)
        self.assertEqual(data["name"], "test")
        self.assertIn("log", data)
        self.assertIn("selfcheck", data["log"])

    def test_gate_log_missing_name(self):
        """测试 /api/gate-log 缺少 name 参数返回 400。"""
        status, data = self.fetch_json("/api/gate-log")
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_contracts_endpoint(self):
        """测试 /api/contracts 返回契约列表与开窗。"""
        status, data = self.fetch_json("/api/contracts")
        self.assertEqual(status, 200)
        self.assertIsInstance(data, dict)
        self.assertIn("contracts", data)
        self.assertIn("unlocks", data)
        self.assertIn("disputes", data)

    def test_audit_endpoint_full_and_limit(self):
        """测试 /api/audit 返回审计流水列表，且支持 limit 参数。"""
        status, data = self.fetch_json("/api/audit")
        self.assertEqual(status, 200)
        self.assertIsInstance(data, list)

        # 带 limit
        status_lim, data_lim = self.fetch_json("/api/audit?limit=2")
        self.assertEqual(status_lim, 200)
        self.assertIsInstance(data_lim, list)
        self.assertLessEqual(len(data_lim), 2)

    def test_api_metadata_endpoint(self):
        """测试访问 /api 正常返回纯 API 服务的元数据信息。"""
        status, data = self.fetch_json("/api")
        self.assertEqual(status, 200)
        self.assertEqual(data.get("service"), "wb-dashboard-api")
        self.assertEqual(data.get("status"), "online")
        self.assertIn("endpoints", data)
        self.assertIn("/api/overview", data["endpoints"].values())

    def test_index_html_landing(self):
        """测试访问 / 与 /index.html 返回自包含单文件看板 HTML 界面而非裸 JSON 数据。"""
        for path in ("/", "/index.html"):
            status, text, headers = self.fetch_text(path)
            self.assertEqual(status, 200)
            self.assertIn("text/html", headers.get("content-type", ""))
            self.assertIn("<!DOCTYPE html>", text)
            self.assertIn("Workbench Dashboard", text)
            self.assertIn("renderDAG", text)

    def test_live_dashboard_self_contained_html(self):
        """测试交付的 Live 看板 HTML 完全自包含（包含内嵌样式、SVG与通信脚本，无需外部构建包）。"""
        status, text, _ = self.fetch_text("/")
        self.assertEqual(status, 200)
        self.assertIn("<style>", text)
        self.assertIn("function renderDAG", text)
        self.assertIn('"is_static": false', text)
        self.assertIn("/api/events", text)

    def test_render_live_dashboard_html_helper(self):
        """测试 render_live_dashboard_html 渲染有效性与元数据注入。"""
        html_out = dashboard.render_live_dashboard_html(ROOT, target_flow="main")
        self.assertIn("<!DOCTYPE html>", html_out)
        self.assertIn("Workbench Dashboard", html_out)
        self.assertIn('"is_static": false', html_out)

    def test_cors_and_options(self):
        """测试 CORS 预检 OPTIONS 与响应跨域头。"""
        url = f"{self.base_url}/api/overview"
        req = urllib.request.Request(url, method="OPTIONS")
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            self.assertEqual(resp.status, 204)
            self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")
            self.assertIn("GET", resp.headers.get("Access-Control-Allow-Methods", ""))


class TestSecurityDefenses(TestDashboardServerBase):
    """测试路径穿越与越界访问的安全拦截。"""

    def test_task_detail_traversal_blocked(self):
        """测试在 task-detail 传入 ../ 穿越路径返回 403 Forbidden。"""
        status, data = self.fetch_json("/api/task-detail?id=../../etc/passwd")
        self.assertEqual(status, 403)
        self.assertIn("error", data)
        self.assertEqual(data["error"], "Forbidden")

    def test_gate_log_traversal_blocked(self):
        """测试在 gate-log 传入 ../ 穿越路径返回 403 Forbidden。"""
        status, data = self.fetch_json("/api/gate-log?name=../../etc/passwd")
        self.assertEqual(status, 403)
        self.assertIn("error", data)
        self.assertEqual(data["error"], "Forbidden")

    def test_flow_traversal_blocked(self):
        """测试在 flow 参数传入 ../ 越界路径返回 403 Forbidden。"""
        status, data = self.fetch_json("/api/overview?flow=../../etc")
        self.assertEqual(status, 403)
        self.assertIn("error", data)
        self.assertEqual(data["error"], "Forbidden")

    def test_flow_invalid_characters_blocked(self):
        """测试在 flow 参数传入非法字符（大写、空格等）返回 400 Bad Request 而不崩溃。"""
        status, data = self.fetch_json("/api/overview?flow=INVALID_UPPER")
        self.assertEqual(status, 400)
        self.assertIn("error", data)
        self.assertEqual(data["error"], "Bad Request")


class TestSSEEventStream(TestDashboardServerBase):
    """测试 SSE 实时事件通道建立与消息推送。"""

    def test_sse_connection_and_broadcast(self):
        """测试客户端连接 /api/events 并接收广播事件。"""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10.0)
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertIn("text/event-stream", resp.headers.get("Content-Type", ""))

        # 1. 验证握手注释
        first_chunk = resp.readline().decode("utf-8")
        self.assertEqual(first_chunk.strip(), ": connected")
        # 跳过空行
        empty_line = resp.readline().decode("utf-8")
        self.assertEqual(empty_line.strip(), "")

        # 2. 触发一次广播
        self.server.watcher.notify(flow="main", changed_files=["state.json", "gate-test.log"])

        # 3. 读取事件
        event_line = resp.readline().decode("utf-8").strip()
        data_line = resp.readline().decode("utf-8").strip()
        resp.readline()  # 吞掉分隔空行

        self.assertEqual(event_line, "event: state_change")
        self.assertTrue(data_line.startswith("data: "))
        payload = json.loads(data_line[6:])
        self.assertEqual(payload.get("event"), "state_change")
        self.assertEqual(payload.get("flow"), "main")
        self.assertIn("state.json", payload.get("changed_files", []))

        conn.close()


class TestVendorAssetsDelivery(TestDashboardServerBase):
    """测试开源第三方依赖库静态交付与安全性。"""

    def test_vendor_marked_served_successfully(self):
        """测试 /vendor/marked.min.js 正确交付且具备正确的 Content-Type。"""
        status, content, headers = self.fetch_text("/vendor/marked.min.js")
        self.assertEqual(status, 200)
        self.assertIn("application/javascript", headers.get("content-type", ""))
        self.assertIn("marked", content)

    def test_vendor_prism_served_successfully(self):
        """测试 /vendor/prism.min.js 正确交付且包含支持的语言模块。"""
        status, content, headers = self.fetch_text("/vendor/prism.min.js")
        self.assertEqual(status, 200)
        self.assertIn("application/javascript", headers.get("content-type", ""))
        self.assertIn("Prism", content)

    def test_vendor_diff_served_successfully(self):
        """测试 /vendor/diff.min.js 正确交付且包含 jsdiff。"""
        status, content, headers = self.fetch_text("/vendor/diff.min.js")
        self.assertEqual(status, 200)
        self.assertIn("application/javascript", headers.get("content-type", ""))
        self.assertIn("jsdiff", content)

    def test_vendor_fuse_served_successfully(self):
        """测试 /vendor/fuse.min.js 正确交付且包含 Fuse。"""
        status, content, headers = self.fetch_text("/vendor/fuse.min.js")
        self.assertEqual(status, 200)
        self.assertIn("application/javascript", headers.get("content-type", ""))
        self.assertIn("Fuse", content)

    def test_vendor_nonexistent_returns_404(self):
        """测试请求不存在的第三方库文件返回 404。"""
        status, _, _ = self.fetch_text("/vendor/nonexistent.js")
        self.assertEqual(status, 404)

    def test_vendor_path_traversal_blocked(self):
        """测试对 /vendor/ 的路径穿越探测被安全阻断为 403 或 404。"""
        status, _, _ = self.fetch_text("/vendor/..%2Fwb_dashboard.py")
        self.assertIn(status, (400, 403, 404))


class TestStateWatcherFileMonitoring(unittest.TestCase):
    """测试 StateWatcher 对物理文件变动的真实轮询探测与广播。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="wb_test_watcher_")
        self.root = Path(self.tmp_dir)
        wbd = self.root / ".workbench"
        wbd.mkdir(parents=True)
        self.state_file = wbd / "state.json"
        self.state_file.write_text('{"phase": "clarify"}', encoding="utf-8")

        self.watcher = dashboard.StateWatcher(self.root, poll_interval=0.05)
        self.watcher.start()

    def tearDown(self):
        self.watcher.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_file_modification_triggers_event(self):
        """修改 state.json，验证 watcher 在毫秒级内探测到并广播。"""
        sub_queue = self.watcher.subscribe()
        try:
            # 模拟文件修改
            time.sleep(0.06)
            self.state_file.write_text('{"phase": "analyze"}', encoding="utf-8")

            # 等待事件
            event = sub_queue.get(timeout=2.0)
            self.assertEqual(event.get("event"), "state_change")
            changed = event.get("changed_files", [])
            self.assertTrue(any("state.json" in f for f in changed))
        finally:
            self.watcher.unsubscribe(sub_queue)


class TestDashboardServerShutdown(unittest.TestCase):
    """测试 DashboardServer 在存在活跃 SSE 长连接时的优雅停机与资源回收。"""

    def test_shutdown_with_active_sse_stream(self):
        """测试存在活跃 /api/events SSE 连接时，调用 shutdown() 能够在短时间内优雅退出且不报错。"""
        server = dashboard.create_server(
            root=ROOT,
            host="127.0.0.1",
            port=0,
            quiet=True,
            poll_interval=0.1,
        )
        port = server.server_address[1]
        server_thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
            name="TestShutdownServerThread",
        )
        server.watcher.start()
        server_thread.start()
        time.sleep(0.1)

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)

        # 发起停机
        start_t = time.time()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3.0)
        elapsed = time.time() - start_t

        self.assertFalse(server_thread.is_alive(), "服务器线程应在调用 shutdown 后迅速退出")
        self.assertLess(elapsed, 2.0, "优雅退出耗时应小于 2 秒")
        conn.close()


def main():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()

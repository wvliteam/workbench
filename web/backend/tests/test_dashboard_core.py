#!/usr/bin/env python3
"""test_dashboard_core.py: 针对 wb_dashboard_core.py 的独立单元测试与验证套件。

覆盖以下 4 类关键断言及更多边界场景：
1. 拓扑排序与分层计算（线性依赖、菱形依赖、深度与坐标）。
2. 循环依赖容错降级（双向环、自依赖、局部环、不发生死循环）。
3. 路径安全拦截守卫（针对 ../../etc/passwd 等跨目录穿越防御）。
4. 真实工作区状态读取与数据一致性（DAG、任务状态、细节提取、门禁与审计）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# 引入被测模块
ROOT = Path(__file__).resolve().parent.parent.parent.parent
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import wb_dashboard_core as core


class TestDAGTopologicalCalculation(unittest.TestCase):
    """测试 DAG 拓扑分层与排版坐标计算。"""

    def test_linear_dependencies(self):
        """测试线性依赖链：T1 -> T2 -> T3。"""
        tasks = [
            {"id": "T1", "title": "任务1", "deps": [], "status": "done"},
            {"id": "T2", "title": "任务2", "deps": ["T1"], "status": "doing"},
            {"id": "T3", "title": "任务3", "deps": ["T2"], "status": "todo"},
        ]
        dag = core.calculate_dag(tasks)
        self.assertFalse(dag["has_cycle"])
        self.assertEqual(dag["total_nodes"], 3)
        self.assertEqual(dag["max_depth"], 2)

        nodes_by_id = {n["id"]: n for n in dag["nodes"]}
        self.assertEqual(nodes_by_id["T1"]["layer"], 0)
        self.assertEqual(nodes_by_id["T2"]["layer"], 1)
        self.assertEqual(nodes_by_id["T3"]["layer"], 2)

        # 检查坐标递增
        self.assertLess(nodes_by_id["T1"]["x"], nodes_by_id["T2"]["x"])
        self.assertLess(nodes_by_id["T2"]["x"], nodes_by_id["T3"]["x"])

        # 检查边
        expected_edges = [
            {"source": "T1", "target": "T2"},
            {"source": "T2", "target": "T3"},
        ]
        self.assertEqual(dag["edges"], expected_edges)

    def test_diamond_dependencies(self):
        """测试经典菱形依赖：T1 -> T2, T1 -> T3, T2 -> T4, T3 -> T4。"""
        tasks = [
            {"id": "T1", "title": "根任务", "deps": []},
            {"id": "T2", "title": "分支A", "deps": ["T1"]},
            {"id": "T3", "title": "分支B", "deps": ["T1"]},
            {"id": "T4", "title": "汇聚任务", "deps": ["T2", "T3"]},
        ]
        dag = core.calculate_dag(tasks)
        self.assertFalse(dag["has_cycle"])
        self.assertEqual(dag["max_depth"], 2)

        nodes_by_id = {n["id"]: n for n in dag["nodes"]}
        self.assertEqual(nodes_by_id["T1"]["layer"], 0)
        self.assertEqual(nodes_by_id["T2"]["layer"], 1)
        self.assertEqual(nodes_by_id["T3"]["layer"], 1)
        self.assertEqual(nodes_by_id["T4"]["layer"], 2)

        # 同层节点 row 应该不同，x 相同，y 不同
        self.assertEqual(nodes_by_id["T2"]["x"], nodes_by_id["T3"]["x"])
        self.assertNotEqual(nodes_by_id["T2"]["y"], nodes_by_id["T3"]["y"])
        self.assertEqual(nodes_by_id["T2"]["row"], 0)
        self.assertEqual(nodes_by_id["T3"]["row"], 1)

        # 汇聚节点 x 处于下一列
        self.assertLess(nodes_by_id["T2"]["x"], nodes_by_id["T4"]["x"])

        # 验证所有 4 条边
        edges = {(e["source"], e["target"]) for e in dag["edges"]}
        self.assertEqual(edges, {("T1", "T2"), ("T1", "T3"), ("T2", "T4"), ("T3", "T4")})

    def test_empty_and_isolated_tasks(self):
        """测试空任务列表和互不依赖的孤立任务。"""
        empty_dag = core.calculate_dag([])
        self.assertEqual(empty_dag["total_nodes"], 0)
        self.assertEqual(empty_dag["max_depth"], 0)
        self.assertEqual(empty_dag["edges"], [])

        isolated_tasks = [
            {"id": "T1", "deps": []},
            {"id": "T2", "deps": []},
            {"id": "T3", "deps": []},
        ]
        iso_dag = core.calculate_dag(isolated_tasks)
        self.assertFalse(iso_dag["has_cycle"])
        self.assertEqual(iso_dag["max_depth"], 0)
        for n in iso_dag["nodes"]:
            self.assertEqual(n["layer"], 0)
        # 3 个任务排在同一列的不同行
        rows = [n["row"] for n in iso_dag["nodes"]]
        self.assertEqual(rows, [0, 1, 2])

    def test_missing_dependency_tolerance(self):
        """测试前置依赖引用了不存在任务时的容错能力。"""
        tasks = [
            {"id": "T1", "deps": ["NON_EXISTENT_TASK"]},
            {"id": "T2", "deps": ["T1"]},
        ]
        dag = core.calculate_dag(tasks)
        self.assertFalse(dag["has_cycle"])
        nodes = {n["id"]: n for n in dag["nodes"]}
        self.assertEqual(nodes["T1"]["layer"], 0)
        self.assertEqual(nodes["T2"]["layer"], 1)


class TestCycleDetectionAndDegradation(unittest.TestCase):
    """测试循环依赖的检测与容错降级。"""

    def test_two_node_cycle(self):
        """测试双节点循环依赖：T1 -> T2 -> T1。"""
        tasks = [
            {"id": "T1", "deps": ["T2"]},
            {"id": "T2", "deps": ["T1"]},
        ]
        dag = core.calculate_dag(tasks)
        self.assertTrue(dag["has_cycle"])
        nodes = {n["id"]: n for n in dag["nodes"]}
        self.assertTrue(nodes["T1"]["has_cycle"])
        self.assertTrue(nodes["T2"]["has_cycle"])
        # 不会发生死循环，且所有节点均分配了合法深度和坐标
        self.assertIsInstance(nodes["T1"]["layer"], int)
        self.assertIsInstance(nodes["T2"]["layer"], int)

    def test_self_dependency_cycle(self):
        """测试自依赖循环：T1 -> T1。"""
        tasks = [
            {"id": "T1", "deps": ["T1"]},
            {"id": "T2", "deps": ["T1"]},
        ]
        dag = core.calculate_dag(tasks)
        self.assertTrue(dag["has_cycle"])
        nodes = {n["id"]: n for n in dag["nodes"]}
        self.assertTrue(nodes["T1"]["has_cycle"])

    def test_partial_cycle_in_graph(self):
        """测试大型图中的局部环：T1 -> T2 -> T3 -> T2，T1 正常，T2/T3 处于环路。"""
        tasks = [
            {"id": "T1", "deps": []},
            {"id": "T2", "deps": ["T1", "T3"]},
            {"id": "T3", "deps": ["T2"]},
            {"id": "T4", "deps": ["T1"]},
        ]
        dag = core.calculate_dag(tasks)
        self.assertTrue(dag["has_cycle"])
        nodes = {n["id"]: n for n in dag["nodes"]}
        self.assertFalse(nodes["T1"]["has_cycle"])
        self.assertFalse(nodes["T4"]["has_cycle"])
        self.assertTrue(nodes["T2"]["has_cycle"])
        self.assertTrue(nodes["T3"]["has_cycle"])
        self.assertEqual(nodes["T1"]["layer"], 0)


class TestPathSecurityGuard(unittest.TestCase):
    """测试文件路径防穿越与安全守卫。"""

    def setUp(self):
        self.root = ROOT

    def test_safe_resolve_relative_traversal(self):
        """测试传入 ../../etc/passwd 抛出 SecurityError。"""
        with self.assertRaises(core.SecurityError):
            core.safe_resolve_path(self.root, "../../etc/passwd")

    def test_safe_resolve_absolute_outside_path(self):
        """测试传入 /etc/passwd 等绝对越界路径抛出 SecurityError。"""
        with self.assertRaises(core.SecurityError):
            core.safe_resolve_path(self.root, "/etc/passwd")

    def test_safe_resolve_valid_path(self):
        """测试合法路径正常解析且属于 root。"""
        resolved = core.safe_resolve_path(self.root, "web/backend/wb_dashboard_core.py")
        self.assertTrue(resolved.is_file())
        self.assertTrue(resolved.is_relative_to(self.root.resolve()))

    def test_get_gate_log_security_interception(self):
        """测试在 get_gate_log 传入越界路径时被安全拦截。"""
        with self.assertRaises(core.SecurityError):
            core.get_gate_log(self.root, "../../etc/passwd")

    def test_get_task_detail_security_interception(self):
        """测试在 get_task_detail 传入越界 ID/路径时被安全拦截。"""
        with self.assertRaises(core.SecurityError):
            core.get_task_detail(self.root, "../../etc/passwd")

    def test_validate_flow_name_security_and_format(self):
        """测试 validate_flow_name 对合法、越界与非法字符的精准判定。"""
        # 合法 flow 名称
        self.assertEqual(core.validate_flow_name("main"), "main")
        self.assertEqual(core.validate_flow_name("feature-b"), "feature-b")
        self.assertEqual(core.validate_flow_name("fix_issue_123"), "fix_issue_123")
        self.assertIsNone(core.validate_flow_name(None))

        # 越界穿越路径 -> SecurityError
        with self.assertRaises(core.SecurityError):
            core.validate_flow_name("../../etc")
        with self.assertRaises(core.SecurityError):
            core.validate_flow_name("flow/child")

        # 格式非法（大写、空格、特殊字符） -> ValueError
        with self.assertRaises(ValueError):
            core.validate_flow_name("Feature-A")
        with self.assertRaises(ValueError):
            core.validate_flow_name("flow with spaces")
        with self.assertRaises(ValueError):
            core.validate_flow_name("")


class TestRealWorkspaceDataIntegration(unittest.TestCase):
    """在当前 Workbench 真实数据上验证状态一致性与 API 聚合。"""

    def setUp(self):
        self.root = ROOT
        self.state = core.load_workbench_state(self.root, flow="main")

    def test_dag_consistency_with_workbench_state(self):
        """验证真实数据上读取出的 DAG、任务列表与 state.json 完全一致。"""
        dag = core.get_tasks_dag(self.root, flow="main")
        raw_tasks = self.state.get("tasks", [])

        # 任务总数一致
        self.assertEqual(dag["total_nodes"], len(raw_tasks))

        # 任务 ID 序列与状态一致
        raw_ids = [t["id"] for t in raw_tasks]
        dag_ids = [n["id"] for n in dag["nodes"]]
        self.assertEqual(set(dag_ids), set(raw_ids))

        dag_nodes_map = {n["id"]: n for n in dag["nodes"]}
        for t in raw_tasks:
            tid = t["id"]
            node = dag_nodes_map[tid]
            self.assertEqual(node["status"], t.get("status"))
            self.assertEqual(node["role"], t.get("role"))
            self.assertEqual(node["phase"], t.get("phase"))
            self.assertEqual(node["deps"], t.get("deps", []))

    def test_overview_data(self):
        """验证 overview 接口能正确反映 Flow、阶段与角色锁信息。"""
        ov = core.get_overview(self.root, flow="main")
        self.assertEqual(ov["project"], self.state.get("project"))
        self.assertEqual(ov["current_flow"], "main")
        self.assertIn("main", ov["flows"])
        self.assertEqual(ov["current_phase"], self.state.get("phase"))
        self.assertEqual(len(ov["phases"]), 6)
        self.assertIsInstance(ov["role_locked"], bool)
        self.assertEqual(ov["total_tasks"], len(self.state.get("tasks", [])))

    def test_task_detail_extraction(self):
        """验证任务细节（代码改动、复核命令、Markdown）提取。"""
        t1_detail = core.get_task_detail(self.root, "T1", flow="main")
        self.assertIsNotNone(t1_detail)
        self.assertEqual(t1_detail["id"], "T1")
        self.assertEqual(t1_detail["role"], "backend-developer")
        # 验证 verification.md 解析
        ver = t1_detail["verification"]
        self.assertGreater(len(ver["commands"]), 0)
        self.assertTrue(any("selfcheck" in cmd["command"] for cmd in ver["commands"]))

        # 验证不存在的任务返回 None
        none_detail = core.get_task_detail(self.root, "NON_EXISTENT_TASK_ID", flow="main")
        self.assertIsNone(none_detail)

    def test_contracts_and_audit(self):
        """验证契约与审计日志读取。"""
        contracts_data = core.get_contracts(self.root, flow="main")
        self.assertIn("contracts", contracts_data)
        self.assertIn("unlocks", contracts_data)
        self.assertIn("disputes", contracts_data)
        self.assertEqual(len(contracts_data["contracts"]), len(self.state.get("contracts", [])))

        audit = core.get_audit_log(self.root, flow="main", limit=5)
        self.assertIsInstance(audit, list)
        self.assertLessEqual(len(audit), 5)


def main():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()

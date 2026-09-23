#!/usr/bin/env python3
"""wb_dashboard: Workbench 可视化看板本地 Web 服务与 SSE 实时事件通道 (FastAPI 重构版)。

提供基于 FastAPI + Uvicorn 现代异步 Web 栈的高性能本地服务：
- REST API：提供概览、DAG 拓扑、任务细节、门禁日志、契约与审计流。
- SSE 通道：毫秒级监听 .workbench 文件变动并实时广播 state_change 事件。
- 自动化文档：原生提供 /docs (Swagger UI) 交互式调试面板。
- 静态导出：支持烘焙单文件离线 HTML 报告。
- 安全守卫：严防跨目录越界与路径穿越。
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import queue
import re
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
import uvicorn

# 动态引入 core 模块
_WEB_DIR = Path(__file__).resolve().parent
if str(_WEB_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_DIR))

import wb_dashboard_core as core

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8088
SSE_PING_INTERVAL = 15.0
POLL_INTERVAL = 0.5


# --------------------------------------------------------------------------
# SSE 文件变动轮询与事件广播器
# --------------------------------------------------------------------------

class StateWatcher:
    """轮询监听 .workbench 关键文件 mtime 变动，向已连接的 SSE 客户端广播事件。"""

    def __init__(self, root: Path, poll_interval: float = POLL_INTERVAL):
        self.root = Path(root).resolve()
        self.poll_interval = poll_interval
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_mtimes: dict[str, float] = self._collect_mtimes()

    def _get_watch_targets(self) -> list[Path]:
        """收集所有需监听的关键文件路径（定向扫描，避免无界全量递归）。"""
        targets: list[Path] = []
        wbd = self.root / ".workbench"
        if not wbd.is_dir():
            return targets

        # 根级状态与核心元数据
        for name in ("current-flow", "state.json", "role", "unlock", "dispute", "artifacts.jsonl", "audit.jsonl"):
            p = wbd / name
            if p.is_file():
                targets.append(p)

        # flows 目录下各需求线的状态、日志与审计
        flows_dir = wbd / "flows"
        if flows_dir.is_dir():
            try:
                for flow_entry in flows_dir.iterdir():
                    if flow_entry.is_dir():
                        for name in ("state.json", "role", "unlock", "dispute", "audit.jsonl"):
                            fp = flow_entry / name
                            if fp.is_file():
                                targets.append(fp)
                        for log_p in flow_entry.glob("gate-*.log"):
                            if log_p.is_file():
                                targets.append(log_p)
            except OSError:
                pass

        # artifacts 目录下各阶段产物、复核文档与任务现场笔记
        art_dir = wbd / "artifacts"
        if art_dir.is_dir():
            try:
                for flow_art in art_dir.iterdir():
                    if flow_art.is_dir():
                        for phase_dir in flow_art.iterdir():
                            if phase_dir.is_dir():
                                for md_p in phase_dir.glob("*.md"):
                                    if md_p.is_file():
                                        targets.append(md_p)
                                tasks_dir = phase_dir / "tasks"
                                if tasks_dir.is_dir():
                                    for note_p in tasks_dir.glob("*.md"):
                                        if note_p.is_file():
                                            targets.append(note_p)
            except OSError:
                pass

        return targets

    def _collect_mtimes(self) -> dict[str, float]:
        mtimes: dict[str, float] = {}
        for p in self._get_watch_targets():
            try:
                mtimes[str(p)] = p.stat().st_mtime
            except OSError:
                pass
        return mtimes

    def subscribe(self) -> queue.Queue:
        """注册一个新的 SSE 客户端订阅队列。"""
        q: queue.Queue = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        """注销已断开的客户端队列。"""
        with self._lock:
            self._subscribers.discard(q)

    def broadcast(self, event_data: dict[str, Any]) -> None:
        """向所有活跃订阅队列分发事件。"""
        with self._lock:
            dead_queues = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event_data)
                except queue.Full:
                    dead_queues.append(q)
            for dq in dead_queues:
                self._subscribers.discard(dq)

    def notify(self, flow: str | None = None, changed_files: list[str] | None = None) -> None:
        """主动触发一次状态变更广播通知（供测试或钩子直接调用）。"""
        target_flow = flow
        if not target_flow and core.read_current_flow:
            try:
                target_flow = core.read_current_flow(self.root)
            except Exception:
                target_flow = "main"
        self.broadcast({
            "event": "state_change",
            "flow": target_flow or "main",
            "changed_files": changed_files or [],
            "timestamp": time.time(),
        })

    def start(self) -> None:
        """启动后台轮询线程。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="DashboardStateWatcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """优雅停止后台轮询线程。"""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        with self._lock:
            for q in self._subscribers:
                try:
                    q.put_nowait({"type": "shutdown"})
                except Exception:
                    pass
            self._subscribers.clear()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                current_mtimes = self._collect_mtimes()
                changed = False
                changed_files: list[str] = []

                # 检查修改或新增的文件
                for p_str, mt in current_mtimes.items():
                    if p_str not in self._last_mtimes or mt > self._last_mtimes[p_str]:
                        changed = True
                        try:
                            rel = os.path.relpath(p_str, self.root)
                        except ValueError:
                            rel = p_str
                        changed_files.append(rel)

                # 检查被删除的文件
                for p_str in list(self._last_mtimes.keys()):
                    if p_str not in current_mtimes:
                        changed = True
                        try:
                            rel = os.path.relpath(p_str, self.root)
                        except ValueError:
                            rel = p_str
                        changed_files.append(f"deleted:{rel}")

                if changed:
                    self._last_mtimes = current_mtimes
                    current_flow = "main"
                    if core.read_current_flow:
                        try:
                            current_flow = core.read_current_flow(self.root)
                        except Exception:
                            pass
                    self.broadcast({
                        "event": "state_change",
                        "flow": current_flow,
                        "changed_files": changed_files,
                        "timestamp": time.time(),
                    })
            except Exception:
                pass

            self._stop_event.wait(timeout=self.poll_interval)


# --------------------------------------------------------------------------
# 离线单文件导出模板 (按需加载，服务运行时不占内存)
# --------------------------------------------------------------------------

TEMPLATE_FILE = _WEB_DIR / "dashboard_template.html"

def load_export_template() -> str:
    """按需加载离线单文件导出模板。"""
    if TEMPLATE_FILE.is_file():
        return TEMPLATE_FILE.read_text(encoding="utf-8")
    return ""

DASHBOARD_HTML_TEMPLATE = load_export_template()


def render_live_dashboard_html(root: Path, target_flow: str = "main") -> str:
    """渲染单文件自包含 HTML 看板 (Live 实时模式)。"""
    template = load_export_template() or DASHBOARD_HTML_TEMPLATE
    if not template:
        return "<!DOCTYPE html><html><body><h1>Workbench Dashboard</h1><p>Dashboard template missing.</p></body></html>"
    ov = core.get_overview(root, flow=target_flow) if hasattr(core, "get_overview") else {}
    proj_name = html.escape(str(ov.get("project", "workbench")))
    phase_name = html.escape(str(ov.get("current_phase", "unknown")))
    ver = html.escape(str(ov.get("version", getattr(core, "WB_VERSION", "0.1.0"))))

    live_meta = {
        "is_static": False,
        "project": proj_name,
        "current_flow": target_flow,
    }
    initial_json = json.dumps(live_meta, ensure_ascii=False)
    return (
        template
        .replace("{{PROJECT}}", proj_name)
        .replace("{{FLOW}}", html.escape(target_flow))
        .replace("{{PHASE}}", phase_name)
        .replace("{{VERSION}}", ver)
        .replace("{{INITIAL_DATA_JSON}}", initial_json)
    )


# --------------------------------------------------------------------------
# FastAPI 应用工厂
# --------------------------------------------------------------------------

def create_app(root: Path, watcher: StateWatcher | None = None) -> FastAPI:
    """构建看板 FastAPI 实例，挂载路由、CORS 中间件与统一异常拦截。"""
    actual_root = Path(root).resolve()

    app = FastAPI(
        title="Workbench Dashboard API",
        version=getattr(core, "WB_VERSION", "0.1.0"),
        docs_url="/docs",
        redoc_url=None,
    )

    # 统一 CORS 处理（支持所有方法与直接 OPTIONS 请求）
    @app.middleware("http")
    async def add_cors_headers(request: Request, call_next):
        if request.method == "OPTIONS":
            return Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": "*",
                },
            )
        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "*"
        return response

    # 统一安全与参数异常拦截
    @app.exception_handler(core.SecurityError)
    async def security_error_handler(request: Request, exc: core.SecurityError):
        return JSONResponse(status_code=403, content={"error": "Forbidden", "detail": str(exc)})

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"error": "Bad Request", "detail": str(exc)})

    # 前端自包含单文件看板页面交付 (Live 实时模式)
    @app.get("/")
    @app.get("/index.html")
    async def serve_dashboard_ui(flow: str | None = Query(None)):
        current_flow = flow or (core.read_current_flow(actual_root) if getattr(core, "read_current_flow", None) else "main")
        page_html = render_live_dashboard_html(actual_root, target_flow=current_flow)
        return HTMLResponse(content=page_html, status_code=200)

    # 根静态文件友好处理 (如 favicon.ico，避免控制台刷屏 404)
    @app.get("/favicon.ico")
    async def serve_favicon():
        return Response(status_code=204)

    # 静态第三方库资源交付 (web/vendor 目录)
    @app.get("/vendor/{filename}")
    async def serve_vendor_asset(filename: str):
        vendor_dir = (_WEB_DIR / "vendor").resolve()
        vendor_file = (vendor_dir / filename).resolve()
        if not str(vendor_file).startswith(str(vendor_dir)):
            return JSONResponse(status_code=403, content={"error": "Forbidden", "detail": "Path traversal detected"})
        if not vendor_file.is_file():
            return JSONResponse(status_code=404, content={"error": f"Vendor file '{filename}' not found"})
        return Response(content=vendor_file.read_bytes(), media_type="application/javascript; charset=utf-8")

    # API 规范与服务元数据清单
    @app.get("/api")
    async def api_info():
        return {
            "service": "wb-dashboard-api",
            "status": "online",
            "version": getattr(core, "WB_VERSION", "0.1.0"),
            "endpoints": {
                "overview": "/api/overview",
                "tasks": "/api/tasks",
                "task_detail": "/api/task-detail?id=<task_id>",
                "gate_log": "/api/gate-log?name=<gate_name>",
                "contracts": "/api/contracts",
                "audit": "/api/audit?limit=<n>",
                "events": "/api/events (SSE)",
                "docs": "/docs",
            },
        }

    @app.get("/api/overview")
    async def api_overview(flow: str | None = Query(None)):
        return core.get_overview(actual_root, flow=flow)

    @app.get("/api/tasks")
    async def api_tasks(flow: str | None = Query(None)):
        return core.get_tasks_dag(actual_root, flow=flow)

    @app.get("/api/task-detail")
    async def api_task_detail(id: str | None = Query(None), flow: str | None = Query(None)):
        if not id:
            return JSONResponse(status_code=400, content={"error": "Missing required parameter 'id'"})
        detail = core.get_task_detail(actual_root, id, flow=flow)
        if detail is None:
            return JSONResponse(status_code=404, content={"error": f"Task '{id}' not found", "detail": f"id={id}"})
        return detail

    @app.get("/api/gate-log")
    async def api_gate_log(
        name: str | None = Query(None),
        flow: str | None = Query(None),
        format: str = Query("text"),
    ):
        if not name:
            return JSONResponse(status_code=400, content={"error": "Missing required parameter 'name'"})
        log_text = core.get_gate_log(actual_root, name, flow=flow)
        if format == "json":
            return {"name": name, "log": log_text}
        return PlainTextResponse(log_text, headers={"Content-Type": "text/plain; charset=utf-8"})

    @app.get("/api/contracts")
    async def api_contracts(flow: str | None = Query(None)):
        return core.get_contracts(actual_root, flow=flow)

    @app.get("/api/audit")
    async def api_audit(limit: int = Query(50), flow: str | None = Query(None)):
        return core.get_audit_log(actual_root, limit=limit, flow=flow)

    @app.get("/api/events")
    async def api_events():
        async def sse_stream():
            q = watcher.subscribe() if watcher else queue.Queue()
            yield ": connected\n\n"
            try:
                while True:
                    try:
                        ev = await asyncio.to_thread(q.get, True, SSE_PING_INTERVAL)
                        yield f"event: state_change\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    except queue.Empty:
                        yield ": ping\n\n"
            finally:
                if watcher:
                    watcher.unsubscribe(q)

        return StreamingResponse(
            sse_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app


# --------------------------------------------------------------------------
# 看板独立服务器封装 (兼容标准控制接口与端口分配)
# --------------------------------------------------------------------------

class DashboardServer:
    """基于 FastAPI + Uvicorn 的看板服务，具备可控生命周期与端口分配能力。"""

    def __init__(
        self,
        root: Path,
        app: FastAPI,
        watcher: StateWatcher,
        host: str,
        port: int,
        quiet: bool = False,
    ):
        self.root = root
        self.app = app
        self.watcher = watcher
        self.host = host
        self.quiet = quiet

        # 预绑定 Socket 分配操作系统空闲端口 (支持 port=0 随机端口)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        actual_port = self._sock.getsockname()[1]
        self.server_address = (host, actual_port)

        log_level = "critical" if quiet else "warning"
        self.config = uvicorn.Config(
            app=self.app,
            host=host,
            port=actual_port,
            log_level=log_level,
            access_log=not quiet,
        )
        self.uvicorn_server = uvicorn.Server(self.config)

    def serve_forever(self) -> None:
        """运行服务器直到外部发出退出信号。"""
        self.uvicorn_server.run(sockets=[self._sock])

    def shutdown(self) -> None:
        """优雅关闭服务。"""
        self.uvicorn_server.should_exit = True

    def server_close(self) -> None:
        """关闭底层套接字资源 (Uvicorn shutdown 会自动管理)。"""
        pass


def create_server(
    root: Path | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    quiet: bool = False,
    poll_interval: float = POLL_INTERVAL,
) -> DashboardServer:
    """创建看板服务实例并初始化文件监听器。"""
    target_root = Path(root).resolve() if root else (core.find_root() if core.find_root else Path.cwd())
    watcher = StateWatcher(target_root, poll_interval=poll_interval)
    app = create_app(target_root, watcher=watcher)
    return DashboardServer(
        root=target_root,
        app=app,
        watcher=watcher,
        host=host,
        port=port,
        quiet=quiet,
    )


def run_server(server: DashboardServer, open_browser: bool = False, flow: str | None = None) -> None:
    """启动看板 Web 服务与文件变动轮询。"""
    host, port = server.server_address
    url = f"http://{host}:{port}"
    open_url = f"{url}/?flow={flow}" if flow else url
    if not server.quiet:
        sys.stdout.write(f"\n=======================================================\n")
        sys.stdout.write(f" 🚀 Workbench Dashboard (FastAPI) 已启动: {url}\n")
        sys.stdout.write(f" 📖 API 交互文档 (Swagger UI): {url}/docs\n")
        sys.stdout.write(f" ⌨️  按 Ctrl+C 退出服务\n")
        sys.stdout.write(f"=======================================================\n\n")
        sys.stdout.flush()

    server.watcher.start()
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(open_url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.watcher.stop()
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------
# 静态离线单文件导出引擎
# --------------------------------------------------------------------------

def build_static_export_data(root: Path, target_flow: str = "main") -> dict[str, Any]:
    """聚合工作区内所有 Flow、任务 DAG、细节与日志，预烘焙用于单文件离线展示。"""
    actual_root = Path(root).resolve()
    base_overview = core.get_overview(actual_root)
    all_flow_list = list(base_overview.get("flows", ["main"]))
    if not all_flow_list:
        all_flow_list = ["main"]

    current_flow = target_flow or base_overview.get("current_flow", "main") or "main"
    if current_flow not in all_flow_list:
        all_flow_list.insert(0, current_flow)

    flows_data: dict[str, dict[str, Any]] = {}
    for fl in all_flow_list:
        try:
            ov = core.get_overview(actual_root, flow=fl)
            tasks_dag = core.get_tasks_dag(actual_root, flow=fl)
            contracts = core.get_contracts(actual_root, flow=fl)
            audit = core.get_audit_log(actual_root, limit=100, flow=fl)

            task_details: dict[str, Any] = {}
            for node in tasks_dag.get("nodes", []):
                tid = node.get("id")
                if tid:
                    try:
                        detail = core.get_task_detail(actual_root, tid, flow=fl)
                        if detail:
                            task_details[tid] = detail
                    except Exception:
                        pass

            gate_names = set(["test", "lint", "build"])
            for g in ov.get("configured_gates", []):
                gate_names.add(g)
            for g in ov.get("gate_logs", []):
                gate_names.add(g)

            gate_logs: dict[str, str] = {}
            for g in sorted(gate_names):
                try:
                    log_content = core.get_gate_log(actual_root, g, flow=fl)
                    if log_content:
                        gate_logs[g] = log_content
                except Exception:
                    pass

            flows_data[fl] = {
                "overview": ov,
                "tasks": tasks_dag,
                "task_details": task_details,
                "contracts": contracts,
                "audit": audit,
                "gate_logs": gate_logs,
            }
        except Exception as e:
            sys.stderr.write(f"警告：收集 flow '{fl}' 静态数据时出错: {e}\n")

    current_data = flows_data.get(current_flow, {})

    export_payload: dict[str, Any] = {
        "is_static": True,
        "export_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "current_flow": current_flow,
        "flows": all_flow_list,
        "flows_data": flows_data,
        "overview": current_data.get("overview", {}),
        "tasks": current_data.get("tasks", {}),
        "task_details": current_data.get("task_details", {}),
        "contracts": current_data.get("contracts", {}),
        "audit": current_data.get("audit", []),
        "gate_logs": current_data.get("gate_logs", {}),
    }
    return export_payload


def export_static_dashboard(
    root: Path | None = None,
    output_path: Path | str = "dashboard_static.html",
    flow: str | None = None,
    quiet: bool = False,
) -> Path:
    """烘焙并导出独立单文件 HTML 看板，包含完整的离线数据快照。"""
    core.validate_flow_name(flow)
    actual_root = Path(root or (core.find_root() if core.find_root else Path.cwd())).resolve()
    target_flow = flow or (core.read_current_flow(actual_root) if core.read_current_flow else "main") or "main"
    export_data = build_static_export_data(actual_root, target_flow=target_flow)

    ov = export_data.get("overview", {})
    proj_name = html.escape(str(ov.get("project", "workbench")))
    phase_name = html.escape(str(ov.get("current_phase", "unknown")))
    ver = html.escape(str(ov.get("version", "0.1.0")))

    initial_json = json.dumps(export_data, ensure_ascii=False).replace("</script>", "<\\/script>")

    vendor_dir = _WEB_DIR / "vendor"
    marked_file = vendor_dir / "marked.min.js"
    prism_file = vendor_dir / "prism.min.js"
    diff_file = vendor_dir / "diff.min.js"
    fuse_file = vendor_dir / "fuse.min.js"
    marked_code = marked_file.read_text(encoding="utf-8") if marked_file.is_file() else ""
    prism_code = prism_file.read_text(encoding="utf-8") if prism_file.is_file() else ""
    diff_code = diff_file.read_text(encoding="utf-8") if diff_file.is_file() else ""
    fuse_code = fuse_file.read_text(encoding="utf-8") if fuse_file.is_file() else ""

    html_content = (
        DASHBOARD_HTML_TEMPLATE
        .replace("{{PROJECT}}", proj_name)
        .replace("{{FLOW}}", html.escape(target_flow))
        .replace("{{PHASE}}", phase_name)
        .replace("{{VERSION}}", ver)
        .replace("{{INITIAL_DATA_JSON}}", initial_json)
    )

    if marked_code:
        html_content = re.sub(
            r'<script\s+src=["\x27](?:/vendor/|vendor/)marked\.min\.js["\x27]>\s*</script>',
            lambda _: f'<script id="__VENDOR_MARKED__">\n{marked_code}\n</script>',
            html_content,
        )
    if prism_code:
        html_content = re.sub(
            r'<script\s+src=["\x27](?:/vendor/|vendor/)prism\.min\.js["\x27]>\s*</script>',
            lambda _: f'<script id="__VENDOR_PRISM__">\n{prism_code}\n</script>',
            html_content,
        )
    if diff_code:
        html_content = re.sub(
            r'<script\s+src=["\x27](?:/vendor/|vendor/)diff\.min\.js["\x27]>\s*</script>',
            lambda _: f'<script id="__VENDOR_DIFF__">\n{diff_code}\n</script>',
            html_content,
        )
    if fuse_code:
        html_content = re.sub(
            r'<script\s+src=["\x27](?:/vendor/|vendor/)fuse\.min\.js["\x27]>\s*</script>',
            lambda _: f'<script id="__VENDOR_FUSE__">\n{fuse_code}\n</script>',
            html_content,
        )

    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(html_content, encoding="utf-8")

    if not quiet:
        file_size_kb = len(html_content.encode("utf-8")) / 1024
        sys.stdout.write(f"\n📦 Workbench 看板静态单文件导出成功！\n")
        sys.stdout.write(f"📄 目标文件: {out_file} ({file_size_kb:.1f} KB)\n")
        sys.stdout.write(f"✨ 包含 Flow: {', '.join(export_data.get('flows', []))}\n")
        sys.stdout.write("💡 该文件完全自包含，无外部依赖，可直接双击用浏览器打开或随项目归档。\n\n")
        sys.stdout.flush()

    return out_file


# --------------------------------------------------------------------------
# CLI 命令行入口
# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Workbench 可视化看板 HTTP 与 SSE 服务 (FastAPI)")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"监听主机地址 (默认: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"监听端口号 (默认: {DEFAULT_PORT})")
    parser.add_argument("--root", type=str, default=None, help="Workbench 工作区根目录 (默认自动探测)")
    parser.add_argument("--open", action="store_true", help="启动后自动在浏览器中打开")
    parser.add_argument("--export", type=str, default=None, help="导出静态单文件 HTML 看板报告并退出")
    parser.add_argument("--flow", type=str, default=None, help="指定需求线 flow (默认当前 flow)")
    parser.add_argument("--quiet", action="store_true", help="静默模式，不输出请求日志")
    parser.add_argument("--poll-interval", type=float, default=POLL_INTERVAL, help="SSE 轮询探测周期秒数 (默认: 0.5)")

    args = parser.parse_args()

    target_root = Path(args.root).resolve() if args.root else None

    if args.export:
        export_static_dashboard(
            root=target_root,
            output_path=args.export,
            flow=args.flow,
            quiet=args.quiet,
        )
        return

    server = create_server(
        root=target_root,
        host=args.host,
        port=args.port,
        quiet=args.quiet,
        poll_interval=args.poll_interval,
    )
    run_server(server, open_browser=args.open, flow=args.flow)


if __name__ == "__main__":
    main()

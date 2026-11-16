"""Web 后端服务（FastAPI + SSE 流式推送）。

启动：python3 main.py --web [--host 127.0.0.1] [--port 8765]
环境变量：OPS_WEB_HOST / OPS_WEB_PORT / OPS_WEB_CORS（=1 开启跨域）

接口：
  GET    /                    对话页面（web/index.html）
  GET    /healthz             健康检查
  GET    /api/capabilities    组件、Tool、Skill 和数据源能力
  GET    /api/status          工具模式 / 知识库模式 / 模型状态
  GET    /api/check           组件连通性自检
  GET    /api/wiki?q=         知识库检索（LLM_WIKI）
  POST   /api/alerts          Alertmanager webhook / 简化告警入口
  POST   /api/chat            一轮对话（SSE：message / approval / end / error）
  POST   /api/resume          审批结果恢复（approve + operator）
  DELETE /api/session/{id}    清理会话状态（SqliteSaver delete_thread）

审批协议：写操作触发 LangGraph interrupt 时，服务端推送 approval 事件并结束本次响应；
前端确认后调用 /api/resume，由 checkpointer 中的暂停状态继续执行。
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Iterator

from fastapi import (
    BackgroundTasks,
    Body,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from pydantic import BaseModel, Field

from src.alerts import (
    AlertValidationError,
    alert_summary,
    normalize_alert_payload,
)
from src.skill_registry import build_skill_context

# 日志需在导入 src.agent（构建 SQLite checkpoint）之前就绪，否则启动日志被丢弃；
# basicConfig 幂等，main() 中的重复调用在 root 已有 handler 时自动跳过
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from src.agent import get_chat_graph  # noqa: E402

logger = logging.getLogger("opsai.web")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
THREAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# 同一会话串行执行：避免并发请求打断 checkpointer 中的暂停状态
_thread_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


# ---------- 应用生命周期 ----------

@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("对话服务已启动：对话链路写操作审批走 interrupt")
    yield
    logger.info("对话服务已停止")


app = FastAPI(title="Ops AI 对话服务", version="0.3.0", lifespan=lifespan)

if os.getenv("OPS_WEB_CORS", "").lower() in {"1", "true", "yes"}:
    # 默认关闭：同源部署无需跨域；前后端分离部署时用环境变量开启
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def _access_log(request: Request, call_next):
    """访问日志与安全响应头（SSE 耗时仅统计到响应头返回）。"""
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info("%s %s -> %s (%.1fms)", request.method, request.url.path, response.status_code, elapsed_ms)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


# ---------- 请求模型 ----------

class ChatRequest(BaseModel):
    thread_id: str | None = Field(default=None, max_length=64)
    text: str = Field(min_length=1, max_length=8000)


class ResumeRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=64)
    approve: bool = False
    operator: str | None = Field(default=None, max_length=120)


# ---------- 对话事件流 ----------

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _iter_events(payload: Any, thread_id: str) -> Iterator[dict]:
    """执行一轮图，产出可序列化事件；遇审批中断时产出 approval 并结束。"""
    graph = get_chat_graph()
    config = {"configurable": {"thread_id": thread_id}}

    pending = None
    for event in graph.stream(payload, config=config, stream_mode="updates"):
        if "__interrupt__" in event:
            pending = event["__interrupt__"][0]
            continue
        for node, output in event.items():
            if node.startswith("__") or not isinstance(output, dict):
                continue
            for msg in output.get("messages") or []:
                yield {"type": "message", "node": node, "content": str(msg.content)}

    if pending is None:
        yield {"type": "end"}
        return

    value = pending.value if isinstance(pending.value, dict) else {"action": str(pending.value)}
    yield {
        "type": "approval",
        "action": value.get("action", ""),
        "args": value.get("args", {}),
    }


def _sse_events(payload: Any, thread_id: str, lock: threading.Lock) -> Iterator[str]:
    """同步生成器：Starlette 会放到线程池迭代，不阻塞事件循环。"""
    try:
        for event in _iter_events(payload, thread_id):
            yield _sse(event)
    except Exception as exc:  # noqa: BLE001 - 单轮异常需回传前端展示
        logger.exception("会话 %s 执行失败", thread_id)
        yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
    finally:
        lock.release()


def _sse_response(payload: Any, thread_id: str, lock: threading.Lock) -> StreamingResponse:
    return StreamingResponse(
        _sse_events(payload, thread_id, lock),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 反向代理（Nginx）下禁用缓冲
        },
    )


def _acquire(thread_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _thread_locks.setdefault(thread_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="同一会话正在处理中，请等待本轮结束")
    return lock


def _ensure_thread_id(raw: str | None) -> str:
    if raw and THREAD_ID_PATTERN.match(raw):
        return raw
    return f"web-{uuid.uuid4().hex[:8]}"


def _resolve_web_operator(req: ResumeRequest, request: Request) -> tuple[str, str]:
    body_operator = (req.operator or "").strip()
    if body_operator:
        return body_operator, "web_body"
    header_operator = request.headers.get("X-Operator", "").strip()
    if header_operator:
        return header_operator, "web_header"
    raise HTTPException(status_code=422, detail="审批人标识不能为空")


def _verify_alert_token(request: Request) -> None:
    """可选的 webhook 共享密钥校验，未配置时保持兼容。"""
    expected = os.getenv("ALERT_WEBHOOK_TOKEN", "")
    if not expected:
        return

    provided = request.headers.get("X-Alert-Token", "")
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if not secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="告警 webhook 鉴权失败")


def _diagnose_alert(alert: dict[str, Any]) -> None:
    """后台执行单条告警诊断；告警入口不允许自动执行写操作。"""
    from src.agent import run_agent

    alert_id = alert.get("alert_id", "")
    try:
        steps = list(
            run_agent(
                alert,
                mode="offline_with_rag",
                approved=False,
                source="alert_webhook",
            )
        )
        logger.info("告警 %s 诊断完成: %s", alert_id, json.dumps(steps, ensure_ascii=False))
    except Exception:
        logger.exception("告警 %s 诊断失败", alert_id)


# ---------- 路由 ----------

@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    page = WEB_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(status_code=500, detail=f"前端页面缺失: {page}")
    return FileResponse(page, media_type="text/html; charset=utf-8")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": app.version}


@app.get("/api/capabilities")
def capabilities() -> dict[str, Any]:
    from src.capabilities import build_capabilities

    return build_capabilities()


@app.get("/api/status")
def status() -> dict[str, Any]:
    from src.config_loader import resolve_model_config, resolve_ops_config
    from src.tools import get_tools_mode

    model_config = resolve_model_config()
    ops_config = resolve_ops_config()
    info: dict[str, Any] = {
        "environment": ops_config["environment_name"],
        "tools_mode": get_tools_mode(),
        "prometheus_url": ops_config["prometheus_base_url"],
        "loki_url": ops_config["loki_base_url"],
        "llm_provider": model_config["llm_provider"],
        "llm_model": model_config["llm_model"],
        "llm_offline": model_config["llm_provider"] not in {"openai", "ollama"}
        or (
            model_config["llm_provider"] == "openai"
            and not bool(model_config["llm_api_key"])
        ),
        "embedding_provider": model_config["embed_provider"],
        "embedding_model": model_config["embed_model"],
    }
    try:
        from src.llm_wiki import get_wiki

        info["wiki_mode"] = get_wiki().mode
    except Exception as exc:  # noqa: BLE001 - 知识库初始化失败不应阻断状态接口
        info["wiki_mode"] = f"unavailable: {exc}"
    return info


@app.get("/api/check")
def check() -> dict[str, list[str]]:
    from src.tools import connectivity_check

    return {"lines": connectivity_check()}


@app.get("/api/wiki")
def wiki(
    q: str = Query(min_length=1, max_length=500),
    service: str = Query(default="", max_length=100),
) -> dict[str, str]:
    from src.llm_wiki import get_wiki

    try:
        engine = get_wiki()
        return {"mode": engine.mode, "answer": engine.search(q, top_k=2, service=service or None)}
    except Exception as exc:  # noqa: BLE001 - 检索异常需返回给前端展示
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/api/chat")
def chat(req: ChatRequest) -> StreamingResponse:
    thread_id = _ensure_thread_id(req.thread_id)
    skill_context = build_skill_context(req.text)
    payload: dict[str, Any] = {
        "messages": [HumanMessage(content=req.text)],
        "alert": {},
        "iteration": 0,
        "approved": False,
        "interactive_approval": True,
        "skill_context": skill_context,
        "approval_context": {
            "thread_id": thread_id,
            "alert_id": "",
            "source": "web",
            "actor": "",
            "actor_source": "",
            "skills": skill_context["skills"],
        },
    }
    return _sse_response(payload, thread_id, _acquire(thread_id))


@app.post("/api/alerts", status_code=202)
def receive_alerts(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """接收 Alertmanager webhook 或简化告警，并异步触发只读诊断。"""
    _verify_alert_token(request)
    try:
        alerts = normalize_alert_payload(payload)
    except AlertValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    queued = 0
    for alert in alerts:
        if alert["status"] == "resolved":
            continue
        background_tasks.add_task(_diagnose_alert, alert)
        queued += 1

    return {
        "accepted": len(alerts),
        "queued": queued,
        "alerts": [alert_summary(alert) for alert in alerts],
    }


@app.post("/api/resume")
def resume(req: ResumeRequest, request: Request) -> StreamingResponse:
    if not THREAD_ID_PATTERN.match(req.thread_id):
        raise HTTPException(status_code=400, detail="thread_id 不合法")
    actor, actor_source = _resolve_web_operator(req, request)
    decision = "approved" if req.approve else "rejected"
    resume_value = {
        "approved": req.approve,
        "decision": decision,
        "actor": actor,
        "actor_source": actor_source,
    }
    return _sse_response(
        Command(resume=resume_value),
        req.thread_id,
        _acquire(req.thread_id),
    )


@app.delete("/api/session/{thread_id}")
def delete_session(thread_id: str) -> dict[str, str]:
    if not THREAD_ID_PATTERN.match(thread_id):
        raise HTTPException(status_code=400, detail="thread_id 不合法")
    saver = getattr(get_chat_graph(), "checkpointer", None)
    try:
        if saver is not None and hasattr(saver, "delete_thread"):
            saver.delete_thread(thread_id)
    except Exception as exc:  # noqa: BLE001 - 清理失败不影响前端切换会话
        logger.warning("清理会话 %s 失败: %s", thread_id, exc)
    return {"deleted": thread_id}


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("未处理异常: %s %s", request.method, request.url.path)
    return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


# ---------- 入口 ----------

def _arg_value(flag: str) -> str | None:
    argv = sys.argv
    if flag in argv:
        idx = argv.index(flag)
        if idx + 1 < len(argv):
            return argv[idx + 1]
    return None


def main() -> int:
    host = _arg_value("--host") or os.getenv("OPS_WEB_HOST", "127.0.0.1")
    port_text = _arg_value("--port") or os.getenv("OPS_WEB_PORT", "8765")
    port = int(port_text) if str(port_text).isdigit() else 8765

    # 配置应用日志（uvicorn 仅配置自身 logger，root 默认无 handler）
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    import uvicorn

    print(f"Web 对话界面已启动: http://{host}:{port}  (Ctrl+C 停止)")
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

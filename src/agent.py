"""LangGraph 诊断 Agent（单次诊断 + CLI 多轮对话）。

设计要点：
- AgentState 使用 TypedDict + add_messages reducer，消息按轮次追加；
- iteration 为每轮工具调用计数，达到 MAX_ITERATIONS 强制收敛，防止死循环；
- 写操作（重启/扩容）经审批门放行：单次诊断用 approved 参数，
  对话模式用 LangGraph interrupt() 暂停等待用户确认（可审计）；
- 对话模式复用同一张图，checkpoint 支持 SQLite / PostgreSQL：
  会话（含审批暂停态）持久化后进程重启可续聊。
"""

import logging
import os
import re
import sqlite3
import time
from typing import Annotated, Any, Literal

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from typing_extensions import TypedDict

from src.approval_audit import get_approval_audit
from src.database import DatabaseConfig
from src.llm_client import call_llm
from src.observability import metrics
from src.skill_registry import build_skill_context
from src.tools import get_tool_registry

MAX_ITERATIONS = 8  # 单轮工具调用上限，防止死循环

logger = logging.getLogger(__name__)

CHECKPOINT_DB = os.getenv("CHECKPOINT_DB", "checkpoints.sqlite")  # 会话持久化库文件


def _build_checkpointer():
    """会话 checkpoint 存储：SQLite / PostgreSQL。"""
    db = DatabaseConfig.resolve(CHECKPOINT_DB, "CHECKPOINT_DB")
    if db.backend == "postgres":
        return _build_postgres_checkpointer(db)
    return _build_sqlite_checkpointer()


def _build_sqlite_checkpointer():
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        logger.warning("langgraph-checkpoint-sqlite 未安装，会话降级为进程内存储: %s", exc)
        return MemorySaver()

    try:
        conn = sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)
        saver = SqliteSaver(conn)
        saver.setup()
        logger.info("会话 checkpoint 已启用 SQLite 持久化: %s", CHECKPOINT_DB)
        return saver
    except Exception as exc:
        logger.warning("SQLite checkpoint 初始化失败（%s），降级为进程内存储", exc)
        return MemorySaver()


def _build_postgres_checkpointer(db: DatabaseConfig):
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:
        raise RuntimeError(
            "OPS_DB_BACKEND=postgres 需要 langgraph-checkpoint-postgres"
        ) from exc

    conn = db.open()
    saver = PostgresSaver(conn)
    saver.setup()
    logger.info("会话 checkpoint 已启用 PostgreSQL 持久化")
    return saver


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    alert: dict
    iteration: int  # 每轮工具调用次数（无 reducer，按轮重置）
    approved: bool  # 非交互式审批标记
    interactive_approval: bool  # 仅对话链路使用 interrupt() 等待人工确认
    approval_context: dict  # thread_id / alert_id / source / actor 等审计上下文
    skill_context: dict  # 当前命中的 Skill、版本和工具白名单


# ---------- 动作解析 ----------

_ACTION_RE = re.compile(r"([A-Za-z_]\w*)\s*\((.*)\)")
_KV_RE = re.compile(r"([A-Za-z_]\w*)\s*=\s*('[^']*'|\"[^\"]*\"|[^,)]+)")

# 工具注册表：支持内置工具和 Connector SDK 扩展
_TOOL_REGISTRY = get_tool_registry()
# 高危写操作：由工具元数据派生，必须经审批门放行
_WRITE_TOOLS = _TOOL_REGISTRY.write_tools()


def _coerce(value: str) -> Any:
    """将字符串参数转换为 int/bool/str。"""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lstrip("-").isdigit():
        return int(value)
    return value


def parse_action(text: str) -> tuple[str, dict]:
    """解析 'tool_name(key=value, ...)' 形式的动作文本。"""
    match = _ACTION_RE.search(text)
    if not match:
        return "", {}
    name, arg_str = match.group(1), match.group(2)
    kwargs = {k: _coerce(v) for k, v in _KV_RE.findall(arg_str)}
    return name, kwargs


def _dispatch(
    name: str,
    kwargs: dict,
    approved: bool,
    allowed_tools: set[str] | None = None,
) -> str:
    """按注册表调度工具；写操作强制走审批门。"""
    if allowed_tools and name not in allowed_tools:
        metrics.observe_tool_call(name, "denied", 0.0)
        return f"当前 Skill 策略不允许执行工具: {name}"

    started = time.perf_counter()
    try:
        result = _TOOL_REGISTRY.dispatch(name, kwargs, approved=approved)
    except Exception as exc:  # noqa: BLE001 - 把工具异常转为 Agent 可观察消息
        logger.exception("工具 %s 执行失败", name)
        result = f"工具执行失败: {name}: {type(exc).__name__}: {exc}"
        metrics.observe_tool_call(name, "error", time.perf_counter() - started)
        return result
    metrics.observe_tool_call(name, "success", time.perf_counter() - started)
    return result


def _record_write_approval(
    state: AgentState,
    name: str,
    kwargs: dict,
    *,
    approved: bool,
    decision: str,
    actor: str,
    actor_source: str,
) -> None:
    context = state.get("approval_context", {})
    audit_args = {key: value for key, value in kwargs.items() if key != "approved"}
    audit_skills = [
        {
            "id": skill.get("id", ""),
            "version": skill.get("version", ""),
            "content_hash": skill.get("content_hash", ""),
            "source": skill.get("file", ""),
        }
        for skill in context.get("skills", [])
    ]
    get_approval_audit().record(
        thread_id=str(context.get("thread_id", "")),
        alert_id=str(context.get("alert_id", "")),
        action=name,
        args=audit_args,
        decision=decision,
        approved=approved,
        actor=actor or "unknown",
        actor_source=actor_source or "unknown",
        source=str(context.get("source", "unknown")),
        trace_id=str(context.get("trace_id", "")),
        skills=audit_skills,
    )


# ---------- 图节点 ----------

def _route(state: AgentState) -> Literal["tools", "conclude"]:
    if state.get("iteration", 0) >= MAX_ITERATIONS:
        return "conclude"  # 熔断：超过最大轮次强制收敛
    last = state["messages"][-1].content if state["messages"] else ""
    if "ACTION:" in last:
        return "tools"
    return "conclude"


def _execute_tools(state: AgentState) -> dict:
    last = state["messages"][-1].content if state["messages"] else ""
    action = last.split("ACTION:")[-1].strip() if "ACTION:" in last else ""
    name, kwargs = parse_action(action)
    skill_context = state.get("skill_context", {})
    allowed_tools = set(skill_context.get("allowed_tools") or [])

    approved = state.get("approved", False)
    interactive_approval = bool(state.get("interactive_approval", False))
    if name in _WRITE_TOOLS:
        decision = "blocked"
        actor = "approval_gate"
        actor_source = "system"

        if interactive_approval and not approved:
            # 人工审批：图在此暂停，等待对话入口返回结构化审批结果
            resume = interrupt({"action": name, "args": kwargs})
            if isinstance(resume, dict):
                approved = bool(resume.get("approved", False))
                decision = str(resume.get("decision") or ("approved" if approved else "rejected"))
                actor = str(resume.get("actor") or "unknown")
                actor_source = str(resume.get("actor_source") or "unknown")
            else:
                approved = bool(resume)
                decision = "approved" if approved else "rejected"
                actor_source = "legacy"
        elif approved:
            context = state.get("approval_context", {})
            decision = "approved"
            actor = str(context.get("actor") or "unknown")
            actor_source = str(context.get("actor_source") or "caller")

        try:
            _record_write_approval(
                state,
                name,
                kwargs,
                approved=approved,
                decision=decision,
                actor=actor,
                actor_source=actor_source,
            )
        except Exception as exc:  # noqa: BLE001 - 审计失败时禁止写操作
            logger.exception("写操作 %s 的审批审计失败", name)
            return {
                "messages": [
                    AIMessage(content=f"审批审计写入失败，已拒绝执行 {name}: {exc}")
                ],
                "iteration": MAX_ITERATIONS,
            }

    result = _dispatch(
        name,
        kwargs,
        approved=approved,
        allowed_tools=allowed_tools,
    )
    if name in _WRITE_TOOLS and not approved:
        # 审批未通过：终止本轮，避免 LLM 对同一动作反复请求审批
        return {"messages": [AIMessage(content=result)], "iteration": MAX_ITERATIONS}
    return {
        "messages": [AIMessage(content=result)],
        "iteration": state.get("iteration", 0) + 1,
    }


def _conclude(state: AgentState) -> dict:
    last = state["messages"][-1].content if state["messages"] else ""
    return {"messages": [AIMessage(content=f"[结论] {last}")]}


def _llm_node(state: AgentState) -> dict:
    """LLM 节点适配器：为 LangGraph 提供精确的 AgentState 签名。"""
    return call_llm(state)


def _build_graph(checkpointer=None):
    builder = StateGraph(AgentState)
    builder.add_node("llm", _llm_node)
    builder.add_node("tools", _execute_tools)
    builder.add_node("conclude", _conclude)

    builder.add_edge(START, "llm")
    builder.add_conditional_edges("llm", _route)
    builder.add_edge("tools", "llm")
    builder.add_edge("conclude", END)
    return builder.compile(checkpointer=checkpointer)


_graph = _build_graph()
# 对话模式：SqliteSaver 落盘会话（多轮记忆 + interrupt 审批暂停/恢复，重启可续聊）
_chat_graph = _build_graph(checkpointer=_build_checkpointer())


def get_chat_graph():
    return _chat_graph


def run_agent(
    alert: dict,
    mode: str = "offline",
    approved: bool = False,
    approved_by: str = "",
    source: str = "single_run",
    trace_id: str = "",
):
    """生成器：逐轮产出诊断步骤（单次告警诊断，无会话记忆）。

    mode 含 "rag" 时在告警上下文中注入 wiki 检索指令，驱动 Agent 使用 LLM_WIKI；
    approved 为 False 时，写操作（重启/扩容）会被审批门拦截。
    """
    if approved and not approved_by.strip():
        raise ValueError("approved=True 时必须提供 approved_by")

    def _stream():
        prompt = f"告警: {alert}"
        if "rag" in mode:
            prompt += "\n请先完成只读诊断，再通过 wiki_search 检索运维知识库获取处置方案。"
        skill_context = build_skill_context(prompt, alert=alert)
        initial_state: AgentState = {
            "messages": [HumanMessage(content=prompt)],
            "alert": alert,
            "iteration": 0,
            "approved": approved,
            "interactive_approval": False,
            "skill_context": skill_context,
            "approval_context": {
                "thread_id": "",
                "alert_id": str(alert.get("alert_id", "")),
                "source": source,
                "actor": approved_by,
                "actor_source": "caller",
                "trace_id": trace_id,
                "skills": skill_context["skills"],
            },
        }
        for event in _graph.stream(initial_state):
            for node_name, output in event.items():
                if "messages" in output:
                    for msg in output["messages"]:
                        yield f"[{node_name}] {msg.content}"

    return _stream()

"""LangGraph 诊断 Agent（单次诊断 + CLI 多轮对话）。

设计要点：
- AgentState 使用 TypedDict + add_messages reducer，消息按轮次追加；
- iteration 为每轮工具调用计数，达到 MAX_ITERATIONS 强制收敛，防止死循环；
- 写操作（重启/扩容）经审批门放行：单次诊断用 approved 参数，
  对话模式用 LangGraph interrupt() 暂停等待用户确认（可审计）；
- 对话模式复用同一张图，checkpointer 为 SqliteSaver：会话（含审批暂停态）
  落盘 SQLite（CHECKPOINT_DB，默认 checkpoints.sqlite），进程重启可续聊；
  依赖缺失或库文件不可写时自动降级为 MemorySaver（仅进程内）。
"""

import logging
import os
import re
import sqlite3
from typing import Annotated, Any, Literal

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from typing_extensions import TypedDict

from src.llm_client import call_llm
from src.tools import (
    get_pod_status,
    query_loki_logs,
    query_prometheus,
    query_runbook,
    restart_pod,
    scale_deployment,
    wiki_search,
)

MAX_ITERATIONS = 8  # 单轮工具调用上限，防止死循环

logger = logging.getLogger(__name__)

CHECKPOINT_DB = os.getenv("CHECKPOINT_DB", "checkpoints.sqlite")  # 会话持久化库文件


def _build_checkpointer():
    """会话 checkpoint 存储：SQLite 文件持久化，失败降级进程内存。

    - check_same_thread=False：Web 线程池与 CLI 共用同一连接并发访问；
    - 依赖缺失 / 库文件不可写时降级 MemorySaver，交互链路仍可用。
    """
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        logger.warning("langgraph-checkpoint-sqlite 未安装，会话降级为进程内存储: %s", exc)
        return MemorySaver()

    try:
        conn = sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)
        saver = SqliteSaver(conn)
        saver.setup()  # 初始化 checkpoint 表结构（幂等）
        logger.info("会话 checkpoint 已启用 SQLite 持久化: %s", CHECKPOINT_DB)
        return saver
    except Exception as exc:  # noqa: BLE001 - 存储不可用不应阻断交互链路
        logger.warning("SQLite checkpoint 初始化失败（%s），降级为进程内存储", exc)
        return MemorySaver()


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    alert: dict
    iteration: int  # 每轮工具调用次数（无 reducer，按轮重置）
    approved: bool  # 非交互式审批标记


# ---------- 交互式审批开关 ----------

_interactive_approval: dict[str, bool] = {"enabled": False}


def set_interactive_approval(enabled: bool) -> None:
    """由对话入口开启：写操作通过 interrupt() 暂停等待人工确认。"""
    _interactive_approval["enabled"] = enabled


# ---------- 动作解析 ----------

_ACTION_RE = re.compile(r"([A-Za-z_]\w*)\s*\((.*)\)")
_KV_RE = re.compile(r"([A-Za-z_]\w*)\s*=\s*('[^']*'|\"[^\"]*\"|[^,)]+)")

# 工具注册表：动作名 -> 可调用对象
_TOOL_REGISTRY = {
    "query_prometheus": query_prometheus,
    "query_loki_logs": query_loki_logs,
    "query_loki": query_loki_logs,  # 兼容 LLM 输出的短名
    "get_pod_status": get_pod_status,
    "query_runbook": query_runbook,
    "wiki_search": wiki_search,
    "restart_pod": restart_pod,
    "scale_deployment": scale_deployment,
}
# 高危写操作：必须经审批门放行
_WRITE_TOOLS = {"restart_pod", "scale_deployment"}


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


def _dispatch(name: str, kwargs: dict, approved: bool) -> str:
    """按注册表调度工具；写操作强制走审批门。"""
    fn = _TOOL_REGISTRY.get(name)
    if fn is None:
        return f"未知动作: {name}"
    if name in _WRITE_TOOLS:
        # 安全护栏：忽略 LLM 自带的 approved 参数，以审批门状态为准
        kwargs["approved"] = approved
    try:
        return fn(**kwargs)
    except TypeError as exc:
        return f"参数错误: {name}: {exc}"


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

    approved = state.get("approved", False)
    if name in _WRITE_TOOLS and not approved and _interactive_approval["enabled"]:
        # 人工审批：图在此暂停，等待对话入口确认后 resume
        approved = bool(interrupt({"action": name, "args": kwargs}))

    result = _dispatch(name, kwargs, approved=approved)
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


def run_agent(alert: dict, mode: str = "offline", approved: bool = False):
    """生成器：逐轮产出诊断步骤（单次告警诊断，无会话记忆）。

    mode 含 "rag" 时在告警上下文中注入 wiki 检索指令，驱动 Agent 使用 LLM_WIKI；
    approved 为 False 时，写操作（重启/扩容）会被审批门拦截。
    """
    set_interactive_approval(False)  # 单次诊断走参数审批，不启用交互暂停
    prompt = f"告警: {alert}"
    if "rag" in mode:
        prompt += "\n请先完成只读诊断，再通过 wiki_search 检索运维知识库获取处置方案。"
    initial_state: AgentState = {
        "messages": [HumanMessage(content=prompt)],
        "alert": alert,
        "iteration": 0,
        "approved": approved,
    }
    for event in _graph.stream(initial_state):
        for node_name, output in event.items():
            if "messages" in output:
                for msg in output["messages"]:
                    yield f"[{node_name}] {msg.content}"

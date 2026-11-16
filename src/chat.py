"""CLI 对话式运维助手（REPL）。

用法：python3 main.py --chat [--auto-approve]

- 多轮上下文：图挂 MemorySaver，按 thread_id 保留会话记忆；
- 写操作审批：LLM 提议重启/扩容时，图内 interrupt() 暂停，由 REPL 询问确认；
- 命令：/help /wiki /check /approve /history /reset /exit
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from src.agent import get_chat_graph, set_interactive_approval

HELP_TEXT = """可用命令：
  /wiki <问题>      直接检索运维知识库（LLM_WIKI）
  /check           外部组件连通性自检（Prometheus/Loki/Kubernetes）
  /approve on|off  写操作自动审批（默认 off，需逐次确认）
  /history         查看当前会话消息数
  /reset           开启新会话（清空上下文）
  /exit            退出
其余输入将作为运维诉求交给诊断 Agent（如：order-service 最近总重启，帮我看看）"""


def _describe_action(value: Any) -> str:
    if isinstance(value, dict):
        action = value.get("action", "写操作")
        args = value.get("args", {})
        args_text = ", ".join(f"{key}={val}" for key, val in args.items())
        return f"{action}({args_text})"
    return str(value)


def _confirm(value: Any, auto_approve: bool) -> bool:
    """写操作人工确认；返回是否批准。"""
    desc = _describe_action(value)
    if auto_approve:
        print(f"[自动审批] 已批准: {desc}")
        return True
    try:
        answer = input(f"[审批] 即将执行 {desc}，是否继续? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in {"y", "yes"}


def _print_event(event: dict) -> None:
    for node, output in event.items():
        if node.startswith("__"):
            continue
        if isinstance(output, dict) and "messages" in output:
            for msg in output["messages"]:
                print(f"[{node}] {msg.content}")


def run_turn(text: str, config: dict, auto_approve: bool) -> None:
    """执行一轮对话：流式输出；遇到审批中断则询问后 resume。"""
    graph = get_chat_graph()
    payload: Any = {
        "messages": [HumanMessage(content=text)],
        "alert": {},
        "iteration": 0,  # 每轮重置工具调用计数
        "approved": False,
    }
    while True:
        pending = None
        for event in graph.stream(payload, config=config, stream_mode="updates"):
            if "__interrupt__" in event:
                pending = event["__interrupt__"][0]
                continue
            _print_event(event)
        if pending is None:
            return
        payload = Command(resume=_confirm(pending.value, auto_approve))


def _handle_command(line: str, config: dict, auto_approve: bool) -> tuple[bool, bool]:
    """处理 / 命令；返回 (是否继续循环, 最新 auto_approve)。"""
    cmd, _, arg = line.partition(" ")
    cmd = cmd.lower()

    if cmd in ("/exit", "/quit"):
        print("再见。")
        return False, auto_approve
    if cmd == "/help":
        print(HELP_TEXT)
        return True, auto_approve
    if cmd == "/wiki":
        if not arg.strip():
            print("用法: /wiki <问题>")
            return True, auto_approve
        from src.llm_wiki import get_wiki

        wiki = get_wiki()
        print(f"[wiki:{wiki.mode}] {wiki.search(arg.strip(), top_k=2)}")
        return True, auto_approve
    if cmd == "/check":
        from src.tools import connectivity_check

        for line_text in connectivity_check():
            print(line_text)
        return True, auto_approve
    if cmd == "/approve":
        value = arg.strip().lower()
        if value in ("on", "off"):
            auto_approve = value == "on"
            print(f"自动审批已{'开启' if auto_approve else '关闭'}")
        else:
            print("用法: /approve on|off")
        return True, auto_approve
    if cmd == "/history":
        state = get_chat_graph().get_state(config)
        count = len(state.values.get("messages", [])) if state.values else 0
        print(f"当前会话消息数: {count}")
        return True, auto_approve
    if cmd == "/reset":
        config["configurable"]["thread_id"] = f"chat-{uuid.uuid4().hex[:8]}"
        print(f"已开启新会话: {config['configurable']['thread_id']}")
        return True, auto_approve

    print(f"未知命令: {cmd}（输入 /help 查看帮助）")
    return True, auto_approve


def main() -> int:
    auto_approve = any(flag in sys.argv for flag in ("--auto-approve", "--yes"))
    set_interactive_approval(True)  # 对话模式：写操作走 interrupt 人工确认

    try:
        from src.tools import get_tools_mode

        tools_mode = get_tools_mode()
    except Exception:  # noqa: BLE001 - 工具层异常不应阻断对话入口
        tools_mode = "unknown"

    config = {"configurable": {"thread_id": f"chat-{uuid.uuid4().hex[:8]}"}}

    print("=" * 48)
    print("运维智能体 · 对话模式（/help 查看命令，Ctrl+C 退出）")
    print(f"工具模式: {tools_mode} | 自动审批: {'开' if auto_approve else '关'}")
    print("=" * 48)

    while True:
        try:
            line = input("\n运维> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return 0

        if not line:
            continue
        if line.startswith("/"):
            keep, auto_approve = _handle_command(line, config, auto_approve)
            if not keep:
                return 0
            continue

        try:
            run_turn(line, config, auto_approve)
        except KeyboardInterrupt:
            print("\n[中断] 本轮对话已取消")
        except Exception as exc:  # noqa: BLE001 - REPL 不应因单轮失败退出
            print(f"[错误] {exc}")

"""Ops AI Demo 入口。

用法：
- python3 main.py           # 运行全部演示
- python3 main.py --check   # 检测 Prometheus/Loki/Kubernetes 连通性
- python3 main.py --chat    # 进入 CLI 对话模式（多轮记忆 + 写操作人工审批）
- python3 main.py --web     # 启动 Web 对话界面（http://127.0.0.1:8765）

自动降级策略：
- 缺 langgraph/langchain-openai -> 跳过 Agent 相关演示，只跑 RAG；
- 缺 llama-index -> 跳过 RAG 演示，只跑 Agent；
- 两者都缺 -> 提示用户至少安装一项。
所有演示彼此隔离，单个失败不影响其余。
"""

import sys


def demo_agent():
    print("=" * 40)
    print("Demo 1: Agent 诊断（LangGraph 状态机）")
    print("=" * 40)
    # 延迟导入：没装 langgraph 时，只有跑到这里才报错
    from src.agent import run_agent

    alert = {
        "alert_name": "HighCPU",
        "service": "order-service",
        "message": "CPU usage > 90% for 5 minutes",
    }
    for step in run_agent(alert, mode="offline"):
        print(step)


def demo_rag():
    print("\n" + "=" * 40)
    print("Demo 2: RAG 知识库问答")
    print("=" * 40)
    # 延迟导入：没装 llama-index 时，只有跑到这里才报错
    from src.rag_engine import create_rag_engine

    engine = create_rag_engine("knowledge_base")
    for question in ["CPU 告警怎么排查？", "Pod CrashLoopBackOff 怎么办？"]:
        print(f"\nQ: {question}")
        response = engine.query(question)
        print(f"A: {response}")


def demo_agent_with_rag():
    print("\n" + "=" * 40)
    print("Demo 3: Agent 调用 LLM_WIKI 检索 Runbook")
    print("=" * 40)
    # 这个演示同时依赖 langgraph 和 llama-index
    from src.agent import run_agent

    alert = {
        "alert_name": "HighCPU",
        "service": "order-service",
        "message": "CPU usage > 90% for 5 minutes",
    }
    # mode 含 "rag"：Agent 在定位到根因后调用 wiki_search 检索处置方案
    for step in run_agent(alert, mode="offline_with_rag"):
        print(step)


def demo_wiki():
    print("\n" + "=" * 40)
    print("Demo 4: LLM_WIKI 知识库检索")
    print("=" * 40)
    from src.llm_wiki import get_wiki

    wiki = get_wiki()
    print(f"检索模式: {wiki.mode}")
    for question in ["CPU 告警怎么排查？", "Pod CrashLoopBackOff 怎么办？"]:
        print(f"\nQ: {question}")
        print(wiki.search(question, top_k=1))


def _safe(func, name: str, need: str):
    """统一封装：捕获依赖缺失与运行异常，打印提示但不中断整体流程。"""
    print(f"\n>>> 开始：{name}")
    try:
        func()
        print(f"\n<<< 完成：{name}")
        return True
    except ImportError as exc:
        print(f"[跳过] {name} 缺少依赖：{exc}")
        print(f"       如需运行，请安装：{need}")
        return False
    except Exception as exc:  # 运行期异常也不阻断其它演示
        print(f"[失败] {name} 运行出错：{exc}")
        return False


def main():
    ok_agent = _safe(demo_agent, "Demo 1 Agent 诊断", "pip install langgraph langchain-openai")
    ok_rag = _safe(demo_rag, "Demo 2 RAG 问答", "pip install llama-index llama-index-llms-openai llama-index-embeddings-openai")
    ok_both = _safe(demo_agent_with_rag, "Demo 3 Agent+LLM_WIKI", "pip install langgraph langchain-openai llama-index")
    ok_wiki = _safe(demo_wiki, "Demo 4 LLM_WIKI 检索", "pip install llama-index")

    print("\n" + "=" * 40)
    print("运行汇总")
    print("=" * 40)
    results = {
        "Demo 1 Agent 诊断": ok_agent,
        "Demo 2 RAG 问答": ok_rag,
        "Demo 3 Agent+LLM_WIKI": ok_both,
        "Demo 4 LLM_WIKI 检索": ok_wiki,
    }
    for name, ok in results.items():
        print(f"  [{'OK' if ok else 'SKIP/FAIL'}] {name}")

    if not any(results.values()):
        print("\n提示：所有演示都未运行成功。请至少安装一项依赖：")
        print("  仅跑 Agent：pip install langgraph langchain-openai")
        print("  仅跑 RAG  ：pip install llama-index llama-index-llms-openai llama-index-embeddings-openai")


def check_connectivity():
    """外部组件连通性自检：python3 main.py --check"""
    print("=" * 40)
    print("组件连通性自检（config/ops_config.yaml）")
    print("=" * 40)
    from src.tools import connectivity_check

    for line in connectivity_check():
        print(line)


if __name__ == "__main__":
    if "--chat" in sys.argv:
        from src.chat import main as chat_main

        sys.exit(chat_main())
    if "--web" in sys.argv:
        from src.web_server import main as web_main

        sys.exit(web_main())
    if "--check" in sys.argv:
        check_connectivity()
    else:
        main()
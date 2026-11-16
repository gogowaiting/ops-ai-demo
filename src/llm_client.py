"""统一模型客户端：OpenAI / 本地 Ollama / 离线占位。"""

from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from src.config_loader import resolve_model_config, resolve_ops_config

_config = resolve_model_config()

AGENT_SYSTEM_PROMPT = """You are an operations diagnosis agent.
Diagnose the alert by gathering evidence before proposing remediation.

Return exactly one action in this form:
ACTION: tool_name(key='value', ...)

Available tools:
- check_environment()
- cluster_snapshot(service='...')
- query_prometheus(query='...')
- query_loki_logs(service='...')
- get_pod_status(service='...')
- wiki_search(query='...', service='...')
- query_runbook(topic='...')
- restart_pod(service='...')
- scale_deployment(service='...', replicas=3)

Use wiki_search when the user asks for runbooks or remediation guidance.
Maintain multi-turn context and combine metrics, logs, and Kubernetes evidence.
When diagnosis is complete, return one concise conclusion instead of ACTION.
Do not wrap the action in Markdown or include multiple actions.
"""


def build_agent_system_prompt(skill_context: Mapping[str, Any] | None = None) -> str:
    ops = resolve_ops_config()
    context = (
        "Current operations environment:\n"
        f"- environment: {ops['environment_name']}\n"
        f"- Kubernetes context: {ops['k8s_context'] or '(default)'}\n"
        f"- Kubernetes namespace: {ops['k8s_namespace']}\n"
        "- Prometheus and Loki are external data sources for this environment.\n"
    )
    skill_text = ""
    active_skills = (skill_context or {}).get("skills", [])
    if active_skills:
        skill_text = "\nActive skills (follow these procedures):\n"
        for skill in active_skills:
            tools = ", ".join(skill.get("allowed_tools", []))
            skill_text += (
                f"\n### {skill.get('id')}@{skill.get('version')} "
                f"(risk={skill.get('risk')})\n"
                f"Allowed tools: {tools}\n"
                f"{skill.get('body', '')}\n"
            )
    return f"{AGENT_SYSTEM_PROMPT}\n{context}{skill_text}"


def _is_online_provider(provider: str, api_key: str) -> bool:
    return provider == "ollama" or (provider == "openai" and bool(api_key))


@lru_cache(maxsize=8)
def _build_chat_model(
    model: str | None,
    temperature: float | None,
    timeout: int | None,
    max_retries: int | None,
) -> BaseChatModel:
    llm_provider = _config["llm_provider"]
    llm_model = model or _config["llm_model"]
    llm_base_url = _config["llm_base_url"]
    llm_api_key = _config["llm_api_key"]
    llm_temperature = temperature if temperature is not None else _config["llm_temperature"]
    llm_timeout = timeout if timeout is not None else _config["llm_timeout"]
    llm_max_retries = max_retries if max_retries is not None else _config["llm_max_retries"]

    if _is_online_provider(llm_provider, llm_api_key):
        from langchain_openai import ChatOpenAI

        kwargs = {
            "model": llm_model,
            "api_key": llm_api_key or "ollama",
            "temperature": llm_temperature,
            "timeout": llm_timeout,
            "max_retries": llm_max_retries,
        }
        if llm_base_url:
            kwargs["base_url"] = llm_base_url
        return ChatOpenAI(**kwargs)

    return _OfflineChatModel(model_name=_config["offline_fallback_model"])


def get_chat_model(
    *,
    model: str | None = None,
    temperature: float | None = None,
    timeout: int | None = None,
    max_retries: int | None = None,
) -> BaseChatModel:
    """对外工厂接口：复用缓存的模型实例，避免重复构建客户端。"""
    return _build_chat_model(model, temperature, timeout, max_retries)


def call_llm(state: Mapping[str, Any]) -> dict:
    """LangGraph 节点：接收 state，返回新消息。"""
    messages: list[BaseMessage] = list(state.get("messages", []))
    model = get_chat_model()
    response = model.invoke(
        [
            SystemMessage(
                content=build_agent_system_prompt(state.get("skill_context"))
            ),
            *messages,
        ]
    )
    return {"messages": [response]}


def get_llm(allow_offline: bool = True):
    """返回 LlamaIndex LLM：OpenAI / Ollama，或按 allow_offline 降级。"""
    llm_provider = _config["llm_provider"]
    llm_api_key = _config["llm_api_key"]
    llm_model = _config["llm_model"]
    llm_base_url = _config["llm_base_url"]

    if not _is_online_provider(llm_provider, llm_api_key):
        if allow_offline:
            # 离线降级：返回本地模型，保证 RAG 问答链路可用
            from src.offline_models import OfflineChatLLM

            return OfflineChatLLM()
        raise RuntimeError("请配置 OpenAI API Key 或 Ollama 后再接入真实模型")

    from llama_index.llms.openai import OpenAI

    kwargs = {"model": llm_model, "api_key": llm_api_key or "ollama"}
    if llm_base_url:
        kwargs["api_base"] = llm_base_url
    return OpenAI(**kwargs)


def get_embedding(allow_offline: bool = True):
    """返回 LlamaIndex Embedding：OpenAI / Ollama，或按 allow_offline 降级。"""
    embed_provider = _config["embed_provider"]
    embed_api_key = _config["embed_api_key"]
    embed_model = _config["embed_model"]
    embed_base_url = _config["embed_base_url"]

    if not _is_online_provider(embed_provider, embed_api_key):
        if allow_offline:
            # 离线降级：本地哈希向量，索引与检索均可正常持久化/加载
            from src.offline_models import OfflineEmbedding

            return OfflineEmbedding()
        raise RuntimeError("请配置 Embedding API Key 或 Ollama 后再接入真实模型")

    from llama_index.embeddings.openai import OpenAIEmbedding

    kwargs = {"model_name": embed_model, "api_key": embed_api_key or "ollama"}
    if embed_base_url:
        kwargs["api_base"] = embed_base_url
    return OpenAIEmbedding(**kwargs)


class _OfflineChatModel(BaseChatModel):
    model_name: str = "offline-agent"

    @property
    def _llm_type(self) -> str:
        return "offline"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager=None,
        **kwargs,
    ) -> ChatResult:
        # 边界处理：空消息列表时回退为空串，避免 IndexError
        # 阶段推进依据最后一条消息；模式判断（如是否启用 Wiki）依据完整历史
        conversation = [m for m in messages if not isinstance(m, SystemMessage)]
        history = "\n".join(str(m.content) for m in conversation)
        last = str(conversation[-1].content) if conversation else ""
        text = _offline_decision(history, last)
        return self._create_result(text)

    # 异步路径交由 BaseChatModel 默认实现（在线程池中调用 _generate），
    # 不再手动转发异步 run_manager，避免回调类型不匹配。

    def _create_result(self, text: str) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


def _offline_decision(history: str, last: str) -> str:
    """离线决策：确定性的诊断推进流程（指标 -> 日志 -> Pod -> [Wiki] -> 结论）。

    last 为最后一条消息：据此判断当前阶段，保证每阶段只推进一次；
    history 为完整对话历史：据此判断运行模式（是否启用 Wiki 检索）。
    """
    last_lower = last.lower()
    history_lower = history.lower()
    # 工具降级时工具层会加 “[降级] ...\n” 前缀，剥离后再做阶段识别
    if last_lower.startswith("[降级]"):
        last_lower = last_lower.split("\n", 1)[-1]
    # 审批未通过的写操作不再重试
    if "未批准" in last_lower or "已取消" in last_lower:
        return "DONE: 写操作已按审批结果终止"
    # 阶段推进：以工具结果首行前缀判断当前进度（避免结果正文中的关键词误判）
    if last_lower.startswith("wiki 检索结果"):
        return "DONE: 依据知识库处置方案重启 Pod 并扩容，回查指标确认错误率回落"
    if last_lower.startswith("环境检查"):
        return "DONE: 环境检查完成，请结合上方连通性结果继续处理"
    if last_lower.startswith("集群环境快照"):
        if "oomkilled" in last_lower or "java.lang.outofmemoryerror" in last_lower:
            return "DONE: 指标、日志和 Pod 状态共同指向 OOM，建议限制内存并处理异常进程"
        return "DONE: 集群指标、日志和 Pod 状态联合检查完成"
    if last_lower.startswith("prometheus 查询结果"):
        return "ACTION: query_loki_logs(service='order-service')"
    if last_lower.startswith("loki 日志"):
        return "ACTION: get_pod_status(service='order-service')"
    if last_lower.startswith("pod 状态"):
        if "wiki" in history_lower or "rag" in history_lower:
            # RAG 模式：定位到 OOM 后检索知识库获取处置方案
            return "ACTION: wiki_search(query='OOM CrashLoopBackOff 处理')"
        if "oomkilled" in last_lower or "crashloopbackoff" in last_lower:
            return "DONE: 根因: OOM 导致 CrashLoopBackOff，建议重启 Pod 并临时扩容"
        return "DONE: 未发现明显异常，建议持续观察"
    # 首轮输入分发（支持中英文关键词，适配对话模式）
    if "巡检" in last_lower or "集群快照" in last_lower or "cluster snapshot" in last_lower:
        return "ACTION: cluster_snapshot(service='')"
    if "连通性" in last_lower or "组件自检" in last_lower or "connectivity" in last_lower:
        return "ACTION: check_environment()"
    if "cpu" in last_lower or "error" in last_lower or "指标" in last_lower:
        return "ACTION: query_prometheus(query='cpu and error rate')"
    if "log" in last_lower or "日志" in last_lower:
        return "ACTION: query_loki_logs(service='order-service')"
    if "pod" in last_lower or "状态" in last_lower:
        return "ACTION: get_pod_status(service='order-service')"
    if "重启" in last_lower or "restart" in last_lower:
        return "ACTION: restart_pod(service='order-service')"
    if "扩容" in last_lower or "scale" in last_lower:
        return "ACTION: scale_deployment(service='order-service', replicas=3)"
    if "知识库" in last_lower or "runbook" in last_lower or "手册" in last_lower or "排查" in last_lower:
        return "ACTION: wiki_search(query='CPU 告警排查')"
    return "DONE: diagnosis completed"

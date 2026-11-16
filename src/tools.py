"""运维工具：Prometheus / Loki / K8s / Runbook / Wiki。

数据源由 config/ops_config.yaml 统一配置，tools.mode 控制行为：
- real：必须连接真实组件，失败直接报错（生产环境）；
- mock：始终使用内置示例数据（离线演示 / 回归测试）；
- auto（默认）：优先真实组件，连接失败自动降级示例数据并标注 [降级]。
"""

from __future__ import annotations

import logging
from functools import lru_cache

from src.config_loader import resolve_ops_config
from src.ops_clients import ComponentError, K8sClient, LokiClient, PrometheusClient

logger = logging.getLogger(__name__)

# 默认 CPU 使用率查询（node_exporter 指标）
DEFAULT_CPU_QUERY = (
    '100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
)


# ---------- 配置与客户端 ----------

@lru_cache(maxsize=1)
def _ops_config() -> dict:
    return resolve_ops_config()


def get_tools_mode() -> str:
    """当前工具模式：real / mock / auto。"""
    return _ops_config()["tools_mode"]


def _degrade(name: str, exc: Exception) -> str:
    """real 模式抛出；auto 模式返回降级提示前缀。"""
    if get_tools_mode() == "real":
        raise exc
    # 日志只记简要原因，完整错误在返回文本中，避免 REPL 输出重复冗长
    logger.warning("%s 不可用，已降级为示例数据", name)
    return f"[降级] {name} 不可用（{exc}），以下为示例数据。\n"


@lru_cache(maxsize=1)
def _prometheus() -> PrometheusClient | None:
    if get_tools_mode() == "mock":
        return None
    cfg = _ops_config()
    try:
        return PrometheusClient(cfg["prometheus_base_url"], cfg["prometheus_timeout"])
    except ComponentError as exc:
        if get_tools_mode() == "real":
            raise
        logger.warning("Prometheus 客户端初始化失败: %s", exc)
        return None


@lru_cache(maxsize=1)
def _loki() -> LokiClient | None:
    if get_tools_mode() == "mock":
        return None
    cfg = _ops_config()
    try:
        return LokiClient(
            cfg["loki_base_url"],
            cfg["loki_timeout"],
            cfg["loki_limit"],
            cfg["loki_lookback_minutes"],
        )
    except ComponentError as exc:
        if get_tools_mode() == "real":
            raise
        logger.warning("Loki 客户端初始化失败: %s", exc)
        return None


@lru_cache(maxsize=1)
def _k8s() -> K8sClient | None:
    if get_tools_mode() == "mock":
        return None
    cfg = _ops_config()
    try:
        return K8sClient(
            cfg["k8s_kubeconfig"],
            cfg["k8s_context"],
            cfg["k8s_namespace"],
            cfg["k8s_timeout"],
        )
    except ComponentError as exc:
        if get_tools_mode() == "real":
            raise
        logger.warning("Kubernetes 客户端初始化失败: %s", exc)
        return None


# ---------- 示例数据（mock / 降级） ----------

def _mock_prometheus() -> str:
    return (
        "Prometheus 查询结果：\n"
        "  service=order-service, cpu_usage=94%, error_rate=12%, "
        "pod=order-service-7b4d5f6c8-x9k2m"
    )


def _mock_loki() -> str:
    return (
        "Loki 日志（最近 10 条）：\n"
        "  [ERROR] order-service: java.lang.OutOfMemoryError: Java heap space\n"
        "  [WARN]  order-service: HikariPool-1 - Connection is not available\n"
        "  [INFO]  order-service: Retrying database connection attempt 3/5"
    )


def _mock_pod_status() -> str:
    return (
        "Pod 状态：\n"
        "  order-service-7b4d5f6c8-x9k2m  CrashLoopBackOff  5/5  OOMKilled\n"
        "  order-service-7b4d5f6c8-k3m7n  Running           0/5"
    )


# ---------- 工具实现 ----------

def query_prometheus(query: str = "") -> str:
    """查询指标：真实 Prometheus 优先，失败时按模式降级。"""
    expr = query or DEFAULT_CPU_QUERY
    client = _prometheus()
    if client is None:
        return _mock_prometheus()
    try:
        result = client.query(expr)
    except ComponentError as exc:
        return _degrade("Prometheus", exc) + _mock_prometheus()

    if not result:
        return f"Prometheus 查询结果（query='{expr}'）：窗口内无数据"
    lines = [f"Prometheus 查询结果（query='{expr}'）："]
    for series in result[:10]:
        metric = series.get("metric", {})
        labels = ",".join(f"{k}={v}" for k, v in metric.items() if k != "__name__") or "value"
        value = series.get("value", ["", ""])[1]
        lines.append(f"  {labels} => {value}")
    return "\n".join(lines)


def query_loki_logs(service: str = "") -> str:
    """查询日志：真实 Loki 优先，失败时按模式降级。"""
    selector = f'{{app="{service}"}}' if service else '{app=~".+"}'
    client = _loki()
    if client is None:
        return _mock_loki()
    try:
        lines = client.query_range(selector)
    except ComponentError as exc:
        return _degrade("Loki", exc) + _mock_loki()

    if not lines:
        return f"Loki 日志（selector={selector}）：时间窗口内无日志"
    head = f"Loki 日志（selector={selector}，最近 {min(len(lines), client.limit)} 条）："
    return head + "\n" + "\n".join(f"  {line}" for line in lines[: client.limit])


def get_pod_status(service: str = "") -> str:
    """查询 Pod 状态：真实集群优先，失败时按模式降级。"""
    client = _k8s()
    if client is None:
        return _mock_pod_status()
    try:
        rows = client.pod_rows(service)
    except ComponentError as exc:
        return _degrade("Kubernetes", exc) + _mock_pod_status()

    if not rows:
        return f"Pod 状态（{client.endpoint}）：未发现匹配的 Pod"
    lines = [f"Pod 状态（{client.endpoint}）："]
    for row in rows:
        lines.append(
            f"  {row['name']}  {row['status']}  {row['ready']}  restarts={row['restarts']}"
        )
    return "\n".join(lines)


def restart_pod(service: str = "", approved: bool = False) -> str:
    if not approved:
        return "重启未批准，请确认后再试。"
    client = _k8s()
    if client is None:
        return f"已重启 {service} 的异常 Pod，等待 60s 观察。"
    try:
        result = client.restart(service)
    except ComponentError as exc:
        return _degrade("Kubernetes", exc) + f"已重启 {service} 的异常 Pod（示例）"
    return result


def scale_deployment(service: str = "", replicas: int = 1, approved: bool = False) -> str:
    if not approved:
        return "扩容未批准，请确认后再试。"
    client = _k8s()
    if client is None:
        return f"已将 {service} 扩容至 {replicas} 副本。"
    try:
        result = client.scale(service, replicas)
    except ComponentError as exc:
        return _degrade("Kubernetes", exc) + f"已将 {service} 扩容至 {replicas} 副本（示例）"
    return result


def query_runbook(topic: str = "") -> str:
    return (
        f"Runbook 查询结果（topic='{topic}'）：\n"
        "1. 检查 Prometheus 中该服务的 CPU 和错误率；\n"
        "2. 查看 Loki 日志确认是否有 OOM；\n"
        "3. 检查 Pod 状态是否为 CrashLoopBackOff；\n"
        "4. 若确认 OOM，重启 Pod 并临时扩容；\n"
        "5. 后续排查连接池配置。"
    )


def wiki_search(query: str = "", top_k: int = 0, service: str = "") -> str:
    """检索运维知识库（LLM_WIKI），可按文档 frontmatter 的 service 过滤。

    RAG 向量检索优先；llama-index 未安装或索引不可用时自动降级为关键词检索。
    惰性导入：保证未安装 llama-index 时本模块仍可被 Agent 使用。
    """
    from src.llm_wiki import get_wiki

    return get_wiki().search(query, top_k or None, service or None)


# ---------- 连通性自检 ----------

def connectivity_check() -> list[str]:
    """逐项检测外部组件连通性（供 `main.py --check` 使用），不抛异常。"""
    cfg = _ops_config()
    lines = [f"工具模式: {get_tools_mode()}"]

    # Prometheus
    try:
        detail = PrometheusClient(cfg["prometheus_base_url"], cfg["prometheus_timeout"]).check()
        lines.append(f"  [OK]   Prometheus  {cfg['prometheus_base_url']}  {detail}")
    except ComponentError as exc:
        lines.append(f"  [FAIL] Prometheus  {cfg['prometheus_base_url']}  {exc}")

    # Loki
    try:
        detail = LokiClient(cfg["loki_base_url"], cfg["loki_timeout"]).check()
        lines.append(f"  [OK]   Loki        {cfg['loki_base_url']}  {detail}")
    except ComponentError as exc:
        lines.append(f"  [FAIL] Loki        {cfg['loki_base_url']}  {exc}")

    # Kubernetes
    try:
        client = K8sClient(
            cfg["k8s_kubeconfig"],
            cfg["k8s_context"],
            cfg["k8s_namespace"],
            cfg["k8s_timeout"],
        )
        detail = client.check()
        rows = client.pod_rows()
        lines.append(f"  [OK]   Kubernetes  {client.endpoint}  {detail}（Pod {len(rows)} 个）")
    except ComponentError as exc:
        lines.append(f"  [FAIL] Kubernetes  context={cfg['k8s_context'] or '(默认)'}  {exc}")

    return lines

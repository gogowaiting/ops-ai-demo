"""外部组件客户端：Prometheus / Loki / Kubernetes（真实实现）。

接入参数统一来自 config/ops_config.yaml（经 src.config_loader.resolve_ops_config）；
- Prometheus / Loki 走 HTTP API（requests）；
- Kubernetes 走 kubectl CLI（-o json 解析），无需额外 Python 依赖，
  天然复用 Docker Desktop 本地集群的 kubeconfig/context。
所有失败统一抛 ComponentError，由 tools.py 按模式决定报错或降级。
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)


class ComponentError(RuntimeError):
    """外部组件调用失败（网络、鉴权、返回异常等）。"""


class PrometheusClient:
    """Prometheus HTTP API 客户端。"""

    def __init__(self, base_url: str, timeout: float = 10):
        if not base_url:
            raise ComponentError("未配置 prometheus.base_url")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def query(self, expr: str) -> list[dict[str, Any]]:
        """瞬时查询，返回结果序列列表。"""
        try:
            resp = requests.get(
                f"{self.base_url}/api/v1/query",
                params={"query": expr},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise ComponentError(f"Prometheus 请求失败: {exc}") from exc

        if payload.get("status") != "success":
            raise ComponentError(f"Prometheus 返回异常: {payload.get('error', payload)}")
        return payload.get("data", {}).get("result", [])

    def check(self) -> str:
        series = self.query("up")
        return f"连接正常（up 序列 {len(series)} 条）"


class LokiClient:
    """Loki HTTP API 客户端（query_range）。"""

    def __init__(self, base_url: str, timeout: float = 15, limit: int = 50, lookback_minutes: int = 10):
        if not base_url:
            raise ComponentError("未配置 loki.base_url")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.limit = limit
        self.lookback_minutes = lookback_minutes

    def query_range(self, query: str) -> list[str]:
        """按时间窗口查询日志，返回按时间倒序的日志行。"""
        end = datetime.now(UTC)
        start = end - timedelta(minutes=self.lookback_minutes)
        params = {
            "query": query,
            "limit": str(self.limit),
            "start": str(int(start.timestamp() * 1e9)),
            "end": str(int(end.timestamp() * 1e9)),
            "direction": "backward",
        }
        try:
            resp = requests.get(
                f"{self.base_url}/loki/api/v1/query_range",
                params=params,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise ComponentError(f"Loki 请求失败: {exc}") from exc

        if payload.get("status") != "success":
            raise ComponentError(f"Loki 返回异常: {payload.get('error', payload)}")

        lines: list[str] = []
        for stream in payload.get("data", {}).get("result", []):
            for _, line in stream.get("values", []):
                lines.append(line)
        return lines

    def check(self) -> str:
        try:
            resp = requests.get(f"{self.base_url}/ready", timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ComponentError(f"Loki 健康检查失败: {exc}") from exc
        return "连接正常（/ready）"


class K8sClient:
    """Kubernetes 客户端：基于 kubectl CLI，复用本地 kubeconfig/context。"""

    def __init__(
        self,
        kubeconfig: str = "",
        context: str = "",
        namespace: str = "default",
        timeout: float = 15,
    ):
        if shutil.which("kubectl") is None:
            raise ComponentError("未找到 kubectl 命令，请安装后重试")
        self.kubeconfig = kubeconfig
        self.context = context
        self.namespace = namespace or "default"
        self.timeout = timeout

    @property
    def endpoint(self) -> str:
        ctx = self.context or "(默认上下文)"
        return f"context={ctx}, namespace={self.namespace}"

    def _run(self, *args: str) -> str:
        cmd = ["kubectl"]
        if self.kubeconfig:
            cmd += ["--kubeconfig", self.kubeconfig]
        if self.context:
            cmd += ["--context", self.context]
        cmd += ["-n", self.namespace, *args, f"--request-timeout={int(self.timeout)}s"]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout + 5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ComponentError(f"kubectl 执行失败: {exc}") from exc
        if proc.returncode != 0:
            raise ComponentError(f"kubectl {' '.join(args)} 失败: {proc.stderr.strip()[:200]}")
        return proc.stdout

    def _get_json(self, *args: str) -> dict[str, Any]:
        try:
            return json.loads(self._run(*args))
        except ValueError as exc:
            raise ComponentError(f"kubectl 输出解析失败: {exc}") from exc

    def list_pods(self, service: str = "") -> list[dict[str, Any]]:
        """按 app 标签筛选 Pod；标签缺失时回退为名称匹配。"""
        if service:
            items = self._get_json("get", "pods", "-l", f"app={service}", "-o", "json").get("items", [])
            if items:
                return items
            all_pods = self._get_json("get", "pods", "-o", "json").get("items", [])
            return [p for p in all_pods if service in p.get("metadata", {}).get("name", "")]
        return self._get_json("get", "pods", "-o", "json").get("items", [])

    def pod_rows(self, service: str = "") -> list[dict[str, Any]]:
        """归一化 Pod 状态字段，供工具层格式化输出。"""
        rows: list[dict[str, Any]] = []
        for pod in self.list_pods(service):
            meta = pod.get("metadata", {})
            status = pod.get("status", {})
            containers = status.get("containerStatuses") or []
            ready = sum(1 for c in containers if c.get("ready"))
            restarts = sum(int(c.get("restartCount", 0)) for c in containers)

            reason = status.get("reason") or ""
            if not reason:
                waiting = [
                    (c.get("state") or {}).get("waiting", {}).get("reason") for c in containers
                ]
                waiting = [w for w in waiting if w]
                reason = waiting[0] if waiting else status.get("phase", "Unknown")

            rows.append(
                {
                    "name": meta.get("name", ""),
                    "ready": f"{ready}/{len(containers)}",
                    "status": reason,
                    "restarts": restarts,
                    "created": meta.get("creationTimestamp", ""),
                }
            )
        return rows

    def restart(self, service: str) -> str:
        """滚动重启 Deployment（等价于 kubectl rollout restart）。"""
        self._run("rollout", "restart", f"deployment/{service}")
        return f"已触发 {service} 滚动重启，等待 60s 观察"

    def scale(self, service: str, replicas: int) -> str:
        self._run("scale", f"deployment/{service}", f"--replicas={replicas}")
        return f"已将 {service} 扩容至 {replicas} 副本"

    def check(self) -> str:
        self._get_json("get", "ns", "-o", "json")
        return "集群连接正常"

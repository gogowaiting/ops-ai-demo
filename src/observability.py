"""Prometheus 指标与请求追踪 ID。"""

from __future__ import annotations

import os
import time
from uuid import uuid4

from prometheus_client import CollectorRegistry, Counter, Histogram, Gauge, REGISTRY


def new_trace_id() -> str:
    if existing := os.getenv("OPS_TRACE_ID"):
        return existing
    return uuid4().hex


class RuntimeMetrics:
    def __init__(self, registry: CollectorRegistry | None = None):
        self.registry = registry or REGISTRY
        self.http_requests = Counter(
            "opsai_http_requests_total",
            "HTTP requests processed by Ops AI",
            ["method", "status"],
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "opsai_http_request_duration_seconds",
            "HTTP request duration",
            registry=self.registry,
        )
        self.agent_turns = Counter(
            "opsai_agent_turns_total",
            "Agent turns completed",
            ["entrypoint", "status"],
            registry=self.registry,
        )
        self.agent_nodes = Counter(
            "opsai_agent_node_events_total",
            "Agent node output events",
            ["node"],
            registry=self.registry,
        )
        self.tool_calls = Counter(
            "opsai_tool_calls_total",
            "Operations tool calls",
            ["tool", "status"],
            registry=self.registry,
        )
        self.tool_duration = Histogram(
            "opsai_tool_call_duration_seconds",
            "Operations tool call duration",
            ["tool"],
            registry=self.registry,
        )
        self.alert_jobs = Counter(
            "opsai_alert_jobs_total",
            "Alert diagnostic jobs by lifecycle status",
            ["status"],
            registry=self.registry,
        )
        self.pending_alert_jobs = Gauge(
            "opsai_alert_jobs_pending",
            "Alert diagnostic jobs currently running or queued",
            registry=self.registry,
        )

    def observe_http(self, method: str, status_code: int, seconds: float) -> None:
        self.http_requests.labels(
            method=method, status=str(status_code)
        ).inc()
        self.http_duration.observe(seconds)

    def observe_agent_node(self, node: str) -> None:
        self.agent_nodes.labels(node=node).inc()

    def observe_agent_turn(
        self, entrypoint: str, status: str, seconds: float
    ) -> None:
        self.agent_turns.labels(entrypoint=entrypoint, status=status).inc()

    def observe_tool_call(
        self, tool: str, status: str, seconds: float
    ) -> None:
        self.tool_calls.labels(tool=tool, status=status).inc()
        self.tool_duration.labels(tool=tool).observe(seconds)

    def observe_alert_job(
        self,
        status: str,
        *,
        pending_delta: int = 0,
    ) -> None:
        self.alert_jobs.labels(status=status).inc()
        if pending_delta:
            self.pending_alert_jobs.inc(pending_delta)


metrics = RuntimeMetrics()

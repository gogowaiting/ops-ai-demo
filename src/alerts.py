"""Alertmanager 告警入口与 Agent 告警格式归一化。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

MAX_ALERTS_PER_REQUEST = 50


class AlertValidationError(ValueError):
    """告警 payload 不符合 Alertmanager 或简化告警格式。"""


def _string_mapping(value: Any, field: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise AlertValidationError(f"{field} 必须是对象")
    return {
        str(key): "" if item is None else str(item)
        for key, item in value.items()
    }


def _first(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def normalize_alert(payload: Mapping[str, Any]) -> dict[str, Any]:
    """把单条 Alertmanager 告警或简化告警转换成 Agent 输入。"""
    if not isinstance(payload, Mapping):
        raise AlertValidationError("告警必须是对象")

    labels = _string_mapping(payload.get("labels"), "labels")
    annotations = _string_mapping(payload.get("annotations"), "annotations")
    alert_id = _first(
        payload.get("fingerprint"),
        payload.get("alert_id"),
        payload.get("id"),
        f"alert-{uuid4().hex[:12]}",
    )
    alert_name = _first(
        payload.get("alert_name"),
        payload.get("name"),
        labels.get("alertname"),
        "UnnamedAlert",
    )
    service = _first(
        payload.get("service"),
        labels.get("service"),
        labels.get("app"),
        labels.get("job"),
    )
    message = _first(
        payload.get("message"),
        annotations.get("description"),
        annotations.get("summary"),
        payload.get("summary"),
        alert_name,
    )
    status = _first(payload.get("status"), "firing").lower()

    return {
        "alert_id": alert_id,
        "alert_name": alert_name,
        "service": service,
        "message": message,
        "severity": _first(payload.get("severity"), labels.get("severity")),
        "status": status,
        "labels": labels,
        "annotations": annotations,
        "starts_at": _first(payload.get("startsAt"), payload.get("starts_at")),
        "ends_at": _first(payload.get("endsAt"), payload.get("ends_at")),
        "generator_url": _first(payload.get("generatorURL"), payload.get("generator_url")),
        "source": _first(payload.get("source"), "alertmanager"),
    }


def normalize_alert_payload(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """解析 Alertmanager webhook 或单条告警 payload。"""
    if not isinstance(payload, Mapping):
        raise AlertValidationError("payload 必须是对象")

    if "alerts" not in payload:
        return [normalize_alert(payload)]

    raw_alerts = payload.get("alerts")
    if not isinstance(raw_alerts, list) or not raw_alerts:
        raise AlertValidationError("alerts 必须是非空数组")
    if len(raw_alerts) > MAX_ALERTS_PER_REQUEST:
        raise AlertValidationError(
            f"单次最多接收 {MAX_ALERTS_PER_REQUEST} 条告警"
        )

    common_labels = _string_mapping(payload.get("commonLabels"), "commonLabels")
    common_annotations = _string_mapping(
        payload.get("commonAnnotations"), "commonAnnotations"
    )
    source = _first(payload.get("source"), "alertmanager")

    alerts: list[dict[str, Any]] = []
    for item in raw_alerts:
        if not isinstance(item, Mapping):
            raise AlertValidationError("alerts 中的每一条都必须是对象")
        labels = {**common_labels, **_string_mapping(item.get("labels"), "labels")}
        annotations = {
            **common_annotations,
            **_string_mapping(item.get("annotations"), "annotations"),
        }
        item_payload = dict(item)
        item_payload.setdefault("status", payload.get("status", "firing"))
        alerts.append(
            normalize_alert(
                {
                    **item_payload,
                    "labels": labels,
                    "annotations": annotations,
                    "source": source,
                }
            )
        )
    return alerts


def alert_summary(alert: Mapping[str, Any]) -> dict[str, str]:
    """返回适合 webhook 响应的最小告警摘要。"""
    return {
        "alert_id": str(alert.get("alert_id", "")),
        "alert_name": str(alert.get("alert_name", "")),
        "service": str(alert.get("service", "")),
        "status": str(alert.get("status", "")),
    }

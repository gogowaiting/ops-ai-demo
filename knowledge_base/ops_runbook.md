---
service: order-service
category: runbook
---
# 运维告警处理手册

## CPU 使用率过高

1. 查看 Prometheus 中对应服务的 CPU、内存、错误率。
2. 检查 Loki 日志，确认是否有异常请求或连接池耗尽。
3. 查看 Pod 是否出现 OOMKilled 或频繁重启。
4. 如果确认为临时负载升高，可先扩容副本数。
5. 如果存在内存泄漏嫌疑，需要保留现场并通知研发排查。

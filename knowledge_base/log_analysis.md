---
service: common
category: runbook
---
# 日志分析通用手册

## 常见错误模式识别

1. `OutOfMemoryError`：容器内存不足或 JVM 堆配置不当，结合 Pod 状态确认 OOMKilled。
2. `Connection refused` / `pool exhausted`：下游依赖不可用或连接池耗尽，优先检查依赖健康度。
3. 大量 5xx：按网关 → 服务 → 数据库链路逐层定位，配合监控错误率曲线。
4. 频繁 FullGC：检查内存泄漏与大对象分配，必要时保留堆转储交研发分析。

## 检索建议

- 先用 `query_loki_logs(service=<目标服务>)` 拉取时间窗口内的日志；
- 结合 `get_pod_status` 确认容器级异常（OOMKilled / 重启次数）；
- 无法定位时回到本知识库按错误模式匹配处置方案。

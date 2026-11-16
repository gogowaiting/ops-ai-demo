---
id: diagnose-service
name: diagnose_service
version: 1.0.0
description: 通过指标、日志、Pod 状态和知识库诊断服务异常
risk: low
write_op: false
triggers:
  - 诊断
  - 巡检
  - CPU
  - error
  - 日志
  - Pod
  - OOM
  - CrashLoopBackOff
required_tools:
  - cluster_snapshot
  - query_prometheus
  - query_loki_logs
  - get_pod_status
  - wiki_search
allowed_tools:
  - check_environment
  - cluster_snapshot
  - query_prometheus
  - query_loki_logs
  - get_pod_status
  - query_runbook
  - wiki_search
approval: not_required
priority: 50
conflict_policy: merge
status: enabled
---

# 服务异常诊断

1. 先确认环境、服务和异常时间窗口。
2. 使用 `cluster_snapshot` 或分别查询指标、日志和 Pod 状态。
3. 对齐 CPU、错误率、重启次数、OOM 和异常日志的时间关系。
4. 需要处置方案时检索 Runbook 或 LLM_WIKI。
5. 给出证据、根因判断、置信度和下一步建议。

只读诊断不得直接执行重启或扩容。

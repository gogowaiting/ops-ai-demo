---
id: remediate-service
name: remediate_service
version: 1.0.0
description: 在根因明确后执行受控重启、扩容和恢复验证
risk: high
write_op: true
triggers:
  - 重启
  - 扩容
  - restart
  - scale
  - 恢复服务
required_tools:
  - restart_pod
  - scale_deployment
allowed_tools:
  - cluster_snapshot
  - query_prometheus
  - query_loki_logs
  - get_pod_status
  - wiki_search
  - restart_pod
  - scale_deployment
approval: required
priority: 80
conflict_policy: highest_priority
status: enabled
---

# 服务恢复处置

1. 只有在根因和受影响对象明确后才能提出写操作。
2. 优先选择影响最小的恢复动作。
3. 重启或扩容必须经过人工审批并写入审计。
4. 执行后重新查询指标、日志和 Pod 状态验证效果。
5. 如果异常持续，停止重复写操作并升级人工处理。

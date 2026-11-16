
---
name: restart_pod
description: 当根因已定位、需要临时恢复服务时使用；重启 Pod 属于写操作，必须先经人工审批。
bound_tool: restart_pod
write_op: true
risk: high
---
# 重启 Pod（高危写操作）

## 何时触发
仅在只读诊断已定位根因、且需要立即止血时使用。

## 护栏
- 属 write_op，risk=high；
- 必须由 Review/审批门放行后才可执行；
- 执行后回查指标确认错误率是否回落。
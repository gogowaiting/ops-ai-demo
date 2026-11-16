---
name: diagnose_service
risk: low
description: Diagnose service alerts using metrics, logs, and pod status.
---

When receiving a service alert:

1. Query Prometheus for CPU, error rate, and latency.
2. Query Loki logs for recent errors.
3. Check pod status and restart count.
4. Optionally consult the runbook via query_runbook.
5. Summarize root cause and affected components.
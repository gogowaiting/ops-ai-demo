---
name: remediate_service
risk: high
description: Restart pods or scale deployments after diagnosis.
---

Remediation actions must follow these rules:

1. Only act after root cause is identified.
2. Restart pods if OOM or crash-looping.
3. Scale deployment if load is abnormally high.
4. All write actions require explicit approval.
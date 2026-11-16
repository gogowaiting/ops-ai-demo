---
service: order-service
category: troubleshooting
---
# Kubernetes 故障排查手册

## Pod CrashLoopBackOff

1. 执行 `kubectl describe pod` 查看事件。
2. 执行 `kubectl logs` 查看容器日志。
3. 检查探针配置是否过严。
4. 检查资源限制是否过小导致 OOM。
5. 如果由配置变更引起，优先回滚 Deployment。


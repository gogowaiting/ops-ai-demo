# 开源集群运维组件

Ops AI 的目标形态是部署在 Kubernetes 集群中的开源智能运维组件。它不绑定单一云厂商或监控平台，通过标准接口接入集群、Prometheus、Loki、知识库和模型服务，并以 Web 对话方式提供巡检、诊断和受控处置。

## 组件契约

| 契约 | 说明 |
|---|---|
| 交付物 | 版本化容器镜像、Kubernetes 清单、部署配置模板 |
| 用户入口 | Web 多轮对话、CLI、Alertmanager Webhook |
| 数据源 | Prometheus、Loki、Kubernetes、LLM_WIKI |
| 模型 | OpenAI 兼容 API、Ollama、离线模型 |
| 扩展点 | Skill Manifest、Tool Connector SDK |
| 安全边界 | 工具白名单、风险分级、写操作审批、SQLite 审计 |
| 状态 | RAG 索引、会话 checkpoint、审批记录 |

## 能力发现

组件暴露 `GET /api/capabilities`，返回：

- 组件名称、版本和协议版本；
- 当前环境及外部数据源配置；
- 模型提供方和模型名；
- 可用 Tool、输入 schema、风险和写操作标记；
- Skill ID、版本、状态、触发依赖和工具白名单。

该接口不返回 API Key、Token 或 kubeconfig。

## 集群部署契约

基础清单位于 `deploy/kubernetes/ops-ai.yaml`：

- 单副本 Deployment；
- ClusterIP Service；
- 外部 Prometheus/Loki 地址 ConfigMap；
- 模型密钥 `ops-ai-secrets`；
- 可选 kubeconfig Secret `ops-ai-kubeconfig`；
- RAG 和应用数据 PVC；
- 非 root、只读根文件系统、探针和资源限制。

当前 SQLite checkpoint 和审批库要求单副本。多副本部署需要迁移到 PostgreSQL、Redis 或其他共享存储。

## 扩展模型

### Skill

Skill 是声明式诊断和处置策略，不允许直接执行 Python。每个 Skill 通过 `SKILL.md` 声明版本、触发条件、风险、工具白名单、审批和生命周期。

### Tool Connector

第三方组件通过 `ToolDefinition` 注册只读或写操作工具。注册表统一处理输入 schema、风险和写操作审批。当前不自动加载任意第三方包；插件签名、来源校验和沙箱是后续安全扩展。

### 知识库

当前知识源为 Markdown + LlamaIndex。后续知识连接器应转换为统一 Document，并保留来源、权限、租户、版本和有效状态。

## 发布与兼容

建议采用语义化版本：

- `major`：Agent 状态、API 或部署契约不兼容变化；
- `minor`：新增 Tool、Skill 字段、知识源或兼容能力；
- `patch`：缺陷修复和安全更新。

每次发布应提供：

- 多架构镜像和 SHA256；
- Kubernetes 清单；
- Skill/Tool 协议版本；
- 配置迁移说明；
- SBOM 和安全扫描结果。

## 安全要求

- 对外暴露必须使用 TLS 和可信身份；
- Secret 使用 Kubernetes Secret 或 Secret Manager；
- 高危写操作必须人工审批并审计；
- Tool Connector 必须最小权限；
- Skill、镜像和插件需要来源验证；
- 不将客户数据、Token、kubeconfig 写入知识库或模型提示词。

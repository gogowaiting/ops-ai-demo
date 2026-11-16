# Ops AI

[![CI](https://github.com/gogowaiting/ops-ai-demo/actions/workflows/ci.yml/badge.svg)](https://github.com/gogowaiting/ops-ai-demo/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

> 集群智能运维 Agent：接入监控、日志、告警、集群状态和知识库，通过多轮对话完成智能分析与受控运维。

Ops AI 部署在 Kubernetes 或本地容器环境中，统一接入 Prometheus、Loki、Kubernetes 和运维知识库。用户通过 Web 或 CLI 对话发起巡检、故障诊断、知识检索和处置操作；高危写操作必须经过人工审批并留下审计记录。

**Project Status:** Alpha

## Why Ops AI

- **对话式运维**：一个窗口完成多轮巡检、诊断、分析和处置。
- **多源证据分析**：关联指标、日志、Pod 状态、告警和知识库。
- **知识增强**：支持 Markdown/RAG、Runbook、来源标注和关键词降级。
- **受控执行**：工具白名单、风险分级、人工审批和写操作审计。
- **开放扩展**：Skill Manifest 和 Tool Connector SDK。
- **离线可用**：无模型密钥时提供离线 Agent、本地向量和抽取式问答。
- **集群原生**：版本化容器镜像、Kubernetes 清单和能力发现接口。

## Architecture

```text
User Chat / Alertmanager
            │
            ▼
     LangGraph Agent
       /     |      \
      ▼      ▼       ▼
   Models  Tools   LLM_WIKI
             │        │
             │        └── RAG / Knowledge Base
             ├── Prometheus
             ├── Loki
             └── Kubernetes

State: session checkpoint / vector index / approval audit
```

完整设计：

- [Architecture](docs/architecture.md)
- [Open Source Component Contract](docs/open-source-component.md)

## Quick Start

### Prerequisites

- Python 3.12+
- Conda environment `ai`
- Optional: Docker、Kubernetes、Ollama
- External Prometheus and Loki for real monitoring data

### Run Locally

```bash
conda run -n ai python -m pip install -r requirements.txt
conda run -n ai python main.py --web
```

Open `http://127.0.0.1:8765`.

No model API key is required for offline mode.

### Use External Components

```bash
OPS_ENVIRONMENT=production \
OPS_TOOLS_MODE=real \
PROMETHEUS_URL=https://prometheus.example.com \
LOKI_URL=https://loki.example.com \
K8S_CONTEXT=production \
K8S_NAMESPACE=default \
conda run -n ai python main.py --web
```

### Run with Docker

Fill `.env` from `.env.example`, validate it, then:

```bash
make env-check
make docker-up
```

### Run in Kubernetes

```bash
kubectl apply -f deploy/kubernetes/ops-ai.yaml
kubectl port-forward svc/ops-ai 8765:8765
```

Use `make env-check` before deployment. Detailed installation, model setup, Secret, persistence and troubleshooting are in [Deployment Guide](docs/deployment.md).

## Usage

### Web Chat

示例对话：

```text
对当前集群做一次完整巡检
order-service CPU 很高，结合指标、日志和 Pod 状态帮我诊断
刚才 OOM 的服务接下来怎么处理？
查看 order-service 最近的错误日志
```

写操作会暂停并要求人工确认：

```text
重启 order-service
```

### CLI

```bash
conda run -n ai python main.py --chat
```

| 命令 | 功能 |
|---|---|
| `/wiki <问题>` | 检索运维知识库 |
| `/check` | 检查外部组件连通性 |
| `/approve on\|off` | 切换自动审批 |
| `/history` | 查看当前会话消息数 |
| `/reset` | 创建新会话 |
| `/exit` | 退出 |

### Alerts

Alertmanager 或其他系统可以调用：

```text
POST /api/alerts
```

`firing` 告警会异步进入只读诊断，`resolved` 告警只确认接收。接收器示例位于 `deploy/alertmanager.yml.example`。

### Capability Discovery

```text
GET /api/capabilities
```

返回组件协议、数据源、Tool、Skill 和模型能力，不返回 API Key、Token 或 kubeconfig。

## Configuration

| 变量 | 说明 |
|---|---|
| `OPS_ENVIRONMENT` | 当前环境名 |
| `OPS_TOOLS_MODE` | `real` / `mock` / `auto` |
| `PROMETHEUS_URL` | 外部 Prometheus API |
| `LOKI_URL` | 外部 Loki API |
| `K8S_CONTEXT` | Kubernetes context |
| `K8S_NAMESPACE` | Kubernetes namespace |
| `LLM_PROVIDER` | `openai` / `ollama` / 离线 |
| `EMBED_PROVIDER` | Embedding provider |
| `OPENAI_API_KEY` | OpenAI 或兼容服务密钥 |
| `OLLAMA_BASE_URL` | Ollama `/v1` 地址 |
| `OLLAMA_MODEL` | Ollama Chat 模型 |
| `OLLAMA_EMBED_MODEL` | Ollama Embedding 模型 |
| `ALERT_WEBHOOK_TOKEN` | 告警 Webhook Token |
| `OPS_WEB_AUTH_MODE` | `none` / `token` / `trusted_header` |
| `OPS_WEB_API_TOKEN` | Web API Token |
| `OPS_WEB_API_USER` | Token 对应操作者 |
| `OPS_WEB_API_ROLES` | Token 对应角色 |
| `OPS_WEB_IDENTITY_HEADER` | 可信网关身份头 |
| `OPS_WEB_ROLES_HEADER` | 可信网关角色头 |
| `OPS_DB_BACKEND` | `sqlite` / `postgres` |
| `OPS_DB_URL` | PostgreSQL 连接串；SQLite 共享库可用 `sqlite:///path` |

配置优先级：

```text
CLI > Environment > YAML > Defaults
```

模型配置模板：

- `config/model_config.yaml.example`
- `config/model_config.ollama.yaml.example`
- `.env.example`

## Extensibility

### Skills

每个运维策略位于 `skills/<id>/SKILL.md`，声明：

- 触发条件；
- 版本和风险；
- 工具白名单；
- 写操作和审批要求；
- 优先级、冲突策略和生命周期。

支持 `disabled`、`shadow`、`enabled` 和 `deprecated`。

### Tool Connectors

第三方工具通过 `ToolDefinition` 注册：

```python
ToolDefinition(
    name="search_cases",
    description="Search historical cases",
    execute=search_cases,
    risk="low",
    write_op=False,
    input_schema={"query": "string"},
)
```

注册表统一处理风险、输入和审批。写操作的 `approved` 参数不能由调用方伪造。

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/healthz` | Health check |
| `GET` | `/metrics` | Prometheus metrics |
| `GET` | `/api/capabilities` | Component capabilities |
| `GET` | `/api/status` | Environment and model status |
| `GET` | `/api/check` | Component connectivity |
| `GET` | `/api/wiki` | Knowledge search |
| `POST` | `/api/chat` | Multi-turn chat via SSE |
| `POST` | `/api/resume` | Resume approved/rejected action |
| `POST` | `/api/alerts` | Alert webhook |
| `GET` | `/api/alert-jobs` | Persistent alert job list |
| `GET` | `/api/traces/{id}` | Diagnostic trace details |
| `DELETE` | `/api/session/{id}` | Delete session state |

Interactive API documentation is available at `/docs`.

## Testing

```bash
conda run -n ai python -W error::ResourceWarning \
  -m unittest discover -s tests -v

conda run -n ai python -m compileall -q main.py src tests
docker compose config --quiet
kubectl apply --dry-run=client --validate=false \
  -f deploy/kubernetes/ops-ai.yaml
```

CI runs the same checks, builds the container image, performs a `/healthz` smoke test, and uploads build artifacts.

## Security

- Prometheus、Loki 和 Kubernetes 均作为外部数据源接入。
- 高危工具必须经过审批，并在执行前写入审计库。
- Webhook 可配置 Bearer Token。
- Secret 不进入 Git、镜像或能力发现接口。
- Web 认证支持 `token` 和 `trusted_header`；`none` 仅用于本地开发。
- Token 和可信网关身份会映射为 `viewer`、`operator`、`admin`。
- 诊断会记录 TraceId、节点输出和状态；指标通过 `/metrics` 暴露。
- Tool Connector 不应绕过 Agent 工具注册表。
- 审批、告警任务、诊断轨迹和会话 checkpoint 默认使用 SQLite；可通过 `OPS_DB_BACKEND=postgres` + `OPS_DB_URL` 切换。

生产环境必须在可信网关后使用 TLS，并注入 `X-User` / `X-Roles`；或直接配置 Web API Token。Kubernetes 清单默认使用 `trusted_header`，并附带 namespace 级 Role：读 Pod/log，读和 patch Deployment。

## Project Layout

```text
src/                  Agent, RAG, tools, skills, web and clients
skills/               Declarative operations skills
knowledge_base/       Markdown knowledge sources
web/                  Web chat UI
deploy/               Kubernetes and integration examples
tests/                Unit and contract tests
docs/                 Architecture and deployment documentation
```

## Documentation

- [Architecture](docs/architecture.md)
- [Deployment and Operations](docs/deployment.md)
- [Open Source Component Contract](docs/open-source-component.md)

## Roadmap

- Trusted identity and remote approval workflows
- PostgreSQL-backed multi-replica session, audit and observability storage
- Signed and sandboxed third-party connectors
- Jira、云效、CMDB and ticket knowledge sources
- End-to-end integration tests and alerting on runtime metrics

## Contributing

Contributions are welcome.

1. Create an issue for feature proposals or security-sensitive changes.
2. Keep pull requests focused and include tests.
3. Run the test and configuration checks above.
4. Update architecture or deployment documentation for contract changes.

Skill and Tool changes must preserve risk classification, approval behavior and audit compatibility.

## License

Licensed under the [Apache License 2.0](LICENSE).

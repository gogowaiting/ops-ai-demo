# 部署与运维

本文包含 Ops AI 的本地运行、容器部署、Kubernetes 部署、外部组件接入、构建发布和常见故障处理。

## 1. 部署方式

| 方式 | 适用场景 | 说明 |
|---|---|---|
| Conda 本地运行 | 开发、调试、离线演示 | 直接运行 Python 入口 |
| Docker Compose | 单节点部署、试用 | 只启动 Ops AI，外部组件通过 URL 接入 |
| Kubernetes | 集群内生产部署 | 使用 Deployment、Service、PVC、ConfigMap、Secret |

## 2. 配置原则

配置优先级：

```text
CLI 参数 > 环境变量 > YAML 配置 > 内置默认值
```

部署只需要维护一个 `.env`。模板分五组：

1. 部署身份与安全模式；
2. 外部数据源和 Web Token；
3. 模型提供方；
4. 推荐但可省略项；
5. 高级默认值。

部署前校验：

```bash
make env-check
```

常用入口：

```bash
make web          # Conda 本地 Web
make docker-up    # 校验并启动容器
make k8s-apply    # 应用 Kubernetes 清单
make test         # 运行测试
```

底层仍保留 `config/*.yaml`，用于非部署默认值；Kubernetes Secret 用于 Token、API Key 和 kubeconfig。

数据库后端由 `OPS_DB_BACKEND` 控制。默认 `sqlite` 保持原有单文件部署；生产环境可切换：

```bash
OPS_DB_BACKEND=postgres
OPS_DB_URL=postgresql+psycopg://ops_ai:password@postgres:5432/ops_ai
```

切换后，会话 checkpoint、审批审计、告警任务和诊断轨迹使用同一个 PostgreSQL 数据库。`OPS_DB_URL` 应放入 Kubernetes Secret，不建议写入 ConfigMap。

核心必填变量：

| 变量 | 说明 |
|---|---|
| `OPS_ENVIRONMENT` | 当前环境名 |
| `OPS_TOOLS_MODE` | `real` / `mock` / `auto` |
| `PROMETHEUS_URL` | 外部 Prometheus API 地址 |
| `LOKI_URL` | 外部 Loki API 地址 |
| `K8S_CONTEXT` | Kubernetes context |
| `K8S_NAMESPACE` | Kubernetes namespace |
| `LLM_PROVIDER` | `openai` / `ollama` / 离线 |
| `EMBED_PROVIDER` | Embedding 提供方 |
| `OPENAI_API_KEY` | OpenAI 或兼容服务密钥 |
| `OLLAMA_BASE_URL` | Ollama OpenAI 兼容地址 |
| `ALERT_WEBHOOK_TOKEN` | 告警 Webhook Bearer Token |
| `OPS_DB_BACKEND` | `sqlite` / `postgres` |
| `OPS_DB_URL` | PostgreSQL 连接串；SQLite 共享库可用 `sqlite:///path` |
| `OPS_WEB_AUTH_MODE` | Web 认证模式：`none` / `token` / `trusted_header` |
| `OPS_WEB_API_TOKEN` | Web API Token |
| `OPS_WEB_API_USER` | Token 对应操作者 |

高级变量默认值已内置于 Compose 和 Kubernetes 清单；确需覆盖时见 `.env.example` 的 “高级默认值” 区块。

## 3. Conda 本地运行

项目开发和验证环境为 Conda `ai`：

```bash
conda run -n ai python -m pip install -r requirements.txt
conda run -n ai python main.py --web
```

其他入口：

```bash
conda run -n ai python main.py
conda run -n ai python main.py --demo
conda run -n ai python main.py --check
conda run -n ai python main.py --chat
```

无模型密钥时自动使用离线 Agent、哈希 Embedding 和抽取式问答。

## 4. 模型配置

### OpenAI

设置 `OPENAI_API_KEY` 后即可使用 OpenAI 或兼容服务。可选 `OPENAI_BASE_URL`、`LLM_MODEL`、`EMBED_MODEL`。

### Ollama

```bash
ollama serve
ollama pull qwen3:8b
ollama pull bge-m3
```

设置：

```bash
LLM_PROVIDER=ollama
EMBED_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=qwen3:8b
OLLAMA_EMBED_MODEL=bge-m3
```

Embedding 模型必须支持 Ollama embeddings API。

## 5. 外部组件

`tools.mode`：

- `real`：外部组件失败立即报错；
- `mock`：始终使用示例数据；
- `auto`：优先真实组件，失败后降级示例数据。

真实环境至少配置：

```bash
OPS_TOOLS_MODE=real
PROMETHEUS_URL=https://prometheus.example.com
LOKI_URL=https://loki.example.com
K8S_CONTEXT=production
K8S_NAMESPACE=default
```

执行连通性检查：

```bash
conda run -n ai python main.py --check
```

## 6. Docker Compose

根据 `.env.example` 准备 `.env`，然后执行：

```bash
docker compose up -d --build
curl http://127.0.0.1:8765/healthz
curl http://127.0.0.1:8765/api/status
docker compose logs -f ops-ai
```

停止服务：

```bash
docker compose down
```

删除向量索引、会话和审计卷：

```bash
docker compose down -v
```

Compose 不启动 Prometheus/Loki。Ollama 在宿主机运行时使用：

```text
OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
```

## 7. Kubernetes

基础清单位于 `deploy/kubernetes/ops-ai.yaml`：

- ServiceAccount；
- ConfigMap；
- 数据和 RAG PVC；
- 单副本 Deployment；
- ClusterIP Service；
- 非 root、只读根文件系统、探针和资源限制。

创建 Secret：

```bash
kubectl create secret generic ops-ai-secrets \
  --from-literal=OPENAI_API_KEY='' \
  --from-literal=ALERT_WEBHOOK_TOKEN=''

kubectl create secret generic ops-ai-kubeconfig \
  --from-file=config=$HOME/.kube/config
```

部署：

```bash
kubectl apply -f deploy/kubernetes/ops-ai.yaml
kubectl port-forward svc/ops-ai 8765:8765
```

默认 SQLite 后端要求单副本。设置 `OPS_DB_BACKEND=postgres` 后，checkpoint、审批库、告警任务和诊断轨迹可共享 PostgreSQL；RAG 索引仍按 `rag.store` 配置。

### Web 权限

角色从低到高：

1. `viewer`：状态、能力、连通性、知识库和可观测查询；
2. `operator`：对话、审批恢复；
3. `admin`：会话删除。

生产部署使用：

```text
OPS_WEB_AUTH_MODE=token
OPS_WEB_API_TOKEN=<secret>
OPS_WEB_API_USER=<operator-id>
OPS_WEB_API_ROLES=operator
```

或可信网关模式：

```text
OPS_WEB_AUTH_MODE=trusted_header
OPS_WEB_IDENTITY_HEADER=X-User
OPS_WEB_ROLES_HEADER=X-Roles
```

Kubernetes 清单默认 `trusted_header`，并附带 namespace 级 Role：读 Pod/log，读和 patch Deployment。

## 8. 告警接入

Alertmanager 接收器示例位于 `deploy/alertmanager.yml.example`：

```text
POST /api/alerts
```

请求支持 Alertmanager webhook 和简化告警对象。`firing` 告警异步执行只读诊断，`resolved` 只确认接收。配置 `ALERT_WEBHOOK_TOKEN` 后，请求必须携带 Bearer Token。

## 9. 数据持久化

SQLite 后端保存：

| 数据 | 本地位置 | 容器位置 |
|---|---|---|
| RAG 索引 | `.rag_index/` | `/app/.rag_index` |
| 会话 checkpoint | `checkpoints.sqlite` | `/app/data/checkpoints.sqlite` |
| 审批审计 | `data/approvals.sqlite` | `/app/data/approvals.sqlite` |
| 告警任务 | `data/alert_jobs.sqlite` | `/app/data/alert_jobs.sqlite` |
| 诊断轨迹 | `data/traces.sqlite` | `/app/data/traces.sqlite` |
| 嵌入式 Qdrant | `.rag_qdrant/` | `/app/.rag_qdrant` |

PostgreSQL 后端保存会话 checkpoint、审批审计、告警任务和诊断轨迹，应按数据库标准备份；RAG 仍按 `rag.store` 备份。

升级、备份和迁移时必须保存这些数据。`docker compose down -v` 会删除 SQLite/RAG 持久卷。

## 10. 镜像构建

```bash
docker build -t ops-ai-demo:0.3.0 .
docker build --check .
```

多架构构建：

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --build-arg APP_VERSION=0.3.0 \
  -t registry.example.com/ops/ops-ai-demo:0.3.0 \
  --push .
```

镜像内置与构建平台匹配的 kubectl，并在构建期执行 SHA256 校验和客户端自检。

## 11. CI 构建产物

GitHub Actions 在 push、Pull Request 和手动触发时：

1. 安装依赖并运行测试；
2. 构建 Docker 镜像；
3. 执行 `/healthz` 冒烟测试；
4. 上传镜像压缩包、部署包和 `SHA256SUMS`。

## 12. 常见问题

| 现象 | 处理 |
|---|---|
| 依赖导入失败 | 使用 Conda `ai` 并安装 `requirements.txt` |
| Ollama 连接失败 | 确认服务监听 `11434`，地址包含 `/v1` |
| 容器访问宿主机 Ollama 失败 | 使用 `host.docker.internal` |
| Prometheus/Loki 显示降级 | 检查 URL、网络、TLS 和 `OPS_TOOLS_MODE` |
| Kubernetes 连接失败 | 检查 kubeconfig、context、namespace 和 RBAC |
| RAG 结果过期 | 调用 `LLMWiki.rebuild()` 或清理索引目录 |
| Web 端口占用 | 修改 `WEB_PORT` 或 `OPS_WEB_PORT` |
| PVC 写入失败 | 检查 `fsGroup=10001` 和卷权限 |
| Web 请求返回 401 | 检查 `OPS_WEB_AUTH_MODE`、Token 或可信身份头 |
| Web 请求返回 403 | 当前身份角色不足以访问接口 |
| kubectl 镜像构建失败 | 检查 `dl.k8s.io` 网络或使用内部镜像 |

# Ops AI 项目架构

本文描述当前实现的系统边界、核心数据流、技术原理、持久化和安全约束。快速安装、操作命令和部署步骤见 [README](../README.md)。

## 1. 系统概述

Ops AI 是一个面向 Kubernetes 的开源智能运维组件。它部署在集群中，通过版本化接口接入集群、Prometheus、Loki、知识库和模型服务；用户通过对话窗口进行多轮巡检、诊断和处置。涉及重启或扩容时，系统必须经过人工审批并写入审计库。

核心能力：

- Alertmanager webhook 和简化告警入口；
- LangGraph 多步诊断编排和工具调用；
- OpenAI、本地 Ollama 和离线模型适配；
- LLM_WIKI 知识检索，支持 RAG 与关键词降级；
- Prometheus、Loki、Kubernetes 真实客户端与 mock/auto 降级；
- CLI 和 Web 多轮会话；
- 写操作审批、中断恢复和 SQLite 审计；
- RAG 索引、会话状态和审批记录的本地持久化。

## 2. 系统上下文

```text
Alertmanager ─────┐
                  │ HTTP webhook
用户 / 浏览器 ────┤
                  ▼
       用户对话窗口（Web / CLI）
                  │
                  ▼
          LangGraph Agent
           /      |       \
          ▼       ▼        ▼
       模型层   工具层    LLM_WIKI
        │        │          │
        │        │          └── RAG / 关键词检索
        │        ├── 外部 Prometheus
        │        ├── 外部 Loki
        │        └── Kubernetes
        │
        ├── OpenAI 兼容 API
        ├── 本地 Ollama
        └── 离线模型

持久化：会话 checkpoint / RAG 向量数据 / 审批审计
```

## 3. 分层架构

| 层 | 主要模块 | 职责 |
|---|---|---|
| 入口层 | `main.py`、`chat.py`、`web_server.py`、`web/index.html` | CLI、HTTP API、SSE 和 Web 会话 |
| 告警适配层 | `alerts.py` | 解析 Alertmanager webhook，归一化为 Agent 告警对象 |
| 编排层 | `agent.py` | LangGraph 状态机、动作解析、工具注册表、审批门 |
| 模型层 | `llm_client.py`、`offline_models.py` | Chat/Embedding 工厂、在线/离线适配 |
| 知识层 | `llm_wiki.py`、`rag_engine.py`、`knowledge_docs.py` | 文档加载、切分、向量化、检索和来源标注 |
| 工具层 | `tools.py`、`tool_registry.py`、`ops_clients.py` | 工具注册、Connector SDK、外部组件客户端、降级策略 |
| Skill 层 | `skill_loader.py`、`skill_registry.py` | Manifest 校验、触发匹配、策略注入、工具白名单 |
| 审计层 | `approval_audit.py` | 写操作审批结果持久化 |
| 配置层 | `config_loader.py`、`config/*.yaml` | YAML 配置和环境变量覆盖 |

## 4. 核心运行模型

### 4.1 LangGraph 状态机

`AgentState` 保存：

| 字段 | 含义 |
|---|---|
| `messages` | 使用 `add_messages` reducer 按轮追加消息 |
| `alert` | 当前告警上下文 |
| `iteration` | 当前轮工具调用次数，超过 `MAX_ITERATIONS=8` 强制收敛 |
| `approved` | 非交互式写操作批准标记 |
| `interactive_approval` | 是否使用 `interrupt()` 等待人工确认 |
| `approval_context` | 会话、告警、入口和审批责任上下文 |
| `skill_context` | 当前命中 Skill 的 ID、版本、hash 和工具白名单 |

图结构：

```text
START
  │
  ▼
 llm ── ACTION ──► tools ── 结果 ──► llm
  │                                  │
  └──────────── 非 ACTION ───────────┘
                    │
                    ▼
                conclude ──► END
```

模型通过文本动作协议请求工具：

```text
ACTION: query_prometheus(query='cpu and error rate')
```

`agent.py` 使用正则解析动作名和键值参数，再通过白名单 `_TOOL_REGISTRY` 分发。该协议简单、可离线演示，但不如原生结构化 Tool Calling 对复杂参数稳健。

### 4.2 单次诊断

`run_agent(alert, ...)` 使用无 checkpointer 的图：

1. 将告警转换为初始 `HumanMessage`；
2. 模型选择只读工具收集证据；
3. 工具结果追加回消息历史；
4. 模型输出结论，或在达到迭代上限时由 `conclude` 收敛；
5. `approved=True` 必须同时提供 `approved_by`，否则调用失败。

单次诊断默认 `approved=False`，因此告警 webhook 只能形成诊断和建议，不会自动执行写操作。

### 4.3 多轮对话

CLI 和 Web 共用 `_chat_graph`：

- `SqliteSaver` 按 `thread_id` 保存消息与审批暂停状态；
- `MemorySaver` 是 checkpoint 依赖或文件不可用时的进程内降级；
- Web 对同一个 `thread_id` 使用进程内锁，避免并发打断恢复流程；
- 当前 SQLite 方案要求单 Worker，多副本部署需更换共享 checkpoint。

### 4.4 审批与审计

`restart_pod` 和 `scale_deployment` 是写操作。执行顺序为：

```text
模型提出写操作
  → interrupt() 保存待审批状态
  → CLI/Web 返回 approved + actor
  → 审批记录写入 SQLite
  → 记录成功后调用 Kubernetes 工具
```

审计记录包含审批人、身份来源、UTC 决策时间、动作、参数、结果以及会话/告警上下文。`approved`、`rejected`、`auto_approved` 和 `blocked` 都会记录。审计写入失败时写操作被拒绝，采用 fail-closed 策略。

Web 身份可来自 `operator` 字段或 `X-Operator` 请求头。当前应用没有完整身份系统，因此该值是自报标识；生产部署应由可信认证网关注入身份。

## 5. 告警入口

`POST /api/alerts` 支持：

- Alertmanager webhook 的 `alerts` 批量格式；
- 带 `labels`、`annotations`、`fingerprint` 的单条告警；
- `alert_name`、`service`、`message` 等简化字段。

归一化后的告警包含 `alert_id`、`alert_name`、`service`、`message`、`severity`、`status`、labels、annotations 和时间字段。单次最多接收 50 条告警。

`firing` 告警由 FastAPI `BackgroundTasks` 异步调用 `run_agent`；`resolved` 只确认接收。当前诊断结果写入应用日志，没有独立告警任务表或查询接口。

## 6. 模型适配

### 6.1 在线模型

- `openai`：`ChatOpenAI` 和 LlamaIndex OpenAI 客户端；
- `ollama`：复用 Ollama 的 OpenAI 兼容 `/v1` 接口；
- Ollama 地址允许省略 `/v1`，配置层会自动补齐；
- 模型实例使用 `lru_cache` 复用。

Agent 会注入固定 System Prompt，声明允许的工具和 `ACTION` 输出格式，以提高本地指令模型的动作协议遵循度。

### 6.2 离线模型

- Agent：确定性阶段流程，推进指标、日志、Pod、Wiki 和结论；
- Embedding：英文词、中文单字和二元组哈希到 384 维并归一化；
- RAG 问答：抽取检索上下文作为摘要，不生成额外推测内容。

离线模型用于演示、回归和无网络环境，不代表真实语义模型能力。

### 4.5 Skill 扩展

Skill 是声明式运维策略，不直接执行 Python。`skill_loader.py` 校验版本、触发条件、风险、工具引用和生命周期；`skill_registry.py` 根据用户文本、告警和环境选择 Skill，并应用 `merge`、`highest_priority` 或 `exclusive` 冲突策略。

命中的 Skill 会注入 System Prompt，并约束本轮允许的工具。`shadow` 和 `deprecated` Skill 仅记录命中，不进入执行策略。写操作工具风险由 `ToolDefinition` 强制约束，Skill 声明不能绕过审批门。

第三方连接器通过 `ToolDefinition` 和 `ToolRegistry` 接入，统一声明名称、描述、输入 schema、风险和 `write_op`。注册表在执行写操作时强制覆盖 `approved`，并由 Agent 审批门与审计库完成最终控制。

## 7. RAG 与知识库

```text
Markdown
  → YAML frontmatter / SHA256
  → SentenceSplitter(chunk_size, chunk_overlap)
  → Embedding
  → VectorStoreIndex
  → Top-K 相似度检索 + service 元数据过滤
  → LLM 生成或离线摘要
```

文档加载规则：

- 仅读取知识目录第一层的 `*.md`；
- frontmatter 中的 `service`、`category`、`severity`、`owner`、`tags` 进入节点 metadata；
- 文件名是稳定文档 ID；
- 全文 SHA256 记录在 `doc_state.json`。

索引同步策略：

- 文档集合和 hash 不变时复用向量数据；
- 新增、删除或内容变化时按当前文档集合整体重建；
- local 后端使用 `SimpleVectorStore` 和 `.rag_index`；
- qdrant 后端支持嵌入式本地目录或远程服务。

`LLMWiki` 是统一检索门面。RAG 初始化失败时降级为关键词检索，并返回文档来源信息。

## 8. 外部工具

| 工具 | 真实实现 | mock 数据 |
|---|---|---|
| `query_prometheus` | Prometheus `/api/v1/query` | CPU、错误率示例 |
| `query_loki_logs` | Loki `/loki/api/v1/query_range` | OOM、连接池示例日志 |
| `get_pod_status` | `kubectl get pods -o json` | CrashLoopBackOff 示例 |
| `cluster_snapshot` | 聚合指标、日志和 Pod 状态 | 聚合 mock 数据 |
| `restart_pod` | `kubectl rollout restart` | 示例成功文本 |
| `scale_deployment` | `kubectl scale` | 示例成功文本 |
| `wiki_search` | LLM_WIKI | 关键词降级 |

`tools.mode` 控制失败策略：

- `real`：外部组件失败立即报错；
- `mock`：始终使用示例数据；
- `auto`：优先真实组件，失败后返回带 `[降级]` 标记的示例数据。

## 9. 配置

配置文件：

- `config/model_config.yaml`：模型、Embedding、Ollama 和离线设置；
- `config/ops_config.yaml`：工具模式、Prometheus、Loki、Kubernetes 和 RAG；
- `.env.example`：部署环境入口，集中填写环境名、外部组件地址和模型参数；
- `config/*.example`：可提交的配置模板。

配置优先级为环境变量高于 YAML。`OPS_ENVIRONMENT` 用于标识当前环境，`PROMETHEUS_URL` 和 `LOKI_URL` 指向外部环境地址。模型提供方支持 `LLM_PROVIDER`、`EMBED_PROVIDER`；Ollama 支持 `OLLAMA_BASE_URL`、`OLLAMA_MODEL`、`OLLAMA_EMBED_MODEL`。

## 10. 持久化

数据库统一由 `OPS_DB_BACKEND` 控制，默认 SQLite；生产环境可通过 `OPS_DB_BACKEND=postgres` + `OPS_DB_URL` 切换。切换后，checkpoint、审批审计、告警任务和诊断轨迹使用同一个 PostgreSQL 数据库；RAG 索引仍由 `rag.store` 控制。

| 数据 | 默认位置 | 用途 |
|---|---|---|
| 会话 checkpoint | `checkpoints.sqlite` | 多轮消息和审批暂停状态 |
| 审批审计 | `data/approvals.sqlite` | 谁在何时批准或拒绝了什么 |
| local RAG | `.rag_index/` | 向量、docstore 和同步状态 |
| embedded Qdrant | `.rag_qdrant/` | 本地 Qdrant 向量数据 |

SQLite 后端将 `.rag_index` 和 `/app/data` 挂载为持久卷。SQLite 文件和 WAL 文件均不进入版本控制。PostgreSQL 连接串建议放入 Secret。

## 11. 部署拓扑

`docker-compose.yaml` 只启动 `ops-ai` 应用服务。Prometheus、Loki 由外部环境提供，应用通过 `PROMETHEUS_URL`、`LOKI_URL` 访问；Kubernetes 通过 kubeconfig/context 访问。

应用容器可通过 `host.docker.internal` 访问宿主机 Ollama 和 Docker Desktop Kubernetes。镜像采用多阶段构建、非 root 用户和 kubectl SHA256 校验。

## 12. 安全边界

- 写操作由 Agent 执行层强制审批，不能信任模型输出中的 `approved`；
- 审批审计失败时拒绝写操作；
- Alertmanager webhook 支持可选 Bearer token；
- Web 认证支持 `none`（本地开发）、`token` 和 `trusted_header`；
- 角色分为 `viewer`、`operator`、`admin`，分别控制查询、对话审批和管理操作；
- Kubernetes 附带 namespace 级最小权限：读 Pod/log，读和 patch Deployment；
- 当前 token/trusted_header 提供基础认证与角色边界；完整 SSO、多租户和审计防篡改仍在生产化路线中；
- 外部暴露时必须增加 TLS 和可信身份注入。

## Runtime observability

运行时可观测包含：

- `GET /metrics`：HTTP 请求、Agent 节点、工具调用、告警任务状态和耗时；
- `X-Trace-ID`：每个请求的追踪 ID；
- `DIAGNOSTIC_TRACE_DB`：按 TraceId 持久化 Agent 输入、节点输出和状态；
- `ALERT_JOB_DB`：告警诊断任务的 queued/running/completed/failed 生命周期；
- 审批审计记录 TraceId、Skill 版本、hash 和来源。

## 13. 当前限制

- 工具调用采用文本 `ACTION` 协议，不支持复杂嵌套参数；
- Skill 与基础 Tool Connector SDK 已接入 Agent，但自动插件加载、包签名和沙箱隔离尚未开放；
- SQLite checkpoint/审计/告警任务/诊断轨迹适合单 Worker；
- PostgreSQL 后端可共享这些状态，但需要验证连接池与迁移策略；
- `llm_client` 配置和 LLM_WIKI 索引是进程级缓存；
- 本地单元测试覆盖告警解析、配置、模型工厂、审批审计、依赖清单、安全边界、告警任务和诊断轨迹；完整外部组件集成测试仍待建设。

## 14. 扩展建议

1. 将文本动作协议迁移为 LangChain/LangGraph 原生 Tool Calling；
2. 为 Tool Connector 提供签名、沙箱和版本兼容检查；
3. 为 PostgreSQL 后端提供连接池、Schema 迁移和多副本验证；
4. 接入统一身份、审批工单和不可篡改审计；
5. 增加 SSO、多租户、指标告警和端到端集成测试。

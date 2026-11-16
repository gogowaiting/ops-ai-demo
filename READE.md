# Ops AI Demo：运维诊断 Agent + LLM_WIKI 知识库

面向运维场景的 AI 诊断项目：**LangGraph 状态机 + LLM_WIKI 知识库（RAG）+ 工具调用 + 审批门 + 多模型适配**。
无 API Key 时自动进入离线模式（本地向量化 + 离线问答），全链路可直接运行。

## 快速开始

```bash
pip install -r requirements.txt
cp config/model_config.yaml.example config/model_config.yaml   # 可选，缺省即可运行离线模式
export OPENAI_API_KEY=sk-xxx                                   # 可选，配置后走真实模型
python3 main.py                                                # 运行全部演示
python3 main.py --check                                        # 检测组件连通性
python3 main.py --chat                                         # 对话式运维助手（REPL）
python3 main.py --web                                          # 浏览器对话界面（默认 :8765）
```

### 本地 Ollama

Ollama 通过其 OpenAI 兼容端点 `/v1` 接入，同时覆盖 Agent Chat、RAG 问答和向量化：

```bash
ollama serve
ollama pull qwen3:8b
ollama pull bge-m3

cp config/model_config.ollama.yaml.example config/model_config.yaml
python3 main.py
```

也可只用环境变量切换提供方，不修改本地配置：

```bash
LLM_PROVIDER=ollama EMBED_PROVIDER=ollama \
OLLAMA_MODEL=qwen3:8b OLLAMA_EMBED_MODEL=bge-m3 \
python3 main.py
```

容器默认通过 `http://host.docker.internal:11434/v1` 访问宿主机 Ollama：

```bash
LLM_PROVIDER=ollama EMBED_PROVIDER=ollama docker compose up --build
```

容器方式（详见「构建与镜像制作」）：

```bash
docker build -t ops-ai-demo:dev .
docker run --rm -p 8765:8765 ops-ai-demo:dev
```

## 目录结构

```text
ops-ai-demo/
├── config/
│   ├── model_config.yaml          # 模型配置（本地使用，已 gitignore）
│   ├── model_config.yaml.example  # 模型配置模板
│   ├── model_config.ollama.yaml.example  # 本地 Ollama 配置模板
│   └── ops_config.yaml            # 组件 API 统一配置（Prometheus/Loki/K8s + 工具模式）
├── src/
│   ├── agent.py                   # LangGraph 诊断状态机（迭代熔断 + 审批门 + SqliteSaver 会话持久化）
│   ├── chat.py                    # CLI 对话入口（REPL：多轮记忆 + interrupt 人工审批）
│   ├── web_server.py              # Web 后端（FastAPI + SSE：Pydantic 校验 / 会话锁 / 访问日志）
│   ├── llm_client.py              # 模型工厂：OpenAI 兼容接口 / 离线降级 / 实例缓存
│   ├── offline_models.py          # 离线向量化 + 离线问答模型（无 Key 时启用）
│   ├── llm_wiki.py                # LLM_WIKI 知识层：检索入口、模式切换、索引重建
│   ├── rag_engine.py              # RAG 引擎：可配置向量库（local/qdrant）+ hash 增量同步 + 过滤检索
│   ├── knowledge_docs.py          # 文档加载：frontmatter 元数据、内容 hash、稳定文档 ID
│   ├── ops_clients.py             # 真实组件客户端：Prometheus / Loki / kubectl
│   ├── tools.py                   # 工具层：real/mock/auto 模式 + 连通性自检
│   ├── skill_loader.py            # SKILL.md 加载与校验（risk / write_op 元数据）
│   └── config_loader.py           # YAML 配置 + 环境变量覆盖
├── skills/                        # Skill 目录（diagnose / remediate，含风险分级）
├── knowledge_base/                # 知识库 Markdown（运维手册 / 故障排查）
├── web/index.html                 # Web 对话页面（单文件，无构建依赖）
├── deploy/
│   ├── demo-app.yaml              # 本地集群验证用示例负载（order-service）
│   └── prometheus.yml             # Prometheus 最小抓取配置（compose 挂载）
├── Dockerfile                     # 多阶段构建：依赖/运行分离、kubectl 内置+校验、非 root
├── .dockerignore                  # 构建上下文排除（密钥、向量索引、缓存）
├── docker-compose.yaml            # 一键启动：应用 + Prometheus + Loki
├── .rag_index/                    # 持久化向量索引（自动生成，可重建，已 gitignore）
├── main.py                        # 入口：4 个演示 + --check 自检 + --chat/--web 交互
└── requirements.txt
```

## 架构与数据流

```text
                        ┌──────────────────────────────┐
  告警 dict ──────────► │ agent.run_agent(alert, mode) │  单次诊断（无记忆）
  用户对话 ──────────► │ agent.get_chat_graph()       │  对话模式（chat.py REPL + checkpointer）
                        └──────────────┬───────────────┘
                                       │ LangGraph StateGraph
                     ┌─────────────────▼──────────────────┐
                     │ AgentState (TypedDict)             │
                     │  messages: add_messages（追加）     │
                     │  iteration: operator.add（熔断≤8）  │
                     │  approved: 写操作审批标记            │
                     └─────────────────┬──────────────────┘
                                       │
              ┌────────────────────────┼─────────────────────────┐
              ▼                        ▼                         ▼
       ┌────────────┐          ┌─────────────┐            ┌─────────────┐
       │ llm 节点   │─ACTION──►│ tools 节点   │──结果──────►│ conclude    │
       │ (在线/离线)│          │ 工具注册表   │  回到 llm   │ (END)       │
       └────────────┘          │ + 审批门     │            └─────────────┘
                               └──────┬──────┘
                                      │ wiki_search
                                      ▼
                        ┌──────────────────────────────┐
                        │ LLM_WIKI (llm_wiki.LLMWiki)  │
                        │  RAG 优先 / 关键词降级         │
                        └──────────────┬───────────────┘
                                       ▼
                        ┌──────────────────────────────┐
                        │ rag_engine                   │
                        │  ┌────────────────────────┐  │
                        │  │ .rag_index 向量数据     │◄─┼── 已存在且未过期：直接加载
                        │  └────────────────────────┘  │
                        │  过期/缺失：文档→切分→向量化  │
                        └──────────────┬───────────────┘
                                       ▼
                            knowledge_base/*.md
```

## 模块职责

| 模块 | 职责 |
|---|---|
| `agent.py` | 状态机编排：LLM 决策 → 工具执行 → 结论；迭代熔断、动作参数解析、写操作审批门、SqliteSaver 会话持久化（失败降级 MemorySaver） |
| `llm_client.py` | 模型工厂：OpenAI / 本地 Ollama / 离线 Chat 模型，LlamaIndex LLM/Embedding（含离线降级）、实例缓存 |
| `offline_models.py` | `OfflineEmbedding`（哈希向量）+ `OfflineChatLLM`（原文摘要），保证无 Key 时 RAG 可用 |
| `llm_wiki.py` | 知识层统一入口 `search()`、模式切换（rag/keyword）、`rebuild()`、进程级单例 |
| `rag_engine.py` | 向量库工厂（local/qdrant）、SentenceSplitter 切分、hash 增量同步、service 过滤检索 |
| `knowledge_docs.py` | Markdown 加载：frontmatter 解析、SHA256 hash、稳定文档 ID、坏文件跳过 |
| `ops_clients.py` | 真实客户端：Prometheus/Loki HTTP API、kubectl 驱动的 Pod 查询/重启/扩容 |
| `tools.py` | 工具层：real/mock/auto 模式、降级示例数据、`wiki_search`、连通性自检 |
| `skill_loader.py` | SKILL.md frontmatter 解析、重名校验、风险分级（待接入 Agent） |
| `chat.py` / `web_server.py` | 交互层：CLI REPL 与 FastAPI Web 服务（共用 `get_chat_graph` 与审批协议） |

## 对话模式（CLI REPL）

```bash
python3 main.py --chat              # 多轮对话，写操作逐次人工确认
python3 main.py --chat --auto-approve   # 演示/自动化：写操作自动批准
```

命令：

| 命令 | 说明 |
|---|---|
| `/wiki <问题>` | 直接检索运维知识库（LLM_WIKI，RAG/关键词） |
| `/check` | 组件连通性自检（Prometheus/Loki/Kubernetes） |
| `/approve on\|off` | 写操作自动审批开关（默认 off） |
| `/history` | 当前会话消息数 |
| `/reset` | 开启新会话（清空上下文） |
| `/exit` | 退出 |

对话链路要点：

- **多轮记忆与会话持久化**：对话图挂 `SqliteSaver`（`CHECKPOINT_DB`，默认 `checkpoints.sqlite`），消息与审批暂停态落盘，**进程重启可续聊**；依赖缺失时自动降级 `MemorySaver`（仅进程内）；`iteration` 每轮重置；
- **人工审批**：LLM 提议 `restart_pod` / `scale_deployment` 时，节点内 `interrupt()` 暂停，REPL 询问 `[y/N]` 后 `Command(resume=...)` 恢复；拒绝则立即收敛，不重复请求；
- **单次诊断不受影响**：`run_agent(alert, approved=...)` 走无 checkpointer 的图，保持参数式审批。

## Web 对话界面

```bash
python3 main.py --web                          # 默认 http://127.0.0.1:8765
python3 main.py --web --host 0.0.0.0 --port 9000
OPS_WEB_CORS=1 python3 main.py --web           # 前后端分离部署时开启跨域
```

前端为单文件 `web/index.html`（原生 JS，无构建、无 CDN 依赖）；后端 `src/web_server.py` 为 **FastAPI + uvicorn + SSE**：Pydantic 请求校验、同会话并发锁、访问日志中间件、全局异常兜底。

| 接口 | 说明 |
|---|---|
| `GET /` | 对话页面 |
| `GET /healthz` | 健康检查（返回版本号，供探针使用） |
| `GET /api/status` | 工具模式 / 知识库模式 / 模型提供方、模型名与在线状态 |
| `GET /api/check` | 组件连通性自检 |
| `GET /api/wiki?q=` | 知识库检索（LLM_WIKI） |
| `POST /api/chat` | 一轮对话，SSE 推送 `message` / `approval` / `end` / `error` |
| `POST /api/resume` | 审批结果恢复执行（`approve: true/false`） |
| `DELETE /api/session/{thread_id}` | 清理会话状态 |

自动生成的交互式文档：http://127.0.0.1:8765/docs

### 部署要点

| 项 | 说明 |
|---|---|
| 并发模型 | 同步图在线程池执行（`def` 端点 + `StreamingResponse` 同步生成器），不阻塞事件循环 |
| 会话互斥 | 同一 `thread_id` 并发请求返回 `409`，避免打断 checkpointer 中的审批暂停态 |
| 进程数 | 会话已落盘 SQLite（`SqliteSaver`），容器重启不丢；但 SQLite 单写者，**仍须单 worker**，多副本部署换 `RedisSaver`/`PostgresSaver` |
| 反向代理 | Nginx 需 `proxy_buffering off`（已设置 `X-Accel-Buffering: no`） |
| 安全 | 默认仅绑定 `127.0.0.1`；对外暴露请加认证与 TLS |
| 日志 | `opsai.web` 结构化访问日志（方法/路径/状态/耗时） |

审批协议（两阶段，安全边界不在前端）：

1. `POST /api/chat` 触发写操作 → 图内 `interrupt()` 暂停 → 服务端推送 `{"type":"approval","action":"restart_pod","args":{...}}` 并结束响应；
2. 页面弹出审批卡片 → 用户点击批准/拒绝 → `POST /api/resume` 携带 `approve`，由 checkpointer 中的暂停状态继续执行。

页面能力：流式消息（区分 `llm` / `tools` / `conclude` 节点）、审批卡片、状态徽章、快捷指令、`/wiki` `/check` `/reset` 命令、多轮会话（`thread_id` 隔离）。

## 构建与镜像制作

### 镜像设计

| 设计点 | 做法 | 收益 |
|---|---|---|
| 多阶段构建 | `builder` 把依赖装进 `/opt/venv`，`runtime` 仅复制 venv | 镜像不含编译工具链与构建期依赖 |
| 依赖层缓存 | `COPY requirements.txt` 先于源码，pip 走 BuildKit 缓存挂载 | 改代码不重装依赖，二次构建走缓存 |
| 零 APT 依赖 | 默认不执行 `apt-get`；kubectl 用 Python 下载（架构取自 `platform.machine()`，SHA256 校验，构建期 `kubectl version` 自检） | 受限网络可构建；架构错配在构建期暴露而非运行期 |
| 可选编译链 | `--build-arg INSTALL_BUILD_TOOLS=true` 才安装 `build-essential` | 仅有 wheel 依赖时构建最快 |
| 非 root | `uid 10001`（app），预建 `/app/.rag_index` 并 chown | 满足最小权限 / PodSecurity |
| 健康检查 | `HEALTHCHECK` 调用 `/healthz` | `docker ps`、K8s 探针可直接复用 |
| 密钥隔离 | `.dockerignore` 排除 `config/model_config.yaml` | 密钥只经环境变量注入，不进镜像层 |

### 构建镜像

```bash
# 本地构建（tag 规范：<registry>/<name>:<version>）
docker build -t ops-ai-demo:0.3.0 .
docker build -t ops-ai-demo:dev .              # 开发标签

# 覆盖构建参数（Dockerfile 顶部 ARG）
docker build \
  --build-arg PYTHON_VERSION=3.12 \
  --build-arg KUBECTL_VERSION=v1.31.4 \
  --build-arg APP_VERSION=0.3.0 \
  -t ops-ai-demo:0.3.0 .

# 目标平台缺 wheel 时才需要编译工具链
docker build --build-arg INSTALL_BUILD_TOOLS=true -t ops-ai-demo:0.3.0 .

# 提交前静态校验（秒级，不需要拉取镜像）
docker build --check .

# 查看分层与体积，确认依赖层被复用
docker history ops-ai-demo:0.3.0
docker images ops-ai-demo
```

### 多架构与推送

```bash
docker buildx create --name ops-builder --use    # 首次执行
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --build-arg APP_VERSION=0.3.0 \
  -t registry.example.com/ops/ops-ai-demo:0.3.0 \
  --push .
```

kubectl 的架构取自构建容器内的 `platform.machine()`，因此单平台构建与 buildx 多架构构建都能匹配（`TARGETARCH` 在普通 `docker build` 下不会被注入，若依赖其默认值会导致 arm64 机器装入 amd64 二进制）。下载后校验官方 SHA256，并在构建期执行 `kubectl version --client` 做架构自检。

### 受限网络 / 内网环境构建

镜像构建需要访问三处：基础镜像仓库、PyPI、`dl.k8s.io`。任一不可达时按下面方式处理：

```bash
# 1. 为 Docker daemon 配置代理（Docker Desktop：Settings → Resources → Proxies）
#    或为单次构建注入代理
docker build \
  --build-arg HTTP_PROXY=http://proxy:3128 \
  --build-arg HTTPS_PROXY=http://proxy:3128 \
  -t ops-ai-demo:0.3.0 .

# 2. PyPI 走内网源（构建时传入，不改 requirements.txt）
docker build \
  --build-arg PIP_INDEX_URL=https://mirrors.example.com/pypi/simple \
  -t ops-ai-demo:0.3.0 .
# 注：如内网源需要信任证书，可在 builder 阶段追加 trusted-host 参数

# 3. 基础镜像与 kubectl 走内网仓库
docker tag mirror.local/library/python:3.12-slim python:3.12-slim
docker build --build-arg KUBECTL_VERSION=v1.31.4 -t ops-ai-demo:0.3.0 .

# 4. 离线分发（在可联网机器构建后导入）
docker save ops-ai-demo:0.3.0 | gzip > ops-ai-demo-0.3.0.tar.gz
docker load < ops-ai-demo-0.3.0.tar.gz
```

`PIP_INDEX_URL` / `HTTP_PROXY` 作为环境变量传入 `docker build` 时，pip 会自动读取（无需在 Dockerfile 中声明）。

### 容器运行（连接宿主机集群）

```bash
docker run -d --name ops-ai \
  -p 8765:8765 \
  -e OPS_TOOLS_MODE=auto \
  -e K8S_CONTEXT=docker-desktop \
  -v "${HOME}/.kube:/home/app/.kube:ro" \
  -v ops_rag_index:/app/.rag_index \
  -e CHECKPOINT_DB=/app/data/checkpoints.sqlite \
  -v ops_checkpoints:/app/data \
  ops-ai-demo:0.3.0

curl -s http://127.0.0.1:8765/healthz
docker exec ops-ai python main.py --check        # 容器内组件自检
```

Docker Desktop 的 kubeconfig 中 server 为 `https://kubernetes.docker.internal:6443`，容器内可解析到宿主机，因此挂载 kubeconfig 后 Pod 查询/重启/扩容可直接工作。

> 其它集群若 kubeconfig 指向 `127.0.0.1`，需把 server 改为 `https://host.docker.internal:<port>`；证书不含该 SAN 时追加 `insecure-skip-tls-verify`。

### 一键启动（compose）

```bash
docker compose up -d --build      # 应用 + Prometheus + Loki
docker compose logs -f ops-ai
curl -s http://127.0.0.1:8765/api/status
docker compose down               # 停止（保留索引卷与会话卷）
docker compose down -v            # 连索引卷、会话 checkpoint 卷一起清理
```

compose 已把 `PROMETHEUS_URL` / `LOKI_URL` 指向服务名（`http://prometheus:9090`、`http://loki:3100`），无需改代码；`deploy/prometheus.yml` 提供最小抓取配置与业务指标接入示例。

### 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `context deadline exceeded`（拉基础镜像） | `registry-1.docker.io` 不可达 | 配代理 / 配 registry mirror / 从内网仓库 `docker tag` 同版本镜像 |
| `kubectl SHA256 校验失败` | 下载中断或代理返回了错误页面 | 重试构建；确认 `dl.k8s.io` 可达或经代理 |
| `SSL: CERTIFICATE_VERIFY_FAILED`（下载 kubectl） | 基础镜像缺 CA 证书 | 在 runtime 阶段安装 `ca-certificates`，或改用非 slim 的 `python:3.12` 基础镜像 |
| `no space left on device` | 依赖层体积较大 | `docker builder prune` 清理构建缓存 |
| 索引卷写入失败 | 卷属主为 root，非 root 用户不可写 | 镜像已预建并 chown；自定义卷执行 `chown 10001:10001` |
| 容器内 kubectl 连接失败 | kubeconfig server 指向 `127.0.0.1` | 改为 `host.docker.internal`，或补 TLS 跳过参数 |
| 无 Key 时问答偏弱 | 离线哈希向量 + 抽取式回答 | 注入 `OPENAI_API_KEY` / `EMBED_API_KEY` 后重建索引 |

## 外部组件接入（config/ops_config.yaml）

所有外部组件 API 集中在 `config/ops_config.yaml`，并支持环境变量覆盖：

| 配置项 | 环境变量 | 说明 |
|---|---|---|
| `tools.mode` | `OPS_TOOLS_MODE` | `real` / `mock` / `auto`（默认） |
| `prometheus.base_url` | `PROMETHEUS_URL` | Prometheus HTTP API 地址 |
| `loki.base_url` | `LOKI_URL` | Loki HTTP API 地址 |
| `kubernetes.kubeconfig` | `KUBECONFIG` | 留空使用 `~/.kube/config` |
| `kubernetes.context` | `K8S_CONTEXT` | 默认 `docker-desktop` |
| `kubernetes.namespace` | `K8S_NAMESPACE` | 默认 `default` |

三种模式行为：

- **real**：必须连上真实组件，连接失败直接抛 `ComponentError`（生产环境用）；
- **mock**：始终使用内置示例数据（离线演示 / 回归测试）；
- **auto**：优先真实组件，失败时降级示例数据并在输出前标注 `[降级]`。

### 本地 Docker Desktop 集群验证

```bash
# 1. 部署示例负载（可选，用于验证 Pod 状态/重启/扩容）
kubectl apply -f deploy/demo-app.yaml

# 2. 部署 Prometheus / Loki（示例，按需选择镜像与命名空间）
kubectl create deployment prometheus --image=prom/prometheus
kubectl create deployment loki --image=grafana/loki
kubectl port-forward svc/prometheus 9090:9090 &
kubectl port-forward svc/loki 3100:3100 &

# 3. 自检组件连通性
python3 main.py --check

# 4. 跑一次诊断（写操作需显式审批才放行）
python3 -c "
from src.agent import run_agent
alert = {'alert_name':'HighCPU','service':'order-service','message':'CPU usage > 90%'}
for s in run_agent(alert, approved=True): print(s)
"
```

## RAG 与文档存储

### 文档切分与元数据

- **切分**：`SentenceSplitter`，`chunk_size` / `chunk_overlap` 由 `config/ops_config.yaml → rag` 配置（默认 512/50），加载与构建两条路径均生效；
- **frontmatter 元数据**：Markdown 头部 YAML（`service` / `category` / `severity` / `owner` / `tags`）剥离后注入节点 metadata，是**过滤检索的依据**；
- **稳定文档 ID**：文件名即 `ref_doc_id`，是增量同步的删除/替换单位；
- **内容 hash**：全文 SHA256 记入 `doc_state.json`，比 mtime 可靠（git 切分支、文件复制不误判）。

### 可配置向量库（rag.store）

| 后端 | 场景 | 说明 |
|---|---|---|
| `local`（默认） | 单机 / 演示 / 离线 | llama_index `SimpleVectorStore`，JSON 持久化到 `.rag_index/`，零外部依赖 |
| `qdrant`（嵌入式） | 数据量增长、需元数据过滤 | `qdrant.url` 留空走本地模式，向量数据存 `.rag_qdrant/`，无需部署服务 |
| `qdrant`（远程） | 多副本 / 生产 | `qdrant.url=http://qdrant:6333`（`QDRANT_URL` 环境变量覆盖） |

切换后端**无需改代码**，改配置或环境变量后调用 `LLMWiki.rebuild()` 重建一次即可。

### 增量同步（doc_state.json）

1. 首次运行：加载文档 → 切分 → 向量化 → 持久化 + 写入同步状态；
2. 后续运行：对比文档 hash，**无变化直接复用**（实测 0.15s），变更时按新文档集合重建（local 清目录 / qdrant 重建集合），日志记录变更原因（新增 / 删除 / 内容变更的文档名）；
3. 索引损坏：告警后兜底重建一次，不静默失败；
4. 手动重建：`LLMWiki.rebuild()`（同时清理同步状态与嵌入式 qdrant 目录）。

> 逐条 upsert 的增量更新待接入（当前规模全量重建为秒级，可靠性优先）。

### LLM_WIKI 使用方式（同一检索入口，四个出口）

```python
# 1. 代码直调（service 可选，按 frontmatter 过滤）
from src.llm_wiki import get_wiki
get_wiki().search("OOM 处理", top_k=2, service="order-service")

# 2. Agent 工具动作（LLM 可自主调用）
ACTION: wiki_search(query='OOM 怎么处理', service='order-service')

# 3. CLI REPL
运维> /wiki OOM 怎么处理

# 4. Web API
GET /api/wiki?q=OOM+处理&service=order-service
```

返回文本自带**来源标注**（文档名 / service / 相似度分数），keyword 降级模式同样支持 service 过滤。

## 运行模式

| 条件 | Agent 决策 | 向量化 / 问答 | 检索方式 |
|---|---|---|---|
| `provider: openai` + `OPENAI_API_KEY` | 真实 LLM | OpenAI | RAG 向量检索 |
| `provider: ollama` | 本地 Ollama Chat | Ollama Chat + Embedding | RAG 向量检索 |
| 未配置 Key | 离线阶段化流程 | `OfflineEmbedding` / `OfflineChatLLM` | RAG 向量检索（本地向量） |
| 未安装 llama-index | 离线阶段化流程 | — | 关键词降级检索 |

Ollama 模型名直接使用 `ollama pull` 后的标签，例如 `qwen3:8b`、`qwen2.5:14b`；Embedding 模型必须支持 Ollama embeddings API，例如 `bge-m3` 或 `nomic-embed-text`。

## 安全护栏

- 写操作（`restart_pod` / `scale_deployment`）审批门：`run_agent(alert, approved=False)` 默认拦截，**忽略 LLM 输出中的 `approved=True`**。
- `config/model_config.yaml` 已 gitignore；OpenAI 支持 `OPENAI_API_KEY` / `OPENAI_BASE_URL`，Ollama 支持 `OLLAMA_BASE_URL` / `OLLAMA_MODEL` / `OLLAMA_EMBED_MODEL` 环境变量覆盖。
- 检索结果原样注入，不额外生成推测内容（离线模型仅返回原文摘要）。

## 演示

```text
Demo 1  Agent 诊断         指标→日志→Pod→根因结论（三步收敛）
Demo 2  RAG 问答           向量检索 + 摘要回答
Demo 3  Agent + LLM_WIKI   诊断定位 OOM 后自动检索处置方案
Demo 4  LLM_WIKI 检索      知识层独立检索，打印当前模式（rag/keyword）
```

## 生产化状态

已具备：

- 状态一致性（`TypedDict + add_messages`）、死循环熔断、写操作审批门；
- **CLI 对话模式**：多轮记忆（SqliteSaver 落盘）、`interrupt()` 写操作人工确认、内置 `/wiki` `/check` 等命令；
- **Web 对话界面**：FastAPI + uvicorn + SSE（Pydantic 校验、会话锁、访问日志、`/healthz`）+ 单文件前端，流式诊断过程、审批卡片、状态徽章；
- **真实组件接入**：Prometheus / Loki HTTP API + kubectl 驱动的 K8s 查询、重启、扩容，支持 `real/mock/auto` 三模式与降级；
- 组件配置统一收口（`config/ops_config.yaml` + 环境变量覆盖）、`python3 main.py --check` 连通性自检；
- **可配置向量库**：RAG 存储后端 `local` / `qdrant`（嵌入式或远程）配置切换，frontmatter 元数据过滤检索，hash 增量同步与来源标注；
- **容器化交付**：多阶段 Dockerfile（kubectl 多架构、非 root、健康检查）、`docker-compose.yaml` 一键起应用 + Prometheus + Loki。

待补齐（按优先级）：

1. **告警入口**：Alertmanager webhook 接入现有 FastAPI 服务 → `run_agent`，替代手工构造告警 dict；
2. **审批审计与远端确认**：审批记录落库（谁/何时/批准了什么），IM/工单系统确认替代页面点击；
3. **会话存储升级**：`SqliteSaver` 换 `RedisSaver`/`PostgresSaver`，支持多 worker 与集群部署（接口不变，仅换 checkpointer）；
4. **Skill 生效**：`skill_loader` 注入诊断/修复策略，按 `risk` 动态约束可用工具；
5. **可观测性**：Prometheus 指标埋点、TraceId 串联、诊断轨迹持久化；
6. **测试与 CI**：客户端解析、审批门、动作解析、索引生命周期的单元测试。

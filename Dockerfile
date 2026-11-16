# syntax=docker/dockerfile:1.7
# 运维智能体镜像：多阶段构建（依赖与运行分离），非 root 运行
#
# 构建：docker build -t ops-ai-demo:dev .
# 多架构：docker buildx build --platform linux/amd64,linux/arm64 -t <repo>/ops-ai-demo:0.3.0 --push .

ARG PYTHON_VERSION=3.12

# ---------- 依赖构建阶段 ----------
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# 默认不装编译工具链（依赖均有预编译 wheel，构建更快、对受限网络更友好）；
# 目标平台缺 wheel 时用 --build-arg INSTALL_BUILD_TOOLS=true 启用
ARG INSTALL_BUILD_TOOLS=false
RUN if [ "$INSTALL_BUILD_TOOLS" = "true" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends build-essential \
        && rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /build
COPY requirements.txt .

# 依赖层与源码层分离：仅 requirements.txt 变化时才重装
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt

# ---------- 运行阶段 ----------
FROM python:${PYTHON_VERSION}-slim AS runtime

ARG KUBECTL_VERSION=v1.31.4
ARG APP_VERSION=0.3.0

LABEL org.opencontainers.image.title="ops-ai-demo" \
      org.opencontainers.image.description="运维智能体：告警诊断 + 知识库检索 + 受控修复" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OPS_WEB_HOST=0.0.0.0 \
    OPS_WEB_PORT=8765 \
    KUBECONFIG=/home/app/.kube/config

# kubectl：K8s 工具层（Pod 查询 / 重启 / 扩容）依赖该二进制。
# 用 Python 直接下载，无需安装 curl（少一层 APT 依赖）；架构取自解释器所在平台，
# 单平台与 buildx 多架构构建均匹配（TARGETARCH 在普通 docker build 下不会被注入）。
# 下载后校验官方 SHA256，避免供应链篡改。
RUN python - <<PY
import hashlib
import os
import platform
import urllib.request

base = "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux"
arch = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}[platform.machine()]
target = "/usr/local/bin/kubectl"

urllib.request.urlretrieve(f"{base}/{arch}/kubectl", target)
expected = urllib.request.urlopen(f"{base}/{arch}/kubectl.sha256").read().decode().split()[0]
actual = hashlib.sha256(open(target, "rb").read()).hexdigest()
if actual != expected:
    raise SystemExit(f"kubectl SHA256 校验失败: {actual} != {expected}")
os.chmod(target, 0o755)
PY

# 构建期自检：架构不匹配（如 arm64 上装了 amd64 二进制）会在此直接失败，避免运行期才暴露
RUN kubectl version --client > /dev/null

COPY --from=builder /opt/venv /opt/venv

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /app

WORKDIR /app
COPY --chown=app:app . .

# 预建可写目录：named volume 首次挂载时继承该属主，避免非 root 写索引/会话库失败
RUN mkdir -p /app/.rag_index /app/data /home/app/.kube && chown -R app:app /app /home/app/.kube

USER app
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3)"

CMD ["python", "main.py", "--web"]

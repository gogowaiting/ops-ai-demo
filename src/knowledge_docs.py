"""知识库文档加载：frontmatter 元数据、内容 hash 与稳定文档 ID。

运维文档（Markdown）支持 YAML frontmatter 元数据，加载后注入节点 metadata，
可在检索时按 service / category 过滤：

    ---
    service: order-service
    category: troubleshooting
    ---

文档 ID 取文件名（作为 ref_doc_id），是增量同步与删除的单位；
doc_hash 取全文 SHA256，用于检测内容变更（比 mtime 可靠：git 操作不影响 hash）。
"""

import hashlib
import logging
from pathlib import Path
from typing import Any

import yaml
from llama_index.core import Document

logger = logging.getLogger(__name__)

# 业务元数据字段：frontmatter 中出现即注入检索 metadata
_META_KEYS = ("service", "category", "severity", "owner", "tags")


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """剥离 YAML frontmatter，返回 (元数据, 正文)。无 frontmatter 时正文原样返回。"""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text  # 只有起始分隔符，视为普通正文
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        logger.warning("frontmatter 解析失败，按普通正文处理: %s", exc)
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    return meta, parts[2].strip()


def load_documents(knowledge_dir: str) -> list[Document]:
    """加载知识库目录下的全部 Markdown 文档（含元数据与内容 hash）。"""
    root = Path(knowledge_dir)
    if not root.is_dir():
        # 边界处理：目录不存在时快速失败，避免静默返回空结果
        raise FileNotFoundError(f"知识库目录不存在: {knowledge_dir}")

    docs: list[Document] = []
    for file in sorted(root.glob("*.md")):
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            # 边界处理：跳过无法读取/解码的文件，避免单个坏文件中断整体加载
            logger.warning("跳过无法读取的文档 %s: %s", file, exc)
            continue

        front, body = split_frontmatter(text)
        metadata: dict[str, Any] = {
            "source": file.name,
            "doc_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        for key in _META_KEYS:
            if key in front and front[key] is not None:
                metadata[key] = str(front[key])

        docs.append(
            Document(
                text=body,
                metadata=metadata,
                id_=file.name,  # 稳定文档 ID：ref_doc_id = 文件名，供增量同步删除/替换
            )
        )
    return docs

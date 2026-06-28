"""知识库管理器 — 文档上传、文本分块、嵌入向量化、Milvus 语义搜索"""

import uuid
import re
import logging
from datetime import datetime
from pymilvus import MilvusClient, DataType

logger = logging.getLogger(__name__)

# Milvus 连接配置
MILVUS_URI = "http://101.132.81.140:19530"
MILVUS_DIM = 384  # all-MiniLM-L6-v2 输出维度

# 全局 embedding 模型（懒加载）
_embedding_model = None


def get_embedding_model():
    """获取或初始化 sentence-transformers 模型（强制 CPU）"""
    global _embedding_model
    if _embedding_model is None:
        import os
        os.environ["CUDA_VISIBLE_DEVICES"] = ""  # 强制 CPU，避免 CUDA 兼容性问题
        from sentence_transformers import SentenceTransformer
        logger.info("加载 embedding 模型: all-MiniLM-L6-v2 (CPU) ...")
        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
        logger.info("embedding 模型加载完成")
    return _embedding_model


def get_milvus_client() -> MilvusClient:
    """获取 Milvus 客户端"""
    return MilvusClient(uri=MILVUS_URI)


class TextChunker:
    """文本分块器"""

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk(self, text: str) -> list[str]:
        """将文本分成固定大小的块，支持重叠"""
        if not text or not text.strip():
            return []

        paragraphs = re.split(r'\n\s*\n', text.strip())
        chunks = []
        current = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current) + len(para) + 1 <= self.chunk_size:
                current = current + "\n" + para if current else para
            else:
                if current:
                    chunks.append(current.strip())
                if len(para) > self.chunk_size:
                    sentences = re.split(r'(?<=[。！？.!?\n])\s*', para)
                    sub_current = ""
                    for sent in sentences:
                        if len(sub_current) + len(sent) + 1 <= self.chunk_size:
                            sub_current = sub_current + " " + sent if sub_current else sent
                        else:
                            if sub_current:
                                chunks.append(sub_current.strip())
                            sub_current = sent
                    if sub_current:
                        current = sub_current
                    else:
                        current = ""
                else:
                    current = para

        if current.strip():
            chunks.append(current.strip())

        if self.chunk_overlap > 0 and len(chunks) > 1:
            overlapped = [chunks[0]]
            for i in range(1, len(chunks)):
                prev = chunks[i - 1]
                overlap_text = prev[-self.chunk_overlap:] if len(prev) > self.chunk_overlap else prev
                overlapped.append(overlap_text + " " + chunks[i])
            chunks = overlapped

        return chunks


def _collection_name(dataset_id: str) -> str:
    """Milvus collection 命名：kb_{dataset_id}，替换非法字符"""
    safe = dataset_id.replace("-", "_")
    return f"kb_{safe}"


def _ensure_collection(client: MilvusClient, dataset_id: str):
    """确保 Milvus collection 存在，不存在则创建"""
    name = _collection_name(dataset_id)
    if client.has_collection(name):
        return
    client.create_collection(
        collection_name=name,
        dimension=MILVUS_DIM,
        metric_type="COSINE",
        auto_id=True,
        datatype=DataType.FLOAT_VECTOR,
    )
    logger.info(f"创建 Milvus collection: {name}")


class KnowledgeManager:
    """知识库管理器 — Milvus 后端"""

    def __init__(self, db):
        self.db = db
        self._milvus = get_milvus_client()
        # 预热 embedding 模型
        try:
            get_embedding_model()
        except Exception as e:
            logger.warning(f"embedding 模型加载失败（将延迟重试）: {e}")

    # ==================== Dataset CRUD ====================

    def create_dataset(self, data: dict) -> dict:
        ds_id = str(uuid.uuid4())[:12]
        ds = {
            "id": ds_id,
            "name": data.get("name", "未命名知识库"),
            "description": data.get("description", ""),
            "embedding_model": data.get("embedding_model", "sentence-transformers/all-MiniLM-L6-v2"),
            "chunk_size": data.get("chunk_size", 500),
            "chunk_overlap": data.get("chunk_overlap", 50),
        }
        self.db.create_dataset(ds)
        # 创建 Milvus collection
        _ensure_collection(self._milvus, ds_id)
        logger.info(f"创建知识库: {ds['name']} ({ds_id})")
        return self.db.get_dataset(ds_id)

    def get_dataset(self, ds_id: str) -> dict | None:
        return self.db.get_dataset(ds_id)

    def list_datasets(self, limit: int = 100) -> list[dict]:
        return self.db.list_datasets(limit)

    def update_dataset(self, ds_id: str, data: dict) -> dict | None:
        self.db.update_dataset(ds_id, data)
        return self.db.get_dataset(ds_id)

    def delete_dataset(self, ds_id: str) -> bool:
        # 删除 Milvus collection
        name = _collection_name(ds_id)
        try:
            if self._milvus.has_collection(name):
                self._milvus.drop_collection(name)
                logger.info(f"删除 Milvus collection: {name}")
        except Exception as e:
            logger.error(f"删除 Milvus collection 失败: {e}")
        return self.db.delete_dataset(ds_id)

    # ==================== Document CRUD ====================

    def upload_document(self, dataset_id: str, name: str, content: str,
                        file_type: str = "txt", file_size: int = 0,
                        auto_chunk: bool = True) -> dict:
        """上传文档并自动分块、嵌入、写入 Milvus"""
        ds = self.db.get_dataset(dataset_id)
        if not ds:
            raise ValueError(f"知识库 {dataset_id} 不存在")

        doc_id = str(uuid.uuid4())[:12]
        doc = {
            "id": doc_id,
            "dataset_id": dataset_id,
            "name": name,
            "file_type": file_type,
            "file_size": file_size or len(content.encode("utf-8")),
            "content": content,
            "status": "chunking",
        }
        self.db.create_document(doc)

        if auto_chunk:
            try:
                self._chunk_document(doc_id, dataset_id, content, ds)
                self.db.update_document(doc_id, {"status": "completed"})
            except Exception as e:
                logger.error(f"文档分块/索引失败: {e}")
                self.db.update_document(doc_id, {"status": "error", "error_msg": str(e)})

        self.db.update_dataset_counts(dataset_id)
        return self.db.get_document(doc_id)

    def _chunk_document(self, doc_id: str, dataset_id: str, content: str, ds: dict):
        """分块 → 嵌入 → 写入 Milvus"""
        chunker = TextChunker(
            chunk_size=ds.get("chunk_size", 500),
            chunk_overlap=ds.get("chunk_overlap", 50),
        )
        chunks = chunker.chunk(content)
        logger.info(f"文档 {doc_id} 分成 {len(chunks)} 个片段")

        if not chunks:
            logger.warning(f"文档 {doc_id} 无可分块内容")
            self.db.update_document(doc_id, {"segment_count": 0})
            return

        # 获取 embedding 模型
        model = get_embedding_model()

        # 批量嵌入
        embeddings = model.encode(chunks, show_progress_bar=False).tolist()

        # 确保 collection 存在
        _ensure_collection(self._milvus, dataset_id)

        # 准备 Milvus 插入数据（auto_id=True，不传 id）
        milvus_data = []
        seg_ids = []
        for i, (chunk_text, vec) in enumerate(zip(chunks, embeddings)):
            seg_id = str(uuid.uuid4())[:12]
            seg_ids.append(seg_id)
            milvus_data.append({
                "vector": vec,
                "document_id": doc_id,
                "dataset_id": dataset_id,
                "content": chunk_text,
                "position": i,
                "word_count": len(chunk_text),
            })

        # 批量插入 Milvus
        insert_result = self._milvus.insert(collection_name=_collection_name(dataset_id), data=milvus_data)

        # 同时在 SQLite 中记录 segments（用于管理界面展示）
        for seg_id, item in zip(seg_ids, milvus_data):
            self.db.create_segment({
                "id": seg_id,
                "document_id": doc_id,
                "dataset_id": dataset_id,
                "content": item["content"],
                "word_count": item["word_count"],
                "position": item["position"],
            })

        self.db.update_document(doc_id, {"segment_count": len(chunks)})
        logger.info(f"文档 {doc_id}: {len(chunks)} 片段已写入 Milvus")

    def get_document(self, doc_id: str) -> dict | None:
        return self.db.get_document(doc_id)

    def list_documents(self, dataset_id: str, limit: int = 100) -> list[dict]:
        return self.db.list_documents(dataset_id, limit)

    def update_document(self, doc_id: str, data: dict) -> dict | None:
        self.db.update_document(doc_id, data)
        return self.db.get_document(doc_id)

    def delete_document(self, doc_id: str) -> bool:
        """删除文档及其在 Milvus 中的向量"""
        doc = self.db.get_document(doc_id)
        if not doc:
            return False
        dataset_id = doc["dataset_id"]
        # 从 Milvus 删除
        try:
            self._milvus.delete(
                collection_name=_collection_name(dataset_id),
                filter=f'document_id == "{doc_id}"',
            )
            logger.info(f"从 Milvus 删除文档 {doc_id} 的向量")
        except Exception as e:
            logger.error(f"Milvus 删除失败: {e}")
        return self.db.delete_document(doc_id)

    # ==================== 语义搜索 ====================

    def search(self, dataset_ids: list[str], query: str, top_k: int = 5) -> list[dict]:
        """跨数据集 Milvus 语义搜索"""
        if not dataset_ids or not query:
            return []

        model = get_embedding_model()
        query_vec = model.encode(query).tolist()

        all_results = []
        for ds_id in dataset_ids:
            name = _collection_name(ds_id)
            if not self._milvus.has_collection(name):
                continue
            try:
                results = self._milvus.search(
                    collection_name=name,
                    data=[query_vec],
                    limit=top_k,
                    output_fields=["document_id", "dataset_id", "content", "position"],
                )
                for hit in results[0]:
                    entity = hit.get("entity", {})
                    all_results.append({
                        "segment_id": hit["id"],
                        "document_id": entity.get("document_id", ""),
                        "dataset_id": ds_id,
                        "content": entity.get("content", ""),
                        "score": round(hit["distance"], 4),
                    })
            except Exception as e:
                logger.error(f"搜索 {name} 失败: {e}")
                continue

        all_results.sort(key=lambda x: x["score"], reverse=True)
        return all_results[:top_k]

    def search_in_dataset(self, dataset_id: str, query: str, top_k: int = 5) -> list[dict]:
        """单数据集搜索"""
        return self.search([dataset_id], query, top_k)

    def get_dataset_stats(self, dataset_id: str) -> dict:
        """获取知识库统计（含 Milvus 向量数）"""
        ds = self.db.get_dataset(dataset_id)
        if not ds:
            return {}
        name = _collection_name(dataset_id)
        vector_count = 0
        try:
            if self._milvus.has_collection(name):
                stats = self._milvus.get_collection_stats(name)
                vector_count = stats.get("row_count", 0)
        except Exception:
            pass
        doc_count = self.db.get_document_count(dataset_id)
        return {
            **ds,
            "document_count": doc_count,
            "vector_count": vector_count,
        }

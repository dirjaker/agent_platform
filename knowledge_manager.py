"""知识库管理器 — 文档上传、文本分块、TF-IDF 向量化、语义搜索"""

import uuid
import re
import json
import logging
import math
from collections import Counter
from datetime import datetime

logger = logging.getLogger(__name__)


class TextChunker:
    """文本分块器"""

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk(self, text: str) -> list[str]:
        """将文本分成固定大小的块，支持重叠"""
        if not text or not text.strip():
            return []

        # 先按段落分割
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
                # 如果单个段落超过 chunk_size，按句子再分
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

        # 添加重叠
        if self.chunk_overlap > 0 and len(chunks) > 1:
            overlapped = [chunks[0]]
            for i in range(1, len(chunks)):
                prev = chunks[i - 1]
                overlap_text = prev[-self.chunk_overlap:] if len(prev) > self.chunk_overlap else prev
                overlapped.append(overlap_text + " " + chunks[i])
            chunks = overlapped

        return chunks


class TFIDFVectorizer:
    """简单的 TF-IDF 向量化器"""

    def __init__(self):
        self.vocabulary: dict[str, int] = {}
        self.idf: dict[str, float] = {}
        self._fitted = False

    def _tokenize(self, text: str) -> list[str]:
        """简单分词（中英文混合）"""
        # 英文单词
        english_words = re.findall(r'[a-zA-Z]+', text.lower())
        # 中文字符（按字分）
        chinese_chars = re.findall(r'[\u4e00-\u9fff]', text)
        # 中文 bigram
        chinese_bigrams = []
        for i in range(len(chinese_chars) - 1):
            chinese_bigrams.append(chinese_chars[i] + chinese_chars[i + 1])
        return english_words + chinese_chars + chinese_bigrams

    def fit_transform(self, documents: list[str]) -> list[list[float]]:
        """拟合并转换文档为 TF-IDF 向量"""
        if not documents:
            return []

        # 构建词汇表
        all_tokens = set()
        doc_tokens = []
        for doc in documents:
            tokens = self._tokenize(doc)
            doc_tokens.append(tokens)
            all_tokens.update(tokens)

        self.vocabulary = {token: idx for idx, token in enumerate(sorted(all_tokens))}

        # 计算 IDF
        n_docs = len(documents)
        doc_freq = Counter()
        for tokens in doc_tokens:
            unique = set(tokens)
            for token in unique:
                doc_freq[token] += 1

        self.idf = {}
        for token in self.vocabulary:
            df = doc_freq.get(token, 0)
            self.idf[token] = math.log((n_docs + 1) / (df + 1)) + 1

        self._fitted = True

        # 计算 TF-IDF 向量
        vectors = []
        for tokens in doc_tokens:
            vec = self._to_vector(tokens)
            vectors.append(vec)
        return vectors

    def transform(self, text: str) -> list[float]:
        """将单个文本转换为 TF-IDF 向量"""
        if not self._fitted:
            return []
        tokens = self._tokenize(text)
        return self._to_vector(tokens)

    def _to_vector(self, tokens: list[str]) -> list[float]:
        """将 token 列表转换为 TF-IDF 向量"""
        tf = Counter(tokens)
        total = len(tokens) if tokens else 1
        vec = [0.0] * len(self.vocabulary)
        for token, count in tf.items():
            if token in self.vocabulary:
                idx = self.vocabulary[token]
                tf_val = count / total
                idf_val = self.idf.get(token, 1.0)
                vec[idx] = tf_val * idf_val
        # L2 归一化
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec

    def to_json(self) -> str:
        """序列化"""
        return json.dumps({
            "vocabulary": self.vocabulary,
            "idf": self.idf,
        }, ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str) -> "TFIDFVectorizer":
        """反序列化"""
        obj = cls()
        d = json.loads(data)
        obj.vocabulary = d["vocabulary"]
        obj.idf = d["idf"]
        obj._fitted = True
        return obj


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算余弦相似度"""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class KnowledgeManager:
    """知识库管理器"""

    def __init__(self, db):
        self.db = db
        self._vectorizers: dict[str, TFIDFVectorizer] = {}  # dataset_id -> vectorizer

    # ==================== Dataset CRUD ====================

    def create_dataset(self, data: dict) -> dict:
        ds_id = str(uuid.uuid4())[:12]
        ds = {
            "id": ds_id,
            "name": data.get("name", "未命名知识库"),
            "description": data.get("description", ""),
            "embedding_model": data.get("embedding_model", "tfidf"),
            "chunk_size": data.get("chunk_size", 500),
            "chunk_overlap": data.get("chunk_overlap", 50),
        }
        self.db.create_dataset(ds)
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
        self._vectorizers.pop(ds_id, None)
        return self.db.delete_dataset(ds_id)

    # ==================== Document CRUD ====================

    def upload_document(self, dataset_id: str, name: str, content: str,
                        file_type: str = "txt", file_size: int = 0,
                        auto_chunk: bool = True) -> dict:
        """上传文档并自动分块"""
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
                logger.error(f"文档分块失败: {e}")
                self.db.update_document(doc_id, {"status": "error", "error_msg": str(e)})

        self.db.update_dataset_counts(dataset_id)
        return self.db.get_document(doc_id)

    def _chunk_document(self, doc_id: str, dataset_id: str, content: str, ds: dict):
        """分块并生成向量"""
        chunker = TextChunker(
            chunk_size=ds.get("chunk_size", 500),
            chunk_overlap=ds.get("chunk_overlap", 50),
        )
        chunks = chunker.chunk(content)
        logger.info(f"文档 {doc_id} 分成 {len(chunks)} 个片段")

        # 创建片段
        segments = []
        for i, chunk_text in enumerate(chunks):
            seg_id = str(uuid.uuid4())[:12]
            seg = {
                "id": seg_id,
                "document_id": doc_id,
                "dataset_id": dataset_id,
                "content": chunk_text,
                "word_count": len(chunk_text),
                "position": i,
            }
            self.db.create_segment(seg)
            segments.append(seg)

        # 生成 TF-IDF 向量
        self._build_vectors(dataset_id)

        self.db.update_document(doc_id, {"segment_count": len(chunks)})

    def _build_vectors(self, dataset_id: str):
        """为数据集构建/重建 TF-IDF 向量"""
        segments = self.db.list_segments(dataset_id=dataset_id)
        if not segments:
            return

        contents = [s["content"] for s in segments]
        vectorizer = TFIDFVectorizer()
        vectors = vectorizer.fit_transform(contents)
        self._vectorizers[dataset_id] = vectorizer

        # 将向量存入数据库
        for seg, vec in zip(segments, vectors):
            # 只存非零值以节省空间
            sparse = {str(i): v for i, v in enumerate(vec) if v != 0}
            self.db.create_segment({
                **seg,
                "vector": json.dumps(sparse),
            }) if False else None
            # 直接更新 vector 字段
            with self.db._get_conn() as conn:
                conn.execute(
                    "UPDATE segments SET vector = ? WHERE id = ?",
                    (json.dumps(sparse), seg["id"]),
                )

        logger.info(f"数据集 {dataset_id} 向量构建完成: {len(vectors)} 个片段, 词汇量 {len(vectorizer.vocabulary)}")

    def get_document(self, doc_id: str) -> dict | None:
        return self.db.get_document(doc_id)

    def list_documents(self, dataset_id: str, limit: int = 100) -> list[dict]:
        return self.db.list_documents(dataset_id, limit)

    def update_document(self, doc_id: str, data: dict) -> dict | None:
        self.db.update_document(doc_id, data)
        return self.db.get_document(doc_id)

    def delete_document(self, doc_id: str) -> bool:
        doc = self.db.get_document(doc_id)
        if doc:
            self._vectorizers.pop(doc["dataset_id"], None)
        return self.db.delete_document(doc_id)

    # ==================== 语义搜索 ====================

    def search(self, dataset_ids: list[str], query: str, top_k: int = 5) -> list[dict]:
        """跨数据集语义搜索"""
        all_results = []

        for ds_id in dataset_ids:
            results = self._search_single_dataset(ds_id, query, top_k)
            all_results.extend(results)

        # 按相似度排序，取 top_k
        all_results.sort(key=lambda x: x["score"], reverse=True)
        return all_results[:top_k]

    def _search_single_dataset(self, dataset_id: str, query: str,
                                top_k: int = 5) -> list[dict]:
        """单个数据集内搜索"""
        # 获取或构建 vectorizer
        vectorizer = self._vectorizers.get(dataset_id)
        if not vectorizer:
            vectorizer = self._load_vectorizer(dataset_id)
            if not vectorizer:
                return []

        # 查询向量化
        query_vec = vectorizer.transform(query)
        if not query_vec:
            return []

        # 获取所有片段
        segments = self.db.list_segments(dataset_id=dataset_id, limit=10000)
        if not segments:
            return []

        # 计算相似度
        results = []
        for seg in segments:
            if not seg.get("vector"):
                continue
            try:
                sparse = json.loads(seg["vector"])
                # 还原稠密向量
                dim = len(query_vec)
                seg_vec = [0.0] * dim
                for idx_str, val in sparse.items():
                    idx = int(idx_str)
                    if idx < dim:
                        seg_vec[idx] = val
                score = cosine_similarity(query_vec, seg_vec)
                if score > 0.01:
                    results.append({
                        "segment_id": seg["id"],
                        "document_id": seg["document_id"],
                        "dataset_id": dataset_id,
                        "content": seg["content"],
                        "score": round(score, 4),
                    })
            except (json.JSONDecodeError, ValueError):
                continue

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def _load_vectorizer(self, dataset_id: str) -> TFIDFVectorizer | None:
        """从数据库加载重建 vectorizer"""
        segments = self.db.list_segments(dataset_id=dataset_id, limit=10000)
        if not segments:
            return None

        contents = [s["content"] for s in segments]
        vectorizer = TFIDFVectorizer()
        vectorizer.fit_transform(contents)
        self._vectorizers[dataset_id] = vectorizer
        return vectorizer

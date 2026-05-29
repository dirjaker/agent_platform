"""扩展工具集 — HTTP、JSON、文本分析等实用工具"""

import json
import re
import hashlib
from datetime import datetime, timedelta
from tool_registry import registry
from models import ToolParameter, ParameterType


# ==================== HTTP 工具 ====================

@registry.register(
    name="http_get",
    description="发送 HTTP GET 请求，返回响应内容",
    category="网络",
    parameters=[
        ToolParameter(name="url", type=ParameterType.STRING, description="请求 URL", required=True),
        ToolParameter(name="headers", type=ParameterType.OBJECT, description="请求头（可选）"),
    ],
    timeout=30,
)
async def http_get(url: str, headers: dict = None) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            return {"status": resp.status_code, "data": resp.json()}
        else:
            text = resp.text[:5000]  # 限制长度
            return {"status": resp.status_code, "data": text, "content_type": content_type}


@registry.register(
    name="http_post",
    description="发送 HTTP POST 请求",
    category="网络",
    parameters=[
        ToolParameter(name="url", type=ParameterType.STRING, description="请求 URL", required=True),
        ToolParameter(name="body", type=ParameterType.OBJECT, description="请求体（JSON）"),
        ToolParameter(name="headers", type=ParameterType.OBJECT, description="请求头（可选）"),
    ],
    timeout=30,
)
async def http_post(url: str, body: dict = None, headers: dict = None) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        resp = await client.post(url, json=body, headers=headers)
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            return {"status": resp.status_code, "data": resp.json()}
        else:
            return {"status": resp.status_code, "data": resp.text[:5000]}


# ==================== JSON 工具 ====================

@registry.register(
    name="json_parse",
    description="解析 JSON 字符串为对象",
    category="数据",
    parameters=[
        ToolParameter(name="text", type=ParameterType.STRING, description="JSON 字符串", required=True),
    ],
)
def json_parse(text: str) -> str:
    try:
        obj = json.loads(text)
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except json.JSONDecodeError as e:
        return f"JSON 解析错误: {e}"


@registry.register(
    name="json_query",
    description="从 JSON 数据中提取指定字段",
    category="数据",
    parameters=[
        ToolParameter(name="data", type=ParameterType.STRING, description="JSON 字符串", required=True),
        ToolParameter(name="path", type=ParameterType.STRING, description="字段路径，用点分隔，如 'a.b.c'", required=True),
    ],
)
def json_query(data: str, path: str) -> str:
    try:
        obj = json.loads(data)
        keys = path.split(".")
        current = obj
        for key in keys:
            if isinstance(current, dict):
                current = current.get(key)
            elif isinstance(current, list) and key.isdigit():
                current = current[int(key)]
            else:
                return f"路径错误: 无法访问 '{key}'"
            if current is None:
                return f"字段 '{path}' 不存在"
        return json.dumps(current, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"查询错误: {e}"


# ==================== 文本分析工具 ====================

@registry.register(
    name="text_count",
    description="统计文本的字符数、单词数、行数",
    category="文本",
    parameters=[
        ToolParameter(name="text", type=ParameterType.STRING, description="待统计文本", required=True),
    ],
)
def text_count(text: str) -> dict:
    chars = len(text)
    words = len(text.split())
    lines = text.count("\n") + 1
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    return {
        "字符数": chars,
        "单词数": words,
        "行数": lines,
        "中文字符": chinese,
    }


@registry.register(
    name="text_extract",
    description="从文本中提取指定模式的内容（正则表达式）",
    category="文本",
    parameters=[
        ToolParameter(name="text", type=ParameterType.STRING, description="待提取文本", required=True),
        ToolParameter(name="pattern", type=ParameterType.STRING, description="正则表达式", required=True),
    ],
)
def text_extract(text: str, pattern: str) -> list[str]:
    try:
        matches = re.findall(pattern, text)
        return matches if matches else ["未找到匹配内容"]
    except re.error as e:
        return [f"正则表达式错误: {e}"]


@registry.register(
    name="text_summarize",
    description="提取文本的关键信息（前 N 句话）",
    category="文本",
    parameters=[
        ToolParameter(name="text", type=ParameterType.STRING, description="待摘要文本", required=True),
        ToolParameter(name="num_sentences", type=ParameterType.INTEGER, description="提取句子数", default=3),
    ],
)
def text_summarize(text: str, num_sentences: int = 3) -> str:
    # 简单的基于句子分割的摘要
    sentences = re.split(r'[。！？.!?]', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 5]
    summary = sentences[:num_sentences]
    return "。".join(summary) + "。" if summary else "文本过短，无法提取"


# ==================== 加密/哈希工具 ====================

@registry.register(
    name="hash_text",
    description="计算文本的哈希值（MD5/SHA256）",
    category="工具",
    parameters=[
        ToolParameter(name="text", type=ParameterType.STRING, description="待哈希文本", required=True),
        ToolParameter(name="algorithm", type=ParameterType.STRING, description="哈希算法", default="md5",
                      enum=["md5", "sha256", "sha1"]),
    ],
)
def hash_text(text: str, algorithm: str = "md5") -> dict:
    h = hashlib.new(algorithm)
    h.update(text.encode("utf-8"))
    return {"algorithm": algorithm, "hash": h.hexdigest()}


@registry.register(
    name="base64_encode",
    description="Base64 编码/解码",
    category="工具",
    parameters=[
        ToolParameter(name="text", type=ParameterType.STRING, description="待处理文本", required=True),
        ToolParameter(name="action", type=ParameterType.STRING, description="操作", default="encode",
                      enum=["encode", "decode"]),
    ],
)
def base64_encode(text: str, action: str = "encode") -> str:
    import base64
    try:
        if action == "encode":
            return base64.b64encode(text.encode("utf-8")).decode("ascii")
        else:
            return base64.b64decode(text).decode("utf-8")
    except Exception as e:
        return f"错误: {e}"


# ==================== 时间日期工具 ====================

@registry.register(
    name="timestamp_convert",
    description="时间戳与日期时间互转",
    category="工具",
    parameters=[
        ToolParameter(name="value", type=ParameterType.STRING, description="时间戳（秒）或日期字符串（YYYY-MM-DD HH:MM:SS）"),
        ToolParameter(name="action", type=ParameterType.STRING, description="操作", default="to_date",
                      enum=["to_date", "to_timestamp"]),
    ],
)
def timestamp_convert(value: str, action: str = "to_date") -> str:
    try:
        if action == "to_date":
            ts = float(value)
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        else:
            dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            return str(int(dt.timestamp()))
    except Exception as e:
        return f"转换错误: {e}"


# ==================== 知识库工具（模拟） ====================

KNOWLEDGE_BASE = {
    "python": "Python 是一种高级编程语言，由 Guido van Rossum 于 1991 年创建。特点：简洁易读、动态类型、丰富的标准库。",
    "deepseek": "DeepSeek 是一家中国 AI 公司，开发了 DeepSeek-V4 等大语言模型。API 兼容 OpenAI 格式。",
    "agent": "AI Agent 是能够自主完成任务的智能体，通过工具调用与外部世界交互。核心循环：思考-行动-观察。",
    "react": "ReAct 是一种 Agent 范式，结合推理（Reasoning）和行动（Acting），通过交替思考和工具调用解决问题。",
    "function_calling": "Function Calling 是大模型的一种能力，可以识别用户意图并调用预定义的函数/工具。",
}


@registry.register(
    name="knowledge_query",
    description="查询内置知识库",
    category="搜索",
    parameters=[
        ToolParameter(name="query", type=ParameterType.STRING, description="查询关键词", required=True),
    ],
)
def knowledge_query(query: str) -> str:
    query_lower = query.lower()
    results = []
    for key, value in KNOWLEDGE_BASE.items():
        if key in query_lower or query_lower in key:
            results.append(f"【{key}】{value}")
    if results:
        return "\n\n".join(results)
    return f"未找到与 '{query}' 相关的知识。可用主题: {', '.join(KNOWLEDGE_BASE.keys())}"

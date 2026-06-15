"""DAG 工作流引擎 — 拓扑排序、节点执行器、变量插值"""

import uuid
import json
import re
import time
import logging
import traceback
from datetime import datetime
from typing import Any
from collections import deque

logger = logging.getLogger(__name__)


# ==================== 变量插值 ====================

INTERPOLATION_PATTERN = re.compile(r'\{\{(\w+)\.(\w+)\}\}')


def interpolate(text: str, context: dict[str, dict]) -> str:
    """
    变量插值：将 {{node_id.field}} 替换为实际值

    context 示例: {"node_1": {"output": "hello", "status": "ok"}}
    """
    if not isinstance(text, str):
        return text

    def replacer(match):
        node_id = match.group(1)
        field = match.group(2)
        node_ctx = context.get(node_id, {})
        value = node_ctx.get(field, match.group(0))
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    return INTERPOLATION_PATTERN.sub(replacer, text)


def interpolate_dict(data: dict, context: dict[str, dict]) -> dict:
    """递归插值整个字典"""
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = interpolate(value, context)
        elif isinstance(value, dict):
            result[key] = interpolate_dict(value, context)
        elif isinstance(value, list):
            result[key] = [
                interpolate_dict(item, context) if isinstance(item, dict)
                else interpolate(item, context) if isinstance(item, str)
                else item
                for item in value
            ]
        else:
            result[key] = value
    return result


# ==================== DAG 拓扑排序 ====================

def topological_sort(nodes: list[dict], edges: list[dict]) -> list[str]:
    """
    拓扑排序

    nodes: [{"id": "node_1", "type": "llm", ...}, ...]
    edges: [{"source": "node_1", "target": "node_2"}, ...]

    返回排序后的 node_id 列表
    """
    node_ids = {n["id"] for n in nodes}
    in_degree = {nid: 0 for nid in node_ids}
    adjacency = {nid: [] for nid in node_ids}

    for edge in edges:
        src = edge["source"]
        tgt = edge["target"]
        if src in node_ids and tgt in node_ids:
            adjacency[src].append(tgt)
            in_degree[tgt] = in_degree.get(tgt, 0) + 1

    # BFS 拓扑排序
    queue = deque([nid for nid, deg in in_degree.items() if deg == 0])
    order = []

    while queue:
        node_id = queue.popleft()
        order.append(node_id)
        for neighbor in adjacency[node_id]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(order) != len(node_ids):
        raise ValueError("工作流存在循环依赖")

    return order


# ==================== 节点执行器 ====================

class NodeExecutor:
    """节点执行器基类"""

    def __init__(self, model_router=None, knowledge_manager=None, tool_registry=None):
        self.model_router = model_router
        self.knowledge_manager = knowledge_manager
        self.tool_registry = tool_registry

    async def execute(self, node: dict, inputs: dict, context: dict) -> dict:
        """
        执行节点

        node: 节点配置 {"id": "xxx", "type": "llm", "config": {...}}
        inputs: 插值后的输入
        context: 所有已执行节点的上下文 {"node_id": {"output": ..., ...}}

        返回: {"output": ..., "status": "success/error"}
        """
        raise NotImplementedError


class LLMNodeExecutor(NodeExecutor):
    """LLM 节点"""

    async def execute(self, node: dict, inputs: dict, context: dict) -> dict:
        config = node.get("config", {})
        model = config.get("model", "deepseek-chat")
        prompt = inputs.get("prompt", config.get("prompt", ""))
        system_prompt = config.get("system_prompt", "")
        temperature = config.get("temperature", 0.7)
        max_tokens = config.get("max_tokens", 2048)

        if not self.model_router:
            return {"output": "错误: 模型路由器未配置", "status": "error"}

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            response = await self.model_router.chat(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return {
                "output": response.content or "",
                "status": "success",
                "usage": response.usage,
                "model": response.model,
            }
        except Exception as e:
            return {"output": f"LLM 调用失败: {e}", "status": "error"}


class CodeNodeExecutor(NodeExecutor):
    """代码执行节点"""

    async def execute(self, node: dict, inputs: dict, context: dict) -> dict:
        config = node.get("config", {})
        code = inputs.get("code", config.get("code", ""))
        language = config.get("language", "python")

        if language != "python":
            return {"output": f"不支持的语言: {language}", "status": "error"}

        import io
        import contextlib as cl

        safe_builtins = {
            "print": print, "len": len, "str": str, "int": int, "float": float,
            "bool": bool, "list": list, "dict": dict, "tuple": tuple, "set": set,
            "range": range, "enumerate": enumerate, "zip": zip, "map": map,
            "filter": filter, "sorted": sorted, "reversed": reversed,
            "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
            "isinstance": isinstance, "hasattr": hasattr, "getattr": getattr,
            "json": json,
            "__context__": context,
            "__inputs__": inputs,
        }

        stdout = io.StringIO()
        local_ns = {}
        try:
            with cl.redirect_stdout(stdout):
                exec(code, {"__builtins__": safe_builtins}, local_ns)
            output = stdout.getvalue()
            # 检查是否有 output 变量
            if "output" in local_ns:
                output = local_ns["output"]
            elif not output:
                output = "代码执行完成（无输出）"
            return {"output": output, "status": "success"}
        except Exception as e:
            return {"output": f"执行错误: {e}", "status": "error"}


class IfElseNodeExecutor(NodeExecutor):
    """条件分支节点"""

    async def execute(self, node: dict, inputs: dict, context: dict) -> dict:
        config = node.get("config", {})
        condition = inputs.get("condition", config.get("condition", ""))
        operator = config.get("operator", "contains")
        compare_value = config.get("value", "")

        # 获取比较的源值
        source_value = inputs.get("value", "")

        result = False
        try:
            if operator == "contains":
                result = compare_value in str(source_value)
            elif operator == "not_contains":
                result = compare_value not in str(source_value)
            elif operator == "equals":
                result = str(source_value) == str(compare_value)
            elif operator == "not_equals":
                result = str(source_value) != str(compare_value)
            elif operator == "starts_with":
                result = str(source_value).startswith(compare_value)
            elif operator == "ends_with":
                str(source_value).endswith(compare_value)
                result = str(source_value).endswith(compare_value)
            elif operator == "is_empty":
                result = not source_value
            elif operator == "is_not_empty":
                result = bool(source_value)
            elif operator == "greater_than":
                result = float(source_value) > float(compare_value)
            elif operator == "less_than":
                result = float(source_value) < float(compare_value)
        except Exception as e:
            return {"output": f"条件判断错误: {e}", "status": "error", "branch": "false"}

        return {
            "output": str(result),
            "status": "success",
            "branch": "true" if result else "false",
        }


class HTTPNodeExecutor(NodeExecutor):
    """HTTP 请求节点"""

    async def execute(self, node: dict, inputs: dict, context: dict) -> dict:
        import httpx

        config = node.get("config", {})
        url = inputs.get("url", config.get("url", ""))
        method = inputs.get("method", config.get("method", "GET")).upper()
        headers = inputs.get("headers", config.get("headers", {}))
        body = inputs.get("body", config.get("body", None))

        if not url:
            return {"output": "错误: URL 为空", "status": "error"}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                if method == "GET":
                    resp = await client.get(url, headers=headers)
                elif method == "POST":
                    resp = await client.post(url, headers=headers, json=body)
                elif method == "PUT":
                    resp = await client.put(url, headers=headers, json=body)
                elif method == "DELETE":
                    resp = await client.delete(url, headers=headers)
                else:
                    return {"output": f"不支持的方法: {method}", "status": "error"}

                content_type = resp.headers.get("content-type", "")
                if "json" in content_type:
                    data = resp.json()
                    output = json.dumps(data, ensure_ascii=False, indent=2)
                else:
                    output = resp.text

                return {
                    "output": output,
                    "status": "success",
                    "http_status": resp.status_code,
                }
        except Exception as e:
            return {"output": f"HTTP 请求失败: {e}", "status": "error"}


class ToolNodeExecutor(NodeExecutor):
    """工具调用节点"""

    async def execute(self, node: dict, inputs: dict, context: dict) -> dict:
        config = node.get("config", {})
        tool_name = inputs.get("tool_name", config.get("tool_name", ""))
        tool_args = inputs.get("arguments", config.get("arguments", {}))

        if not self.tool_registry:
            return {"output": "错误: 工具注册中心未配置", "status": "error"}

        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except json.JSONDecodeError:
                tool_args = {"input": tool_args}

        try:
            result = await self.tool_registry.execute(tool_name, tool_args)
            return {
                "output": json.dumps(result.model_dump(), ensure_ascii=False, default=str),
                "status": "success" if result.success else "error",
                "execution_time": result.execution_time,
            }
        except Exception as e:
            return {"output": f"工具执行失败: {e}", "status": "error"}


class KnowledgeRetrievalNodeExecutor(NodeExecutor):
    """知识检索节点"""

    async def execute(self, node: dict, inputs: dict, context: dict) -> dict:
        config = node.get("config", {})
        query = inputs.get("query", "")
        dataset_ids = config.get("dataset_ids", [])
        top_k = config.get("top_k", 5)

        if not self.knowledge_manager:
            return {"output": "错误: 知识管理器未配置", "status": "error"}

        if not query:
            return {"output": "错误: 查询为空", "status": "error"}

        try:
            results = self.knowledge_manager.search(dataset_ids, query, top_k)
            if results:
                formatted = "\n\n---\n\n".join(
                    f"[来源: {r['document_id']}, 相似度: {r['score']}]\n{r['content']}"
                    for r in results
                )
                return {"output": formatted, "status": "success", "results": results}
            else:
                return {"output": "未找到相关内容", "status": "success", "results": []}
        except Exception as e:
            return {"output": f"知识检索失败: {e}", "status": "error"}


# ==================== 节点执行器映射 ====================

NODE_EXECUTORS = {
    "llm": LLMNodeExecutor,
    "code": CodeNodeExecutor,
    "if_else": IfElseNodeExecutor,
    "http": HTTPNodeExecutor,
    "tool": ToolNodeExecutor,
    "knowledge_retrieval": KnowledgeRetrievalNodeExecutor,
}


# ==================== 工作流引擎 ====================

class WorkflowEngine:
    """DAG 工作流执行引擎"""

    def __init__(self, db, model_router=None, knowledge_manager=None, tool_registry=None):
        self.db = db
        self.executors: dict[str, NodeExecutor] = {}

        # 初始化所有执行器
        for node_type, executor_cls in NODE_EXECUTORS.items():
            self.executors[node_type] = executor_cls(
                model_router=model_router,
                knowledge_manager=knowledge_manager,
                tool_registry=tool_registry,
            )

    async def run_workflow(self, workflow_config: dict, inputs: dict = None,
                           app_id: str = None, conversation_id: str = None) -> dict:
        """
        执行工作流

        workflow_config: {
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {"id": "llm_1", "type": "llm", "config": {"model": "...", "prompt": "..."}},
                ...
            ],
            "edges": [
                {"source": "start", "target": "llm_1"},
                ...
            ]
        }

        inputs: {"query": "...", ...}

        返回: {"status": "success/error", "outputs": {...}, "node_results": {...}}
        """
        inputs = inputs or {}
        nodes = workflow_config.get("nodes", [])
        edges = workflow_config.get("edges", [])

        if not nodes:
            return {"status": "error", "error": "工作流无节点"}

        # 创建运行记录
        run_id = str(uuid.uuid4())[:12]
        run = {
            "id": run_id,
            "app_id": app_id,
            "conversation_id": conversation_id,
            "workflow_config": workflow_config,
            "inputs": inputs,
            "status": "running",
        }
        self.db.create_workflow_run(run)
        start_time = time.time()

        try:
            # 拓扑排序
            execution_order = topological_sort(nodes, edges)
            node_map = {n["id"]: n for n in nodes}

            # 执行上下文
            context: dict[str, dict] = {"__inputs__": inputs}
            node_results: dict[str, dict] = {}

            # 逐节点执行
            for node_id in execution_order:
                node = node_map.get(node_id)
                if not node:
                    continue

                node_type = node.get("type", "")

                # Start 节点直接传递输入
                if node_type == "start":
                    context[node_id] = {"output": json.dumps(inputs, ensure_ascii=False), "status": "success"}
                    node_results[node_id] = context[node_id]
                    continue

                # End 节点不执行
                if node_type == "end":
                    context[node_id] = {"output": "", "status": "success"}
                    node_results[node_id] = context[node_id]
                    continue

                executor = self.executors.get(node_type)
                if not executor:
                    context[node_id] = {"output": f"未知节点类型: {node_type}", "status": "error"}
                    node_results[node_id] = context[node_id]
                    continue

                # 变量插值：替换节点配置中的变量引用
                node_config = node.get("config", {})
                interpolated_config = interpolate_dict(node_config, context)
                # 合并 inputs 中的对应字段
                node_inputs = {}
                for key, val in interpolated_config.items():
                    node_inputs[key] = val
                # 也从全局 inputs 中获取
                for key, val in inputs.items():
                    if key not in node_inputs:
                        node_inputs[key] = val

                # 执行节点
                try:
                    result = await executor.execute(node, node_inputs, context)
                    context[node_id] = result
                    node_results[node_id] = result

                    # 如果节点执行失败，停止工作流（可选）
                    if result.get("status") == "error":
                        logger.warning(f"节点 {node_id} 执行失败: {result.get('output')}")

                except Exception as e:
                    logger.error(f"节点 {node_id} 执行异常: {e}")
                    context[node_id] = {"output": str(e), "status": "error"}
                    node_results[node_id] = context[node_id]

            # 收集输出（end 节点或最后一个节点）
            outputs = {}
            end_nodes = [n for n in nodes if n.get("type") == "end"]
            if end_nodes:
                for end_node in end_nodes:
                    end_id = end_node["id"]
                    # 找到指向 end 的节点
                    for edge in edges:
                        if edge["target"] == end_id and edge["source"] in context:
                            outputs[end_id] = context[edge["source"]]
            else:
                # 取最后一个执行的节点的输出
                if execution_order:
                    last_id = execution_order[-1]
                    outputs["final"] = context.get(last_id, {})

            elapsed = time.time() - start_time
            total_tokens = sum(
                r.get("usage", {}).get("total_tokens", 0)
                for r in node_results.values()
            )

            # 更新运行记录
            self.db.update_workflow_run(run_id, {
                "outputs": outputs,
                "status": "success",
                "elapsed_time": elapsed,
                "total_tokens": total_tokens,
                "finished_at": datetime.now().isoformat(),
            })

            return {
                "run_id": run_id,
                "status": "success",
                "outputs": outputs,
                "node_results": node_results,
                "elapsed_time": round(elapsed, 2),
                "total_tokens": total_tokens,
            }

        except Exception as e:
            elapsed = time.time() - start_time
            error_msg = f"{e}\n{traceback.format_exc()}"
            logger.error(f"工作流执行失败: {error_msg}")

            self.db.update_workflow_run(run_id, {
                "status": "error",
                "error": str(e),
                "elapsed_time": elapsed,
                "finished_at": datetime.now().isoformat(),
            })

            return {
                "run_id": run_id,
                "status": "error",
                "error": str(e),
                "elapsed_time": round(elapsed, 2),
            }

"""工具注册中心 — 管理所有可用工具"""

import inspect
import json
import time
import logging
from typing import Callable, Any
from models import ToolDefinition, ToolParameter, ToolResult, ParameterType

logger = logging.getLogger(__name__)


class ToolRegistry:
    """工具注册中心"""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._executors: dict[str, Callable] = {}

    def register(
        self,
        name: str,
        description: str,
        category: str = "general",
        parameters: list[ToolParameter] | None = None,
        timeout: int = 30,
    ):
        """装饰器：注册一个工具"""
        def decorator(func: Callable):
            tool_params = parameters or self._infer_parameters(func)
            tool_def = ToolDefinition(
                name=name,
                display_name=name.replace("_", " ").title(),
                description=description or inspect.getdoc(func) or "",
                category=category,
                parameters=tool_params,
                timeout=timeout,
            )
            self._tools[name] = tool_def
            self._executors[name] = func
            logger.info(f"注册工具: {name} ({category})")
            return func
        return decorator

    def _infer_parameters(self, func: Callable) -> list[ToolParameter]:
        """从函数签名自动推断参数"""
        sig = inspect.signature(func)
        params = []
        type_map = {
            str: ParameterType.STRING,
            int: ParameterType.INTEGER,
            float: ParameterType.FLOAT,
            bool: ParameterType.BOOLEAN,
            list: ParameterType.ARRAY,
            dict: ParameterType.OBJECT,
        }
        for name, param in sig.parameters.items():
            if name in ("self", "cls"):
                continue
            required = param.default is inspect.Parameter.empty
            params.append(ToolParameter(
                name=name,
                type=type_map.get(param.annotation, ParameterType.STRING),
                description=f"参数: {name}",
                required=required,
                default=None if required else param.default,
            ))
        return params

    def get_tool(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_tools(self, category: str | None = None) -> list[ToolDefinition]:
        tools = list(self._tools.values())
        if category:
            tools = [t for t in tools if t.category == category]
        return [t for t in tools if t.enabled]

    def to_function_calls(self, tool_names: list[str] | None = None) -> list[dict]:
        """转换为 OpenAI Function Calling 格式"""
        tools = self._tools.values() if tool_names is None else [
            self._tools[n] for n in tool_names if n in self._tools
        ]
        result = []
        for t in tools:
            props = {}
            required = []
            for p in t.parameters:
                props[p.name] = {
                    "type": p.type.value,
                    "description": p.description,
                }
                if p.enum:
                    props[p.name]["enum"] = p.enum
                if p.required:
                    required.append(p.name)

            result.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required,
                    },
                },
            })
        return result

    async def execute(self, name: str, arguments: dict) -> ToolResult:
        """执行工具"""
        executor = self._executors.get(name)
        if not executor:
            return ToolResult(
                success=False, error=f"工具 '{name}' 不存在", execution_time=0,
            )

        start = time.time()
        try:
            if inspect.iscoroutinefunction(executor):
                result = await executor(**arguments)
            else:
                result = executor(**arguments)

            return ToolResult(
                success=True, data=result,
                execution_time=time.time() - start,
            )
        except Exception as e:
            logger.error(f"工具 {name} 执行失败: {e}")
            return ToolResult(
                success=False, error=str(e),
                execution_time=time.time() - start,
            )


# ========== 全局工具注册中心 ==========
registry = ToolRegistry()


# ========== 内置工具 ==========

@registry.register(
    name="get_current_time",
    description="获取当前日期和时间",
    category="系统",
)
def get_current_time() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@registry.register(
    name="calculate",
    description="计算数学表达式，支持加减乘除、幂运算等",
    category="工具",
    parameters=[
        ToolParameter(name="expression", type=ParameterType.STRING, description="数学表达式，如 2+3*4", required=True),
    ],
)
def calculate(expression: str) -> str:
    """安全计算数学表达式"""
    import math
    allowed = {
        "abs": abs, "round": round, "min": min, "max": max,
        "int": int, "float": float,
        "sqrt": math.sqrt, "pow": pow, "log": math.log,
        "pi": math.pi, "e": math.e,
    }
    try:
        result = eval(expression, {"__builtins__": {}}, allowed)
        return str(result)
    except Exception as e:
        return f"计算错误: {e}"


@registry.register(
    name="search_web",
    description="搜索互联网信息（模拟）",
    category="搜索",
    parameters=[
        ToolParameter(name="query", type=ParameterType.STRING, description="搜索关键词", required=True),
        ToolParameter(name="num_results", type=ParameterType.INTEGER, description="返回结果数量", default=3),
    ],
)
async def search_web(query: str, num_results: int = 3) -> list[dict]:
    """模拟网页搜索"""
    return [
        {"title": f"搜索结果 {i+1}: {query}", "url": f"https://example.com/{i}", "snippet": f"关于'{query}'的搜索结果摘要..."}
        for i in range(num_results)
    ]


@registry.register(
    name="read_file",
    description="读取指定路径的文件内容",
    category="文件",
    parameters=[
        ToolParameter(name="path", type=ParameterType.STRING, description="文件路径", required=True),
        ToolParameter(name="encoding", type=ParameterType.STRING, description="文件编码", default="utf-8"),
    ],
)
def read_file(path: str, encoding: str = "utf-8") -> str:
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return f"文件不存在: {path}"
    if p.stat().st_size > 1024 * 1024:  # 1MB 限制
        return "文件过大（超过 1MB），无法读取"
    return p.read_text(encoding=encoding)


@registry.register(
    name="write_file",
    description="将内容写入指定路径的文件",
    category="文件",
    parameters=[
        ToolParameter(name="path", type=ParameterType.STRING, description="文件路径", required=True),
        ToolParameter(name="content", type=ParameterType.STRING, description="写入内容", required=True),
    ],
)
def write_file(path: str, content: str) -> str:
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入 {len(content)} 个字符到 {path}"


@registry.register(
    name="list_directory",
    description="列出目录下的文件和子目录",
    category="文件",
    parameters=[
        ToolParameter(name="path", type=ParameterType.STRING, description="目录路径", default="."),
    ],
)
def list_directory(path: str = ".") -> list[str]:
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return [f"目录不存在: {path}"]
    items = []
    for item in sorted(p.iterdir()):
        prefix = "📁" if item.is_dir() else "📄"
        items.append(f"{prefix} {item.name}")
    return items


@registry.register(
    name="python_execute",
    description="在安全沙箱中执行 Python 代码并返回结果",
    category="开发",
    parameters=[
        ToolParameter(name="code", type=ParameterType.STRING, description="Python 代码", required=True),
    ],
    timeout=10,
)
def python_execute(code: str) -> str:
    """安全执行 Python 代码"""
    import io
    import contextlib

    safe_builtins = {
        "print": print, "len": len, "str": str, "int": int, "float": float,
        "bool": bool, "list": list, "dict": dict, "tuple": tuple, "set": set,
        "range": range, "enumerate": enumerate, "zip": zip, "map": map,
        "filter": filter, "sorted": sorted, "reversed": reversed,
        "isinstance": isinstance, "hasattr": hasattr, "getattr": getattr,
        "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
    }

    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            exec(code, {"__builtins__": safe_builtins})
        output = stdout.getvalue()
        return output if output else "代码执行完成（无输出）"
    except Exception as e:
        return f"执行错误: {e}"

"""Agent 编排器 — ReAct 循环核心，支持真正的流式输出"""

import json
import logging
from datetime import datetime
from typing import AsyncIterator
from models import Message, ModelResponse, ToolResult
from tool_registry import ToolRegistry
from model_router import ModelRouter

logger = logging.getLogger(__name__)


# 系统提示词模板
SYSTEM_PROMPT_TEMPLATE = """你是一个智能助手，可以通过调用工具来完成用户的任务。

## 可用工具
你可以调用以下工具: {tool_list}

## 工作原则
1. 先理解用户意图，再选择合适的工具
2. 如果需要多个步骤，按顺序逐步执行
3. 每次只调用必要的工具，避免冗余操作
4. 如果工具返回错误，尝试修复或告知用户
5. 最终回复要简洁、准确、有帮助
6. 使用 Markdown 格式美化输出（代码块、列表、粗体等）

## 工具分类
- **系统**: 时间、计算等基础工具
- **文件**: 文件读写、目录操作
- **网络**: HTTP 请求
- **数据**: JSON 处理、文本分析
- **搜索**: 网页搜索、知识库查询
- **开发**: 代码执行

{extra_context}
"""


class AgentOrchestrator:
    """Agent 编排器 — 实现 Think-Act-Observe 循环，支持真正的流式输出"""

    def __init__(
        self,
        model_router: ModelRouter,
        tool_registry: ToolRegistry,
        config: dict,
    ):
        self.router = model_router
        self.tools = tool_registry
        self.config = config.get("agent", {})
        self.max_steps = self.config.get("max_steps", 10)
        self.system_prompt = self.config.get("system_prompt", "")
        self.db = None  # 由 api.py 注入

    def set_database(self, db):
        """注入数据库实例用于记录工具调用日志"""
        self.db = db

    def _build_system_prompt(self, extra_context: str = "") -> str:
        """构建系统提示词"""
        tool_list = ", ".join([t.name for t in self.tools.list_tools()])
        return SYSTEM_PROMPT_TEMPLATE.format(
            tool_list=tool_list,
            extra_context=extra_context or self.system_prompt,
        )

    async def run(
        self,
        user_input: str,
        conversation_id: str = None,
        model: str = None,
        history: list[Message] = None,
    ) -> list[Message]:
        """
        同步执行 ReAct 循环

        流程：
        1. 构建消息列表（系统提示 + 历史 + 用户输入）
        2. 获取可用工具定义
        3. 循环：调用模型 → 有工具调用则执行 → 将结果加入上下文 → 继续
        4. 返回最终消息列表
        """
        model = model or self.config.get("default_model", "deepseek-chat")
        messages = []

        # 系统提示
        system_content = self._build_system_prompt()
        messages.append({"role": "system", "content": system_content})

        # 历史消息
        if history:
            for msg in history[-20:]:  # 最近 20 条
                if msg.role == "user":
                    messages.append({"role": "user", "content": msg.content})
                elif msg.role == "assistant":
                    messages.append({"role": "assistant", "content": msg.content})

        # 用户输入
        messages.append({"role": "user", "content": user_input})

        # 获取工具定义
        tool_schemas = self.tools.to_function_calls()

        # 结果消息
        result_messages = []

        # ReAct 循环
        for step in range(self.max_steps):
            logger.info(f"Agent 步骤 {step + 1}/{self.max_steps}")

            # 调用模型
            response = await self.router.chat(
                messages=messages,
                model=model,
                tools=tool_schemas if tool_schemas else None,
                temperature=self.config.get("temperature", 0.7),
                max_tokens=self.config.get("max_tokens", 4096),
            )

            if response.tool_calls:
                # 模型请求调用工具
                assistant_msg = Message(
                    role="assistant",
                    content=response.content or "",
                    tool_call=response.tool_calls[0],
                )
                result_messages.append(assistant_msg)

                # 将助手消息加入上下文
                messages.append({
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": response.tool_calls,
                })

                # 执行所有工具调用
                for tool_call in response.tool_calls:
                    func = tool_call["function"]
                    tool_name = func["name"]
                    tool_args = json.loads(func["arguments"])

                    logger.info(f"调用工具: {tool_name}({tool_args})")

                    # 执行工具
                    result = await self.tools.execute(tool_name, tool_args)

                    # 记录工具调用日志
                    if self.db and conversation_id:
                        self.db.log_tool_call(
                            conversation_id=conversation_id,
                            tool_name=tool_name,
                            arguments=tool_args,
                            result=result.model_dump(),
                            success=result.success,
                            execution_time=result.execution_time,
                        )

                    # 构造工具结果消息
                    tool_msg = Message(
                        role="tool",
                        content=json.dumps(result.model_dump(), ensure_ascii=False, default=str),
                        tool_result={
                            "tool_call_id": tool_call["id"],
                            "name": tool_name,
                            "result": result.model_dump(),
                        },
                    )
                    result_messages.append(tool_msg)

                    # 将工具结果加入上下文
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(result.model_dump(), ensure_ascii=False, default=str),
                    })

            else:
                # 模型返回最终答案
                final_msg = Message(
                    role="assistant",
                    content=response.content or "",
                    metadata={
                        "steps": step + 1,
                        "model": response.model,
                        "usage": response.usage,
                    },
                )
                result_messages.append(final_msg)
                return result_messages

        # 超过最大步数
        result_messages.append(Message(
            role="assistant",
            content=f"⚠️ 已达到最大推理步数（{self.max_steps}），请尝试简化任务。",
        ))
        return result_messages

    async def run_stream(
        self,
        user_input: str,
        conversation_id: str = None,
        model: str = None,
        history: list[Message] = None,
    ) -> AsyncIterator[Message]:
        """
        流式执行 ReAct 循环 — 逐 token 输出

        流程：
        1. 构建消息列表
        2. 流式调用模型
        3. 如果是工具调用，执行工具并继续
        4. 如果是文本，逐 token 返回
        """
        model = model or self.config.get("default_model", "deepseek-chat")
        messages = []

        # 系统提示
        system_content = self._build_system_prompt()
        messages.append({"role": "system", "content": system_content})

        # 历史消息
        if history:
            for msg in history[-20:]:
                if msg.role == "user":
                    messages.append({"role": "user", "content": msg.content})
                elif msg.role == "assistant":
                    messages.append({"role": "assistant", "content": msg.content})

        # 用户输入
        messages.append({"role": "user", "content": user_input})

        # 获取工具定义
        tool_schemas = self.tools.to_function_calls()

        # ReAct 循环
        for step in range(self.max_steps):
            logger.info(f"Agent 流式步骤 {step + 1}/{self.max_steps}")

            full_content = ""
            tool_calls_data = []
            usage_data = {}

            # 流式调用模型
            async for chunk in self.router.chat_stream(
                messages=messages,
                model=model,
                tools=tool_schemas if tool_schemas else None,
                temperature=self.config.get("temperature", 0.7),
                max_tokens=self.config.get("max_tokens", 4096),
            ):
                if chunk["type"] == "token":
                    # 流式返回 token
                    full_content += chunk["content"]
                    yield Message(
                        role="assistant",
                        content=chunk["content"],
                        metadata={"type": "token", "step": step + 1},
                    )
                elif chunk["type"] == "done":
                    # 流结束，获取完整响应
                    response_data = chunk["response"]
                    tool_calls_data = response_data.get("tool_calls") or []
                    usage_data = response_data.get("usage", {})

            if tool_calls_data:
                # 模型请求调用工具
                # 返回工具调用状态
                yield Message(
                    role="assistant",
                    content="",
                    tool_call=tool_calls_data[0],
                    metadata={"type": "tool_call", "step": step + 1},
                )

                # 将助手消息加入上下文
                messages.append({
                    "role": "assistant",
                    "content": full_content,
                    "tool_calls": tool_calls_data,
                })

                # 执行所有工具调用
                for tool_call in tool_calls_data:
                    func = tool_call["function"]
                    tool_name = func["name"]
                    tool_args = json.loads(func["arguments"])

                    logger.info(f"调用工具: {tool_name}({tool_args})")

                    # 执行工具
                    result = await self.tools.execute(tool_name, tool_args)

                    # 记录工具调用日志
                    if self.db and conversation_id:
                        self.db.log_tool_call(
                            conversation_id=conversation_id,
                            tool_name=tool_name,
                            arguments=tool_args,
                            result=result.model_dump(),
                            success=result.success,
                            execution_time=result.execution_time,
                        )

                    # 返回工具执行结果
                    yield Message(
                        role="tool",
                        content=json.dumps(result.model_dump(), ensure_ascii=False, default=str),
                        tool_result={
                            "tool_call_id": tool_call["id"],
                            "name": tool_name,
                            "result": result.model_dump(),
                        },
                        metadata={"type": "tool_result", "step": step + 1},
                    )

                    # 将工具结果加入上下文
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(result.model_dump(), ensure_ascii=False, default=str),
                    })

            else:
                # 模型返回最终答案
                yield Message(
                    role="assistant",
                    content="",
                    metadata={
                        "type": "done",
                        "steps": step + 1,
                        "model": model,
                        "usage": usage_data,
                    },
                )
                return

        # 超过最大步数
        yield Message(
            role="assistant",
            content=f"⚠️ 已达到最大推理步数（{self.max_steps}），请尝试简化任务。",
            metadata={"type": "max_steps_reached"},
        )

    async def generate_title(self, user_input: str, model: str = None) -> str:
        """自动生成对话标题"""
        model = model or self.config.get("default_model", "deepseek-chat")
        try:
            response = await self.router.chat(
                messages=[
                    {"role": "system", "content": "你是一个标题生成器。根据用户的第一条消息，生成一个简短（10字以内）的对话标题。只返回标题，不要其他内容。"},
                    {"role": "user", "content": user_input},
                ],
                model=model,
                temperature=0.3,
                max_tokens=50,
            )
            title = response.content.strip().strip('"').strip("'")
            return title[:20] if title else user_input[:20]
        except Exception:
            return user_input[:20]

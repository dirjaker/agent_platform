"""Agent 编排器 — ReAct 循环核心"""

import json
import logging
from datetime import datetime
from models import Message, ModelResponse, ToolResult
from tool_registry import ToolRegistry
from model_router import ModelRouter

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """Agent 编排器 — 实现 Think-Act-Observe 循环"""

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

    async def run(
        self,
        user_input: str,
        conversation_id: str = None,
        model: str = None,
        history: list[Message] = None,
    ) -> list[Message]:
        """
        执行 ReAct 循环

        流程：
        1. 构建消息列表（系统提示 + 历史 + 用户输入）
        2. 获取可用工具定义
        3. 循环：调用模型 → 有工具调用则执行 → 将结果加入上下文 → 继续
        4. 返回最终消息列表
        """
        model = model or self.config.get("default_model", "deepseek-chat")
        messages = []

        # 系统提示
        tool_list = ", ".join([t.name for t in self.tools.list_tools()])
        system_content = f"""{self.system_prompt}

## 可用工具
你可以调用以下工具: {tool_list}

## 工作原则
1. 先理解用户意图，再选择合适的工具
2. 如果需要多个步骤，按顺序逐步执行
3. 每次只调用必要的工具
4. 最终回复要简洁、准确、有帮助
"""
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

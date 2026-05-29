"""模型适配器 — 统一接口对接不同模型提供商，支持真正的流式输出"""

import httpx
import json
import logging
from abc import ABC, abstractmethod
from typing import AsyncIterator
from models import ModelResponse

logger = logging.getLogger(__name__)


class BaseModelAdapter(ABC):
    """模型适配器基类"""

    def __init__(self, name: str, api_key: str, base_url: str):
        self.name = name
        self.api_key = api_key
        self.base_url = base_url

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
    ) -> ModelResponse:
        pass

    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[dict]:
        """流式输出 — 默认实现：调用 chat 并将结果分块返回"""
        response = await self.chat(
            messages=messages, model=model, tools=tools,
            temperature=temperature, max_tokens=max_tokens, stream=False,
        )
        # 模拟流式输出：逐字符返回
        if response.content:
            for char in response.content:
                yield {"type": "token", "content": char}
        yield {"type": "done", "response": response.model_dump()}


class DeepSeekAdapter(BaseModelAdapter):
    """DeepSeek 模型适配器 — 支持真正的流式输出"""

    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com"):
        super().__init__("deepseek", api_key, base_url)

    async def chat(
        self,
        messages: list[dict],
        model: str = "deepseek-chat",
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
    ) -> ModelResponse:
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json=body, headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]

        return ModelResponse(
            content=message.get("content"),
            tool_calls=message.get("tool_calls"),
            usage=data.get("usage", {}),
            model=data.get("model", model),
            finish_reason=choice.get("finish_reason", ""),
        )

    async def chat_stream(
        self,
        messages: list[dict],
        model: str = "deepseek-chat",
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[dict]:
        """DeepSeek 真正的流式输出"""
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            body["tools"] = tools

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        full_content = ""
        tool_calls_data = []
        usage_data = {}

        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=body, headers=headers,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data_str)
                        delta = chunk["choices"][0].get("delta", {})

                        # 处理文本 token
                        if "content" in delta and delta["content"]:
                            token = delta["content"]
                            full_content += token
                            yield {"type": "token", "content": token}

                        # 处理工具调用（增量）
                        if "tool_calls" in delta:
                            for tc in delta["tool_calls"]:
                                idx = tc.get("index", 0)
                                while len(tool_calls_data) <= idx:
                                    tool_calls_data.append({
                                        "id": "", "type": "function",
                                        "function": {"name": "", "arguments": ""}
                                    })
                                if "id" in tc:
                                    tool_calls_data[idx]["id"] = tc["id"]
                                if "function" in tc:
                                    if "name" in tc["function"]:
                                        tool_calls_data[idx]["function"]["name"] = tc["function"]["name"]
                                    if "arguments" in tc["function"]:
                                        tool_calls_data[idx]["function"]["arguments"] += tc["function"]["arguments"]

                        # 处理 usage
                        if "usage" in chunk:
                            usage_data = chunk["usage"]

                    except json.JSONDecodeError:
                        continue

        # 返回最终结果
        response = ModelResponse(
            content=full_content if full_content else None,
            tool_calls=tool_calls_data if tool_calls_data else None,
            usage=usage_data,
            model=model,
            finish_reason="stop",
        )
        yield {"type": "done", "response": response.model_dump()}


class OpenRouterAdapter(BaseModelAdapter):
    """OpenRouter 模型适配器"""

    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api/v1"):
        super().__init__("openrouter", api_key, base_url)

    async def chat(
        self,
        messages: list[dict],
        model: str = "google/gemini-2.0-flash-001",
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
    ) -> ModelResponse:
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://agent-platform.local",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json=body, headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]

        return ModelResponse(
            content=message.get("content"),
            tool_calls=message.get("tool_calls"),
            usage=data.get("usage", {}),
            model=data.get("model", model),
            finish_reason=choice.get("finish_reason", ""),
        )

    async def chat_stream(
        self,
        messages: list[dict],
        model: str = "google/gemini-2.0-flash-001",
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[dict]:
        """OpenRouter 流式输出"""
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            body["tools"] = tools

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://agent-platform.local",
            "Content-Type": "application/json",
        }

        full_content = ""
        tool_calls_data = []
        usage_data = {}

        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=body, headers=headers,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data_str)
                        delta = chunk["choices"][0].get("delta", {})

                        if "content" in delta and delta["content"]:
                            token = delta["content"]
                            full_content += token
                            yield {"type": "token", "content": token}

                        if "tool_calls" in delta:
                            for tc in delta["tool_calls"]:
                                idx = tc.get("index", 0)
                                while len(tool_calls_data) <= idx:
                                    tool_calls_data.append({
                                        "id": "", "type": "function",
                                        "function": {"name": "", "arguments": ""}
                                    })
                                if "id" in tc:
                                    tool_calls_data[idx]["id"] = tc["id"]
                                if "function" in tc:
                                    if "name" in tc["function"]:
                                        tool_calls_data[idx]["function"]["name"] = tc["function"]["name"]
                                    if "arguments" in tc["function"]:
                                        tool_calls_data[idx]["function"]["arguments"] += tc["function"]["arguments"]

                        if "usage" in chunk:
                            usage_data = chunk["usage"]

                    except json.JSONDecodeError:
                        continue

        response = ModelResponse(
            content=full_content if full_content else None,
            tool_calls=tool_calls_data if tool_calls_data else None,
            usage=usage_data,
            model=model,
            finish_reason="stop",
        )
        yield {"type": "done", "response": response.model_dump()}


class OpenAICompatibleAdapter(BaseModelAdapter):
    """通用 OpenAI 兼容适配器（适用于本地模型、vLLM 等）"""

    def __init__(self, name: str, api_key: str = "EMPTY", base_url: str = "http://localhost:8000"):
        super().__init__(name, api_key, base_url)

    async def chat(
        self,
        messages: list[dict],
        model: str = "default",
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
    ) -> ModelResponse:
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.base_url}/v1/chat/completions",
                json=body, headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]

        return ModelResponse(
            content=message.get("content"),
            tool_calls=message.get("tool_calls"),
            usage=data.get("usage", {}),
            model=data.get("model", model),
            finish_reason=choice.get("finish_reason", ""),
        )

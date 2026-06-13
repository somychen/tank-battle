from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelInfo:
    """AI 模型信息"""
    id: str
    display_name: str = ""
    provider: str = ""  # ollama / openai / claude / qwen / custom

    def __post_init__(self):
        if not self.display_name:
            self.display_name = self.id


@dataclass
class RawToolCall:
    """AI 返回的工具调用（执行前）"""
    id: str
    name: str
    arguments: dict


@dataclass
class ChatMessage:
    """聊天消息 — 支持多轮 Function Calling"""
    role: str  # system / user / assistant / tool
    content: str
    # Function Calling 扩展字段
    tool_calls: list[RawToolCall] | None = None
    tool_call_id: str = ""
    name: str = ""


@dataclass
class ChatResponse:
    """AI 聊天响应"""
    content: str
    model: str
    provider: str
    usage: dict[str, int] | None = None  # prompt_tokens, completion_tokens
    # Function Calling
    tool_calls: list[RawToolCall] | None = None
    tool_results: list[dict] | None = None  # [{name, arguments, result, success}]


class AIProvider(ABC):
    """所有 AI Provider 必须实现的接口"""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider 唯一名称"""
        ...

    @property
    def supports_function_calling(self) -> bool:
        """是否支持 Function Calling"""
        return False

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """列出可用模型"""
        ...

    @abstractmethod
    async def chat(self, messages: list[ChatMessage], model: str,
                   system_prompt: str = "", tools: list[dict] | None = None) -> ChatResponse:
        """发送聊天请求，返回完整响应"""
        ...

    @abstractmethod
    async def check_health(self) -> bool:
        """检查 Provider 连接是否正常"""
        ...

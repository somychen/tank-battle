"""工具系统基础数据结构"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolParameter:
    """工具参数定义"""
    name: str
    type: str  # "string" | "number" | "boolean"
    description: str
    required: bool = True
    enum: list[str] | None = None

    def to_json_schema(self) -> dict:
        schema: dict = {"type": self.type, "description": self.description}
        if self.enum:
            schema["enum"] = self.enum
        return schema


@dataclass
class ToolDefinition:
    """工具定义"""
    name: str
    description: str
    parameters: list[ToolParameter] = field(default_factory=list)

    def to_openai_function(self) -> dict:
        """转换为 OpenAI function calling 格式"""
        props = {}
        required = []
        for p in self.parameters:
            props[p.name] = p.to_json_schema()
            if p.required:
                required.append(p.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        }


@dataclass
class ToolResult:
    """工具执行结果"""
    call_id: str
    name: str
    arguments: dict
    result: str = ""
    success: bool = True
    error: str = ""


class BaseTool(ABC):
    """所有工具必须实现的接口"""

    @abstractmethod
    def get_definition(self) -> ToolDefinition:
        """返回工具定义"""
        ...

    @abstractmethod
    async def execute(self, arguments: dict) -> str:
        """执行工具，返回结果字符串"""
        ...

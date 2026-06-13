"""工具注册表"""

from __future__ import annotations

from .base import BaseTool, ToolDefinition


class ToolRegistry:
    """工具注册表 — 管理所有可用工具"""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册一个工具"""
        name = tool.get_definition().name
        self._tools[name] = tool

    def get(self, name: str) -> BaseTool | None:
        """获取指定名称的工具"""
        return self._tools.get(name)

    def get_all_definitions(self) -> list[ToolDefinition]:
        """获取所有工具定义"""
        return [t.get_definition() for t in self._tools.values()]

    def get_openai_tools(self) -> list[dict]:
        """获取 OpenAI function calling 格式的工具列表"""
        return [t.get_definition().to_openai_function() for t in self._tools.values()]


# 全局单例
_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """获取工具注册表单例（延迟注册默认工具）"""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        # 延迟导入避免循环引用
        from .web_search import WebSearchTool
        from .weather import WeatherTool
        from .datetime_tools import DateTimeTool
        from .translator import TranslatorTool

        _registry.register(WebSearchTool())
        _registry.register(WeatherTool())
        _registry.register(DateTimeTool())
        _registry.register(TranslatorTool())
    return _registry

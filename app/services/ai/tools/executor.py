"""工具执行器"""

from __future__ import annotations

from app.services.ai.base import RawToolCall
from app.services.ai.openai_provider import sanitize_surrogates
from .base import ToolResult
from .registry import get_tool_registry


async def execute_tool_calls(tool_calls: list[RawToolCall]) -> list[ToolResult]:
    """执行一组工具调用，返回结果列表"""
    registry = get_tool_registry()
    results: list[ToolResult] = []

    for tc in tool_calls:
        tool = registry.get(tc.name)
        if tool is None:
            results.append(ToolResult(
                call_id=tc.id,
                name=tc.name,
                arguments=tc.arguments,
                success=False,
                error=f"未知工具: {tc.name}",
            ))
            continue

        try:
            result_text = await tool.execute(tc.arguments)
            results.append(ToolResult(
                call_id=tc.id,
                name=tc.name,
                arguments=tc.arguments,
                result=sanitize_surrogates(result_text),
                success=True,
            ))
        except Exception as e:
            results.append(ToolResult(
                call_id=tc.id,
                name=tc.name,
                arguments=tc.arguments,
                success=False,
                error=sanitize_surrogates(str(e)),
            ))

    return results

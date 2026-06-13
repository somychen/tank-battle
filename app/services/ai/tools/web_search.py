"""全网搜索工具 — 使用 DuckDuckGo (免费，无需 API Key)"""

from __future__ import annotations

from .base import BaseTool, ToolDefinition, ToolParameter


class WebSearchTool(BaseTool):

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="web_search",
            description="搜索互联网获取最新信息。当需要查找实时信息、新闻、资料时使用此工具。返回相关网页的标题、URL和摘要。",
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="搜索关键词，使用简洁明确的关键词以获得最佳结果",
                ),
            ],
        )

    async def execute(self, arguments: dict) -> str:
        query = arguments.get("query", "")
        if not query.strip():
            return "错误：搜索关键词不能为空"

        try:
            from ddgs import DDGS

            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=5):
                    title = r.get("title", "")
                    url = r.get("href", "")
                    body = r.get("body", "")
                    results.append(f"{title}\n  {url}\n  {body}")

            if not results:
                return f"未找到关于「{query}」的搜索结果。"

            output = "\n\n".join(results)
            # 截断到 2000 字符以内
            if len(output) > 2000:
                output = output[:1997] + "..."

            return output

        except ImportError:
            return "错误：搜索功能不可用（缺少 duckduckgo_search 库）。"
        except Exception as e:
            return f"搜索失败: {str(e)}"

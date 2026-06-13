"""天气查询工具 — 使用 wttr.in (免费，无需 API Key)"""

from __future__ import annotations

import urllib.parse

from .base import BaseTool, ToolDefinition, ToolParameter


class WeatherTool(BaseTool):

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_weather",
            description="查询指定城市的实时天气信息，包括温度、天气状况、湿度、风力等。当用户询问天气相关问题时使用。",
            parameters=[
                ToolParameter(
                    name="city",
                    type="string",
                    description="城市名称，如 北京、上海、深圳、Tokyo、London",
                ),
            ],
        )

    async def execute(self, arguments: dict) -> str:
        city = arguments.get("city", "")
        if not city.strip():
            return "错误：城市名称不能为空"

        try:
            import httpx

            encoded = urllib.parse.quote(city)
            url = f"https://wttr.in/{encoded}?format=%l:+%C+%t+%h+%w&lang=zh"

            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.get(url, headers={"User-Agent": "curl/8.0"})
                if resp.status_code != 200:
                    return f"天气查询失败: HTTP {resp.status_code}"

                text = resp.text.strip()
                if not text or "not found" in text.lower() or "抱歉" in text:
                    return f"未找到城市「{city}」的天气信息，请检查城市名称。"

                # 格式化输出
                result = f"{city} 天气: {text}"
                return result

        except ImportError:
            return "错误：天气功能不可用（缺少 httpx 库）。"
        except Exception as e:
            return f"天气查询失败: {str(e)}"

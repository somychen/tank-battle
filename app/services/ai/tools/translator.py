"""翻译工具 — 利用现有AI模型翻译（免费）"""

from __future__ import annotations

from .base import BaseTool, ToolDefinition, ToolParameter


class TranslatorTool(BaseTool):

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="translate",
            description="将文本翻译成指定语言。当用户需要翻译内容时使用此工具。",
            parameters=[
                ToolParameter(
                    name="text",
                    type="string",
                    description="需要翻译的文本",
                ),
                ToolParameter(
                    name="target_language",
                    type="string",
                    description="目标语言",
                    enum=["中文", "English", "日本語", "한국어", "Français", "Deutsch"],
                ),
            ],
        )

    async def execute(self, arguments: dict) -> str:
        text = arguments.get("text", "")
        target = arguments.get("target_language", "中文")

        if not text.strip():
            return "错误：翻译文本不能为空"

        # 优先使用 Google Translate 免费端点（无需 AI）
        try:
            result = await self._google_translate(text, target)
            if result and "翻译失败" not in result:
                return result
        except Exception:
            pass

        # 备用：返回提示让 AI 直接翻译
        return (
            f"翻译请求: 将以下文本翻译为{target}\n"
            f"原文: {text}\n"
            f"（自动翻译服务暂时不可用，请AI直接完成翻译）"
        )

    @staticmethod
    async def _google_translate(text: str, target: str) -> str:
        import httpx
        import urllib.parse

        lang_map = {
            "中文": "zh-CN",
            "English": "en",
            "日本語": "ja",
            "한국어": "ko",
            "Français": "fr",
            "Deutsch": "de",
        }
        target_code = lang_map.get(target, "zh-CN")

        encoded = urllib.parse.quote(text, safe="")
        url = (
            f"https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl=auto&tl={target_code}&dt=t&q={encoded}"
        )

        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return f"翻译失败: HTTP {resp.status_code}"

            data = resp.json()
            # Google Translate 返回格式: [[["translated text", "original", ...]], ...]
            if isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                parts = []
                for segment in data[0]:
                    if isinstance(segment, list) and len(segment) > 0:
                        parts.append(str(segment[0]))
                result = "".join(parts)
                return f"[{target}] {result}"

            return "翻译失败: 无法解析响应"

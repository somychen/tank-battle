from __future__ import annotations

import json
import logging

from openai import AsyncOpenAI

from app.services.ai.base import AIProvider, ModelInfo, ChatMessage, ChatResponse, RawToolCall

logger = logging.getLogger(__name__)


def sanitize_surrogates(text: str) -> str:
    """移除字符串中的孤立代理对字符（U+D800-U+DFFF），防止 UTF-8 编码失败"""
    if not text:
        return text
    return ''.join(c for c in text if not ('\uD800' <= c <= '\uDFFF'))


class OpenAIProvider(AIProvider):
    """OpenAI 及兼容 API Provider（同时支持 通义千问、DeepSeek 等）"""

    def __init__(self, api_key: str, base_url: str | None = None,
                 provider_name: str = "openai"):
        self._api_key = api_key
        self._base_url = base_url
        self._provider_name = provider_name
        self._client: AsyncOpenAI | None = None
        self._cached_models: list[ModelInfo] | None = None
        self._fallback_models: list[str] = []

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def supports_function_calling(self) -> bool:
        return True

    def _ensure_client(self):
        if self._client is None:
            kwargs = {"api_key": self._api_key, "timeout": 30.0, "max_retries": 0}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = AsyncOpenAI(**kwargs)

    async def close(self):
        if self._client:
            await self._client.close()
            self._client = None

    async def check_health(self) -> bool:
        try:
            models = await self.list_models()
            return len(models) > 0
        except Exception:
            return False

    async def list_models(self) -> list[ModelInfo]:
        if self._cached_models is not None:
            return self._cached_models

        self._ensure_client()
        try:
            resp = await self._client.models.list()
            models = []
            for m in resp.data:
                model_id = m.id
                models.append(ModelInfo(
                    id=model_id,
                    display_name=model_id,
                    provider=self._provider_name,
                ))
            self._cached_models = models
            return models
        except Exception:
            if self._fallback_models:
                self._cached_models = [
                    ModelInfo(id=m, display_name=m, provider=self._provider_name)
                    for m in self._fallback_models
                ]
                return self._cached_models
            return []

    def set_models_override(self, model_ids: list[str]):
        self._fallback_models = model_ids

    async def chat(self, messages: list[ChatMessage], model: str,
                   system_prompt: str = "", tools: list[dict] | None = None) -> ChatResponse:
        self._ensure_client()

        built_messages = self._build_messages(messages, system_prompt)

        kwargs: dict = {
            "model": model,
            "messages": built_messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        resp = await self._client.chat.completions.create(**kwargs)

        choice = resp.choices[0]
        content = sanitize_surrogates(choice.message.content or "")
        usage = resp.usage

        # 解析 tool_calls
        tool_calls: list[RawToolCall] | None = None
        if choice.message.tool_calls:
            tool_calls = []
            for tc in choice.message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Failed to parse tool call arguments: %s", tc.function.arguments)
                    continue
                tool_calls.append(RawToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        return ChatResponse(
            content=content,
            model=model,
            provider=self._provider_name,
            usage={
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
            },
            tool_calls=tool_calls,
        )

    @staticmethod
    def _build_messages(messages: list[ChatMessage], system_prompt: str) -> list[dict]:
        """构建 OpenAI 兼容的消息列表，支持 tool 角色和 tool_calls"""
        built: list[dict] = []
        if system_prompt:
            built.append({"role": "system", "content": system_prompt})

        for m in messages:
            msg: dict = {"role": m.role, "content": m.content}

            if m.role == "assistant" and m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in m.tool_calls
                ]

            if m.role == "tool":
                msg["tool_call_id"] = m.tool_call_id
                if m.name:
                    msg["name"] = m.name

            built.append(msg)

        return built

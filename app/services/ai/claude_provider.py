from __future__ import annotations

from anthropic import AsyncAnthropic

from app.services.ai.base import AIProvider, ModelInfo, ChatMessage, ChatResponse
from app.services.ai.openai_provider import sanitize_surrogates


class ClaudeProvider(AIProvider):
    """Anthropic Claude Provider"""

    def __init__(self, api_key: str, base_url: str | None = None):
        self._api_key = api_key
        self._base_url = base_url
        self._client: AsyncAnthropic | None = None
        self._cached_models: list[ModelInfo] = []

    @property
    def provider_name(self) -> str:
        return "claude"

    def _ensure_client(self):
        if self._client is None:
            kwargs = {"api_key": self._api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = AsyncAnthropic(**kwargs)

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
        if self._cached_models:
            return self._cached_models
        return self._cached_models

    def set_models_override(self, model_ids: list[str]):
        self._cached_models = [
            ModelInfo(id=mid, display_name=mid, provider=self.provider_name)
            for mid in model_ids
        ]

    async def chat(self, messages: list[ChatMessage], model: str,
                   system_prompt: str = "", tools: list[dict] | None = None) -> ChatResponse:
        self._ensure_client()

        # Claude 使用 system 参数，不通过 messages 传递 system 角色
        user_messages = []
        for m in messages:
            if m.role == "system":
                if not system_prompt:
                    system_prompt = m.content
            else:
                user_messages.append({"role": m.role, "content": m.content})

        kwargs = {
            "model": model,
            "max_tokens": 4096,
            "messages": user_messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        resp = await self._client.messages.create(**kwargs)

        content = ""
        for block in resp.content:
            if block.type == "text":
                content += block.text
        content = sanitize_surrogates(content)

        return ChatResponse(
            content=content,
            model=model,
            provider=self.provider_name,
            usage={
                "prompt_tokens": resp.usage.input_tokens if resp.usage else 0,
                "completion_tokens": resp.usage.output_tokens if resp.usage else 0,
            },
        )

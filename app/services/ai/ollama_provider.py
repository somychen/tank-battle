from __future__ import annotations

import httpx

from app.services.ai.base import AIProvider, ModelInfo, ChatMessage, ChatResponse


class OllamaProvider(AIProvider):
    """Ollama 本地模型 Provider"""

    def __init__(self, base_url: str = "http://localhost:11434"):
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    @property
    def provider_name(self) -> str:
        return "ollama"

    async def _ensure_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(120))

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def check_health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[ModelInfo]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
                resp.raise_for_status()
                data = resp.json()
                models = []
                for m in data.get("models", []):
                    name = m.get("name", "")
                    models.append(ModelInfo(
                        id=name,
                        display_name=name,
                        provider=self.provider_name,
                    ))
                return models
        except Exception:
            return []

    async def chat(self, messages: list[ChatMessage], model: str,
                   system_prompt: str = "", tools: list[dict] | None = None) -> ChatResponse:
        await self._ensure_client()

        built_messages = []
        if system_prompt:
            built_messages.append({"role": "system", "content": system_prompt})
        for m in messages:
            built_messages.append({"role": m.role, "content": m.content})

        payload = {
            "model": model,
            "messages": built_messages,
            "stream": False,
        }

        resp = await self._client.post(
            f"{self._base_url}/api/chat",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

        content = data.get("message", {}).get("content", "")
        eval_count = data.get("eval_count", 0)
        prompt_eval_count = data.get("prompt_eval_count", 0)

        return ChatResponse(
            content=content,
            model=model,
            provider=self.provider_name,
            usage={
                "prompt_tokens": prompt_eval_count or 0,
                "completion_tokens": eval_count or 0,
            },
        )

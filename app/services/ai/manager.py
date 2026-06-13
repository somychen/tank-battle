from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.services.ai.base import AIProvider, ModelInfo, ChatMessage, ChatResponse
from app.services.ai.ollama_provider import OllamaProvider
from app.services.ai.openai_provider import OpenAIProvider
from app.services.ai.claude_provider import ClaudeProvider

# 配置文件路径
AI_CONFIG_PATH = Path(__file__).parent / "ai_config.json"


def _load_persisted_config() -> dict[str, Any]:
    """从持久化配置文件加载 AI 配置"""
    if AI_CONFIG_PATH.exists():
        try:
            with open(AI_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_persisted_config(config: dict[str, Any]):
    """保存 AI 配置到持久化文件"""
    AI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(AI_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


class AIManager:
    """AI 管理器：统一管理所有 Provider，提供模型列表和聊天接口"""

    def __init__(self):
        self._providers: dict[str, AIProvider] = {}
        self._initialized = False

    def initialize(self, config_override: dict[str, Any] | None = None):
        """初始化所有 Provider

        config_override: 可选的前端传入配置，合并入环境变量配置
        """
        # 合并配置：持久化文件 -> 环境变量 -> 前端传入
        persisted = _load_persisted_config()
        env_config = _load_env_config()
        merged = {**persisted, **env_config}
        if config_override:
            merged = {**merged, **config_override}

        self._providers.clear()

        # Ollama 本地
        if merged.get("ollama_enabled", "true") != "false":
            ollama_url = merged.get("ollama_base_url", "http://localhost:11434")
            self._providers["ollama"] = OllamaProvider(base_url=ollama_url)

        # OpenAI
        if merged.get("openai_enabled", "true") != "false":
            openai_key = merged.get("openai_api_key", "")
            if openai_key:
                openai_url = merged.get("openai_base_url", "https://api.openai.com/v1")
                provider = OpenAIProvider(
                    api_key=openai_key,
                    base_url=openai_url,
                    provider_name="openai",
                )
                openai_models = merged.get("openai_models", "gpt-4o-mini,gpt-4o")
                if openai_models and not openai_url.startswith("https://api.openai.com"):
                    provider.set_models_override(
                        [m.strip() for m in openai_models.split(",") if m.strip()]
                    )
                self._providers["openai"] = provider

        # Claude
        if merged.get("claude_enabled", "true") != "false":
            claude_key = merged.get("claude_api_key", "")
            if claude_key:
                claude_url = merged.get("claude_base_url", None)
                provider = ClaudeProvider(api_key=claude_key, base_url=claude_url or None)
                claude_models = merged.get("claude_models",
                                           "claude-3-5-haiku-20241022,claude-3-5-sonnet-20241022")
                if claude_models:
                    provider.set_models_override(
                        [m.strip() for m in claude_models.split(",") if m.strip()]
                    )
                self._providers["claude"] = provider

        # 通义千问 个人版
        if merged.get("qwen_enabled", "true") != "false":
            qwen_key = merged.get("qwen_api_key", "")
            if qwen_key:
                qwen_base = merged.get("qwen_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
                provider = OpenAIProvider(
                    api_key=qwen_key,
                    base_url=qwen_base,
                    provider_name="qwen",
                )
                qwen_models = merged.get("qwen_models", "qwen-turbo,qwen-plus,qwen-max,qwen2.5-7b-instruct,qwen2.5-14b-instruct,qwen2.5-32b-instruct,qwen2.5-72b-instruct,qwen2.5-coder-7b-instruct,qwen-long,qwq-32b")
                provider.set_models_override(
                    [m.strip() for m in qwen_models.split(",") if m.strip()]
                )
                self._providers["qwen"] = provider

        # 通义千问 团队版
        if merged.get("qwen_team_enabled", "true") != "false":
            qwt_key = merged.get("qwen_team_api_key", "")
            if qwt_key:
                qwt_base = merged.get("qwen_team_base_url", "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
                provider = OpenAIProvider(
                    api_key=qwt_key,
                    base_url=qwt_base,
                    provider_name="qwen-team",
                )
                qwt_models = merged.get("qwen_team_models", "qwen3.6-flash,qwen3.6-plus,qwen3.7-max")
                provider.set_models_override(
                    [m.strip() for m in qwt_models.split(",") if m.strip()]
                )
                self._providers["qwen-team"] = provider

        # 自定义 OpenAI 兼容 API
        if merged.get("custom_enabled", "true") != "false":
            custom_url = merged.get("custom_base_url", "")
            custom_key = merged.get("custom_api_key", "")
            if custom_url and custom_key:
                provider = OpenAIProvider(
                    api_key=custom_key,
                    base_url=custom_url,
                    provider_name="custom",
                )
                custom_models = merged.get("custom_models", "")
                if custom_models:
                    provider.set_models_override(
                        [m.strip() for m in custom_models.split(",") if m.strip()]
                    )
                self._providers["custom"] = provider

        self._initialized = True

    def is_initialized(self) -> bool:
        return self._initialized

    def get_providers(self) -> list[dict[str, Any]]:
        """获取所有已配置的 Provider 信息"""
        result = []
        provider_order = ["ollama", "openai", "claude", "qwen", "qwen-team", "custom"]
        for name in provider_order:
            if name in self._providers:
                result.append({
                    "name": name,
                    "label": _PROVIDER_LABELS.get(name, name),
                })
        return result

    async def list_all_models(self) -> list[dict[str, Any]]:
        """获取所有 Provider 的模型列表（并行，每个最多 3 秒超时）"""
        import asyncio

        async def _list_one(prov_name: str, provider) -> list[dict]:
            try:
                models = await asyncio.wait_for(
                    provider.list_models(), timeout=3.0
                )
                return [
                    {"id": m.id, "display_name": m.display_name, "provider": prov_name}
                    for m in models
                ]
            except (asyncio.TimeoutError, Exception):
                return []

        tasks = [_list_one(name, p) for name, p in self._providers.items()]
        results = await asyncio.gather(*tasks)
        all_models = []
        for r in results:
            all_models.extend(r)
        return all_models

    async def check_all_health(self) -> dict[str, bool]:
        """检查所有 Provider 健康状态（并行，每个最多 3 秒超时）"""
        import asyncio

        async def _check_one(name: str, provider) -> tuple[str, bool]:
            try:
                result = await asyncio.wait_for(
                    provider.check_health(), timeout=3.0
                )
                return name, result
            except (asyncio.TimeoutError, Exception):
                return name, False

        tasks = [_check_one(name, p) for name, p in self._providers.items()]
        results_list = await asyncio.gather(*tasks)
        return dict(results_list)

    async def chat(self, provider_name: str, model: str,
                   messages: list[ChatMessage],
                   system_prompt: str = "", tools: list[dict] | None = None) -> ChatResponse:
        """统一聊天接口"""
        provider = self._providers.get(provider_name)
        if not provider:
            raise ValueError(f"未知的 AI Provider: {provider_name}")
        return await provider.chat(
            messages=messages,
            model=model,
            system_prompt=system_prompt,
            tools=tools,
        )

    async def chat_with_tools(self, provider_name: str, model: str,
                              messages: list[ChatMessage],
                              system_prompt: str = "",
                              max_iterations: int = 5) -> ChatResponse:
        """带 Function Calling 的聊天接口 — AI 可自动调用工具多轮交互

        如果 Provider 不支持 Function Calling，则退化为普通 chat。
        每次 AI 返回 tool_calls 后自动执行工具并将结果反馈给 AI，
        直到 AI 不再调用工具或达到最大迭代次数。
        """
        provider = self._providers.get(provider_name)
        if not provider:
            raise ValueError(f"未知的 AI Provider: {provider_name}")

        # 不支持 Function Calling 的 Provider 直接退化为普通聊天
        if not provider.supports_function_calling:
            return await provider.chat(
                messages=messages,
                model=model,
                system_prompt=system_prompt,
            )

        from .tools.registry import get_tool_registry
        from .tools.executor import execute_tool_calls

        registry = get_tool_registry()
        tools = registry.get_openai_tools()
        if not tools:
            return await provider.chat(
                messages=messages,
                model=model,
                system_prompt=system_prompt,
            )

        current_messages = list(messages)  # 复制，避免修改原始消息
        all_tool_results: list[dict] = []
        last_response: ChatResponse | None = None

        for _ in range(max_iterations):
            response = await provider.chat(
                messages=current_messages,
                model=model,
                system_prompt=system_prompt,
                tools=tools,
            )
            last_response = response

            # AI 没有调用工具，返回最终结果
            if not response.tool_calls:
                if all_tool_results:
                    last_response.tool_results = all_tool_results
                return last_response

            # 执行工具调用
            tool_results = await execute_tool_calls(response.tool_calls)

            # 记录工具调用结果
            for tr in tool_results:
                all_tool_results.append({
                    "name": tr.name,
                    "arguments": tr.arguments,
                    "result": tr.result if tr.success else "",
                    "error": tr.error if not tr.success else "",
                    "success": tr.success,
                })

            # 添加 assistant 消息（含 tool_calls）
            current_messages.append(ChatMessage(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
            ))

            # 为每个工具调用添加 tool 角色消息
            for tr in tool_results:
                tool_content = tr.result if tr.success else f"工具调用错误: {tr.error}"
                current_messages.append(ChatMessage(
                    role="tool",
                    content=tool_content,
                    tool_call_id=tr.call_id,
                    name=tr.name,
                ))

        # 达到最大迭代次数，返回最后一次响应
        if all_tool_results:
            last_response.tool_results = all_tool_results
        return last_response

    @staticmethod
    def save_config(config: dict[str, Any]):
        """持久化保存 AI 配置"""
        _save_persisted_config(config)

    @staticmethod
    def get_config() -> dict[str, Any]:
        """获取当前持久化配置 + 环境变量配置"""
        persisted = _load_persisted_config()
        env_config = _load_env_config()
        return {**persisted, **env_config}


_PROVIDER_LABELS = {
    "ollama": "本地 (Ollama)",
    "openai": "OpenAI",
    "claude": "Claude",
    "qwen": "通义千问 (个人版)",
    "qwen-team": "通义千问 (团队版)",
    "custom": "自定义",
}


def _load_env_config() -> dict[str, Any]:
    """从环境变量加载 AI 配置"""
    config = {}
    env_map = {
        "SCRAPER_AI_OLLAMA_ENABLED": "ollama_enabled",
        "SCRAPER_AI_OLLAMA_BASE_URL": "ollama_base_url",
        "SCRAPER_AI_OPENAI_ENABLED": "openai_enabled",
        "SCRAPER_AI_OPENAI_API_KEY": "openai_api_key",
        "SCRAPER_AI_OPENAI_BASE_URL": "openai_base_url",
        "SCRAPER_AI_OPENAI_MODELS": "openai_models",
        "SCRAPER_AI_CLAUDE_ENABLED": "claude_enabled",
        "SCRAPER_AI_CLAUDE_API_KEY": "claude_api_key",
        "SCRAPER_AI_CLAUDE_BASE_URL": "claude_base_url",
        "SCRAPER_AI_CLAUDE_MODELS": "claude_models",
        "SCRAPER_AI_QWEN_ENABLED": "qwen_enabled",
        "SCRAPER_AI_QWEN_API_KEY": "qwen_api_key",
        "SCRAPER_AI_QWEN_BASE_URL": "qwen_base_url",
        "SCRAPER_AI_QWEN_MODELS": "qwen_models",
        "SCRAPER_AI_QWEN_TEAM_ENABLED": "qwen_team_enabled",
        "SCRAPER_AI_QWEN_TEAM_API_KEY": "qwen_team_api_key",
        "SCRAPER_AI_QWEN_TEAM_BASE_URL": "qwen_team_base_url",
        "SCRAPER_AI_QWEN_TEAM_MODELS": "qwen_team_models",
        "SCRAPER_AI_CUSTOM_ENABLED": "custom_enabled",
        "SCRAPER_AI_CUSTOM_BASE_URL": "custom_base_url",
        "SCRAPER_AI_CUSTOM_API_KEY": "custom_api_key",
        "SCRAPER_AI_CUSTOM_MODELS": "custom_models",
    }
    for env_key, config_key in env_map.items():
        val = os.environ.get(env_key, "")
        if val:
            config[config_key] = val
    return config


# 全局单例
_ai_manager: AIManager | None = None


def get_ai_manager() -> AIManager:
    global _ai_manager
    if _ai_manager is None:
        _ai_manager = AIManager()
    return _ai_manager

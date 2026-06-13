import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {
        "env_prefix": "SCRAPER_",
        "case_sensitive": False,
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    # 数据目录
    data_dir: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

    # 输出目录
    output_dir: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")

    # 请求超时（秒）
    request_timeout: int = 30
    connect_timeout: int = 10

    # 最大内容大小（MB）
    max_content_size_mb: int = 10

    # 重试次数（仅对网络错误）
    max_retries: int = 2

    # User-Agent
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )

    # 速率限制（每分钟每 IP 最大请求数）
    rate_limit_per_minute: int = 60

    # 缓存 TTL（秒），相同 URL 在此时间内复用缓存
    cache_ttl: int = 300

    # 提取内容最短长度（字符），低于此值视为提取失败
    min_content_length: int = 50

    # AI 集成配置
    ai_enabled: bool = True

    # Ollama 本地
    ai_ollama_enabled: bool = True
    ai_ollama_base_url: str = "http://localhost:11434"

    # OpenAI
    ai_openai_enabled: bool = True
    ai_openai_api_key: str = ""
    ai_openai_base_url: str = "https://api.openai.com/v1"
    ai_openai_models: str = "gpt-4o-mini,gpt-4o"

    # Anthropic Claude
    ai_claude_enabled: bool = True
    ai_claude_api_key: str = ""
    ai_claude_base_url: str = ""
    ai_claude_models: str = "claude-3-5-haiku-20241022,claude-3-5-sonnet-20241022"

    # 通义千问 个人版 (DashScope)
    ai_qwen_enabled: bool = True
    ai_qwen_api_key: str = ""
    ai_qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ai_qwen_models: str = "qwen-turbo,qwen-plus,qwen-max,qwen2.5-7b-instruct,qwen2.5-14b-instruct,qwen2.5-32b-instruct,qwen2.5-72b-instruct,qwen2.5-coder-7b-instruct,qwen-long,qwq-32b"

    # 通义千问 团队版 (DashScope 企业/团队)
    ai_qwen_team_enabled: bool = True
    ai_qwen_team_api_key: str = ""
    ai_qwen_team_base_url: str = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    ai_qwen_team_models: str = "qwen3.6-flash,qwen3.6-plus,qwen3.7-max"

    # 自定义 OpenAI 兼容 API
    ai_custom_enabled: bool = True
    ai_custom_base_url: str = ""
    ai_custom_api_key: str = ""
    ai_custom_models: str = ""


settings = Settings()

from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass

import httpx

from app.config import settings
from app.exceptions import (
    FetchError,
    FetchTimeoutError,
    ContentTooLargeError,
    UnsupportedContentError,
)
from app.utils import decode_html


@dataclass
class FetchResult:
    """抓取结果"""
    html: str              # 解码后的 HTML 字符串
    final_url: str         # 最终 URL（重定向后）
    status_code: int       # HTTP 状态码
    content_type: str      # Content-Type 响应头
    content_length: int    # 原始内容字节数


class Fetcher:
    """异步 HTTP 网页抓取服务"""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, FetchResult]] = {}

    async def fetch(self, url: str) -> FetchResult:
        # 检查缓存
        cached = self._get_cached(url)
        if cached:
            return cached

        max_size_bytes = settings.max_content_size_mb * 1024 * 1024

        last_error: Exception | None = None
        for attempt in range(settings.max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=settings.request_timeout,
                    follow_redirects=True,
                    max_redirects=10,
                    headers={"User-Agent": settings.user_agent},
                ) as client:
                    response = await client.get(url)
                    return await self._process_response(response, url, max_size_bytes)

            except httpx.TimeoutException as e:
                last_error = FetchTimeoutError(
                    detail=f"{url} 在 {settings.request_timeout} 秒内未响应"
                )
                if attempt < settings.max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise last_error

            except (httpx.ConnectError, httpx.RemoteProtocolError) as e:
                last_error = FetchError(
                    detail=f"无法连接到 {url}: {str(e)[:200]}"
                )
                if attempt < settings.max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise last_error

            except Exception as e:
                last_error = FetchError(detail=f"抓取失败: {str(e)[:200]}")
                if attempt < settings.max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise last_error

        raise last_error  # type: ignore

    async def _process_response(
        self, response: httpx.Response, url: str, max_size: int
    ) -> FetchResult:
        # 检查 HTTP 状态
        if response.status_code >= 400:
            raise FetchError(
                detail=f"目标服务器返回 HTTP {response.status_code}: {url}"
            )

        # 检查 Content-Type
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type.lower() and content_type:
            raise UnsupportedContentError(
                detail=f"内容类型为 {content_type}，仅支持 HTML 网页"
            )

        # 读取原始内容
        raw = response.read()
        if len(raw) > max_size:
            raise ContentTooLargeError(
                detail=f"网页内容 {len(raw) / 1024 / 1024:.1f}MB，超过 {settings.max_content_size_mb}MB 限制"
            )

        # 解码
        html = decode_html(raw, content_type)

        result = FetchResult(
            html=html,
            final_url=str(response.url),
            status_code=response.status_code,
            content_type=content_type,
            content_length=len(raw),
        )

        # 写入缓存
        self._set_cache(url, result)

        return result

    def _get_cached(self, url: str) -> FetchResult | None:
        if url in self._cache:
            ts, result = self._cache[url]
            if time.time() - ts < settings.cache_ttl:
                return result
            else:
                del self._cache[url]
        return None

    def _set_cache(self, url: str, result: FetchResult) -> None:
        self._cache[url] = (time.time(), result)
        # 清理过期缓存
        if len(self._cache) > 100:
            now = time.time()
            self._cache = {
                k: v for k, v in self._cache.items()
                if now - v[0] < settings.cache_ttl
            }

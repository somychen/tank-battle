from __future__ import annotations
import asyncio
from dataclasses import dataclass

from playwright.async_api import async_playwright, Browser, BrowserContext

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
    """抓取结果（与 services/fetcher.py 保持一致）"""
    html: str
    final_url: str
    status_code: int
    content_type: str = "text/html"
    content_length: int = 0


class BrowserFetcher:
    """基于 Playwright 的浏览器渲染抓取服务 - 用于 SPA 页面"""

    def __init__(self):
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._lock = asyncio.Lock()
        self._wait_time: float = 3.0
        self._headless: bool = True  # 默认无头

    async def _ensure_browser(self, headless: bool | None = None):
        """确保浏览器实例已启动。headless 参数变化时会重建浏览器。"""
        if headless is None:
            headless = self._headless

        # 如果 headless 模式变了，关掉旧浏览器
        if self._browser and self._browser.is_connected() and headless != self._headless:
            await self.close()

        if self._browser and self._browser.is_connected():
            return

        async with self._lock:
            if self._browser and self._browser.is_connected():
                return

            self._headless = headless
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=headless,
                args=[
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
            )
            self._context = await self._browser.new_context(
                user_agent=settings.user_agent,
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )

    def _parse_cookies(self, url: str, cookie_str: str) -> list[dict]:
        """解析 cookie 字符串为 Playwright cookie 对象列表
        
        Cookie 格式: name1=value1; name2=value2
        """
        domain = self._extract_domain(url)
        parsed_cookies = []
        
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" not in part:
                continue
            name, _, value = part.partition("=")
            name = name.strip()
            value = value.strip()
            if not name:
                continue
            parsed_cookies.append({
                "name": name,
                "value": value,
                "domain": f".{domain}",
                "path": "/",
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            })
        
        return parsed_cookies

    async def _set_cookies(self, url: str, cookie_str: str) -> None:
        """注入 cookies 到浏览器上下文（fetch 内部使用）"""
        cookies = self._parse_cookies(url, cookie_str)
        if cookies:
            await self._context.add_cookies(cookies)
    
    async def set_cookies_for_url(self, url: str, cookie_str: str) -> None:
        """预先设定 cookies，后续该域名下所有页面导航都会携带这些 cookies。"""
        await self._ensure_browser()
        cookies = self._parse_cookies(url, cookie_str)
        if cookies:
            await self._context.add_cookies(cookies)

    async def configure_mode(self, headless: bool) -> None:
        """配置浏览器可见/无头模式，模式变化时会自动重建浏览器。"""
        await self._ensure_browser(headless=headless)

    async def create_page(self, headless: bool | None = None):
        """创建新页面，调用方负责关闭。适用于需要自定义页面交互的场景。"""
        await self._ensure_browser(headless=headless)
        return await self._context.new_page()

    @staticmethod
    def _extract_domain(url: str) -> str:
        """从 URL 中提取域名"""
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.hostname or "localhost"

    async def fetch(self, url: str, wait_seconds: float | None = None,
                    cookies: str | None = None, headless: bool | None = None) -> FetchResult:
        """
        使用浏览器渲染方式抓取网页。

        Args:
            url: 目标 URL
            wait_seconds: 等待 JS 渲染的时间（秒）
            cookies: Cookie 字符串 (name1=value1; name2=value2)
            headless: 是否无头模式，None 使用默认值
        """
        await self._ensure_browser(headless=headless)

        # 注入 Cookies
        if cookies:
            await self._set_cookies(url, cookies)

        wait = wait_seconds if wait_seconds is not None else self._wait_time
        page = None
        try:
            page = await self._context.new_page()

            # 设置超时
            page.set_default_timeout(settings.request_timeout * 1000)

            # 导航到页面
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=settings.request_timeout * 1000,
            )

            if response is None:
                raise FetchError(detail=f"无法加载页面: {url}")

            # 检查 HTTP 状态
            if response.status >= 400:
                raise FetchError(
                    detail=f"目标服务器返回 HTTP {response.status}: {url}"
                )

            # 等待网络空闲（给 JS 时间渲染内容）
            try:
                await page.wait_for_load_state("networkidle", timeout=wait * 1000)
            except Exception:
                pass

            # 可见模式下额外等待，让用户有时间登录
            if not self._headless:
                await asyncio.sleep(30)
                # 滚动页面触发懒加载内容
                try:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(2)
                    await page.evaluate("window.scrollTo(0, 0)")
                    await asyncio.sleep(1)
                except Exception:
                    pass
            else:
                await asyncio.sleep(1)

            # 获取渲染后的完整 HTML（包含 JS 动态加载的内容）
            full_html = await page.content()
            html = full_html
            final_url = page.url

            # 检查内容大小
            max_size = settings.max_content_size_mb * 1024 * 1024
            content_length = len(html.encode("utf-8"))
            if content_length > max_size:
                raise ContentTooLargeError(
                    detail=f"页面内容 {content_length / 1024 / 1024:.1f}MB，超过限制"
                )

            # 获取 Content-Type
            content_type = response.headers.get("content-type", "text/html")

            # 不在这里解码，浏览器已经返回了渲染后的 HTML
            # 但为了保持接口一致，仍然检查并解码
            raw_html = html.encode("utf-8")
            decoded = decode_html(raw_html, content_type)

            return FetchResult(
                html=decoded,
                final_url=final_url,
                status_code=response.status,
                content_type=content_type,
                content_length=len(raw_html),
            )

        except FetchError:
            raise
        except Exception as e:
            if "Timeout" in str(e) or "timeout" in str(e).lower():
                raise FetchTimeoutError(
                    detail=f"页面加载超时: {url}"
                )
            raise FetchError(detail=f"浏览器抓取失败: {str(e)[:200]}")
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

    async def close(self):
        """关闭浏览器"""
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._browser = None
        self._context = None
        self._playwright = None

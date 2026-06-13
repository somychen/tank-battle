class AppException(Exception):
    """应用基础异常"""
    code: str = "INTERNAL_ERROR"
    message: str = "服务器内部错误"
    status_code: int = 500

    def __init__(self, message: str | None = None, detail: str | None = None):
        if message:
            self.message = message
        self.detail = detail or self.message
        super().__init__(self.message)


# ---- 400 系列 ----

class InvalidURLError(AppException):
    code = "INVALID_URL"
    message = "URL 格式不合法"
    status_code = 400


class MissingURLError(AppException):
    code = "MISSING_URL"
    message = "缺少 URL 参数"
    status_code = 400


# ---- 413 ----

class ContentTooLargeError(AppException):
    code = "CONTENT_TOO_LARGE"
    message = "网页内容超过大小限制"
    status_code = 413


# ---- 422 系列 ----

class UnsupportedContentError(AppException):
    code = "UNSUPPORTED_CONTENT"
    message = "内容类型不支持，仅支持 HTML 网页"
    status_code = 422


class ExtractionFailedError(AppException):
    code = "EXTRACTION_FAILED"
    message = "无法提取正文内容"
    status_code = 422


# ---- 429 ----

class RateLimitedError(AppException):
    code = "RATE_LIMITED"
    message = "请求频率超限，请稍后重试"
    status_code = 429


# ---- 502/504 系列 ----

class FetchError(AppException):
    code = "FETCH_ERROR"
    message = "目标服务器连接失败"
    status_code = 502


class FetchTimeoutError(AppException):
    code = "FETCH_TIMEOUT"
    message = "请求目标网页超时"
    status_code = 504

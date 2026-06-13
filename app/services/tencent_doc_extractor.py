"""腾讯文档正文提取 —— 通过拦截 dop-api/opendoc 接口获取文档原始数据"""

from __future__ import annotations
import base64
import json
import re
from urllib.parse import urlparse

from playwright.async_api import Page


class TencentDocExtractor:
    """从腾讯文档 (docs.qq.com) 的 API 响应中提取正文内容。

    腾讯文档使用 Canvas 渲染正文，DOM 中不存在文档内容。
    但页面初始化时会调用 dop-api/opendoc 接口获取原始文档数据，
    其中 initialAttributedText.text 包含了完整的文档文本。
    """

    @staticmethod
    def is_tencent_doc_url(url: str) -> bool:
        """判断是否为腾讯文档链接"""
        host = urlparse(url).hostname or ""
        return "docs.qq.com" in host and "/doc/" in url

    def __init__(self):
        self._raw_text: str = ""
        self._title: str = ""

    async def setup_intercept(self, page: Page) -> None:
        """在页面上设置响应拦截，捕获 opendoc 接口数据"""
        self._raw_text = ""
        self._title = ""

        async def on_response(response):
            if self._raw_text:
                return  # 已经拿到了
            if "dop-api/opendoc" not in response.url:
                return
            if "clientVarsCallback" not in response.url:
                return
            try:
                body = await response.text()
                self._parse_opendoc(body)
            except Exception:
                pass

        page.on("response", on_response)

    def _parse_opendoc(self, body: str) -> None:
        """解析 opendoc JSONP 响应"""
        m = re.search(r"clientVarsCallback\((.*)\)\s*$", body, re.DOTALL)
        if not m:
            return
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            return

        cv = data.get("clientVars", {})
        self._title = cv.get("title", "") or cv.get("initialTitle", "") or ""

        ccv = cv.get("collab_client_vars", {})
        iat = ccv.get("initialAttributedText", {})
        text_chunks = iat.get("text", [])

        if not text_chunks:
            return

        # 解码所有文本块
        decoded_parts = []
        for chunk in text_chunks:
            try:
                raw = base64.b64decode(chunk)
                decoded_parts.append(raw)
            except Exception:
                continue

        if not decoded_parts:
            return

        # 合并并提取可读文本
        full_raw = b"".join(decoded_parts)
        text = self._extract_text(full_raw)
        # 对提取后的文本进行标题层级标记
        text = self._apply_headings(text)
        self._raw_text = text

    def _apply_headings(self, text: str) -> str:
        """对提取的文本进行标题层级标记

        腾讯文档编辑器中通过样式设置的标题(H1/H2/H3)在 raw text 中没有 # 标记，
        只有作者手动输入的 # 字符会保留。本方法通过启发式规则识别这些结构标题：

        1. 文档标题(_title)已经在前面作为 H1 处理
        2. 检测出现在空行后的短文本行(<=80字符)，如果它们不以标点结尾，
           且下一行是更长的正文内容，则标记为 ## 二级标题
        """
        lines = text.split("\n")
        result: list[str] = []
        prev_blank = True  # 前一行是否为空

        for i, line in enumerate(lines):
            stripped = line.strip()

            # 保留空行
            if not stripped:
                result.append("")
                prev_blank = True
                continue

            # 已有 # 标记的行保持不变 (作者手动输入的 markdown 标题)
            if re.match(r"^#{1,6}\s", stripped):
                result.append(stripped)
                prev_blank = False
                continue

            # 列表项、表格行保持不变
            if re.match(r"^[-*+]\s|^\d+[\.\)]\s|^\|", stripped):
                result.append(stripped)
                prev_blank = False
                continue

            # 检测候选标题：短行(<=80字符)、不以标点结尾、前面有空行或位于文档开头
            # 注意：如果行尾是 ）但行内有 （，说明是 "名称（备注）" 格式，不应排除
            _has_closing_paren = bool(re.search(r"[\)）]$", stripped))
            _has_opening_paren = "（" in stripped or "(" in stripped
            _excluded_by_paren = _has_closing_paren and not _has_opening_paren

            is_candidate = (
                prev_blank
                and len(stripped) <= 80
                and not re.search(r"[。，；：、！？\.,;:!\?]$", stripped)
                and not _excluded_by_paren
                and not re.match(r"^[（(]*第[一二三四五六七八九十\d]+[步章节]", stripped)
                and not re.match(r"^(?:step|Step)\s*\d", stripped)
            )

            if is_candidate:
                # 检查下一非空行是否为正文内容（或也是候选标题，处理连续标题情况）
                next_text = ""
                for j in range(i + 1, min(i + 4, len(lines))):
                    nl = lines[j].strip()
                    if nl and not re.match(r"^#{1,6}\s", nl):
                        next_text = nl
                        break

                if next_text:
                    # 下一行是否是候选标题（连续标题模式，如"文档标题"后跟"副标题"）
                    _nh_cp = bool(re.search(r"[\)）]$", next_text))
                    _nh_op = "（" in next_text or "(" in next_text
                    next_is_candidate = (
                        len(next_text) <= 80
                        and not re.search(r"[。，；：、！？\.,;:!\?]$", next_text)
                        and not (_nh_cp and not _nh_op)
                        and not re.match(r"^[（(]*第[一二三四五六七八九十\d]+[步章节]", next_text)
                    )
                    # 长正文(>20字符)或下一个也是候选标题 → 标记当前为标题
                    if len(next_text) > 20 or next_is_candidate:
                        result.append(f"## {stripped}")
                        prev_blank = False
                        continue

            result.append(stripped)
            prev_blank = False

        return "\n".join(result)

    @staticmethod
    def _extract_text(raw: bytes) -> str:
        """从二进制数据中提取可读文本"""
        # 先按 UTF-8 解码，替换无效字节
        text = raw.decode("utf-8", errors="replace")

        # 移除大量出现的替换字符
        text = re.sub(r"\ufffd+", " ", text)

        # 统一换行符：腾讯文档数据使用 \r 换行，转换为 \n
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # 移除控制字符（保留换行 \n）
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)

        # 合并多余空白
        text = re.sub(r" {2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

        # 按行处理，过滤二进制垃圾
        lines = text.split("\n")
        cleaned = []
        consecutive_garbage = 0

        for line in lines:
            line = line.strip()
            if not line:
                cleaned.append("")
                continue
            if len(line) < 2:
                continue

            # ---- 检测二进制垃圾行 ----
            is_garbage = False

            # 腾讯文档内部 UID 行: p.144115216014554986@eJ ...
            if re.match(r"^p\.\d{10,}\s*@eJ", line):
                is_garbage = True
            # 样式标记行: xP4:, xPF :/ ,J 等
            elif re.match(r"^xP[A-Za-z]*\s*:", line):
                is_garbage = True
            # 纯碎片行: J/:, :/ ,J 等
            elif re.fullmatch(r"[J/:,\s]+", line):
                is_garbage = True
            # 字体名称引用: Helvetica*
            elif re.match(r"^Helvetica", line):
                is_garbage = True
            # 纯 ASCII 短行（<20 字符）且不含中文，包含字母+特殊字符混合
            elif (len(line) < 20
                  and not any('\u4e00' <= c <= '\u9fff' for c in line)
                  and re.search(r"[A-Za-z]", line)
                  and re.search(r"[@:;\[\]{}|\\/~`!$%^&*()+=<>?#]", line)):
                is_garbage = True

            if is_garbage:
                consecutive_garbage += 1
                # 累积 3 行以上连续垃圾，进入截断模式
                if consecutive_garbage > 3:
                    break
                continue
            # ---- 检测结束 ----

            # 有中文内容的行，重置垃圾计数
            if any('\u4e00' <= c <= '\u9fff' for c in line):
                consecutive_garbage = 0
            else:
                # 非中文且非垃圾的行，计数但不立即丢弃
                # （可能是英文单词或数字编号等正常内容）
                pass

            # 拆分过长的行（超过 200 字符可能缺少换行）
            if len(line) > 200:
                parts = re.split(r"(?=(?:#{1,3}\s|##\s|\d+\.\s|\*\*|【))", line)
                for p in parts:
                    p = p.strip()
                    if p and len(p) >= 2:
                        cleaned.append(p)
            else:
                cleaned.append(line)

        result = "\n".join(cleaned)

        # 清理行内残留的二进制垃圾片段
        # 当垃圾内容紧贴文本末尾无换行分隔时，行级过滤会遗漏
        result = re.sub(r"\s*J/:,?\s*", "", result)
        result = re.sub(r"\s*p\.\d{10,}\s*@eJ\s*\S*\s*", "", result)
        result = re.sub(r"\s*Helvetica\*?\s*", "", result)

        return result

    def get_text(self) -> str:
        """获取提取的正文文本"""
        return self._raw_text

    def get_title(self) -> str:
        """获取文档标题"""
        return self._title

    def has_content(self) -> bool:
        """是否成功提取到内容"""
        return len(self._raw_text) > 100

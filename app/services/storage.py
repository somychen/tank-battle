from __future__ import annotations
import os
from datetime import datetime

from app.config import settings
from app.utils import generate_filename


class Storage:
    """文件存储服务"""

    def __init__(self) -> None:
        self.output_dir = settings.output_dir
        self._ensure_output_dir()

    def _ensure_output_dir(self) -> None:
        """确保输出目录存在"""
        os.makedirs(self.output_dir, exist_ok=True)

    def save(self, content: str, filename: str | None = None, title: str | None = None) -> tuple[str, str]:
        """
        保存 Markdown 内容到文件

        Returns:
            (文件绝对路径, 文件名)
        """
        if not filename:
            filename = generate_filename(title)

        # 确保文件名以 .md 结尾
        if not filename.endswith(".md"):
            filename += ".md"

        filepath = self._resolve_path(filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return filepath, filename

    def get_file_path(self, filename: str) -> str:
        """获取文件的完整路径"""
        return os.path.join(self.output_dir, filename)

    def file_exists(self, filename: str) -> bool:
        """检查文件是否存在"""
        return os.path.isfile(os.path.join(self.output_dir, filename))

    def delete_file(self, filename: str) -> bool:
        """删除指定文件，返回是否成功"""
        filepath = self.get_file_path(filename)
        if os.path.isfile(filepath):
            os.remove(filepath)
            return True
        return False

    def _resolve_path(self, filename: str) -> str:
        """解析文件路径，处理冲突"""
        filepath = os.path.join(self.output_dir, filename)

        # 处理文件名冲突：追加序号
        if os.path.exists(filepath):
            base, ext = os.path.splitext(filename)
            counter = 1
            while os.path.exists(filepath):
                filepath = os.path.join(self.output_dir, f"{base}-{counter}{ext}")
                counter += 1

        return filepath

    def list_files(self) -> list[dict]:
        """列出 output 目录下所有 .md 文件，按修改时间倒序"""
        files: list[dict] = []
        try:
            for f in os.listdir(self.output_dir):
                if f.endswith(".md"):
                    path = os.path.join(self.output_dir, f)
                    stat = os.stat(path)
                    files.append({
                        "name": f,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    })
        except FileNotFoundError:
            pass
        files.sort(key=lambda x: x["modified"], reverse=True)
        return files

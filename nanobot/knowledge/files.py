"""字节真源：哈希、原子复制、路径安全。"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import uuid
from contextlib import suppress
from pathlib import Path
from typing import IO

# 控制字符与 Unicode 格式/双向控制符直接删除（替换成下划线会平白多出一个分隔符）；
# 其余文件系统敏感字符归一为下划线。
_CONTROL = re.compile(r"[\x00-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]+")
_UNSAFE = re.compile(r'[/\\:*?"<>|]+')

# 落盘目录权限：0700（umask 只会进一步收紧）
_DIR_MODE = 0o700
_FILE_MODE = 0o600

# 单个文件名（含临时文件前缀 ``.{name}.{uuid}.tmp`` 约 38 字节）的总字节上限。
# Linux NAME_MAX 为 255 字节，留出足够余量，同时兼容 CJK/emoji 的多字节编码。
_MAX_NAME_BYTES = 120


def hash_file(path: Path) -> tuple[str, int]:
    """流式 sha256 + 字节数，不整文件载入内存。

    摘要与字节数取自同一遍读取，避免文件在两次 stat 之间变化导致二者描述不同字节。
    """
    path = Path(path)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _mkdir_private(path: Path) -> None:
    """逐级创建目录，新建目录权限 0700；已存在的目录不做 chmod。"""
    missing: list[Path] = []
    cursor = Path(path)
    while not cursor.exists():
        missing.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    for directory in reversed(missing):
        with suppress(FileExistsError):
            os.mkdir(directory, _DIR_MODE)


def _create_private(tmp: Path) -> IO[bytes]:
    """以 O_EXCL + 0600 原子创建临时文件（不依赖 umask，不存在敞口窗口）。"""
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
    return os.fdopen(fd, "wb")


def _fsync_directory(path: Path) -> None:
    with suppress(PermissionError, NotImplementedError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        except OSError as exc:
            if exc.errno != errno.EINVAL:
                raise
        finally:
            os.close(fd)


def copy_and_hash(src: Path, dest: Path) -> tuple[str, int]:
    """流式复制 + sha256 + fsync + os.replace；目标目录不存在时按 0700 创建。

    临时文件与目标同目录，保证 ``os.replace`` 落在同一文件系统上；临时文件创建即为
    0600（无权限敞口窗口）；任何失败都会清理临时文件，目标文件要么是完整新字节，
    要么保持原状（不留半截副本）。
    """
    src, dest = Path(src), Path(dest)
    _mkdir_private(dest.parent)
    digest = hashlib.sha256()
    size = 0
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}.tmp")
    try:
        with src.open("rb") as fi, _create_private(tmp) as fo:
            while block := fi.read(1024 * 1024):
                digest.update(block)
                fo.write(block)
                size += len(block)
            fo.flush()
            os.fsync(fo.fileno())
        os.replace(tmp, dest)
        _fsync_directory(dest.parent)
    finally:
        tmp.unlink(missing_ok=True)
    return digest.hexdigest(), size


def write_bytes_atomic(dest: Path, data: bytes) -> tuple[str, int]:
    """原子写入字节并返回 (sha256, 字节数)；文件权限 0600。"""
    dest = Path(dest)
    _mkdir_private(dest.parent)
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}.tmp")
    try:
        with _create_private(tmp) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, dest)
        _fsync_directory(dest.parent)
    finally:
        tmp.unlink(missing_ok=True)
    return hashlib.sha256(data).hexdigest(), len(data)


def _truncate_utf8(text: str, limit: int) -> str:
    """按 UTF-8 字节数截断，丢弃被截断的半个码点。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", "ignore")


def safe_filename(name: str) -> str:
    """把任意来源的名字清洗成安全 basename：去目录、去控制/双向控制字符、去 Windows 敏感字符。

    整个名字（含扩展名）按 UTF-8 字节数收敛到 ``_MAX_NAME_BYTES`` 以内，避免多字节
    字符超出 NAME_MAX；扩展名优先保留。
    """
    base = Path(name.replace("\\", "/")).name
    cleaned = _UNSAFE.sub("_", _CONTROL.sub("", base)).replace(" ", "_")
    cleaned = cleaned.strip(". ")
    if not cleaned:
        return "file"

    dot = cleaned.rfind(".")
    if dot > 0:
        stem, suffix = cleaned[:dot], cleaned[dot:]
    else:
        stem, suffix = cleaned, ""
    if len(suffix.encode("utf-8")) >= _MAX_NAME_BYTES:
        stem, suffix = cleaned, ""

    stem = _truncate_utf8(stem, _MAX_NAME_BYTES - len(suffix.encode("utf-8")))
    stem = stem.strip(". ")
    return (stem + suffix) or "file"


def is_within(path: Path, root: Path) -> bool:
    """解析软链后判断 path 是否位于 root 内（含软链逃逸、``..`` 逃逸）。"""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except ValueError:
        return False
    return True

import hashlib
import os
import stat
from pathlib import Path

import pytest

import nanobot.knowledge.files as files_module
from nanobot.knowledge.files import (
    copy_and_hash,
    hash_file,
    is_within,
    safe_filename,
    write_bytes_atomic,
)


def _spy_regular_file_modes(monkeypatch) -> list[int]:
    """记录每次 ``os.fsync`` 时普通文件 fd 的权限位（目录 fd 忽略）。"""
    recorded: list[int] = []
    real_fsync = os.fsync

    def spy(fd: int) -> None:
        st = os.fstat(fd)
        if stat.S_ISREG(st.st_mode):
            recorded.append(stat.S_IMODE(st.st_mode))
        real_fsync(fd)

    monkeypatch.setattr(files_module.os, "fsync", spy)
    return recorded


def test_hash_file_streams_and_reports_size(tmp_path):
    path = tmp_path / "a.bin"
    payload = b"x" * (2 * 1024 * 1024 + 7)
    path.write_bytes(payload)
    digest, size = hash_file(path)
    assert digest == hashlib.sha256(payload).hexdigest()
    assert size == len(payload)


def test_copy_and_hash_atomic_and_private(tmp_path):
    src = tmp_path / "src.pdf"
    src.write_bytes(b"hello")
    dest = tmp_path / "uploads" / "doc_1" / "a.pdf"
    dest.parent.mkdir(parents=True)
    digest, size = copy_and_hash(src, dest)
    assert dest.read_bytes() == b"hello"
    assert digest == hashlib.sha256(b"hello").hexdigest()
    assert size == 5
    if os.name != "nt":
        assert dest.stat().st_mode & 0o777 == 0o600
    assert not list(dest.parent.glob(".*.tmp"))


def test_copy_failure_leaves_no_destination(tmp_path):
    src = tmp_path / "missing.pdf"
    dest = tmp_path / "a.pdf"
    with pytest.raises(FileNotFoundError):
        copy_and_hash(src, dest)
    assert not dest.exists()


def test_write_bytes_atomic_is_private(tmp_path):
    dest = tmp_path / "uploads" / "doc_1" / "a.txt"
    digest, size = write_bytes_atomic(dest, b"hello")
    assert dest.read_bytes() == b"hello"
    assert digest == hashlib.sha256(b"hello").hexdigest()
    assert size == 5
    if os.name != "nt":
        assert dest.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\tmp\\a b.pdf", "a_b.pdf"),
        ("产品 手册.pdf", "产品_手册.pdf"),
        ("...", "file"),
        ("a\x00b.md", "ab.md"),
        # 回归断言：控制字符（含 \t）直接删除而不是替换为下划线
        ("a\tb.md", "ab.md"),
    ],
)
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected


def test_is_within_rejects_traversal(tmp_path):
    root = tmp_path / "uploads"
    root.mkdir()
    assert is_within(root / "a" / "b.pdf", root)
    assert not is_within(root / ".." / "b.pdf", root)


def test_is_within_rejects_symlink_escape(tmp_path):
    root = tmp_path / "uploads"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_bytes(b"x")
    (outside / "dir").mkdir()

    file_link = root / "report.md"
    file_link.symlink_to(secret)
    dir_link = root / "nested"
    dir_link.symlink_to(outside / "dir")

    assert not is_within(file_link, root)
    assert not is_within(dir_link, root)
    # 逃逸目录下的子路径也必须拒绝
    assert not is_within(dir_link / "a.pdf", root)


def test_is_within_distinguishes_prefix_collision_sibling(tmp_path):
    root = tmp_path / "uploads"
    root.mkdir()
    sibling = tmp_path / "uploads-evil"
    sibling.mkdir()
    assert not is_within(sibling / "a.pdf", root)


@pytest.mark.parametrize("writer", ["copy", "write"])
def test_tmp_file_is_private_at_fsync_time(writer, tmp_path, monkeypatch):
    # 回归：临时文件必须在创建时就 0600，而不是写入/fsync 之后再 chmod
    recorded = _spy_regular_file_modes(monkeypatch)
    dest = tmp_path / "out" / "a.pdf"
    if writer == "copy":
        src = tmp_path / "src.pdf"
        src.write_bytes(b"hello")
        copy_and_hash(src, dest)
    else:
        write_bytes_atomic(dest, b"hello")
    assert recorded, "os.fsync 未被调用"
    assert set(recorded) == {0o600}


def test_parent_directories_are_0700(tmp_path):
    dest = tmp_path / "uploads" / "doc_1" / "a.txt"
    write_bytes_atomic(dest, b"x")
    assert stat.S_IMODE(dest.parent.stat().st_mode) & 0o777 == 0o700
    assert stat.S_IMODE((tmp_path / "uploads").stat().st_mode) & 0o777 == 0o700


def test_copy_parent_directories_are_0700(tmp_path):
    src = tmp_path / "src.pdf"
    src.write_bytes(b"x")
    dest = tmp_path / "uploads" / "doc_2" / "a.pdf"
    copy_and_hash(src, dest)
    assert stat.S_IMODE(dest.parent.stat().st_mode) & 0o777 == 0o700


def test_safe_filename_long_ascii_keeps_extension():
    name = safe_filename("a" * 200 + ".pdf")
    assert name.endswith(".pdf")
    assert len(name.encode("utf-8")) <= 120
    assert name != "a" * 120


@pytest.mark.parametrize(
    "raw",
    ["文" * 200 + ".pdf", "😀" * 100 + ".md", ("é" * 150) + ".txt"],
)
def test_safe_filename_long_cjk_emoji_within_byte_budget(raw):
    name = safe_filename(raw)
    assert len(name.encode("utf-8")) <= 120
    assert name.endswith(Path(raw).suffix)
    assert name == name.strip(". ")


def test_safe_filename_strips_bidi_and_format_chars():
    assert safe_filename("a\u202eb.md") == "ab.md"
    assert safe_filename("\u202a\u2066a\u2069\u200f.md") == "a.md"
    assert safe_filename("a\ufeffb.md") == "ab.md"


def test_safe_filename_long_name_without_extension_fits():
    name = safe_filename("b" * 300)
    assert len(name.encode("utf-8")) <= 120
    assert name == "b" * 120

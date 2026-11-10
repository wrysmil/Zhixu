"""正文清洗与 passage 预算（WeKnora §7.3）。

「passage 不是 chunk.Content」：送进 cross-encoder 的是标题 + 面包屑 + 清洗正文。
清洗只做**信息无损**的规整（控制字符、空白、图片/链接标记），不改写、不删句子；
真正需要判断「这段能不能回答 query」的是模型，不是这里的正则。

限长口径：`MaxPassageRunes = min(单条上限, 请求上限 − query)`——厂商对单条 document
有字符上限，超了整个请求失败；请求级上限要扣掉 query 本身。
"""

from __future__ import annotations

import re

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_SPACE_RUN_RE = re.compile(r"[ \t 　]{2,}")


def clean_text(text: str) -> str:
    """清洗正文：去控制字符、压缩空白、去掉图片与链接的标记部分。"""
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _CONTROL_RE.sub("", normalized)
    normalized = _IMAGE_RE.sub("", normalized)
    normalized = _LINK_RE.sub(r"\1", normalized)
    normalized = _SPACE_RUN_RE.sub(" ", normalized)
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    normalized = _BLANK_RUN_RE.sub("\n\n", normalized)
    return normalized.strip()


def passage_char_limit(
    query: str, *, max_passage_chars: int, max_request_chars: int
) -> int:
    """单条 passage 的字符上限 = min(单条上限, 请求上限 − query 长度)。

    请求上限按字符计（与厂商的 MaxRequestChars 同量纲）；query 吃掉的部分
    从 passage 里扣，扣完不剩正数时返回 1，避免出现空字符串被当成有效 passage。
    """
    single = max(1, int(max_passage_chars))
    budget = int(max_request_chars) - len(query or "")
    return max(1, min(single, budget))


def build_passage(
    *,
    title: str,
    heading_path: str,
    content: str,
    limit: int,
) -> str:
    """拼 passage = 标题 + 面包屑 + 清洗正文；超长从尾部截断。

    尾部是优先级最低的附属文本（文章 §7.3），所以从尾部截而不是从中间截。
    """
    parts = [part.strip() for part in (title, heading_path) if part and part.strip()]
    body = clean_text(content)
    if body:
        parts.append(body)
    text = "\n".join(parts)
    return text[: max(1, limit)]
